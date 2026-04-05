#!/usr/bin/env python3

import asyncio
import json
import uuid
import requests
from urllib.parse import quote
import websockets
from datetime import datetime

CONFERENCE_ID = "75047680642749"
CONFERENCE_URL = f"https://telemost.yandex.ru/j/{CONFERENCE_ID}"
API_BASE = "https://cloud-api.yandex.ru/telemost_front/v2/telemost"

session = requests.Session()

def log(msg, level="INFO"):
    timestamp = datetime.now().strftime("%H:%M:%S.%f")[:-3]
    log_msg = f"[{timestamp}] [{level}] {msg}"
    print(log_msg)

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
    
    log(f"GET {url}", "HTTP")
    log(f"Params: {params}", "DEBUG")
    
    response = session.get(url, params=params, headers=headers)
    response.raise_for_status()
    
    log(f"Response: {response.status_code}", "HTTP")
    log(f"Cookies: {list(session.cookies.keys())}", "DEBUG")
    
    return response.json()

def get_participants_list(peer_ids=None):
    url = f"{API_BASE}/conferences/{quote(CONFERENCE_URL, safe='')}/request-states"
    
    headers = {
        "User-Agent": "Mozilla/5.0 (X11; Linux x86_64; rv:149.0) Gecko/20100101 Firefox/149.0",
        "Accept": "*/*",
        "content-type": "application/json",
        "Client-Instance-Id": generate_uuid(),
        "idempotency-key": generate_uuid(),
        "Origin": "https://telemost.yandex.ru",
        "Referer": "https://telemost.yandex.ru/"
    }
    
    if peer_ids:
        payload = {
            "peers": [{"peer_id": pid} for pid in peer_ids],
            "permissions": {},
            "conference": {"version": -1}
        }
    else:
        payload = {
            "peers": [],
            "permissions": {},
            "conference": {"version": -1}
        }
    
    log(f"POST {url}", "HTTP")
    log(f"Payload: {json.dumps(payload)[:100]}...", "DEBUG")
    
    response = session.post(url, json=payload, headers=headers)
    response.raise_for_status()
    
    log(f"Response: {response.status_code}", "HTTP")
    
    result = response.json()
    log(f"Response body: {json.dumps(result)[:500]}...", "DEBUG")
    
    return result

async def collect_webrtc_info():
    log(r"""

                WebRTC Full Information Collector                
              Complete conference & peer analysis                
                    by zowue for olc

""", "INFO")
    
    info = {
        "connection": {},
        "participants": {},
        "webrtc": {
            "ice": {},
            "sdp": {},
            "datachannel": {},
            "audio": {},
            "video": {}
        },
        "server": {}
    }
    
    log("[1/5] Getting connection info...")
    try:
        conn_info = get_connection_info("InfoCollector")
        
        cookies_dict = session.cookies.get_dict()
        if cookies_dict:
            log(f"      :P Cookies: {', '.join(cookies_dict.keys())}", "INFO")
        
        info["connection"] = {
            "connection_type": conn_info.get("connection_type"),
            "uri": conn_info.get("uri"),
            "room_id": conn_info.get("room_id"),
            "safe_room_id": conn_info.get("safe_room_id"),
            "peer_id": conn_info.get("peer_id"),
            "session_id": conn_info.get("session_id"),
            "peer_session_id": conn_info.get("peer_session_id"),
            "expiration_time": conn_info.get("expiration_time"),
            "conference_limit": conn_info.get("conference_limit"),
            "media_platform": conn_info.get("media_platform")
        }
        
        client_config = conn_info.get("client_configuration", {})
        info["connection"]["client_config"] = {
            "media_server_url": client_config.get("media_server_url"),
            "service_name": client_config.get("service_name"),
            "session_timeout_ms": client_config.get("goloom_session_open_ms"),
            "reconnect_wait_ms": client_config.get("wait_time_to_reconnect_ms")
        }
        
        log(f"      :P Room: {info['connection']['room_id']}")
        log(f"      :P Peer: {info['connection']['peer_id']}")
        log(f"      :P Limit: {info['connection']['conference_limit']}")
    except Exception as e:
        log(f"      X Error: {e}", "ERROR")
        return info
    
    log("\n[2/5] Connecting to WebSocket...")
    try:
        ws_url = client_config.get("media_server_url")
        log(f"      -> WS URL: {ws_url}", "DEBUG")
        ws = await websockets.connect(ws_url)
        
        hello_msg = {
            "uid": generate_uuid(),
            "hello": {
                "participantMeta": {"name": "InfoCollector", "role": "SPEAKER", "sendAudio": False, "sendVideo": False},
                "participantAttributes": {"name": "InfoCollector", "role": "SPEAKER"},
                "sendAudio": False,
                "sendVideo": False,
                "sendSharing": False,
                "participantId": conn_info["peer_id"],
                "roomId": conn_info["room_id"],
                "serviceName": "telemost",
                "credentials": conn_info["credentials"],
                "capabilitiesOffer": {
                    "offerAnswerMode": ["SEPARATE"],
                    "initialSubscriberOffer": ["ON_HELLO"],
                    "slotsMode": ["FROM_CONTROLLER"],
                    "simulcastMode": ["STATIC"],
                    "selfVadStatus": ["FROM_SERVER"],
                    "dataChannelSharing": ["TO_RTP"]
                },
                "sdkInfo": {"implementation": "python", "version": "1.0.0", "userAgent": "InfoCollector"},
                "sdkInitializationId": generate_uuid(),
                "disablePublisher": False,
                "disableSubscriber": False
            }
        }
        
        await ws.send(json.dumps(hello_msg))
        log("      :P Connected")
        log(f"      -> Sent hello message", "DEBUG")
        
        log("\n[3/5] Collecting participants...")
        info["participants"]["list"] = []
        collected_peer_ids = set()
        
        log("\n[4/5] Collecting WebRTC details...")
        
        for i in range(25):
            try:
                data = json.loads(await asyncio.wait_for(ws.recv(), timeout=1.0))
                
                msg_type = next((k for k in data.keys() if k != "uid"), "unknown")
                log(f"      <- WS message #{i+1}: {msg_type}", "WS")
                
                if "updateDescription" in data:
                    update_desc = data["updateDescription"]
                    log(f"      -> updateDescription: {json.dumps(update_desc)[:200]}...", "DEBUG")
                    
                    if "description" in update_desc:
                        for desc_item in update_desc["description"]:
                            peer_id = desc_item.get("id")
                            
                            if peer_id:
                                log(f"      -> Found peer in updateDescription: {peer_id}", "DEBUG")
                                
                                if peer_id not in collected_peer_ids:
                                    collected_peer_ids.add(peer_id)
                                    
                                    meta = desc_item.get("meta", {})
                                    peer_data = {
                                        "peer_id": peer_id,
                                        "display_name": meta.get("name"),
                                        "role": meta.get("role"),
                                        "send_audio": meta.get("sendAudio"),
                                        "send_video": meta.get("sendVideo"),
                                        "send_sharing": desc_item.get("sendSharing", False)
                                    }
                                    
                                    info["participants"]["list"].append(peer_data)
                                    audio_status = "ON" if peer_data['send_audio'] else "OFF"
                                    video_status = "ON" if peer_data['send_video'] else "OFF"
                                    log(f"      :P {peer_data['display_name']} (A:{audio_status} V:{video_status})")
                    
                    await ws.send(json.dumps({"uid": data["uid"], "ack": {"status": {"code": "OK"}}}))
                
                if "serverHello" in data:
                    server_hello = data["serverHello"]
                    
                    if "capabilitiesAnswer" in server_hello:
                        caps = server_hello["capabilitiesAnswer"]
                        info["webrtc"]["capabilities"] = {
                            "offerAnswerMode": caps.get("offerAnswerMode"),
                            "initialSubscriberOffer": caps.get("initialSubscriberOffer"),
                            "slotsMode": caps.get("slotsMode"),
                            "simulcastMode": caps.get("simulcastMode"),
                            "selfVadStatus": caps.get("selfVadStatus"),
                            "dataChannelSharing": caps.get("dataChannelSharing"),
                            "videoEncoderConfig": caps.get("videoEncoderConfig"),
                            "dataChannelVideoCodec": caps.get("dataChannelVideoCodec"),
                            "bandwidthLimitationReason": caps.get("bandwidthLimitationReason"),
                            "publisherVp9": caps.get("publisherVp9"),
                            "svcMode": caps.get("svcMode")
                        }
                    
                    if "servingComponents" in server_hello:
                        info["server"]["components"] = []
                        for comp in server_hello["servingComponents"]:
                            info["server"]["components"].append({
                                "type": comp.get("type"),
                                "host": comp.get("host"),
                                "version": comp.get("version")
                            })
                    
                    if "rtcConfiguration" in server_hello:
                        rtc_config = server_hello["rtcConfiguration"]
                        info["webrtc"]["ice"]["servers"] = []
                        
                        for server in rtc_config.get("iceServers", []):
                            info["webrtc"]["ice"]["servers"].append({
                                "urls": server.get("urls"),
                                "has_credentials": bool(server.get("credential"))
                            })
                    
                    if "pingPongConfiguration" in server_hello:
                        ping_config = server_hello["pingPongConfiguration"]
                        info["webrtc"]["ping"] = {
                            "interval_ms": ping_config.get("pingInterval"),
                            "ack_timeout_ms": ping_config.get("ackTimeout")
                        }
                    
                    if "telemetryConfiguration" in server_hello:
                        telem_config = server_hello["telemetryConfiguration"]
                        info["webrtc"]["telemetry"] = {
                            "sending_interval_ms": telem_config.get("sendingInterval")
                        }
                    
                    await ws.send(json.dumps({"uid": data["uid"], "ack": {"status": {"code": "OK"}}}))
                    log("      :P Server hello")
                
                if "subscriberSdpOffer" in data:
                    sdp = data["subscriberSdpOffer"]["sdp"]
                    
                    info["webrtc"]["sdp"]["raw"] = sdp
                    info["webrtc"]["sdp"]["lines"] = sdp.count('\r\n')
                    
                    info["webrtc"]["audio"]["codecs"] = []
                    info["webrtc"]["video"]["codecs"] = []
                    info["webrtc"]["datachannel"]["supported"] = False
                    
                    for line in sdp.split('\r\n'):
                        if line.startswith('a=rtpmap:') and 'm=audio' in sdp[:sdp.find(line)]:
                            parts = line.split()
                            if len(parts) >= 2:
                                codec_info = parts[1].split('/')
                                info["webrtc"]["audio"]["codecs"].append({
                                    "name": codec_info[0],
                                    "rate": int(codec_info[1]) if len(codec_info) > 1 else None,
                                    "channels": int(codec_info[2]) if len(codec_info) > 2 else None
                                })
                        
                        if line.startswith('a=rtpmap:') and 'm=video' in sdp[:sdp.find(line)]:
                            parts = line.split()
                            if len(parts) >= 2:
                                codec_info = parts[1].split('/')
                                codec_name = codec_info[0].upper()
                                if codec_name not in ['RTX', 'RED', 'ULPFEC']:
                                    if codec_name not in [c["name"] for c in info["webrtc"]["video"]["codecs"]]:
                                        info["webrtc"]["video"]["codecs"].append({
                                            "name": codec_name,
                                            "rate": int(codec_info[1]) if len(codec_info) > 1 else None
                                        })
                        
                        if 'm=application' in line and 'SCTP' in line:
                            info["webrtc"]["datachannel"]["supported"] = True
                        
                        if 'sctp-port:' in line:
                            info["webrtc"]["datachannel"]["sctp_port"] = int(line.split(':')[1].strip())
                        
                        if 'max-message-size:' in line:
                            size = int(line.split(':')[1].strip())
                            info["webrtc"]["datachannel"]["max_message_size"] = size
                            info["webrtc"]["datachannel"]["max_message_size_mb"] = size / 1024 / 1024
                        
                        if line.startswith('a=fmtp:') and 'opus' in sdp[max(0, sdp.find(line)-200):sdp.find(line)].lower():
                            params = line.split(':', 1)[1].strip().split(';')
                            info["webrtc"]["audio"]["opus_params"] = {}
                            for param in params:
                                if '=' in param:
                                    key, val = param.strip().split('=')
                                    info["webrtc"]["audio"]["opus_params"][key] = val
                        
                        if line.startswith('a=extmap:'):
                            if "rtp_extensions" not in info["webrtc"]:
                                info["webrtc"]["rtp_extensions"] = []
                            ext_info = line.split()[1]
                            info["webrtc"]["rtp_extensions"].append(ext_info)
                    
                    await ws.send(json.dumps({"uid": data["uid"], "ack": {"status": {"code": "OK"}}}))
                    log("      :P SDP analyzed")
                    
            except asyncio.TimeoutError:
                log(f"      -> Timeout on message #{i+1}, continuing...", "DEBUG")
                break
            except Exception as e:
                log(f"      X Error processing message: {e}", "ERROR")
                break
        
        info["participants"]["count"] = len(info["participants"]["list"])
        
        await ws.close()
        log("      :P Closed")
        
        log("\n      -> Fetching all participants via API...")
        try:
            all_participants = get_participants_list()
            
            log(f"      -> API returned: peers={len(all_participants.get('peers', []))}, permissions={bool(all_participants.get('permissions'))}", "DEBUG")
            
            if "peers" in all_participants and all_participants["peers"]:
                log(f"      :P Found {len(all_participants['peers'])} participants via API")
                
                for peer in all_participants["peers"]:
                    peer_id = peer.get("peer_id")
                    if peer_id and peer_id not in collected_peer_ids:
                        peer_data = {
                            "peer_id": peer_id,
                            "peer_type": peer.get("peer_type"),
                            "send_audio": None,
                            "send_video": None,
                            "send_sharing": None
                        }
                        
                        if "state" in peer and "user_data" in peer["state"]:
                            user_data = peer["state"]["user_data"]
                            peer_data["display_name"] = user_data.get("display_name")
                            peer_data["role"] = user_data.get("role")
                            peer_data["uid"] = user_data.get("uid")
                            if "avatar_placeholder" in user_data:
                                peer_data["avatar"] = user_data["avatar_placeholder"]
                        
                        info["participants"]["list"].append(peer_data)
                        collected_peer_ids.add(peer_id)
                        log(f"      :P {peer_data.get('display_name', 'Unknown')} ({peer_data.get('role', 'N/A')})")
                
                info["participants"]["count"] = len(info["participants"]["list"])
            else:
                log(f"      X No peers in API response", "WARN")
            
            if "permissions" in all_participants:
                info["conference"] = {"permissions": all_participants["permissions"]}
                if "conference" in all_participants:
                    info["conference"]["state"] = all_participants["conference"].get("state")
                    
        except Exception as e:
            log(f"      X API request failed: {e}", "ERROR")
        
        log(f"\n      :P Total participants: {info['participants']['count']}")
        
    except Exception as e:
        log(f"      X Error: {e}", "ERROR")
    
    return info

def print_full_report(info):
    print("CONNECTION INFO")
    conn = info["connection"]
    print(f"Type:              {conn.get('connection_type')}")
    print(f"URI:               {conn.get('uri')}")
    print(f"Room ID:           {conn.get('room_id')}")
    print(f"Peer ID:           {conn.get('peer_id')}")
    print(f"Session ID:        {conn.get('session_id')}")
    print(f"Media Platform:    {conn.get('media_platform')}")
    print(f"Max Participants:  {conn.get('conference_limit')}")
    
    if "client_config" in conn:
        cfg = conn["client_config"]
        print(f"\nClient Configuration:")
        print(f"  Media Server:    {cfg.get('media_server_url')}")
        print(f"  Session Timeout: {cfg.get('session_timeout_ms')}ms ({cfg.get('session_timeout_ms', 0)/1000/60:.1f}min)")
        print(f"  Reconnect Wait:  {cfg.get('reconnect_wait_ms')}ms")
    
    print("PARTICIPANTS")
    if "list" in info["participants"] and info["participants"]["list"]:
        for i, peer in enumerate(info["participants"]["list"], 1):
            print(f"\n{i}. {peer.get('display_name', 'Unknown')}")
            print(f"   Peer ID:    {peer.get('peer_id')}")
            print(f"   Role:       {peer.get('role')}")
            if peer.get('uid'):
                print(f"   UID:        {peer.get('uid')}")
            print(f"   Audio:      {peer.get('send_audio')}")
            print(f"   Video:      {peer.get('send_video')}")
            print(f"   Sharing:    {peer.get('send_sharing')}")
            if "avatar" in peer:
                avatar = peer["avatar"]
                print(f"   Avatar:     {avatar.get('abbreviation')} ({avatar.get('background_color')})")
        print(f"\nTotal: {info['participants'].get('count', 0)} participants")
    else:
        print("No participants in conference")
    
    if "conference" in info and "permissions" in info["conference"]:
        print("\n" + "=" * 70)
        print("CONFERENCE PERMISSIONS")
        print("=" * 70)
        perms = info["conference"]["permissions"]
        print(f"Version: {perms.get('version')}")
        
        if "public_role_permissions" in perms:
            print("\nRole Permissions:")
            for role_perm in perms["public_role_permissions"]:
                role = role_perm.get("role")
                allowed = role_perm.get("allowed", [])
                print(f"  {role}: {', '.join(allowed)}")
        
        if "personal_allowed" in perms:
            print(f"\nPersonal: {', '.join(perms['personal_allowed'])}")
        
        if "state" in info["conference"]:
            state = info["conference"]["state"]
            print("\nConference State:")
            print(f"  Access Level:       {state.get('access_level')}")
            print(f"  Recording:          {state.get('local_recording_allowed')}")
            print(f"  Cloud Recording:    {state.get('cloud_recording_allowed')}")
            print(f"  Chat:               {state.get('chat_allowed')}")
            print(f"  Control:            {state.get('control_allowed')}")
            print(f"  Broadcast:          {state.get('broadcast_allowed')}")
    
    print("WEBRTC CAPABILITIES")
    if "capabilities" in info["webrtc"]:
        caps = info["webrtc"]["capabilities"]
        print(f"Offer/Answer Mode:        {caps.get('offerAnswerMode')}")
        print(f"Initial Subscriber Offer: {caps.get('initialSubscriberOffer')}")
        print(f"Slots Mode:               {caps.get('slotsMode')}")
        print(f"Simulcast Mode:           {caps.get('simulcastMode')}")
        print(f"VAD Status:               {caps.get('selfVadStatus')}")
        print(f"DataChannel Sharing:      {caps.get('dataChannelSharing')}")
        print(f"Video Encoder Config:     {caps.get('videoEncoderConfig')}")
        print(f"DC Video Codec:           {caps.get('dataChannelVideoCodec')}")
        print(f"Bandwidth Limitation:     {caps.get('bandwidthLimitationReason')}")
        print(f"Publisher VP9:            {caps.get('publisherVp9')}")
        print(f"SVC Mode:                 {caps.get('svcMode')}")
    
    print("AUDIO")
    if "codecs" in info["webrtc"]["audio"]:
        print("Codecs:")
        for codec in info["webrtc"]["audio"]["codecs"]:
            print(f"  - {codec['name']}: {codec.get('rate', 'N/A')}Hz, {codec.get('channels', 'N/A')} channels")
    
    if "opus_params" in info["webrtc"]["audio"]:
        print("\nOpus Parameters:")
        for key, val in info["webrtc"]["audio"]["opus_params"].items():
            print(f"  {key}: {val}")
    
    print("VIDEO")
    if "codecs" in info["webrtc"]["video"]:
        print("Codecs:")
        for codec in info["webrtc"]["video"]["codecs"]:
            print(f"  - {codec['name']}: {codec.get('rate', 'N/A')}Hz")
    
    print("DATACHANNEL")
    dc = info["webrtc"]["datachannel"]
    print(f"Supported:         {':P YES' if dc.get('supported') else 'X NO'}")
    if dc.get("supported"):
        print(f"SCTP Port:         {dc.get('sctp_port')}")
        print(f"Max Message Size:  {dc.get('max_message_size_mb', 0):.0f}MB ({dc.get('max_message_size', 0):,} bytes)")
        print(f"Note:              Actual limit is 8KB due to server fragmentation")
    
    print("ICE/NETWORK")
    if "servers" in info["webrtc"]["ice"]:
        stun_count = sum(1 for s in info["webrtc"]["ice"]["servers"] if any('stun:' in u for u in s.get("urls", [])))
        turn_count = sum(1 for s in info["webrtc"]["ice"]["servers"] if any('turn:' in u for u in s.get("urls", [])))
        
        print(f"STUN Servers: {stun_count}")
        print(f"TURN Servers: {turn_count}")
        print("\nServers:")
        for server in info["webrtc"]["ice"]["servers"]:
            for url in server.get("urls", []):
                cred_status = "with credentials" if server.get("has_credentials") else "no credentials"
                print(f"  - {url} ({cred_status})")
    
    if "ping" in info["webrtc"]:
        ping = info["webrtc"]["ping"]
        print(f"\nPing Configuration:")
        print(f"  Interval:     {ping.get('interval_ms')}ms")
        print(f"  ACK Timeout:  {ping.get('ack_timeout_ms')}ms")
    
    if "rtp_extensions" in info["webrtc"]:
        print(f"\nRTP Extensions: {len(info['webrtc']['rtp_extensions'])}")
        for ext in info["webrtc"]["rtp_extensions"][:5]:
            print(f"  - {ext}")
    
    print("SERVER COMPONENTS")
    if "components" in info["server"]:
        for comp in info["server"]["components"]:
            print(f"{comp.get('type'):20} {comp.get('host'):30} v{comp.get('version')}")
    
    if "telemetry" in info["webrtc"]:
        telem = info["webrtc"]["telemetry"]
        print(f"\nTelemetry:")
        print(f"  Sending Interval: {telem.get('sending_interval_ms')}ms")
    
    print("SDP STATISTICS")
    if "lines" in info["webrtc"]["sdp"]:
        print(f"Total SDP Lines: {info['webrtc']['sdp']['lines']}")
        print(f"SDP Size:        {len(info['webrtc']['sdp'].get('raw', ''))} bytes")

async def main():
    log(f"Starting WebRTC info collection...")
    
    info = await collect_webrtc_info()
    
    log("\n[5/5] Generating report...\n")
    print_full_report(info)

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        log("\n\nInfo collection interrupted.", "WARN")
