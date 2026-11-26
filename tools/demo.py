"""시연 실행기: IP 는 demo.env 에만 적고, 명령은 매번 같게.

    python tools/demo.py check           demo.env 의 IP 마다 응답 확인 (허브·카메라·로봇견)
    python tools/demo.py hub             허브. CAMERAS 를 CAM_A/B/C 로 만들어 uvicorn 실행
    python tools/demo.py A               정찰견 A: teleop.py (--monitor, B 가 있으면 --behind B)
    python tools/demo.py B               확인견 B: marker_follow.py (마커 1, 허브 거쳐 카메라)
    python tools/demo.py C               확인견 C: marker_follow.py (마커 2)
    python tools/demo.py B --hold-sec 0  뒤에 붙인 옵션은 그대로 전달 (같은 옵션이면 뒤에 붙인 것이 우선)
    python tools/demo.py B --dry-run     실행하지 않고 만들어질 명령만 보기

처음 한 번: cp demo.env.example demo.env (Windows: copy). 다른 파일을 쓰려면 DEMO_ENV=경로.
실행 전에 실제로 돌리는 명령을 한 줄 보여 준다. 끄는 법은 원래 프로그램과 같다 (Ctrl+C, 창에서 q).
"""

from __future__ import annotations

import os
import platform
import shlex
import socket
import subprocess
import sys
import urllib.request
from urllib.parse import urlsplit
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ENV = Path(os.environ.get("DEMO_ENV", ROOT / "demo.env"))
DOGS = ("A", "B", "C")
MARKER = {"B": 1, "C": 2}   # B 는 A 등의 마커 1, C 는 B 등의 마커 2


def load_env() -> dict[str, str]:
    if not ENV.exists():
        sys.exit(f"{ENV} 가 없습니다. 먼저: cp demo.env.example demo.env  (Windows: copy) 후 IP 를 적으세요")
    env = {}
    for line in ENV.read_text(encoding="utf-8").splitlines():
        line = line.split("#", 1)[0].strip()
        if "=" in line:
            k, v = line.split("=", 1)
            env[k.strip()] = v.strip().strip('"').strip("'")
    return env


def need(env: dict, key: str, why: str) -> str:
    if not env.get(key):
        sys.exit(f"demo.env 에 {key} 가 비어 있습니다 ({why})")
    return env[key]


def cam_url(v: str) -> str:
    return v if "://" in v else f"http://{v}:81/stream"


def opts(env: dict, key: str) -> list[str]:
    return shlex.split(env.get(key, ""))


def build(cmd: str, env: dict, extra: list[str]) -> tuple[list[str], dict]:
    py = sys.executable
    port = env.get("HUB_PORT") or "8000"
    if cmd == "hub":
        cams = ",".join(f"{n}={cam_url(env[f'CAM_{n}'])}" for n in DOGS if env.get(f"CAM_{n}"))
        argv = [py, "-m", "uvicorn", "server.app:app", "--host", "0.0.0.0", "--port", port,
                "--timeout-graceful-shutdown", "2", *opts(env, "OPTS_HUB"), *extra]
        return argv, ({"CAMERAS": cams} if cams else {})
    hub = need(env, "HUB", "허브 IP")
    if cmd == "A":
        argv = [py, "brain/teleop.py", "--dog", need(env, "DOG_A", "정찰견 A IP"), "--name", "A", "--monitor", hub]
        if env.get("DOG_B"):
            argv += ["--behind", "B"]   # 2단 행렬(#11): B 가 마커를 놓치면 전진만 막음
        return argv + opts(env, "OPTS_A") + extra, {}
    if cmd in ("B", "C"):
        argv = [py, "brain/marker_follow.py",
                "--camera", f"http://{hub}:{port}/api/camera/{cmd}/stream",
                "--dog", need(env, f"DOG_{cmd}", f"확인견 {cmd} IP"), "--name", cmd,
                "--marker-id", str(MARKER[cmd]), "--monitor", hub, "--show"]
        if cmd == "B" and not env.get("DOG_C"):
            argv += ["--behind", "none"]   # C 가 없으면 기다리지 않음 (C 흉내를 쓸 땐 뒤에 --behind C)
        if not env.get(f"CAM_{cmd}"):
            print(f"주의: CAM_{cmd} 가 비어 있음 → 허브에 카메라 {cmd} 가 없어 영상이 안 나옵니다")
        return argv + opts(env, f"OPTS_{cmd}") + extra, {}
    sys.exit(__doc__)


def show(argv: list[str], add_env: dict) -> str:
    shown = [Path(a).name if a == sys.executable else a for a in argv]
    pre = " ".join(f'{k}="{v}"' for k, v in add_env.items())
    return (pre + " " if pre else "") + shlex.join(shown)


def run(argv: list[str], add_env: dict) -> int:
    p = subprocess.Popen(argv, cwd=ROOT, env={**os.environ, **add_env})
    while True:
        try:
            return p.wait()
        except KeyboardInterrupt:   # Ctrl+C 는 자식에게도 가므로, 자식이 정지 명령을 보내고 끝날 때까지 기다림
            continue


# ── check ────────────────────────────────────────────────────────

def ping(ip: str) -> bool:
    flag = ["-n", "1", "-w", "1500"] if platform.system() == "Windows" else (
        ["-c", "1", "-t", "2"] if platform.system() == "Darwin" else ["-c", "1", "-W", "2"])
    return subprocess.run(["ping", *flag, ip], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode == 0


def tcp(host: str, port: int) -> bool:
    try:
        with socket.create_connection((host, port), timeout=2):
            return True
    except OSError:
        return False


def check(env: dict) -> None:
    rows = []
    hub, port = env.get("HUB"), int(env.get("HUB_PORT") or 8000)
    if hub:
        try:
            urllib.request.urlopen(f"http://{hub}:{port}/api/dogs", timeout=2).read()
            rows.append(("허브", hub, True, "응답함"))
        except Exception:
            rows.append(("허브", hub, ping(hub), f"ping 만 확인 (허브 :{port} 는 아직 안 켜짐)"))
    for n in DOGS:
        if env.get(f"DOG_{n}"):
            rows.append((f"로봇견 {n}", env[f"DOG_{n}"], ping(env[f"DOG_{n}"]), "ping"))
        if env.get(f"CAM_{n}"):
            u = urlsplit(cam_url(env[f"CAM_{n}"]))
            rows.append((f"카메라 {n}", u.hostname, tcp(u.hostname, u.port or 80), f"포트 {u.port or 80}"))
    if not rows:
        sys.exit("demo.env 에 IP 가 하나도 없습니다")
    for name, ip, ok, how in rows:
        print(f"  {'✅' if ok else '❌'} {name:7s} {ip:15s} {how}")
    if not all(r[2] for r in rows):
        print("\n❌ 이면: 전원·Wi-Fi 연결을 확인하고, IP 가 바뀌었으면 공유기 DHCP 목록을 보고 demo.env 를 고치세요")


def main() -> None:
    args = sys.argv[1:]
    if not args or args[0] in ("-h", "--help"):
        print(__doc__)
        return
    cmd, extra = args[0], args[1:]
    if cmd.upper() in DOGS:
        cmd = cmd.upper()
    env = load_env()
    if cmd == "check":
        check(env)
        return
    dry = "--dry-run" in extra
    extra = [x for x in extra if x != "--dry-run"]
    argv, add_env = build(cmd, env, extra)
    print("실행:", show(argv, add_env))
    if not dry:
        sys.exit(run(argv, add_env))


if __name__ == "__main__":
    main()
