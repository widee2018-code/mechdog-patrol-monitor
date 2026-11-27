"""허브의 마지막 SLAM 지도 + 초음파 장애물 + 로봇견 위치를 대시보드 기본 화면(web/sample_map.json)으로 저장.

대시보드는 실제 데이터(지도·LiDAR·로봇견 위치)가 하나도 안 들어왔을 때만 이 파일을 보여 준다.
탐사를 끝낸 뒤(허브는 켜 둔 채) 한 번 실행하면, 다음에 허브를 켰을 때 그 지도가 기본으로 보인다.

    python tools/snapshot_map.py                      # 허브 127.0.0.1:8000 에서 읽어 web/sample_map.json 에 저장
    python tools/snapshot_map.py --hub 192.168.0.10   # 다른 컴퓨터의 허브
    python tools/snapshot_map.py --no-dogs            # 로봇견 위치는 빼고 지도·장애물만
    python tools/snapshot_map.py --dry-run            # 저장하지 않고 무엇이 들어갈지만 보기

저장하기 전에 원래 파일은 web/sample_map.prev.json 으로 남긴다 (되돌리려면 그 파일을 sample_map.json 으로).
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DOGS = ("A", "B", "C")


def main() -> None:
    ap = argparse.ArgumentParser(description="허브의 마지막 지도를 대시보드 기본 화면으로 저장")
    ap.add_argument("--hub", default="127.0.0.1", help="허브 IP")
    ap.add_argument("--port", type=int, default=8000)
    ap.add_argument("--out", default=str(ROOT / "web" / "sample_map.json"))
    ap.add_argument("--no-dogs", action="store_true", help="로봇견 마지막 위치는 넣지 않음")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()

    url = f"http://{a.hub}:{a.port}/api/state"
    try:
        with urllib.request.urlopen(url, timeout=3) as r:
            latest = json.load(r)["latest"]
    except Exception as e:
        sys.exit(f"허브를 못 읽음 ({url}): {e}\n허브(python tools/demo.py hub)가 켜져 있고, 탐사를 한 번 돌린 뒤에 실행하세요.")

    maps = [m for m in latest.values() if m.get("type") == "map" and m.get("png")]
    if not maps:
        sys.exit("허브에 SLAM 지도가 없음: frontier_drive.py 나 lidar_slam.py 를 --hub 로 돌린 뒤 (허브를 끄기 전에) 실행하세요.")
    m = max(maps, key=lambda x: x.get("ts", 0))
    when = time.strftime("%m-%d %H:%M", time.localtime(m.get("ts", time.time())))

    sonar = []
    for ex in latest.values():
        if ex.get("type") == "explore":
            sonar += ex.get("sonar") or []

    dogs = {}
    if not a.no_dogs:
        for n in DOGS:
            d = latest.get(f"dog:{n}") or {}
            if isinstance(d.get("x"), (int, float)) and isinstance(d.get("y"), (int, float)):
                dogs[n] = [round(d["x"], 3), round(d["y"], 3), round(d.get("heading_deg") or 0.0, 1)]

    out = {
        "_comment": f"Dashboard default view (shown only until real data arrives). Saved by tools/snapshot_map.py from the hub at {when}: "
                    "last C1 SLAM map, ultrasonic obstacle marks, last dog positions.",
        "label": f"지난 탐사 지도 · {when} · 실시간 아님",
        "meta": f"지난 탐사 지도 ({when} SLAM 지도 + 초음파 장애물 {len(sonar)}점"
                + (f" + 로봇견 {', '.join(dogs)} 마지막 위치" if dogs else "") + ") · 로봇견·LiDAR가 연결되면 실제 데이터로 바뀜",
        "map": {k: m[k] for k in ("res", "w", "h", "origin", "rot", "png")},
        "sonar": sonar,
        "dogs": dogs,
    }
    print(f"지도 {when} · {m['w']}×{m['h']} 칸 ({m['res']} m) · 초음파 점 {len(sonar)}개 · 로봇견 {list(dogs) or '없음'}")
    if a.dry_run:
        print("(--dry-run: 저장 안 함)")
        return
    path = Path(a.out)
    if path.exists():
        shutil.copyfile(path, path.with_suffix(".prev.json"))
    path.write_text(json.dumps(out, ensure_ascii=False, indent=1) + "\n")
    print(f"저장: {path}  (이전 파일: {path.with_suffix('.prev.json').name}) · 대시보드를 새로고침하면 실제 데이터가 없을 때 보임")


if __name__ == "__main__":
    main()
