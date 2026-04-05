#!/usr/bin/env python3

import asyncio
import json
import uuid
import websockets
import requests
import qrcode
import cv2
import numpy as np
import base64
import os
import time
from urllib.parse import quote
from cryptography.hazmat.primitives.ciphers.aead import ChaCha20Poly1305
from aiortc import RTCPeerConnection, RTCSessionDescription, RTCIceCandidate, RTCConfiguration, RTCIceServer
from aiortc.mediastreams import MediaStreamTrack
from av import VideoFrame
from PIL import Image
from pyzbar import pyzbar
from fractions import Fraction

CONFERENCE_ID = "75047680642749"
CONFERENCE_URL = f"https://telemost.yandex.ru/j/{CONFERENCE_ID}"
API_BASE = "https://cloud-api.yandex.ru/telemost_front/v2/telemost"

QR_SIZE = 600
CHUNK_SIZE = 400
FRAME_RATE = 1
SHARED_KEY = os.urandom(32)

def gen_uid():
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
        "Client-Instance-Id": gen_uid(),
        "X-Telemost-Client-Version": "187.1.0",
        "idempotency-key": gen_uid(),
        "Origin": "https://telemost.yandex.ru",
        "Referer": "https://telemost.yandex.ru/"
    }
    r = requests.get(url, params=params, headers=headers)
    r.raise_for_status()
    return r.json()

def encrypt_payload(tag_str, data_bytes):
    nonce = os.urandom(12)
    chacha = ChaCha20Poly1305(SHARED_KEY)
    ciphertext = chacha.encrypt(nonce, data_bytes, None)
    blob = nonce + ciphertext
    tag_bytes = tag_str.encode('ascii').ljust(4, b'\x00')[:4]
    len_bytes = len(blob).to_bytes(4, 'big')
    return tag_bytes + len_bytes + blob

def decrypt_payload(envelope):
    tag = envelope[:4].decode('ascii').strip('\x00')
    length = int.from_bytes(envelope[4:8], 'big')
    blob = envelope[8:8+length]
    nonce = blob[:12]
    ciphertext = blob[12:]
    chacha = ChaCha20Poly1305(SHARED_KEY)
    data = chacha.decrypt(nonce, ciphertext, None)
    return tag, data

def make_qr_frame(data, pts):
    qr = qrcode.QRCode(
        version=None,
        error_correction=qrcode.constants.ERROR_CORRECT_M,
        box_size=12,
        border=4
    )
    qr.add_data(data)
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white").resize(
        (QR_SIZE, QR_SIZE), Image.NEAREST
    )
    arr = np.array(img.convert('RGB'))
    frame = VideoFrame.from_ndarray(arr, format="rgb24")
    frame.pts = pts
    frame.time_base = Fraction(1, FRAME_RATE)
    return frame

def chunk_data(data, tid):
    b64 = base64.b64encode(data).decode()
    n = (len(b64) + CHUNK_SIZE - 1) // CHUNK_SIZE
    return [json.dumps({"tid": tid, "idx": i, "total": n,
                        "data": b64[i * CHUNK_SIZE:(i + 1) * CHUNK_SIZE]})
            for i in range(n)]

class QRVideoTrack(MediaStreamTrack):
    kind = "video"

    def __init__(self):
        super().__init__()
        self._frames = []
        self._idx = 0
        self._pts = 0

    def set_data(self, chunks):
        self._frames = [make_qr_frame(c, i) for i, c in enumerate(chunks)]
        self._idx = 0
        self._pts = 0

    async def recv(self):
        await asyncio.sleep(1.0 / FRAME_RATE)
        if not self._frames:
            f = make_qr_frame("WAIT", self._pts)
            self._pts += 1
            return f
        f = self._frames[self._idx]
        f.pts = self._pts
        f.time_base = Fraction(1, FRAME_RATE)
        self._pts += 1
        self._idx = (self._idx + 1) % len(self._frames)
        return f

class DualReceiver:
    def __init__(self):
        self._bufs = {}
        self.vc_result = None
        self.dc_result = None
        self._cv2_detector = cv2.QRCodeDetector()

    def feed_frame(self, frame):
        if self.vc_result is not None:
            return False
            
        try:
            arr = frame.to_ndarray(format="rgb24")
            h, w = arr.shape[:2]
            gray = cv2.cvtColor(arr, cv2.COLOR_RGB2GRAY)

            variants = [
                gray,
                cv2.resize(gray, (w * 2, h * 2), interpolation=cv2.INTER_CUBIC),
                cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)[1],
            ]

            decoded = set()
            for v in variants:
                try:
                    for code in pyzbar.decode(v):
                        decoded.add(code.data.decode('utf-8'))
                except Exception:
                    pass
                try:
                    val, _, _ = self._cv2_detector.detectAndDecode(v)
                    if val:
                        decoded.add(val)
                except Exception:
                    pass

            for raw in decoded:
                try:
                    pkt = json.loads(raw)
                    tid = pkt["tid"]
                    idx = pkt["idx"]
                    total = pkt["total"]
                    data = pkt["data"]
                    if tid not in self._bufs:
                        self._bufs[tid] = {}
                    if idx not in self._bufs[tid]:
                        self._bufs[tid][idx] = data
                    if len(self._bufs[tid]) == total:
                        b64 = "".join(self._bufs[tid][i] for i in range(total))
                        self.vc_result = base64.b64decode(b64)
                        return True
                except Exception:
                    pass
        except Exception:
            pass
        return False

async def process_track(track, receiver):
    while True:
        try:
            frame = await asyncio.wait_for(track.recv(), timeout=30.0)
            if receiver.feed_frame(frame):
                return
        except Exception:
            return

def make_ice_servers(raw_list):
    result = []
    for s in raw_list:
        urls = s.get("urls", [])
        cred = s.get("credential", "")
        user = s.get("username", "")
        if cred:
            result.append(RTCIceServer(urls=urls, credential=cred, username=user))
        else:
            result.append(RTCIceServer(urls=urls))
    return result or [RTCIceServer(urls=["stun:stun.rtc.yandex.net:3478"])]

async def connect_peer(name, conn):
    room_id = conn["room_id"]
    peer_id = conn["peer_id"]
    credentials = conn["credentials"]
    ws_url = conn["client_configuration"]["media_server_url"]
    is_sender = "Sender" in name

    default_ice = [RTCIceServer(urls=["stun:stun.rtc.yandex.net:3478"])]

    video_track = QRVideoTrack() if is_sender else None
    receiver_obj = DualReceiver() if not is_sender else None
    track_tasks = []
    
    pc_sub_ref = [RTCPeerConnection(RTCConfiguration(iceServers=default_ice))]
    pc_pub_ref = [RTCPeerConnection(RTCConfiguration(iceServers=default_ice))]

    dc_pub_ref = []
    dc_open_event = asyncio.Event()

    if is_sender:
        pc_pub_ref[0].addTrack(video_track)
        dc = pc_pub_ref[0].createDataChannel("invisible", ordered=True)
        dc_pub_ref.append(dc)
        
        @dc.on("open")
        def on_open():
            dc_open_event.set()

    ws = await websockets.connect(
        ws_url,
        additional_headers={
            "Origin": "https://telemost.yandex.ru",
            "User-Agent": "Mozilla/5.0 (X11; Linux x86_64; rv:149.0) Gecko/20100101 Firefox/149.0"
        }
    )

    async def send(obj):
        await ws.send(json.dumps(obj))

    async def ack(uid):
        await send({"uid": uid, "ack": {"status": {"code": "OK", "description": ""}}})

    def setup_pc(pc_sub, pc_pub):
        if not is_sender:
            @pc_sub.on("datachannel")
            def on_datachannel(channel):
                @channel.on("message")
                def on_message(message):
                    if receiver_obj is not None:
                        receiver_obj.dc_result = message

        @pc_sub.on("track")
        def on_track(track):
            if track.kind == "video" and receiver_obj is not None:
                t = asyncio.ensure_future(process_track(track, receiver_obj))
                track_tasks.append(t)

        @pc_sub.on("icecandidate")
        async def on_sub_ice(e):
            if e.candidate:
                await send({
                    "uid": gen_uid(),
                    "webrtcIceCandidate": {
                        "candidate": e.candidate.candidate,
                        "sdpMid": e.candidate.sdpMid,
                        "sdpMlineIndex": e.candidate.sdpMLineIndex,
                        "usernameFragment": "",
                        "target": "SUBSCRIBER",
                        "pcSeq": 1
                    }
                })

        @pc_pub.on("icecandidate")
        async def on_pub_ice(e):
            if e.candidate:
                await send({
                    "uid": gen_uid(),
                    "webrtcIceCandidate": {
                        "candidate": e.candidate.candidate,
                        "sdpMid": e.candidate.sdpMid,
                        "sdpMlineIndex": e.candidate.sdpMLineIndex,
                        "usernameFragment": "",
                        "target": "PUBLISHER",
                        "pcSeq": 1
                    }
                })

    setup_pc(pc_sub_ref[0], pc_pub_ref[0])

    hello = {
        "uid": gen_uid(),
        "hello": {
            "participantMeta": {
                "name": name, "role": "SPEAKER", "description": "",
                "sendAudio": False, "sendVideo": is_sender
            },
            "participantAttributes": {"name": name, "role": "SPEAKER", "description": ""},
            "sendAudio": False,
            "sendVideo": is_sender,
            "sendSharing": False,
            "participantId": peer_id,
            "roomId": room_id,
            "serviceName": "telemost",
            "credentials": credentials,
            "capabilitiesOffer": {
                "offerAnswerMode": ["SEPARATE"],
                "initialSubscriberOffer": ["ON_HELLO"],
                "slotsMode": ["FROM_CONTROLLER"],
                "simulcastMode": ["DISABLED", "STATIC"],
                "selfVadStatus": ["FROM_SERVER", "FROM_CLIENT"],
                "dataChannelSharing": ["TO_RTP"],
                "videoEncoderConfig": ["NO_CONFIG", "ONLY_INIT_CONFIG", "RUNTIME_CONFIG"],
                "dataChannelVideoCodec": ["VP8", "UNIQUE_CODEC_FROM_TRACK_DESCRIPTION"]
            },
            "sdkInfo": {
                "implementation": "browser",
                "version": "5.27.0",
                "userAgent": "Mozilla/5.0",
                "hwConcurrency": 24
            },
            "sdkInitializationId": gen_uid(),
            "disablePublisher": not is_sender,
            "disableSubscriber": False,
            "disableSubscriberAudio": True
        }
    }

    await send(hello)
    pub_sdp_sent = False

    async def ws_loop():
        nonlocal pub_sdp_sent
        try:
            async for raw in ws:
                msg = json.loads(raw)
                keys = [k for k in msg if k != "uid"]
                if not keys:
                    continue
                mtype = keys[0]
                uid = msg.get("uid", "")

                if mtype == "ack":
                    pass

                elif mtype == "serverHello":
                    sh = msg["serverHello"]
                    raw_ice = sh.get("rtcConfiguration", {}).get("iceServers", [])
                    if raw_ice:
                        ice = make_ice_servers(raw_ice)
                        old_sub = pc_sub_ref[0]
                        old_pub = pc_pub_ref[0]
                        pc_sub_ref[0] = RTCPeerConnection(RTCConfiguration(iceServers=ice))
                        pc_pub_ref[0] = RTCPeerConnection(RTCConfiguration(iceServers=ice))
                        
                        if is_sender:
                            pc_pub_ref[0].addTrack(video_track)
                            dc = pc_pub_ref[0].createDataChannel("invisible", ordered=True)
                            dc_pub_ref.clear()
                            dc_pub_ref.append(dc)
                            @dc.on("open")
                            def on_open():
                                dc_open_event.set()

                        setup_pc(pc_sub_ref[0], pc_pub_ref[0])
                        await old_sub.close()
                        await old_pub.close()
                    await ack(uid)

                elif mtype == "subscriberSdpOffer":
                    offer_sdp = msg["subscriberSdpOffer"]["sdp"]
                    pc_seq = msg["subscriberSdpOffer"]["pcSeq"]
                    pc_sub = pc_sub_ref[0]
                    pc_pub = pc_pub_ref[0]

                    await pc_sub.setRemoteDescription(
                        RTCSessionDescription(sdp=offer_sdp, type="offer")
                    )
                    answer = await pc_sub.createAnswer()
                    await pc_sub.setLocalDescription(answer)

                    await send({
                        "uid": gen_uid(),
                        "subscriberSdpAnswer": {
                            "pcSeq": pc_seq,
                            "sdp": pc_sub.localDescription.sdp
                        }
                    })
                    await ack(uid)

                    if not is_sender:
                        await send({
                            "uid": gen_uid(),
                            "setSlots": {
                                "slots": [{"width": 1280, "height": 720}],
                                "audioSlotsCount": 0,
                                "key": 1,
                                "shutdownAllVideo": None,
                                "withSelfView": False,
                                "selfViewVisibility": "ON_LOADING_THEN_SHOW",
                                "gridConfig": {}
                            }
                        })

                    if is_sender and not pub_sdp_sent:
                        await asyncio.sleep(0.3)
                        pub_offer = await pc_pub.createOffer()
                        await pc_pub.setLocalDescription(pub_offer)
                        tracks_info = []
                        for t in pc_pub.getTransceivers():
                            if t.sender.track:
                                tracks_info.append({
                                    "mid": t.mid,
                                    "transceiverMid": t.mid,
                                    "kind": t.sender.track.kind.upper(),
                                    "priority": 0,
                                    "label": "QRVideoTrack",
                                    "codecs": {},
                                    "groupId": 1,
                                    "description": ""
                                })
                        await send({
                            "uid": gen_uid(),
                            "publisherSdpOffer": {
                                "pcSeq": 1,
                                "sdp": pc_pub.localDescription.sdp,
                                "tracks": tracks_info
                            }
                        })
                        pub_sdp_sent = True

                elif mtype == "publisherSdpAnswer":
                    await pc_pub_ref[0].setRemoteDescription(
                        RTCSessionDescription(sdp=msg["publisherSdpAnswer"]["sdp"], type="answer")
                    )
                    await ack(uid)

                elif mtype == "webrtcIceCandidate":
                    cand = msg["webrtcIceCandidate"]
                    parts = cand.get("candidate", "").split()
                    if len(parts) >= 8:
                        try:
                            ice_c = RTCIceCandidate(
                                component=int(parts[1]),
                                foundation=parts[0].replace("candidate:", ""),
                                ip=parts[4],
                                port=int(parts[5]),
                                priority=int(parts[3]),
                                protocol=parts[2].lower(),
                                type=parts[7],
                                sdpMid=cand.get("sdpMid", "0"),
                                sdpMLineIndex=cand.get("sdpMlineIndex", 0)
                            )
                            if cand.get("target") == "SUBSCRIBER":
                                await pc_sub_ref[0].addIceCandidate(ice_c)
                            elif cand.get("target") == "PUBLISHER":
                                await pc_pub_ref[0].addIceCandidate(ice_c)
                        except Exception:
                            pass

                elif mtype in ("setSlots", "slotsConfig", "slotsMeta", "vadActivity",
                               "updateDescription", "upsertDescription", "sdkCodecsInfo",
                               "pingPong", "selfQualityReport", "upsertParticipantsQualityReport",
                               "removeDescription"):
                    await ack(uid)
                else:
                    if uid:
                        await ack(uid)
        except websockets.exceptions.ConnectionClosed:
            pass
        except Exception:
            pass

    ws_task = asyncio.create_task(ws_loop())

    return {
        "name": name,
        "ws": ws,
        "ws_task": ws_task,
        "pc_pub_ref": pc_pub_ref,
        "pc_sub_ref": pc_sub_ref,
        "video_track": video_track,
        "receiver": receiver_obj,
        "track_tasks": track_tasks,
        "dc_pub_ref": dc_pub_ref,
        "dc_open_event": dc_open_event
    }

async def run():
    print("ChaCha20-Poly1305 over Telemost DC + VC")
    print("text + video encrypted transfer")
    print("      by zarazaex for olc\n")

    sender_conn = get_connection_info("QR_Sender")
    receiver_conn = get_connection_info("QR_Receiver")

    print("[1/4] Generating payloads...")
    text_data = "привет как деееееееееееееееееееееееела".encode('utf-8')
    video_data = os.urandom(2048) 
    
    print(f"-> Text payload: {len(text_data)} bytes")
    print(f"-> Video payload: {len(video_data)} bytes\n")

    print("[2/4] Creating sender peer...")
    sender = await connect_peer("QR_Sender", sender_conn)
    await sender["dc_open_event"].wait()
    print(":P Sender ready\n")

    print("[3/4] Creating receiver peer...")
    receiver = await connect_peer("QR_Receiver", receiver_conn)
    await asyncio.sleep(5)
    print(":P Receiver ready\n")

    print("[4/4] Encrypting and sending...\n")
    
    enc_text = encrypt_payload("TEXT", text_data)
    print(f"[TEXT] Original data ({len(text_data)} bytes):")
    print(f"         UTF-8: {text_data.decode('utf-8')}")
    print(f"         HEX:   {text_data.hex()}")
    print(f"[TEXT] Encrypted envelope ({len(enc_text)} bytes):")
    print(f"         Tag:   {enc_text[:4].hex().upper()}")
    print(f"         Len:   {int.from_bytes(enc_text[4:8], 'big')}")
    print(f"         Blob:  {enc_text[8:72].hex()}...\n")
    
    print("-> Sending TEXT...")
    sender["dc_pub_ref"][0].send(enc_text)
    print(":P Text sent\n")

    enc_video = encrypt_payload("VID\x00", video_data)
    print(f"[VIDEO] Original data ({len(video_data)} bytes):")
    print(f"          HEX:   {video_data[:64].hex()}...")
    print(f"[VIDEO] Encrypted envelope ({len(enc_video)} bytes):")
    print(f"          Tag:   {enc_video[:4].hex().upper()}")
    print(f"          Len:   {int.from_bytes(enc_video[4:8], 'big')}")
    print(f"          Blob:  {enc_video[8:72].hex()}...\n")

    print("-> Sending VIDEO...")
    tid = gen_uid()
    chunks = chunk_data(enc_video, tid)
    sender["video_track"].set_data(chunks)
    print(":P Video sent\n")

    print("-> Waiting for receiver...")
    
    for i in range(120):
        await asyncio.sleep(1)
        if receiver["receiver"].dc_result is not None and receiver["receiver"].vc_result is not None:
            break
            
    dc_res = receiver["receiver"].dc_result
    vc_res = receiver["receiver"].vc_result

    if dc_res:
        print(f"[Receiver] <- received 'TEXT': {len(dc_res)} bytes")
    if vc_res:
        print(f"[Receiver] <- received 'VID': {len(vc_res)} bytes")

    print("\n--- Received & Decrypted ---")
    
    if dc_res:
        tag, dec_text = decrypt_payload(dc_res)
        print(f"[TEXT] Decrypted ({len(dec_text)} bytes):")
        print(f"UTF-8: {dec_text.decode('utf-8')}")
        print(f"HEX:   {dec_text.hex()}")
        
    if vc_res:
        tag, dec_vid = decrypt_payload(vc_res)
        print(f"[VIDEO] Decrypted ({len(dec_vid)} bytes):")
        print(f"HEX:   {dec_vid[:64].hex()}...")

    print("\nCleaning up...")
    for p in [sender, receiver]:
        p["ws_task"].cancel()
        try:
            await p["ws"].close()
        except Exception:
            pass
        try:
            await p["pc_pub_ref"][0].close()
            await p["pc_sub_ref"][0].close()
        except Exception:
            pass
    print(":P Done")

if __name__ == "__main__":
    try:
        asyncio.run(run())
    except KeyboardInterrupt:
        pass