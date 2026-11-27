"""frontier 자동 탐사 시뮬레이터 (#14): 가짜 정찰견 A 가 스스로 지도를 채우는지 확인한다.

실물 없이 frontier.py (1·2단계)를 돌려 본다.
- 가짜 세상(방 3개 + 상자, 또는 저장한 SLAM 지도)에 가짜 C1 (360 빔, --max m)을 쏴서 지도를 쌓는다.
  지도 값은 BreezySLAM 과 같음 (0 = 벽, 127 = 안 봄, 255 = 빈 칸). 위치는 정확하다고 봄 (SLAM 오차 없음)
- 실물 계획처럼 "멈춤 → 스캔 → 이동": 경로를 따라 --step m 걷고 --pause 초 서서 다시 계획
- 목표에 가도 frontier 가 안 사라지거나, 막혀서 못 가면 그 자리는 제외 목록에 넣고 다른 곳으로
- 갈 수 있는 frontier 가 없으면 "탐사 완료"

실행:
    python brain/frontier_sim.py                              # 창으로 보기 (q 끝내기)
    python brain/frontier_sim.py --hub 127.0.0.1 --speed 3    # 대시보드에도 (지도, Dog A 위치, LiDAR, 경로·frontier)
    python brain/frontier_sim.py --world maps/slam_....npy    # 저장한 실제 지도를 세상으로 (빈 칸이 아닌 곳 = 벽)
    python brain/frontier_sim.py --no-window                  # 창 없이 최대 속도로 돌리고 결과 그림만 저장
"""

from __future__ import annotations

import argparse
import json
import math
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import cv2
import numpy as np

from frontier import OCC_MAX, UNKNOWN, Planner, draw, korean_font

ROOT = Path(__file__).resolve().parent.parent
MAP_M, MAP_PX = 12.0, 600          # lidar_slam.py 기본값과 같음 (칸 2 cm, 시작점 = 가운데)
CELL = MAP_M / MAP_PX
START = (MAP_M / 2, MAP_M / 2)

# 기본 세상: 시작점(0, 0)에서 +x 를 보고 시작. 6.0 × 4.8 m, 방 3개(문 1 m, 통로 1.3 m), 상자 4개
WALL = 0.06
ROOMS = [
    (-0.8, -2.4, 5.2, -2.4), (-0.8, 2.4, 5.2, 2.4), (-0.8, -2.4, -0.8, 2.4), (5.2, -2.4, 5.2, 2.4),   # 바깥 벽
    (1.8, -2.4, 1.8, -0.5), (1.8, 0.5, 1.8, 2.4),     # 첫 방 ↔ 오른쪽 (문 y -0.5 ~ 0.5)
    (3.2, 0.2, 5.2, 0.2),                             # 오른쪽을 위·아래 방으로 (통로 x 1.8 ~ 3.2)
]
BOXES = [(0.6, 1.2, 1.1, 1.7), (-0.6, -1.9, -0.1, -1.4), (3.6, -1.6, 4.2, -1.0), (4.3, 1.0, 4.8, 1.5)]


def default_world() -> np.ndarray:
    w = np.zeros((MAP_PX, MAP_PX), np.uint8)
    px = lambda v, o: int(round((v + o) / CELL))
    for x0, y0, x1, y1 in ROOMS:
        cv2.line(w, (px(x0, START[0]), px(y0, START[1])), (px(x1, START[0]), px(y1, START[1])), 1,
                 max(1, round(WALL / CELL)))
    for x0, y0, x1, y1 in BOXES:
        cv2.rectangle(w, (px(x0, START[0]), px(y0, START[1])), (px(x1, START[0]), px(y1, START[1])), 1, -1)
    return w.astype(bool)


def load_world(path: str) -> np.ndarray:
    g = np.load(path)
    if g.shape != (MAP_PX, MAP_PX):
        sys.exit(f"{path}: {MAP_PX}×{MAP_PX} 지도만 됨 (lidar_slam.py 기본값)")
    # 벽 = 어두운 칸 + 안 본 칸(127 근처). 빈 공간 안의 작은 점(빔 사이 잡음)은 지움
    wall = ((g < OCC_MAX) | (np.abs(g.astype(np.int16) - UNKNOWN) <= 10)).astype(np.uint8)
    return cv2.morphologyEx(wall, cv2.MORPH_OPEN, np.ones((3, 3), np.uint8)).astype(bool)


class Lidar:
    """가짜 C1: 빔 n 개를 반 칸 간격으로 따라가며 처음 벽을 만나는 거리."""

    def __init__(self, world: np.ndarray, n: int = 360, max_m: float = 8.0):
        self.world, self.n, self.max_m = world, n, max_m
        self.d = np.arange(1, int(max_m / (CELL / 2)) + 1) * (CELL / 2)

    def scan(self, x, y, th):
        a = th + np.radians(np.arange(self.n) * 360 / self.n)
        xs, ys = x + np.outer(np.cos(a), self.d), y + np.outer(np.sin(a), self.d)
        c, r = (xs / CELL).astype(int), (ys / CELL).astype(int)
        out = (c < 0) | (r < 0) | (c >= MAP_PX) | (r >= MAP_PX)
        c, r = np.clip(c, 0, MAP_PX - 1), np.clip(r, 0, MAP_PX - 1)
        hit = self.world[r, c] | out
        any_hit = hit.any(1)
        first = np.where(any_hit, hit.argmax(1), len(self.d))
        return a, r, c, first, any_hit & ~out[np.arange(self.n), np.minimum(first, len(self.d) - 1)]

    def update(self, known: np.ndarray, x, y, th):
        """known(0/127/255)에 스캔을 쌓고, (빔 각도 rad, 거리 m 또는 nan) 을 돌려준다."""
        a, r, c, first, wall = self.scan(x, y, th)
        free = np.arange(len(self.d))[None, :] < first[:, None]
        fr, fc = r[free], c[free]
        keep = known[fr, fc] != 0                   # 벽은 지우지 않음 (스치는 빔 때문에 벽이 깎이지 않게)
        known[fr[keep], fc[keep]] = 255
        i = np.nonzero(wall)[0]
        known[r[i, first[i]], c[i, first[i]]] = 0
        dist = np.where(wall, self.d[np.minimum(first, len(self.d) - 1)], np.nan)
        return a, dist


class Robot:
    """MechDog 흉내: 제자리 회전 또는 걸으면서 살짝 회전. 몸이 벽에 닿는 곳으로는 안 감."""

    def __init__(self, world, x, y, th, v=0.20, w_deg=60.0, body=0.10):
        self.x, self.y, self.th = x, y, th
        self.world = world
        self.v, self.w = v, math.radians(w_deg)
        r = max(1, round(body / CELL))
        self.solid = cv2.dilate(world.astype(np.uint8), cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * r + 1,) * 2)).astype(bool)
        self.odo = 0.0

    def step_to(self, tx, ty, dt) -> bool:
        """(tx, ty) 쪽으로 dt 만큼. 몸이 막히면 False."""
        e = (math.atan2(ty - self.y, tx - self.x) - self.th + math.pi) % (2 * math.pi) - math.pi
        if abs(e) > math.radians(25):               # 많이 틀어졌으면 제자리 회전
            self.th += math.copysign(min(abs(e), self.w * dt), e)
            return True
        self.th += max(-self.w * dt, min(self.w * dt, 1.5 * e * dt))
        s = min(self.v * dt, math.hypot(tx - self.x, ty - self.y))
        nx, ny = self.x + s * math.cos(self.th), self.y + s * math.sin(self.th)
        # 몸이 벽에 닿는 곳으로는 못 감. 이미 닿아 있으면(벽 옆에서 시작) 실제 벽만 막음
        here = self.solid[int(self.y / CELL), int(self.x / CELL)]
        if (self.world if here else self.solid)[int(ny / CELL), int(nx / CELL)]:
            return False
        self.x, self.y = nx, ny
        self.odo += s
        return True


class Hub:
    """대시보드로: map(HTTP, lidar_slam 과 같은 형식), lidar·dog A·explore(UDP)."""

    def __init__(self, args):
        from lidar_slam import Arena, Sender, map_message   # BreezySLAM 이 있어야 import 됨 (lidar_slam 과 같은 환경)
        origin = [3.0, 0.15]
        try:
            origin = json.loads((ROOT / "web" / "arena.json").read_text())["lidar_origin"]
        except Exception:
            pass
        self.arena = Arena(origin, args.mount, MAP_M)
        self.sender = Sender(args.hub, args.http_port, args.udp_port, args.token)
        self.map_message = map_message
        self.mapper = SimpleNamespace(map_m=MAP_M, map_px=MAP_PX, updates=0)
        self.t = {}

    def due(self, k, every):
        now = time.time()
        if now - self.t.get(k, 0) >= every:
            self.t[k] = now
            return True
        return False

    def pose(self, rb):
        return self.arena.pose((rb.x * 1000, rb.y * 1000, math.degrees(rb.th)))

    def send(self, known, rb, scan, plan, state, updates):
        now = round(time.time(), 3)
        x, y, hd = self.pose(rb)
        if self.due("scan", 0.2):
            a, dist = scan
            pts = [[round((self.arena.mount + math.degrees(ai)) % 360, 1), round(float(di), 3)]
                   for ai, di in zip(a[::2], dist[::2]) if not np.isnan(di)]
            self.sender.udp_send({"type": "lidar", "from": "pi", "ts": now, "origin": [round(x, 3), round(y, 3)],
                                  "heading_deg": round(hd, 1), "points": pts})
            self.sender.udp_send({"type": "dog", "from": "A", "ts": now, "x": round(x, 3), "y": round(y, 3),
                                  "heading_deg": round(hd, 1)})
        if self.due("explore", 0.3):
            P = lambda p: [round(v, 3) for v in self.arena.point(*p)]
            fronts = []
            for fr in plan.frontiers if plan else []:
                fronts += [P(c) for c in fr.cells[::2]]
            self.sender.udp_send({"type": "explore", "from": "A", "ts": now, "state": state,
                                  "goal": P((plan.goal.x, plan.goal.y)) if plan and plan.goal else None,
                                  "path": [P(p) for p in plan.path] if plan else [],
                                  "frontier": fronts[:600], "n": len(plan.frontiers) if plan else 0})
        if self.due("map", 1.0):
            self.mapper.updates = updates
            msg = self.map_message(known, [(rb.x * 1000, rb.y * 1000, math.degrees(rb.th))], self.mapper,
                                   self.arena, 0.04)
            if msg:
                self.sender.http_send(msg)

    def event(self, text, level="info"):
        self.sender.udp_send({"type": "event", "from": "A", "ts": round(time.time(), 3), "level": level, "text": text})


def coverage(known, world, reach):
    return float(((known == 255) & reach).sum() / max(reach.sum(), 1))


def main() -> None:
    ap = argparse.ArgumentParser(description="frontier 자동 탐사 시뮬레이터 (#14)")
    ap.add_argument("--world", help="세상으로 쓸 저장 지도 .npy (기본: 방 3개 + 상자)")
    ap.add_argument("--speed", type=float, default=1.0, help="배속 (--no-window 이고 --hub 가 없으면 최대 속도)")
    ap.add_argument("--seconds", type=float, default=900, help="시뮬레이션 시간 상한 (초)")
    ap.add_argument("--max", type=float, default=8.0, help="LiDAR 최대 거리 m")
    ap.add_argument("--v", type=float, default=0.057, help="걷는 속도 m/s (실측 11-27: teleop 속도 90, 0.5 m / 8.8 s, 짐 300~400 g 과 같음)")
    ap.add_argument("--w", type=float, default=13.0, help="제자리 회전 속도 °/s (실측 11-27: 360° / 28 s)")
    ap.add_argument("--step", type=float, default=0.6, help="한 번에 걷는 거리 m (그다음 멈춰서 다시 계획)")
    ap.add_argument("--pause", type=float, default=0.8, help="멈춰서 스캔하는 시간 (초)")
    ap.add_argument("--radius", type=float, default=0.18, help="계획할 때 몸 반지름 m")
    ap.add_argument("--gain", type=float, default=1.0)
    ap.add_argument("--no-window", action="store_true")
    ap.add_argument("--out", default=str(ROOT / "maps"), help="결과 그림 폴더")
    g = ap.add_argument_group("대시보드 (--hub 를 주면 보냄)")
    g.add_argument("--hub")
    g.add_argument("--http-port", type=int, default=8000)
    g.add_argument("--udp-port", type=int, default=9000)
    g.add_argument("--token", default="")
    g.add_argument("--mount", type=float, default=90.0, help="시작 방향의 아레나 각도 (lidar_slam --mount 와 같음)")
    a = ap.parse_args()

    world = load_world(a.world) if a.world else default_world()
    known = np.full((MAP_PX, MAP_PX), UNKNOWN, np.uint8)
    lidar = Lidar(world, max_m=a.max)
    rb = Robot(world, *START, 0.0, v=a.v, w_deg=a.w)
    planner = Planner(CELL, radius=a.radius, gain=a.gain)
    hub = Hub(a) if a.hub else None
    # 커버리지 분모: 시작점과 이어진 실제 빈 칸 전체
    _, lab = cv2.connectedComponents((~world).astype(np.uint8), connectivity=4)
    reach = lab == lab[int(START[1] / CELL), int(START[0] / CELL)]

    plt = None
    if not a.no_window:
        import matplotlib.pyplot as plt
        korean_font(plt)
        plt.ion()
        fig, ax = plt.subplots(figsize=(8, 7))
        fig.canvas.mpl_connect("key_press_event", lambda e: setattr(a, "quit", e.key == "q"))
    realtime = not a.no_window or a.hub
    rows, cols = np.nonzero(world if not a.world else reach)
    view = (cols.min() * CELL - 0.3, rows.min() * CELL - 0.3, cols.max() * CELL + 0.3, rows.max() * CELL + 0.3)

    dt, t = 0.1, 0.0
    state, plan, pause_until, walked = "plan", None, 0.0, 0.0
    blacklist, goals, last_goal, goal_t0, give_up = [], 0, None, 0.0, 60.0
    scan = lidar.update(known, rb.x, rb.y, rb.th)
    updates, last_draw, wall0 = 1, 0.0, time.time()
    if hub:
        hub.event("Dog A: frontier 자동 탐사 시작 (시뮬레이션)")
    print("탐사 시작. 창에서 q = 끝내기" if plt else "탐사 시작")

    while t < a.seconds and not getattr(a, "quit", False):
        if state == "plan":
            plan = planner.plan(known, rb.x, rb.y, blacklist)
            if plan.goal is None:
                state = "done"
            else:
                g_ = (plan.goal.x, plan.goal.y)
                # 같은 목표를 또 골랐는데 거의 안 움직였으면(가도 안 보이는 곳) 제외
                if last_goal and math.dist(g_, last_goal) < 0.15 and math.dist(g_, (rb.x, rb.y)) < 0.25:
                    blacklist.append(g_)
                    continue
                if not last_goal or math.dist(g_, last_goal) > 0.15:
                    goals += 1
                    goal_t0 = t
                    # 포기 시간: 경로를 실측 속도로 걷는 시간의 2배 + 회전·멈춤 여유 (느린 실물에서 먼 목표를 버리지 않게)
                    give_up = max(60.0, 2 * plan.goal.cost_m / a.v + 60)
                last_goal = g_
                new = goal_t0 == t
                state, walked, wp = "walk", 0.0, 1
                if new:
                    print(f"[{t:5.1f}s] 목표 {goals}: ({g_[0] - START[0]:+.2f}, {g_[1] - START[1]:+.2f}) "
                          f"경로 {plan.goal.cost_m:.2f} m, frontier {len(plan.frontiers)}개, 계획 {plan.ms:.0f} ms, "
                          f"지도 {coverage(known, world, reach) * 100:.0f}%")
        elif state == "walk":
            tx, ty = plan.path[wp]
            o0 = rb.odo
            if not rb.step_to(tx, ty, dt):
                print(f"[{t:5.1f}s] 막힘 → 이 목표 제외")
                blacklist.append(last_goal)
                state, pause_until = "pause", t + a.pause
            walked += rb.odo - o0
            if math.dist((rb.x, rb.y), (tx, ty)) < 0.05:
                wp += 1
            if wp >= len(plan.path) or walked >= a.step:
                state, pause_until = "pause", t + a.pause
            elif t - goal_t0 > give_up:
                print(f"[{t:5.1f}s] {give_up:.0f}초 넘게 못 감 → 이 목표 제외")
                blacklist.append(last_goal)
                state, pause_until = "pause", t + a.pause
        elif state == "pause":
            if t >= pause_until:
                state = "plan"
        elif state == "done":
            break

        scan = lidar.update(known, rb.x, rb.y, rb.th)
        updates += 1
        t += dt
        if hub:
            hub.send(known, rb, scan, plan, state, updates)
        if plt and time.time() - last_draw > 0.15:
            last_draw = time.time()
            draw(ax, known, plan or SimpleNamespace(frontiers=[], goal=None, path=[]), (rb.x, rb.y, rb.th), CELL, view,
                 f"{t:5.1f}s · {STATE_KO[state]} · 목표 {goals}개 · 지도 {coverage(known, world, reach) * 100:.0f}%")
            ax.contour(np.arange(MAP_PX) * CELL + CELL / 2, np.arange(MAP_PX) * CELL + CELL / 2, world, [0.5],
                       colors="#d94f4f", linewidths=0.4, alpha=0.35)
            plt.pause(0.001)
        if realtime:
            lag = wall0 + t / a.speed - time.time()
            if lag > 0:
                time.sleep(lag)

    cov = coverage(known, world, reach)
    done = state == "done"
    msg = (f"탐사 완료: {t:.0f}초, 목표 {goals}개, 걸은 거리 {rb.odo:.1f} m, 지도 {cov * 100:.0f}%"
           if done else f"시간 끝 ({t:.0f}초): 목표 {goals}개, 걸은 거리 {rb.odo:.1f} m, 지도 {cov * 100:.0f}%")
    print(msg)
    if hub:
        hub.event(f"Dog A: {msg}")
        hub.send(known, rb, scan, plan, "done" if done else state, updates)
        time.sleep(0.3)

    import matplotlib
    if not plt:
        matplotlib.use("Agg")
    import matplotlib.pyplot as plt2
    korean_font(plt2)
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    f2, ax2 = plt2.subplots(figsize=(8, 7))
    draw(ax2, known, plan or SimpleNamespace(frontiers=[], goal=None, path=[]), (rb.x, rb.y, rb.th), CELL, view, msg)
    ax2.contour(np.arange(MAP_PX) * CELL + CELL / 2, np.arange(MAP_PX) * CELL + CELL / 2, world, [0.5],
                colors="#d94f4f", linewidths=0.4, alpha=0.35)
    path = out / f"frontier_sim_{time.strftime('%Y%m%d_%H%M%S')}.png"
    f2.savefig(path, dpi=110, bbox_inches="tight")
    print("그림:", path)
    if plt and not getattr(a, "quit", False):
        plt.ioff()
        plt.show()


STATE_KO = {"plan": "계획", "walk": "이동", "pause": "멈춤·스캔", "done": "탐사 완료"}

if __name__ == "__main__":
    main()
