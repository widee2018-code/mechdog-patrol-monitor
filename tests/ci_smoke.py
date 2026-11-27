"""CI 코드 검사 (.github/workflows/code-check.yml). 로봇·C1·BreezySLAM 없이 몇 분 안에 끝나는 것만.

    python tests/ci_smoke.py            # 전부
    python tests/ci_smoke.py hub drive  # 고른 것만 (hub, sim, drive)

검사
- hub:   공개 주소(cf-connecting-ip)에서 미션·사이렌·촬영·/api/ingest 는 403, 공유기 안 /api/ingest 는 200, 미션이 안 바뀜
- sim:   brain/frontier_sim.py 창 없이 → "탐사 완료", 지도 95% 이상
- drive: brain/frontier_drive.Driver 를 시뮬레이터 세상에서 (실물 frontier_drive 와 같은 상태 기계)
         자동 탐사 완료 · 제자리에서 이어서 도는 각 ≤ 50° (45° 씩 쉬기) · --confirm 제안마다 y 로 완료 ·
         C1 에 안 보이는 상자가 있어도 초음파로 피해서 완료 (막혀서 목표 제외 0번)
실패하면 무엇이 틀렸는지 출력하고 종료 코드 1.
"""

from __future__ import annotations

import math
import os
import re
import subprocess
import sys
import tempfile
import time
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "brain"))
os.environ.setdefault("MPLBACKEND", "Agg")

FAILS: list[str] = []


def check(ok: bool, what: str) -> None:
    print(f"  {'✅' if ok else '❌'} {what}")
    if not ok:
        FAILS.append(what)


# ── hub ────────────────────────────────────────────────────────────

def test_hub() -> None:
    print("hub: 공개 주소에서 조작 막기")
    from fastapi.testclient import TestClient
    import server.app as hub

    pub = {"cf-connecting-ip": "203.0.113.7"}
    with TestClient(hub.app) as c:
        for path in ("/api/mission/start", "/api/mission/contain", "/api/mission/standby", "/api/alarm/ack", "/api/snapshot"):
            r = c.post(path, headers=pub, json={})
            check(r.status_code == 403, f"공개 {path} → {r.status_code} (403 이어야)")
        r = c.post("/api/ingest", headers=pub, json={"type": "mission", "from": "ci", "state": "EXPLORE"})
        check(r.status_code == 403, f"공개 /api/ingest → {r.status_code} (403 이어야)")
        check(c.get("/api/dogs").json()["mission"] == "STANDBY", "공개 ingest 뒤에도 미션 STANDBY")
        r = c.post("/api/ingest", json={"type": "event", "from": "ci", "level": "info", "text": "ci"})
        check(r.status_code == 200, f"공유기 안 /api/ingest → {r.status_code} (200 이어야)")


# ── frontier_sim ───────────────────────────────────────────────────

def test_sim() -> None:
    print("sim: frontier 시뮬레이터 (창 없이)")
    with tempfile.TemporaryDirectory() as out:
        p = subprocess.run([sys.executable, str(ROOT / "brain" / "frontier_sim.py"), "--no-window", "--out", out],
                           capture_output=True, text=True, timeout=300)
    m = re.search(r"탐사 완료: (\d+)초, 목표 (\d+)개.*지도 (\d+)%", p.stdout)
    check(p.returncode == 0, f"종료 코드 {p.returncode}" + (f"\n{p.stderr[-800:]}" if p.returncode else ""))
    check(bool(m), "\"탐사 완료\" 출력")
    if m:
        check(int(m.group(3)) >= 95, f"지도 {m.group(3)}% (95% 이상)")


# ── frontier_drive.Driver ──────────────────────────────────────────

def drive(confirm=False, boxes=(), seconds=1500.0, maxm=8.0):
    """실물과 같은 Driver 를 시뮬레이터 세상에서. boxes = C1 에 안 보이고 몸·초음파만 막는 상자 (x, y, w, h) m."""
    import numpy as np
    import frontier_sim as fs
    from frontier import Planner
    from frontier_drive import Driver

    world = fs.default_world()
    body = world.copy()
    for bx, by, w, h in boxes:
        body[int(by / fs.CELL):int((by + h) / fs.CELL), int(bx / fs.CELL):int((bx + w) / fs.CELL)] = True
    known = np.full((fs.MAP_PX, fs.MAP_PX), fs.UNKNOWN, np.uint8)
    lidar, rb = fs.Lidar(world, max_m=maxm), fs.Robot(world, *fs.START, 0.0)
    a = types.SimpleNamespace(   # frontier_drive.py 기본값과 같게
        settle=1.5, v_ms=0.057, reach=0.12, step=0.4, turn=40, walk_turn=20, k_turn=1.0, speed=90, stuck_sec=6,
        max_fails=3, confirm=confirm, turn_chunk=45, turn_pause=2.0, arc_deg=100, arc_room=0.7, arc_turn=30,
        sonar_mark=45, sonar_slow=40, sonar_slow_speed=60, sonar_stop=25, sonar_back=0.12, sonar_offset=0.15,
        sonar_width=0.1, sonar_keep=300)
    logs, t, dt = [], 0.0, 0.1
    drv = Driver(Planner(cell_m=fs.CELL, radius=0.15), a, log=lambda text, lv="info": logs.append(text))
    lidar.update(known, rb.x, rb.y, rb.th)
    drv.start(0)
    spin = max_spin = 0.0

    def sonar_cm():
        hx, hy = rb.x + 0.15 * math.cos(rb.th), rb.y + 0.15 * math.sin(rb.th)
        for d in np.arange(0.0, 3.0, 0.01):
            if body[int((hy + d * math.sin(rb.th)) / fs.CELL), int((hx + d * math.cos(rb.th)) / fs.CELL)]:
                return d * 100
        return 6142.0

    while t < seconds and drv.state not in ("done", "blocked"):
        if drv.state == "propose":
            drv.accept(t)
        cm = sonar_cm()
        v, left = drv.step(t, rb.x, rb.y, rb.th, lambda: known, front_cm=cm)
        if v > 0 and cm < 20:
            v = 0                                    # 펌웨어 STOP_CM
        dth = math.radians(13) * left / 40 * dt     # 실측 회전 13°/s (회전 값 40)
        rb.th += dth
        spin = spin + abs(dth) if (left and not v) else 0.0
        max_spin = max(max_spin, spin)
        s = 0.057 * v / 90 * dt                      # 실측 속도 0.057 m/s (속도 90)
        nx, ny = rb.x + s * math.cos(rb.th), rb.y + s * math.sin(rb.th)
        if not body[int(ny / fs.CELL), int(nx / fs.CELL)]:
            rb.x, rb.y = nx, ny
        lidar.update(known, rb.x, rb.y, rb.th)
        t += dt
    return t, drv, math.degrees(max_spin), logs


def test_drive() -> None:
    print("drive: frontier_drive.Driver (시뮬레이터 세상)")
    t0 = time.time()
    t, d, spin, _ = drive()
    check(d.state == "done", f"자동 탐사: {d.state}, {t:.0f} s, 목표 {d.goals}개 (done 이어야)")
    check(spin <= 50, f"제자리에서 이어서 돈 최대 {spin:.0f}° (50° 이하, 45° 씩 쉬기)")
    t, d, _, logs = drive(confirm=True, maxm=1.5)
    asks = sum("제안" in x and "→ y" in x for x in logs)
    check(d.state == "done" and asks >= 5, f"--confirm max 1.5 제안마다 y: {d.state}, {t:.0f} s, 제안 {asks}번")
    t, d, _, logs = drive(boxes=[(6.75, 5.75, 0.2, 0.5), (8.3, 5.5, 0.25, 0.3)])
    drops = sum("제외" in x for x in logs)
    sonar = sum("초음파 앞" in x for x in logs)
    check(d.state == "done" and drops == 0 and sonar > 0,
          f"C1 에 안 보이는 상자 2개: {d.state}, {t:.0f} s, 초음파 반응 {sonar}번, 목표 제외 {drops}번 (done·0 이어야)")
    print(f"  ({time.time() - t0:.0f} s)")


TESTS = {"hub": test_hub, "sim": test_sim, "drive": test_drive}

if __name__ == "__main__":
    for name in sys.argv[1:] or list(TESTS):
        TESTS[name]()
    print("\n" + (f"실패 {len(FAILS)}개:\n  - " + "\n  - ".join(FAILS) if FAILS else "모두 통과"))
    sys.exit(1 if FAILS else 0)
