"""RPLIDAR C1 → 모니터 허브 브리지 (USB 시리얼 → UDP `lidar` 메시지).

C1을 USB(동봉된 USB-C 변환 보드)로 Mac이나 Pi에 꽂고 실행하면, 한 바퀴 스캔을
1° 칸으로 묶어 docs/message_schema.md 의 `lidar` 메시지로 허브(UDP :9000)에 보냅니다.
대시보드 지도에 하늘색 점으로 바로 그려집니다.

    pip install pyserial
    python tools/lidar_bridge.py --check                  # 연결·상태만 확인
    python tools/lidar_bridge.py --print                  # 허브 없이 터미널에 요약 출력
    python tools/lidar_bridge.py --calib                  # 방향 보정: 맞는 --mount 값 찾기
    python tools/lidar_bridge.py --tune                   # 대시보드 보면서 키보드로 방향·위치 맞추기
    python tools/lidar_bridge.py --hub 127.0.0.1          # 대시보드로 전송

포트는 자동으로 찾습니다 (macOS /dev/cu.usbserial-*, /dev/cu.SLAB_USBtoUART, Pi /dev/ttyUSB0).
못 찾으면 --port 로 지정하세요.

좌표: RPLIDAR는 센서 정면 기준 시계 방향 각도를 줍니다. 대시보드는 아레나 좌표
(x 오른쪽, y 위, 반시계 방향)를 쓰므로 angle_ccw = (mount - angle_cw) % 360 으로 바꿉니다.
--mount 는 센서 정면(케이블 반대쪽 화살표)이 아레나에서 향하는 방향입니다. 기본값 90 = 위(+y).
--origin 기본값은 web/arena.json 의 lidar_origin 입니다.
"""

from __future__ import annotations

import argparse
import glob
import json
import socket
import struct
import sys
import threading
import time
from pathlib import Path

import serial  # pyserial

BAUD_C1 = 460800
SYNC = 0xA5
CMD_STOP, CMD_RESET, CMD_SCAN = 0x25, 0x40, 0x20
CMD_GET_INFO, CMD_GET_HEALTH = 0x50, 0x52
HEALTH_NAMES = {0: "정상", 1: "경고", 2: "오류"}

ARENA = Path(__file__).resolve().parent.parent / "web" / "arena.json"


def find_port() -> str | None:
    pats = ["/dev/cu.usbserial*", "/dev/cu.SLAB_USBtoUART*", "/dev/cu.wchusbserial*",
            "/dev/ttyUSB*", "/dev/ttyACM*"]
    for p in pats:
        hits = sorted(glob.glob(p))
        if hits:
            return hits[0]
    return None


class RPLidar:
    def __init__(self, port: str, baud: int = BAUD_C1):
        self.ser = serial.Serial(port, baud, timeout=1)
        self.ser.dtr = False  # A 시리즈는 DTR로 모터를 켬. C1은 무시하지만 끄지 않도록 내려 둠

    def _cmd(self, cmd: int) -> None:
        self.ser.write(bytes([SYNC, cmd]))

    def _descriptor(self) -> tuple[int, int]:
        d = self.ser.read(7)
        if len(d) != 7 or d[0] != 0xA5 or d[1] != 0x5A:
            raise IOError(f"응답 헤더가 이상함: {d.hex() or '(응답 없음)'} — 보드레이트·케이블·전원 확인")
        size = struct.unpack("<I", d[2:6])[0] & 0x3FFFFFFF
        return size, d[6]

    def stop(self) -> None:
        self._cmd(CMD_STOP)
        time.sleep(0.05)
        self.ser.reset_input_buffer()

    def info(self) -> dict:
        self.stop()
        self._cmd(CMD_GET_INFO)
        size, _ = self._descriptor()
        b = self.ser.read(size)
        return {"model": b[0], "firmware": f"{b[2]}.{b[1]:02d}", "hardware": b[3], "serial": b[4:20].hex().upper()}

    def health(self) -> tuple[str, int]:
        self.stop()
        self._cmd(CMD_GET_HEALTH)
        size, _ = self._descriptor()
        b = self.ser.read(size)
        return HEALTH_NAMES.get(b[0], str(b[0])), struct.unpack("<H", b[1:3])[0]

    def scans(self):
        """한 바퀴마다 [(angle_cw_deg, dist_m, quality), ...] 를 내보낸다."""
        self.stop()
        self._cmd(CMD_SCAN)
        self._descriptor()
        buf = bytearray()
        rev: list[tuple[float, float, int]] = []
        while True:
            buf += self.ser.read(max(5, self.ser.in_waiting))
            i = 0
            while len(buf) - i >= 5:
                b0, b1, b2, b3, b4 = buf[i:i + 5]
                start, inv = b0 & 1, (b0 >> 1) & 1
                if start == inv or not (b1 & 1):  # 동기 깨짐 → 한 바이트 밀어서 다시 맞춤
                    i += 1
                    continue
                i += 5
                if start and rev:
                    yield rev
                    rev = []
                dist_mm = (b3 | (b4 << 8)) / 4.0
                if dist_mm > 0:
                    angle = ((b1 >> 1) | (b2 << 7)) / 64.0
                    rev.append((angle, dist_mm / 1000.0, b0 >> 2))
            del buf[:i]

    def close(self) -> None:
        try:
            self.stop()
        finally:
            self.ser.close()


class Keys:
    """터미널에서 키 하나씩 기다리지 않고 읽는다 (macOS·Linux). 끝나면 터미널 설정을 되돌림."""

    def __enter__(self):
        import termios, tty
        self.fd = sys.stdin.fileno()
        self.old = termios.tcgetattr(self.fd)
        tty.setcbreak(self.fd)
        return self

    def __exit__(self, *exc):
        import termios
        termios.tcsetattr(self.fd, termios.TCSADRAIN, self.old)

    def read(self) -> list[str]:
        import os, select
        keys = []
        while select.select([self.fd], [], [], 0)[0]:
            data = os.read(self.fd, 32).decode(errors="ignore")
            i = 0
            while i < len(data):
                if data[i] == "\x1b" and data[i + 1:i + 2] == "[" and i + 2 < len(data):
                    keys.append({"A": "UP", "B": "DOWN", "C": "RIGHT", "D": "LEFT"}.get(data[i + 2], ""))
                    i += 3
                else:
                    keys.append(data[i])
                    i += 1
        return keys


TUNE_HELP = ("키: ←/→ 5° 회전 · [ ] 1° 회전 · f 좌우 반전 · i/k/j/l 위치 위/아래/왼쪽/오른쪽 5 cm · "
             "r 처음 값 · q 또는 Enter 끝내기")


def to_points(rev, mount: float, min_d: float, max_d: float, min_q: int, flip: bool = False) -> list[list[float]]:
    """1° 칸마다 가장 가까운 점 하나만 남긴다 (360점 ≈ 4 KB, UDP 한 개에 들어감)."""
    bins: dict[int, float] = {}
    for a_cw, d, q in rev:
        if q < min_q or not (min_d <= d <= max_d):
            continue
        k = int(round(((mount + a_cw) if flip else (mount - a_cw)) % 360)) % 360
        if k not in bins or d < bins[k]:
            bins[k] = d
    return [[k, round(bins[k], 3)] for k in sorted(bins)]


def stop_on_sigterm() -> None:
    """kill(SIGTERM)·백그라운드 종료도 Ctrl+C 처럼 처리한다.
    그래야 finally 에서 C1 에 스캔 멈춤 명령을 보내고 (안 보내면 모터가 계속 돎) 지도도 저장한다."""
    import signal

    def _raise(*_):
        raise KeyboardInterrupt

    signal.signal(signal.SIGTERM, _raise)
    if hasattr(signal, "SIGHUP"):
        signal.signal(signal.SIGHUP, _raise)   # 터미널 창을 닫았을 때


def main() -> None:
    stop_on_sigterm()
    default_origin = [3.0, 0.15]
    try:
        default_origin = json.loads(ARENA.read_text())["lidar_origin"]
    except Exception:
        pass

    ap = argparse.ArgumentParser(description="RPLIDAR C1 → 모니터 허브 lidar 메시지")
    ap.add_argument("--port", help="시리얼 포트 (기본: 자동 탐색)")
    ap.add_argument("--baud", type=int, default=BAUD_C1, help="C1 = 460800 (A1/A2M8 = 115200)")
    ap.add_argument("--hub", default="127.0.0.1", help="허브 IP (UDP)")
    ap.add_argument("--udp-port", type=int, default=9000)
    ap.add_argument("--origin", type=float, nargs=2, default=default_origin, metavar=("X", "Y"),
                    help=f"아레나에서 센서 위치 m (기본 arena.json: {default_origin})")
    ap.add_argument("--mount", type=float, default=90.0, help="센서 정면이 향하는 아레나 각도 (반시계, 0=오른쪽, 90=위)")
    ap.add_argument("--hz", type=float, default=5.0, help="전송 빈도 (스키마 5 Hz)")
    ap.add_argument("--min", type=float, default=0.05, dest="min_d", help="이보다 가까운 점 버림 m")
    ap.add_argument("--max", type=float, default=12.0, dest="max_d", help="이보다 먼 점 버림 m")
    ap.add_argument("--min-quality", type=int, default=0)
    ap.add_argument("--check", action="store_true", help="장치 정보·상태만 출력하고 종료")
    ap.add_argument("--print", action="store_true", dest="print_only", help="전송 없이 터미널에 요약만")
    ap.add_argument("--calib", type=float, nargs="?", const=90.0, metavar="TARGET",
                    help="방향 보정: 대시보드에서 TARGET° (기본 90 = 위) 쪽이 되길 원하는 방향에 손·상자를 "
                         "1 m 안에 두면 맞는 --mount 값을 알려 줌")
    ap.add_argument("--flip", action="store_true", help="좌우 반전 (점이 실제와 반대로 돌 때)")
    ap.add_argument("--tune", action="store_true",
                    help="대시보드로 보내면서 키보드로 방향·위치 조정, 끝내면 쓸 명령을 알려 줌")
    args = ap.parse_args()
    if args.tune and args.print_only:
        sys.exit("--tune 은 대시보드로 보내면서 맞추는 모드라 --print 와 같이 쓸 수 없습니다")

    port = args.port or find_port()
    if not port:
        sys.exit("LiDAR 포트를 못 찾음. USB를 꽂고 `ls /dev/cu.*`(Mac) 또는 `ls /dev/ttyUSB*`(Pi) 확인 후 --port 지정")

    lidar = RPLidar(port, args.baud)
    try:
        info = lidar.info()
        status, code = lidar.health()
        print(f"[{port}] model={info['model']} fw={info['firmware']} hw={info['hardware']} "
              f"serial={info['serial']} 상태={status}" + (f" (오류 코드 {code})" if code else ""))
        if args.check:
            return
        if status == "오류":
            sys.exit("LiDAR가 오류 상태입니다. 전원을 뺐다 꽂아 보세요.")

        if args.calib is not None:
            print(f"보정: 대시보드에서 {args.calib:g}° 쪽(90=위, 0=오른쪽)이 되길 원하는 방향에 "
                  "손이나 상자를 센서에서 1 m 안에 두세요. 다른 물체는 1 m 밖으로. (Ctrl+C 로 종료)")
            for rev in lidar.scans():
                near = [(d, a) for a, d, q in rev if 0.05 <= d <= 1.0]
                if not near:
                    print("\r1 m 안에 물체 없음                                        ", end="", flush=True)
                    continue
                d, a_cw = min(near)
                mount = (args.calib + a_cw) % 360
                print(f"\r가장 가까운 물체: 센서 기준 {a_cw:5.1f}° (시계 방향), {d:.2f} m → "
                      f"--mount {mount:.0f}   ", end="", flush=True)

        sock = None if args.print_only else socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        dest = (args.hub, args.udp_port)
        if sock:
            print(f"전송: udp://{args.hub}:{args.udp_port}  origin={args.origin} mount={args.mount}°  (Ctrl+C 로 종료)")
        if args.tune:
            print("대시보드를 보면서 맞추세요. " + TUNE_HELP)

        start = (args.mount, args.flip, list(args.origin))
        keys = Keys() if args.tune else None
        if keys:
            keys.__enter__()
        try:
            stream(lidar, args, sock, dest, keys, start)
        finally:
            if keys:
                keys.__exit__()
                ox, oy = args.origin
                print(f"\n\n다음부터 이 명령으로 실행하세요:\n  python3 tools/lidar_bridge.py --hub {args.hub} "
                      f"--mount {args.mount:g}" + (" --flip" if args.flip else "") + f" --origin {ox:g} {oy:g}")
    except KeyboardInterrupt:
        print()
    finally:
        lidar.close()


def stream(lidar: RPLidar, args, sock, dest, keys, start) -> None:
    period = 1.0 / args.hz
    last = 0.0
    revs = 0
    t0 = time.time()
    for rev in lidar.scans():
        revs += 1
        if keys:
            for k in keys.read():
                if k in ("q", "\n", "\r"):
                    return
                step = {"LEFT": 5, "RIGHT": -5, "[": 1, "]": -1}.get(k)
                if step is not None:
                    args.mount = (args.mount + step) % 360
                elif k == "f":
                    args.flip = not args.flip
                elif k in ("i", "k", "j", "l"):
                    dx, dy = {"i": (0, 0.05), "k": (0, -0.05), "j": (-0.05, 0), "l": (0.05, 0)}[k]
                    args.origin = [round(args.origin[0] + dx, 2), round(args.origin[1] + dy, 2)]
                elif k == "r":
                    args.mount, args.flip, args.origin = start[0], start[1], list(start[2])
        now = time.time()
        if now - last < period:
            continue
        last = now
        pts = to_points(rev, args.mount, args.min_d, args.max_d, args.min_quality, args.flip)
        if sock:
            msg = {"type": "lidar", "from": "pi", "ts": round(now, 3), "origin": list(args.origin), "points": pts}
            sock.sendto(json.dumps(msg, separators=(",", ":")).encode(), dest)
        if keys:
            print(f"\rmount {args.mount:5.1f}° · 반전 {'켬' if args.flip else '끔'} · "
                  f"위치 ({args.origin[0]:.2f}, {args.origin[1]:.2f}) m · {len(pts)}점      ", end="", flush=True)
            continue
        front = [d for a, d in pts if abs(((a - args.mount) + 180) % 360 - 180) <= 10]
        rps = revs / max(now - t0, 1e-6)
        print(f"\r{rps:4.1f} 바퀴/s · 원시 {len(rev):4d}점 → {len(pts):3d}점 · "
              f"정면 ±10° 최근접 {min(front):.2f} m   " if front else
              f"\r{rps:4.1f} 바퀴/s · 원시 {len(rev):4d}점 → {len(pts):3d}점 · 정면 비어 있음   ",
              end="", flush=True)


if __name__ == "__main__":
    main()
