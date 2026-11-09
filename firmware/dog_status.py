# MicroPython for the MechDog (ESP32-S3): join Wi-Fi and send `dog` status to the monitor.
# Open in Thonny, edit the four settings below, then press Run. Do not save it over the
# dog's own main.py without first downloading a backup copy of that file.
import network, socket, json, time

WIFI_SSID = "YOUR_WIFI"
WIFI_PASS = "YOUR_PASSWORD"
MONITOR = ("192.168.0.11", 9000)   # IP of the computer running server/app.py
NAME = "B"                          # A, B or C

# Sensor hooks. Replace the bodies with the calls from Hiwonder's MechDog examples
# for your firmware. Return None until then; the dashboard shows "—" for missing fields.
def read_battery():
    return None   # volts, e.g. 7.6

def read_front_cm():
    return None   # ultrasonic distance in cm

def read_tilt():
    return None   # "ok" or "fallen"


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
        raise RuntimeError("Wi-Fi failed. 2.4 GHz network? Right password?")
    print("Wi-Fi OK, dog IP:", wlan.ifconfig()[0])
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
      "text": "Dog %s online at %s" % (NAME, wlan.ifconfig()[0])})

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
    for name, err in errors.items():   # report each broken sensor hook once
        if name not in reported:
            reported.add(name)
            send({"type": "event", "from": NAME, "level": "warn", "text": "%s failed: %s" % (name, err)})
    if not wlan.isconnected():
        wlan = connect()
    time.sleep_ms(100)
