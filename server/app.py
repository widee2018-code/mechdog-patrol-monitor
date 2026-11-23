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
import base64
import json
import math
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

try:   # 대시보드 영상에 마커 표시용. opencv가 없으면(서버만 설치) 원본 영상만 보낸다.
    import cv2
    import numpy as np
except ImportError:
    cv2 = None

UDP_PORT = 9000
MARKER_M = 0.07    # 출력한 마커 한 변 (brain/marker_follow.py --marker-cm)
CAM_HFOV = 62.0    # 카메라 가로 화각 (brain/marker_follow.py --hfov)
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


# 사이렌 (#6): 서버 한 곳에서 판단해 alarm 메시지를 보낸다. 대시보드를 여러 개 열어도 기록은 한 번만 남고,
# recorder 가 alarm/event 를 Supabase 에 저장한다 (cloud/supabase/incidents.sql 이 읽는 형식).
# 울림: 사람 발견(SUSPECT, 사람 인식 detect 가 만든다) 이후 단계로 들어갈 때. 범인인지는 관제가 보고 포위 버튼으로 정한다.
# 멈춤: 확인 버튼(ack) / 줄 다시 서기(regroup) / 포위 전 대기·탐사로 복귀(false_alarm) / 포위 뒤 대기로(stop)
MISSION_ALIAS = {"MAP": "EXPLORE", "PATROL": "EXPLORE", "ALERT": "CONFIRM", "INTERCEPT": "CONTAIN", "RETURN": "REGROUP"}
INCIDENT_STATES = {"SUSPECT", "CONFIRM", "CONTAIN", "HANDOVER", "REGROUP"}
RING_STATES = {"SUSPECT", "CONFIRM", "CONTAIN", "HANDOVER"}
siren = {"mission": None, "incident": 0, "on": False, "acked": 0, "since": 0.0}
mission_ts = [0.0]   # 지금 단계가 시작된 시각 (/api/dogs 로 brain 이 읽는다)

# 탐사 시작 버튼: 확인견 B·C 의 marker_follow.py 가 앞 개 마커를 보고 준비 완료(ready)를 보내고 있어야 한다.
FOLLOWERS = {"B": 1, "C": 2}   # 확인견 -> 보고 따라가는 앞 개의 마커 ID
READY_SEC = 2.0                # marker_follow.py 는 0.5초마다 ready 를 보낸다
DETECT_HOLD = 1.5              # 사람 인식(detect)이 이 시간 동안 없으면 발견 끝 (대시보드 "발견" 불도 같은 시간)


def fmt_dur(sec: float) -> str:
    return f"{int(sec // 60):02d}:{int(sec % 60):02d}"


def siren_off(reason: str, text: str, level: str, ts: float) -> None:
    siren["on"] = False
    n, dur = siren["incident"], fmt_dur(ts - siren["since"])
    ingest({"type": "alarm", "from": "monitor", "ts": ts, "state": "off", "reason": reason, "incident": n})
    ingest({"type": "event", "from": "monitor", "ts": ts, "level": level, "text": text.format(n=n, dur=dur)})


def on_mission(msg: dict) -> None:
    state = MISSION_ALIAS.get(msg.get("state"), msg.get("state"))
    prev, siren["mission"] = siren["mission"], state
    if state in INCIDENT_STATES and prev not in INCIDENT_STATES:
        siren["incident"] += 1
    if state in INCIDENT_STATES:
        msg["incident"] = siren["incident"]   # 대시보드가 같은 사건 번호를 쓰도록 붙여 보낸다
    if state == prev:
        return
    mission_ts[0] = msg["ts"]
    # 사이렌 기록은 원인이 된 mission 과 같은 시각으로 남긴다 (incidents 뷰가 사건 시간 범위로 묶기 때문)
    ts = msg["ts"]
    if state in RING_STATES and not siren["on"] and siren["acked"] != siren["incident"]:
        siren["on"], siren["since"] = True, ts
        n, intr = siren["incident"], latest.get("intruder:pi", {})
        reason, why = ("detect", "사람 발견") if state == "SUSPECT" else ("confirm", "범인 확인 단계 시작")
        ingest({"type": "alarm", "from": "monitor", "ts": ts, "state": "on", "reason": reason, "incident": n,
                **{k: intr[k] for k in ("x", "y") if k in intr}})
        ingest({"type": "event", "from": "monitor", "ts": ts, "level": "alert", "text": f"🚨 사이렌 울림 · 사건 #{n} · {why}"})
    elif state not in RING_STATES and siren["on"]:
        if state == "REGROUP":
            siren_off("regroup", "⚠ 사이렌 자동 멈춤 · 사건 #{n} · 줄 다시 서기(REGROUP) 시작 · 아무도 확인하지 않음 (울린 시간 {dur})", "warn", ts)
        elif prev in ("CONTAIN", "HANDOVER"):
            siren_off("stop", "⚠ 사이렌 자동 멈춤 · 사건 #{n} · 포위 중 대기로 전환 · 아무도 확인하지 않음 (울린 시간 {dur})", "warn", ts)
        else:
            siren_off("false_alarm", "⚠ 사이렌 자동 멈춤 · 사건 #{n} · 오탐 · 대기·탐사로 복귀 · 아무도 확인하지 않음 (울린 시간 {dur})", "warn", ts)


def on_detect(msg: dict) -> None:
    """사람 인식은 감지한 프레임마다 온다. 새로 발견했을 때만 타임라인 이벤트를 남긴다 (recorder 가 기록).
    탐사 중에 발견하면 SUSPECT 로 바꿔 사이렌을 울린다. 대기 중에는 줄 세우는 팀원이 보이므로 불만 켠다."""
    prev = latest.get(cache_key(msg))
    if prev is None or msg["ts"] - prev["ts"] > DETECT_HOLD:
        conf = f" · 신뢰도 {msg['confidence']:.2f}" if isinstance(msg.get("confidence"), (int, float)) else ""
        ingest({"type": "event", "from": "monitor", "ts": msg["ts"], "level": "warn",
                "text": f"👤 Dog {msg.get('from', '-')} 카메라: 사람 발견{conf}"})
    if siren["mission"] == "EXPLORE":
        ingest({"type": "mission", "from": "monitor", "ts": msg["ts"], "state": "SUSPECT"})


def ingest(msg: dict) -> None:
    if not isinstance(msg, dict) or "type" not in msg:
        return
    msg.setdefault("ts", time.time())
    kind = msg["type"]
    if kind == "mission":
        on_mission(msg)
    elif kind == "detect":
        on_detect(msg)
    if kind in HISTORY_TYPES:
        history[kind] = (history[kind] + [msg])[-HISTORY_TYPES[kind]:]
    elif kind == "dog":
        # 위치는 Pi(LiDAR 추적)에서, 배터리/센서는 로봇견 자신에게서 오므로 둘을 합친다.
        key = f"dog:{msg['from']}"
        latest[key] = {**latest.get(key, {}), **msg}
        if "front_cm" in msg or "rssi" in msg:   # 로봇견 자신(dog_cmd.py)이 보낸 메시지: 살아 있다는 표시
            latest[key]["self_ts"] = msg["ts"]
        if "ready" in msg:   # marker_follow.py 준비 확인: 따라가기를 시작하면 ready 를 그만 보낸다
            latest[key]["ready_ts"] = msg["ts"]
        # 로봇견이 직접 보낸 적이 있으면, 다시 온라인도 로봇견 자신의 메시지로만 판단한다
        # (SLAM·brain 위치 메시지가 계속 오면 오프라인 ↔ 온라인 알림이 반복되므로)
        if msg["from"] in offline and ("self_ts" not in latest[key] or latest[key]["self_ts"] == msg["ts"]):
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
            # 로봇견이 직접 보낸 적이 있으면 그 시각(self_ts) 기준: SLAM 위치만 계속 와도 로봇견이 꺼진 걸 알린다
            last = msg.get("self_ts", msg["ts"]) if key.startswith("dog:") else 0
            if key.startswith("dog:") and name not in offline and now - last > DOG_TIMEOUT:
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


def draw_markers(detector, jpg: bytes) -> bytes:
    """ArUco 마커(DICT_4X4_50)마다 테두리, ID, 어림 거리를 그린다. 거리는 marker_follow.py와 같은 핀홀 모델."""
    img = cv2.imdecode(np.frombuffer(jpg, np.uint8), cv2.IMREAD_COLOR)
    if img is None:
        return jpg
    corners, ids, _ = detector.detectMarkers(cv2.cvtColor(img, cv2.COLOR_BGR2GRAY))
    w = img.shape[1]
    focal_px = (w / 2) / math.tan(math.radians(CAM_HFOV) / 2)
    for c, marker_id in zip(corners, [] if ids is None else ids.flatten()):
        c = c.reshape(4, 2)
        side_px = np.mean([np.linalg.norm(c[i] - c[(i + 1) % 4]) for i in range(4)])
        x, y = c.min(axis=0).astype(int)
        cv2.polylines(img, [c.astype(int)], True, (0, 200, 0), 2)
        cv2.putText(img, f"ID {marker_id}  {focal_px * MARKER_M / side_px:.2f} m", (x, max(y - 6, 14)),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 200, 0), 2)
    if ids is None:
        cv2.putText(img, "no marker", (8, 20), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 0, 220), 2)
    ok, out = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, 80])
    return out.tobytes() if ok else jpg


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
        # 마커 표시 영상: 보는 대시보드가 여러 개여도 프레임마다 한 번만 그린다.
        self._mark_lock = threading.Lock()
        self._marked_src: bytes | None = None
        self._marked: bytes | None = None
        self._detector = cv2.aruco.ArucoDetector(cv2.aruco.getPredefinedDictionary(cv2.aruco.DICT_4X4_50),
                                                 cv2.aruco.DetectorParameters()) if cv2 else None

    def marked(self, jpg: bytes) -> bytes:
        with self._mark_lock:
            if self._marked_src is not jpg:
                self._marked, self._marked_src = draw_markers(self._detector, jpg), jpg
            return self._marked

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


@app.middleware("http")
async def revalidate_web(request: Request, call_next):
    """대시보드 파일(app.js 등)은 매번 서버에 바뀌었는지 묻게 한다. 안 그러면 코드를 고쳐도 브라우저가 예전 파일을 쓴다."""
    response = await call_next(request)
    if not request.url.path.startswith("/api/"):
        response.headers.setdefault("Cache-Control", "no-cache")
    return response


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


@app.post("/api/alarm/ack")
async def alarm_ack(request: Request):
    """대시보드의 사이렌 "확인" 버튼. 공개 주소(Cloudflare 터널)로 연 화면에서는 받지 않는다."""
    if request.headers.get("cf-connecting-ip"):
        raise HTTPException(403, "공개 주소에서는 사이렌을 끌 수 없음")
    if siren["on"]:
        siren["acked"] = siren["incident"]
        siren_off("ack", "✅ 사이렌 확인 · 사건 #{n} · 관제가 확인 버튼을 누름 (울린 지 {dur})", "info", time.time())
    return {"ok": True}


def not_ready(now: float) -> list[str]:
    """탐사 시작 전에 확인견마다 앞 개 마커를 보고 있는지 확인한다. 준비 안 된 이유 목록 (비면 출발 가능)."""
    reasons = []
    for name, marker in FOLLOWERS.items():
        dog = latest.get(f"dog:{name}", {})
        if now - dog.get("ready_ts", 0) > READY_SEC:
            reasons.append(f"Dog {name} 따라가기(marker_follow.py) 응답 없음")
        elif not dog.get("ready"):
            reasons.append(f"Dog {name} 마커 {marker} 준비 안 됨")
    return reasons


@app.post("/api/mission/start")
async def mission_start(request: Request):
    """대시보드의 "탐사 시작" 버튼. B·C 가 모두 준비됐을 때만 EXPLORE 로 바꾸고, marker_follow.py 가 이를 보고 출발한다."""
    if request.headers.get("cf-connecting-ip"):
        raise HTTPException(403, "공개 주소에서는 탐사를 시작할 수 없음")
    if siren["mission"] not in (None, "STANDBY"):
        raise HTTPException(409, f"대기(STANDBY)가 아님: 지금 {siren['mission']}")
    if reasons := not_ready(time.time()):
        raise HTTPException(409, " · ".join(reasons))
    ingest({"type": "mission", "from": "monitor", "state": "EXPLORE"})
    ingest({"type": "event", "from": "monitor", "level": "info", "text": "▶ 탐사 시작 · 관제가 시작 버튼을 누름 (B·C 마커 확인됨)"})
    return {"ok": True}


@app.post("/api/mission/contain")
async def mission_contain(request: Request):
    """대시보드의 "포위" 버튼: 관제가 발견한 사람을 범인으로 판단. B·C 의 marker_follow.py 가 멈추고 teleop.py 로 넘어간다."""
    if request.headers.get("cf-connecting-ip"):
        raise HTTPException(403, "공개 주소에서는 포위를 시작할 수 없음")
    if siren["mission"] not in ("SUSPECT", "CONFIRM"):
        raise HTTPException(409, f"사람 발견(SUSPECT) 중이 아님: 지금 {siren['mission'] or 'STANDBY'}")
    ingest({"type": "mission", "from": "monitor", "state": "CONTAIN"})
    ingest({"type": "event", "from": "monitor", "level": "alert",
            "text": f"◎ 포위 시작 · 사건 #{siren['incident']} · 관제가 범인으로 판단 (B·C 사람 조종으로 전환)"})
    return {"ok": True}


@app.post("/api/mission/standby")
async def mission_standby(request: Request):
    """대기로 되돌리기. 탐사 중이면 B·C 가 일시정지, 사람 발견 중이면 오탐 처리 (A는 조종하는 사람이 멈춘다)."""
    if request.headers.get("cf-connecting-ip"):
        raise HTTPException(403, "공개 주소에서는 바꿀 수 없음")
    if siren["mission"] in (None, "STANDBY"):
        raise HTTPException(409, "이미 대기(STANDBY)")
    prev = siren["mission"]
    ingest({"type": "mission", "from": "monitor", "state": "STANDBY"})
    why = "B·C 따라가기 일시정지" if prev == "EXPLORE" else "범인 아님 (오탐)" if prev in ("SUSPECT", "CONFIRM") else f"{prev} 끝"
    ingest({"type": "event", "from": "monitor", "level": "warn", "text": f"⏸ 대기로 전환 · 관제가 버튼을 누름 ({why})"})
    return {"ok": True}


@app.post("/api/snapshot")
async def snapshot(request: Request):
    """대시보드의 "촬영" 버튼. 실시간 영상이 연결된 로봇견마다 지금 프레임(마커 표시 없는 원본)을 증거 사진으로 남긴다.
    evidence 가 참인 camera 메시지라 대시보드 증거 사진 칸에 뜨고, recorder 가 Supabase Storage evidence 버킷에 올린다."""
    if request.headers.get("cf-connecting-ip"):
        raise HTTPException(403, "공개 주소에서는 촬영할 수 없음")
    live = {name: feed.frame for name, feed in feeds.items() if feed.online and feed.frame}
    if not live:
        raise HTTPException(409, "실시간 영상이 연결된 카메라가 없음")
    ts = time.time()
    note = f"촬영 · 사건 #{siren['incident']}" if siren["mission"] in INCIDENT_STATES else "촬영"
    for name, jpg in live.items():
        ingest({"type": "camera", "from": name, "ts": ts, "evidence": True, "note": note,
                "image": "data:image/jpeg;base64," + base64.b64encode(jpg).decode()})
    ingest({"type": "event", "from": "monitor", "ts": ts, "level": "info",
            "text": f"📷 촬영 · Dog {', '.join(live)} 증거 사진 {len(live)}장"})
    return {"ok": True, "dogs": list(live)}


@app.get("/api/state")
async def state():
    return {"latest": latest, "events": history["event"], "evidence": history["evidence"]}


@app.get("/api/dogs")
async def dogs():
    """로봇견별 최신 상태와 미션 단계만 가볍게 (brain/marker_follow.py가 0.5초마다 읽는다)."""
    return {"now": time.time(), "mission": siren["mission"] or "STANDBY", "mission_ts": mission_ts[0],
            "dogs": {k[4:]: v for k, v in latest.items() if k.startswith("dog:")}}


@app.get("/api/cameras")
async def cameras():
    return {name: {"online": feed.online, "url": feed.url, "error": feed.last_error, "markers": cv2 is not None}
            for name, feed in feeds.items()}


@app.get("/api/camera/{name}/stream")
async def camera_stream(name: str, markers: bool = False):
    """markers=1이면 마커 표시를 그린 영상 (대시보드용). 따라가기·포위 프로그램은 원본을 읽는다."""
    feed = feeds.get(name)
    if feed is None:
        raise HTTPException(404, f"Dog {name} 카메라가 CAMERAS에 설정되지 않음")
    markers = markers and cv2 is not None

    async def body():
        async for jpg in feed.frames():
            if markers:
                jpg = await asyncio.to_thread(feed.marked, jpg)
            yield b"--frame\r\nContent-Type: image/jpeg\r\nContent-Length: %d\r\n\r\n%s\r\n" % (len(jpg), jpg)

    return StreamingResponse(body(), media_type="multipart/x-mixed-replace; boundary=frame",
                             headers={"Cache-Control": "no-store"})


@app.websocket("/ws")
async def ws_endpoint(ws: WebSocket):
    await ws.accept()
    clients.add(ws)
    # 새로 연 대시보드가 비어 있지 않도록 현재 상태를 다시 보낸다.
    now = time.time()
    for msg in [*history["event"], *history["evidence"], *latest.values()]:
        if msg["type"] == "detect" and now - msg["ts"] > DETECT_HOLD:
            continue   # 끝난 발견을 다시 보내면 새 화면에 "발견" 불이 잠깐 켜진다
        await ws.send_text(json.dumps(msg))
    try:
        while True:
            await ws.receive_text()
    except WebSocketDisconnect:
        clients.discard(ws)


app.mount("/", StaticFiles(directory=WEB_DIR, html=True), name="web")
