"""키보드로 로봇견을 조종한다 (firmware/dog_cmd.py가 실행 중이어야 함).

    python brain/teleop.py --dog 192.168.0.30

작은 창이 뜨면 그 창을 클릭한 상태에서 키를 누른다.
    w 전진   s 후진   a 왼쪽 회전   d 오른쪽 회전   q/e 전진하며 왼쪽/오른쪽
    스페이스 정지   1~3 속도 단계   x 종료
마지막 키를 0.3초 넘게 누르지 않으면 멈춘다. 처음에는 a/d로 회전 방향 부호를 확인하고,
반대로 돌면 --turn-sign -1 로 실행한다 (marker_follow.py에도 같은 값을 쓴다).
"""
import argparse
import json
import socket
import time

import cv2
import numpy as np

CMD_PORT = 9100

p = argparse.ArgumentParser()
p.add_argument("--dog", required=True, help="로봇견 IP")
p.add_argument("--turn-sign", type=int, choices=(1, -1), default=1)
a = p.parse_args()

SPEEDS = {ord("1"): 60, ord("2"): 90, ord("3"): 120}
TURN = 40
KEYS = {  # 키 -> (전진 방향, 회전 방향). 회전 +1 = 오른쪽
    ord("w"): (1, 0), ord("s"): (-1, 0), ord("a"): (0, -1), ord("d"): (0, 1),
    ord("q"): (1, -1), ord("e"): (1, 1), ord(" "): (0, 0),
}

sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
send = lambda v, t: sock.sendto(json.dumps({"cmd": "move", "v": v, "turn": t}).encode(), (a.dog, CMD_PORT))
speed, held, last_key = 90, (0, 0), 0.0
print(f"로봇견 {a.dog}:{CMD_PORT} 조종. 창을 클릭하고 w/a/s/d, 스페이스=정지, x=종료")

try:
    while True:
        key = cv2.waitKey(100) & 0xFF
        now = time.time()
        if key == ord("x"):
            break
        if key in SPEEDS:
            speed = SPEEDS[key]
        elif key in KEYS:
            held, last_key = KEYS[key], now
        if now - last_key > 0.3:   # 키 반복 입력이 끊기면 정지
            held = (0, 0)
        fwd, rot = held
        v = speed * fwd if fwd >= 0 else -40   # 후진은 원래 앱처럼 느리게
        t = a.turn_sign * TURN * rot
        send(v, t)

        panel = np.full((120, 360, 3), 245, np.uint8)
        cv2.putText(panel, f"v={v:4d}  turn={t:4d}", (12, 40), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 0, 0), 2)
        cv2.putText(panel, f"speed level {speed}  (1/2/3)", (12, 75), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (90, 90, 90), 1)
        cv2.putText(panel, "w a s d  q e  space=stop  x=quit", (12, 105), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (90, 90, 90), 1)
        cv2.imshow(f"teleop {a.dog}", panel)
finally:
    for _ in range(3):
        send(0, 0)
        time.sleep(0.05)
    print("종료: 정지 명령 전송")
