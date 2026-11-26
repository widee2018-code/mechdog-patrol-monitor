"""frontier 탐사 1·2단계: SLAM 격자 지도에서 갈 곳(frontier)을 고르고 길을 계획한다 (#14).

frontier = 이미 본 빈 칸 중 바로 옆이 아직 안 본 칸인 곳. 거기까지 가서 LiDAR 로 보면 지도가 넓어진다.

1. 지도를 계획용 칸(기본 4 cm)으로 줄인다: 벽(어두움 < 70) / 빈 칸(밝음 > 170) / 안 봄(나머지)
2. 벽을 몸 반지름(기본 0.18 m)만큼 부풀린다 → 그 안으로는 못 감
3. 안 본 칸과 맞닿은 갈 수 있는 빈 칸 = frontier. 벽 가장자리의 얇은 회색 띠는 안 본 곳으로 치지 않음
4. 로봇 위치에서 Dijkstra(목표가 여러 개인 A*)로 모든 칸까지 경로 비용을 한 번에 구함.
   벽에 가까운 칸(--clear 안쪽)은 비용을 더 매겨 가운데로 다니게 함
5. frontier 덩어리마다 가장 가까운 칸을 후보로, 점수 = 경로 길이 − gain × 덩어리 길이 가 가장 작은 곳이 목표
6. 경로에서 직선으로 이어도 되는 구간은 건너뛰고 꺾이는 점(waypoint)만 남김

좌표: SLAM 지도 m (지도 왼쪽 아래 = (0, 0), x 오른쪽, y 위). grid[행 = y, 열 = x] (lidar_slam.py 와 같음).

저장된 지도로 확인 (그림을 지도 옆에 저장):
    python brain/frontier.py maps/slam_20261123_150242.npy              # 로봇 = 지도 가운데(SLAM 시작점)
    python brain/frontier.py maps/slam_20261123_150242.npy --at 5.2 6.4  # 로봇 위치를 지도 m 로
"""

from __future__ import annotations

import argparse
import heapq
import math
import time
from dataclasses import dataclass, field
from pathlib import Path

import cv2
import numpy as np

UNKNOWN = 127    # BreezySLAM 에서 아직 안 본 칸
OCC_MAX = 70     # 이보다 어두우면 벽 (lidar_slam.map_message 와 같은 기준)
FREE_MIN = 170   # 이보다 밝으면 빈 칸

STEPS = [(-1, -1), (-1, 0), (-1, 1), (0, -1), (0, 1), (1, -1), (1, 0), (1, 1)]


@dataclass
class Frontier:
    x: float            # 목표 칸 (덩어리 중 경로 비용이 가장 작은 칸), 지도 m
    y: float
    size_m: float       # 덩어리 길이 (칸 수 × 칸 크기)
    cost_m: float       # 로봇에서 경로 비용 (벽 근처 가중 포함), 못 가면 inf
    cells: np.ndarray   # 덩어리 칸들 (N, 2) 지도 m, 그림용


@dataclass
class Plan:
    frontiers: list[Frontier] = field(default_factory=list)   # 길이가 --min-size 이상인 것 전부 (못 가는 것 포함)
    goal: Frontier | None = None
    path: list[tuple[float, float]] = field(default_factory=list)   # waypoint, 로봇 → 목표 (지도 m)
    note: str = ""
    ms: float = 0.0


class Planner:
    def __init__(self, cell_m: float = 0.02, res: float = 0.04, radius: float = 0.18, clear: float = 0.35,
                 wall_cost: float = 3.0, min_size: float = 0.20, gain: float = 1.0):
        self.cell = cell_m
        self.f = max(1, round(res / cell_m))
        self.res = self.f * cell_m
        self.radius, self.clear, self.wall_cost = radius, clear, wall_cost
        self.min_size, self.gain = min_size, gain
        r = max(1, math.ceil(radius / self.res))
        self.kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * r + 1, 2 * r + 1))

    # ── 지도 → 계획용 칸 ─────────────────────────────────────────
    def layers(self, grid: np.ndarray):
        """보이는 부분만 잘라 계획용 칸으로 줄인다. (occ, free, unknown, 행 시작, 열 시작) — 시작은 원래 지도 칸."""
        seen = np.abs(grid.astype(np.int16) - UNKNOWN) > 10
        if not seen.any():
            return None
        f, n = self.f, grid.shape[0]
        pad = f * (math.ceil(self.radius / self.res) + 4)
        (r0, c0), (r1, c1) = np.argwhere(seen).min(0), np.argwhere(seen).max(0)
        r0, c0 = max(r0 - pad, 0) // f * f, max(c0 - pad, 0) // f * f
        r1, c1 = min(r1 + pad, n), min(grid.shape[1], c1 + pad)
        sub = grid[r0:r1, c0:c1]
        h, w = sub.shape[0] // f, sub.shape[1] // f
        b = sub[:h * f, :w * f].reshape(h, f, w, f)
        mn, mx = b.min(axis=(1, 3)), b.max(axis=(1, 3))
        occ = mn < OCC_MAX
        free = ~occ & (mx > FREE_MIN)
        unk = ~occ & ~free
        return occ, free, unk, r0, c0

    def to_m(self, r, c, r0, c0):
        return (c0 + (np.asarray(c) + 0.5) * self.f) * self.cell, (r0 + (np.asarray(r) + 0.5) * self.f) * self.cell

    # ── 계획 ────────────────────────────────────────────────────
    def plan(self, grid: np.ndarray, x: float, y: float, blacklist=()) -> Plan:
        t0 = time.perf_counter()
        L = self.layers(grid)
        if L is None:
            return Plan(note="지도가 비어 있음")
        occ, free, unk, r0, c0 = L
        h, w = occ.shape
        blocked = cv2.dilate(occ.astype(np.uint8), self.kernel).astype(bool)
        ok = free & ~blocked
        # 얇은 회색 띠(벽 가장자리, 빔 사이 틈)는 빼고 넓게 비어 있는 안 본 곳만
        unk_big = cv2.morphologyEx(unk.astype(np.uint8), cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))
        near_unk = cv2.dilate(unk_big, cv2.getStructuringElement(cv2.MORPH_CROSS, (3, 3))).astype(bool)
        front = ok & near_unk

        # 벽 가까운 칸 추가 비용: 몸 반지름에서 wall_cost, --clear 에서 0
        dwall = cv2.distanceTransform((~occ).astype(np.uint8), cv2.DIST_L2, 5) * self.res
        span = max(self.clear - self.radius, 1e-6)
        pen = 1.0 + self.wall_cost * np.clip((self.clear - dwall) / span, 0, 1)

        # 시작 칸: 몸이 벽 근처라 못 가는 칸이면 0.3 m 안의 가장 가까운 갈 수 있는 칸에서
        sr = int((y / self.cell - r0) // self.f)
        sc = int((x / self.cell - c0) // self.f)
        if not (0 <= sr < h and 0 <= sc < w):
            return Plan(note="로봇이 지도 밖", ms=(time.perf_counter() - t0) * 1e3)
        if not ok[sr, sc]:
            k = math.ceil(0.3 / self.res)
            win = ok[max(sr - k, 0):sr + k + 1, max(sc - k, 0):sc + k + 1]
            cand = np.argwhere(win)
            if len(cand) == 0:
                return Plan(note="로봇 주변에 갈 수 있는 칸이 없음 (벽에 붙음?)", ms=(time.perf_counter() - t0) * 1e3)
            cand += [max(sr - k, 0), max(sc - k, 0)]
            sr, sc = cand[np.argmin(((cand - [sr, sc]) ** 2).sum(1))]

        cost, parent = self._dijkstra(ok, pen, int(sr), int(sc))

        plan = Plan()
        n, labels = cv2.connectedComponents(front.astype(np.uint8), connectivity=8)
        bl = np.array(blacklist, float).reshape(-1, 2)
        best, best_score, best_cell = None, math.inf, None
        for i in range(1, n):
            rc = np.argwhere(labels == i)
            size = len(rc) * self.res
            if size < self.min_size:
                continue
            cs = cost[rc[:, 0], rc[:, 1]]
            j = int(np.argmin(cs))
            fx, fy = self.to_m(rc[j, 0], rc[j, 1], r0, c0)
            mx_, my_ = self.to_m(rc[:, 0], rc[:, 1], r0, c0)
            fr = Frontier(float(fx), float(fy), size, float(cs[j]) * self.res, np.stack([mx_, my_], 1))
            plan.frontiers.append(fr)
            if not math.isfinite(fr.cost_m):
                continue
            if len(bl) and (np.hypot(*(fr.cells[:, None, :] - bl[None]).transpose(2, 0, 1)) < 0.3).any():
                continue
            score = fr.cost_m - self.gain * size
            if score < best_score:
                best, best_score, best_cell = fr, score, (int(rc[j, 0]), int(rc[j, 1]))

        if best is None:
            plan.note = "갈 수 있는 frontier 없음 → 탐사 완료" if plan.frontiers or not front.any() else "frontier 가 전부 너무 작음"
        else:
            plan.goal = best
            cells = self._trace(parent, best_cell, w)
            cells = self._shortcut(cells, ok)
            plan.path = [tuple(map(float, self.to_m(r, c, r0, c0))) for r, c in cells]
            plan.path[0] = (x, y)
        plan.ms = (time.perf_counter() - t0) * 1e3
        return plan

    def _dijkstra(self, ok, pen, sr, sc):
        h, w = ok.shape
        okl, penl = ok.ravel().tolist(), pen.ravel().tolist()
        cost = [math.inf] * (h * w)
        parent = [-1] * (h * w)
        s = sr * w + sc
        cost[s] = 0.0
        pq = [(0.0, s)]
        steps = [(dr * w + dc, dr, dc, math.hypot(dr, dc)) for dr, dc in STEPS]
        while pq:
            d, u = heapq.heappop(pq)
            if d > cost[u]:
                continue
            ur, uc = divmod(u, w)
            for off, dr, dc, ln in steps:
                r, c = ur + dr, uc + dc
                if not (0 <= r < h and 0 <= c < w):
                    continue
                v = u + off
                if not okl[v]:
                    continue
                nd = d + ln * 0.5 * (penl[u] + penl[v])
                if nd < cost[v]:
                    cost[v] = nd
                    parent[v] = u
                    heapq.heappush(pq, (nd, v))
        return np.array(cost).reshape(h, w), parent

    @staticmethod
    def _trace(parent, cell, w):
        u = cell[0] * w + cell[1]
        out = []
        while u != -1:
            out.append(divmod(u, w))
            u = parent[u]
        return out[::-1]

    @staticmethod
    def _shortcut(cells, ok):
        """앞에서부터 직선으로 갈 수 있는 가장 먼 점으로 건너뛴다 (waypoint 만 남김)."""
        if len(cells) <= 2:
            return cells

        def clear(a, b):
            n = max(abs(b[0] - a[0]), abs(b[1] - a[1])) * 2 + 1
            rs = np.rint(np.linspace(a[0], b[0], n)).astype(int)
            cs = np.rint(np.linspace(a[1], b[1], n)).astype(int)
            return bool(ok[rs, cs].all())

        out, i = [cells[0]], 0
        while i < len(cells) - 1:
            j = len(cells) - 1
            while j > i + 1 and not clear(cells[i], cells[j]):
                j -= 1
            out.append(cells[j])
            i = j
        return out


# ── 그림 ─────────────────────────────────────────────────────────

def draw(ax, grid, plan: Plan, pose, cell_m: float, view=None, title: str = ""):
    """지도 + frontier(주황, 목표는 진하게) + 경로(하늘색) + 로봇(빨간 화살표)."""
    n = grid.shape[0] * cell_m
    ax.clear()
    ax.imshow(grid, cmap="gray", origin="lower", vmin=0, vmax=255, extent=(0, n, 0, n))
    for fr in plan.frontiers:
        tgt = fr is plan.goal
        col = "#ff8c1a" if tgt else ("#ffc680" if math.isfinite(fr.cost_m) else "#b0b0b0")
        ax.plot(fr.cells[:, 0], fr.cells[:, 1], "s", ms=2.2 if tgt else 1.6, color=col, mec="none")
    if plan.path:
        p = np.array(plan.path)
        ax.plot(p[:, 0], p[:, 1], "-o", color="#2f9bff", lw=2, ms=3)
    if plan.goal:
        ax.plot(plan.goal.x, plan.goal.y, "*", ms=16, color="#ff8c1a", mec="k", mew=0.8)
    x, y, th = pose
    L = 0.3
    ax.arrow(x, y, L * math.cos(th), L * math.sin(th), color="#ff3b30", width=0.04, head_width=0.14,
             length_includes_head=True, zorder=5)
    if view:
        ax.set_xlim(view[0], view[2]); ax.set_ylim(view[1], view[3])
    ax.set_title(title, fontsize=10)
    ax.set_xticks([]); ax.set_yticks([])


def korean_font(plt) -> None:
    for f in ("AppleGothic", "NanumGothic", "Noto Sans CJK KR"):
        if any(f == x.name for x in plt.matplotlib.font_manager.fontManager.ttflist):
            plt.rcParams["font.family"] = f
            return


def seen_view(grid, cell_m, pad_m=0.5):
    seen = np.argwhere(np.abs(grid.astype(np.int16) - UNKNOWN) > 10)
    if len(seen) == 0:
        return None
    (r0, c0), (r1, c1) = seen.min(0), seen.max(0)
    return (c0 * cell_m - pad_m, r0 * cell_m - pad_m, c1 * cell_m + pad_m, r1 * cell_m + pad_m)


def main() -> None:
    ap = argparse.ArgumentParser(description="저장된 SLAM 지도에서 frontier 와 경로 확인 (#14 1·2단계)")
    ap.add_argument("map", help="lidar_slam.py 가 저장한 .npy")
    ap.add_argument("--map-m", type=float, default=12.0, help="지도 한 변 m (lidar_slam.py 의 --map-m)")
    ap.add_argument("--at", type=float, nargs=2, metavar=("X", "Y"), help="로봇 위치 지도 m (기본: 가운데 = SLAM 시작점)")
    ap.add_argument("--heading", type=float, default=0.0, help="로봇 방향 ° (그림용)")
    ap.add_argument("--res", type=float, default=0.04, help="계획용 칸 m")
    ap.add_argument("--radius", type=float, default=0.18, help="몸 반지름 m (벽을 이만큼 부풀림)")
    ap.add_argument("--clear", type=float, default=0.35, help="벽에서 이만큼 안쪽은 비용을 더함 m")
    ap.add_argument("--min-size", type=float, default=0.20, help="이보다 짧은 frontier 는 무시 m")
    ap.add_argument("--gain", type=float, default=1.0, help="frontier 길이 1 m 를 경로 몇 m 만큼 쳐 줄지")
    ap.add_argument("--out", help="그림 파일 (기본: 지도 이름_frontier.png)")
    a = ap.parse_args()

    grid = np.load(a.map)
    cell = a.map_m / grid.shape[0]
    x, y = a.at or (a.map_m / 2, a.map_m / 2)
    pl = Planner(cell, a.res, a.radius, a.clear, min_size=a.min_size, gain=a.gain)
    plan = pl.plan(grid, x, y)

    print(f"계획 {plan.ms:.0f} ms, frontier {len(plan.frontiers)}개 (≥ {a.min_size} m)")
    for fr in sorted(plan.frontiers, key=lambda f: f.cost_m):
        mark = "← 목표" if fr is plan.goal else ""
        cost = f"{fr.cost_m:5.2f} m" if math.isfinite(fr.cost_m) else "  못 감"
        print(f"  ({fr.x:5.2f}, {fr.y:5.2f})  길이 {fr.size_m:4.2f} m  경로 비용 {cost} {mark}")
    if plan.goal:
        print("경로 waypoint:", " → ".join(f"({px:.2f},{py:.2f})" for px, py in plan.path))
    if plan.note:
        print(plan.note)

    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    korean_font(plt)
    out = Path(a.out or Path(a.map).with_name(Path(a.map).stem + "_frontier.png"))
    fig, ax = plt.subplots(figsize=(7, 7))
    draw(ax, grid, plan, (x, y, math.radians(a.heading)), cell, seen_view(grid, cell),
         f"frontier {len(plan.frontiers)}개 · 목표 ★ · 경로 {len(plan.path)}점 · {plan.ms:.0f} ms")
    fig.savefig(out, dpi=110, bbox_inches="tight")
    print("그림:", out)


if __name__ == "__main__":
    main()
