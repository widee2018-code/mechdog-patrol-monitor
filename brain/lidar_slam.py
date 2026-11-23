"""RPLIDAR C1 + BreezySLAM 으로 지도 만들기 (1단계: 손에 들고 매핑, 2단계: 대시보드 표시).

C1을 USB로 꽂고 들고 천천히 걸으면, 스캔끼리 맞춰서(스캔 매칭) 센서 위치를 구하고
격자 지도를 쌓습니다. 오도메트리(이동 거리 정보) 없이 동작합니다 (RMHC-SLAM).

설치 (BreezySLAM은 PyPI에 없음 → GitHub에서):
    pip install pyserial numpy matplotlib
    git clone https://github.com/simondlevy/BreezySLAM.git
    cd BreezySLAM/python && pip install .

실행:
    python brain/lidar_slam.py                     # 지도 창 띄우기 (s 저장, q 끝내기)
    python brain/lidar_slam.py --hub 127.0.0.1     # + 대시보드에 지도·현재 스캔·C1 위치 표시
    python brain/lidar_slam.py --hub 127.0.0.1 --no-window   # 창 없이 대시보드로만 (Pi·SSH용, Ctrl+C 로 끝)
    python brain/lidar_slam.py --seconds 20        # 창 없이 20초 돌리고 지도 저장

대시보드 전송 (--hub):
- 지도: 1초마다 `map` 메시지 (PNG, HTTP POST /api/ingest, 크기 때문에 UDP 아님)
- 현재 스캔: 5 Hz `lidar` 메시지 (UDP), origin 이 C1 현재 위치를 따라 움직임
- 아레나에 놓는 법은 tools/lidar_bridge.py 와 같음: 시작할 때 C1이 --origin 에서 --mount 방향을 보고 있다고 봄
  (--tune 으로 맞춘 --mount, --flip, --origin 을 그대로 쓰면 됨)

끝나면 maps/slam_<시각>.png (지도 + 이동 경로) 와 .npy (원본 격자) 를 저장합니다.

잘 만들어지게 하는 법:
- 천천히 (초당 0.3 m 이하), 회전은 특히 천천히. 센서는 수평으로, 몸이 가리지 않게 머리 위나 앞으로 들기
- 벽·가구처럼 모양이 있는 곳에서 시작. 긴 복도나 빈 공간은 매칭이 미끄러짐
- 처음 2~3초는 가만히 (첫 지도를 잡는 시간)
"""

from __future__ import annotations

import argparse
import base64
import json
import math
import os
import socket
import struct
import sys
import threading
import time
import urllib.request
import zlib
from datetime import datetime
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))
from lidar_bridge import BAUD_C1, RPLidar, find_port, stop_on_sigterm, to_points  # noqa: E402

try:
    from breezyslam.algorithms import RMHC_SLAM
    from breezyslam.sensors import Laser
except ImportError:
    sys.exit("BreezySLAM 이 없습니다. 파일 맨 위 '설치' 를 따라 GitHub에서 설치하세요.")

MIN_POINTS = 150  # 1° 칸 중 이보다 적게 찬 바퀴는 버림 (시작 직후 반 바퀴 등)
UNKNOWN = 127     # BreezySLAM 지도에서 아직 안 본 칸의 값 (0 = 벽, 255 = 빈 공간)


class Mapper:
    """LiDAR 읽기 + SLAM 을 별도 스레드에서 돌리고, 최신 지도·위치를 꺼내 쓰게 한다."""

    def __init__(self, lidar: RPLidar, map_px: int, map_m: float, max_m: float, flip: bool):
        self.lidar = lidar
        self.map_px, self.map_m = map_px, map_m
        self.max_mm = max_m * 1000
        self.flip = flip
        self.slam = RMHC_SLAM(Laser(360, 10, 360, int(self.max_mm)), map_px, map_m)
        self.mapbytes = bytearray(map_px * map_px)
        self.lock = threading.Lock()
        self.path: list[tuple[float, float, float]] = []
        self.last_rev: list = []
        self.updates = 0
        self.skipped = 0
        self.error: Exception | None = None
        self.stop = threading.Event()

    def run(self) -> None:
        try:
            for rev in self.lidar.scans():
                if self.stop.is_set():
                    return
                # BreezySLAM 은 한 바퀴에 scan_size(360)점까지만 받는다 (넘으면 C 코드에서 메모리 넘침 → 강제 종료).
                # C1 은 한 바퀴 약 460점이라 1° 칸마다 칸 가운데에 가장 가까운 각도의 점 하나만 남긴다.
                bins: dict[int, tuple[float, float, float]] = {}
                for a, d, _ in rev:
                    mm = d * 1000
                    if not (0 < mm <= self.max_mm):
                        continue
                    k = int(a) % 360
                    off = abs(a - (k + 0.5))
                    if k not in bins or off < bins[k][0]:
                        bins[k] = (off, a, mm)
                if len(bins) < MIN_POINTS:
                    self.skipped += 1
                    continue
                # BreezySLAM 은 입력 각도 a 를 센서 정면 기준 반시계 (a - 180)° 로 쓴다.
                # C1 은 시계 방향이라 a_in = 180 - a_cw 로 바꿔야 지도가 좌우로 뒤집히지 않는다 (--flip 이면 반대).
                pts = sorted(((180 + a) % 360 if self.flip else (180 - a) % 360, mm) for _, a, mm in bins.values())
                with self.lock:
                    self.slam.update([d for _, d in pts], scan_angles_degrees=[a for a, _ in pts])
                    self.path.append(self.slam.getpos())
                    self.last_rev = rev
                    self.updates += 1
        except Exception as e:  # 스레드 안 오류를 메인에 알림
            self.error = e

    def snapshot(self) -> tuple[np.ndarray, list[tuple[float, float, float]]]:
        with self.lock:
            self.slam.getmap(self.mapbytes)
            grid = np.frombuffer(bytes(self.mapbytes), dtype=np.uint8).reshape(self.map_px, self.map_px)
            return grid.copy(), list(self.path)


# ── 대시보드로 보내기 ──────────────────────────────────────────────

class Arena:
    """SLAM 좌표(시작점 = 지도 가운데, 시작 방향 = +x) → 아레나 좌표 (lidar_bridge 의 origin·mount 와 같은 기준)."""

    def __init__(self, origin, mount_deg: float, map_m: float):
        self.ox, self.oy = origin
        self.mount = mount_deg
        self.c, self.s = math.cos(math.radians(mount_deg)), math.sin(math.radians(mount_deg))
        self.half = map_m / 2

    def point(self, lx: float, ly: float) -> tuple[float, float]:
        """SLAM 지도 좌표 m (지도 왼쪽 아래 = 0) → 아레나 m."""
        x, y = lx - self.half, ly - self.half
        return self.ox + self.c * x - self.s * y, self.oy + self.s * x + self.c * y

    def pose(self, p) -> tuple[float, float, float]:
        x, y = self.point(p[0] / 1000, p[1] / 1000)
        return x, y, (self.mount + p[2]) % 360


def png_rgba(img: np.ndarray) -> bytes:
    """RGBA 배열(행 0 = 위) → PNG 바이트. 추가 패키지 없이 zlib 로 만든다."""
    h, w, _ = img.shape
    raw = b"".join(b"\x00" + img[r].tobytes() for r in range(h))

    def chunk(tag: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)

    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 6, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw, 9)) + chunk(b"IEND", b""))


def map_message(grid: np.ndarray, path, mapper: Mapper, arena: Arena, send_res: float) -> dict | None:
    cell = mapper.map_m / mapper.map_px
    f = max(1, round(send_res / cell))
    rs, cs = crop(grid, pad=2 * f)
    if rs.start is None:
        return None
    r0, c0 = rs.start - rs.start % f, cs.start - cs.start % f
    sub = grid[r0:rs.stop, c0:cs.stop]
    h, w = sub.shape[0] // f, sub.shape[1] // f
    if not h or not w:
        return None
    blocks = sub[:h * f, :w * f].reshape(h, f, w, f)
    mn, mx = blocks.min(axis=(1, 3)), blocks.max(axis=(1, 3))
    rgba = np.zeros((h, w, 4), np.uint8)              # 아직 안 본 곳: 투명
    rgba[mx > 170] = (94, 225, 255, 34)               # 빈 공간: 옅은 하늘색
    rgba[mn < 70] = (232, 238, 247, 240)              # 벽·물체: 밝은 회백색
    png = png_rgba(rgba[::-1])                         # PNG 는 위쪽 행부터 → y 를 뒤집음
    ox, oy = arena.point(c0 * cell, r0 * cell)
    x, y, hd = arena.pose(path[-1])
    return {"type": "map", "from": "pi", "ts": round(time.time(), 3), "res": round(cell * f, 4), "w": w, "h": h,
            "origin": [round(ox, 3), round(oy, 3)], "rot": round(arena.mount, 2),
            "pose": [round(x, 3), round(y, 3), round(hd, 1)], "updates": mapper.updates,
            "png": "data:image/png;base64," + base64.b64encode(png).decode()}


class Sender:
    def __init__(self, hub: str, http_port: int, udp_port: int, token: str):
        self.url = f"http://{hub}:{http_port}/api/ingest"
        self.udp = (hub, udp_port)
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.headers = {"Content-Type": "application/json"}
        if token:
            self.headers["Authorization"] = f"Bearer {token}"
        self.warned = False
        self.busy = threading.Lock()

    def udp_send(self, msg: dict) -> None:
        self.sock.sendto(json.dumps(msg, separators=(",", ":")).encode(), self.udp)

    def http_send(self, msg: dict) -> None:
        """지도는 크니 HTTP. 화면이 멈추지 않게 별도 스레드에서, 앞 전송이 안 끝났으면 이번 것은 건너뜀."""
        if not self.busy.acquire(blocking=False):
            return

        def go():
            try:
                req = urllib.request.Request(self.url, json.dumps(msg).encode(), self.headers, method="POST")
                urllib.request.urlopen(req, timeout=3).read()
                self.warned = False
            except Exception as e:
                if not self.warned:
                    print(f"\n지도 전송 실패 ({self.url}): {e} — 허브가 켜져 있는지 확인")
                    self.warned = True
            finally:
                self.busy.release()

        threading.Thread(target=go, daemon=True).start()


def push(mapper: Mapper, arena: Arena, sender: Sender, args, grid, path, state: dict) -> None:
    now = time.time()
    if not path:
        return
    if now - state.get("scan", 0) >= 0.2:
        state["scan"] = now
        with mapper.lock:
            rev = mapper.last_rev
        x, y, hd = arena.pose(path[-1])
        pts = to_points(rev, hd, 0.05, args.max, 0, args.flip)
        sender.udp_send({"type": "lidar", "from": "pi", "ts": round(now, 3), "origin": [round(x, 3), round(y, 3)],
                         "heading_deg": round(hd, 1), "points": pts})
    if now - state.get("map", 0) >= 1.0:
        state["map"] = now
        msg = map_message(grid, path, mapper, arena, args.send_res)
        if msg:
            sender.http_send(msg)


# ── 저장·화면 ──────────────────────────────────────────────────────

def mm_to_px(mm: float, map_px: int, map_m: float) -> float:
    return mm / (map_m * 1000) * map_px


def draw(ax, grid, path, map_px, map_m, title):
    ax.clear()
    ax.imshow(grid, cmap="gray", origin="lower", vmin=0, vmax=255)
    if path:
        xs = [mm_to_px(p[0], map_px, map_m) for p in path]
        ys = [mm_to_px(p[1], map_px, map_m) for p in path]
        ax.plot(xs, ys, "-", color="#2f9bff", lw=1.5)
        x, y, th = xs[-1], ys[-1], np.radians(path[-1][2])
        L = map_px * 0.03
        ax.arrow(x, y, L * np.cos(th), L * np.sin(th), color="#ff5a52", width=map_px * 0.004,
                 head_width=map_px * 0.015, length_includes_head=True)
    ax.set_title(title, fontsize=10)
    ax.set_xticks([]); ax.set_yticks([])


def crop(grid: np.ndarray, pad: int = 20) -> tuple[slice, slice]:
    """아직 안 본 곳(회색 127 근처)을 잘라 내고 지도가 있는 부분만 남긴다."""
    seen = np.argwhere(np.abs(grid.astype(int) - UNKNOWN) > 10)
    if len(seen) == 0:
        return slice(None), slice(None)
    (r0, c0), (r1, c1) = seen.min(0), seen.max(0)
    n = grid.shape[0]
    return slice(max(r0 - pad, 0), min(r1 + pad, n)), slice(max(c0 - pad, 0), min(c1 + pad, n))


def save(grid, path, map_px, map_m, out_dir: Path) -> Path:
    import matplotlib.pyplot as plt
    out_dir.mkdir(parents=True, exist_ok=True)
    stem = out_dir / f"slam_{datetime.now():%Y%m%d_%H%M%S}"
    np.save(f"{stem}.npy", grid)
    rs, cs = crop(grid)
    fig, ax = plt.subplots(figsize=(7, 7))
    draw(ax, grid, path, map_px, map_m, f"{map_m:g} m 지도 · 칸 {map_m / map_px * 100:.1f} cm · SLAM {len(path)}회")
    ax.set_xlim(cs.start or 0, cs.stop or map_px)
    ax.set_ylim(rs.start or 0, rs.stop or map_px)
    fig.savefig(f"{stem}.png", dpi=120, bbox_inches="tight")
    plt.close(fig)
    return Path(f"{stem}.png")


def pose_text(path, map_m) -> str:
    if not path:
        return "위치 아직 없음"
    x, y, th = path[-1]
    c = map_m * 1000 / 2
    return f"시작점 기준 x {(x - c) / 1000:+.2f} m, y {(y - c) / 1000:+.2f} m, 방향 {th % 360:5.1f}°"


def main() -> None:
    stop_on_sigterm()   # 백그라운드 종료(SIGTERM)도 C1 멈춤 + 지도 저장
    default_origin = [3.0, 0.15]
    try:
        default_origin = json.loads((ROOT / "web" / "arena.json").read_text())["lidar_origin"]
    except Exception:
        pass

    ap = argparse.ArgumentParser(description="RPLIDAR C1 + BreezySLAM 매핑 (+ 대시보드 표시)")
    ap.add_argument("--port", help="시리얼 포트 (기본: 자동 탐색)")
    ap.add_argument("--baud", type=int, default=BAUD_C1)
    ap.add_argument("--map-m", type=float, default=12.0, help="지도 한 변 m (시작점이 가운데)")
    ap.add_argument("--map-px", type=int, default=600, help="지도 한 변 칸 수 (12 m / 600 = 2 cm)")
    ap.add_argument("--max", type=float, default=8.0, help="이보다 먼 점은 버림 m")
    ap.add_argument("--seconds", type=float, help="창 없이 이 시간만 돌리고 저장 (Pi·SSH용)")
    ap.add_argument("--no-window", action="store_true", help="지도 창 없이 (Ctrl+C 로 끝, 끝낼 때 저장)")
    ap.add_argument("--out", default=str(ROOT / "maps"), help="저장 폴더")
    g = ap.add_argument_group("대시보드 (--hub 를 주면 보냄)")
    g.add_argument("--hub", help="허브 IP (예: 127.0.0.1)")
    g.add_argument("--http-port", type=int, default=8000)
    g.add_argument("--udp-port", type=int, default=9000)
    g.add_argument("--token", default=os.environ.get("INGEST_TOKEN", ""), help="허브 INGEST_TOKEN (설정했을 때만)")
    g.add_argument("--origin", type=float, nargs=2, default=default_origin, metavar=("X", "Y"),
                   help=f"시작할 때 C1 의 아레나 위치 m (기본 arena.json: {default_origin})")
    g.add_argument("--mount", type=float, default=90.0, help="시작할 때 C1 정면의 아레나 각도 (반시계, 90=위)")
    g.add_argument("--flip", action="store_true", help="좌우 반전 (lidar_bridge --tune 에서 반전을 켰다면)")
    g.add_argument("--send-res", type=float, default=0.04, help="대시보드로 보내는 지도 칸 크기 m")
    args = ap.parse_args()

    port = args.port or find_port()
    if not port:
        sys.exit("LiDAR 포트를 못 찾음. --port 로 지정하세요.")
    headless = bool(args.seconds or args.no_window)
    import matplotlib
    if headless:
        matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    for f in ("AppleGothic", "NanumGothic", "Noto Sans CJK KR"):
        if any(f == x.name for x in matplotlib.font_manager.fontManager.ttflist):
            plt.rcParams["font.family"] = f
            break

    lidar = RPLidar(port, args.baud)
    info = lidar.info()
    status, _ = lidar.health()
    print(f"[{port}] model={info['model']} fw={info['firmware']} 상태={status}")
    mapper = Mapper(lidar, args.map_px, args.map_m, args.max, args.flip)
    arena = Arena(args.origin, args.mount, args.map_m)
    sender = Sender(args.hub, args.http_port, args.udp_port, args.token) if args.hub else None
    if sender:
        print(f"대시보드로 전송: 지도 {sender.url} (1초), 스캔 udp://{args.hub}:{args.udp_port} (5 Hz) · "
              f"시작 위치 {args.origin}, 방향 {args.mount:g}°" + (", 반전" if args.flip else ""))
    th = threading.Thread(target=mapper.run, daemon=True)
    th.start()
    out = Path(args.out)
    t0 = time.time()
    state: dict = {}

    try:
        if headless:
            last_print = 0.0
            while not mapper.error and not (args.seconds and time.time() - t0 >= args.seconds):
                time.sleep(0.1)
                grid, path = mapper.snapshot()
                if sender:
                    push(mapper, arena, sender, args, grid, path, state)
                if time.time() - last_print >= 1:
                    last_print = time.time()
                    print(f"\r{time.time() - t0:4.0f}s · SLAM {mapper.updates}회 · {pose_text(path, args.map_m)}   ",
                          end="", flush=True)
        else:
            plt.ion()
            fig, ax = plt.subplots(figsize=(8, 8))
            fig.canvas.manager.set_window_title("C1 SLAM — s 저장, q 끝내기")
            want_save = []
            fig.canvas.mpl_connect("key_press_event", lambda e: want_save.append(1) if e.key == "s" else None)
            print("지도 창: s 저장, q 또는 창 닫기로 끝내기 (끝낼 때 자동 저장)")
            last_draw = 0.0
            while plt.fignum_exists(fig.number) and not mapper.error:
                grid, path = mapper.snapshot()
                if sender:
                    push(mapper, arena, sender, args, grid, path, state)
                if time.time() - last_draw >= 0.3:
                    last_draw = time.time()
                    draw(ax, grid, path, args.map_px, args.map_m,
                         f"SLAM {mapper.updates}회 · {pose_text(path, args.map_m)}")
                if want_save:
                    want_save.clear()
                    print(f"저장: {save(grid, path, args.map_px, args.map_m, out)}")
                plt.pause(0.1)
    except KeyboardInterrupt:
        pass
    finally:
        mapper.stop.set()
        th.join(timeout=2)
        if mapper.error:
            print(f"\nLiDAR/SLAM 오류: {mapper.error}")
        grid, path = mapper.snapshot()
        lidar.close()
        if path:
            print(f"\n저장: {save(grid, path, args.map_px, args.map_m, out)}  (SLAM {mapper.updates}회)")


if __name__ == "__main__":
    main()
