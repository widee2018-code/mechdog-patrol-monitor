"""frontier 탐사 3단계: C1 SLAM 위치로 계획하고 실물 로봇견에 move 명령을 보낸다 (#14).

C1 을 로봇견 등에 얹고(정면 = 개 머리 방향, 케이블 뒤로) USB 로 이 컴퓨터(Mac 또는 Pi)에 꽂는다.
lidar_slam.py 의 SLAM 을 그대로 돌리면서, frontier.py 로 목표·경로를 고르고 따라 걷는다.

    python brain/frontier_drive.py --dog 192.168.0.18 --hub 127.0.0.1 --name B   # 오늘 개는 secrets.py 이름이 B
    python brain/frontier_drive.py --hub 127.0.0.1                                # --dog 없음: 명령은 안 보내고 계산만 (C1 손에 들고 확인)

움직이는 법 (시뮬레이터 frontier_sim.py 와 같음):
    대기 → (g) → 멈춰서 스캔 --settle 초 → 계획 → 경로 따라 --step m 걷기 → 멈춰서 스캔 → 다시 계획 … → 갈 곳 없으면 완료
    - 목표 방향과 25° 넘게 틀어져 있으면 제자리 회전, 아니면 걸으면서 살짝 회전
    - 뒤따르는 확인견을 위해: 앞쪽 frontier 우선 (frontier.Planner 방향 비용), 한 번에 --turn-chunk ° 까지만 돌고
      --turn-pause 초 서서 기다림 (그동안 B 가 마커를 놓치면 --behind 로 계속 기다림),
      --arc-deg ° 넘게 뒤로 가야 하고 앞·옆에 --arc-room m 이 비어 있으면 제자리 대신 걸으면서 넓게 U턴
    - 초음파 (허브의 front_cm, --hub 필요): C1 이 못 본 장애물도 피함
      --sonar-mark cm 안에 잡히면 그 자리를 계획용 지도에 벽으로 찍고 (--sonar-keep 초 동안),
      --sonar-slow cm 안이면 천천히, --sonar-stop cm 안이면 바로 서서 다시 계획 (같은 곳에서 3번이면 그 목표 제외)
      로봇견 펌웨어의 20 cm 전진 막기(dog_cmd.py STOP_CM)는 그대로
    - --stuck-sec 동안 5 cm 도 못 가면(벽·초음파 정지) 그 목표는 제외하고 다시 계획
    - SLAM 위치가 1초 넘게 안 바뀌면 멈춤
    - 제자리 회전을 4초 했는데 목표 방향과 오히려 멀어지면 멈춤 → 회전 부호가 반대 (--turn-sign 1 로)
    - 길막힘: 몸이 막혀 목표를 --max-fails(3)번 연달아 빼거나, 남은 frontier 가 막혀서 뺀 곳뿐이면 멈추고
      대시보드에 경보 (explore state "blocked"). 그동안 명령을 안 보내니 teleop.py 로 옮기고 g 로 다시 시작

대시보드 미션 (--hub 가 있으면, --no-mission 으로 끔):
    ▶ 탐사 시작(EXPLORE) → g 와 같음 · 사람 발견(SUSPECT)·포위·인계·대기 → 멈추고 명령 안 보냄 (A 는 teleop 으로)
    사건 중에는 g 를 무시. --behind B 면 B 가 마커를 놓친 동안 서서 기다림 (#11)
    켜기 전에 이미 EXPLORE 였으면 반응하지 않음 (marker_follow.py 와 같음) → 그때는 g

사람이 고르기 (--confirm): 계획할 때마다 바로 가지 않고 목표를 제안(분홍 경로·고리, 1/N)하고 기다린다.
    y 또는 Enter = 그 목표로 (도착·막힘·다 보일 때까지 그 목표 유지), n = 다음 후보 (점수 순, 끝이면 처음부터)

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
from frontier import FREE_MIN, OCC_MAX, Planner, direction_ko, korean_font  # noqa: E402

CMD_PORT = 9100
INCIDENT = {"SUSPECT", "CONFIRM", "CONTAIN", "HANDOVER", "REGROUP"}   # server/app.py INCIDENT_STATES
LOST_STATES = {"search", "gave_up", "wait_back"}                       # marker_follow.LOST_STATES
STATE_KO = {"hold": "대기 (g = 시작)", "settle": "멈춰서 스캔", "turn": "제자리 회전", "walk": "걷는 중", "back": "초음파: 뒤로 물러남",
            "done": "탐사 완료", "blocked": "길막힘 (사람이 옮긴 뒤 g)", "propose": "제안: y = 가기, n = 다음"}
EXPLORE_STATE = {"hold": "pause", "settle": "pause", "turn": "walk", "walk": "walk", "back": "walk", "done": "done",
                 "blocked": "blocked", "propose": "propose"}


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
        self.pos = (0.0, 0.0)
        self.fails = 0                                       # 연달아 몸이 막혀 못 간 횟수 (목표에 닿으면 0)
        self.hard: list[tuple[float, float]] = []            # 몸이 막혀서 뺀 목표 (지도 m)
        self.blocked_at: tuple[float, float] | None = None
        self.ask = getattr(a, "confirm", False)               # 사람이 고르기: 제안 → y 가기 / n 다음
        self.chosen: tuple[float, float] | None = None       # 사람이 OK 한 목표 (도착·포기·다 보일 때까지 유지)
        self.rank = 0                                        # 지금 제안이 몇 번째 후보인지
        self.turn_th0 = 0.0                                  # 이번 회전을 시작한 (또는 마지막으로 쉰) 머리 방향
        self.arc, self.arc_room = False, {1: False, -1: False}   # 넓게 U턴 중인지, 왼쪽(+1)·오른쪽(-1)으로 돌 자리가 있는지
        self.front_cm: float | None = None                   # 초음파 앞 거리 (없거나 오래됐으면 None)
        self.sonar: list[tuple[float, float, float]] = []    # 초음파로 찍은 장애물 점 (x, y, 시각) 지도 m
        self.sonar_hits, self.sonar_hit_at = 0, None         # 같은 곳에서 초음파로 선 횟수
        self.sonar_new, self.sonar_replan_t = False, -1e9    # 이번에 새 점을 찍었는지, 마지막으로 초음파 때문에 다시 계획한 시각

    # 사람 키
    def start(self, now):
        if self.state == "blocked":
            # 사람이 옮겼거나 길을 치웠으니 막혔던 목표도 다시 후보로 (초음파 점도 지움)
            self.blacklist, self.hard, self.fails, self.blocked_at, self.goal = [], [], 0, None, None
            self.sonar, self.sonar_hits = [], 0
        if self.state in ("hold", "done", "blocked"):
            self.chosen, self.rank = None, 0
            self.state, self.until = "settle", now + self.a.settle
            self.log("시작: 멈춰서 스캔 후 계획")

    def accept(self, now):
        """제안 OK → 그 목표로 간다."""
        if self.state == "propose" and self.plan and self.plan.goal:
            self.chosen = (self.plan.goal.x, self.plan.goal.y)
            self.log(f"OK: 목표 ({self.chosen[0]:.2f}, {self.chosen[1]:.2f}) 로 감")
            self.state, self.until = "settle", now

    def next(self, now):
        """제안 다음 후보 (끝까지 가면 처음부터)."""
        if self.state == "propose":
            self.rank += 1
            self.state, self.until = "settle", now

    def pause(self, why="멈춤 (g = 다시 시작)"):
        if self.state not in ("hold", "done"):   # 길막힘도 대기로: 사건이 끝나면 다시 시작으로 풀림
            self.state = "hold"
            self.log(why)

    def _settle(self, now, why=""):
        if why:
            self.log(why)
        self.state, self.until = "settle", now + self.a.settle

    def _drop_goal(self, now, why, hard=False):
        """hard = 몸이 막혀서 못 감 (벽·초음파 정지). 연달아 --max-fails 번이면 길막힘."""
        if self.goal:
            self.blacklist.append(self.goal)
            if hard:
                self.hard.append(self.goal)
        self.chosen, self.rank = None, 0
        if hard:
            self.fails += 1
            if self.fails >= self.a.max_fails:
                self._block(f"{why}, {self.fails}번 연속")
                return
        self._settle(now, why + " → 이 목표 제외")

    def _sonar_update(self, now, x, y, th, front_cm) -> None:
        """초음파 거리 → 개 머리 앞 그 거리에 폭 --sonar-width 짜리 장애물 점을 찍는다."""
        a = self.a
        cm = front_cm if front_cm is not None and 2 < front_cm < 500 else None   # 반사 없음 = 6142 같은 큰 값
        self.front_cm = cm
        keep = getattr(a, "sonar_keep", 0)
        if keep:
            self.sonar = [p for p in self.sonar if now - p[2] <= keep]
        if cm is None or cm >= getattr(a, "sonar_mark", 0):
            return
        d = getattr(a, "sonar_offset", 0.15) + cm / 100
        c, s = math.cos(th), math.sin(th)
        half = getattr(a, "sonar_width", 0.1) / 2
        for l in np.arange(-half, half + 1e-6, 0.04):
            px, py = x + d * c - l * s, y + d * s + l * c
            if not any(math.dist((px, py), q[:2]) < 0.03 for q in self.sonar[-60:]):
                self.sonar.append((px, py, now))
                self.sonar_new = True
        del self.sonar[:-400]

    def with_sonar(self, grid):
        """계획용 지도에 초음파 점을 벽(0)으로 그린 복사본 (원본 SLAM 지도는 그대로)."""
        if not self.sonar:
            return grid
        g = grid.copy()
        cell = self.p.cell
        r = max(1, round(0.03 / cell))
        near = max(1, round(0.10 / cell))
        for px, py, _ in self.sonar:
            c, rr = int(px / cell), int(py / cell)
            win = grid[max(rr - near, 0):rr + near + 1, max(c - near, 0):c + near + 1]
            if win.size and win.min() < OCC_MAX:
                continue        # C1 지도에 이미 있는 물체 (10 cm 안): 두 번 그려 길을 넓게 막지 않음
            cv2.circle(g, (c, rr), r, 0, -1)
        return g

    def _room_for_arc(self, grid, x, y, th, side) -> bool:
        """걸으며 U턴(반지름 약 arc_room/2) 할 자리: 앞으로 arc_room/2 m, 도는 쪽으로 arc_room m 네모 안이
        다 빈 칸이면 True (벽이 있거나 모르는 곳이 1/4 넘으면 False). 뒤쪽 목표는 보통 앞이 막혀서 생기므로 앞은 반지름만 본다."""
        room = getattr(self.a, "arc_room", 0.0)
        if room <= 0:
            return False
        cell = self.p.cell
        c, s = math.cos(th), math.sin(th)
        n = unseen = 0
        for f in np.arange(0.0, room / 2 + 1e-6, 0.05):
            for l in np.arange(-0.15, room + 1e-6, 0.05):
                px, py = x + f * c - side * l * s, y + f * s + side * l * c
                r, k = int(py / cell), int(px / cell)
                if not (0 <= r < grid.shape[0] and 0 <= k < grid.shape[1]) or grid[r, k] < OCC_MAX:
                    return False                      # 벽·물체
                n += 1
                unseen += grid[r, k] <= FREE_MIN      # 스캔 빔 사이 작은 빈틈은 봐줌
        return unseen <= 0.25 * n

    def _block(self, why):
        self.state, self.blocked_at = "blocked", self.pos
        self.log(f"길막힘: {why} → 멈춤. teleop 으로 옮기거나 길을 치운 뒤 g", "alert")

    def step(self, now: float, x: float, y: float, th: float, grid_fn, wait: bool = False,
             front_cm: float | None = None) -> tuple[int, int]:
        """th = 개 머리 방향 rad (지도 기준 반시계). 돌려주는 값 (v, 왼쪽 회전).
        wait = 뒤 개가 마커를 놓쳐 기다리는 중 (#11): 그 자리에 서고, 그동안은 막힘·포기 시간을 세지 않는다."""
        a = self.a
        self.pos = (x, y)
        dt, self.last_now = now - getattr(self, "last_now", now), now
        self._sonar_update(now, x, y, th, front_cm)
        if self.state not in ("turn", "walk"):
            self.sonar_new = False     # 서 있을 때 찍힌 점으로 출발하자마자 다시 계획하지 않게
        if self.state in ("hold", "done", "blocked", "propose"):
            return 0, 0
        if wait and self.state in ("turn", "walk"):
            self.goal_t0 += dt
            self.trail = []
            return 0, 0
        if self.state == "back":
            # 초음파로 너무 가까움: 곧게 뒤로 --sonar-back m (또는 3초) 물러난 뒤 다시 계획
            if math.dist(self.back_from, (x, y)) >= getattr(a, "sonar_back", 0.12) or now - self.back_t0 > 3.0:
                self._settle(now)
                return 0, 0
            return -getattr(a, "sonar_slow_speed", 60), 0
        if self.state == "settle":
            if now < self.until:
                return 0, 0
            grid = self.with_sonar(grid_fn())
            plan = self.p.plan(grid, x, y, self.blacklist, rank=self.rank, prefer=self.chosen, th=th)
            self.plan = plan
            self.arc_room = {s: self._room_for_arc(grid, x, y, th, s) for s in (1, -1)}
            if plan.note == "gone":
                self.log("고른 곳이 다 보임 → 다음 제안")
                self.chosen, self.rank = None, 0
                self.until = now
                return 0, 0
            if plan.goal is None and plan.note != "gone" and self.sonar:
                # 초음파 점 때문에 길이 없어졌으면 '완료'가 아니라 길막힘 (사람이 치우거나 옮긴 뒤 g → 점도 지움)
                bare = self.p.plan(grid_fn(), x, y, self.blacklist, rank=self.rank, prefer=self.chosen, th=th)
                if bare.goal is not None:
                    self._block("초음파로 찍은 장애물 때문에 갈 길이 없음")
                    return 0, 0
            if plan.goal is None:
                # 남은 frontier 중 몸이 막혀 뺀 곳이 있으면 = 다 본 게 아니라 길이 막힌 것
                if self.hard and any((np.hypot(fr.cells[:, 0, None] - np.array(self.hard)[:, 0],
                                               fr.cells[:, 1, None] - np.array(self.hard)[:, 1]) < 0.3).any()
                                     for fr in plan.frontiers):
                    self._block("갈 수 있는 frontier 가 없고, 남은 곳은 막혀서 못 간 곳")
                    return 0, 0
                self.state, self.note = "done", plan.note
                self.log(f"탐사 완료: {plan.note} (목표 {self.goals}개)")
                return 0, 0
            g = (plan.goal.x, plan.goal.y)
            if self.ask and self.chosen is None:
                self.state = "propose"
                self.log(f"제안 {plan.rank + 1}/{len(plan.ranked)} [{direction_ko(plan.goal.turn_deg)} {plan.goal.turn_deg:.0f}°]: "
                         f"지도 ({g[0]:.2f}, {g[1]:.2f}) 경로 {plan.goal.cost_m:.2f} m, "
                         f"열린 폭 {plan.goal.size_m:.2f} m → y = 가기, n = 다음")
                return 0, 0
            if self.goal and math.dist(g, self.goal) < 0.15 and math.dist(g, (x, y)) < 0.25:
                self._drop_goal(now, "같은 목표인데 가도 안 보임")   # 목표에 와 있는데 frontier 가 안 사라짐
                return 0, 0
            if not self.goal or math.dist(g, self.goal) > 0.15:
                self.goals += 1
                self.goal_t0 = now
                self.give_up = max(60.0, 2 * plan.goal.cost_m / a.v_ms + 60)
                self.log(f"목표 {self.goals} [{direction_ko(plan.goal.turn_deg)} {plan.goal.turn_deg:.0f}°]: "
                         f"지도 ({g[0]:.2f}, {g[1]:.2f}) 경로 {plan.goal.cost_m:.2f} m, "
                         f"frontier {len(plan.frontiers)}개, 계획 {plan.ms:.0f} ms")
            self.goal = g
            self.wp, self.step_from, self.trail = 1, (x, y), []
            self.state = "walk"

        # turn / walk
        cm = self.front_cm
        forward = self.state == "walk" or (self.state == "turn" and self.arc)
        new, self.sonar_new = self.sonar_new, False
        if forward and new and cm is not None and cm >= getattr(a, "sonar_stop", 0) and now - self.sonar_replan_t > 3.0:
            # 처음 잡혔을 때(아직 --sonar-mark 거리) 바로 다시 계획: 돌아갈 자리가 있을 때 피함
            self.sonar_replan_t = now
            self._settle(now, f"초음파 앞 {cm:.0f} cm 에 장애물 → 지도에 찍고 피해 가는 길로 다시 계획")
            return 0, 0
        if forward and cm is not None and cm < getattr(a, "sonar_stop", 0):
            # 초음파로 앞이 막힘: 점은 이미 지도에 찍었으니 서서 다시 계획 (6초 막힘 판정을 기다리지 않음)
            if self.sonar_hit_at and math.dist(self.sonar_hit_at, (x, y)) < 0.2:
                self.sonar_hits += 1
            else:
                self.sonar_hits = 1
            self.sonar_hit_at = (x, y)
            if self.sonar_hits >= 3:
                self.sonar_hits = 0
                self._drop_goal(now, f"초음파 앞 {cm:.0f} cm 에서 3번 막힘", hard=True)
            elif getattr(a, "sonar_back", 0) > 0:
                self.log(f"초음파 앞 {cm:.0f} cm → 뒤로 {a.sonar_back * 100:.0f} cm 물러나서 다시 계획")
                self.state, self.back_from, self.back_t0 = "back", (x, y), now
            else:
                self._settle(now, f"초음파 앞 {cm:.0f} cm → 서서 피해 가는 길로 다시 계획")
            return 0, 0
        if now - self.goal_t0 > self.give_up:
            self._drop_goal(now, f"{self.give_up:.0f}초 넘게 못 감", hard=True)
            return 0, 0
        path = self.plan.path
        while self.wp < len(path) and math.dist((x, y), path[self.wp]) < a.reach:
            self.wp += 1
        if self.wp >= len(path):
            self.fails, self.chosen, self.rank = 0, None, 0
            self._settle(now, "목표 도착")
            return 0, 0
        if math.dist((x, y), self.step_from) >= a.step:
            self._settle(now)
            return 0, 0
        tx, ty = path[self.wp]
        e = wrap(math.atan2(ty - y, tx - x) - th)
        if abs(e) > math.radians(25):
            side = 1 if e > 0 else -1
            if self.state != "turn":
                self.state, self.turn_t0, self.turn_e0, self.turn_th0 = "turn", now, abs(e), th
                self.arc = abs(e) > math.radians(getattr(a, "arc_deg", 181)) and self.arc_room[side]
                if self.arc:
                    self.log(f"목표가 뒤쪽 ({math.degrees(abs(e)):.0f}°) → 걸으면서 넓게 U턴 (확인견이 따라오게)")
            elif (not self.arc and now - self.turn_t0 > 4 and abs(e) > self.turn_e0 + math.radians(15)):
                self.state = "hold"
                self.log("제자리 회전 중 목표와 더 멀어짐 → 멈춤. 회전 부호가 반대일 수 있음: --turn-sign 을 바꿔 실행")
                return 0, 0
            chunk = getattr(a, "turn_chunk", 0)
            if chunk and abs(wrap(th - self.turn_th0)) >= math.radians(chunk):
                # 한 번에 크게 돌지 않음: 서서 뒤 개가 마커를 다시 잡을 시간을 줌 (놓쳤으면 --behind 로 더 기다림)
                self.state, self.until = "settle", now + max(a.settle, getattr(a, "turn_pause", 0.0))
                return 0, 0
            self.trail = []
            if self.arc:
                return a.speed, int(side * getattr(a, "arc_turn", a.walk_turn))
            return 0, int(side * a.turn)
        if self.state != "walk":
            self.state = "walk"
        self.trail.append((now, x, y))
        while self.trail and now - self.trail[0][0] > a.stuck_sec:
            old = self.trail.pop(0)
            if math.dist(old[1:], (x, y)) < 0.05:
                self._drop_goal(now, f"{a.stuck_sec:.0f}초 동안 5 cm 도 못 감 (막힘·초음파 정지?)", hard=True)
                return 0, 0
        left = max(-a.walk_turn, min(a.walk_turn, round(a.k_turn * math.degrees(e))))
        speed = a.speed
        if cm is not None and cm < getattr(a, "sonar_slow", 0):
            speed = min(speed, getattr(a, "sonar_slow_speed", speed))
        return speed, int(left)


class Dog:
    def __init__(self, ip: str | None, turn_sign: int):
        self.ip, self.sign = ip, turn_sign
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.last = None
        self.ok = True

    def move(self, v: int, left: int) -> None:
        # teleop.py 와 같은 부호: 왼쪽 키 a 는 turn = -turn_sign × 40
        t = -self.sign * left
        if self.ip:
            try:
                self.sock.sendto(json.dumps({"cmd": "move", "v": int(v), "turn": int(t)}).encode(), (self.ip, CMD_PORT))
                self.ok = True
            except OSError as e:   # 로봇견이 Wi-Fi 에 없음. 죽지 않고 알림만 (C1 멈춤·지도 저장은 해야 하므로)
                if self.ok:
                    print(f"\n로봇견 {self.ip} 로 못 보냄 ({e.strerror}) → dog_cmd.py 실행·Wi-Fi·IP 확인")
                self.ok = False
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
        if plan.path and drv.state in ("turn", "walk", "settle", "propose"):
            pts = [P(*p) for p in plan.path]
            ask = drv.state == "propose"
            cv2.polylines(img, [np.array(pts)], False, (230, 80, 230) if ask else (255, 155, 47), 2)
            cv2.circle(img, pts[-1], 12 if ask else 9, (230, 80, 230) if ask else (0, 140, 255), 3 if ask else 2)
            if ask:
                d = plan.goal.turn_deg if plan.goal else 0
                cv2.putText(img, f"{plan.rank + 1}/{len(plan.ranked)} {'FRONT' if d < 45 else 'SIDE' if d < 120 else 'BACK'}", (pts[-1][0] + 14, pts[-1][1] - 8),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.6, (230, 80, 230), 2)
    for sx, sy, _ in drv.sonar:
        cv2.circle(img, P(sx, sy), 3, (40, 40, 255), -1)          # 초음파로 찍은 장애물: 빨간 점
    if drv.blocked_at:
        cv2.drawMarker(img, P(*drv.blocked_at), (0, 0, 255), cv2.MARKER_TILTED_CROSS, 28, 3)
    for bx, by in drv.blacklist:
        cv2.drawMarker(img, P(bx, by), (60, 60, 220), cv2.MARKER_TILTED_CROSS, 12, 2)
    p0 = P(x, y)
    p1 = (int(p0[0] + 22 * math.cos(th)), int(p0[1] - 22 * math.sin(th)))
    cv2.arrowedLine(img, p0, p1, (60, 60, 240), 3, tipLength=0.4)
    bar = np.full((54, img.shape[1], 3), 245, np.uint8)
    cv2.putText(bar, title, (8, 22), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (0, 0, 0), 1)
    keys = "y/Enter go  n next  space pause  q quit" if drv.state == "propose" else "g start  space pause  s save  q quit"
    cv2.putText(bar, keys, (8, 45), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (90, 90, 90), 1)
    return np.vstack([bar, img])


STATE_EN = {"hold": "HOLD (g)", "settle": "SCAN", "turn": "TURN", "walk": "WALK", "back": "BACK OFF (sonar)", "done": "DONE",
            "blocked": "BLOCKED - move by hand, then g", "propose": "PROPOSE"}


def main() -> None:
    from lidar_bridge import BAUD_C1, Keys, RPLidar, find_port, stop_on_sigterm
    from lidar_slam import Arena, Mapper, Sender, clean_view, push, save

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
    g.add_argument("--confirm", action="store_true", help="사람이 고르기: 목표를 제안만 하고 y(Enter) = 가기, n = 다음 후보")
    g.add_argument("--max-fails", type=int, default=3, help="몸이 막혀 목표를 연달아 이만큼 빼면 길막힘 (대시보드 경보)")
    g = ap.add_argument_group("뒤따르는 확인견 배려 (#11)")
    g.add_argument("--turn-chunk", type=float, default=45.0, help="한 번에 도는 최대 각 ° (그다음 --turn-pause 초 섬, 0 = 끔)")
    g.add_argument("--turn-pause", type=float, default=2.0, help="한 번 돈 뒤 서 있는 시간 초 (뒤 개가 마커를 다시 잡게)")
    g.add_argument("--arc-deg", type=float, default=100.0, help="목표가 이보다 뒤쪽이면 걸으면서 넓게 U턴 (자리가 있을 때)")
    g.add_argument("--arc-room", type=float, default=0.7, help="U턴에 필요한 옆 빈 공간 m (앞은 그 절반). 0 = U턴 안 함, 늘 제자리")
    g.add_argument("--arc-turn", type=int, default=30, help="U턴할 때 걸으면서 쓰는 회전 값")
    g.add_argument("--turn-weight", type=float, default=0.64, help="방향 비용: 처음 틀 각 1 rad 당 m (0 = 거리·폭만, 예전처럼)")
    g.add_argument("--sonar-mark", type=float, default=45.0, help="초음파가 이보다 가까우면 그 자리를 지도에 장애물로 cm (0 = 안 씀)")
    g.add_argument("--sonar-slow", type=float, default=40.0, help="초음파가 이보다 가까우면 천천히 cm")
    g.add_argument("--sonar-slow-speed", type=int, default=60, help="천천히 갈 때 전진 값")
    g.add_argument("--sonar-stop", type=float, default=25.0, help="초음파가 이보다 가까우면 바로 서서 다시 계획 cm (펌웨어 20 cm 보다 조금 멀게)")
    g.add_argument("--sonar-back", type=float, default=0.12, help="--sonar-stop 안으로 들어오면 이만큼 뒤로 물러난 뒤 다시 계획 m (0 = 서기만)")
    g.add_argument("--sonar-offset", type=float, default=0.15, help="C1 중심에서 초음파(개 머리)까지 앞으로 m")
    g.add_argument("--sonar-width", type=float, default=0.1, help="초음파 한 번에 찍는 장애물 폭 m (몸 반지름만큼 더 부풀려 계획)")
    g.add_argument("--sonar-keep", type=float, default=300.0, help="초음파 장애물을 기억하는 시간 초 (g 로 길막힘을 풀면 지움)")
    g.add_argument("--back-penalty", type=float, default=2.0, help="120° 넘게 뒤인 목표에 더하는 m (앞·옆이 없을 때만 뒤로)")
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
    g.add_argument("--hole", type=int, default=600, help="벽 두께 mm (작을수록 또렷 · 300 정도 시도)")
    g.add_argument("--quality", type=int, default=50, help="스캔 한 번이 지도를 바꾸는 세기 0~255")
    g.add_argument("--raw-view", action="store_true", help="정리 안 한 원본 지도를 창·대시보드에 표시")
    g.add_argument("--map-moving", action="store_true",
                   help="걷는·도는 중에도 지도에 그림 (기본: 서 있을 때만 그리고, 움직일 땐 위치만 계산)")
    g.add_argument("--still-sec", type=float, default=0.5, help="멈춘 뒤 이만큼 지나야 다시 지도에 그림 (흔들림 가라앉기)")
    g.add_argument("--hub", help="허브 IP: 지도·위치·explore 를 대시보드로")
    g.add_argument("--http-port", type=int, default=8000)
    g.add_argument("--udp-port", type=int, default=9000)
    g.add_argument("--token", default="")
    g.add_argument("--origin", type=float, nargs=2, default=default_origin, metavar=("X", "Y"))
    g.add_argument("--mount", type=float, default=90.0)
    g.add_argument("--send-res", type=float, default=0.04)
    g.add_argument("--no-window", action="store_true", help="지도 창 없이 (키는 터미널에서)")
    g = ap.add_argument_group("미션 (5단계, --hub 의 /api/dogs 를 0.5초마다 읽음)")
    g.add_argument("--no-mission", action="store_true", help="대시보드 미션을 무시 (g·스페이스로만)")
    g.add_argument("--behind", help="바로 뒤 개 이름 (예: B). 그 개가 마커를 놓치면 서서 기다림 (#11, teleop --behind 와 같음)")
    g.add_argument("--link-sec", type=float, default=2.0, help="뒤 개 상태가 이보다 오래되면 무시 (초)")
    g.add_argument("--out", default=str(ROOT / "maps"))
    a = ap.parse_args()
    a.dog_ip, a.dog = a.dog, (a.name if a.hub else None)   # lidar_slam.push 는 a.dog 를 대시보드 이름으로 씀

    port = a.port or find_port()
    if not port:
        sys.exit("LiDAR 포트를 못 찾음. --port 로 지정하세요.")
    lidar = RPLidar(port, a.baud)
    info, (status, _) = lidar.info(), lidar.health()
    print(f"[{port}] C1 model={info['model']} 상태={status}")
    mapper = Mapper(lidar, a.map_px, a.map_m, a.max, a.flip, a.hole, a.quality)
    arena = Arena(a.origin, a.mount, a.map_m)
    sender = Sender(a.hub, a.http_port, a.udp_port, a.token) if a.hub else None
    cell = a.map_m / a.map_px
    planner = Planner(cell_m=cell, radius=a.radius, min_size=a.min_size, gain=a.gain,
                      turn_w=a.turn_weight, back_pen=a.back_penalty)

    def log(text, level="info"):
        print(f"[{time.strftime('%H:%M:%S')}] {text}")
        if sender:
            sender.udp_send({"type": "event", "from": a.name, "ts": round(time.time(), 3), "level": level,
                             "text": f"Dog {a.name} 탐사: {text}"})

    drv = Driver(planner, a, log)
    link = None
    if a.hub and not a.no_mission:
        from marker_follow import MONITOR_HTTP, DogLink
        if a.http_port != MONITOR_HTTP:
            print(f"주의: 미션은 허브 HTTP {MONITOR_HTTP} 에서 읽음")
        link = DogLink(a.hub, a.name)
        print("대시보드 미션 따름: ▶ 탐사 시작 → 출발, 사람 발견·포위·대기 → 멈춤" + (f" · 뒤 Dog {a.behind} 기다림" if a.behind else ""))
    mission_seen, was_wait = None, False
    dog = Dog(a.dog_ip, a.turn_sign)
    print(f"로봇견: {a.dog_ip or '(없음: 명령 안 보냄, 계산만)'} · 이름 {a.name} · 속도 {a.speed} · 회전 {a.turn}")
    if a.dog_ip and not link and a.sonar_mark:
        print("주의: --hub 가 없거나 --no-mission 이라 초음파(front_cm)를 못 읽음 → C1 지도로만 피함")
    th_ = threading.Thread(target=mapper.run, daemon=True)
    th_.start()

    keys = Keys().__enter__() if a.no_window else None
    if not a.no_window:
        cv2.namedWindow("frontier_drive", cv2.WINDOW_AUTOSIZE)
    print("처음 몇 초 가만히 두세요 (SLAM 첫 지도). 준비되면 g = 시작, 스페이스 = 멈춤, s = 저장, q = 끝")

    grid = None
    last_upd, last_upd_t, moving, last_send, last_draw, last_snap, push_state = -1, time.time(), False, 0.0, 0.0, 0.0, {}
    last_motion = 0.0
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
            # 대시보드 미션 (5단계): 탐사 시작 → 출발, 그 밖의 단계로 바뀌면 → 멈춤. 켜기 전의 변화에는 반응 안 함
            mission = link.mission[0] if link and link.mission else None
            if link and link.mission != mission_seen:
                if mission_seen is not None and mission:
                    if mission == "EXPLORE":
                        log("대시보드 탐사 시작 → 출발")
                        drv.start(now)
                    else:
                        drv.pause(f"대시보드 {mission} → 멈춤" + (" (A 는 사람이 teleop 으로)" if mission in INCIDENT else ""))
                        dog.stop()
                mission_seen = link.mission
            wait = bool(link and a.behind and link.follow_of(a.behind, a.link_sec) in LOST_STATES)
            if wait != was_wait:
                if wait and drv.state in ("turn", "walk", "settle"):
                    log(f"뒤 Dog {a.behind} 가 마커를 놓침 → 그 자리에서 기다림")
                was_wait = wait

            for k in pressed:
                if k == "q":
                    raise KeyboardInterrupt
                if k == "g" and link and a.dog_ip and not link.fresh(3.0):
                    log(f"주의: Dog {a.name} 상태가 3초 넘게 안 옴 → 명령이 안 닿을 수 있음 (dog_cmd.py·Wi-Fi·배터리·--name 확인)", "warn")
                if k == "g":
                    if mission in INCIDENT:
                        print(f"\n지금 대시보드가 {mission} (사건 중) → g 무시. 대기·탐사로 바뀐 뒤에")
                    else:
                        drv.start(now)
                if k in ("y", "\r", "\n"):
                    drv.accept(now)
                if k == "n":
                    drv.next(now)
                if k == " ":
                    drv.pause()
                    dog.stop()
                if k == "s":
                    print(f"저장: {save(grid, path, a.map_px, a.map_m, Path(a.out))}")

            stale = now - last_upd_t > 1.0
            if stale and drv.state in ("turn", "walk", "back"):
                drv.state = "hold"
                log("SLAM 위치가 1초 넘게 안 바뀜 → 멈춤 (C1 연결 확인 후 g)", "warn")
            front = link.front_cm if link and link.fresh(1.5) else None
            v, left = drv.step(now, x, y, th, lambda: mapper.snapshot()[0], wait=wait, front_cm=front)
            if (v, left) != (0, 0):
                if now - last_send >= 0.1:
                    dog.move(v, left)
                    last_send = now
                moving = True
                last_motion = now
            elif moving:
                dog.stop()
                moving = False
                last_motion = now
            # 걷는·도는 중 + 멈춘 직후 still_sec 동안은 위치만 계산 (흔들린 스캔이 벽을 번지게 하지 않음)
            mapper.map_on = a.map_moving or now - last_motion >= a.still_sec

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
                                     "path": [P(p) for p in pl.path] if pl and drv.state in ("turn", "walk", "propose") else [],
                                     "rank": [pl.rank + 1, len(pl.ranked)] if pl and drv.state == "propose" else None,
                                     "dir": direction_ko(pl.goal.turn_deg) if pl and pl.goal and drv.state == "propose" else None,
                                     "sonar": [P(q[:2]) for q in drv.sonar[-150:]],
                                     "frontier": fronts[:600], "n": len(pl.frontiers) if pl else 0,
                                     "at": P(drv.blocked_at) if drv.state == "blocked" and drv.blocked_at else None})

            if not keys and now - last_draw > 0.3:
                last_draw = now
                cmd = f"v={dog.last[0]} turn={dog.last[1]}" if dog.last else "v=0"
                offline = bool(link and a.dog_ip and not link.fresh(3.0))
                sonar = f"sonar {drv.front_cm:.0f}cm" if drv.front_cm is not None else "sonar -"
                title = (f"{STATE_EN[drv.state]} goals {drv.goals}  {cmd}  {sonar}" + ("  SLAM STALE" if stale else "")
                         + ("  DOG OFFLINE" if offline else ""))
                cv2.imshow("frontier_drive", render(grid if a.raw_view else clean_view(grid), (x, y, th), drv, cell, title))
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
