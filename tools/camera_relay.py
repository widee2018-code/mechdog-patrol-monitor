"""Forward frames from a dog's camera module to the monitor dashboard.

Find the camera's picture address first:
    python tools/camera_relay.py --probe 192.168.4.1

Then relay it (a snapshot URL or an MJPEG stream URL both work):
    python tools/camera_relay.py --url http://192.168.4.1/capture
    python tools/camera_relay.py --url http://192.168.4.1:81/stream --fps 2 --dog A

Uses only the standard library. Runs on the Mac now, on the Pi later.
"""
import argparse, base64, json, socket, time, urllib.error, urllib.request

# Endpoints commonly used by ESP32 camera firmware (Espressif CameraWebServer and derivatives).
CANDIDATES = ["/capture", "/jpg", "/snapshot", "/cam-hi.jpg", "/cam-lo.jpg", ":81/stream", "/stream", "/mjpeg", "/"]


def local_ip_towards(host):
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect((host.split(":")[0], 9))
        return s.getsockname()[0]
    except OSError:
        return "?"
    finally:
        s.close()


def probe(host):
    host = host.removeprefix("http://").rstrip("/")
    mine = local_ip_towards(host)
    print(f"This computer's IP towards {host}: {mine}")
    if mine.rsplit(".", 1)[0] != host.split(":")[0].rsplit(".", 1)[0]:
        print("  ! Not on the same network as the camera. Join the camera's Wi-Fi first.")
    found = []
    for path in CANDIDATES:
        url = f"http://{host}{path}"
        try:
            with urllib.request.urlopen(url, timeout=3) as r:
                ctype = r.headers.get("Content-Type", "?")
                head = r.read(4096)
        except urllib.error.HTTPError as e:
            print(f"  {url:40s} -- HTTP {e.code} (camera answered, wrong path)")
            continue
        except Exception as e:
            print(f"  {url:40s} -- {getattr(e, 'reason', e)}")
            continue
        kind = ("JPEG snapshot" if head[:2] == b"\xff\xd8" else
                "MJPEG stream" if "multipart" in ctype else ctype)
        print(f"  {url:40s} OK  {kind}")
        if kind in ("JPEG snapshot", "MJPEG stream"):
            found.append(url)
    print("\nUse one with --url:" if found else "\nNo picture address found.", *found, sep="\n  ")


def snapshots(url):
    while True:
        with urllib.request.urlopen(url, timeout=5) as r:
            yield r.read()


def mjpeg_frames(url):
    # Split the stream on JPEG start/end markers; works regardless of the multipart boundary.
    with urllib.request.urlopen(url, timeout=5) as r:
        buf = b""
        while chunk := r.read(4096):
            buf += chunk
            start = buf.find(b"\xff\xd8")
            end = buf.find(b"\xff\xd9", start + 2)
            if start != -1 and end != -1:
                yield buf[start:end + 2]
                buf = buf[end + 2:]
            elif len(buf) > 2_000_000:   # no complete frame in 2 MB: drop and resync
                buf = b""


def frames(url):
    with urllib.request.urlopen(url, timeout=5) as r:
        stream = "multipart" in r.headers.get("Content-Type", "")
    return mjpeg_frames(url) if stream else snapshots(url)


def relay(a):
    ingest = f"http://{a.monitor}/api/ingest"
    print(f"{a.url} -> {ingest} at {a.fps} fps. Ctrl+C to stop.")
    while True:
        try:
            last = 0.0
            for jpg in frames(a.url):
                if time.time() - last < 1 / a.fps:
                    continue   # MJPEG runs faster than we forward; skip extra frames
                last = time.time()
                msg = {"type": "camera", "from": a.dog, "note": "live",
                       "image": "data:image/jpeg;base64," + base64.b64encode(jpg).decode()}
                req = urllib.request.Request(ingest, json.dumps(msg).encode(), {"Content-Type": "application/json"})
                urllib.request.urlopen(req, timeout=3).read()
                print(f"\rsent {len(jpg) // 1024} KB frame at {time.strftime('%H:%M:%S')}", end="", flush=True)
        except KeyboardInterrupt:
            return
        except Exception as e:
            print(f"\n{type(e).__name__}: {e}. Retrying in 2 s")
            time.sleep(2)


p = argparse.ArgumentParser()
p.add_argument("--probe", metavar="HOST", help="try common camera addresses on this host")
p.add_argument("--url", help="snapshot or MJPEG stream URL")
p.add_argument("--monitor", default="127.0.0.1:8000", help="monitor server host:port")
p.add_argument("--dog", default="A")
p.add_argument("--fps", type=float, default=2)
a = p.parse_args()

if a.probe:
    probe(a.probe)
elif a.url:
    relay(a)
else:
    p.print_help()
