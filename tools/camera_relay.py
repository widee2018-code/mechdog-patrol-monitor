"""로봇견 카메라 모듈의 영상을 모니터 대시보드로 전달한다.

먼저 카메라의 사진 주소를 찾는다:
    python tools/camera_relay.py --probe 192.168.4.1

그다음 전달한다 (스냅샷 URL, MJPEG 스트림 URL 모두 가능):
    python tools/camera_relay.py --url http://192.168.4.1/capture
    python tools/camera_relay.py --url http://192.168.4.1:81/stream --fps 2 --dog A

서버에 INGEST_TOKEN 이 설정돼 있으면 같은 값을 --token 이나 INGEST_TOKEN 환경 변수로 준다.

표준 라이브러리만 사용한다. 지금은 Mac에서, 나중에는 Pi에서 실행한다.
"""
import argparse, base64, json, os, socket, time, urllib.error, urllib.request

# ESP32 카메라 펌웨어가 흔히 쓰는 주소 (Espressif CameraWebServer 및 파생 펌웨어).
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
    print(f"{host} 쪽으로 향하는 이 컴퓨터의 IP: {mine}")
    if mine.rsplit(".", 1)[0] != host.split(":")[0].rsplit(".", 1)[0]:
        print("  ! 카메라와 같은 네트워크가 아닙니다. 먼저 카메라 Wi-Fi에 연결하세요.")
    found = []
    for path in CANDIDATES:
        url = f"http://{host}{path}"
        try:
            with urllib.request.urlopen(url, timeout=3) as r:
                ctype = r.headers.get("Content-Type", "?")
                head = r.read(4096)
        except urllib.error.HTTPError as e:
            print(f"  {url:40s} -- HTTP {e.code} (카메라 응답 있음, 잘못된 경로)")
            continue
        except Exception as e:
            print(f"  {url:40s} -- {getattr(e, 'reason', e)}")
            continue
        kind = ("JPEG 스냅샷" if head[:2] == b"\xff\xd8" else
                "MJPEG 스트림" if "multipart" in ctype else ctype)
        print(f"  {url:40s} OK  {kind}")
        if kind in ("JPEG 스냅샷", "MJPEG 스트림"):
            found.append(url)
    print("\n--url 에 사용할 주소:" if found else "\n사진 주소를 찾지 못했습니다.", *found, sep="\n  ")


def snapshots(url):
    while True:
        with urllib.request.urlopen(url, timeout=5) as r:
            yield r.read()


def mjpeg_frames(url):
    # JPEG 시작/끝 표시로 스트림을 자른다. multipart 경계 문자열과 상관없이 동작한다.
    with urllib.request.urlopen(url, timeout=5) as r:
        buf = b""
        while chunk := r.read(4096):
            buf += chunk
            start = buf.find(b"\xff\xd8")
            end = buf.find(b"\xff\xd9", start + 2)
            if start != -1 and end != -1:
                yield buf[start:end + 2]
                buf = buf[end + 2:]
            elif len(buf) > 2_000_000:   # 2 MB 안에 완전한 프레임이 없으면 버리고 다시 맞춘다
                buf = b""


def frames(url):
    with urllib.request.urlopen(url, timeout=5) as r:
        stream = "multipart" in r.headers.get("Content-Type", "")
    return mjpeg_frames(url) if stream else snapshots(url)


def relay(a):
    ingest = f"http://{a.monitor}/api/ingest"
    print(f"{a.url} -> {ingest}, {a.fps} fps. Ctrl+C로 중지.")
    while True:
        try:
            last = 0.0
            for jpg in frames(a.url):
                if time.time() - last < 1 / a.fps:
                    continue   # MJPEG가 전달 속도보다 빠르므로 남는 프레임은 건너뛴다
                last = time.time()
                msg = {"type": "camera", "from": a.dog, "note": "live",
                       "image": "data:image/jpeg;base64," + base64.b64encode(jpg).decode()}
                headers = {"Content-Type": "application/json"}
                if a.token:
                    headers["Authorization"] = f"Bearer {a.token}"
                req = urllib.request.Request(ingest, json.dumps(msg).encode(), headers)
                urllib.request.urlopen(req, timeout=3).read()
                print(f"\r{len(jpg) // 1024} KB 프레임 전송 {time.strftime('%H:%M:%S')}", end="", flush=True)
        except KeyboardInterrupt:
            return
        except Exception as e:
            print(f"\n{type(e).__name__}: {e}. 2초 후 재시도")
            time.sleep(2)


p = argparse.ArgumentParser()
p.add_argument("--probe", metavar="HOST", help="이 호스트에서 흔한 카메라 주소들을 시험")
p.add_argument("--url", help="스냅샷 또는 MJPEG 스트림 URL")
p.add_argument("--monitor", default="127.0.0.1:8000", help="모니터 서버 host:port")
p.add_argument("--dog", default="A")
p.add_argument("--fps", type=float, default=2)
p.add_argument("--token", default=os.environ.get("INGEST_TOKEN"), help="서버의 INGEST_TOKEN (설정된 경우)")  # allow-secret: 환경 변수 이름일 뿐
a = p.parse_args()

if a.probe:
    probe(a.probe)
elif a.url:
    relay(a)
else:
    p.print_help()
