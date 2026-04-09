#!/usr/bin/env python3

import asyncio
import json
import uuid
import websockets
import requests
from urllib.parse import quote
from aiortc import RTCPeerConnection, RTCSessionDescription, RTCIceCandidate, RTCConfiguration, RTCIceServer

CONFERENCE_ID = "75047680642749"
CONFERENCE_URL = f"https://telemost.yandex.ru/j/{CONFERENCE_ID}"
API_BASE = "https://cloud-api.yandex.ru/telemost_front/v2/telemost"

CHUNK_SIZE = 8152
BUFFER_THRESHOLD = 262144
SEND_DELAY = 0.0005

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

class StreamReceiver:
    def __init__(self):
        self.streams = {}
        self.data = {}
    
    def handle_packet(self, packet):
        if len(packet) < 40:
            return
        
        try:
            stream_id = packet[:36].decode().strip()
            seq = int.from_bytes(packet[36:40], 'big')
            chunk = packet[40:]
            
            if stream_id not in self.streams:
                self.streams[stream_id] = {}
                self.data[stream_id] = []
            
            self.streams[stream_id][seq] = chunk
        except:
            pass
    
    def get_data(self, stream_id):
        if stream_id not in self.streams:
            return b""
        
        result = []
        seq = 0
        while seq in self.streams[stream_id]:
            result.append(self.streams[stream_id][seq])
            seq += 1
        
        return b"".join(result)

async def create_peer(name, is_server=False):
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
    
    dc_pub = pc_pub.createDataChannel("dcstream", ordered=True)
    dc_pub_open = asyncio.Event()
    
    receiver = StreamReceiver()
    
    stats = {
        "sent": 0,
        "received": 0,
        "bytes_sent": 0,
        "bytes_received": 0,
        "start_time": None,
        "commands": []
    }
    
    @dc_pub.on("open")
    def on_pub_open():
        dc_pub_open.set()
    
    @dc_pub.on("message")
    def on_pub_msg(msg):
        if isinstance(msg, str):
            stats["commands"].append(msg)
        else:
            receiver.handle_packet(msg)
            stats["received"] += 1
            stats["bytes_received"] += len(msg)
    
    @pc_sub.on("datachannel")
    def on_sub_dc(channel):
        @channel.on("message")
        def on_message(message):
            if isinstance(message, str):
                stats["commands"].append(message)
                
                if is_server and message.startswith("STREAM "):
                    url = message[7:].strip()
                    asyncio.create_task(stream_data(url, dc_pub, stats))
            else:
                receiver.handle_packet(message)
                stats["received"] += 1
                stats["bytes_received"] += len(message)
    
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
            "sdkInfo": {"implementation": "python", "version": "1.0.0", "userAgent": f"DCStream-{name}"},
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
                    
                    await ws.send(json.dumps({
                        "uid": generate_uuid(),
                        "publisherSdpOffer": {
                            "pcSeq": 1,
                            "sdp": pc_pub.localDescription.sdp
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
        "receiver": receiver,
        "ws": ws,
        "ws_task": ws_task,
        "pc_sub": pc_sub,
        "pc_pub": pc_pub
    }

async def stream_data(url, dc, stats):
    try:
        if not url.startswith(('http://', 'https://')):
            url = 'https://' + url
        
        print(f"      -> Streaming {url}...")
        
        stream_id = generate_uuid()
        seq = 0
        
        response = requests.get(url, stream=True, timeout=30)
        response.raise_for_status()
        
        total_size = int(response.headers.get('content-length', 0))
        
        if stats["start_time"] is None:
            import time
            stats["start_time"] = time.time()
        
        dc.send(f"START {stream_id} {total_size}")
        
        bytes_sent = 0
        last_progress = 0
        stall_count = 0
        
        for chunk in response.iter_content(chunk_size=CHUNK_SIZE):
            if not chunk:
                break
            
            wait_count = 0
            while dc.bufferedAmount > BUFFER_THRESHOLD:
                await asyncio.sleep(0.01)
                wait_count += 1
                if wait_count > 500:
                    stall_count += 1
                    if stall_count > 3:
                        print(f"      X Buffer stalled at {dc.bufferedAmount} bytes")
                        raise Exception("Buffer overflow")
                    wait_count = 0
            
            packet = stream_id.encode().ljust(36) + seq.to_bytes(4, 'big') + chunk
            dc.send(packet)
            
            stats["sent"] += 1
            stats["bytes_sent"] += len(packet)
            bytes_sent += len(chunk)
            seq += 1
            
            await asyncio.sleep(SEND_DELAY)
            
            if total_size > 0:
                progress = (bytes_sent / total_size) * 100
                if progress - last_progress >= 5:
                    print(f"      -> Sending: {bytes_sent / 1024 / 1024:.2f} MB / {total_size / 1024 / 1024:.2f} MB ({progress:.1f}%) [buffer: {dc.bufferedAmount}]")
                    last_progress = progress
        
        dc.send(f"END {stream_id}")
        
        import time
        elapsed = time.time() - stats["start_time"]
        mbps = (stats["bytes_sent"] * 8) / (elapsed * 1_000_000) if elapsed > 0 else 0
        
        print(f"      :P Streamed {stats['bytes_sent']} bytes in {elapsed:.2f}s ({mbps:.2f} Mbps)")
        
    except Exception as e:
        print(f"      X Error: {e}")
        try:
            dc.send(f"ERROR {str(e)}")
        except:
            pass

async def run_stream():
    print(r"""
                DCStream - High-Speed DataChannel                
           Optimized streaming over Yandex Telemost            
                    by zarazaex for olc
""")
    
    print("[1/3] Creating server peer...")
    try:
        server = await create_peer("Server", is_server=True)
        await asyncio.wait_for(server["dc_pub_open"].wait(), timeout=10.0)
        print("      :P Server ready")
    except Exception as e:
        print(f"      X Error: {e}")
        return
    
    print("\n[2/3] Creating client peer...")
    try:
        client = await create_peer("Client", is_server=False)
        await asyncio.wait_for(client["dc_pub_open"].wait(), timeout=10.0)
        print("      :P Client ready")
    except Exception as e:
        print(f"      X Error: {e}")
        return
    
    print("\n[3/3] Starting stream...")
    await asyncio.sleep(2)
    
    url = "https://raw.githubusercontent.com/openlibrecommunity/olcng/refs/heads/master/olcng.apk"
    print(f"      -> Client requesting: {url}")
    
    stream_id = None
    total_size = 0
    last_received = 0
    
    client["dc_pub"].send(f"STREAM {url}")
    
    print("      -> Receiving stream...")
    
    for i in range(300):
        await asyncio.sleep(0.5)
        
        for cmd in client["stats"]["commands"]:
            if cmd.startswith("START "):
                parts = cmd.split()
                stream_id = parts[1]
                total_size = int(parts[2])
                print(f"      -> Stream started, expecting {total_size} bytes ({total_size / 1024 / 1024:.2f} MB)")
            
            elif cmd.startswith("END "):
                if stream_id:
                    final_data = client["receiver"].get_data(stream_id)
                    print(f"\n      :P Received {len(final_data)} bytes ({len(final_data) / 1024 / 1024:.2f} MB)")
                    
                    if len(final_data) < 1024:
                        print("\n--- Stream Content ---")
                        try:
                            print(final_data.decode('utf-8'))
                        except:
                            print(f"[Binary data: {len(final_data)} bytes]")
                        print("--- End ---\n")
                    else:
                        print(f"      -> Large file received, skipping content display")
                
                print("\nCleaning up...")
                server["ws_task"].cancel()
                client["ws_task"].cancel()
                await server["ws"].close()
                await client["ws"].close()
                await server["pc_sub"].close()
                await server["pc_pub"].close()
                await client["pc_sub"].close()
                await client["pc_pub"].close()
                
                print(":P Stream complete")
                return
            
            elif cmd.startswith("ERROR"):
                print(f"\n      X {cmd}\n")
                return
        
        client["stats"]["commands"].clear()
        
        if stream_id and total_size > 0:
            if stream_id in client["receiver"].streams:
                current_received = len(client["receiver"].streams[stream_id]) * CHUNK_SIZE
                if current_received > last_received:
                    progress = (current_received / total_size) * 100 if total_size > 0 else 0
                    print(f"      -> Receiving: {current_received / 1024 / 1024:.2f} MB / {total_size / 1024 / 1024:.2f} MB ({progress:.1f}%) [packets: {client['stats']['received']}]")
                    last_received = current_received
    
    print("\n      X Timeout waiting for stream")

async def main():
    await run_stream()

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n\nStream interrupted.")
