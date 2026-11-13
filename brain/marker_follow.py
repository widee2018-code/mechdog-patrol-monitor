"""ArUco 마커 따라가기: 카메라에서 마커를 찾아 로봇견에 move(속도, 회전) 명령을 보낸다.

마커가 화면 가운데에서 벗어난 정도는 회전으로, 마커 크기로 잰 거리는 속도로 바꾼다(P 제어).
마커를 놓치면 멈추고, 잠시 뒤 마지막으로 본 쪽으로 돌며 다시 찾는다.

STANDBY(일시정지) 상태로 시작한다: 움직이지 않고 준비 확인만 한다. 앞 개의 마커가 보이고, 앞 개와의
거리가 0.12~0.25 m이고, 로봇견(dog_cmd.py)이 응답하는 상태가 1초 이상 이어지면 "EXPLORE 준비 완료"를
대시보드 타임라인에 띄운다. 준비가 된 뒤 스페이스(카메라 창) 또는 Enter(터미널)를 누르면 출발, 다시 누르면
일시정지. 준비 확인 없이 출발하려면 --no-ready-check, 바로 시작하려면 --autostart.

거리는 로봇견 초음파(front_cm)를 먼저 쓰고, 없으면 카메라로 잰 마커 거리를 쓴다. 초음파와 로봇견 응답은
모니터 서버(--monitor)의 /api/dogs에서 읽으므로, --monitor가 없으면 카메라만으로 판단한다.

개 없이 연습 (노트북 웹캠, 명령은 화면과 터미널에만 표시):
    python brain/marker_follow.py --camera 0 --show

실제 로봇견 (모니터 서버가 카메라 스트림을 받고 있고, 로봇견에서 firmware/dog_cmd.py 실행 중):
    python brain/marker_follow.py --camera http://localhost:8000/api/camera/B/stream \\
        --dog 192.168.0.31 --name B --monitor 127.0.0.1 --show
"""
import argparse
import json
import math
import socket
import sys
import threading
import time
import urllib.request

import cv2
import numpy as np

CMD_PORT = 9100       # firmware/dog_cmd.py가 명령을 받는 포트
MONITOR_PORT = 9000   # server/app.py의 UDP 포트
MONITOR_HTTP = 8000   # server/app.py의 HTTP 포트 (/api/dogs)
STALE_SEC = 0.5       # 새 프레임이 이 시간 동안 안 오면 영상이 멈춘 것으로 보고 정지


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="ArUco 마커 따라가기")
    p.add_argument("--camera", default="0", help="웹캠 번호(0), 영상 파일, 또는 MJPEG 스트림 URL")
    p.add_argument("--marker-id", type=int, default=1, help="따라갈 마커 ID (A 등 뒤 = 1)")
    p.add_argument("--marker-cm", type=float, default=7.0, help="출력한 마커의 실제 한 변 길이 (검은 테두리 포함, cm)")
    p.add_argument("--hfov", type=float, default=62.0, help="카메라 가로 화각(도). 거리 추정에 사용")
    p.add_argument("--target-m", type=float, default=0.15, help="유지할 거리 (m)")
    p.add_argument("--back-m", type=float, default=0.0, help="이보다 가까우면 뒤로 물러남 (m). 0이면 후진 안 함")
    p.add_argument("--k-speed", type=float, default=500.0, help="거리 오차 1 m당 속도")
    p.add_argument("--k-turn", type=float, default=60.0, help="화면 끝(dx=1)일 때 회전값")
    p.add_argument("--min-speed", type=int, default=40, help="이보다 작은 전진 속도는 걷지 못하므로 올림")
    p.add_argument("--max-speed", type=int, default=120)
    p.add_argument("--back-speed", type=int, default=40)
    p.add_argument("--max-turn", type=int, default=50)
    p.add_argument("--turn-sign", type=int, choices=(1, -1), default=-1,
                   help="MechDog B 실측(2026-11-12)은 -1. 마커 반대쪽으로 돌면 부호를 바꾼다")
    p.add_argument("--lost-sec", type=float, default=1.5, help="마커를 놓친 뒤 찾기 시작할 때까지 (초)")
    p.add_argument("--search-sec", type=float, default=12.0, help="이 시간 동안 못 찾으면 포기하고 정지 (초)")
    p.add_argument("--search-speed", type=int, default=0, help="찾는 동안 전진 속도 (제자리 회전이 안 되면 40 정도)")
    p.add_argument("--search-turn", type=int, default=10)
    p.add_argument("--rate", type=float, default=10.0, help="명령 주기 (Hz)")
    p.add_argument("--dog", help="로봇견 IP. 없으면 명령을 보내지 않는 연습 모드")
    p.add_argument("--name", default="B", help="로봇견 이름 (모니터 표시용)")
    p.add_argument("--monitor", help="모니터 서버 IP. 주면 상태와 이벤트를 대시보드로 보낸다")
    p.add_argument("--show", action="store_true", help="카메라 화면 창 띄우기 (스페이스: 시작/일시정지, q: 종료)")
    p.add_argument("--autostart", action="store_true",
                   help="시작 키를 기다리지 않고 마커가 보이면 바로 따라가기 (기본은 일시정지 상태로 시작)")
    p.add_argument("--ready-min-m", type=float, default=0.12, help="준비 완료로 볼 앞 개와의 최소 거리 (m)")
    p.add_argument("--ready-max-m", type=float, default=0.25, help="준비 완료로 볼 앞 개와의 최대 거리 (m)")
    p.add_argument("--ready-hold-sec", type=float, default=1.0, help="조건이 이 시간 이상 이어져야 준비 완료 (초)")
    p.add_argument("--link-sec", type=float, default=2.0, help="로봇견이 이 시간 안에 상태를 보냈으면 연결된 것으로 봄 (초)")
    p.add_argument("--no-ready-check", action="store_true", help="준비 확인 없이 시작 키로 바로 출발")
    return p.parse_args()


class LatestFrame:
    """카메라를 별도 스레드에서 계속 읽고 가장 최근 프레임만 남긴다.

    제어 루프가 오래된 프레임을 처리하면 그만큼 반응이 늦어지므로 밀린 프레임은 버린다.
    """

    def __init__(self, source: str):
        self.source = source
        self.frame: np.ndarray | None = None
        self.seq = 0
        self.error: str | None = None
        self._lock = threading.Lock()
        threading.Thread(target=self._run, daemon=True).start()

    def get(self) -> tuple[int, np.ndarray | None]:
        with self._lock:
            return self.seq, self.frame

    def _put(self, frame: np.ndarray) -> None:
        with self._lock:
            self.frame, self.seq, self.error = frame, self.seq + 1, None

    def _run(self) -> None:
        while True:
            try:
                if self.source.startswith("http"):
                    self._read_mjpeg()
                else:
                    self._read_capture()
                self.error = "스트림 종료"
            except Exception as e:
                self.error = str(getattr(e, "reason", e))
            time.sleep(1)

    def _read_mjpeg(self) -> None:
        # server/app.py의 mjpeg_frames와 같은 방식: JPEG 시작/끝 표시로 자른다.
        with urllib.request.urlopen(self.source, timeout=5) as r:
            buf = b""
            while chunk := r.read1(65536):
                buf += chunk
                start = buf.find(b"\xff\xd8")
                end = buf.find(b"\xff\xd9", start + 2)
                if start != -1 and end != -1:
                    img = cv2.imdecode(np.frombuffer(buf[start:end + 2], np.uint8), cv2.IMREAD_COLOR)
                    buf = buf[end + 2:]
                    if img is not None:
                        self._put(img)
                elif len(buf) > 2_000_000:
                    buf = b""

    def _read_capture(self) -> None:
        cap = cv2.VideoCapture(int(self.source) if self.source.isdigit() else self.source)
        if not cap.isOpened():
            raise RuntimeError(f"카메라를 열 수 없음: {self.source}")
        while True:
            ok, img = cap.read()
            if not ok:
                break
            self._put(img)
        cap.release()


def make_detector() -> cv2.aruco.ArucoDetector:
    dictionary = cv2.aruco.getPredefinedDictionary(cv2.aruco.DICT_4X4_50)
    return cv2.aruco.ArucoDetector(dictionary, cv2.aruco.DetectorParameters())


def find_marker(detector: cv2.aruco.ArucoDetector, frame: np.ndarray, marker_id: int) -> np.ndarray | None:
    """찾는 ID의 마커 꼭짓점 4개(4×2 픽셀 좌표)를 돌려준다. 없으면 None."""
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    corners, ids, _ = detector.detectMarkers(gray)
    if ids is None:
        return None
    for c, i in zip(corners, ids.flatten()):
        if i == marker_id:
            return c.reshape(4, 2)
    return None


def measure(corners: np.ndarray, width_px: int, hfov_deg: float, marker_m: float) -> tuple[float, float]:
    """마커의 좌우 위치 dx(-1 왼쪽 ~ +1 오른쪽)와 거리(m)를 구한다.

    카메라 보정 없이 화각만으로 초점거리를 어림하는 핀홀 모델: 거리 = 초점거리 × 실제 크기 / 화면 크기.
    마커가 비스듬하면 작게 보여 거리가 조금 길게 나온다.
    """
    dx = (corners[:, 0].mean() - width_px / 2) / (width_px / 2)
    side_px = np.mean([np.linalg.norm(corners[i] - corners[(i + 1) % 4]) for i in range(4)])
    focal_px = (width_px / 2) / math.tan(math.radians(hfov_deg) / 2)
    return float(dx), float(focal_px * marker_m / side_px)


def clamp(v: float, lim: float) -> float:
    return max(-lim, min(lim, v))


def follow_command(dx: float, dist: float, a: argparse.Namespace) -> tuple[int, int]:
    """마커가 보일 때의 (속도, 회전)."""
    turn = clamp(a.turn_sign * a.k_turn * dx, a.max_turn)
    if dist < a.back_m:
        return -a.back_speed, 0   # 너무 가까움: 돌지 말고 똑바로 물러난다
    err = dist - a.target_m
    if err < 0.05:
        speed = 0.0               # 목표 거리 안쪽이면 제자리에서 방향만 맞춘다
    else:
        speed = max(a.min_speed, min(a.max_speed, a.k_speed * err))
        speed *= max(0.0, 1 - abs(dx))   # 마커가 옆으로 많이 벗어나면 먼저 돌고 나서 걷는다
        if speed < a.min_speed:
            speed = 0.0
    return int(speed), int(turn)


class Follower:
    """마커를 보고 / 놓치고 / 찾고 / 포기하는 상태를 관리한다."""

    def __init__(self, a: argparse.Namespace):
        self.a = a
        self.state = "wait"           # wait(아직 못 봄) / follow / hold(잠깐 놓침) / search / gave_up
        self.last_seen = 0.0
        self.last_dx = 0.0

    def step(self, seen: tuple[float, float] | None, now: float) -> tuple[int, int]:
        a = self.a
        if seen is not None:
            dx, dist = seen
            self.state, self.last_seen, self.last_dx = "follow", now, dx
            return follow_command(dx, dist, a)
        if self.last_seen == 0:
            self.state = "wait"
            return 0, 0
        if self.state == "gave_up":
            return 0, 0
        lost_for = now - self.last_seen
        if lost_for < a.lost_sec:
            self.state = "hold"       # 프레임 한두 장 놓친 것일 수 있으니 멈춰서 기다린다
        elif lost_for < a.lost_sec + a.search_sec:
            self.state = "search"
            direction = 1 if self.last_dx >= 0 else -1
            return a.search_speed, int(a.turn_sign * direction * a.search_turn)
        else:
            self.state = "gave_up"
        return 0, 0


class DogLink:
    """모니터 서버의 /api/dogs를 0.5초마다 읽어 로봇견 초음파 거리와 마지막 응답 시각을 알려 준다."""

    def __init__(self, monitor: str | None, name: str):
        self.url = f"http://{monitor}:{MONITOR_HTTP}/api/dogs" if monitor else None
        self.name = name
        self.front_cm: float | None = None
        self.age: float | None = None   # 로봇견이 마지막으로 보낸 지 몇 초 (모름 = None)
        self.at = 0.0                   # 마지막으로 서버에서 읽은 시각
        if self.url:
            threading.Thread(target=self._run, daemon=True).start()

    def _run(self) -> None:
        while True:
            try:
                with urllib.request.urlopen(self.url, timeout=1) as r:
                    body = json.load(r)
                dog = body["dogs"].get(self.name, {})
                self.front_cm = dog.get("front_cm")
                self.age = body["now"] - dog["self_ts"] if "self_ts" in dog else None
                self.at = time.time()
            except Exception:
                pass   # 서버가 아직 안 떴거나 예전 서버(/api/dogs 없음): 카메라만으로 판단
            time.sleep(0.5)

    def fresh(self, link_sec: float) -> bool:
        return self.age is not None and self.age + (time.time() - self.at) <= link_sec


class ReadyCheck:
    """STANDBY 동안 '출발해도 되는가'를 판단한다. 조건이 hold 시간 이상 이어져야 준비 완료,
    조건이 0.5초 넘게 깨져야 준비 취소 (마커 인식이 잠깐 깜빡여도 표시가 흔들리지 않게)."""

    def __init__(self, a: argparse.Namespace, link: DogLink):
        self.a, self.link = a, link
        self.ready = False
        self.reason = "확인 중"
        self.dist: float | None = None
        self.dist_src = ""
        self._ok_since: float | None = None
        self._bad_since: float | None = None

    def step(self, seen: tuple[float, float] | None, now: float) -> bool:
        a = self.a
        reasons = []
        if seen is None:
            reasons.append(f"marker {a.marker_id} not seen")
        use_link = bool(a.dog and self.link.url)
        link_ok = use_link and self.link.fresh(a.link_sec)
        if use_link and not link_ok:
            reasons.append("dog not responding (dog_cmd.py?)")
        if link_ok and self.link.front_cm is not None:
            self.dist, self.dist_src = self.link.front_cm / 100, "sonar"
        elif seen is not None:
            self.dist, self.dist_src = seen[1], "camera"
        else:
            self.dist, self.dist_src = None, ""
        if self.dist is not None and not a.ready_min_m <= self.dist <= a.ready_max_m:
            reasons.append(f"gap {self.dist:.2f} m ({a.ready_min_m}-{a.ready_max_m} m)")
        ok = not reasons
        self.reason = "; ".join(reasons)
        if ok:
            self._bad_since = None
            self._ok_since = self._ok_since or now
            if now - self._ok_since >= a.ready_hold_sec:
                self.ready = True
            else:
                self.reason = f"holding {a.ready_hold_sec:.0f} s"   # 조건은 맞고 유지 시간을 기다리는 중
        else:
            self._ok_since = None
            self._bad_since = self._bad_since or now
            if now - self._bad_since > 0.5:
                self.ready = False
        return self.ready


STATE_TEXT = {"paused": "STANDBY (일렬 대기)", "wait": "마커 기다리는 중", "follow": "따라가는 중", "hold": "잠깐 놓침, 정지",
              "search": "다시 찾는 중", "gave_up": "못 찾음, 정지", "no_video": "영상 끊김, 정지"}


def draw(frame: np.ndarray, corners: np.ndarray | None, seen, state: str, cmd: tuple[int, int], a,
         ready: ReadyCheck | None = None) -> None:
    h, w = frame.shape[:2]
    cv2.line(frame, (w // 2, 0), (w // 2, h), (180, 180, 180), 1)
    if corners is not None:
        cv2.polylines(frame, [corners.astype(int)], True, (0, 200, 0), 2)
        cx, cy = corners.mean(axis=0).astype(int)
        cv2.line(frame, (w // 2, cy), (cx, cy), (0, 0, 255), 2)
    color = (0, 160, 0) if state == "follow" else (0, 140, 255)
    lines = [f"{state}  cmd v={cmd[0]} turn={cmd[1]}"]
    if state == "paused" and ready is not None:
        color = (0, 160, 0) if ready.ready else (0, 0, 220)
        lines = ["STANDBY - READY for EXPLORE" if ready.ready else "STANDBY - not ready",
                 "SPACE (or Enter) = start" if ready.ready else ready.reason]
        if ready.dist is not None:
            lines.append(f"gap {ready.dist:.2f} m ({ready.dist_src})")
    elif state == "paused":
        color = (0, 160, 0) if seen else (0, 0, 220)
        lines = [f"PAUSED - marker {'OK' if seen else 'NOT seen'}", "SPACE (or Enter in terminal) = start"]
    if seen:
        lines.append(f"dx={seen[0]:+.2f}  dist={seen[1]:.2f} m  (target {a.target_m} m)")
    for i, text in enumerate(lines):   # OpenCV 기본 글꼴은 한글을 못 그려서 영어로 표시한다
        cv2.putText(frame, text, (10, 24 + 22 * i), cv2.FONT_HERSHEY_SIMPLEX, 0.6, color, 2)


def main() -> None:
    a = parse_args()
    cam = LatestFrame(a.camera)
    detector = make_detector()
    follower = Follower(a)
    link = DogLink(a.monitor, a.name)
    ready = None if a.no_ready_check else ReadyCheck(a, link)
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

    def to_monitor(msg: dict) -> None:
        if a.monitor:
            sock.sendto(json.dumps(msg).encode(), (a.monitor, MONITOR_PORT))

    dog_err = [None]   # 같은 전송 오류를 매 주기 출력하지 않도록 마지막 오류만 기억

    def to_dog(speed: int, turn: int) -> None:
        if not a.dog:
            return
        try:
            sock.sendto(json.dumps({"cmd": "move", "v": speed, "turn": turn}).encode(), (a.dog, CMD_PORT))
            if dog_err[0]:
                print(f"[{time.strftime('%H:%M:%S')}] 로봇견 {a.dog} 전송 다시 됨")
                dog_err[0] = None
        except OSError as e:   # 로봇견이 꺼졌거나 IP가 바뀜: 죽지 말고 계속 시도
            if dog_err[0] != e.errno:
                print(f"[{time.strftime('%H:%M:%S')}] 로봇견 {a.dog}에 전송 실패 ({e.strerror}). "
                      "Thonny에서 dog_cmd.py 실행과 IP를 확인하세요.")
                dog_err[0] = e.errno

    mode = f"로봇견 {a.dog}:{CMD_PORT}로 명령 전송" if a.dog else "연습 모드 (명령 전송 안 함)"
    print(f"마커 ID {a.marker_id} 따라가기 · {mode} · 카메라 {a.camera} · Ctrl+C로 종료")
    if not a.autostart:
        if ready:
            print(f"STANDBY로 시작합니다. 마커 {a.marker_id}가 보이고 앞 개와 {a.ready_min_m}~{a.ready_max_m} m 거리가 "
                  f"{a.ready_hold_sec:.0f}초 이상 유지되면 준비 완료. 그다음 스페이스(카메라 창) 또는 Enter(터미널)로 출발.")
            if a.dog and not a.monitor:
                print("  --monitor가 없어 초음파와 로봇견 응답은 확인하지 않고 카메라로만 판단합니다.")
        else:
            print("일시정지 상태로 시작합니다. 스페이스(카메라 창) 또는 Enter(터미널)로 시작/일시정지 (준비 확인 안 함).")
    to_monitor({"type": "event", "from": a.name, "level": "info",
                "text": f"Dog {a.name} 마커 {a.marker_id} 따라가기 준비 ({'실제' if a.dog else '연습'})"})

    # 시작/일시정지 신호: 카메라 창의 스페이스 키 또는 터미널의 Enter
    toggle = threading.Event()

    def read_enter() -> None:
        try:
            for _ in sys.stdin:
                toggle.set()
        except (OSError, ValueError):
            pass   # 터미널 입력이 없는 환경(백그라운드 실행 등)

    threading.Thread(target=read_enter, daemon=True).start()

    period = 1 / a.rate
    running = a.autostart
    last_seq, last_state, last_print, last_frame_at = -1, None, 0.0, time.time()
    was_ready = None
    cmd, corners, seen = (0, 0), None, None
    try:
        while True:
            t0 = time.time()
            if toggle.is_set():
                toggle.clear()
                running = not running
                if running and ready and not ready.ready:
                    running = False   # 준비가 안 됐으면 출발하지 않는다
                    print(f"\n[{time.strftime('%H:%M:%S')}] 아직 준비 안 됨: {ready.reason}. "
                          "준비 확인 없이 출발하려면 --no-ready-check")
                elif running:
                    follower = Follower(a)   # 이전에 놓쳤던 기록 없이 새로 시작
                    print(f"\n[{time.strftime('%H:%M:%S')}] 시작" + ("" if seen else " (마커가 아직 안 보임: 보이면 따라감)"))
                else:
                    print(f"\n[{time.strftime('%H:%M:%S')}] 일시정지")
            seq, frame = cam.get()
            if frame is None:
                if t0 - last_print > 2:
                    print("카메라 영상 기다리는 중...", cam.error or "")
                    last_print = t0
                to_dog(0, 0)
                time.sleep(period)
                continue
            if seq != last_seq:
                last_seq, last_frame_at = seq, t0
                corners = find_marker(detector, frame, a.marker_id)
                seen = measure(corners, frame.shape[1], a.hfov, a.marker_cm / 100) if corners is not None else None
                if running:
                    cmd = follower.step(seen, t0)
                else:
                    follower.state, cmd = "paused", (0, 0)   # 마커는 계속 인식하지만 움직이지 않는다
            elif t0 - last_frame_at > STALE_SEC:
                # 영상이 멈췄으면 오래된 화면을 보고 움직이지 않도록 정지한다
                follower.state, cmd = "no_video", (0, 0)
            if ready and not running:
                ready.step(None if t0 - last_frame_at > STALE_SEC else seen, t0)   # 영상이 멈췄으면 준비 아님
                if ready.ready != was_ready:
                    if ready.ready:
                        text = f"Dog {a.name} STANDBY: EXPLORE 준비 완료 (마커 {a.marker_id}, 거리 {ready.dist:.2f} m)"
                        print(f"\n[{time.strftime('%H:%M:%S')}] 준비 완료. 스페이스 또는 Enter로 출발")
                    else:
                        text = f"Dog {a.name} STANDBY: 준비 안 됨 ({ready.reason})"
                        if was_ready:
                            print(f"\n[{time.strftime('%H:%M:%S')}] 준비 취소: {ready.reason}")
                    if was_ready is not None or ready.ready:
                        to_monitor({"type": "event", "from": a.name, "level": "info" if ready.ready else "warn", "text": text})
                    was_ready = ready.ready
            elif running:
                was_ready = None   # 다시 일시정지하면 준비 확인을 처음부터
            to_dog(*cmd)   # 같은 명령이라도 매 주기 보낸다: 로봇견 워치독(1초)이 멈추지 않게

            if follower.state != last_state:
                print(f"[{time.strftime('%H:%M:%S')}] {STATE_TEXT[follower.state]}")
                level = "warn" if follower.state in ("search", "gave_up") else "info"
                what = "" if follower.state == "no_video" else f"마커 {a.marker_id} "
                to_monitor({"type": "event", "from": a.name, "level": level,
                            "text": f"Dog {a.name}: {what}{STATE_TEXT[follower.state]}"})
                last_state = follower.state
            if t0 - last_print > 0.5:
                to_monitor({"type": "dog", "from": a.name, "state": follower.state,
                            **({"ready": ready.ready} if ready and not running else {})})
                print(f"  {follower.state:8s} v={cmd[0]:4d} turn={cmd[1]:4d}", end="\r")
                last_print = t0

            if a.show:
                view = frame.copy()
                draw(view, corners, seen, follower.state, cmd, a, ready)
                cv2.imshow(f"marker follow - Dog {a.name}", view)
                key = cv2.waitKey(1) & 0xFF
                if key == ord("q"):
                    break
                if key == ord(" "):
                    toggle.set()
            time.sleep(max(0.0, period - (time.time() - t0)))
    except KeyboardInterrupt:
        pass
    finally:
        for _ in range(3):   # UDP는 잃어버릴 수 있으니 정지 명령을 여러 번 보낸다
            to_dog(0, 0)
            time.sleep(0.05)
        to_monitor({"type": "event", "from": a.name, "level": "info", "text": f"Dog {a.name} 마커 따라가기 종료"})
        print("\n종료: 정지 명령 전송")


if __name__ == "__main__":
    main()
