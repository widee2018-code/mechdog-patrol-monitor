"""안전 반사 시험 (WBS 4.1.2) 통신 두절: 공유기를 끄지 않고 '갑자기 끊김'을 흉내 낸다.

공유기가 꺼지면 두 가지가 같이 일어난다. 둘을 따로 재고 결과를 표로 보여 준다.
  ① 로봇견 쪽 (--part dog): 명령이 안 옴 → 펌웨어 워치독(dog_cmd.py WATCHDOG_MS 1초)이 세우고 state "idle".
     실제 로봇견에 move 를 0.1초마다 보내 걷게 한 뒤 갑자기 끊고, 허브에 오는 로봇견 상태로 멈춘 시각을 잰다.
  ② 허브 쪽 (--part hub): 로봇견 상태가 안 옴 → 허브가 DOG_TIMEOUT(3초) 뒤 "Dog X 오프라인" 경보.
     가짜 로봇견(기본 C, 실제로 안 쓰는 이름)으로 상태를 보내다가 끊고, 경보가 뜨는 시각을 잰다. 로봇견 필요 없음.

    python tools/link_cut_test.py --dog 192.168.0.18 --name B          # ①·② 둘 다, 3번씩
    python tools/link_cut_test.py --part hub                           # ② 만 (로봇견 없이)
    python tools/link_cut_test.py --dog 192.168.0.18 --name B --part dog --trials 5

준비: 허브 켜기 (python tools/demo.py hub), 로봇견 dog_cmd.py Run, 앞 1 m 비우기 (걷는 속도는 --speed, 기본 60 = 천천히).
Ctrl+C 로 언제든 멈추면 정지 명령을 보낸다.
"""

from __future__ import annotations

import argparse
import json
import socket
import sys
import time
import urllib.request

CMD_PORT = 9100      # firmware/dog_cmd.py
HUB_UDP = 9000
WATCHDOG_S = 1.0     # firmware/dog_cmd.py WATCHDOG_MS
DOG_TIMEOUT = 3.0    # server/app.py


def get(url: str):
    with urllib.request.urlopen(url, timeout=1) as r:
        return json.load(r)


def dog_state(hub: str, port: int, name: str):
    """(state, 로봇견이 마지막으로 직접 보낸 지 몇 초)"""
    try:
        b = get(f"http://{hub}:{port}/api/dogs")
    except Exception:
        return None, None
    d = b["dogs"].get(name, {})
    age = b["now"] - d["self_ts"] if "self_ts" in d else None
    return d.get("state"), age


def part_dog(a, sock) -> list[dict]:
    def move(v):
        sock.sendto(json.dumps({"cmd": "move", "v": v, "turn": 0}).encode(), (a.dog, CMD_PORT))

    st, age = dog_state(a.hub, a.port, a.name)
    if age is None or age > 2:
        sys.exit(f"허브에 Dog {a.name} 상태가 안 옴 (dog_cmd.py Run, --name, 허브 확인)")
    rows = []
    for i in range(1, a.trials + 1):
        print(f"\n① 로봇견 {i}/{a.trials}: {a.walk:.0f}초 걷기 (속도 {a.speed}) …")
        t_end, moving_seen = time.time() + a.walk, False
        while time.time() < t_end:
            move(a.speed)
            st, _ = dog_state(a.hub, a.port, a.name)
            moving_seen |= st == "moving"
            if st in ("blocked", "fallen"):
                break
            time.sleep(0.1)
        if not moving_seen or st != "moving":
            sock.sendto(json.dumps({"cmd": "stop"}).encode(), (a.dog, CMD_PORT))
            print(f"   걷는 상태(moving)가 아님 (마지막 state={st}) → 이번 회는 건너뜀 (앞 장애물·넘어짐 확인)")
            rows.append({"n": i, "ok": False, "stop_s": None, "state": st})
            time.sleep(2)
            continue
        t_cut = time.time()          # 여기서 '통신 끊김': 명령을 더 안 보냄
        print("   ✂ 명령 끊음")
        stop_s, final = None, st
        while time.time() - t_cut < 4:
            st, _ = dog_state(a.hub, a.port, a.name)
            if st and st != "moving":
                stop_s, final = time.time() - t_cut, st
                break
            time.sleep(0.03)
        ok = final == "idle" and stop_s is not None and stop_s <= WATCHDOG_S + a.margin
        print(f"   → {'멈춤' if stop_s else '안 멈춤'} {stop_s or 0:.2f} s 뒤, state={final}  {'✅' if ok else '❌'}")
        rows.append({"n": i, "ok": ok, "stop_s": stop_s, "state": final})
        time.sleep(2)
    sock.sendto(json.dumps({"cmd": "stop"}).encode(), (a.dog, CMD_PORT))
    return rows


def part_hub(a, sock) -> list[dict]:
    rows = []
    for i in range(1, a.trials + 1):
        print(f"\n② 허브 {i}/{a.trials}: 가짜 Dog {a.fake} 상태 3초 보내기 …")
        t_end = time.time() + 3
        while time.time() < t_end:
            # 로봇견이 직접 보낸 것처럼 front_cm·rssi 포함 (허브가 self_ts 로 기록)
            sock.sendto(json.dumps({"type": "dog", "from": a.fake, "state": "idle", "front_cm": 6142, "rssi": -50,
                                    "ts": round(time.time(), 3)}).encode(), (a.hub, HUB_UDP))
            time.sleep(0.1)
        t_cut = time.time()
        print("   ✂ 상태 끊음")
        alarm_s = None
        while time.time() - t_cut < DOG_TIMEOUT + 3:
            try:
                ev = get(f"http://{a.hub}:{a.port}/api/state")["events"]
            except Exception:
                ev = []
            hit = [e for e in ev if e.get("ts", 0) >= t_cut and f"Dog {a.fake} 오프라인" in str(e.get("text"))]
            if hit:
                alarm_s = hit[0]["ts"] - t_cut
                break
            time.sleep(0.1)
        ok = alarm_s is not None and alarm_s <= DOG_TIMEOUT + 1.0
        print(f"   → {'오프라인 경보' if alarm_s else '경보 없음'} {alarm_s or 0:.2f} s 뒤  {'✅' if ok else '❌'}")
        rows.append({"n": i, "ok": ok, "alarm_s": alarm_s})
    return rows


def main() -> None:
    ap = argparse.ArgumentParser(description="통신 두절 안전 반사 시험 (공유기 끄기 대신)")
    ap.add_argument("--part", choices=("dog", "hub", "both"), default="both")
    ap.add_argument("--dog", help="로봇견 IP (① 에 필요)")
    ap.add_argument("--name", default="A", help="로봇견 이름 (secrets.py NAME)")
    ap.add_argument("--hub", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8000)
    ap.add_argument("--trials", type=int, default=3)
    ap.add_argument("--walk", type=float, default=3.0, help="끊기 전에 걷는 시간 초")
    ap.add_argument("--speed", type=int, default=60, help="걷는 속도 (teleop 1 = 60)")
    ap.add_argument("--margin", type=float, default=0.5,
                    help="① 합격 기준: 워치독 1초 + 이만큼 (상태 전송 0.1초·허브 읽기 지연 포함)")
    ap.add_argument("--fake", default="C", help="② 가짜 로봇견 이름 (지금 안 쓰는 이름)")
    a = ap.parse_args()
    if a.part in ("dog", "both") and not a.dog:
        sys.exit("① 로봇견 시험에는 --dog IP 가 필요합니다 (로봇견 없이: --part hub)")
    if a.part in ("hub", "both") and a.fake == a.name and a.part == "both":
        sys.exit("--fake 는 실제 로봇견 이름과 달라야 합니다")

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    dog_rows, hub_rows = [], []
    try:
        if a.part in ("dog", "both"):
            dog_rows = part_dog(a, sock)
        if a.part in ("hub", "both"):
            hub_rows = part_hub(a, sock)
    except KeyboardInterrupt:
        print("\n중단")
    finally:
        if a.dog:
            for _ in range(3):
                sock.sendto(json.dumps({"cmd": "stop"}).encode(), (a.dog, CMD_PORT))

    print("\n=== 결과 (WBS 4.1.2 통신 두절) ===")
    if dog_rows:
        print(f"① 로봇견 워치독 (기준: state idle, {WATCHDOG_S + a.margin:.1f} s 안)")
        for r in dog_rows:
            print(f"   {r['n']}회: {'%.2f s' % r['stop_s'] if r['stop_s'] else '-'}  state={r['state']}  {'✅' if r['ok'] else '❌'}")
    if hub_rows:
        print(f"② 허브 오프라인 경보 (기준: {DOG_TIMEOUT + 1:.0f} s 안)")
        for r in hub_rows:
            print(f"   {r['n']}회: {'%.2f s' % r['alarm_s'] if r['alarm_s'] else '-'}  {'✅' if r['ok'] else '❌'}")
    allr = dog_rows + hub_rows
    if allr:
        print(f"합계: {sum(r['ok'] for r in allr)}/{len(allr)} 통과")


if __name__ == "__main__":
    main()
