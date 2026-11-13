# MechDog(ESP32-S3)용 MicroPython: Pi/노트북이 보내는 move 명령을 Wi-Fi(UDP)로 받아 걷는다.
# brain/marker_follow.py 같은 행동 알고리즘이 이 로봇견을 조종할 때 쓴다.
# 안전 반사는 로봇견 안에서 돈다: 1초간 명령이 없으면 정지, 앞이 막혔으면 전진 금지.
# 상태(초음파 거리, Wi-Fi 세기)는 dog_status.py처럼 모니터로 보낸다.
#
# 로봇견 안에 secrets.py(Wi-Fi 정보, 모니터 IP, 이 개의 이름)를 저장해 두고, Thonny에서 Run을 누른다. 원래 main.py를 백업해 두기 전에는
# 이 파일을 main.py로 덮어쓰지 말 것.
#
# 명령 형식 (UDP 9100):  {"cmd": "move", "v": 80, "turn": -20}   {"cmd": "stop"}
#   v    : 전진(+)/후진(-) 속도. 원래 앱 코드는 전진 80~120, 후진 -40을 쓴다.
#   turn : 회전. 원래 앱 코드는 -50~50을 쓴다. 어느 부호가 왼쪽인지는 처음에 확인할 것.
import network, socket, json, time, gc
import Hiwonder_IIC
from HW_MechDog import MechDog

try:
    from secrets import WIFI_SSID, WIFI_PASS   # Wi-Fi 정보는 로봇견 안의 secrets.py에 둔다
except ImportError:
    raise RuntimeError("로봇견에 secrets.py가 없습니다. firmware/secrets.py.example을 참고해 Thonny로 저장하세요.")
try:
    from secrets import MONITOR   # 모니터 서버(노트북) 주소도 장소마다 바뀌므로 secrets.py에 둔다
except ImportError:
    raise RuntimeError("secrets.py에 MONITOR = (\"노트북 IP\", 9000) 한 줄을 추가해 로봇견에 다시 저장하세요.")
try:
    from secrets import NAME      # 로봇견 이름(A, B, C)은 개마다 다르므로 그 개의 secrets.py에 둔다
except ImportError:
    raise RuntimeError("secrets.py에 NAME = \"B\"처럼 이 로봇견 이름(A, B, C) 한 줄을 추가해 다시 저장하세요.")
CMD_PORT = 9100

WATCHDOG_MS = 1000   # 이 시간 동안 명령이 없으면 정지
STOP_CM = 15         # 앞 장애물이 이보다 가까우면 전진 명령을 무시 (후진과 회전은 허용)
MAX_SPEED = 120
MAX_TURN = 50

dog = MechDog()
sonar = Hiwonder_IIC.I2CSonar(Hiwonder_IIC.IIC(1))


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


def clamp(v, lim):
    return max(-lim, min(lim, int(v)))


def led(r, g, b):
    # 초음파 센서의 RGB LED로 상태 표시. 실패해도 걷기에는 영향 없게 한다.
    try:
        sonar.setRGB(0, r, g, b)
    except Exception:
        pass


wlan = connect()
ip = wlan.ifconfig()[0]
mon = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)


def send(msg):
    # 상태 전송은 실패해도 된다. Wi-Fi가 느리면 전송 버퍼가 차서 OSError(ENOMEM)가 나는데,
    # 이걸로 프로그램이 죽으면 워치독도 같이 멈추므로 메시지 하나만 버리고 계속 돈다.
    try:
        mon.sendto(json.dumps(msg).encode(), MONITOR)
    except OSError:
        gc.collect()


cmd_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
cmd_sock.bind(("0.0.0.0", CMD_PORT))
cmd_sock.settimeout(0.05)

dog.set_default_pose(duration=500)
time.sleep(1)
send({"type": "event", "from": NAME, "level": "info",
      "text": "Dog %s 명령 대기 (%s:%d)" % (NAME, ip, CMD_PORT)})
print("명령 대기:", ip, CMD_PORT)

want = (0, 0)          # 마지막으로 받은 명령
current = None         # 실제로 dog.move에 넘긴 값
last_cmd = time.ticks_ms() - WATCHDOG_MS
last_report = 0
front_cm = None
led_state = None

try:
    while True:
        try:
            data, _ = cmd_sock.recvfrom(256)
            msg = json.loads(data)
            if msg.get("cmd") == "move":
                want = (clamp(msg.get("v", 0), MAX_SPEED), clamp(msg.get("turn", 0), MAX_TURN))
                last_cmd = time.ticks_ms()
            elif msg.get("cmd") == "stop":
                want = (0, 0)
                last_cmd = time.ticks_ms()
        except OSError:
            pass            # 0.05초 동안 명령 없음
        except ValueError:
            pass            # 깨진 JSON은 무시

        now = time.ticks_ms()
        if time.ticks_diff(now, last_report) >= 100:
            try:
                front_cm = sonar.getDistance()
            except Exception:
                front_cm = None

        v, turn = want
        if time.ticks_diff(now, last_cmd) > WATCHDOG_MS:
            v, turn, state = 0, 0, "idle"
        elif v > 0 and front_cm is not None and front_cm < STOP_CM:
            v, state = 0, "blocked"
        else:
            state = "moving" if (v, turn) != (0, 0) else "stopped"

        if (v, turn) != current:
            dog.move(v, turn)
            current = (v, turn)
        colors = {"idle": (0, 0, 0x40), "blocked": (0xff, 0, 0), "moving": (0, 0xff, 0), "stopped": (0xff, 0xcc, 0)}
        if state != led_state:
            led(*colors[state])
            led_state = state

        if time.ticks_diff(now, last_report) >= 100:
            last_report = now
            msg = {"type": "dog", "from": NAME, "state": state, "front_cm": front_cm}
            try:
                msg["rssi"] = wlan.status("rssi")
            except Exception:
                pass
            send({k: x for k, x in msg.items() if x is not None})
            if not wlan.isconnected():
                dog.move(0, 0)
                current = (0, 0)
                wlan = connect()
finally:
    # 어떤 이유로든 끝나면(오류, Thonny Stop) 반드시 멈춘다. 걷던 중에 죽으면 마지막 move가 남아 계속 걷기 때문
    dog.move(0, 0)
    led(0, 0, 0)
