"""포위(CONTAIN)와 재정렬(REGROUP) 연습: 정해 둔 경로를 시간으로 움직인다 (추측 항법, LiDAR 없이).

장면 (위에서 본 그림, 줄은 오른쪽 X를 향함. A는 침입자 앞 반경 R에서 정지한 관제탑):

        C ●  (왼쪽 120° 지점: C는 맨 뒤라 크게 돌아 침입자 둘레 시계 방향으로 들어감)
    C ─ B ─ A    X
        B ●  (오른쪽 120° 지점: B는 작게 돌아 침입자 둘레 시계 반대 방향으로 들어감)

1. STANDBY: 일렬로 대기. Enter = "침입자 발견"
2. CONTAIN: 옆으로 --out만큼(90° 돌아 직진) → 앞으로 --forward만큼(다시 돌아 직진). --path arc면 곡선 하나로
3. HOLD:    정지하고 사람이 확인. 어긋났으면 이때 teleop.py로 살짝 조정해도 됨. Enter = "재정렬"
4. REGROUP: 같은 길을 거꾸로 밟아 원래 줄 자리로 돌아옴. --camera를 주면 마지막에 앞 개 마커가
   화면 가운데 오도록 제자리에서 조금씩 돌아 방향을 맞춤 (짧게 돌고 → 멈춰서 보고 → 반복, 최대 ±15°)

기본값은 이름으로 정한다: B = 오른쪽 0.2 m → 앞 0.2 m, C = 왼쪽 0.6 m → 앞 0.6 m(크게).
도착 지점은 출발 위치 기준 --forward, --out으로 정하고, 곡선이 바깥으로 불룩한 정도는 --swing.

MechDog는 자기 위치를 모르므로, 개마다 "회전값 T로 초당 몇 도", "속도 V로 초당 몇 m",
"걸으면서 회전값 A로 초당 몇 도(곡선)"를 먼저 재 둔다(calibrate, calibrate --arc).
오차가 쌓이므로(미끄러짐, 배터리) 처음에는 넓은 공간에서 천천히 시험한다.

보정 (개마다 한 번, 결과는 brain/calibration.json에 저장):
    python brain/contain_demo.py calibrate --dog 192.168.0.19 --name B         # 제자리 회전, 직진
    python brain/contain_demo.py calibrate --dog 192.168.0.19 --name B --arc   # 걸으면서 반 바퀴 (곡선)

연습 모드 (명령을 보내지 않고 경로와 시간만 출력, --monitor를 주면 대시보드에 추측 위치 표시):
    python brain/contain_demo.py run --name B --monitor 127.0.0.1

실제 실행:
    python brain/contain_demo.py run --dog 192.168.0.19 --name B --monitor 127.0.0.1
    python brain/contain_demo.py run --dog 192.168.0.19 --name C   # C는 왼쪽으로 크게
"""
import argparse
import json
import math
import socket
import sys
import threading
import time
from pathlib import Path

CMD_PORT = 9100       # firmware/dog_cmd.py가 명령을 받는 포트
MONITOR_PORT = 9000   # server/app.py의 UDP 포트
RATE = 10.0           # 명령 주기 (Hz). 로봇견 워치독(1초)보다 충분히 자주
SETTLE_SEC = 0.6      # 동작 사이에 잠깐 멈춰 흔들림을 가라앉힌다
CALIB_FILE = Path(__file__).resolve().parent / "calibration.json"


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="포위와 재정렬 연습 (추측 항법)")
    p.add_argument("mode", choices=("calibrate", "run"), help="calibrate: 회전·직진 속도 재기 / run: 포위 → 재정렬")
    p.add_argument("--dog", help="로봇견 IP. 없으면 명령을 보내지 않는 연습 모드")
    p.add_argument("--name", default="B", help="로봇견 이름 (보정값 저장 키, 모니터 표시용)")
    p.add_argument("--side", choices=("left", "right", "both"),
                   help="포위 방향. 기본: B는 right(침입자 둘레 시계 반대), C는 left(시계). both = 양쪽 차례로")
    p.add_argument("--slot", type=int, help="줄에서 A 뒤 몇 번째인지. 기본: B는 1, C는 2")
    p.add_argument("--forward", type=float, help="출발 위치에서 앞으로 몇 m 간 곳에 설지. 기본: B 0.2, C 0.6")
    p.add_argument("--out", type=float, help="출발 위치에서 옆(포위 방향)으로 몇 m 간 곳에 설지. 기본: B 0.2, C 0.6")
    p.add_argument("--radius", type=float, default=0.8, help="A 앞 몇 m에 침입자가 있는지 (도착 후 바라볼 방향, 지도 표시)")
    p.add_argument("--swing", type=float,
                   help="곡선이 출발점–도착점 직선보다 바깥으로 불룩한 정도 (m). 크면 크게 돈다. 기본: 직선 길이의 30%%")
    p.add_argument("--spacing", type=float, default=0.45, help="줄에서 개 중심 사이 거리 (m): 몸길이 + 간격 약 0.2 m")
    p.add_argument("--speed", type=int, default=80, help="직진 속도 값 (보정할 때와 같은 값을 써야 함)")
    p.add_argument("--turn", type=int, default=40, help="제자리 회전 값 (보정할 때와 같은 값을 써야 함)")
    p.add_argument("--path", choices=("lines", "arc"), default="lines",
                   help="lines: 옆으로 꺾어 직진 → 앞으로 직진 (기본) / arc: 곡선 하나 (calibrate --arc 필요)")
    p.add_argument("--turn-in", type=float, default=100.0,
                   help="포위 3번째 동작(옆으로 빠진 뒤 다시 앞을 보는 회전) 각도. 계산상 90°지만 실측에 맞춰 조정 (B 실측 2026-11-13: 100)")
    p.add_argument("--turn-back", type=float, default=190.0,
                   help="복귀 첫 동작(뒤로 도는 회전) 각도. 계산상 180°지만 실측에 맞춰 조정 (B 실측 2026-11-13: 190)")
    p.add_argument("--face", action="store_true", help="도착 후 제자리에서 침입자 쪽으로 돈다 (기본: 앞을 보고 멈춤)")
    p.add_argument("--arc-turn", type=int, default=20,
                   help="걸으면서 돌 때(곡선) 보정에 쓰는 회전 값. 경로에 맞게 이 값에 비례해 조절한다")
    p.add_argument("--arc", action="store_true", help="calibrate에서 곡선(걸으며 회전) 보정만 한다")
    p.add_argument("--turn-sign", type=int, choices=(1, -1), default=-1,
                   help="MechDog B 실측(2026-11-12)은 -1. 보정할 때 반대로 돌면 바꾼다")
    p.add_argument("--deg-per-sec", type=float, help="보정값 직접 입력 (calibration.json보다 우선)")
    p.add_argument("--m-per-sec", type=float, help="보정값 직접 입력 (calibration.json보다 우선)")
    p.add_argument("--intruder", type=float, nargs=2, default=(3.5, 2.0), metavar=("X", "Y"),
                   help="대시보드 지도에 그릴 침입자 위치 (web/arena.json 좌표, m)")
    p.add_argument("--monitor", help="모니터 서버 IP. 주면 상태, 이벤트, 추측 위치를 대시보드로 보낸다")
    p.add_argument("--camera", help="복귀 끝에 앞 개 마커를 화면 가운데로 맞출 카메라 (marker_follow.py와 같은 형식)")
    p.add_argument("--marker-id", type=int, help="복귀 끝에 맞출 앞 개 마커 ID. 기본: B는 1, C는 2")
    p.add_argument("--center-max", type=float, default=15.0, help="마커 정렬 때 복귀 방향에서 최대 몇 도까지 돌지")
    p.add_argument("--center-tol", type=float, default=0.1,
                   help="마커가 가운데로부터 이 범위(화면 반폭 대비, 0.1 = 약 3°) 안이면 정렬 완료")
    a = p.parse_args()
    is_c = a.name.upper() == "C"
    a.side = a.side or ("left" if is_c else "right")
    a.forward = a.forward if a.forward is not None else (0.6 if is_c else 0.2)
    a.out = a.out if a.out is not None else (0.6 if is_c else 0.2)
    a.slot = a.slot or (2 if is_c else 1)
    a.marker_id = a.marker_id if a.marker_id is not None else (2 if is_c else 1)
    return a


# ---------------------------------------------------------------- 명령 보내기

class Link:
    """로봇견에 move 명령을 10 Hz로 계속 보내고, 대시보드에 상태를 보낸다.

    지금 명령(self.cmd)만 바꾸면 백그라운드 스레드가 매 주기 보낸다. None이면 아무것도 보내지 않는다
    (HOLD 중 teleop.py로 조정할 수 있게. 1초 뒤 로봇견 워치독이 알아서 멈춘다).
    """

    def __init__(self, a: argparse.Namespace):
        self.a = a
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.cmd: tuple[int, int] | None = (0, 0)
        self.err = None
        threading.Thread(target=self._run, daemon=True).start()

    def _run(self) -> None:
        while True:
            if self.cmd is not None:
                self.send_dog(*self.cmd)
            time.sleep(1 / RATE)

    def send_dog(self, v: int, turn: int) -> None:
        if not self.a.dog:
            return
        try:
            self.sock.sendto(json.dumps({"cmd": "move", "v": v, "turn": turn}).encode(), (self.a.dog, CMD_PORT))
            self.err = None
        except OSError as e:   # 로봇견이 꺼졌거나 IP가 바뀜: 죽지 말고 계속 시도
            if self.err != e.errno:
                print(f"\n로봇견 {self.a.dog}에 전송 실패 ({e.strerror}). Thonny에서 dog_cmd.py 실행과 IP를 확인하세요.")
                self.err = e.errno

    def stop(self) -> None:
        self.cmd = (0, 0)
        for _ in range(3):   # UDP는 잃어버릴 수 있으니 여러 번
            self.send_dog(0, 0)
            time.sleep(0.05)

    def monitor(self, msg: dict) -> None:
        if self.a.monitor:
            self.sock.sendto(json.dumps(msg).encode(), (self.a.monitor, MONITOR_PORT))

    def event(self, text: str, level: str = "info") -> None:
        print(f"[{time.strftime('%H:%M:%S')}] {text}")
        self.monitor({"type": "event", "from": self.a.name, "level": level, "text": f"Dog {self.a.name}: {text}"})


def turn_value(a: argparse.Namespace, deg: float, mag: int) -> int:
    """반시계(왼쪽)가 +인 방향을 move의 turn 값으로. turn_sign × 양수 = 오른쪽 회전 (marker_follow.py와 같은 약속)."""
    return int(a.turn_sign * mag * (1 if deg < 0 else -1))


def turn_cmd(a: argparse.Namespace, deg: float) -> tuple[int, int]:
    """제자리 회전 명령."""
    return 0, turn_value(a, deg, a.turn)


# ---------------------------------------------------------------- 보정

def calibrate(a: argparse.Namespace, link: Link) -> None:
    if not a.dog:
        sys.exit("보정은 실제 로봇견이 필요합니다: --dog <IP>")
    if a.arc:
        return calibrate_arc(a, link)
    print(f"Dog {a.name} 보정: 회전값 {a.turn}, 직진 속도 {a.speed}. 넓은 바닥에 내려놓으세요. Ctrl+C로 언제든 정지.\n")
    rates = []
    for label, deg in (("왼쪽(반시계)", 360), ("오른쪽(시계)", -360)):
        input(f"[회전] Enter를 누르면 {label}으로 제자리 회전을 시작합니다. 머리 방향을 기억해 두세요.")
        link.cmd, t0 = turn_cmd(a, deg), time.time()
        input("        한 바퀴 돌아 처음 방향으로 오면 Enter")
        dt = time.time() - t0
        link.stop()
        ok = input(f"        {dt:.1f}초. {label}으로 돌았나요? (y/n) ").strip().lower()
        if ok.startswith("n"):
            print(f"        회전 방향이 반대입니다. --turn-sign {-a.turn_sign}로 다시 보정하세요.")
            return
        rates.append(360 / dt)
    input(f"\n[직진] 바닥에 1 m를 표시하고 시작선에 세운 뒤 Enter (속도 {a.speed}로 직진)")
    link.cmd, t0 = (a.speed, 0), time.time()
    input("        코(카메라)가 1 m 선을 넘으면 Enter")
    dt = time.time() - t0
    link.stop()
    m_per_sec = 1.0 / dt
    data = json.loads(CALIB_FILE.read_text()) if CALIB_FILE.exists() else {}
    data[a.name] = {"turn": a.turn, "deg_per_sec": round(sum(rates) / 2, 1),
                    "deg_per_sec_left": round(rates[0], 1), "deg_per_sec_right": round(rates[1], 1),
                    "speed": a.speed, "m_per_sec": round(m_per_sec, 3),
                    "turn_sign": a.turn_sign, "date": time.strftime("%Y-%m-%d")}
    CALIB_FILE.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n")
    print(f"\n저장: {CALIB_FILE.name} → {a.name} {data[a.name]}")
    if abs(rates[0] - rates[1]) / max(rates) > 0.2:
        print("왼쪽·오른쪽 회전 속도가 20% 넘게 다릅니다. 경로 계산은 방향별 값을 씁니다.")


def calibrate_arc(a: argparse.Namespace, link: Link) -> None:
    """걸으면서 회전값 arc_turn으로 돌 때 초당 몇 도 도는지 (반 바퀴 = 머리가 반대쪽을 볼 때까지)."""
    data = json.loads(CALIB_FILE.read_text()) if CALIB_FILE.exists() else {}
    if a.name not in data:
        sys.exit("먼저 제자리 회전·직진 보정을 하세요: calibrate (--arc 없이)")
    speed = data[a.name]["speed"]
    print(f"Dog {a.name} 곡선 보정: 속도 {speed}로 걸으면서 회전값 {a.arc_turn}. 지름 2~3 m 공간이 필요합니다. Ctrl+C로 정지.\n")
    rates, arc_sign = [], data[a.name].get("arc_sign", 1)
    for label, deg in (("왼쪽(반시계)", 180), ("오른쪽(시계)", -180)):
        input(f"[곡선] Enter를 누르면 {label}으로 걸으면서 돕니다. 처음 머리 방향을 기억해 두세요.")
        link.cmd, t0 = (speed, arc_sign * turn_value(a, deg, a.arc_turn)), time.time()
        input("        머리가 처음과 반대쪽(180°)을 보면 Enter")
        dt = time.time() - t0
        link.stop()
        if deg > 0 and input(f"        {label}으로 휘었나요? (y/n) ").strip().lower().startswith("n"):
            arc_sign = -arc_sign   # 걸을 때는 제자리 회전과 방향이 반대: 곡선만 뒤집는다
            data[a.name]["arc_sign"] = arc_sign
            CALIB_FILE.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n")
            print("        걸으면서 돌 때 방향이 반대입니다. 곡선 방향을 뒤집어 저장했습니다. 왼쪽부터 다시 잽니다.")
            return calibrate_arc(a, link)
        print(f"        {dt:.1f}초 → 초당 {180 / dt:.1f}°, 곡선 반지름 약 {data[a.name]['m_per_sec'] * dt / math.pi:.2f} m")
        rates.append(180 / dt)
    data[a.name].update({"arc_turn": a.arc_turn, "arc_sign": arc_sign, "arc_deg_per_sec_left": round(rates[0], 2),
                         "arc_deg_per_sec_right": round(rates[1], 2), "arc_date": time.strftime("%Y-%m-%d")})
    CALIB_FILE.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n")
    print(f"\n저장: {CALIB_FILE.name} → {a.name} {data[a.name]}")


def load_rates(a: argparse.Namespace) -> dict:
    """보정값. 없으면 연습용 어림값 (ok=False / arc_ok=False)."""
    c = json.loads(CALIB_FILE.read_text()).get(a.name, {}) if CALIB_FILE.exists() else {}
    if c and (c.get("turn") != a.turn or c.get("speed") != a.speed):
        print(f"경고: 보정은 회전값 {c.get('turn')}, 속도 {c.get('speed')}로 했습니다. 같은 값을 쓰세요.")
    left = a.deg_per_sec or c.get("deg_per_sec_left") or c.get("deg_per_sec")
    right = a.deg_per_sec or c.get("deg_per_sec_right") or c.get("deg_per_sec")
    mps = a.m_per_sec or c.get("m_per_sec")
    r = {"left": left or 30.0, "right": right or 30.0, "mps": mps or 0.10, "ok": bool(left and right and mps),
         "arc_turn": c.get("arc_turn", a.arc_turn), "arc_left": c.get("arc_deg_per_sec_left", 8.0),
         "arc_right": c.get("arc_deg_per_sec_right", 8.0), "arc_ok": "arc_deg_per_sec_left" in c,
         "arc_sign": c.get("arc_sign", 1)}
    if r["arc_ok"] and r["arc_turn"] != a.arc_turn:
        a.arc_turn = r["arc_turn"]   # 보정한 값 기준으로 비례 계산
    return r


# ---------------------------------------------------------------- 경로

def plan(a: argparse.Namespace, side: str, rates: dict) -> tuple[list, list, tuple, tuple]:
    return plan_lines(a, side) if a.path == "lines" else plan_arc(a, side, rates)


def plan_lines(a: argparse.Namespace, side: str) -> tuple[list, list, tuple, tuple]:
    """옆으로 --out만큼 → 앞으로 --forward만큼. MechDog는 옆걸음을 못 하므로 90° 돌아 걷고 다시 돌아 걷는다.
    동작은 ("turn", 각도°, 반시계 +) 또는 ("walk", 거리 m). 복귀는 같은 길을 거꾸로."""
    s = 1 if side == "left" else -1
    start = (-a.radius - a.slot * a.spacing, 0.0)
    corner = (start[0], s * a.out)
    target = (start[0] + a.forward, s * a.out)
    contain, h = path(start, 0.0, [corner, target])
    end = plan_face(target) if a.face else 0.0
    contain.append(("turn", norm(end - h)))
    regroup, h = path(target, end, [corner, start])
    regroup.append(("turn", norm(0.0 - h)))
    # 실측 보정: 이 두 회전은 계산값과 실제가 달라 각도를 직접 정한다 (방향은 그대로)
    contain[2] = ("turn", math.copysign(a.turn_in, contain[2][1]))
    if not a.face:
        regroup[0] = ("turn", math.copysign(a.turn_back, regroup[0][1]))
    keep = lambda steps: [x for x in steps if abs(x[1]) > 1e-3]
    return keep(contain), keep(regroup), start, target


def path(p: tuple, heading: float, waypoints: list) -> tuple[list, float]:
    """현재 위치·방향에서 경유점을 차례로: 그쪽으로 돌고 → 걷기. (동작 목록, 마지막 방향)"""
    steps = []
    for w in waypoints:
        dx, dy = w[0] - p[0], w[1] - p[1]
        dist = math.hypot(dx, dy)
        if dist < 1e-6:
            continue
        bearing = math.degrees(math.atan2(dy, dx))
        steps += [("turn", norm(bearing - heading)), ("walk", dist)]
        p, heading = w, bearing
    return steps, heading


def plan_arc(a: argparse.Namespace, side: str, rates: dict) -> tuple[list, list, tuple, tuple]:
    """침입자를 원점(0, 0), A를 (-R, 0)에서 +x를 바라본다고 두고 포위·복귀 동작 목록을 만든다.

    직진 없이 곡선 하나로: 제자리에서 곡선 시작 방향으로 돌기 → 걸으면서 돌아(원호) 목표 지점까지 →
    제자리에서 침입자를 바라보기. 곡선은 출발점–목표 직선보다 --swing만큼 바깥(A·침입자 반대쪽)으로 불룩하다.
    복귀는 같은 곡선을 거꾸로. 동작은 ("turn", 각도°) 또는 ("arc", 각도°, 회전값, °/s), 반시계가 +.
    """
    s = 1 if side == "left" else -1
    start = (-a.radius - a.slot * a.spacing, 0.0)
    target = (start[0] + a.forward, s * a.out)            # 출발 위치 기준 앞으로 forward, 옆으로 out
    dx, dy = target[0] - start[0], target[1] - start[1]
    chord = math.hypot(dx, dy)
    h = a.swing if a.swing is not None else 0.3 * chord
    if h <= 0.03:
        raise SystemExit("--swing은 0.03 m보다 커야 곡선이 됩니다.")
    r = (chord ** 2 / 4 + h ** 2) / (2 * h)               # 현의 길이와 불룩한 정도로 원 반지름
    beta = 2 * math.degrees(math.asin(min(1.0, chord / (2 * r))))
    if h > chord / 2:
        beta = 360 - beta                                    # 반원보다 크게 도는 경우
    d = -s                                                   # 오른쪽 포위는 반시계(+), 왼쪽은 시계(-) 곡선
    phi_c = math.degrees(math.atan2(dy, dx))
    h0, h1 = phi_c - d * beta / 2, phi_c + d * beta / 2     # 곡선 시작·끝 방향
    face = plan_face(target)
    out_arc = arc_step(a, rates, d * beta, r)
    back_arc = arc_step(a, rates, -d * beta, r)
    contain = [("turn", norm(h0)), out_arc, ("turn", norm(face - h1))]
    regroup = [("turn", norm(h1 + 180 - face)), back_arc, ("turn", norm(0 - (h0 + 180)))]
    return [x for x in contain if abs(x[1]) > 0.5], [x for x in regroup if abs(x[1]) > 0.5], start, target


def arc_step(a, rates: dict, deg: float, r: float) -> tuple:
    """반지름 r의 원호를 deg만큼. 필요한 회전 속도(°/s) = 걷는 속도 / r. 회전값은 보정값에 비례한다고 보고 고른다."""
    need = math.degrees(rates["mps"] / r)
    cal = rates["arc_left"] if deg > 0 else rates["arc_right"]
    value = round(a.arc_turn * need / cal)
    if not 3 <= value <= 50:
        raise SystemExit(f"반지름 {r:.2f} m 곡선에는 회전값 {value}가 필요해 범위(3~50)를 벗어납니다. "
                         f"{'--swing을 키우거나 --forward/--out을 늘리세요' if value > 50 else '--swing을 줄이세요'}.")
    return ("arc", deg, value, cal * value / a.arc_turn)


def norm(deg: float) -> float:
    return (deg + 180) % 360 - 180


def describe(steps: list, rates: dict) -> str:
    out = []
    for step in steps:
        kind, x = step[0], step[1]
        side = "왼쪽" if x > 0 else "오른쪽"
        if kind == "turn":
            out.append(f"제자리 {side} {abs(x):.0f}° ({abs(x) / rates['left' if x > 0 else 'right']:.1f}초)")
        elif kind == "walk":
            out.append(f"직진 {x:.2f} m ({x / rates['mps']:.1f}초)")
        else:
            r = math.degrees(rates["mps"] / step[3])
            out.append(f"{side}으로 곡선 {abs(x):.0f}° (반지름 {r:.2f} m, 회전값 {step[2]}, {abs(x) / step[3]:.1f}초)")
    return " → ".join(out)


class Pose:
    """추측 위치. 침입자 기준 좌표를 대시보드 지도 좌표로 옮겨 보낸다."""

    def __init__(self, a, link: Link, x: float, y: float, heading: float):
        self.a, self.link = a, link
        self.x, self.y, self.h = x, y, heading

    def send(self, state: str) -> None:
        ix, iy = self.a.intruder
        self.link.monitor({"type": "dog", "from": self.a.name, "x": round(ix + self.x, 3), "y": round(iy + self.y, 3),
                           "heading_deg": round(self.h % 360, 1), "state": state})


def execute(a, link: Link, pose: Pose, steps: list, rates: dict, state: str) -> None:
    mps = rates["mps"]
    for step in steps:
        kind, x = step[0], step[1]
        if kind == "turn":
            rate = rates["left" if x > 0 else "right"]
            dur, cmd = abs(x) / rate, turn_cmd(a, x)
        elif kind == "walk":
            rate = 0.0
            dur, cmd = x / mps, (a.speed, 0)
        else:
            rate = step[3]
            dur, cmd = abs(x) / rate, (a.speed, rates["arc_sign"] * turn_value(a, x, step[2]))
        link.cmd, t0, last = cmd, time.time(), time.time()
        while (now := time.time()) - t0 < dur:
            dt, last = now - last, now
            pose.h += math.copysign(dt * rate, x)
            if kind in ("arc", "walk"):
                pose.x += math.cos(math.radians(pose.h)) * mps * dt
                pose.y += math.sin(math.radians(pose.h)) * mps * dt
            pose.send(state)
            time.sleep(1 / RATE)
        link.cmd = (0, 0)
        time.sleep(SETTLE_SEC)
        pose.send(state)


# ---------------------------------------------------------------- 마커로 방향 맞추기

class Camera:
    """marker_follow.py의 카메라 읽기·마커 인식을 그대로 쓴다. 정렬할 때만 켠다."""

    def __init__(self, source: str, marker_id: int):
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        import marker_follow as mf
        self.mf, self.marker_id = mf, marker_id
        self.cam, self.det = mf.LatestFrame(source), mf.make_detector()

    def look(self, wait: float = 0.6) -> tuple[float, float] | None:
        """멈춘 뒤 wait초 기다려 흔들림·영상 지연이 지나간 새 프레임에서 (dx, 거리)를 본다. 없으면 None."""
        seq0, _ = self.cam.get()
        time.sleep(wait)
        seq, frame = self.cam.get()
        if frame is None or seq == seq0:
            return None
        corners = self.mf.find_marker(self.det, frame, self.marker_id)
        return self.mf.measure(corners, frame.shape[1], 62.0, 0.07) if corners is not None else None


def center_marker(a, link: Link, rates: dict, cam: Camera) -> bool:
    """앞 개 마커가 화면 가운데 오도록 제자리에서 조금씩 돈다. 복귀가 끝난 방향에서 ±--center-max°까지만.

    영상이 0.3초쯤 늦게 오므로 계속 돌면서 보면 지나친다: 짧게 돌고 → 멈춰서 보고 → 다시 돈다.
    마커가 안 보이면 왼쪽 끝, 오른쪽 끝까지 한 번씩 찾아본다. 맞추면 True."""
    lim = a.center_max
    deadline = time.time() + 30
    turned = 0.0                       # 정렬 시작 방향 기준 지금까지 돈 각도 (왼쪽 +)
    sweep = [lim, -lim]                # 안 보일 때 찾아볼 방향 (시작 기준 절대 각도)
    while time.time() < deadline:
        link.cmd = (0, 0)
        seen = cam.look()
        if seen is None:
            if not sweep:
                break
            deg = sweep.pop(0) - turned
            link.event(f"마커 {a.marker_id} 안 보임: {'왼쪽' if deg > 0 else '오른쪽'}으로 {abs(deg):.0f}° 돌며 찾기")
        else:
            dx, dist = seen
            if abs(dx) <= a.center_tol:
                link.event(f"마커 {a.marker_id} 가운데 정렬 완료 (dx {dx:+.2f}, 거리 {dist:.2f} m)")
                return True
            deg = -dx * 31 * 0.8   # 화면 반폭 = 화각 절반(약 31°). 지나치지 않게 80%만 돈다
            deg = math.copysign(max(abs(deg), 2.0), deg)
            deg = max(-lim - turned, min(lim - turned, deg))   # 시작 방향 기준 ±lim을 넘지 않게
            if abs(deg) < 1.0:
                link.event(f"마커가 ±{lim:.0f}° 범위 밖 (dx {dx:+.2f}). 여기서 멈춤", "warn")
                return False
            print(f"  마커 dx {dx:+.2f} → {'왼쪽' if deg > 0 else '오른쪽'} {abs(deg):.0f}°")
        link.cmd = turn_cmd(a, deg)
        time.sleep(abs(deg) / rates["left" if deg > 0 else "right"])
        turned += deg
    link.cmd = (0, 0)
    link.event(f"±{lim:.0f}° 안에서 마커 {a.marker_id}를 가운데로 맞추지 못함. 손으로 방향을 맞춰 주세요", "warn")
    return False


# ---------------------------------------------------------------- 실행

def run(a: argparse.Namespace, link: Link) -> None:
    rates = load_rates(a)
    sides = ["left", "right"] if a.side == "both" else [a.side]
    mode = f"로봇견 {a.dog}" if a.dog else "연습 모드 (명령 안 보냄)"
    print(f"Dog {a.name} 포위 연습 · {mode} · {' → '.join(f'{KO[x]} ({AROUND[x]})' for x in sides)} · "
          f"앞으로 {a.forward} m, 옆으로 {a.out} m · 줄 {a.slot}번째")
    if not (rates["ok"] and (rates["arc_ok"] or a.path == "lines")):
        missing = "calibrate" if not rates["ok"] else "calibrate --arc"
        if a.dog:
            sys.exit(f"Dog {a.name} 보정값이 없습니다. 먼저: python brain/contain_demo.py {missing} --dog {a.dog} --name {a.name}")
        print(f"보정값이 없어 어림값으로 계산합니다 ({missing} 필요).")
    for side in sides:
        contain, regroup, start, target = plan(a, side, rates)
        print(f"\n[{KO[side]}] 포위: {describe(contain, rates)}")
        print(f"[{KO[side]}] 복귀: {describe(regroup, rates)}")
        print(f"[{KO[side]}] 출발 ({start[0]:.2f}, {start[1]:.2f}) → 목표 ({target[0]:.2f}, {target[1]:.2f}) m (침입자 기준)\n")
        contain_once(a, link, rates, side, contain, regroup, start, target)


KO = {"left": "왼쪽", "right": "오른쪽"}
AROUND = {"left": "침입자 둘레 시계 방향", "right": "침입자 둘레 시계 반대 방향"}


def contain_once(a, link: Link, rates: dict, side: str, contain: list, regroup: list, start: tuple, target: tuple) -> None:
    pose = Pose(a, link, *start, 0.0)
    ix, iy = a.intruder
    pose.send("standby")
    link.monitor({"type": "dog", "from": "A", "x": ix - a.radius, "y": iy, "heading_deg": 0, "state": "tower"})
    link.monitor({"type": "mission", "state": "STANDBY"})
    link.event("STANDBY: 일렬 대기")
    input(f"Enter = 침입자 발견, {KO[side]} 포위 시작 (Ctrl+C = 정지)")

    link.monitor({"type": "intruder", "x": ix, "y": iy, "confidence": 1.0})
    link.monitor({"type": "mission", "state": "CONTAIN"})
    link.event(f"CONTAIN: {KO[side]} {a.out} m, 앞 {a.forward} m로 이동 ({AROUND[side]})", "alert")
    execute(a, link, pose, contain, rates, "contain")
    link.stop()

    link.monitor({"type": "mission", "state": "HANDOVER"})
    link.event("HOLD: 포위 완료, 확인 후 재정렬 명령 대기", "alert")
    link.cmd = None   # 명령을 멈춰 teleop.py로 조정할 수 있게 (로봇견은 1초 뒤 스스로 정지)
    input("확인했으면 Enter = 재정렬 (필요하면 그 전에 teleop.py로 위치 조정 후 끄기)")

    link.cmd = (0, 0)
    link.monitor({"type": "mission", "state": "REGROUP"})
    link.event("REGROUP: 줄 자리로 복귀")
    pose.x, pose.y = target   # 조정했더라도 계산상 목표 지점에서 출발
    execute(a, link, pose, regroup, rates, "regroup")
    link.stop()
    if a.camera:
        link.event(f"복귀 경로 끝: 마커 {a.marker_id}로 방향 맞추기")
        center_marker(a, link, rates, Camera(a.camera, a.marker_id))
        link.stop()
    link.monitor({"type": "mission", "state": "STANDBY"})
    link.event("복귀 완료, 다시 일렬 대기")


def plan_face(target: tuple) -> float:
    return math.degrees(math.atan2(-target[1], -target[0]))


def main() -> None:
    a = parse_args()
    link = Link(a)
    try:
        calibrate(a, link) if a.mode == "calibrate" else run(a, link)
    except (KeyboardInterrupt, EOFError):
        print("\n중단")
    finally:
        link.stop()
        if a.dog:
            print("정지 명령 전송")


if __name__ == "__main__":
    main()
