"""MechDog 순찰 모니터의 텔레메트리 허브.

Pi 관제탑과 로봇견들이 보내는 JSON 메시지(UDP 또는 HTTP POST)를 받아 종류별 최신 값을 보관하고,
모든 메시지를 WebSocket으로 대시보드에 전달한다.

실행:  uvicorn server.app:app --host 0.0.0.0 --port 8000

로봇견 카메라 실시간 영상까지 보려면 CAMERAS 환경 변수에 스트림 주소를 넣는다.
열린 영상 스트림 때문에 Ctrl+C 종료가 멈추지 않도록 --timeout-graceful-shutdown 을 붙인다:
    CAMERAS="A=http://192.168.0.20:81/stream" uvicorn server.app:app --host 0.0.0.0 --port 8000 --timeout-graceful-shutdown 2

대시보드를 외부(터널)에 공개할 때는 INGEST_TOKEN 을 설정한다. 그러면 HTTP POST /api/ingest 는
"Authorization: Bearer <토큰>" 헤더가 있어야 받는다. UDP는 터널을 거치지 않으므로 그대로 받는다.
"""

import asyncio
import json
import os
import secrets
import threading
import time
import urllib.request
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import StreamingResponse
from fastapi.staticfiles import StaticFiles

UDP_PORT = 9000
WEB_DIR = Path(__file__).resolve().parent.parent / "web"
MAX_EVENTS = 200
MAX_EVIDENCE = 24
DOG_TIMEOUT = 3.0   # 이 시간(초) 동안 메시지가 없으면 로봇견을 오프라인으로 표시
INGEST_TOKEN = os.environ.get("INGEST_TOKEN", "")

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
        if "front_cm" in msg or "rssi" in msg:   # 로봇견 자신(dog_cmd.py)이 보낸 메시지: 살아 있다는 표시
            latest[key]["self_ts"] = msg["ts"]
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


def mjpeg_frames(url: str):
    # JPEG 시작/끝 표시로 스트림을 자른다 (tools/camera_relay.py와 같은 방식).
    with urllib.request.urlopen(url, timeout=5) as r:
        buf = b""
        while chunk := r.read1(65536):   # read1: 도착한 만큼 바로 받아서 지연을 줄인다
            buf += chunk
            start = buf.find(b"\xff\xd8")
            end = buf.find(b"\xff\xd9", start + 2)
            if start != -1 and end != -1:
                yield buf[start:end + 2]
                buf = buf[end + 2:]
            elif len(buf) > 2_000_000:
                buf = b""


class CameraFeed:
    """카메라 스트림 연결을 하나만 열어 두고 최신 프레임을 여러 대시보드에 나눠 준다.

    ESP32 카메라는 스트림을 한 번에 한 곳에만 보낼 수 있으므로 브라우저가 직접 붙지 않게 한다.
    """

    def __init__(self, name: str, url: str):
        self.name, self.url = name, url
        self.frame: bytes | None = None
        self.seq = 0
        self.online = False
        self.last_error: str | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._new: asyncio.Event | None = None

    def start(self, loop: asyncio.AbstractEventLoop) -> None:
        self._loop, self._new = loop, asyncio.Event()
        threading.Thread(target=self._read, daemon=True).start()

    def _read(self) -> None:
        # urllib은 블로킹이라 별도 스레드에서 읽고, 프레임은 이벤트 루프로 넘긴다.
        while True:
            error = "스트림 종료"
            try:
                for jpg in mjpeg_frames(self.url):
                    self._loop.call_soon_threadsafe(self._publish, jpg)
            except Exception as e:
                error = str(getattr(e, "reason", e))
            self._loop.call_soon_threadsafe(self._went_offline, error)
            time.sleep(2)

    def _publish(self, jpg: bytes) -> None:
        self.frame, self.seq = jpg, self.seq + 1
        if not self.online:
            self.online, self.last_error = True, None
            ingest({"type": "event", "from": "monitor", "level": "info", "text": f"Dog {self.name} 카메라 실시간 영상 연결됨"})
        self._new.set()
        self._new = asyncio.Event()

    def _went_offline(self, error: str) -> None:
        print(f"[monitor] Dog {self.name} 카메라 연결 실패 ({self.url}): {error}. 2초 후 재시도")
        # 연결돼 있다가 끊겼을 때와, 처음부터 연결이 안 될 때 모두 같은 오류는 한 번만 타임라인에 올린다.
        if self.online or error != self.last_error:
            ingest({"type": "event", "from": "monitor", "level": "warn", "text": f"Dog {self.name} 카메라 영상 연결 실패: {error}"})
        self.online, self.last_error = False, error

    async def frames(self):
        seen = 0
        while True:
            if self.seq == seen:
                try:
                    await asyncio.wait_for(self._new.wait(), timeout=5)
                except asyncio.TimeoutError:
                    continue
            seen = self.seq
            yield self.frame


def parse_cameras(spec: str) -> dict[str, CameraFeed]:
    feeds = {}
    for item in filter(None, (s.strip() for s in spec.split(","))):
        name, _, url = item.partition("=")
        feeds[name.strip()] = CameraFeed(name.strip(), url.strip())
    return feeds


feeds = parse_cameras(os.environ.get("CAMERAS", ""))


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
    for feed in feeds.values():
        feed.start(loop)
        print(f"[monitor] Dog {feed.name} 카메라 스트림: {feed.url}")
    yield
    watchdog.cancel()
    transport.close()


app = FastAPI(title="MechDog Patrol Monitor", lifespan=lifespan)


@app.post("/api/ingest")
async def http_ingest(request: Request):
    """UDP와 같지만, 데이터그램 하나에 들어가지 않는 큰 데이터(예: 카메라 JPEG)용."""
    if INGEST_TOKEN:
        given = request.headers.get("authorization", "").removeprefix("Bearer ").strip()
        if not secrets.compare_digest(given, INGEST_TOKEN):
            raise HTTPException(401, "INGEST_TOKEN 이 맞지 않음")
    body = await request.json()
    for msg in body if isinstance(body, list) else [body]:
        ingest(msg)
    return {"ok": True}


@app.get("/api/state")
async def state():
    return {"latest": latest, "events": history["event"], "evidence": history["evidence"]}


@app.get("/api/dogs")
async def dogs():
    """로봇견별 최신 상태만 가볍게 (brain/marker_follow.py의 준비 확인이 0.5초마다 읽는다)."""
    return {"now": time.time(), "dogs": {k[4:]: v for k, v in latest.items() if k.startswith("dog:")}}


@app.get("/api/cameras")
async def cameras():
    return {name: {"online": feed.online, "url": feed.url, "error": feed.last_error} for name, feed in feeds.items()}


@app.get("/api/camera/{name}/stream")
async def camera_stream(name: str):
    feed = feeds.get(name)
    if feed is None:
        raise HTTPException(404, f"Dog {name} 카메라가 CAMERAS에 설정되지 않음")

    async def body():
        async for jpg in feed.frames():
            yield b"--frame\r\nContent-Type: image/jpeg\r\nContent-Length: %d\r\n\r\n%s\r\n" % (len(jpg), jpg)

    return StreamingResponse(body(), media_type="multipart/x-mixed-replace; boundary=frame",
                             headers={"Cache-Control": "no-store"})


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
