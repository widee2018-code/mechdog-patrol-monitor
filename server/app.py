"""MechDog 순찰 모니터의 텔레메트리 허브.

Pi 관제탑과 로봇견들이 보내는 JSON 메시지(UDP 또는 HTTP POST)를 받아 종류별 최신 값을 보관하고,
모든 메시지를 WebSocket으로 대시보드에 전달한다.

실행:  uvicorn server.app:app --host 0.0.0.0 --port 8000
"""

import asyncio
import json
import time
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.staticfiles import StaticFiles

UDP_PORT = 9000
WEB_DIR = Path(__file__).resolve().parent.parent / "web"
MAX_EVENTS = 200
MAX_EVIDENCE = 24
DOG_TIMEOUT = 3.0   # 이 시간(초) 동안 메시지가 없으면 로봇견을 오프라인으로 표시

# "최신 값 하나"가 아니라 목록으로 보관하는 메시지 종류.
HISTORY_TYPES = {"event": MAX_EVENTS}

latest: dict[str, dict] = {}       # "dog:A" -> 마지막 로봇견 메시지, "lidar:pi" -> 마지막 스캔, ...
history: dict[str, list] = {"event": [], "evidence": []}
clients: set[WebSocket] = set()
offline: set[str] = set()          # 이미 오프라인으로 알린 로봇견


def cache_key(msg: dict) -> str:
    # 소리 경보는 따로 저장해서 주변 소음 값이 마지막 경보를 덮어쓰지 않게 한다.
    kind = "sound_alert" if msg["type"] == "sound" and msg.get("alert") else msg["type"]
    return f"{kind}:{msg.get('from', '-')}"


async def broadcast(msg: dict) -> None:
    text = json.dumps(msg)
    dead = []
    for ws in clients:
        try:
            await ws.send_text(text)
        except Exception:
            dead.append(ws)
    for ws in dead:
        clients.discard(ws)


def ingest(msg: dict) -> None:
    if not isinstance(msg, dict) or "type" not in msg:
        return
    msg.setdefault("ts", time.time())
    kind = msg["type"]
    if kind in HISTORY_TYPES:
        history[kind] = (history[kind] + [msg])[-HISTORY_TYPES[kind]:]
    elif kind == "dog":
        # 위치는 Pi(LiDAR 추적)에서, 배터리/센서는 로봇견 자신에게서 오므로 둘을 합친다.
        key = f"dog:{msg['from']}"
        latest[key] = {**latest.get(key, {}), **msg}
        if msg["from"] in offline:
            offline.discard(msg["from"])
            ingest({"type": "event", "from": "monitor", "level": "info", "text": f"Dog {msg['from']} 다시 온라인"})
    else:
        latest[cache_key(msg)] = msg
    if kind == "camera" and msg.get("evidence"):
        history["evidence"] = (history["evidence"] + [msg])[-MAX_EVIDENCE:]
    asyncio.get_running_loop().create_task(broadcast(msg))


async def watch_dogs() -> None:
    """로봇견이 메시지를 보내지 않으면 타임라인 이벤트를 올린다."""
    while True:
        await asyncio.sleep(0.5)
        now = time.time()
        for key, msg in list(latest.items()):
            name = msg.get("from")
            if key.startswith("dog:") and name not in offline and now - msg["ts"] > DOG_TIMEOUT:
                offline.add(name)
                ingest({"type": "event", "from": "monitor", "level": "warn",
                        "text": f"Dog {name} 오프라인 ({DOG_TIMEOUT:.0f}초간 데이터 없음)"})


class UDPReceiver(asyncio.DatagramProtocol):
    def datagram_received(self, data: bytes, addr) -> None:
        try:
            ingest(json.loads(data))
        except (ValueError, UnicodeDecodeError):
            pass


@asynccontextmanager
async def lifespan(_app: FastAPI):
    loop = asyncio.get_running_loop()
    transport, _ = await loop.create_datagram_endpoint(UDPReceiver, local_addr=("0.0.0.0", UDP_PORT))
    print(f"[monitor] UDP 텔레메트리 수신 대기 중 :{UDP_PORT}")
    watchdog = asyncio.create_task(watch_dogs())
    yield
    watchdog.cancel()
    transport.close()


app = FastAPI(title="MechDog Patrol Monitor", lifespan=lifespan)


@app.post("/api/ingest")
async def http_ingest(request: Request):
    """UDP와 같지만, 데이터그램 하나에 들어가지 않는 큰 데이터(예: 카메라 JPEG)용."""
    body = await request.json()
    for msg in body if isinstance(body, list) else [body]:
        ingest(msg)
    return {"ok": True}


@app.get("/api/state")
async def state():
    return {"latest": latest, "events": history["event"], "evidence": history["evidence"]}


@app.websocket("/ws")
async def ws_endpoint(ws: WebSocket):
    await ws.accept()
    clients.add(ws)
    # 새로 연 대시보드가 비어 있지 않도록 현재 상태를 다시 보낸다.
    for msg in [*history["event"], *history["evidence"], *latest.values()]:
        await ws.send_text(json.dumps(msg))
    try:
        while True:
            await ws.receive_text()
    except WebSocketDisconnect:
        clients.discard(ws)


app.mount("/", StaticFiles(directory=WEB_DIR, html=True), name="web")
