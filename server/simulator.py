"""로봇이 준비되기 전에 대시보드를 만들고 시연할 수 있도록 가짜 텔레메트리를 보낸다.

patrol_apprehension_plan.md 5절의 3분 데모 시나리오를 반복 재생하며,
실제 Pi / 로봇견이 보낼 메시지와 똑같은 메시지를 보낸다 (docs/message_schema.md 참고).

실행:  python -m server.simulator [--host 127.0.0.1] [--port 9000] [--speed 1.0]
"""

import argparse
import base64
import json
import math
import random
import socket
import time
from pathlib import Path

ARENA = json.loads((Path(__file__).resolve().parent.parent / "web" / "arena.json").read_text())
W, H = ARENA["width"], ARENA["height"]
LIDAR = tuple(ARENA["lidar_origin"])
DOG_SPEED = 0.25        # m/s (실제 MechDog 속도를 재서 수정)
INTRUDER_SPEED = 0.12   # m/s
CYCLE = 180.0           # 시나리오 한 바퀴 시간(초)

EXIT1 = (0.4, 1.9)
EXIT2 = (5.6, 1.9)
PATROL = {
    "A": [(2.5, 0.8), (3.5, 0.8), (3.5, 3.4), (2.5, 3.4)],
    "B": [(0.5, 0.5), (1.5, 0.5), (1.5, 2.2), (0.5, 3.5)],
    "C": [(4.6, 0.5), (5.5, 0.5), (5.5, 3.5), (4.6, 3.5)],
}

# ---------------------------------------------------------------- 기하 계산


def wall_segments():
    segs = [((0, 0), (W, 0)), ((0, H), (W, H))]
    for side_x in (0.0, W):
        gaps = sorted((e["a"][1], e["b"][1]) for e in ARENA["exits"] if e["a"][0] == side_x)
        y = 0.0
        for g0, g1 in gaps:
            segs.append(((side_x, y), (side_x, g0)))
            y = g1
        segs.append(((side_x, y), (side_x, H)))
    for o in ARENA["obstacles"]:
        x, y, w, h = o["x"], o["y"], o["w"], o["h"]
        c = [(x, y), (x + w, y), (x + w, y + h), (x, y + h)]
        segs += [(c[i], c[(i + 1) % 4]) for i in range(4)]
    return segs


SEGMENTS = wall_segments()


def ray_segment(o, d, a, b):
    ex, ey = b[0] - a[0], b[1] - a[1]
    den = d[0] * ey - d[1] * ex
    if abs(den) < 1e-9:
        return None
    t = ((a[0] - o[0]) * ey - (a[1] - o[1]) * ex) / den
    u = ((a[0] - o[0]) * d[1] - (a[1] - o[1]) * d[0]) / den
    return t if t > 0 and 0 <= u <= 1 else None


def ray_circle(o, d, c, r):
    fx, fy = o[0] - c[0], o[1] - c[1]
    b = fx * d[0] + fy * d[1]
    disc = b * b - (fx * fx + fy * fy - r * r)
    if disc < 0:
        return None
    t = -b - math.sqrt(disc)
    return t if t > 0 else None


def lidar_scan(bodies):
    """1도 간격 360개 광선. [[angle_deg, dist_m], ...] 반환; 각도 0 = +x, 반시계 방향."""
    pts = []
    for deg in range(360):
        d = (math.cos(math.radians(deg)), math.sin(math.radians(deg)))
        hits = [ray_segment(LIDAR, d, a, b) for a, b in SEGMENTS]
        hits += [ray_circle(LIDAR, d, c, r) for c, r in bodies]
        hits = [h for h in hits if h is not None and h < 12.0]
        if hits:
            pts.append([deg, round(min(hits) + random.gauss(0, 0.01), 3)])
    return pts


# ---------------------------------------------------------------- 시나리오


def lerp_path(path, t):
    """(time, (x, y)) 키프레임 목록을 따라 보간한 위치."""
    for (t0, p0), (t1, p1) in zip(path, path[1:]):
        if t0 <= t <= t1:
            k = (t - t0) / (t1 - t0) if t1 > t0 else 1
            return (p0[0] + (p1[0] - p0[0]) * k, p0[1] + (p1[1] - p0[1]) * k)
    return None


INTRUDER_PATH = [(40, (-0.4, 1.9)), (62, (2.4, 2.0)), (72, (2.9, 1.7)), (100, (4.4, 1.9)), (140, (4.4, 1.9))]
STATES = [(0, "PATROL"), (44, "SUSPECT"), (48, "ALERT"), (50, "INTERCEPT"), (100, "CONTAIN"),
          (130, "HANDOVER"), (145, "RETURN"), (165, "PATROL")]
EVENTS = [
    (1, "info", "순찰 시작: A=중앙, B=구역 1, C=구역 2"),
    (42, "warn", "소리: glass_break, 방향 EXIT 1 쪽"),
    (44, "warn", "LiDAR: EXIT 1 근처에서 미확인 이동 물체 -> SUSPECT"),
    (46, "info", "Dog A가 카메라로 확인하려고 회전"),
    (48, "alert", "카메라: 사람 확인 + LiDAR + 소리 일치 -> ALERT"),
    (50, "alert", "INTERCEPT: B -> EXIT 1, C -> EXIT 2, A는 60 cm 거리에서 추적"),
    (72, "warn", "침입자가 EXIT 2로 이동 중 (도착 25초, Dog C 도착 8초)"),
    (85, "info", "Dog C가 EXIT 2 봉쇄 중"),
    (100, "alert", "침입자 정지 -> CONTAIN, 로봇견 3대가 반경 0.5 m로 포위"),
    (102, "alert", "음성 경고 재생 (B, C), STOP 표시"),
    (130, "info", "HANDOVER: 위치, 스냅샷 6장, 타임라인을 관제실로 전송"),
    (145, "info", "인계 확인 -> 순찰로 RETURN"),
]


def state_at(t):
    return [s for t0, s in STATES if t >= t0][-1]


class Dog:
    def __init__(self, name):
        self.name = name
        self.x, self.y = PATROL[name][0]
        self.heading = 0.0
        self.wp = 1
        self.battery = 7.9 - random.random() * 0.3
        self.role = "patrol"

    def step_toward(self, target, dt):
        dx, dy = target[0] - self.x, target[1] - self.y
        dist = math.hypot(dx, dy)
        if dist < 0.03:
            return True
        step = min(dist, DOG_SPEED * dt)
        self.x += dx / dist * step
        self.y += dy / dist * step
        self.heading = math.degrees(math.atan2(dy, dx))
        return False

    def patrol(self, dt):
        if self.step_toward(PATROL[self.name][self.wp], dt):
            self.wp = (self.wp + 1) % len(PATROL[self.name])


def ring_targets(p, r=0.5):
    # theta0 = 0 이면 한 자리가 EXIT 2(침입자가 향하던 쪽)를 향한다
    return {n: (p[0] + r * math.cos(i * 2 * math.pi / 3), p[1] + r * math.sin(i * 2 * math.pi / 3))
            for i, n in enumerate(["C", "A", "B"])}


def update_dogs(dogs, t, intruder, dt):
    state = state_at(t)
    for d in dogs.values():
        if state in ("PATROL", "RETURN") or (state == "SUSPECT" and d.name != "A"):
            d.role = "patrol"
            d.patrol(dt)
        elif state == "SUSPECT":
            d.role = "verifying"
            d.heading = math.degrees(math.atan2(intruder[1] - d.y, intruder[0] - d.x))
        elif state in ("ALERT", "INTERCEPT"):
            if d.name == "A":
                d.role = "tracking"
                dx, dy = intruder[0] - d.x, intruder[1] - d.y
                dist = math.hypot(dx, dy)
                if dist > 0.6:
                    d.step_toward((intruder[0] - dx / dist * 0.6, intruder[1] - dy / dist * 0.6), dt)
                d.heading = math.degrees(math.atan2(dy, dx))
            else:
                d.role = "blocking"
                d.step_toward(EXIT1 if d.name == "B" else EXIT2, dt)
        elif state in ("CONTAIN", "HANDOVER"):
            d.role = "containing"
            d.step_toward(ring_targets(intruder)[d.name], dt)
            d.heading = math.degrees(math.atan2(intruder[1] - d.y, intruder[0] - d.x))
        d.battery = max(6.4, d.battery - 0.00004 * dt * 10)


def camera_svg(t, dog, intruder_visible, state):
    """ESP32-S3 카메라 JPEG 대용: 작은 SVG 프레임을 data URL로 만든다."""
    person = ""
    if intruder_visible:
        person = ('<rect x="120" y="40" width="70" height="140" fill="none" stroke="#ff4d4f" stroke-width="3"/>'
                  '<circle cx="155" cy="70" r="18" fill="#556"/><rect x="135" y="90" width="40" height="80" rx="8" fill="#556"/>'
                  '<text x="122" y="34" fill="#ff4d4f" font-size="13" font-family="monospace">person 0.91</text>')
    svg = (f'<svg xmlns="http://www.w3.org/2000/svg" width="320" height="240">'
           f'<rect width="320" height="240" fill="#1b2230"/><rect y="170" width="320" height="70" fill="#262f40"/>'
           f'<rect x="20" y="110" width="60" height="60" fill="#3a4558"/><rect x="250" y="120" width="50" height="50" fill="#3a4558"/>'
           f'{person}<text x="8" y="16" fill="#9fb3c8" font-size="12" font-family="monospace">DOG {dog} CAM  t={t:05.1f}s  {state}</text>'
           f'</svg>')
    return "data:image/svg+xml;base64," + base64.b64encode(svg.encode()).decode()


# ---------------------------------------------------------------- 메인 루프


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=9000)
    ap.add_argument("--speed", type=float, default=1.0, help="시간 배속, 예: 3 = 3배 빠르게")
    args = ap.parse_args()

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    send = lambda msg: sock.sendto(json.dumps(msg).encode(), (args.host, args.port))
    print(f"[sim] udp://{args.host}:{args.port} 로 전송 중, 배속 x{args.speed}")

    dt = 0.1
    t = 0.0
    tick = 0
    dogs = {n: Dog(n) for n in "ABC"}
    last_state, next_event = None, 0
    while True:
        intruder = lerp_path(INTRUDER_PATH, t)
        update_dogs(dogs, t, intruder or (0, 0), dt)
        state = state_at(t)

        if state != last_state:
            send({"type": "mission", "from": "pi", "state": state, "scenario_t": round(t, 1)})
            last_state = state
        while next_event < len(EVENTS) and t >= EVENTS[next_event][0]:
            _, level, text = EVENTS[next_event]
            send({"type": "event", "from": "pi", "level": level, "text": text})
            next_event += 1

        for d in dogs.values():  # 10 Hz
            front = 400.0
            for o in ARENA["obstacles"]:
                front = min(front, max(2.0, (math.hypot(o["x"] - d.x, o["y"] - d.y) - 0.3) * 100))
            send({"type": "dog", "from": d.name, "x": round(d.x, 3), "y": round(d.y, 3),
                  "heading_deg": round(d.heading, 1), "state": d.role, "battery_v": round(d.battery, 2),
                  "front_cm": round(front), "tilt": "ok", "rssi": -55 - random.randint(0, 10)})

        if tick % 2 == 0:  # 5 Hz
            bodies = [((d.x, d.y), 0.12) for d in dogs.values()]
            if intruder and 0 <= intruder[0] <= W:
                bodies.append((intruder, 0.2))
            send({"type": "lidar", "from": "pi", "origin": list(LIDAR), "points": lidar_scan(bodies)})
            if intruder and 44 <= t < 145:
                send({"type": "intruder", "from": "pi", "x": round(intruder[0], 3), "y": round(intruder[1], 3),
                      "confidence": 0.6 if t < 48 else 0.95})

        if tick % 10 == 0:  # 1 Hz
            alert = 42 <= t < 44
            doa = math.degrees(math.atan2(1.9 - LIDAR[1], 0.0 - LIDAR[0])) if alert else random.uniform(0, 360)
            send({"type": "sound", "from": "pi", "label": "glass_break" if alert else "ambient",
                  "doa_deg": round(doa, 1), "level_db": round(78 if alert else 38 + random.random() * 6, 1),
                  "confidence": 0.88 if alert else 0.2, "alert": alert})
            visible = intruder is not None and 46 <= t < 145
            send({"type": "camera", "from": "A", "image": camera_svg(t, "A", visible, state),
                  "evidence": visible and tick % 50 == 0, "note": state.lower()})

        tick += 1
        t = (t + dt) % CYCLE
        if t < dt:
            dogs = {n: Dog(n) for n in "ABC"}
            last_state, next_event = None, 0
        time.sleep(dt / args.speed)


if __name__ == "__main__":
    main()
