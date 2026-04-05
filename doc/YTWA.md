# Yandex Telemost WebRTC API Documentation - YTWA

## Overview

Yandex Telemost implements a Selective Forwarding Unit (SFU) architecture for WebRTC conferencing. The system supports audio, video, and SCTP DataChannel transport over WebRTC with separate publisher and subscriber peer connections.

**Project includes practical implementations:**
- `dcsend.py` - HTTP requests via DataChannel with chunking
- `vcsend.py` - Data transfer via video QR codes  
- `flood.py` - Stress testing connections
- `limits.py` - Limits and performance verification
- `info.py` - Conference information gathering
- `poc.py` - Basic proof-of-concept

## Architecture

### Connection Model

- SFU-based routing (not P2P)
- Separate PeerConnections for publishing and subscribing
- WebSocket signaling channel
- STUN/TURN infrastructure for NAT traversal

### Transport Capabilities

- Audio: Opus codec (48kHz, stereo, DTX, FEC)
- Video: VP8, VP9, H.264, AV1 codecs with simulcast support
- DataChannel: SCTP over DTLS (max message size: 1023MB, port: 5000)

## API Endpoints

### Base URL

```
https://cloud-api.yandex.ru/telemost_front/v2/telemost
```

### 1. Connection Initialization

**Endpoint:** `GET /conferences/{encoded_conference_url}/connection`

**Parameters:**
- `next_gen_media_platform_allowed`: boolean (string "true")
- `display_name`: string (URL-encoded participant name)
- `waiting_room_supported`: boolean (string "true")

**Headers:**
- `User-Agent`: Browser user agent string
- `Accept`: "*/*"
- `content-type`: "application/json"
- `Client-Instance-Id`: UUID v4
- `X-Telemost-Client-Version`: Version string (e.g., "187.1.0")
- `idempotency-key`: UUID v4
- `Origin`: "https://telemost.yandex.ru"
- `Referer`: "https://telemost.yandex.ru/"

**Response:**
```json
{
  "connection_type": "CONFERENCE",
  "uri": "https://telemost.yandex.ru/j/{conference_id}",
  "room_id": "uuid",
  "safe_room_id": "uuid",
  "peer_id": "uuid",
  "session_id": "uuid",
  "peer_session_id": "uuid",
  "credentials": "string",
  "expiration_time": 1775424866469,
  "conference_limit": 40,
  "media_platform": "GOLOOM",
  "client_configuration": {
    "media_server_url": "wss://goloom.strm.yandex.net/join",
    "service_name": "telemost",
    "ice_servers": [
      {
        "urls": ["stun:stun.rtc.yandex.net:3478"]
      }
    ],
    "goloom_session_open_ms": 120000,
    "wait_time_to_reconnect_ms": 3553
  }
}
```

## WebSocket Protocol

### Connection

**URL:** Obtained from `client_configuration.media_server_url`

**Protocol:** WSS (WebSocket Secure)

### Message Format

All messages are JSON objects with a `uid` field (UUID v4) and one message-specific field.

### Message Types

#### 1. Client Hello

**Direction:** Client → Server

**Structure:**
```json
{
  "uid": "uuid",
  "hello": {
    "participantMeta": {
      "name": "string",
      "role": "SPEAKER",
      "description": "string",
      "sendAudio": boolean,
      "sendVideo": boolean
    },
    "participantAttributes": {
      "name": "string",
      "role": "SPEAKER",
      "description": "string"
    },
    "sendAudio": boolean,
    "sendVideo": boolean,
    "sendSharing": boolean,
    "participantId": "uuid",
    "roomId": "uuid",
    "serviceName": "telemost",
    "credentials": "string",
    "capabilitiesOffer": {
      "offerAnswerMode": ["SEPARATE"],
      "initialSubscriberOffer": ["ON_HELLO"],
      "slotsMode": ["FROM_CONTROLLER"],
      "simulcastMode": ["DISABLED", "STATIC"],
      "selfVadStatus": ["FROM_SERVER", "FROM_CLIENT"],
      "dataChannelSharing": ["TO_RTP"]
    },
    "sdkInfo": {
      "implementation": "string",
      "version": "string",
      "userAgent": "string",
      "hwConcurrency": integer
    },
    "sdkInitializationId": "uuid",
    "disablePublisher": boolean,
    "disableSubscriber": boolean,
    "disableSubscriberAudio": boolean
  }
}
```

#### 2. Server Hello

**Direction:** Server → Client

**Structure:**
```json
{
  "uid": "uuid",
  "serverHello": {
    "capabilitiesAnswer": {
      "offerAnswerMode": "SEPARATE",
      "initialSubscriberOffer": "ON_HELLO",
      "slotsMode": "FROM_CONTROLLER",
      "simulcastMode": "DISABLED",
      "selfVadStatus": "FROM_SERVER",
      "dataChannelSharing": "TO_RTP",
      "videoEncoderConfig": "NO_CONFIG",
      "dataChannelVideoCodec": "UNIQUE_CODEC_FROM_TRACK_DESCRIPTION",
      "bandwidthLimitationReason": "BANDWIDTH_REASON_ENABLED",
      "publisherVp9": "PUBLISH_VP9_ENABLED",
      "svcMode": "SVC_MODE_L3T3_KEY"
    },
    "servingComponents": [
      {
        "type": "BORDER|WEBRTC_SERVER|CONTROLLER",
        "host": "string",
        "version": "string"
      }
    ],
    "sessionSecret": "uuid",
    "sfuPeerInitializationId": "uuid",
    "rtcConfiguration": {
      "iceServers": [
        {
          "urls": ["stun:turn.tel.yandex.net", "stun:stun.rtc.yandex.net"],
          "credential": "",
          "username": ""
        },
        {
          "urls": ["turn:turn.tel.yandex.net:443"],
          "credential": "string",
          "username": "string"
        }
      ]
    },
    "pingPongConfiguration": {
      "pingInterval": 5000,
      "ackTimeout": 9000
    },
    "telemetryConfiguration": {
      "sendingInterval": 20000
    }
  }
}
```

#### 3. Acknowledgment

**Direction:** Client → Server

**Structure:**
```json
{
  "uid": "uuid",
  "ack": {
    "status": {
      "code": "OK",
      "description": "string"
    }
  }
}
```

#### 4. Subscriber SDP Offer

**Direction:** Server → Client

**Structure:**
```json
{
  "uid": "uuid",
  "subscriberSdpOffer": {
    "pcSeq": integer,
    "sdp": "string"
  }
}
```

**SDP Format:** Standard WebRTC SDP with bundled media streams. Includes:
- Video tracks (m=video) with VP8, VP9, H.264, AV1 codecs
- Audio tracks (m=audio) with Opus, PCMA, PCMU, G722 codecs
- Application track (m=application) for SCTP DataChannel

#### 5. Subscriber SDP Answer

**Direction:** Client → Server

**Structure:**
```json
{
  "uid": "uuid",
  "subscriberSdpAnswer": {
    "pcSeq": integer,
    "sdp": "string"
  }
}
```

#### 6. Publisher SDP Offer

**Direction:** Client → Server

**Structure:**
```json
{
  "uid": "uuid",
  "publisherSdpOffer": {
    "pcSeq": integer,
    "sdp": "string"
  }
}
```

#### 7. Publisher SDP Answer

**Direction:** Server → Client

**Structure:**
```json
{
  "uid": "uuid",
  "publisherSdpAnswer": {
    "pcSeq": integer,
    "sdp": "string"
  }
}
```

#### 8. ICE Candidate

**Direction:** Bidirectional

**Structure:**
```json
{
  "uid": "uuid",
  "webrtcIceCandidate": {
    "candidate": "string",
    "sdpMid": "string",
    "sdpMlineIndex": integer,
    "usernameFragment": "string",
    "target": "PUBLISHER|SUBSCRIBER",
    "pcSeq": integer
  }
}
```

**Candidate Format:** Standard ICE candidate string
```
candidate:{foundation} {component} {protocol} {priority} {ip} {port} typ {type} [tcptype {tcptype}]
```

#### 9. VAD Activity

**Direction:** Server → Client

**Structure:**
```json
{
  "uid": "uuid",
  "vadActivity": {
    "active": boolean
  }
}
```

#### 10. Update Description

**Direction:** Server → Client

**Structure:**
```json
{
  "uid": "uuid",
  "updateDescription": {
    "description": [
      {
        "id": "peer-uuid",
        "meta": {
          "name": "string",
          "role": "SPEAKER",
          "description": "string",
          "sendAudio": boolean,
          "sendVideo": boolean
        },
        "participantAttributes": {
          "name": "string",
          "role": "SPEAKER",
          "description": "string"
        },
        "sendSharing": boolean,
        "tracks": []
      }
    ]
  }
}
```

**Description:** Sent by server when participants join/leave or change their media state. Contains full state of all participants in the conference.

## Connection Flow

### 1. Initialization Phase

1. Client requests connection info via REST API
2. Server responds with room credentials and WebSocket URL
3. Client establishes WebSocket connection

### 2. Handshake Phase

1. Client sends `hello` message with capabilities
2. Server responds with `serverHello` containing negotiated capabilities
3. Client acknowledges with `ack` message

### 3. Subscriber Setup

1. Server sends `subscriberSdpOffer` with remote media tracks
2. Client creates answer and sends `subscriberSdpAnswer`
3. Client acknowledges offer
4. ICE candidates exchanged for subscriber PeerConnection

### 4. Publisher Setup

1. Client creates offer and sends `publisherSdpOffer`
2. Server responds with `publisherSdpAnswer`
3. Client acknowledges answer
4. ICE candidates exchanged for publisher PeerConnection

### 5. Media Exchange

1. DataChannel opens on publisher PeerConnection
2. Audio/video tracks activated based on configuration
3. Bidirectional media flow through SFU

## DataChannel Specifications

### Configuration

- **Protocol:** SCTP over DTLS
- **Port:** 5000
- **Max Message Size (Advertised):** 1,073,741,823 bytes (1023 MB)
- **Max Message Size (Actual):** 8,192 bytes (8 KB)
- **Ordered:** Configurable (recommended: true)
- **Label:** Custom (e.g., "dcsend", "olcrtc", "limits_test")

### SDP Attributes

```
m=application 9 UDP/DTLS/SCTP webrtc-datachannel
a=sctp-port:5000
a=max-message-size:1073741823
```

### Message Size Limitations

**CRITICAL LIMITATION:** GOLOOM media server enforces SCTP message limit to 8KB despite advertising 1023MB in SDP. Messages over 8KB are silently dropped.

**Verified limits (from `limits.py`):**
- + 8KB (8,192 bytes): Delivered
- X 10KB (10,240 bytes): Dropped
- X 12KB+ : Dropped

**Root cause:** SCTP fragmentation limit. Messages requiring more than ~6-7 UDP packets (MTU 1500) exceed server's reassembly buffer.

### Large Data Transfer

Для данных свыше 8KB используйте чанкинг на уровне приложения:

**Implementation from `dcsend.py`:**
```python
CHUNK_SIZE = 7168  # Safe size accounting for headers
HEADER_SIZE = 1024

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

class ChunkedReceiver:
    def handle_chunk(self, packet):
        header_bytes = packet[:HEADER_SIZE].rstrip(b'\x00')
        chunk_data = packet[HEADER_SIZE:]
        
        header = json.loads(header_bytes)
        tid, idx, total = header["tid"], header["idx"], header["total"]
        
        # Chunk assembly...
```

**Performance (measured in `limits.py`):**
- 64KB: 2,198ms (239 Kbps)
- 128KB: 2,218ms (473 Kbps) 
- 256KB: 2,204ms (952 Kbps)

**Throttling to prevent overflow:**
```python
while datachannel.bufferedAmount > CHUNK_SIZE * 2:
    await asyncio.sleep(0.001)
datachannel.send(chunk)
```

**Latency characteristics (RTT from `limits.py`):**
- 100 bytes: 42-54ms
- 1KB: 63-74ms
- 4KB: 54-118ms
- 8KB: 84-201ms

## Video Channel Data Transfer

### QR Code Video Streaming

**Alternative data transfer method via video stream (from `vcsend.py`):**

```python
# Encoding data into QR codes
def chunk_data(data, tid):
    b64 = base64.b64encode(data).decode()
    n = (len(b64) + CHUNK_SIZE - 1) // CHUNK_SIZE
    return [json.dumps({"tid": tid, "idx": i, "total": n,
                        "data": b64[i * CHUNK_SIZE:(i + 1) * CHUNK_SIZE]})
            for i in range(n)]

class QRVideoTrack(MediaStreamTrack):
    def set_data(self, chunks):
        self._frames = [make_qr_frame(c, i) for i, c in enumerate(chunks)]
    
    async def recv(self):
        # Cyclic QR frame transmission at 1 FPS
        frame = self._frames[self._idx]
        self._idx = (self._idx + 1) % len(self._frames)
        return frame
```

**Decoding on receiver:**
```python
class QRReceiver:
    def feed_frame(self, frame):
        # Multiple processing variants for reliability
        variants = [gray, upscaled, threshold, threshold_upscaled]
        
        for variant in variants:
            # pyzbar + cv2.QRCodeDetector for maximum compatibility
            decoded_data = decode_qr_codes(variant)
```

**Characteristics:**
- QR size: 600x600 pixels
- Frame rate: 1 FPS
- Chunk size: 400 bytes (base64)
- Error correction: ERROR_CORRECT_M

**Advantages:**
- Works even when DataChannel is blocked
- Visual debugging (frame saving to /tmp/)
- Resilient to packet loss

## Practical Implementations

### HTTP Proxy via DataChannel (`dcsend.py`)

**Client-server architecture:**
```python
# Client sends HTTP request
client["dc_pub"].send(f"GET {url}")

# Server processes request
async def handle_request(url, dc, stats):
    response = requests.get(url, timeout=10)
    data = response.content
    
    # Chunking large responses
    transfer_id = generate_uuid()
    packets = chunk_data(data, transfer_id)
    
    for packet in packets:
        while dc.bufferedAmount > CHUNK_SIZE * 2:
            await asyncio.sleep(0.001)
        dc.send(packet)
```

### Stress Testing (`flood.py`)

**Mass peer connections:**
```python
# Connect up to 411 peers simultaneously
for i in range(1, 412):
    name = f"STOP LET'S BE FRIENDS... {suffix}"
    task = asyncio.create_task(connect_peer(name, i))
    await asyncio.sleep(0.5)  # Connection throttling
```

**Test results:**
- Maximum participants: 40 (conference limit)
- Connection time: ~0.5s per peer
- Stability: WebSocket keep-alive mandatory

### Limits Analysis (`limits.py`)

**Automatic verification of all limitations:**
```python
async def check_all_limits():
    dc_limits = await check_datachannel_limits()      # DataChannel
    conf_limits = await check_conference_limits()     # Conference  
    audio_limits = await check_audio_limits()         # Audio codecs
    video_limits = await check_video_limits()         # Video codecs
    ice_limits = await check_ice_limits()             # ICE/TURN
    
    # Real performance tests
    test_results = await test_message_size_limits()
    test_results.extend(await test_latency_microbench())
    test_results.extend(await test_throughput_limits())
```

### Information Gathering (`info.py`)

**Complete conference analysis:**
```python
# WebSocket monitoring + REST API
info = await collect_webrtc_info()

# Detailed report
print_full_report(info)  # Participants, codecs, ICE servers, SDP
```

**Collected data:**
- List of all participants (WebSocket + REST API)
- Supported audio/video codecs
- ICE/TURN servers with credentials
- SDP statistics and RTP extensions
- Access permissions and conference state

## Audio Codec Configuration

- **Sample Rate:** 48000 Hz
- **Channels:** 2 (stereo)
- **Parameters:**
  - `minptime=10`
  - `useinbandfec=1` (Forward Error Correction)
  - `usedtx=1` (Discontinuous Transmission)

### RED (Redundant Audio Data)

- **Payload Type:** 101
- **Format:** `111/111` (Opus redundancy)

### Audio Channel Limitations

**WARNING:** Audio channels are unreliable for data transmission through Yandex Telemost SFU.

**Observed Issues:**
- Severe signal degradation (>95% energy loss)
- Opus codec applies aggressive voice optimization
- Automatic gain control destroys non-voice signals
- Voice activity detection (VAD) filters out data tones
- Noise suppression corrupts encoded data

**Test Results:**
- Transmitted signal energy: 0.4973 (normalized)
- Received signal energy: 0.0031-0.0125 (99% loss)
- DTMF tones: Not detectable after transmission
- ggwave encoding: Completely destroyed by codec

**Root Cause:** WebRTC audio pipeline optimized for human voice, not arbitrary data. The Opus codec with DTX, FEC, and server-side processing makes audio channels unsuitable for data encoding schemes (FSK, DTMF, ggwave, etc.).

**Recommendation:** Use DataChannel for reliable data transmission. Audio channels should only be used for actual voice communication.

## Video Codec Configuration

### Supported Codecs

1. **VP8**
   - Payload type: 96
   - RTX support: 97
   - NACK, PLI, REMB feedback

2. **VP9**
   - Payload type: 98
   - Profile: 0
   - RTX support: 99
   - NACK, PLI, REMB feedback

3. **H.264**
   - Multiple profiles supported:
     - Baseline (42001f, 42e01f)
     - Main (4d001f)
     - High (64001f)
   - Packetization modes: 0, 1
   - Level asymmetry allowed

4. **AV1**
   - Payload type: 45
   - RTX support: 46

### RTP Extensions

- `http://www.webrtc.org/experiments/rtp-hdrext/abs-send-time`

## ICE Configuration

### STUN Servers

- `stun:stun.rtc.yandex.net:3478`
- `stun:turn.tel.yandex.net`

### TURN Servers

- `turn:turn.tel.yandex.net:443`
- `turn:turn.tel.yandex.net:443?transport=tcp`

Credentials are time-limited and provided in `serverHello.rtcConfiguration.iceServers`.

## Error Handling

### Connection Errors

- Retry with exponential backoff
- Maximum wait time: `wait_time_to_reconnect_ms` (typically 3553ms)

### Session Timeout

- Session open timeout: `goloom_session_open_ms` (typically 120000ms)
- Expiration time provided in connection response

### Ping/Pong

- Ping interval: 5000ms
- ACK timeout: 9000ms
- Connection considered dead if no ACK received

## Security Considerations

### Authentication

- Credentials obtained from REST API
- Time-limited session tokens  
- Peer ID and room ID validation

### Transport Security

- WSS (WebSocket Secure) for signaling
- DTLS for media transport
- SRTP for audio/video encryption

### Guest Access

**Important:** The system allows anonymous access to conferences:
- No authentication required for participants
- Only conference initiator needs an account
- Display name can be arbitrary
- Possible flood attacks (see `flood.py`)

### Rate Limiting

**Recommendations based on testing:**
- Limit connection attempts per IP
- Exponential backoff on failures
- Respect session expiration times
- Throttle WebSocket messages

## Implementation Notes

### Conference Limits

**Verified limits (from `limits.py`):**
- Maximum participants: 40 (default)
- Session timeout: 120,000ms (2 minutes)
- Ping interval: 5,000ms
- ACK timeout: 9,000ms

### User Agent Spoofing

- Server may validate `User-Agent` and `sdkInfo`
- Recommended to use realistic browser signatures
- Example from code: `"Mozilla/5.0 (X11; Linux x86_64; rv:149.0) Gecko/20100101 Firefox/149.0"`

### DataChannel Availability

- DataChannel support is not guaranteed
- Check SDP for `m=application` line before assuming availability
- Server may disable DataChannel without notice
- Fallback to video QR codes (see `vcsend.py`)

### Error Handling

**Common errors and solutions:**

```python
# Connection timeout
try:
    await asyncio.wait_for(dc_pub_open.wait(), timeout=10.0)
except asyncio.TimeoutError:
    # Retry with exponential backoff
    
# Buffer overflow
while dc.bufferedAmount > CHUNK_SIZE * 2:
    await asyncio.sleep(0.001)
    
# WebSocket connection loss  
async def ws_handler():
    try:
        async for message in ws:
            # Processing...
    except websockets.exceptions.ConnectionClosed:
        # Reconnection
```

### DataChannel Message Size Workaround

**8KB limit bypass using chunking (from `dcsend.py`):**

1. Split payload into 7KB chunks (accounting for headers)
2. Add sequence headers (chunk index, total chunks, transfer ID)  
3. Implement reassembly buffer on receiver
4. Use bufferedAmount throttling to prevent congestion

**Throughput:** ~950 Kbps sustained for 256KB transfers with 32 sequential 8KB messages.

**Alternative method - QR Video (`vcsend.py`):**
- Encode data into QR codes
- Transmit via 1 FPS video stream
- Decode using pyzbar + OpenCV
- Resilient to losses with visual debugging

## Rate Limits

**Not explicitly documented. Recommended approach from testing:**
- Limit connection attempts per IP
- Exponential backoff on failures  
- Respect session expiration times
- Connection interval: 0.5s (from `flood.py`)

## Testing Tools

### Running tests

```bash
# Install dependencies
pip install -r requirements.txt

# HTTP proxy via DataChannel
python code/dcsend.py

# QR code transfer via video
python code/vcsend.py  

# Stress test connections
python code/flood.py

# Verify all limits
python code/limits.py

# Conference analysis
python code/info.py

# Basic PoC
python code/poc.py
```

### Configuration

All scripts use the same conference:
```python
CONFERENCE_ID = "75047680642749"
CONFERENCE_URL = f"https://telemost.yandex.ru/j/{CONFERENCE_ID}"
```

For testing, create your own conference and update `CONFERENCE_ID`.

## Bandwidth Configuration

### Video Layers

- **L1:** 1000 kbps (single layer)
- **L2:** 120 kbps (low), 360 kbps (med)
- **L3:** 120 kbps (low), 360 kbps (med), 800 kbps (hi)
- **L4:** 120 kbps (low), 360 kbps (med), 800 kbps (hi), 1000 kbps (ultra)

### Screen Sharing (4K)

- **Codec:** VP8
- **Min Bitrate:** 300 kbps
- **Max Bitrate:** 2000 kbps
- **Min Framerate:** 8 fps
- **Max Framerate:** 30 fps
- **Content Hint:** detail

## Telemetry

- Sending interval: 20000ms
- Endpoint: `logEndpoint` (if provided in serverHello)
- Format: Not documented

## Version Information

- **API Version:** v2
- **Client Version:** 187.1.0 (as of documentation date)
- **Media Platform:** GOLOOM
- **SDK Version:** Configurable in `sdkInfo`
