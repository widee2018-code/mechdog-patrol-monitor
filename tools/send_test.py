"""Send fake `dog` messages to the monitor, to check the server before using real hardware.

    python tools/send_test.py                 # Dog B walking a small circle
    python tools/send_test.py --host 192.168.0.11 --dog A
    python tools/send_test.py --x 1.2 --y 0.8 # position only, e.g. to place a real dog on the map
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
    # Position only, sent repeatedly so the marker doesn't go stale. Ctrl+C to stop.
    print(f"Holding Dog {a.dog} at ({a.x}, {a.y}). Ctrl+C to stop.")
    while True:
        send({"type": "dog", "from": a.dog, "x": a.x, "y": a.y, "heading_deg": 90})
        time.sleep(0.5)

send({"type": "event", "from": "test", "level": "info", "text": f"send_test.py talking to {a.host}:{a.port}"})
print(f"Sending Dog {a.dog} to {a.host}:{a.port}. Ctrl+C to stop.")
t0 = time.time()
while True:
    t = time.time() - t0
    send({"type": "dog", "from": a.dog, "x": 3 + math.cos(t / 4), "y": 2 + math.sin(t / 4),
          "heading_deg": math.degrees(t / 4) + 90, "state": "test", "battery_v": 7.6,
          "front_cm": int(60 + 30 * math.sin(t)), "tilt": "ok"})
    time.sleep(0.1)
