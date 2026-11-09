"""실제 하드웨어를 쓰기 전에 서버를 확인하려고 모니터에 가짜 `dog` 메시지를 보낸다.

    python tools/send_test.py                 # Dog B가 작은 원을 그리며 이동
    python tools/send_test.py --host 192.168.0.11 --dog A
    python tools/send_test.py --x 1.2 --y 0.8 # 위치만 전송, 예: 실제 로봇견을 지도에 표시
"""
import argparse, json, math, socket, time

p = argparse.ArgumentParser()
p.add_argument("--host", default="127.0.0.1")
p.add_argument("--port", type=int, default=9000)
p.add_argument("--dog", default="B")
p.add_argument("--x", type=float)
p.add_argument("--y", type=float)
a = p.parse_args()

sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
send = lambda m: sock.sendto(json.dumps(m).encode(), (a.host, a.port))

if a.x is not None and a.y is not None:
    # 위치만 반복해서 보내서 표시가 사라지지 않게 한다. Ctrl+C로 중지.
    print(f"Dog {a.dog}를 ({a.x}, {a.y})에 고정 중. Ctrl+C로 중지.")
    while True:
        send({"type": "dog", "from": a.dog, "x": a.x, "y": a.y, "heading_deg": 90})
        time.sleep(0.5)

send({"type": "event", "from": "test", "level": "info", "text": f"send_test.py가 {a.host}:{a.port} 로 전송 중"})
print(f"Dog {a.dog} 데이터를 {a.host}:{a.port} 로 전송 중. Ctrl+C로 중지.")
t0 = time.time()
while True:
    t = time.time() - t0
    send({"type": "dog", "from": a.dog, "x": 3 + math.cos(t / 4), "y": 2 + math.sin(t / 4),
          "heading_deg": math.degrees(t / 4) + 90, "state": "test", "battery_v": 7.6,
          "front_cm": int(60 + 30 * math.sin(t)), "tilt": "ok"})
    time.sleep(0.1)
