#!/usr/bin/env python3
"""PoC: передача произвольных данных через WB Stream SFU (LiveKit).

В отличие от Яндекс Телемоста или SaluteJazz, WB Stream использует 
стандартный немодифицированный протокол LiveKit (v16). Это означает, 
что нам не нужно собирать кастомные WebSocket-обработчики или костыли 
с Protobuf/JSON. Мы можем использовать официальную библиотеку livekit.
"""

import asyncio
import logging
import time
import uuid
import requests

try:
    from livekit import rtc
except ImportError:
    print("\n[!] Ошибка: не установлена библиотека livekit.")
    print("Выполните: pip install livekit requests\n")
    exit(1)

logging.getLogger("livekit").setLevel(logging.WARNING)

API_BASE = "https://stream.wb.ru"
WS_URL = "wss://wbstream01-el.wb.ru:7880"

def generate_uuid():
    return str(uuid.uuid4())

def get_room_token(room_id: str, display_name: str) -> str:
    headers = {
        "User-Agent": "Mozilla/5.0 (X11; Linux x86_64; rv:149.0)",
        "Accept": "application/json, text/plain, */*",
        "Content-Type": "application/json"
    }

    # 0. Гостевая регистрация для получения Bearer токена
    reg_resp = requests.post(
        f"{API_BASE}/auth/api/v1/auth/user/guest-register",
        json={"displayName": display_name, "device": {"deviceName": "Linux Device", "deviceType": "PARTICIPANT_DEVICE_TYPE_WEB_DESKTOP"}},
        headers=headers
    )
    reg_resp.raise_for_status()
    access_token = reg_resp.json()["accessToken"]
    headers["Authorization"] = f"Bearer {access_token}"
    
    if not room_id:
        # 1. Создаем комнату (только для первого пира)
        resp = requests.post(
            f"{API_BASE}/api-room/api/v2/room",
            json={"roomType": "ROOM_TYPE_ALL_ON_SCREEN", "roomPrivacy": "ROOM_PRIVACY_FREE"},
            headers=headers
        )
        resp.raise_for_status()
        room_id = resp.json()["roomId"]
    
    # 2. Джойнимся
    resp = requests.post(f"{API_BASE}/api-room/api/v1/room/{room_id}/join", json={}, headers=headers)
    resp.raise_for_status()
    
    # 3. Получаем токен
    params = {
        "deviceType": "PARTICIPANT_DEVICE_TYPE_WEB_DESKTOP",
        "displayName": display_name
    }
    resp = requests.get(
        f"{API_BASE}/api-room-manager/api/v1/room/{room_id}/token",
        params=params,
        headers=headers
    )
    resp.raise_for_status()
    
    return room_id, resp.json()["roomToken"]

async def run_full_test():
    print(r"""
                  OlcRTC - Full Test Suite                       
          DataChannel over WB Stream SFU (LiveKit)               
                    by zowue for olc
""")
    
    results = {
        "server_connected": False,
        "client_connected": False,
        "messages_sent": 0,
        "messages_received": 0,
        "bytes_sent": 0,
        "bytes_received": 0,
        "errors": []
    }
    
    print("[1/4] Creating server peer...")
    try:
        shared_room_id, server_token = get_room_token("", "OlcRTC-Server")
        
        server_room = rtc.Room()
        server_stats = {"sent": 0, "received": 0}
        
        @server_room.on("data_received")
        def on_s_data(dp: rtc.DataPacket):
            text = dp.data.decode('utf-8', errors='replace')
            server_stats["received"] += 1
            if dp.topic == "olcrtc_poc":
                resp_text = f"Echo: {text}"
                resp_data = resp_text.encode('utf-8')
                asyncio.create_task(server_room.local_participant.publish_data(resp_data, topic="olcrtc_poc"))
                server_stats["sent"] += 1

        await server_room.connect(WS_URL, server_token)
        results["server_connected"] = True
        print(f"      :P Server connected (Room: {shared_room_id})")
    except Exception as e:
        results["errors"].append(f"Server failed: {e}")
        print(f"      X Error: {e}")
        return results
    
    print("\n[2/4] Creating client peer...")
    try:
        _, client_token = get_room_token(shared_room_id, "OlcRTC-Client")
        
        client_room = rtc.Room()
        client_stats = {"sent": 0, "received": 0, "bytes_sent": 0, "bytes_received": 0}

        @client_room.on("data_received")
        def on_c_data(dp: rtc.DataPacket):
            text = dp.data.decode('utf-8', errors='replace')
            client_stats["received"] += 1
            client_stats["bytes_received"] += len(dp.data)

        await client_room.connect(WS_URL, client_token)
        results["client_connected"] = True
        print("      :P Client connected")
    except Exception as e:
        results["errors"].append(f"Client failed: {e}")
        print(f"      X Error: {e}")
        return results
    
    print("\n[3/4] Testing message exchange...")
    await asyncio.sleep(2)  # Даем время LiveKit поднять WebRTC транспорт
    
    test_messages = [
        "Hello WB Stream!",
        "я всего лиш хотел дружить зачем тролякатся",
        "X" * 100,
        "Final test"
    ]
    
    try:
        for i, msg in enumerate(test_messages, 1):
            msg_bytes = msg.encode('utf-8')
            # Отправляем DataPacket через LiveKit
            await client_room.local_participant.publish_data(msg_bytes, topic="olcrtc_poc")
            client_stats["sent"] += 1
            client_stats["bytes_sent"] += len(msg_bytes)
            print(f"      -> Sent message {i}/{len(test_messages)} ({len(msg_bytes)}b)")
            await asyncio.sleep(0.5)
        
        await asyncio.sleep(3)
        
        results["messages_sent"] = client_stats["sent"]
        results["messages_received"] = client_stats["received"]
        results["bytes_sent"] = client_stats["bytes_sent"]
        results["bytes_received"] = client_stats["bytes_received"]
        
        print(f"      :P Sent: {results['messages_sent']} messages")
        print(f"      :P Received: {results['messages_received']} responses")
        
    except Exception as e:
        results["errors"].append(f"Exchange failed: {e}")
        print(f"      X Error: {e}")
    
    print("\n[4/4] Cleaning up...")
    try:
        await server_room.disconnect()
        await client_room.disconnect()
        print("      :P Cleanup complete")
    except:
        pass
    
    return results

def print_results(results):
    print(r"""
                       TEST RESULTS                              
""")
    
    print("Connection Status:")
    print(f"  - Server: {':P Connected' if results.get('server_connected') else 'X Failed'}")
    print(f"  - Client: {':P Connected' if results.get('client_connected') else 'X Failed'}")
    
    print("\nMessage Exchange:")
    print(f"  - Sent: {results.get('messages_sent', 0)} messages ({results.get('bytes_sent', 0)} bytes)")
    print(f"  - Received: {results.get('messages_received', 0)} messages ({results.get('bytes_received', 0)} bytes)")
    
    sent = results.get('messages_sent', 0)
    success_rate = (results.get('messages_received', 0) / sent * 100) if sent > 0 else 0
    print(f"  - Success Rate: {success_rate:.1f}%")
    
    if results.get('errors'):
        print("\nErrors:")
        for err in results['errors']:
            print(f"  - {err}")
    
    if results.get('messages_received', 0) > 0:
        print("\n :P TEST PASSED - WB Stream PoC works!")
    else:
        print("\n  X TEST FAILED - Check errors above")

async def main():
    results = await run_full_test()
    print_results(results)

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n\nTest interrupted.")
