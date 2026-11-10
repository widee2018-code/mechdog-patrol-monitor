# MechDog Patrol Monitor

`../patrol_apprehension_plan.md`의 MechDog 순찰 및 검거 시나리오를 위한 웹 대시보드입니다. 실시간 LiDAR 스캔, 로봇견 3대의 위치와 상태, 방향이 표시되는 소리 경보, Dog A 카메라 영상, 미션 상태, 이벤트 타임라인, 증거 스냅샷을 보여 줍니다.

프로젝트 진행 가이드(다이어그램, 스크린샷 포함)는 **[docs/project_guide_ko.html](docs/project_guide_ko.html)**에 있습니다. (영문판: [docs/project_guide.html](docs/project_guide.html))

## 빠른 시작 (시뮬레이션 로봇)

```bash
pip install -r requirements.txt

# 터미널 1: 모니터 서버 (UDP :9000 수신, 웹 + WebSocket :8000)
uvicorn server.app:app --host 0.0.0.0 --port 8000

# 터미널 2: 3분짜리 데모 시나리오를 반복 재생하는 가짜 텔레메트리
python -m server.simulator            # --speed 3 을 붙이면 3배속
```

브라우저에서 http://localhost:8000 을 엽니다.

## 폴더 구조

```
mechdog-patrol-monitor/
├── server/
│   ├── app.py          # 텔레메트리 허브: UDP/HTTP 수신 -> WebSocket 송신
│   └── simulator.py    # 가짜 Pi + 로봇견, 실제 시스템과 같은 메시지 사용
├── web/
│   ├── index.html      # 대시보드
│   ├── app.js
│   ├── style.css
│   └── arena.json      # 아레나 크기, 출구, 장애물, 구역, LiDAR 위치
├── firmware/
│   ├── dog_status.py   # MechDog(MicroPython)용 상태 전송 코드
│   └── mechdog_cam/    # 카메라 모듈 펌웨어: 기존 Wi-Fi에 접속 (Arduino)
├── tools/
│   ├── send_test.py    # 실제 하드웨어 없이 서버를 확인하는 테스트 송신기
│   └── camera_relay.py # 로봇견 카메라 영상을 대시보드로 전달
├── docs/
│   ├── project_guide_ko.html
│   ├── project_guide.html
│   ├── message_schema.md
│   └── images/
└── requirements.txt
```

## 실제 로봇 연결하기

Pi와 로봇견이 `udp://<모니터-IP>:9000` 으로 [docs/message_schema.md](docs/message_schema.md)의 메시지를 보내도록 설정하고, 시뮬레이터는 끕니다. 대시보드는 바꿀 필요가 없습니다. 실제로 잰 아레나에 맞게 `web/arena.json` 을 수정하세요.

## 실제 MechDog로 첫 테스트

1. 서버만 실행합니다(시뮬레이터 없이). `python tools/send_test.py` 를 실행해서 http://localhost:8000 에 움직이는 Dog B가 나오는지 확인한 뒤 멈춥니다.
2. Thonny에서 `firmware/dog_status.py` 를 열고 `WIFI_SSID`, `WIFI_PASS`, `MONITOR`(이 컴퓨터의 IP), `NAME` 을 설정한 다음 USB로 로봇견에서 실행합니다. **실제 Wi-Fi 비밀번호를 넣은 상태로 커밋하지 마세요.**
3. 로봇견 카드가 활성화되어 Wi-Fi 세기가 표시되고, 타임라인에 "Dog B 온라인"이 떠야 합니다. 그다음 센서 함수를 하나씩 채웁니다.
4. Pi + LiDAR가 동작하기 전에는 로봇견의 위치가 없습니다. 그동안 지도에 표시하려면: `python tools/send_test.py --dog B --x 1.2 --y 0.8`

## 카메라 연결하기

1. 컴퓨터를 카메라 모듈의 Wi-Fi에 연결하고 사진 주소를 찾습니다.
   ```bash
   python tools/camera_relay.py --probe 192.168.5.1
   ```
2. 찾은 주소로 영상을 대시보드에 전달합니다.
   ```bash
   python tools/camera_relay.py --url http://192.168.5.1/capture --dog A --fps 2
   ```

### 실시간 영상 (카메라가 같은 Wi-Fi에 있을 때)

아래의 카메라 펌웨어를 구워 카메라가 공유기에 접속해 있으면, 서버가 카메라 스트림을 직접 받아 대시보드에 실시간 영상으로 보여 줍니다. 서버를 이렇게 실행합니다:

```bash
CAMERAS="A=http://192.168.0.20:81/stream" uvicorn server.app:app --host 0.0.0.0 --port 8000 --timeout-graceful-shutdown 2
```

- 서버가 카메라와 연결을 하나만 유지하고 모든 대시보드에 나눠 줍니다(ESP32 카메라는 스트림을 한 곳에만 보낼 수 있음). 그래서 서버가 실행 중이면 브라우저에서 `http://<카메라-IP>:81/stream` 을 직접 열면 멈춰 있습니다. `/capture` 는 계속 쓸 수 있습니다.
- 대시보드 카메라 칸에 "실시간"이 표시됩니다. 영상이 끊기면 타임라인에 알림이 뜨고, `camera_relay.py` 스냅샷이 있으면 그것으로 돌아갑니다.
- 증거 스냅샷(`camera` 메시지의 `evidence`)은 예전과 같이 동작합니다.
- macOS에서는 서버를 실행하는 터미널 앱에 **시스템 설정 → 개인정보 보호 및 보안 → 로컬 네트워크** 권한이 있어야 카메라에 접속할 수 있습니다.

## 카메라 펌웨어 교체 (카메라를 기존 Wi-Fi에 접속시키기)

원래 카메라 펌웨어는 자기 Wi-Fi만 만들기 때문에 컴퓨터가 인터넷/모니터 네트워크에서 빠져야 합니다. `firmware/mechdog_cam/` 를 구우면 카메라가 공유기에 직접 접속하고, 같은 `/capture`, `:81/stream` 주소를 제공합니다. Wi-Fi 접속에 실패하면 AP `MechDog-Cam-A`(비밀번호 `mechdog123`, 192.168.4.1)로 열립니다.

1. **원래 펌웨어 백업 (필수).** 카메라 모듈을 USB로 연결하고 포트를 확인(`ls /dev/cu.*`)한 뒤:
   ```bash
   pip install esptool
   mkdir -p firmware/backup
   python -m esptool --chip esp32s3 -p /dev/cu.usbmodemXXXX read_flash 0 ALL firmware/backup/cam_original.bin
   ```
   되돌릴 때: `python -m esptool --chip esp32s3 -p /dev/cu.usbmodemXXXX write_flash 0 firmware/backup/cam_original.bin`
2. `firmware/mechdog_cam/secrets.h.example` 을 `secrets.h` 로 복사하고 2.4 GHz Wi-Fi 이름과 비밀번호를 넣습니다.
3. Arduino IDE에서 `firmware/mechdog_cam/mechdog_cam.ino` 를 엽니다. 보드: **ESP32S3 Dev Module**, PSRAM: **OPI PSRAM** (부팅 로그에 PSRAM이 없다고 나오면 QSPI PSRAM으로 바꿈). 업로드합니다.
4. 시리얼 모니터(115200)에 카메라 IP가 출력됩니다. 브라우저에서 `http://<카메라-IP>/` 로 확인한 뒤:
   ```bash
   python tools/camera_relay.py --url http://<카메라-IP>/capture --dog A --fps 2
   ```
   IP가 바뀌지 않게 하려면 스케치의 `USE_STATIC_IP` 를 켜거나 공유기에서 DHCP 예약을 합니다.

시리얼 로그에 "카메라를 찾지 못함"이 나오면 이 모듈의 핀 배치가 `PIN_MAPS` 후보에 없는 것입니다. 백업한 원래 펌웨어의 부팅 로그나 모듈 회로도에서 핀을 확인해 추가하세요. 교체한 펌웨어에는 원래 펌웨어의 색 인식 등 메인보드 연동 기능이 없습니다.

## 커밋 전 개인정보 검사

저장소를 받은 뒤 한 번만 실행하세요. 커밋할 때마다 개인정보·비밀정보가 들어갔는지 검사하고, 찾으면 커밋을 막습니다.

```bash
git config core.hooksPath .githooks
```

- 공통 규칙(내 컴퓨터 경로, MAC 주소, API 키, 비밀번호 값 등): `.githooks/blocked-patterns.txt`
- 나만의 금지어(이름, 이메일 등): 저장소 최상위에 `.git-blocklist.local` 을 만들고 한 줄에 하나씩 적습니다. 이 파일은 git에 올라가지 않습니다.
- `secrets.h` 의 Wi-Fi 이름과 비밀번호는 자동으로 검사합니다.
- 저장소 전체 검사: `.githooks/pre-commit --all`
- 오탐인 줄은 끝에 `allow-secret` 주석을 달면 건너뜁니다.
- GitHub에 푸시하거나 PR을 올리면 같은 검사가 GitHub Actions(`.github/workflows/privacy-check.yml`)에서 다시 돌아갑니다. hook을 안 켰거나 `--no-verify` 로 건너뛴 커밋도 여기서 잡힙니다. 로그에는 파일:줄번호만 나옵니다.
- 팀 공통 금지어(팀원 이름 등)는 저장소 Settings → Secrets and variables → Actions 에 `PRIVACY_BLOCKLIST` 로 한 줄에 하나씩 넣습니다.
