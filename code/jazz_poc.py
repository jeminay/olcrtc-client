#!/usr/bin/env python3
"""PoC: передача произвольных данных через SaluteJazz SFU (LiveKit 1.5.3).

Два aiortc-клиента подключаются к анонимной комнате Jazz,
обмениваются сообщениями через DataChannel (LiveKit DataPacket protobuf),
весь трафик проходит через TURN-relay Сбера (a-t/s-t.salutejazz.ru).
"""

import asyncio
import io
import json
import logging
import time
import uuid

import aiohttp
from aiortc import (RTCConfiguration, RTCIceCandidate, RTCIceServer,
                    RTCPeerConnection, RTCSessionDescription)
from aiortc.mediastreams import AudioStreamTrack
from aiortc.rtcconfiguration import RTCBundlePolicy

logging.basicConfig(
    level=logging.INFO, format="[%(levelname)s] %(message)s"
)
log = logging.getLogger(__name__)

logging.getLogger("aiortc").setLevel(logging.WARNING)
logging.getLogger("aioice").setLevel(logging.WARNING)
logging.getLogger("aioice.turn").setLevel(logging.INFO)

API_BASE = "https://bk.salutejazz.ru"
JAZZ_HEADERS = {
    "X-Jazz-ClientId": str(uuid.uuid4()),
    "X-Jazz-AuthType": "ANONYMOUS",
    "X-Client-AuthType": "ANONYMOUS",
    "Content-Type": "application/json",
}

# Маппинг подсети relay ↔ TURN-хост.
_SUBNET_TO_TURN: dict[str, str] = {
    "172.17": "s-t.",
    "172.20": "a-t.",
}
_TURN_TO_SUBNET: dict[str, str] = {
    v: k for k, v in _SUBNET_TO_TURN.items()
}


def _pb_varint(value: int) -> bytes:
    """Кодирование varint (protobuf wire format)."""
    buf = bytearray()
    while value > 0x7F:
        buf.append((value & 0x7F) | 0x80)
        value >>= 7
    buf.append(value & 0x7F)
    return bytes(buf)


def _pb_field(field_number: int, wire_type: int, data: bytes) -> bytes:
    """Кодирование одного protobuf-поля."""
    tag = _pb_varint((field_number << 3) | wire_type)
    if wire_type == 0:
        return tag + data
    if wire_type == 2:
        return tag + _pb_varint(len(data)) + data
    return tag + data


def _read_varint(stream: io.BytesIO) -> int | None:
    """Чтение varint из потока."""
    result = 0
    shift = 0
    while True:
        b = stream.read(1)
        if not b:
            return None
        byte = b[0]
        result |= (byte & 0x7F) << shift
        if not (byte & 0x80):
            return result
        shift += 7


def encode_data_packet(payload: bytes, topic: str = "") -> bytes:
    """Сериализация LiveKit DataPacket с UserPacket.

    Args:
        payload: Пользовательские данные.
        topic: Топик сообщения (опционально).

    Returns:
        Сериализованный protobuf DataPacket.
    """
    user_fields = _pb_field(2, 2, payload)
    if topic:
        user_fields += _pb_field(4, 2, topic.encode())
    msg_id = str(uuid.uuid4())
    user_fields += _pb_field(8, 2, msg_id.encode())

    user_packet = user_fields

    dp = _pb_field(1, 0, _pb_varint(0))
    dp += _pb_field(2, 2, user_packet)
    return dp


def decode_data_packet(
    raw: bytes,
) -> tuple[bytes, str] | None:
    """Десериализация LiveKit DataPacket → (payload, topic).

    Returns:
        Кортеж (payload, topic) или None если не UserPacket.
    """
    stream = io.BytesIO(raw)
    user_data: bytes | None = None

    while True:
        tag_val = _read_varint(stream)
        if tag_val is None:
            break
        field_number = tag_val >> 3
        wire_type = tag_val & 0x07

        if wire_type == 0:
            _read_varint(stream)
        elif wire_type == 2:
            length = _read_varint(stream)
            if length is None:
                break
            data = stream.read(length)
            if field_number == 2:
                user_data = data
        elif wire_type == 1:
            stream.read(8)
        elif wire_type == 5:
            stream.read(4)
        else:
            break

    if user_data is None:
        return None

    payload = b""
    topic = ""
    inner = io.BytesIO(user_data)
    while True:
        tag_val = _read_varint(inner)
        if tag_val is None:
            break
        fn = tag_val >> 3
        wt = tag_val & 0x07

        if wt == 0:
            _read_varint(inner)
        elif wt == 2:
            length = _read_varint(inner)
            if length is None:
                break
            data = inner.read(length)
            if fn == 2:
                payload = data
            elif fn == 4:
                topic = data.decode(errors="replace")
        elif wt == 1:
            inner.read(8)
        elif wt == 5:
            inner.read(4)
        else:
            break

    return (payload, topic)


def _subnet_prefix(ip: str) -> str:
    """Первые два октета IP-адреса (например '172.17')."""
    parts = ip.split(".")
    return f"{parts[0]}.{parts[1]}" if len(parts) == 4 else ""


def _relay_subnet_from_turn_url(turn_url: str) -> str:
    """Определение подсети relay по hostname TURN-сервера.

    Args:
        turn_url: TURN URL (напр. 'turn:s-t.salutejazz.ru:3478?transport=udp').

    Returns:
        Подсеть relay (напр. '172.17') или пустая строка.
    """
    for prefix, subnet in _TURN_TO_SUBNET.items():
        if prefix in turn_url:
            return subnet
    return ""


def _pick_turn_for_subnet(
    sfu_subnet: str,
    all_turns: list[str],
) -> str | None:
    """Выбрать TURN URL, совместимый с подсетью SFU."""
    prefix = _SUBNET_TO_TURN.get(sfu_subnet, "")
    if prefix:
        for u in all_turns:
            if prefix in u and "transport=udp" in u:
                return u
    return all_turns[0] if all_turns else None


def gen_uuid() -> str:
    """Генерация UUID v4."""
    return str(uuid.uuid4())


async def create_room(
    session: aiohttp.ClientSession,
) -> dict[str, str]:
    """Создание комнаты и получение WS URL.

    Returns:
        dict с ключами roomId, password, connectorUrl.
    """
    create_resp = await session.post(
        f"{API_BASE}/room/create-meeting",
        headers=JAZZ_HEADERS,
        json={
            "title": "PoC DataChannel",
            "guestEnabled": True,
            "lobbyEnabled": False,
            "serverVideoRecordAutoStartEnabled": False,
            "sipEnabled": False,
            "moderatorEmails": [],
            "summarizationEnabled": False,
            "room3dEnabled": False,
            "room3dScene": "XRLobby",
        },
    )
    create_resp.raise_for_status()
    room_data = await create_resp.json()
    room_id: str = room_data["roomId"]
    password: str = room_data["password"]

    preconnect_resp = await session.post(
        f"{API_BASE}/room/{room_id}/preconnect",
        headers=JAZZ_HEADERS,
        json={
            "password": password,
            "jazzNextMigration": {
                "b2bBaseRoomSupport": True,
                "demoRoomBaseSupport": True,
                "demoRoomVersionSupport": 2,
                "mediaWithoutAutoSubscribeSupport": True,
                "webinarSpeakerSupport": True,
                "webinarViewerSupport": True,
                "sdkRoomSupport": True,
                "sberclassRoomSupport": True,
            },
        },
    )
    preconnect_resp.raise_for_status()
    preconnect_data = await preconnect_resp.json()

    return {
        "roomId": room_id,
        "password": password,
        "connectorUrl": preconnect_data["connectorUrl"],
    }


def parse_ice_candidate(
    cand_data: dict[str, object],
) -> RTCIceCandidate | None:
    """Парсинг ICE-кандидата из строки candidate:... .

    Returns:
        RTCIceCandidate или None, если не удалось распарсить.
    """
    cand_str = str(cand_data.get("candidate", ""))
    parts = cand_str.split()
    if len(parts) < 8:
        return None

    sdp_mid = cand_data.get("sdpMid")
    sdp_m_line_index = cand_data.get("sdpMLineIndex", 0)

    return RTCIceCandidate(
        component=int(parts[1]),
        foundation=parts[0].replace("candidate:", ""),
        ip=parts[4],
        port=int(parts[5]),
        priority=int(parts[3]),
        protocol=parts[2],
        type=parts[7],
        sdpMid=str(sdp_mid) if sdp_mid is not None else "0",
        sdpMLineIndex=int(sdp_m_line_index),
    )


async def create_peer(
    name: str,
    room_info: dict[str, str],
    session: aiohttp.ClientSession,
    is_server: bool = False,
    known_sfu_subnet: str = "",
) -> dict[str, object]:
    """Подключение пира к Jazz SFU.

    Args:
        name: Имя участника.
        room_info: Словарь с roomId, password, connectorUrl.
        session: aiohttp-сессия.
        is_server: Если True, эхо-ответ на входящие сообщения.
        known_sfu_subnet: Подсеть SFU (напр. '172.17') для выбора TURN.

    Returns:
        dict с ключами name, dc_ready, stats, ws, pc_sub, pc_pub,
        sfu_subnet, subnet_ok и т.д.
    """
    room_id = room_info["roomId"]
    password = room_info["password"]
    connector_url = room_info["connectorUrl"]

    group_id: str | None = None
    ice_servers_config: list[RTCIceServer] = []
    all_turn_urls_saved: list[str] = []
    turn_creds_saved: dict[str, str | None] = {
        "username": None,
        "credential": None,
    }

    pc_sub: RTCPeerConnection | None = None
    pc_pub: RTCPeerConnection | None = None
    dc_pub: object | None = None
    dc_ready = asyncio.Event()
    sub_ready = asyncio.Event()

    pending_ice_sub: list[dict[str, object]] = []
    pending_ice_pub: list[dict[str, object]] = []

    relay_subnet: str = ""
    sfu_ip: str | None = None
    subnet_ok: bool | None = None
    subnet_checked = asyncio.Event()

    stats: dict[str, object] = {
        "sent": 0,
        "received": 0,
        "messages": [],
    }

    ws = await session.ws_connect(connector_url)

    # --- join ---
    await ws.send_json(
        {
            "roomId": room_id,
            "event": "join",
            "requestId": gen_uuid(),
            "payload": {
                "password": password,
                "participantName": name,
                "supportedFeatures": {
                    "attachedRooms": True,
                    "sessionGroups": True,
                    "transcription": True,
                },
                "isSilent": False,
            },
        }
    )

    publisher_offer_sent = False

    async def ws_loop() -> None:
        nonlocal group_id, ice_servers_config
        nonlocal pc_sub, pc_pub, dc_pub
        nonlocal publisher_offer_sent
        nonlocal relay_subnet, sfu_ip, subnet_ok

        async for msg in ws:
            if msg.type == aiohttp.WSMsgType.TEXT:
                data = json.loads(msg.data)
                event = data.get("event", "")
                payload = data.get("payload", {})
                method = payload.get("method", "")

                if event == "join-response":
                    p = payload.get("participantGroup", {})
                    group_id = p.get("groupId")
                    log.info(
                        f"[{name}] Подключён, groupId={group_id}"
                    )

                elif event == "media-out" and method == "rtc:config":
                    config = payload.get("configuration", {})
                    servers = config.get("iceServers", [])
                    for s in servers:
                        urls = s.get("urls", [])
                        turn_creds_saved["username"] = s.get(
                            "username"
                        )
                        turn_creds_saved["credential"] = s.get(
                            "credential"
                        )
                        for u in urls:
                            if u.startswith("turns:"):
                                continue
                            all_turn_urls_saved.append(u)

                    log.info(
                        f"[{name}] Все TURN URL:"
                        f" {all_turn_urls_saved}"
                    )

                    if known_sfu_subnet:
                        chosen = _pick_turn_for_subnet(
                            known_sfu_subnet,
                            all_turn_urls_saved,
                        )
                    else:
                        udp_urls = [
                            u for u in all_turn_urls_saved
                            if "transport=udp" in u
                        ]
                        chosen = (
                            udp_urls[0]
                            if udp_urls
                            else all_turn_urls_saved[0]
                            if all_turn_urls_saved
                            else None
                        )

                    if chosen:
                        ice_servers_config.append(
                            RTCIceServer(
                                urls=[chosen],
                                username=turn_creds_saved[
                                    "username"
                                ],
                                credential=turn_creds_saved[
                                    "credential"
                                ],
                            )
                        )
                    if chosen:
                        relay_subnet = (
                            _relay_subnet_from_turn_url(chosen)
                        )
                    log.info(
                        f"[{name}] Используется TURN:"
                        f" {chosen}"
                        f" (relay подсеть={relay_subnet})"
                    )

                elif event == "media-out" and method == "rtc:join":
                    join_data = payload.get("join", {})
                    log.info(
                        f"[{name}] rtc:join получен"
                        f" (LiveKit {join_data.get('serverVersion')})"
                    )

                elif (
                    event == "media-out"
                    and method == "rtc:offer"
                    and pc_sub is None
                ):
                    desc = payload.get("description", {})
                    sdp = desc.get("sdp", "")
                    sdp_type = desc.get("type", "offer")

                    m_lines = [
                        ln for ln in sdp.splitlines()
                        if ln.startswith("m=")
                    ]
                    bundle_lines = [
                        ln for ln in sdp.splitlines()
                        if "BUNDLE" in ln
                    ]
                    log.info(
                        f"[{name}] SFU offer:"
                        f" {len(m_lines)} m-lines:"
                        f" {m_lines}"
                    )
                    log.info(
                        f"[{name}] SFU BUNDLE:"
                        f" {bundle_lines}"
                    )

                    sdp_file = (
                        f"research/{name.lower()}"
                        "_sub_offer.sdp"
                    )
                    with open(sdp_file, "w") as f:
                        f.write(sdp)
                    log.info(
                        f"[{name}] SDP сохранён в {sdp_file}"
                    )

                    rtc_config = RTCConfiguration(
                        iceServers=ice_servers_config,
                        bundlePolicy=RTCBundlePolicy.MAX_BUNDLE,
                    )
                    pc_sub = RTCPeerConnection(
                        configuration=rtc_config
                    )

                    @pc_sub.on("connectionstatechange")
                    def on_sub_state() -> None:
                        state = pc_sub.connectionState
                        log.info(
                            f"[{name}] Subscriber PC: {state}"
                        )
                        if state == "connected":
                            sub_ready.set()

                    @pc_sub.on("datachannel")
                    def on_sub_dc(channel: object) -> None:
                        log.info(
                            f"[{name}] Subscriber DC:"
                            f" {channel.label}"
                        )
                        if channel.label != "_reliable":
                            return

                        @channel.on("message")
                        def on_sub_msg(message: object) -> None:
                            if isinstance(message, str):
                                raw = message.encode()
                            else:
                                raw = bytes(message)

                            parsed = decode_data_packet(raw)
                            if parsed is None:
                                log.debug(
                                    f"[{name}] Не DataPacket:"
                                    f" {raw[:40]!r}"
                                )
                                return

                            payload, topic = parsed
                            if topic != "poc":
                                return

                            text = payload.decode(
                                errors="replace"
                            )
                            stats["received"] += 1
                            stats["messages"].append(
                                ("received", text, time.time())
                            )
                            log.info(
                                f"[{name}] Получено:"
                                f" {text!r}"
                            )

                            if (
                                is_server
                                and dc_pub is not None
                            ):
                                resp = f"Echo: {text}"
                                pkt = encode_data_packet(
                                    resp.encode(), topic="poc"
                                )
                                try:
                                    dc_pub.send(pkt)
                                    stats["sent"] += 1
                                    stats["messages"].append(
                                        (
                                            "sent",
                                            resp,
                                            time.time(),
                                        )
                                    )
                                except Exception:
                                    log.exception(
                                        f"[{name}]"
                                        " Ошибка отправки echo"
                                    )

                    @pc_sub.on("icecandidate")
                    async def on_sub_ice(
                        event_obj: object,
                    ) -> None:
                        if not (
                            event_obj
                            and event_obj.candidate
                            and group_id
                        ):
                            return
                        c = event_obj.candidate
                        await ws.send_json({
                            "roomId": room_id,
                            "event": "media-in",
                            "groupId": group_id,
                            "requestId": gen_uuid(),
                            "payload": {
                                "method": "rtc:ice",
                                "rtcIceCandidates": [{
                                    "candidate": c.candidate,
                                    "sdpMid": c.sdpMid,
                                    "sdpMLineIndex": (
                                        c.sdpMLineIndex
                                    ),
                                    "usernameFragment": "",
                                    "target": "SUBSCRIBER",
                                }],
                            },
                        })

                    await pc_sub.setRemoteDescription(
                        RTCSessionDescription(
                            sdp=sdp, type=sdp_type
                        )
                    )
                    answer = await pc_sub.createAnswer()
                    await pc_sub.setLocalDescription(answer)

                    ans_m = [
                        ln for ln in answer.sdp.splitlines()
                        if ln.startswith("m=")
                    ]
                    ans_bundle = [
                        ln for ln in answer.sdp.splitlines()
                        if "BUNDLE" in ln
                    ]
                    log.info(
                        f"[{name}] Our answer:"
                        f" {len(ans_m)} m-lines:"
                        f" {ans_m}"
                    )
                    log.info(
                        f"[{name}] Answer BUNDLE:"
                        f" {ans_bundle}"
                    )

                    ans_file = (
                        f"research/{name.lower()}"
                        "_sub_answer.sdp"
                    )
                    with open(ans_file, "w") as f:
                        f.write(answer.sdp)

                    await ws.send_json(
                        {
                            "roomId": room_id,
                            "event": "media-in",
                            "groupId": group_id,
                            "requestId": gen_uuid(),
                            "payload": {
                                "method": "rtc:answer",
                                "description": {
                                    "type": "answer",
                                    "sdp": pc_sub.localDescription.sdp,
                                },
                            },
                        }
                    )
                    log.info(
                        f"[{name}] Subscriber SDP answer отправлен"
                    )

                    for c in pending_ice_sub:
                        ice = parse_ice_candidate(c)
                        if ice:
                            await pc_sub.addIceCandidate(ice)
                    pending_ice_sub.clear()

                    await asyncio.sleep(0.3)

                    # --- publisher ---
                    if not publisher_offer_sent:
                        pc_pub = RTCPeerConnection(
                            configuration=RTCConfiguration(
                                iceServers=ice_servers_config,
                                bundlePolicy=RTCBundlePolicy.MAX_BUNDLE,
                            )
                        )

                        @pc_pub.on("connectionstatechange")
                        def on_pub_state() -> None:
                            log.info(
                                f"[{name}] Publisher PC:"
                                f" {pc_pub.connectionState}"
                            )

                        audio_track = AudioStreamTrack()
                        pc_pub.addTrack(audio_track)

                        dc_pub_local = pc_pub.createDataChannel(
                            "_reliable", ordered=True
                        )

                        @dc_pub_local.on("open")
                        def on_dc_open() -> None:
                            log.info(
                                f"[{name}] Publisher DC open"
                            )
                            dc_ready.set()

                        @dc_pub_local.on("message")
                        def on_dc_msg(message: object) -> None:
                            if isinstance(message, str):
                                raw = message.encode()
                            else:
                                raw = bytes(message)
                            parsed = decode_data_packet(raw)
                            if parsed is None:
                                return
                            payload, topic = parsed
                            if topic != "poc":
                                return
                            text = payload.decode(
                                errors="replace"
                            )
                            stats["received"] += 1
                            stats["messages"].append(
                                ("received", text, time.time())
                            )
                            log.info(
                                f"[{name}] DC pub получено:"
                                f" {text!r}"
                            )

                        dc_pub = dc_pub_local

                        await ws.send_json(
                            {
                                "roomId": room_id,
                                "event": "media-in",
                                "groupId": group_id,
                                "requestId": gen_uuid(),
                                "payload": {
                                    "method": "rtc:track:add",
                                    "cid": gen_uuid(),
                                    "track": {
                                        "type": "AUDIO",
                                        "source": "MICROPHONE",
                                        "muted": True,
                                        "disableDtx": False,
                                    },
                                },
                            }
                        )
                        log.info(
                            f"[{name}] rtc:track:add отправлен"
                        )

                        offer = await pc_pub.createOffer()
                        await pc_pub.setLocalDescription(offer)

                        pub_m = [
                            ln for ln in offer.sdp.splitlines()
                            if ln.startswith("m=")
                        ]
                        log.info(
                            f"[{name}] Pub offer:"
                            f" {len(pub_m)} m-lines:"
                            f" {pub_m}"
                        )
                        with open(
                            f"research/{name.lower()}"
                            "_pub_offer.sdp", "w"
                        ) as f:
                            f.write(offer.sdp)

                        await ws.send_json(
                            {
                                "roomId": room_id,
                                "event": "media-in",
                                "groupId": group_id,
                                "requestId": gen_uuid(),
                                "payload": {
                                    "method": "rtc:offer",
                                    "description": {
                                        "type": "offer",
                                        "sdp": pc_pub.localDescription.sdp,
                                    },
                                },
                            }
                        )
                        log.info(
                            f"[{name}] Publisher SDP offer"
                            " отправлен"
                        )

                        @pc_pub.on("icecandidate")
                        async def on_pub_ice(
                            event_obj: object,
                        ) -> None:
                            if not (
                                event_obj
                                and event_obj.candidate
                                and group_id
                            ):
                                return
                            c = event_obj.candidate
                            await ws.send_json({
                                "roomId": room_id,
                                "event": "media-in",
                                "groupId": group_id,
                                "requestId": gen_uuid(),
                                "payload": {
                                    "method": "rtc:ice",
                                    "rtcIceCandidates": [{
                                        "candidate": (
                                            c.candidate
                                        ),
                                        "sdpMid": c.sdpMid,
                                        "sdpMLineIndex": (
                                            c.sdpMLineIndex
                                        ),
                                        "usernameFragment": "",
                                        "target": "PUBLISHER",
                                    }],
                                },
                            })

                        publisher_offer_sent = True

                elif (
                    event == "media-out"
                    and method == "rtc:answer"
                ):
                    desc = payload.get("description", {})
                    sdp = desc.get("sdp", "")
                    sdp_type = desc.get("type", "answer")

                    if pc_pub is not None:
                        await pc_pub.setRemoteDescription(
                            RTCSessionDescription(
                                sdp=sdp, type=sdp_type
                            )
                        )
                        log.info(
                            f"[{name}] Publisher SDP answer"
                            " получен"
                        )

                        for c in pending_ice_pub:
                            ice = parse_ice_candidate(c)
                            if ice:
                                await pc_pub.addIceCandidate(
                                    ice
                                )
                        pending_ice_pub.clear()
                    else:
                        log.warning(
                            f"[{name}] rtc:answer получен,"
                            " но pc_pub ещё не создан"
                        )

                elif event == "media-out" and method == "rtc:ice":
                    candidates = payload.get(
                        "rtcIceCandidates", []
                    )
                    for c in candidates:
                        target = c.get("target", "")
                        ice = parse_ice_candidate(c)
                        if ice is None:
                            continue
                        log.info(
                            f"[{name}] ICE от SFU:"
                            f" {target}"
                            f" {c.get('candidate', '')[:60]}"
                        )

                        if (
                            target == "SUBSCRIBER"
                            and sfu_ip is None
                            and ice.ip
                        ):
                            sfu_ip = ice.ip
                            sfu_sub = _subnet_prefix(sfu_ip)
                            subnet_ok = (
                                sfu_sub == relay_subnet
                                if relay_subnet
                                else None
                            )
                            log.info(
                                f"[{name}] SFU IP={sfu_ip}"
                                f" (подсеть {sfu_sub}),"
                                f" relay подсеть"
                                f"={relay_subnet},"
                                f" совпадение={subnet_ok}"
                            )
                            subnet_checked.set()
                            if subnet_ok is False:
                                log.warning(
                                    f"[{name}] MISMATCH"
                                    " подсетей! Нужен"
                                    " reconnect."
                                )
                                break

                        if target == "SUBSCRIBER":
                            if pc_sub:
                                try:
                                    await pc_sub.addIceCandidate(
                                        ice
                                    )
                                except Exception as e:
                                    log.warning(
                                        f"[{name}] Sub ICE"
                                        f" ошибка: {e}"
                                    )
                            else:
                                pending_ice_sub.append(c)
                        elif target == "PUBLISHER":
                            if pc_pub:
                                try:
                                    await pc_pub.addIceCandidate(
                                        ice
                                    )
                                except Exception as e:
                                    log.warning(
                                        f"[{name}] Pub ICE"
                                        f" ошибка: {e}"
                                    )
                            else:
                                pending_ice_pub.append(c)

                elif (
                    event == "media-out"
                    and method == "rtc:track:published"
                ):
                    log.info(
                        f"[{name}] Трек опубликован"
                    )

                elif (
                    event == "media-out"
                    and method == "rtc:pong"
                ):
                    pass

                elif (
                    event == "media-out"
                    and method == "rtc:participants:update"
                ):
                    pass

                elif (
                    event == "media-out"
                    and method == "rtc:quality"
                ):
                    pass

                elif event in (
                    "hand-statuses",
                    "get-chat-messages-response",
                ):
                    pass

                elif event == "error":
                    code = payload.get("code", "")
                    message = payload.get("message", "")
                    log.warning(
                        f"[{name}] Ошибка: {code} — {message}"
                    )

                else:
                    log.debug(
                        f"[{name}] Необработано:"
                        f" event={event} method={method}"
                    )

            elif msg.type in (
                aiohttp.WSMsgType.CLOSED,
                aiohttp.WSMsgType.ERROR,
            ):
                log.warning(f"[{name}] WS закрыт/ошибка")
                break

    ws_task = asyncio.create_task(ws_loop())

    # --- keepalive ---
    async def keepalive() -> None:
        rtt = 0
        while not ws.closed:
            await asyncio.sleep(5)
            if group_id and not ws.closed:
                ts = int(time.time() * 1000)
                await ws.send_json(
                    {
                        "roomId": room_id,
                        "event": "media-in",
                        "groupId": group_id,
                        "requestId": gen_uuid(),
                        "payload": {
                            "method": "rtc:ping",
                            "ping_req": {
                                "timestamp": ts,
                                "rtt": rtt,
                            },
                        },
                    }
                )

    keepalive_task = asyncio.create_task(keepalive())

    return {
        "name": name,
        "dc_pub": lambda: dc_pub,
        "dc_ready": dc_ready,
        "sub_ready": sub_ready,
        "subnet_checked": subnet_checked,
        "subnet_ok": lambda: subnet_ok,
        "sfu_subnet": lambda: (
            _subnet_prefix(sfu_ip) if sfu_ip else ""
        ),
        "all_turn_urls": all_turn_urls_saved,
        "stats": stats,
        "ws": ws,
        "ws_task": ws_task,
        "keepalive_task": keepalive_task,
        "pc_sub": lambda: pc_sub,
        "pc_pub": lambda: pc_pub,
    }


async def cleanup_peer(peer: dict[str, object]) -> None:
    """Корректно закрывает ресурсы пира."""
    for key in ("ws_task", "keepalive_task"):
        task = peer.get(key)
        if task and isinstance(task, asyncio.Task):
            task.cancel()
    ws = peer.get("ws")
    if ws:
        try:
            await ws.close()  # type: ignore[union-attr]
        except Exception:
            pass
    for getter_key in ("pc_sub", "pc_pub"):
        getter = peer.get(getter_key)
        if callable(getter):
            pc = getter()
            if pc:
                try:
                    await pc.close()
                except Exception:
                    pass


MAX_RECONNECTS = 2


async def run_poc() -> None:
    """Основной сценарий PoC."""
    log.info("Jazz DataChannel PoC")
    log.info("DataChannel через SaluteJazz SFU (LiveKit)")

    peers: list[dict[str, object]] = []
    try:
        success = await _run_attempt(peers)
    finally:
        for p in peers:
            await cleanup_peer(p)

    if not success:
        log.error(
            "Подключение не удалось."
            " Возможно, проблема с сетью/TURN."
        )


async def _run_attempt(
    peers: list[dict[str, object]],
) -> bool:
    """Попытка подключения с быстрым reconnect при mismatch."""
    async with aiohttp.ClientSession() as session:
        log.info("[1/3] Создание комнаты...")
        try:
            room = await create_room(session)
            log.info(
                f"Комната создана:"
                f" roomId={room['roomId']},"
                f" password={room['password']}"
            )
        except Exception as e:
            log.error(f"Ошибка создания комнаты: {e}")
            return False

        known_subnet = ""

        for reconnect_i in range(MAX_RECONNECTS + 1):
            phase = "Фаза 1" if not known_subnet else "Фаза 2"
            if reconnect_i > 0:
                log.info(
                    f"--- {phase}: reconnect"
                    f" #{reconnect_i}"
                    f" (подсеть SFU={known_subnet}) ---"
                )

            ok = await _connect_peers(
                room, session, peers,
                known_sfu_subnet=known_subnet,
            )
            if ok:
                return True

            last = peers[-1] if peers else None
            if (
                last
                and callable(last.get("subnet_ok"))
                and last["subnet_ok"]() is False
            ):
                sfu_sub_fn = last.get("sfu_subnet")
                discovered = (
                    sfu_sub_fn()
                    if callable(sfu_sub_fn)
                    else ""
                )
                if discovered and discovered != known_subnet:
                    known_subnet = discovered
                    log.info(
                        f"Обнаружена подсеть SFU:"
                        f" {known_subnet}, reconnect"
                    )
                    for p in peers:
                        await cleanup_peer(p)
                    peers.clear()
                    continue

            return False

        return False


async def _connect_peers(
    room: dict[str, str],
    session: aiohttp.ClientSession,
    peers: list[dict[str, object]],
    known_sfu_subnet: str = "",
) -> bool:
    """Подключение обоих пиров и обмен сообщениями."""
    log.info("[2/3] Подключение пиров...")

    log.info("Подключение Server...")
    try:
        server = await create_peer(
            "Server", room, session,
            is_server=True,
            known_sfu_subnet=known_sfu_subnet,
        )
        peers.append(server)

        done, _ = await asyncio.wait(
            [
                asyncio.ensure_future(
                    server["dc_ready"].wait()
                ),
                asyncio.ensure_future(
                    server["subnet_checked"].wait()
                ),
            ],
            timeout=30.0,
            return_when=asyncio.FIRST_COMPLETED,
        )

        if server["subnet_checked"].is_set():
            if (
                callable(server.get("subnet_ok"))
                and server["subnet_ok"]() is False
            ):
                log.warning(
                    "MISMATCH подсетей"
                    " — быстрый reconnect"
                )
                return False

        if not server["dc_ready"].is_set():
            await asyncio.wait_for(
                server["dc_ready"].wait(), timeout=15.0
            )
        log.info("Server: DataChannel open")
    except asyncio.TimeoutError:
        log.error(
            "Server DataChannel timeout"
            " (TURN/ICE не прошёл)"
        )
        return False
    except Exception as e:
        log.error(f"Ошибка Server: {e}")
        return False

    sfu_sub_fn = server.get("sfu_subnet")
    server_sfu_subnet = (
        sfu_sub_fn()
        if callable(sfu_sub_fn)
        else ""
    )

    log.info("Подключение Client...")
    try:
        client = await create_peer(
            "Client", room, session,
            is_server=False,
            known_sfu_subnet=(
                server_sfu_subnet or known_sfu_subnet
            ),
        )
        peers.append(client)
        await asyncio.wait_for(
            client["dc_ready"].wait(), timeout=15.0
        )
        log.info("Client: DataChannel open")
    except asyncio.TimeoutError:
        log.error("Client DataChannel timeout")
        return False
    except Exception as e:
        log.error(f"Ошибка Client: {e}")
        return False

    log.info("Ожидание subscriber connected...")
    try:
        await asyncio.wait_for(
            server["sub_ready"].wait(), timeout=30.0
        )
        log.info("Server subscriber: connected")
    except asyncio.TimeoutError:
        log.warning(
            "Server subscriber"
            " не подключился (timeout 30s)"
        )

    try:
        await asyncio.wait_for(
            client["sub_ready"].wait(), timeout=15.0
        )
        log.info("Client subscriber: connected")
    except asyncio.TimeoutError:
        log.warning(
            "Client subscriber"
            " не подключился (timeout 15s)"
        )

    log.info("[3/3] Обмен сообщениями...")
    await asyncio.sleep(2)

    test_messages = [
        "Hello Jazz DC!",
        "Тестовое сообщение на русском",
        "X" * 100,
        "Final test",
    ]

    dc = client["dc_pub"]()
    if dc is None:
        log.error("DataChannel клиента не создан")
        return False

    pc_pub_client = client["pc_pub"]()
    if (
        pc_pub_client is None
        or pc_pub_client.connectionState
        in ("closed", "failed")
    ):
        log.error(
            f"Publisher PC клиента уже"
            f" {pc_pub_client and pc_pub_client.connectionState}"
        )
        return False

    for i, text in enumerate(test_messages, 1):
        pkt = encode_data_packet(
            text.encode(), topic="poc"
        )
        try:
            dc.send(pkt)
        except Exception as e:
            log.error(f"Ошибка отправки: {e}")
            return False
        client["stats"]["sent"] += 1
        log.info(
            f"Отправлено {i}/{len(test_messages)}"
            f" ({len(text)}b → {len(pkt)}b protobuf)"
        )
        await asyncio.sleep(0.5)

    log.info("Ожидание ответов (10с)...")
    await asyncio.sleep(10)

    received = client["stats"]["received"]
    sent = client["stats"]["sent"]

    log.info(f"Отправлено: {sent}")
    log.info(f"Получено ответов: {received}")

    if received > 0:
        log.info("ТЕСТ ПРОЙДЕН — DataChannel работает!")
        return True

    log.error("ТЕСТ НЕ ПРОЙДЕН — ответы не получены")
    srv_recv = server["stats"]["received"]
    log.info(f"Server получил: {srv_recv} сообщений")
    return False


if __name__ == "__main__":
    try:
        asyncio.run(run_poc())
    except KeyboardInterrupt:
        log.info("Прервано.")
