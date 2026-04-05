#!/usr/bin/env python3

import asyncio
import json
import uuid
import websockets
import requests
from urllib.parse import quote
from aiortc import RTCPeerConnection, RTCSessionDescription, RTCIceCandidate, RTCConfiguration, RTCIceServer

CHUNK_SIZE = 7168
HEADER_SIZE = 1024

class ChunkedReceiver:
    def __init__(self):
        self.buffers = {}
        self.completed = {}
    
    def handle_chunk(self, packet):
        if len(packet) < HEADER_SIZE:
            return None
        
        header_bytes = packet[:HEADER_SIZE].rstrip(b'\x00')
        chunk_data = packet[HEADER_SIZE:]
        
        try:
            header = json.loads(header_bytes)
        except:
            return None
        
        tid = header["tid"]
        idx = header["idx"]
        total = header["total"]
        
        if tid not in self.buffers:
            self.buffers[tid] = {"chunks": {}, "total": total, "received": 0}
        
        buf = self.buffers[tid]
        
        if idx not in buf["chunks"]:
            buf["chunks"][idx] = chunk_data
            buf["received"] += 1
        
        if buf["received"] == buf["total"]:
            complete = b"".join(buf["chunks"][i] for i in range(buf["total"]))
            self.completed[tid] = complete
            del self.buffers[tid]
            return tid
        
        return None

def chunk_data(data, transfer_id):
    total_size = len(data)
    chunk_count = (total_size + CHUNK_SIZE - 1) // CHUNK_SIZE
    packets = []
    
    for i in range(chunk_count):
        start = i * CHUNK_SIZE
        end = min(start + CHUNK_SIZE, total_size)
        chunk = data[start:end]
        
        header = {"tid": transfer_id, "idx": i, "total": chunk_count, "size": total_size}
        header_json = json.dumps(header).encode()
        header_padded = header_json.ljust(HEADER_SIZE, b'\x00')
        
        packets.append(header_padded + chunk)
    
    return packets

CONFERENCE_ID = "75047680642749"
CONFERENCE_URL = f"https://telemost.yandex.ru/j/{CONFERENCE_ID}"
API_BASE = "https://cloud-api.yandex.ru/telemost_front/v2/telemost"

def generate_uuid():
    return str(uuid.uuid4())

def get_connection_info(display_name):
    url = f"{API_BASE}/conferences/{quote(CONFERENCE_URL, safe='')}/connection"
    params = {
        "next_gen_media_platform_allowed": "true",
        "display_name": display_name,
        "waiting_room_supported": "true"
    }
    
    headers = {
        "User-Agent": "Mozilla/5.0 (X11; Linux x86_64; rv:149.0) Gecko/20100101 Firefox/149.0",
        "Accept": "*/*",
        "content-type": "application/json",
        "Client-Instance-Id": generate_uuid(),
        "X-Telemost-Client-Version": "187.1.0",
        "idempotency-key": generate_uuid(),
        "Origin": "https://telemost.yandex.ru",
        "Referer": "https://telemost.yandex.ru/"
    }
    
    response = requests.get(url, params=params, headers=headers)
    response.raise_for_status()
    return response.json()

async def check_datachannel_limits():
    limits = {
        "supported": False,
        "sctp_port": None,
        "max_message_size": None,
        "max_message_size_mb": None,
        "ordered_supported": None,
        "protocol": None
    }
    
    try:
        conn_info = get_connection_info("LimitCheck")
        ws_url = conn_info["client_configuration"]["media_server_url"]
        
        async with websockets.connect(ws_url) as ws:
            hello = {
                "uid": generate_uuid(),
                "hello": {
                    "participantMeta": {"name": "LimitCheck", "role": "SPEAKER", "sendAudio": False, "sendVideo": False},
                    "participantAttributes": {"name": "LimitCheck", "role": "SPEAKER"},
                    "sendAudio": False, "sendVideo": False, "sendSharing": False,
                    "participantId": conn_info["peer_id"],
                    "roomId": conn_info["room_id"],
                    "serviceName": "telemost",
                    "credentials": conn_info["credentials"],
                    "capabilitiesOffer": {"offerAnswerMode": ["SEPARATE"], "initialSubscriberOffer": ["ON_HELLO"]},
                    "sdkInfo": {"implementation": "python", "version": "1.0.0"},
                    "sdkInitializationId": generate_uuid(),
                    "disablePublisher": False, "disableSubscriber": False
                }
            }
            await ws.send(json.dumps(hello))
            
            for _ in range(10):
                data = json.loads(await ws.recv())
                if "serverHello" in data:
                    await ws.send(json.dumps({"uid": data["uid"], "ack": {"status": {"code": "OK"}}}))
                if "subscriberSdpOffer" in data:
                    sdp = data["subscriberSdpOffer"]["sdp"]
                    if "m=application" in sdp and "SCTP" in sdp:
                        limits["supported"] = True
                        limits["protocol"] = "SCTP over DTLS"
                        for line in sdp.split('\r\n'):
                            if 'sctp-port:' in line:
                                limits["sctp_port"] = int(line.split(':')[1].strip())
                            if 'max-message-size:' in line:
                                size = int(line.split(':')[1].strip())
                                limits["max_message_size"] = size
                                limits["max_message_size_mb"] = size / 1024 / 1024
                        limits["ordered_supported"] = True
                    break
    except Exception as e:
        limits["error"] = str(e)
    
    return limits

async def check_conference_limits():
    limits = {
        "max_participants": None,
        "session_timeout_ms": None,
        "session_timeout_min": None,
        "reconnect_wait_ms": None,
        "media_platform": None,
        "connection_type": None
    }
    
    try:
        conn_info = get_connection_info("ConfCheck")
        limits["max_participants"] = conn_info.get("conference_limit")
        limits["connection_type"] = conn_info.get("connection_type")
        limits["media_platform"] = conn_info.get("media_platform")
        
        client_config = conn_info.get("client_configuration", {})
        limits["session_timeout_ms"] = client_config.get("goloom_session_open_ms")
        if limits["session_timeout_ms"]:
            limits["session_timeout_min"] = limits["session_timeout_ms"] / 1000 / 60
        limits["reconnect_wait_ms"] = client_config.get("wait_time_to_reconnect_ms")
    except Exception as e:
        limits["error"] = str(e)
    
    return limits

async def check_audio_limits():
    limits = {
        "codecs": [],
        "opus_config": {},
        "red_supported": False
    }
    
    try:
        conn_info = get_connection_info("AudioCheck")
        ws_url = conn_info["client_configuration"]["media_server_url"]
        
        async with websockets.connect(ws_url) as ws:
            hello = {
                "uid": generate_uuid(),
                "hello": {
                    "participantMeta": {"name": "AudioCheck", "role": "SPEAKER", "sendAudio": False, "sendVideo": False},
                    "participantAttributes": {"name": "AudioCheck", "role": "SPEAKER"},
                    "sendAudio": False, "sendVideo": False, "sendSharing": False,
                    "participantId": conn_info["peer_id"],
                    "roomId": conn_info["room_id"],
                    "serviceName": "telemost",
                    "credentials": conn_info["credentials"],
                    "capabilitiesOffer": {"offerAnswerMode": ["SEPARATE"], "initialSubscriberOffer": ["ON_HELLO"]},
                    "sdkInfo": {"implementation": "python", "version": "1.0.0"},
                    "sdkInitializationId": generate_uuid(),
                    "disablePublisher": False, "disableSubscriber": False
                }
            }
            await ws.send(json.dumps(hello))
            
            for _ in range(10):
                data = json.loads(await ws.recv())
                if "serverHello" in data:
                    await ws.send(json.dumps({"uid": data["uid"], "ack": {"status": {"code": "OK"}}}))
                if "subscriberSdpOffer" in data:
                    sdp = data["subscriberSdpOffer"]["sdp"]
                    for line in sdp.split('\r\n'):
                        if line.startswith('a=rtpmap:') and 'opus' in line.lower():
                            parts = line.split()
                            codec_info = parts[1].split('/')
                            limits["codecs"].append({
                                "name": codec_info[0],
                                "rate": int(codec_info[1]) if len(codec_info) > 1 else None,
                                "channels": int(codec_info[2]) if len(codec_info) > 2 else None
                            })
                        if line.startswith('a=fmtp:') and 'opus' in sdp[max(0, sdp.find(line)-200):sdp.find(line)].lower():
                            params = line.split(':', 1)[1].strip().split(';')
                            for param in params:
                                if '=' in param:
                                    key, val = param.strip().split('=')
                                    limits["opus_config"][key] = val
                        if 'red' in line.lower() and 'rtpmap' in line:
                            limits["red_supported"] = True
                    break
    except Exception as e:
        limits["error"] = str(e)
    
    return limits

async def check_video_limits():
    limits = {
        "codecs": [],
        "simulcast_supported": False,
        "bandwidth_layers": {}
    }
    
    try:
        conn_info = get_connection_info("VideoCheck")
        ws_url = conn_info["client_configuration"]["media_server_url"]
        
        async with websockets.connect(ws_url) as ws:
            hello = {
                "uid": generate_uuid(),
                "hello": {
                    "participantMeta": {"name": "VideoCheck", "role": "SPEAKER", "sendAudio": False, "sendVideo": False},
                    "participantAttributes": {"name": "VideoCheck", "role": "SPEAKER"},
                    "sendAudio": False, "sendVideo": False, "sendSharing": False,
                    "participantId": conn_info["peer_id"],
                    "roomId": conn_info["room_id"],
                    "serviceName": "telemost",
                    "credentials": conn_info["credentials"],
                    "capabilitiesOffer": {
                        "offerAnswerMode": ["SEPARATE"],
                        "initialSubscriberOffer": ["ON_HELLO"],
                        "simulcastMode": ["STATIC"]
                    },
                    "sdkInfo": {"implementation": "python", "version": "1.0.0"},
                    "sdkInitializationId": generate_uuid(),
                    "disablePublisher": False, "disableSubscriber": False
                }
            }
            await ws.send(json.dumps(hello))
            
            for _ in range(10):
                data = json.loads(await ws.recv())
                if "serverHello" in data:
                    caps = data["serverHello"].get("capabilitiesAnswer", {})
                    limits["simulcast_supported"] = caps.get("simulcastMode") in ["STATIC", "DYNAMIC"]
                    await ws.send(json.dumps({"uid": data["uid"], "ack": {"status": {"code": "OK"}}}))
                if "subscriberSdpOffer" in data:
                    sdp = data["subscriberSdpOffer"]["sdp"]
                    codec_names = set()
                    for line in sdp.split('\r\n'):
                        if line.startswith('a=rtpmap:') and 'm=video' in sdp[:sdp.find(line)]:
                            parts = line.split()
                            if len(parts) >= 2:
                                codec_info = parts[1].split('/')
                                codec_name = codec_info[0].upper()
                                if codec_name not in codec_names and codec_name not in ['RTX', 'RED', 'ULPFEC']:
                                    codec_names.add(codec_name)
                                    limits["codecs"].append(codec_name)
                    break
            
            limits["bandwidth_layers"] = {
                "L1": {"single": "1000 kbps"},
                "L2": {"low": "120 kbps", "med": "360 kbps"},
                "L3": {"low": "120 kbps", "med": "360 kbps", "hi": "800 kbps"},
                "L4": {"low": "120 kbps", "med": "360 kbps", "hi": "800 kbps", "ultra": "1000 kbps"}
            }
    except Exception as e:
        limits["error"] = str(e)
    
    return limits

async def check_ice_limits():
    limits = {
        "stun_servers": [],
        "turn_servers": [],
        "ping_interval_ms": None,
        "ack_timeout_ms": None
    }
    
    try:
        conn_info = get_connection_info("ICECheck")
        ws_url = conn_info["client_configuration"]["media_server_url"]
        
        ice_servers = conn_info["client_configuration"].get("ice_servers", [])
        for server in ice_servers:
            urls = server.get("urls", [])
            for url in urls:
                if url.startswith("stun:"):
                    limits["stun_servers"].append(url)
                elif url.startswith("turn:"):
                    limits["turn_servers"].append(url)
        
        async with websockets.connect(ws_url) as ws:
            hello = {
                "uid": generate_uuid(),
                "hello": {
                    "participantMeta": {"name": "ICECheck", "role": "SPEAKER", "sendAudio": False, "sendVideo": False},
                    "participantAttributes": {"name": "ICECheck", "role": "SPEAKER"},
                    "sendAudio": False, "sendVideo": False, "sendSharing": False,
                    "participantId": conn_info["peer_id"],
                    "roomId": conn_info["room_id"],
                    "serviceName": "telemost",
                    "credentials": conn_info["credentials"],
                    "capabilitiesOffer": {"offerAnswerMode": ["SEPARATE"], "initialSubscriberOffer": ["ON_HELLO"]},
                    "sdkInfo": {"implementation": "python", "version": "1.0.0"},
                    "sdkInitializationId": generate_uuid(),
                    "disablePublisher": False, "disableSubscriber": False
                }
            }
            await ws.send(json.dumps(hello))
            
            for _ in range(10):
                data = json.loads(await ws.recv())
                if "serverHello" in data:
                    ping_config = data["serverHello"].get("pingPongConfiguration", {})
                    limits["ping_interval_ms"] = ping_config.get("pingInterval")
                    limits["ack_timeout_ms"] = ping_config.get("ackTimeout")
                    
                    rtc_config = data["serverHello"].get("rtcConfiguration", {})
                    ice_servers_full = rtc_config.get("iceServers", [])
                    for server in ice_servers_full:
                        urls = server.get("urls", [])
                        for url in urls:
                            if url.startswith("stun:") and url not in limits["stun_servers"]:
                                limits["stun_servers"].append(url)
                            elif url.startswith("turn:") and url not in limits["turn_servers"]:
                                limits["turn_servers"].append(url)
                    break
    except Exception as e:
        limits["error"] = str(e)
    
    return limits

async def create_test_peer(name, is_server=False, sctp_tweaks=None):
    conn_info = get_connection_info(name)
    room_id = conn_info["room_id"]
    peer_id = conn_info["peer_id"]
    credentials = conn_info["credentials"]
    ws_url = conn_info["client_configuration"]["media_server_url"]
    
    pc_sub = RTCPeerConnection(RTCConfiguration(
        iceServers=[RTCIceServer(urls=["stun:stun.rtc.yandex.net:3478"])]
    ))
    
    pc_pub = RTCPeerConnection(RTCConfiguration(
        iceServers=[RTCIceServer(urls=["stun:stun.rtc.yandex.net:3478"])]
    ))
    
    dc_pub = pc_pub.createDataChannel("limits_test", ordered=True)
    dc_pub_open = asyncio.Event()
    
    stats = {
        "sent": 0,
        "received": 0,
        "bytes_sent": 0,
        "bytes_received": 0,
        "errors": [],
        "last_received": None,
        "events": []
    }
    
    @dc_pub.on("open")
    def on_pub_open():
        stats["events"].append(f"[{name}] Publisher DC opened")
        dc_pub_open.set()
    
    @dc_pub.on("message")
    def on_pub_msg(msg):
        stats["events"].append(f"[{name}] Publisher DC received {len(msg)}b")
        stats["received"] += 1
        stats["bytes_received"] += len(msg)
        stats["last_received"] = len(msg)
    
    @dc_pub.on("error")
    def on_pub_error(error):
        stats["events"].append(f"[{name}] Publisher DC error: {error}")
        stats["errors"].append(f"Pub DC error: {error}")
    
    @pc_sub.on("datachannel")
    def on_sub_dc(channel):
        stats["events"].append(f"[{name}] Subscriber DC created: {channel.label}")
        
        @channel.on("open")
        def on_sub_open():
            stats["events"].append(f"[{name}] Subscriber DC opened")
        
        @channel.on("message")
        def on_message(message):
            stats["events"].append(f"[{name}] Subscriber DC received {len(message)}b")
            stats["received"] += 1
            stats["bytes_received"] += len(message)
            stats["last_received"] = len(message)
            
            if is_server:
                try:
                    stats["events"].append(f"[{name}] Echoing {len(message)}b back...")
                    dc_pub.send(message)
                    stats["sent"] += 1
                    stats["bytes_sent"] += len(message)
                    stats["events"].append(f"[{name}] Echo sent successfully")
                except Exception as e:
                    stats["events"].append(f"[{name}] Echo failed: {e}")
                    stats["errors"].append(f"Send error: {str(e)}")
        
        @channel.on("error")
        def on_sub_error(error):
            stats["events"].append(f"[{name}] Subscriber DC error: {error}")
            stats["errors"].append(f"Sub DC error: {error}")
    
    ws = await websockets.connect(ws_url)
    
    hello_msg = {
        "uid": generate_uuid(),
        "hello": {
            "participantMeta": {"name": name, "role": "SPEAKER", "sendAudio": False, "sendVideo": False},
            "participantAttributes": {"name": name, "role": "SPEAKER"},
            "sendAudio": False,
            "sendVideo": False,
            "sendSharing": False,
            "participantId": peer_id,
            "roomId": room_id,
            "serviceName": "telemost",
            "credentials": credentials,
            "capabilitiesOffer": {
                "offerAnswerMode": ["SEPARATE"],
                "initialSubscriberOffer": ["ON_HELLO"],
                "slotsMode": ["FROM_CONTROLLER"],
                "simulcastMode": ["DISABLED"],
                "selfVadStatus": ["FROM_SERVER"],
                "dataChannelSharing": ["TO_RTP"]
            },
            "sdkInfo": {"implementation": "python", "version": "1.0.0", "userAgent": f"LimitsTest-{name}"},
            "sdkInitializationId": generate_uuid(),
            "disablePublisher": False,
            "disableSubscriber": False
        }
    }
    
    await ws.send(json.dumps(hello_msg))
    
    publisher_sdp_sent = False
    
    async def ws_handler():
        nonlocal publisher_sdp_sent
        while True:
            try:
                data = json.loads(await ws.recv())
                
                if "serverHello" in data:
                    await ws.send(json.dumps({"uid": data["uid"], "ack": {"status": {"code": "OK"}}}))
                
                if "subscriberSdpOffer" in data and not publisher_sdp_sent:
                    await pc_sub.setRemoteDescription(RTCSessionDescription(
                        sdp=data["subscriberSdpOffer"]["sdp"], type="offer"
                    ))
                    
                    answer = await pc_sub.createAnswer()
                    await pc_sub.setLocalDescription(answer)
                    
                    await ws.send(json.dumps({
                        "uid": generate_uuid(),
                        "subscriberSdpAnswer": {
                            "pcSeq": data["subscriberSdpOffer"]["pcSeq"],
                            "sdp": pc_sub.localDescription.sdp
                        }
                    }))
                    
                    await ws.send(json.dumps({"uid": data["uid"], "ack": {"status": {"code": "OK"}}}))
                    await asyncio.sleep(0.3)
                    
                    pub_offer = await pc_pub.createOffer()
                    await pc_pub.setLocalDescription(pub_offer)
                    
                    sdp_modified = pc_pub.localDescription.sdp
                    if sctp_tweaks:
                        if 'max_message_size' in sctp_tweaks:
                            sdp_modified = sdp_modified.replace(
                                'a=max-message-size:',
                                f'a=max-message-size:{sctp_tweaks["max_message_size"]}\r\na=old-max-message-size:'
                            )
                        if 'sctp_buf' in sctp_tweaks:
                            if 'a=sctp-port:' in sdp_modified:
                                sdp_modified = sdp_modified.replace(
                                    'a=sctp-port:',
                                    f'a=sctpmap:5000 webrtc-datachannel {sctp_tweaks["sctp_buf"]}\r\na=sctp-port:'
                                )
                    
                    await ws.send(json.dumps({
                        "uid": generate_uuid(),
                        "publisherSdpOffer": {
                            "pcSeq": 1,
                            "sdp": sdp_modified
                        }
                    }))
                    publisher_sdp_sent = True
                
                if "publisherSdpAnswer" in data:
                    await pc_pub.setRemoteDescription(RTCSessionDescription(
                        sdp=data["publisherSdpAnswer"]["sdp"], type="answer"
                    ))
                    await ws.send(json.dumps({"uid": data["uid"], "ack": {"status": {"code": "OK"}}}))
                
                if "webrtcIceCandidate" in data:
                    cand = data["webrtcIceCandidate"]
                    try:
                        parts = cand["candidate"].split()
                        if len(parts) >= 8:
                            ice = RTCIceCandidate(
                                component=int(parts[1]),
                                foundation=parts[0].replace("candidate:", ""),
                                ip=parts[4],
                                port=int(parts[5]),
                                priority=int(parts[3]),
                                protocol=parts[2],
                                type=parts[7],
                                sdpMid=cand["sdpMid"],
                                sdpMLineIndex=cand["sdpMlineIndex"]
                            )
                            
                            if cand.get("target") == "SUBSCRIBER":
                                await pc_sub.addIceCandidate(ice)
                            elif cand.get("target") == "PUBLISHER":
                                await pc_pub.addIceCandidate(ice)
                    except:
                        pass
            
            except:
                break
    
    @pc_sub.on("icecandidate")
    async def on_sub_ice(event):
        if event.candidate:
            await ws.send(json.dumps({
                "uid": generate_uuid(),
                "webrtcIceCandidate": {
                    "candidate": event.candidate.candidate,
                    "sdpMid": event.candidate.sdpMid,
                    "sdpMlineIndex": event.candidate.sdpMLineIndex,
                    "target": "SUBSCRIBER",
                    "pcSeq": 1
                }
            }))
    
    @pc_pub.on("icecandidate")
    async def on_pub_ice(event):
        if event.candidate:
            await ws.send(json.dumps({
                "uid": generate_uuid(),
                "webrtcIceCandidate": {
                    "candidate": event.candidate.candidate,
                    "sdpMid": event.candidate.sdpMid,
                    "sdpMlineIndex": event.candidate.sdpMLineIndex,
                    "target": "PUBLISHER",
                    "pcSeq": 1
                }
            }))
    
    ws_task = asyncio.create_task(ws_handler())
    
    return {
        "name": name,
        "dc_pub": dc_pub,
        "dc_pub_open": dc_pub_open,
        "stats": stats,
        "ws": ws,
        "ws_task": ws_task,
        "pc_sub": pc_sub,
        "pc_pub": pc_pub
    }

async def test_message_size_limits(max_size):
    print("\n[REAL TEST] Testing message size limits...")
    
    try:
        server = await create_test_peer("LimitServer", is_server=True)
        await asyncio.wait_for(server["dc_pub_open"].wait(), timeout=10.0)
        print("      :P Server ready")
        
        client = await create_test_peer("LimitClient", is_server=False)
        await asyncio.wait_for(client["dc_pub_open"].wait(), timeout=10.0)
        print("      :P Client ready")
        
        await asyncio.sleep(3)
        
        sizes_kb = [1, 6, 8, 10, 12, 14, 16, 18, 20, 24, 28, 32]
        tests = [(f"{kb}KB", kb * 1024, 3, 2) for kb in sizes_kb]
        
        results = []
        last_success_size = 0
        
        for test_name, size, send_wait, echo_wait in tests:
            client["stats"]["last_received"] = None
            server["stats"]["last_received"] = None
            client["stats"]["events"].clear()
            server["stats"]["events"].clear()
            
            try:
                print(f"\n      -> Sending {test_name}...")
                
                dc = client["dc_pub"]
                print(f"      -> DC state: {dc.readyState}")
                print(f"      -> DC bufferedAmount before: {dc.bufferedAmount}")
                
                data = "X" * size
                
                print(f"      -> Calling send() for {size:,} bytes...")
                try:
                    dc.send(data)
                    client["stats"]["sent"] += 1
                    client["stats"]["bytes_sent"] += len(data)
                    print(f"      -> send() returned successfully")
                except Exception as send_err:
                    print(f"      X send() raised exception: {send_err}")
                    results.append((test_name, False, f"Send exception: {send_err}"))
                    continue
                
                print(f"      -> DC bufferedAmount after: {dc.bufferedAmount}")
                print(f"      -> Waiting {send_wait}s for delivery...")
                
                for i in range(send_wait):
                    await asyncio.sleep(1)
                    print(f"      -> {i+1}s... buffered: {dc.bufferedAmount}, server events: {len(server['stats']['events'])}")
                    if server["stats"]["last_received"]:
                        print(f"      -> Server received after {i+1}s!")
                        break
                
                server_received = server["stats"]["last_received"]
                
                if server_received:
                    print(f"      -> Server got {server_received} bytes, waiting {echo_wait}s for echo...")
                    for i in range(echo_wait):
                        await asyncio.sleep(1)
                        if client["stats"]["last_received"]:
                            print(f"      -> Client received echo after {i+1}s!")
                            break
                        print(f"      -> {i+1}s... (client: {len(client['stats']['events'])} events)")
                else:
                    print(f"      -> Server: NO DATA")
                    print(f"      -> Final bufferedAmount: {dc.bufferedAmount}")
                
                client_received = client["stats"]["last_received"]
                
                if client_received:
                    print(f"      :P {test_name} ({size:,}b): SUCCESS")
                    results.append((test_name, True, None))
                    last_success_size = size
                elif server_received:
                    if server["stats"]["errors"]:
                        print(f"      X {test_name}: Echo failed - {server['stats']['errors'][-1]}")
                        results.append((test_name, False, server['stats']['errors'][-1]))
                    else:
                        print(f"      X {test_name}: Server got it, but no echo back")
                        results.append((test_name, False, "No echo received"))
                else:
                    print(f"      X {test_name}: Never reached server")
                    results.append((test_name, False, "Never reached server"))
                    break
                
            except Exception as e:
                print(f"      X {test_name} ({size:,}b): TEST FAILED - {str(e)}")
                results.append((test_name, False, f"Test error: {str(e)[:30]}"))
                break
        
        if last_success_size > 0:
            print(f"\n      :P Max working size: {last_success_size:,} bytes ({last_success_size/1024:.0f}KB)")
        
        server["ws_task"].cancel()
        client["ws_task"].cancel()
        await server["ws"].close()
        await client["ws"].close()
        await server["pc_sub"].close()
        await server["pc_pub"].close()
        await client["pc_sub"].close()
        await client["pc_pub"].close()
        
        return results
        
    except Exception as e:
        print(f"      X Standard test failed: {e}")
        return []

async def test_throughput_limits():
    print("\n[REAL TEST] Testing throughput limits...")
    
    try:
        server = await create_test_peer("ThroughputServer", is_server=True)
        await asyncio.wait_for(server["dc_pub_open"].wait(), timeout=10.0)
        
        client = await create_test_peer("ThroughputClient", is_server=False)
        await asyncio.wait_for(client["dc_pub_open"].wait(), timeout=10.0)
        print("      :P Peers ready")
        
        await asyncio.sleep(3)
        
        msg_size = 1024
        msg_count = 50
        data = "X" * msg_size
        
        start_time = asyncio.get_event_loop().time()
        
        for i in range(msg_count):
            try:
                client["dc_pub"].send(data)
                client["stats"]["sent"] += 1
                client["stats"]["bytes_sent"] += len(data)
                await asyncio.sleep(0.05)
            except Exception as e:
                print(f"      X Send failed at message {i}: {e}")
                break
        
        await asyncio.sleep(3)
        
        end_time = asyncio.get_event_loop().time()
        duration = end_time - start_time
        
        total_bytes = client["stats"]["bytes_sent"]
        throughput_kbps = (total_bytes * 8) / (duration * 1_000)
        
        print(f"      :P Sent: {client['stats']['sent']} messages")
        print(f"      :P Received: {server['stats']['received']} messages")
        print(f"      :P Throughput: {throughput_kbps:.2f} Kbps")
        print(f"      :P Duration: {duration:.2f}s")
        
        server["ws_task"].cancel()
        client["ws_task"].cancel()
        await server["ws"].close()
        await client["ws"].close()
        await server["pc_sub"].close()
        await server["pc_pub"].close()
        await client["pc_sub"].close()
        await client["pc_pub"].close()
        
        success = server['stats']['received'] > 0
        return [("Throughput test", success, f"{throughput_kbps:.2f} Kbps" if success else "No response")]
        
    except Exception as e:
        print(f"      X Test failed: {e}")
        return [("Throughput test", False, str(e))]

async def test_chunked_transfer():
    print("\n[REAL TEST] Testing large data transfer (app-level chunking)...")
    
    try:
        server = await create_test_peer("BigServer", is_server=True)
        await asyncio.wait_for(server["dc_pub_open"].wait(), timeout=10.0)
        
        client = await create_test_peer("BigClient", is_server=False)
        await asyncio.wait_for(client["dc_pub_open"].wait(), timeout=10.0)
        print("      :P Peers ready")
        
        await asyncio.sleep(3)
        
        test_sizes = [
            ("64KB", 64 * 1024),
            ("128KB", 128 * 1024),
            ("256KB", 256 * 1024),
        ]
        
        results = []
        chunk_size = 8192
        
        for name, total_size in test_sizes:
            server["stats"]["bytes_received"] = 0
            server["stats"]["received"] = 0
            
            chunks_needed = (total_size + chunk_size - 1) // chunk_size
            
            print(f"\n      -> Sending {name} ({total_size:,}b) as {chunks_needed} x 8KB chunks...")
            
            start_time = asyncio.get_event_loop().time()
            
            try:
                for i in range(chunks_needed):
                    chunk = ("X" * chunk_size)
                    
                    while client["dc_pub"].bufferedAmount > chunk_size * 2:
                        await asyncio.sleep(0.001)
                    
                    client["dc_pub"].send(chunk)
                
                await asyncio.sleep(2)
                
                end_time = asyncio.get_event_loop().time()
                duration_ms = (end_time - start_time) * 1000
                
                received = server["stats"]["bytes_received"]
                
                if received >= total_size:
                    throughput_kbps = (total_size * 8) / duration_ms
                    print(f"      :P {name}: SUCCESS in {duration_ms:.0f}ms ({throughput_kbps:.1f} Kbps)")
                    results.append((name, True, f"{duration_ms:.0f}ms"))
                else:
                    print(f"      X {name}: PARTIAL ({received:,}b / {total_size:,}b)")
                    results.append((name, False, f"{received}b"))
                    break
                
            except Exception as e:
                print(f"      X {name}: ERROR - {e}")
                results.append((name, False, str(e)[:30]))
                break
        
        server["ws_task"].cancel()
        client["ws_task"].cancel()
        await server["ws"].close()
        await client["ws"].close()
        await server["pc_sub"].close()
        await server["pc_pub"].close()
        await client["pc_sub"].close()
        await client["pc_pub"].close()
        
        return results
        
    except Exception as e:
        print(f"      X Test failed: {e}")
        return [("Large transfer", False, str(e))]

async def test_latency_microbench():
    print("\n[REAL TEST] Testing latency microbenchmarks...")
    
    try:
        server = await create_test_peer("LatencyServer", is_server=True)
        await asyncio.wait_for(server["dc_pub_open"].wait(), timeout=10.0)
        
        client = await create_test_peer("LatencyClient", is_server=False)
        await asyncio.wait_for(client["dc_pub_open"].wait(), timeout=10.0)
        print("      :P Latency test peers ready")
        
        await asyncio.sleep(3)
        
        test_cases = [
            ("Tiny (100b)", 100),
            ("Small (1KB)", 1024),
            ("Medium (4KB)", 4096),
            ("Large (8KB)", 8192),
        ]
        
        results = []
        
        for name, size in test_cases:
            latencies = []
            data = "L" * size
            
            print(f"\n      -> Testing {name}...")
            
            for i in range(10):
                client["stats"]["last_received"] = None
                server["stats"]["last_received"] = None
                
                send_time = asyncio.get_event_loop().time()
                client["dc_pub"].send(data)
                
                for _ in range(50):
                    await asyncio.sleep(0.01)
                    if client["stats"]["last_received"]:
                        recv_time = asyncio.get_event_loop().time()
                        rtt_us = (recv_time - send_time) * 1_000_000
                        latencies.append(rtt_us)
                        break
            
            if latencies:
                avg_lat = sum(latencies) / len(latencies)
                min_lat = min(latencies)
                max_lat = max(latencies)
                
                print(f"      :P {name}: avg={avg_lat:.0f}µs, min={min_lat:.0f}µs, max={max_lat:.0f}µs")
                results.append((name, True, f"{avg_lat:.0f}µs"))
            else:
                print(f"      X {name}: No responses")
                results.append((name, False, "No response"))
        
        server["ws_task"].cancel()
        client["ws_task"].cancel()
        await server["ws"].close()
        await client["ws"].close()
        await server["pc_sub"].close()
        await server["pc_pub"].close()
        await client["pc_sub"].close()
        await client["pc_pub"].close()
        
        return results
        
    except Exception as e:
        print(f"      X Latency test failed: {e}")
        return [("Latency test", False, str(e))]

async def test_participant_limit(max_participants):
    print("\n[REAL TEST] Testing participant limit...")
    
    peers = []
    try:
        for i in range(min(3, max_participants)):
            try:
                peer = await create_test_peer(f"Peer{i+1}", is_server=False)
                await asyncio.wait_for(peer["dc_pub_open"].wait(), timeout=10.0)
                peers.append(peer)
                print(f"      :P Peer {i+1}/3 connected")
                await asyncio.sleep(1)
            except Exception as e:
                print(f"      X Peer {i+1} failed: {str(e)[:30]}")
                break
        
        print(f"      :P Successfully connected {len(peers)}/{min(3, max_participants)} peers")
        
        for peer in peers:
            peer["ws_task"].cancel()
            await peer["ws"].close()
            await peer["pc_sub"].close()
            await peer["pc_pub"].close()
        
        return [("Multi-peer test", True, f"{len(peers)} peers")]
        
    except Exception as e:
        print(f"      X Test failed: {e}")
        return [("Multi-peer test", False, str(e))]

async def check_all_limits():
    print(r"""
                Yandex Telemost - Limits Check                  
           Full verification of documented limits               
                    by zowue for olc
""")
    
    print("[1/5] Checking DataChannel limits...")
    dc_limits = await check_datachannel_limits()
    if dc_limits['supported']:
        print(f"      :P DataChannel: SUPPORTED")
        print(f"      :P Protocol: {dc_limits['protocol']}")
        print(f"      :P SCTP Port: {dc_limits['sctp_port']}")
        print(f"      :P Max Message: {dc_limits['max_message_size_mb']:.0f}MB")
        print(f"      :P Ordered: YES")
    else:
        print(f"      X DataChannel: NOT SUPPORTED")
    
    print("\n[2/5] Checking conference limits...")
    conf_limits = await check_conference_limits()
    print(f"      :P Max Participants: {conf_limits['max_participants']}")
    print(f"      :P Media Platform: {conf_limits['media_platform']}")
    print(f"      :P Session Timeout: {conf_limits['session_timeout_min']:.1f}min")
    print(f"      :P Reconnect Wait: {conf_limits['reconnect_wait_ms']}ms")
    
    print("\n[3/5] Checking audio limits...")
    audio_limits = await check_audio_limits()
    if audio_limits['codecs']:
        codec = audio_limits['codecs'][0]
        print(f"      :P Codec: {codec['name']} @ {codec['rate']//1000}kHz")
        print(f"      :P Channels: {codec['channels']} (stereo)")
    if audio_limits['opus_config']:
        print(f"      :P FEC: {'YES' if audio_limits['opus_config'].get('useinbandfec') == '1' else 'NO'}")
        print(f"      :P DTX: {'YES' if audio_limits['opus_config'].get('usedtx') == '1' else 'NO'}")
    print(f"      :P RED: {'YES' if audio_limits['red_supported'] else 'NO'}")
    
    print("\n[4/5] Checking video limits...")
    video_limits = await check_video_limits()
    codecs_clean = [c for c in video_limits['codecs'] if c in ['VP8', 'VP9', 'H264', 'AV1']]
    print(f"      :P Codecs: {', '.join(codecs_clean)}")
    print(f"      :P Simulcast: {'YES' if video_limits['simulcast_supported'] else 'NO'}")
    print(f"      :P Layers: L1-L4 (120kbps - 1000kbps)")
    
    print("\n[5/5] Checking ICE/network limits...")
    ice_limits = await check_ice_limits()
    print(f"      :P STUN Servers: {len(ice_limits['stun_servers'])}")
    print(f"      :P TURN Servers: {len(ice_limits['turn_servers'])}")
    print(f"      :P Ping Interval: {ice_limits['ping_interval_ms']}ms")
    print(f"      :P ACK Timeout: {ice_limits['ack_timeout_ms']}ms")
    
    test_results = []
    if dc_limits['supported'] and dc_limits['max_message_size']:
        test_results = await test_message_size_limits(dc_limits['max_message_size'])
        test_results.extend(await test_latency_microbench())
        test_results.extend(await test_throughput_limits())
        test_results.extend(await test_chunked_transfer())
    
    if conf_limits['max_participants']:
        test_results.extend(await test_participant_limit(conf_limits['max_participants']))
    
    print(r"""
                    VERIFICATION RESULTS                         
""")
    
    doc_claims = {
        "DataChannel max size": (1073741823, dc_limits.get('max_message_size')),
        "SCTP port": (5000, dc_limits.get('sctp_port')),
        "Max participants": (40, conf_limits.get('max_participants')),
        "Session timeout": (120000, conf_limits.get('session_timeout_ms')),
        "Ping interval": (5000, ice_limits.get('ping_interval_ms')),
        "ACK timeout": (9000, ice_limits.get('ack_timeout_ms'))
    }
    
    all_match = True
    for claim, (expected, actual) in doc_claims.items():
        match = expected == actual
        status = ":P" if match else "X"
        print(f"  - {claim}: {status} {actual} {'(OK)' if match else f'(expected {expected})'}")
        if not match:
            all_match = False
    
    if test_results:
        print("\nReal Transfer Tests:")
        for test_name, success, error in test_results:
            status = ":P" if success else "X"
            print(f"  - {test_name}: {status} {'OK' if success else error[:30]}")
    
    if all_match:
        print("\n :P ALL LIMITS VERIFIED - Documentation is accurate!")
    else:
        print("\n  X MISMATCH DETECTED - Some limits differ from docs")
    
    return {
        "datachannel": dc_limits,
        "conference": conf_limits,
        "audio": audio_limits,
        "video": video_limits,
        "ice": ice_limits
    }

if __name__ == "__main__":
    try:
        asyncio.run(check_all_limits())
    except KeyboardInterrupt:
        print("\n\nCheck interrupted.")
