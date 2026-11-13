# MechDog(ESP32-S3)용 MicroPython: Wi-Fi에 연결해서 모니터로 `dog` 상태를 보낸다.
# 로봇견 안에 secrets.py(Wi-Fi 정보, 모니터 IP)를 저장해 두고, Thonny에서 아래 NAME을 고친 뒤 Run을 누른다. 로봇견의 원래 main.py를
# 백업해 두기 전에는 이 파일을 main.py로 덮어쓰지 말 것.
import network, socket, json, time

try:
    from secrets import WIFI_SSID, WIFI_PASS   # Wi-Fi 정보는 로봇견 안의 secrets.py에 둔다
except ImportError:
    raise RuntimeError("로봇견에 secrets.py가 없습니다. firmware/secrets.py.example을 참고해 Thonny로 저장하세요.")
try:
    from secrets import MONITOR   # 모니터 서버(노트북) 주소도 장소마다 바뀌므로 secrets.py에 둔다
except ImportError:
    raise RuntimeError("secrets.py에 MONITOR = (\"노트북 IP\", 9000) 한 줄을 추가해 로봇견에 다시 저장하세요.")
NAME = "B"                          # A, B 또는 C

# 센서 함수. 본문을 사용 중인 펌웨어에 맞는 Hiwonder MechDog 예제 코드로 바꾼다.
# 그 전까지는 None을 반환하며, 대시보드는 값이 없는 항목을 "—"로 표시한다.
def read_battery():
    return None   # 볼트, 예: 7.6

def read_front_cm():
    return None   # 초음파 거리 (cm)

def read_tilt():
    return None   # "ok" 또는 "fallen"


def connect():
    wlan = network.WLAN(network.STA_IF)
    wlan.active(True)
    if not wlan.isconnected():
        wlan.connect(WIFI_SSID, WIFI_PASS)
        for _ in range(40):
            if wlan.isconnected():
                break
            time.sleep_ms(250)
    if not wlan.isconnected():
        raise RuntimeError("Wi-Fi 연결 실패. 2.4 GHz 네트워크인가요? 비밀번호가 맞나요?")
    print("Wi-Fi 연결됨, 로봇견 IP:", wlan.ifconfig()[0])
    return wlan


def safe(fn, errors):
    try:
        return fn()
    except Exception as e:
        errors[fn.__name__] = str(e)
        return None


wlan = connect()
sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
send = lambda msg: sock.sendto(json.dumps(msg).encode(), MONITOR)
send({"type": "event", "from": NAME, "level": "info",
      "text": "Dog %s 온라인 (%s)" % (NAME, wlan.ifconfig()[0])})

reported = set()
while True:
    errors = {}
    msg = {"type": "dog", "from": NAME, "state": "test",
           "battery_v": safe(read_battery, errors),
           "front_cm": safe(read_front_cm, errors),
           "tilt": safe(read_tilt, errors)}
    try:
        msg["rssi"] = wlan.status("rssi")
    except Exception:
        pass
    send({k: v for k, v in msg.items() if v is not None})
    for name, err in errors.items():   # 고장 난 센서 함수는 한 번씩만 알린다
        if name not in reported:
            reported.add(name)
            send({"type": "event", "from": NAME, "level": "warn", "text": "%s 실패: %s" % (name, err)})
    if not wlan.isconnected():
        wlan = connect()
    time.sleep_ms(100)
