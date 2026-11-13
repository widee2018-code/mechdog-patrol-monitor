# MechDog Patrol Monitor

`../patrol_apprehension_plan.md`의 MechDog 순찰 및 검거 시나리오를 위한 웹 대시보드입니다. 실시간 LiDAR 스캔, 로봇견 3대의 위치와 상태, 방향이 표시되는 소리 경보, Dog A 카메라 영상, 미션 상태, 이벤트 타임라인, 증거 스냅샷을 보여 줍니다.

프로젝트 진행 가이드(다이어그램, 스크린샷 포함)는 **[docs/project_guide_ko.html](docs/project_guide_ko.html)**에 있습니다. 2026-11-11부터 가이드는 **행렬 탐사 순찰** 시나리오(정찰견 등에 LiDAR, 확인견 2마리가 줄지어 따라가며 매핑 → 발견 → 포위 → 다시 탐사)를 기준으로 합니다. 대시보드와 시뮬레이터 코드는 아직 이전 시나리오 그대로입니다. (영문판 [docs/project_guide.html](docs/project_guide.html)은 이전 시나리오 기준)

## 팀 역할 (3명)

| 담당 | GitHub | 맡은 일 | 폴더 |
|---|---|---|---|
| 대시보드 디자인 | `worksenddl2-source` | 대시보드 화면 디자인, 새 시나리오(행렬 탐사) 상태 표시 | `web/` |
| DB · 기록 | `jyj25` | Supabase 프로젝트와 테이블 설계, 기록기(recorder), 기록 데이터 조회 | `cloud/supabase/`, `recorder/` |
| 하드웨어 · 행동 · 통합 | `widee2018-code` | 펌웨어, LiDAR·SLAM, 행동 알고리즘, 서버·시뮬레이터, Docker와 외부 공개, 문서 | 그 외 전부 (`firmware/`, `brain/`, `server/`, `tools/`, `docs/` 등) |

- 각자 자기 폴더에서 작업하고, `dev`에서 브랜치를 따서 PR로 합칩니다. 폴더 주인은 `.github/CODEOWNERS`에 있어 PR에 자동으로 리뷰어로 지정됩니다.
- **함께 정하는 약속**은 바꿀 때 영향을 받는 사람의 리뷰를 받습니다.
  - `docs/message_schema.md` (메시지 형식, 상태 이름): 서버가 보내고 `web/`과 `recorder/`가 읽음 → `widee2018-code` + `worksenddl2-source` + `jyj25`
  - `docker-compose.yml` (hub와 recorder 컨테이너를 함께 띄움): `widee2018-code` + `jyj25`
- `web/`에는 Supabase `service_role` 키를 절대 넣지 않습니다. 브라우저에서 DB를 읽어야 하면 `anon` 키 + RLS 정책으로 합니다.

## 진행 현황

목표 흐름: 정찰견 A가 매핑하며 전진 → B·C가 마커로 따라감 → Edge AI로 범인 인식 → 포위 → 대시보드 사이렌 → 일정 시간 뒤 재정렬 → 멈춘 곳부터 매핑 재개

| 단계 | 상태 | 이슈 |
|---|---|---|
| A가 매핑하며 전진 (LiDAR, SLAM, frontier) | ⬜ 아직 | #14 |
| B·C가 마커로 따라감 | 🟡 B 1마리 마커 따라가기 시연 완료 (2026-11-12) | #11, #12 |
| Edge AI로 범인 인식 | ⬜ 방식 결정 필요 | #5, #13 |
| 포위 | ⬜ 아직 | #15 |
| 대시보드 사이렌 | ⬜ 범위 결정 필요 | #6, #18 |
| 일정 시간 뒤 재정렬 → 매핑 재개 | ⬜ 시점 결정 필요 | #7, #16 |
| 상태 머신, 메시지 형식, 시뮬레이터 | ⬜ 예전 시나리오 그대로 (먼저 할 일) | #8, #9, #10 |
| 대시보드 새 시나리오 반영 | 🟡 디자인 시안 3안 (PR #3) | #17 |
| Supabase 기록 | 🟡 기록기 동작 확인 (PR #4) | #19, #20 |

할 일은 [GitHub Issues](https://github.com/widee2018-code/mechdog-patrol-monitor/issues)에서 관리합니다. 담당별 보기:
[worksenddl2-source](https://github.com/widee2018-code/mechdog-patrol-monitor/issues?q=is%3Aopen+assignee%3Aworksenddl2-source) ·
[jyj25](https://github.com/widee2018-code/mechdog-patrol-monitor/issues?q=is%3Aopen+assignee%3Ajyj25) ·
[widee2018-code](https://github.com/widee2018-code/mechdog-patrol-monitor/issues?q=is%3Aopen+assignee%3Awidee2018-code) ·
[결정 필요](https://github.com/widee2018-code/mechdog-patrol-monitor/issues?q=is%3Aopen+label%3A%22%EA%B2%B0%EC%A0%95+%ED%95%84%EC%9A%94%22)

## 빠른 시작 (시뮬레이션 로봇)

```bash
pip install -r requirements.txt

# 터미널 1: 모니터 서버 (UDP :9000 수신, 웹 + WebSocket :8000)
uvicorn server.app:app --host 0.0.0.0 --port 8000

# 터미널 2: 3분짜리 데모 시나리오를 반복 재생하는 가짜 텔레메트리
python -m server.simulator            # --speed 3 을 붙이면 3배속
```

브라우저에서 http://localhost:8000 을 엽니다.

## Docker로 실행하고 외부에 공개하기 (시연용)

허브 서버(대시보드 + UDP 수신)만 컨테이너로 돌리고, Cloudflare Tunnel로 `https://` 공개 주소를 붙입니다. 공유기 설정이나 공인 IP는 필요 없습니다. LiDAR·`brain/` 처럼 하드웨어를 쓰는 프로그램은 Pi에서 직접 실행하고, 지금처럼 `<허브 IP>:9000`(UDP)으로 보냅니다.

```bash
cp .env.example .env          # CAMERAS, INGEST_TOKEN 채우기
docker compose up --build -d  # 로컬: http://localhost:8000

# 임시 공개 주소 (계정 불필요, 실행할 때마다 주소가 바뀜)
docker compose --profile quick up --build -d
docker compose logs quick-tunnel | grep trycloudflare

# 고정 공개 주소 (시연 QR용): Cloudflare Zero Trust → Networks → Tunnels 에서 터널을 만들고
# Public hostname 의 서비스를 http://hub:8000 으로 지정, 토큰을 .env 의 CF_TUNNEL_TOKEN 에 넣는다
docker compose --profile tunnel up --build -d
```

- **외부에 공개할 때는 `INGEST_TOKEN`을 반드시 설정하세요.** 설정하지 않으면 주소를 아는 누구나 `POST /api/ingest`로 가짜 경보를 띄울 수 있습니다. 설정하면 `camera_relay.py`에도 `--token`(또는 `INGEST_TOKEN` 환경 변수)으로 같은 값을 줍니다. UDP는 터널을 거치지 않으므로 영향이 없습니다.
- 공개 주소로 보는 사람도 카메라 영상을 볼 수 있습니다. 서버가 카메라 연결 하나를 나눠 주므로 카메라 부하는 늘지 않지만, 관람객 수만큼 현장 업로드 대역폭을 씁니다.
- 행사장 인터넷이 끊겨도 로컬 주소 `http://<허브 IP>:8000`는 계속 동작합니다. 발표 화면은 로컬 주소로 여는 것을 권합니다.
- Pi(64-bit OS)에서도 같은 명령으로 실행됩니다. 중지: `docker compose --profile quick --profile tunnel down`

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
├── brain/              # 행동 알고리즘 (Pi/노트북): 마커 따라가기, 키보드 조종, 마커 출력
├── firmware/
│   ├── dog_status.py   # MechDog(MicroPython)용 상태 전송 코드
│   ├── dog_cmd.py      # MechDog: Wi-Fi move 명령 받기 + 안전 반사
│   ├── secrets.py.example # Wi-Fi 정보 틀: secrets.py로 복사해 로봇견에 저장 (커밋 안 됨)
│   └── mechdog_cam/    # 카메라 모듈 펌웨어: 기존 Wi-Fi에 접속 (Arduino)
├── tools/
│   ├── send_test.py    # 실제 하드웨어 없이 서버를 확인하는 테스트 송신기
│   └── camera_relay.py # 로봇견 카메라 영상을 대시보드로 전달
├── docs/
│   ├── project_guide_ko.html
│   ├── project_guide.html
│   ├── message_schema.md
│   └── images/
├── Dockerfile          # 허브 컨테이너 (docker-compose.yml, .env.example 과 함께)
└── requirements.txt
```

## 실제 로봇 연결하기

Pi와 로봇견이 `udp://<모니터-IP>:9000` 으로 [docs/message_schema.md](docs/message_schema.md)의 메시지를 보내도록 설정하고, 시뮬레이터는 끕니다. 대시보드는 바꿀 필요가 없습니다. 실제로 잰 아레나에 맞게 `web/arena.json` 을 수정하세요.

## 실제 MechDog로 첫 테스트

1. 서버만 실행합니다(시뮬레이터 없이). `python tools/send_test.py` 를 실행해서 http://localhost:8000 에 움직이는 Dog B가 나오는지 확인한 뒤 멈춥니다.
2. `firmware/secrets.py.example` 을 `firmware/secrets.py` 로 복사해 Wi-Fi 이름과 비밀번호를 넣고, Thonny로 로봇견 안에 `secrets.py` 로 저장합니다([Thonny 가이드](docs/mechdog_thonny_guide.md) 3절). 그다음 `firmware/dog_status.py` 에서 `MONITOR`(이 컴퓨터의 IP), `NAME` 을 설정하고 USB로 로봇견에서 실행합니다. `secrets.py` 는 `.gitignore`에 들어 있어 커밋되지 않습니다.
3. 로봇견 카드가 활성화되어 Wi-Fi 세기가 표시되고, 타임라인에 "Dog B 온라인"이 떠야 합니다. 그다음 센서 함수를 하나씩 채웁니다.
4. Pi + LiDAR가 동작하기 전에는 로봇견의 위치가 없습니다. 그동안 지도에 표시하려면: `python tools/send_test.py --dog B --x 1.2 --y 0.8`

## 행동 알고리즘 연습 (마커 따라가기)

로봇견이 Wi-Fi로 `move` 명령을 받게 하고(`firmware/dog_cmd.py`), Pi나 노트북에서 카메라로 ArUco 마커를 찾아 따라가게 합니다. 로봇견 없이 노트북 웹캠으로 먼저 연습할 수 있습니다. 순서와 조정 방법은 [brain/README.md](brain/README.md)에 있습니다.

```bash
pip install -r brain/requirements.txt
python brain/marker_follow.py --camera 0 --show   # 연습 모드: 명령은 화면에만 표시
```

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
- `secrets.h`, `secrets.py` 의 Wi-Fi 이름과 비밀번호는 자동으로 검사합니다. 두 파일 자체도 커밋할 수 없습니다.
- 저장소 전체 검사: `.githooks/pre-commit --all`
- 오탐인 줄은 끝에 `allow-secret` 주석을 달면 건너뜁니다.
- GitHub에 푸시하거나 PR을 올리면 같은 검사가 GitHub Actions(`.github/workflows/privacy-check.yml`)에서 다시 돌아갑니다. hook을 안 켰거나 `--no-verify` 로 건너뛴 커밋도 여기서 잡힙니다. 로그에는 파일:줄번호만 나옵니다.
- 팀 공통 금지어(팀원 이름 등)는 저장소 Settings → Secrets and variables → Actions 에 `PRIVACY_BLOCKLIST` 로 한 줄에 하나씩 넣습니다.
