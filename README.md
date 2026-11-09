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
│   └── dog_status.py   # MechDog(MicroPython)용 상태 전송 코드
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
