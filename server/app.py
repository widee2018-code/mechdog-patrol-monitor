"""Telemetry hub for the MechDog patrol monitor.

Receives JSON messages from the Pi control tower and the dogs (UDP or HTTP POST),
keeps the latest value of each, and pushes everything to the dashboard over a WebSocket.

Run:  uvicorn server.app:app --host 0.0.0.0 --port 8000
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
DOG_TIMEOUT = 3.0   # seconds without a dog message before it is reported offline

# Message types that are kept as a list instead of "latest value only".
HISTORY_TYPES = {"event": MAX_EVENTS}

latest: dict[str, dict] = {}       # "dog:A" -> last dog message, "lidar:pi" -> last scan, ...
history: dict[str, list] = {"event": [], "evidence": []}
clients: set[WebSocket] = set()
offline: set[str] = set()          # dogs already reported offline


def cache_key(msg: dict) -> str:
    # Sound alerts get their own slot so ambient readings don't overwrite the last alert.
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
        # Position comes from the Pi (LiDAR tracking), battery/sensors from the dog itself: merge both.
        key = f"dog:{msg['from']}"
        latest[key] = {**latest.get(key, {}), **msg}
        if msg["from"] in offline:
            offline.discard(msg["from"])
            ingest({"type": "event", "from": "monitor", "level": "info", "text": f"Dog {msg['from']} back online"})
    else:
        latest[cache_key(msg)] = msg
    if kind == "camera" and msg.get("evidence"):
        history["evidence"] = (history["evidence"] + [msg])[-MAX_EVIDENCE:]
    asyncio.get_running_loop().create_task(broadcast(msg))


async def watch_dogs() -> None:
    """Post a timeline event when a dog stops sending."""
    while True:
        await asyncio.sleep(0.5)
        now = time.time()
        for key, msg in list(latest.items()):
            name = msg.get("from")
            if key.startswith("dog:") and name not in offline and now - msg["ts"] > DOG_TIMEOUT:
                offline.add(name)
                ingest({"type": "event", "from": "monitor", "level": "warn",
                        "text": f"Dog {name} offline (no data for {DOG_TIMEOUT:.0f} s)"})


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
    print(f"[monitor] listening for UDP telemetry on :{UDP_PORT}")
    watchdog = asyncio.create_task(watch_dogs())
    yield
    watchdog.cancel()
    transport.close()


app = FastAPI(title="MechDog Patrol Monitor", lifespan=lifespan)


@app.post("/api/ingest")
async def http_ingest(request: Request):
    """Same as UDP, for payloads that are too large for one datagram (e.g. camera JPEGs)."""
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
    # Replay current state so a freshly opened dashboard is not empty.
    for msg in [*history["event"], *history["evidence"], *latest.values()]:
        await ws.send_text(json.dumps(msg))
    try:
        while True:
            await ws.receive_text()
    except WebSocketDisconnect:
        clients.discard(ws)


app.mount("/", StaticFiles(directory=WEB_DIR, html=True), name="web")
