"""frontier 탐사 3단계: C1 SLAM 위치로 계획하고 실물 로봇견에 move 명령을 보낸다 (#14).

C1 을 로봇견 등에 얹고(정면 = 개 머리 방향, 케이블 뒤로) USB 로 이 컴퓨터(Mac 또는 Pi)에 꽂는다.
lidar_slam.py 의 SLAM 을 그대로 돌리면서, frontier.py 로 목표·경로를 고르고 따라 걷는다.

    python brain/frontier_drive.py --dog 192.168.0.18 --hub 127.0.0.1 --name B   # 오늘 개는 secrets.py 이름이 B
    python brain/frontier_drive.py --hub 127.0.0.1                                # --dog 없음: 명령은 안 보내고 계산만 (C1 손에 들고 확인)

움직이는 법 (시뮬레이터 frontier_sim.py 와 같음):
    대기 → (g) → 멈춰서 스캔 --settle 초 → 계획 → 경로 따라 --step m 걷기 → 멈춰서 스캔 → 다시 계획 … → 갈 곳 없으면 완료
    - 목표 방향과 25° 넘게 틀어져 있으면 제자리 회전, 아니면 걸으면서 살짝 회전
    - --stuck-sec 동안 5 cm 도 못 가면(벽·초음파 정지) 그 목표는 제외하고 다시 계획
    - SLAM 위치가 1초 넘게 안 바뀌면 멈춤
    - 제자리 회전을 4초 했는데 목표 방향과 오히려 멀어지면 멈춤 → 회전 부호가 반대 (--turn-sign 1 로)

키 (지도 창 또는 --no-window 면 터미널):
    g 시작·다시 시작   스페이스 멈춤 (그동안 teleop.py 로 손으로 옮겨도 됨)   s 지도 저장   q 끝내기
끝낼 때 정지 명령을 보내고 지도를 maps/ 에 저장한다. 로봇견의 dog_cmd.py 도 명령이 1초 끊기면 스스로 멈춘다.
"""

from __future__ import annotations

import argparse
import json
import math
import socket
import sys
import threading
import time
from pathlib import Path

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))
from frontier import Planner, korean_font  # noqa: E402

CMD_PORT = 9100
STATE_KO = {"hold": "대기 (g = 시작)", "settle": "멈춰서 스캔", "turn": "제자리 회전", "walk": "걷는 중",
            "done": "탐사 완료"}
EXPLORE_STATE = {"hold": "pause", "settle": "pause", "turn": "walk", "walk": "walk", "done": "done"}


def wrap(a: float) -> float:
    return (a + math.pi) % (2 * math.pi) - math.pi


class Driver:
    """위치를 받아 (전진 값, 왼쪽 회전 값)을 돌려주는 상태 기계. 통신·SLAM 과 분리해서 시뮬레이터로도 시험한다.

    전진 값·회전 값은 dog_cmd.py 단위 (teleop 속도 90, 회전 40). 회전은 + = 왼쪽(반시계).
    """

    def __init__(self, planner: Planner, a, log=print):
        self.p, self.a, self.log = planner, a, log
        self.state, self.until = "hold", 0.0
        self.plan, self.wp, self.goal = None, 1, None
        self.blacklist: list[tuple[float, float]] = []
        self.goals, self.goal_t0, self.give_up = 0, 0.0, 60.0
        self.step_from = None
        self.trail: list[tuple[float, float, float]] = []      # (시각, x, y) 걷는 동안
        self.turn_t0, self.turn_e0 = 0.0, 0.0
        self.note = ""

    # 사람 키
    def start(self, now):
        if self.state in ("hold", "done"):
            self.state, self.until = "settle", now + self.a.settle
            self.log("시작: 멈춰서 스캔 후 계획")

    def pause(self):
        if self.state != "hold":
            self.state = "hold"
            self.log("멈춤 (g = 다시 시작)")

    def _settle(self, now, why=""):
        if why:
            self.log(why)
        self.state, self.until = "settle", now + self.a.settle

    def _drop_goal(self, now, why):
        if self.goal:
            self.blacklist.append(self.goal)
        self._settle(now, why + " → 이 목표 제외")

    def step(self, now: float, x: float, y: float, th: float, grid_fn) -> tuple[int, int]:
        """th = 개 머리 방향 rad (지도 기준 반시계). 돌려주는 값 (v, 왼쪽 회전)."""
        a = self.a
        if self.state in ("hold", "done"):
            return 0, 0
        if self.state == "settle":
            if now < self.until:
                return 0, 0
            plan = self.p.plan(grid_fn(), x, y, self.blacklist)
            self.plan = plan
            if plan.goal is None:
                self.state, self.note = "done", plan.note
                self.log(f"탐사 완료: {plan.note} (목표 {self.goals}개)")
                return 0, 0
            g = (plan.goal.x, plan.goal.y)
            if self.goal and math.dist(g, self.goal) < 0.15 and math.dist(g, (x, y)) < 0.25:
                self._drop_goal(now, "같은 목표인데 가도 안 보임")   # 목표에 와 있는데 frontier 가 안 사라짐
                return 0, 0
            if not self.goal or math.dist(g, self.goal) > 0.15:
                self.goals += 1
                self.goal_t0 = now
                self.give_up = max(60.0, 2 * plan.goal.cost_m / a.v_ms + 60)
                self.log(f"목표 {self.goals}: 지도 ({g[0]:.2f}, {g[1]:.2f}) 경로 {plan.goal.cost_m:.2f} m, "
                         f"frontier {len(plan.frontiers)}개, 계획 {plan.ms:.0f} ms")
            self.goal = g
            self.wp, self.step_from, self.trail = 1, (x, y), []
            self.state = "walk"

        # turn / walk
        if now - self.goal_t0 > self.give_up:
            self._drop_goal(now, f"{self.give_up:.0f}초 넘게 못 감")
            return 0, 0
        path = self.plan.path
        while self.wp < len(path) and math.dist((x, y), path[self.wp]) < a.reach:
            self.wp += 1
        if self.wp >= len(path):
            self._settle(now, "목표 도착")
            return 0, 0
        if math.dist((x, y), self.step_from) >= a.step:
            self._settle(now)
            return 0, 0
        tx, ty = path[self.wp]
        e = wrap(math.atan2(ty - y, tx - x) - th)
        if abs(e) > math.radians(25):
            if self.state != "turn":
                self.state, self.turn_t0, self.turn_e0 = "turn", now, abs(e)
            elif now - self.turn_t0 > 4 and abs(e) > self.turn_e0 + math.radians(15):
                self.state = "hold"
                self.log("제자리 회전 중 목표와 더 멀어짐 → 멈춤. 회전 부호가 반대일 수 있음: --turn-sign 을 바꿔 실행")
                return 0, 0
            self.trail = []
            return 0, int(math.copysign(a.turn, e))
        if self.state != "walk":
            self.state = "walk"
        self.trail.append((now, x, y))
        while self.trail and now - self.trail[0][0] > a.stuck_sec:
            old = self.trail.pop(0)
            if math.dist(old[1:], (x, y)) < 0.05:
                self._drop_goal(now, f"{a.stuck_sec:.0f}초 동안 5 cm 도 못 감 (막힘·초음파 정지?)")
                return 0, 0
        left = max(-a.walk_turn, min(a.walk_turn, round(a.k_turn * math.degrees(e))))
        return a.speed, int(left)


class Dog:
    def __init__(self, ip: str | None, turn_sign: int):
        self.ip, self.sign = ip, turn_sign
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.last = None

    def move(self, v: int, left: int) -> None:
        # teleop.py 와 같은 부호: 왼쪽 키 a 는 turn = -turn_sign × 40
        t = -self.sign * left
        if self.ip:
            self.sock.sendto(json.dumps({"cmd": "move", "v": int(v), "turn": int(t)}).encode(), (self.ip, CMD_PORT))
        self.last = (int(v), int(t))

    def stop(self) -> None:
        for _ in range(3):
            self.move(0, 0)
            time.sleep(0.05)


# ── 화면 ───────────────────────────────────────────────────────────

def render(grid, pose_xy_th, drv: Driver, cell: float, title: str, size: int = 640) -> np.ndarray:
    seen = np.argwhere(np.abs(grid.astype(int) - 127) > 10)
    x, y, th = pose_xy_th
    if len(seen):
        (r0, c0), (r1, c1) = seen.min(0), seen.max(0)
    else:
        r0 = r1 = int(y / cell)
        c0 = c1 = int(x / cell)
    pad = 25
    r0, c0 = max(r0 - pad, 0), max(c0 - pad, 0)
    r1, c1 = min(r1 + pad, grid.shape[0] - 1), min(c1 + pad, grid.shape[1] - 1)
    sub = grid[r0:r1 + 1, c0:c1 + 1]
    k = size / max(sub.shape)
    img = cv2.cvtColor(cv2.resize(sub[::-1], None, fx=k, fy=k, interpolation=cv2.INTER_NEAREST), cv2.COLOR_GRAY2BGR)
    H = img.shape[0]

    def P(mx, my):
        return int((mx / cell - c0) * k), int(H - (my / cell - r0) * k)

    plan = drv.plan
    if plan:
        for fr in plan.frontiers:
            for cx, cy in fr.cells[::2]:
                cv2.circle(img, P(cx, cy), 2, (71, 181, 255), -1)
        if plan.path and drv.state in ("turn", "walk", "settle"):
            pts = [P(*p) for p in plan.path]
            cv2.polylines(img, [np.array(pts)], False, (255, 155, 47), 2)
            cv2.circle(img, pts[-1], 9, (0, 140, 255), 2)
    for bx, by in drv.blacklist:
        cv2.drawMarker(img, P(bx, by), (60, 60, 220), cv2.MARKER_TILTED_CROSS, 12, 2)
    p0 = P(x, y)
    p1 = (int(p0[0] + 22 * math.cos(th)), int(p0[1] - 22 * math.sin(th)))
    cv2.arrowedLine(img, p0, p1, (60, 60, 240), 3, tipLength=0.4)
    bar = np.full((54, img.shape[1], 3), 245, np.uint8)
    cv2.putText(bar, title, (8, 22), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (0, 0, 0), 1)
    cv2.putText(bar, "g start  space pause  s save  q quit", (8, 45), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (90, 90, 90), 1)
    return np.vstack([bar, img])


STATE_EN = {"hold": "HOLD (g)", "settle": "SCAN", "turn": "TURN", "walk": "WALK", "done": "DONE"}


def main() -> None:
    from lidar_bridge import BAUD_C1, Keys, RPLidar, find_port, stop_on_sigterm
    from lidar_slam import Arena, Mapper, Sender, push, save

    import matplotlib
    matplotlib.use("Agg")   # 화면은 OpenCV 창. matplotlib 은 끝날 때 지도 그림 저장에만
    import matplotlib.pyplot as plt
    korean_font(plt)
    stop_on_sigterm()
    default_origin = [3.0, 0.15]
    try:
        default_origin = json.loads((ROOT / "web" / "arena.json").read_text())["lidar_origin"]
    except Exception:
        pass

    ap = argparse.ArgumentParser(description="frontier 탐사 3단계: 실물 로봇견 (#14)")
    ap.add_argument("--dog", help="로봇견 IP (없으면 명령은 안 보내고 계산·표시만)")
    ap.add_argument("--name", default="A", help="대시보드 로봇견 이름 (로봇견 secrets.py 의 NAME 과 같게)")
    ap.add_argument("--turn-sign", type=int, choices=(1, -1), default=-1, help="teleop.py 와 같은 값")
    ap.add_argument("--c1-yaw", type=float, default=0.0, help="C1 정면이 개 머리에서 반시계로 몇 ° 돌아가 있는지")
    g = ap.add_argument_group("움직임 (실측 11-27: 속도 90 → 0.057 m/s, 회전 40 → 13°/s)")
    g.add_argument("--speed", type=int, default=90, help="전진 값 (teleop 1/2/3 = 60/90/120)")
    g.add_argument("--turn", type=int, default=40, help="제자리 회전 값")
    g.add_argument("--walk-turn", type=int, default=20, help="걸으면서 쓰는 회전 값 최대")
    g.add_argument("--k-turn", type=float, default=1.0, help="걸을 때 방향 오차 1° 당 회전 값")
    g.add_argument("--v-ms", type=float, default=0.057, help="--speed 일 때 실제 속도 m/s (포기 시간 계산용)")
    g.add_argument("--step", type=float, default=0.4, help="한 번에 걷는 거리 m (그다음 멈춰서 다시 계획)")
    g.add_argument("--settle", type=float, default=1.5, help="멈춰서 스캔하는 시간 초 (걷는 흔들림이 SLAM 에 덜 들어가게)")
    g.add_argument("--reach", type=float, default=0.12, help="waypoint 에 이만큼 가까우면 도착 m")
    g.add_argument("--stuck-sec", type=float, default=12.0)
    g = ap.add_argument_group("계획 (frontier.py)")
    g.add_argument("--radius", type=float, default=0.15, help="몸 반지름 m (벽을 이만큼 부풀림)")
    g.add_argument("--min-size", type=float, default=0.20)
    g.add_argument("--gain", type=float, default=1.0)
    g = ap.add_argument_group("C1·SLAM·대시보드 (lidar_slam.py 와 같음)")
    g.add_argument("--port")
    g.add_argument("--baud", type=int, default=BAUD_C1)
    g.add_argument("--map-m", type=float, default=12.0)
    g.add_argument("--map-px", type=int, default=600)
    g.add_argument("--max", type=float, default=8.0)
    g.add_argument("--flip", action="store_true")
    g.add_argument("--hub", help="허브 IP: 지도·위치·explore 를 대시보드로")
    g.add_argument("--http-port", type=int, default=8000)
    g.add_argument("--udp-port", type=int, default=9000)
    g.add_argument("--token", default="")
    g.add_argument("--origin", type=float, nargs=2, default=default_origin, metavar=("X", "Y"))
    g.add_argument("--mount", type=float, default=90.0)
    g.add_argument("--send-res", type=float, default=0.04)
    g.add_argument("--no-window", action="store_true", help="지도 창 없이 (키는 터미널에서)")
    g.add_argument("--out", default=str(ROOT / "maps"))
    a = ap.parse_args()
    a.dog_ip, a.dog = a.dog, (a.name if a.hub else None)   # lidar_slam.push 는 a.dog 를 대시보드 이름으로 씀

    port = a.port or find_port()
    if not port:
        sys.exit("LiDAR 포트를 못 찾음. --port 로 지정하세요.")
    lidar = RPLidar(port, a.baud)
    info, (status, _) = lidar.info(), lidar.health()
    print(f"[{port}] C1 model={info['model']} 상태={status}")
    mapper = Mapper(lidar, a.map_px, a.map_m, a.max, a.flip)
    arena = Arena(a.origin, a.mount, a.map_m)
    sender = Sender(a.hub, a.http_port, a.udp_port, a.token) if a.hub else None
    cell = a.map_m / a.map_px
    planner = Planner(cell_m=cell, radius=a.radius, min_size=a.min_size, gain=a.gain)

    def log(text, level="info"):
        print(f"[{time.strftime('%H:%M:%S')}] {text}")
        if sender:
            sender.udp_send({"type": "event", "from": a.name, "ts": round(time.time(), 3), "level": level,
                             "text": f"Dog {a.name} 탐사: {text}"})

    drv = Driver(planner, a, log)
    dog = Dog(a.dog_ip, a.turn_sign)
    print(f"로봇견: {a.dog_ip or '(없음: 명령 안 보냄, 계산만)'} · 이름 {a.name} · 속도 {a.speed} · 회전 {a.turn}")
    th_ = threading.Thread(target=mapper.run, daemon=True)
    th_.start()

    keys = Keys().__enter__() if a.no_window else None
    if not a.no_window:
        cv2.namedWindow("frontier_drive", cv2.WINDOW_AUTOSIZE)
    print("처음 몇 초 가만히 두세요 (SLAM 첫 지도). 준비되면 g = 시작, 스페이스 = 멈춤, s = 저장, q = 끝")

    grid = None
    last_upd, last_upd_t, moving, last_send, last_draw, last_snap, push_state = -1, time.time(), False, 0.0, 0.0, 0.0, {}
    try:
        while not mapper.error:
            now = time.time()
            with mapper.lock:
                pos = mapper.path[-1] if mapper.path else None
                upd = mapper.updates
            if upd != last_upd:
                last_upd, last_upd_t = upd, now
            if grid is None or now - last_snap > 0.3:
                grid, path = mapper.snapshot()
                last_snap = now
            pressed = keys.read() if keys else []
            if pos is None:
                time.sleep(0.1)
                continue
            x, y = pos[0] / 1000, pos[1] / 1000
            th = math.radians(pos[2] - a.c1_yaw)

            # 키
            if not keys:
                k = cv2.waitKey(1) & 0xFF
                pressed = [chr(k)] if k != 255 else []
            for k in pressed:
                if k == "q":
                    raise KeyboardInterrupt
                if k == "g":
                    drv.start(now)
                if k == " ":
                    drv.pause()
                    dog.stop()
                if k == "s":
                    print(f"저장: {save(grid, path, a.map_px, a.map_m, Path(a.out))}")

            stale = now - last_upd_t > 1.0
            if stale and drv.state in ("turn", "walk"):
                drv.state = "hold"
                log("SLAM 위치가 1초 넘게 안 바뀜 → 멈춤 (C1 연결 확인 후 g)", "warn")
            v, left = drv.step(now, x, y, th, lambda: mapper.snapshot()[0])
            if (v, left) != (0, 0):
                if now - last_send >= 0.1:
                    dog.move(v, left)
                    last_send = now
                moving = True
            elif moving:
                dog.stop()
                moving = False

            if sender:
                push(mapper, arena, sender, a, grid, path, push_state)
                if now - push_state.get("explore", 0) >= 0.3:
                    push_state["explore"] = now
                    P = lambda p: [round(c, 3) for c in arena.point(*p)]
                    pl = drv.plan
                    fronts = [P(c) for fr in (pl.frontiers if pl else []) for c in fr.cells[::2]]
                    sender.udp_send({"type": "explore", "from": a.name, "ts": round(now, 3),
                                     "state": EXPLORE_STATE[drv.state],
                                     "goal": P((pl.goal.x, pl.goal.y)) if pl and pl.goal and drv.state != "done" else None,
                                     "path": [P(p) for p in pl.path] if pl and drv.state in ("turn", "walk") else [],
                                     "frontier": fronts[:600], "n": len(pl.frontiers) if pl else 0})

            if not keys and now - last_draw > 0.3:
                last_draw = now
                cmd = f"v={dog.last[0]} turn={dog.last[1]}" if dog.last else "v=0"
                title = f"{STATE_EN[drv.state]} goals {drv.goals}  {cmd}" + ("  SLAM STALE" if stale else "")
                cv2.imshow("frontier_drive", render(grid, (x, y, th), drv, cell, title))
            elif keys and now - last_draw > 1.0:
                last_draw = now
                print(f"\r{STATE_KO[drv.state]} · 목표 {drv.goals}개 · 지도 x {x:.2f} y {y:.2f} "
                      f"방향 {math.degrees(th) % 360:5.1f}°   ", end="", flush=True)
            time.sleep(0.05)
    except KeyboardInterrupt:
        pass
    finally:
        dog.stop()
        print("\n정지 명령 전송")
        if keys:
            keys.__exit__(None, None, None)
        mapper.stop.set()
        th_.join(timeout=2)
        if mapper.error:
            print(f"LiDAR/SLAM 오류: {mapper.error}")
        grid, path = mapper.snapshot()
        lidar.close()
        if path:
            print(f"저장: {save(grid, path, a.map_px, a.map_m, Path(a.out))}")
        cv2.destroyAllWindows()


if __name__ == "__main__":
    main()
