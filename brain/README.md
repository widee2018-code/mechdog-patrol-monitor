# 행동 알고리즘 (brain/)

Pi 또는 노트북에서 돌며 로봇견을 조종하는 코드입니다. 로봇견은 `firmware/dog_cmd.py`를 실행해 Wi-Fi(UDP 9100)로 `move(속도, 회전)` 명령만 받고, 판단은 여기서 합니다. 시나리오와 연습 순서는 [프로젝트 가이드 7절](../docs/project_guide_ko.html#behavior)에 있습니다.

| 파일 | 하는 일 |
|---|---|
| `marker_follow.py` | 카메라로 ArUco 마커를 찾아 따라가기 (0.15 m 유지, 놓치면 멈춤 → 찾기 → 포기). STANDBY로 시작해서 준비 확인(마커, 거리 0.12~0.25 m, 로봇견 응답)이 끝난 뒤 시작 키를 눌러야 움직임 |
| `teleop.py` | 키보드로 로봇견 조종 (회전 방향 확인, 행렬 연습 때 A 조종) |
| `contain_demo.py` | 포위·재정렬 연습 (추측 항법): B는 오른쪽 0.2 m → 앞 0.2 m, C는 왼쪽으로 크게 이동 → 확인 후 Enter → 같은 길로 복귀. `calibrate`로 개마다 회전·직진 속도를 먼저 잼 (`calibration.json`) |
| `make_markers.py` | 출력용 마커 A4 시트 만들기 |
| `lidar_slam.py` | RPLIDAR C1 + BreezySLAM 손에 들고 매핑 (오도메트리 없이 스캔 매칭). 지도 창에서 `s` 저장, 끝내면 `maps/`에 PNG·NPY 저장. 설치는 파일 맨 위 참고 |
| `../firmware/dog_cmd.py` | 로봇견 쪽: 명령 받기 + 안전 반사(1초 무명령 정지, 15 cm 이내 전진 금지) |

## 설치

```bash
pip install -r brain/requirements.txt
```

## 1. 마커 출력

```bash
python brain/make_markers.py            # ID 1, 2를 7 cm로 → markers.png
```

**실제 크기(100%)로 출력**하고, 출력된 검은 사각형 한 변을 자로 재서 `--marker-cm`에 넣습니다. 둘레의 흰 여백은 자르지 마세요(여백이 없으면 인식이 잘 안 됩니다). 마커는 판지에 붙여 평평하게 유지합니다.

## 2. 개 없이 연습: 노트북 웹캠

```bash
python brain/marker_follow.py --camera 0 --show
```

`--dog`를 주지 않으면 명령을 보내지 않는 **연습 모드**입니다. 마커를 들고 움직이며 화면과 터미널의 명령을 확인하세요.

**시작과 일시정지:** 프로그램은 항상 **일시정지 상태로 시작**합니다. 마커를 인식하면 화면에 `PAUSED - marker OK`가 초록색으로 뜨고, 이때 **스페이스(카메라 창)** 또는 **Enter(터미널)** 를 누르면 따라가기를 시작합니다. 같은 키를 다시 누르면 일시정지(정지 명령)입니다. 마커가 보이기 전에 시작을 누르면, 마커가 보이는 순간부터 따라갑니다. 시작 키 없이 바로 따라가게 하려면 `--autostart`를 붙입니다.

- 마커를 화면 오른쪽으로 → `turn`이 +, 왼쪽으로 → −
- 마커를 멀리 → `v`가 커짐, 0.15 m 근처 → `v=0` (후진은 기본으로 꺼짐, `--back-m 0.3`처럼 주면 그보다 가까울 때 `v=-40`)
- 마커를 가리면 → `hold`(정지) → 1.5초 뒤 `search`(마지막 본 쪽으로 회전) → 12초 뒤 `gave_up`(정지)
- 화면에 표시되는 `dist`가 실제 거리와 많이 다르면 `--hfov`(카메라 가로 화각)를 조정합니다.

macOS에서는 처음 실행할 때 터미널 앱의 카메라 권한을 허용해야 합니다.

## 3. 로봇견 카메라로 연습 (아직 명령은 안 보냄)

모니터 서버가 카메라 스트림을 받게 하고(루트 README의 "실시간 영상" 참고), 서버를 거쳐 영상을 받습니다. ESP32 카메라는 스트림을 한 곳에만 보낼 수 있어서 카메라에 직접 붙지 않습니다.

```bash
CAMERAS="B=http://192.168.0.21:81/stream" uvicorn server.app:app --host 0.0.0.0 --port 8000 --timeout-graceful-shutdown 2
python brain/marker_follow.py --camera http://localhost:8000/api/camera/B/stream --name B --monitor 127.0.0.1 --show
```

로봇견을 손에 들고 마커 쪽으로 돌려 보며 명령이 맞게 나오는지 봅니다. `--monitor`를 주면 상태 변화가 대시보드 타임라인에도 나옵니다. 지연이 크면 카메라 해상도를 320×240으로 낮추세요.

## 4. 로봇견이 명령 받기

1. ([Thonny 연결 방법](../docs/mechdog_thonny_guide.md) 참고) 로봇견 안에 `secrets.py`(Wi-Fi, `MONITOR`, `NAME`)를 저장해 두고, Thonny에서 `firmware/dog_cmd.py`를 Run. 출력되는 로봇견 IP를 적어 둡니다.
2. 로봇견을 들어 올린 상태(다리가 허공)에서 키보드 조종으로 확인합니다.
   ```bash
   python brain/teleop.py --dog 192.168.0.31
   ```
   - `a`를 눌렀을 때 왼쪽으로 돌아야 합니다. 반대로 돌면 이후 모든 명령에 `--turn-sign 1`을 붙입니다 (기본값 -1은 MechDog B 실측값).
   - 키에서 손을 떼면 0.3초 안에 멈춰야 하고, teleop을 끄면 1초 안에 멈춰야 합니다(워치독).
   - 초음파 앞에 손을 15 cm 안으로 대면 전진만 막혀야 합니다(LED 빨강).
3. 바닥에 내려놓고 `1`(느린 속도)부터 시험합니다.

## 5. 실제 따라가기

```bash
python brain/marker_follow.py --camera http://localhost:8000/api/camera/B/stream \
    --dog 192.168.0.31 --name B --monitor 127.0.0.1 --max-speed 80 --show
```

1. B를 A(또는 마커를 든 사람) 바로 뒤 **0.12~0.25 m**에 세웁니다. 화면이 `STANDBY - not ready`(빨강)이면 아랫줄에 이유가 나옵니다.
2. 조건이 1초 이상 유지되면 `STANDBY - READY for EXPLORE`(초록)로 바뀌고, 대시보드 타임라인에 "Dog B STANDBY: EXPLORE 준비 완료"가 뜹니다.
3. 스페이스(또는 터미널에서 Enter)를 눌러 출발합니다. 준비가 안 됐을 때 누르면 출발하지 않고 이유를 알려 줍니다.
   - **대시보드로 출발:** B·C를 모두 `--monitor`로 켜 두고 둘 다 준비 완료가 되면 대시보드의 **▶ 탐사 시작** 버튼이 켜집니다. 누르면 서버가 미션을 EXPLORE로 바꾸고 B·C가 함께 출발합니다. 탐사 중 상단의 **⏸ 대기**를 누르면 B·C가 일시정지합니다. A는 `teleop.py`로 사람이 조종합니다.
   - **대기 중 줄 맞추기 (teleop):** `--dog`를 주면 대기(일시정지) 중에는 `marker_follow.py`가 로봇견에 명령을 보내지 않고 `teleop Dog B` 창을 띄웁니다. 창을 클릭하고 w/a/s/d로 B·C를 줄 자리에 놓으면 준비 확인이 초록이 됩니다. 출발하면 teleop 창은 자동으로 닫힙니다. 창을 띄우지 않으려면 `--no-teleop`.
   - **포위:** 탐사 중 사람 인식이 사람을 찾으면 사이렌이 울리고 단계가 의심(SUSPECT)이 됩니다. 범인이면 **◎ 포위**: B·C가 멈추고 teleop 창이 떠서 사람이 조종합니다. 범인이 아니면 **⏸ 대기 · 범인 아님**: B·C가 멈추고 teleop 창이 뜹니다. 어느 쪽이든 teleop으로 다시 줄을 맞춘 뒤 **▶ 탐사 시작**. `marker_follow.py`는 계속 켜 둡니다.
4. 멈추게 하려면 같은 키를 다시 누릅니다(일시정지). Ctrl+C(또는 창에서 `q`)로 끄면 정지 명령을 보내고 종료합니다.

처음에는 `--max-speed 80`처럼 느리게 시작하세요.

### 준비 확인 (STANDBY)

| 조건 | 기본값 | 바꾸는 인자 |
|---|---|---|
| 앞 개 마커가 보임 | `--marker-id` (B는 1, C는 2) | |
| 앞 개와 거리 | 0.12~0.25 m | `--ready-min-m`, `--ready-max-m` |
| 위 조건이 이어진 시간 | 1초 | `--ready-hold-sec` |
| 로봇견(`dog_cmd.py`)이 상태를 보냄 | 2초 이내 | `--link-sec` |

- 거리는 **카메라로 잰 마커 거리**를 씁니다. 마커가 안 보일 때만 초음파(`front_cm`)를 표시합니다. 화면에 `gap 0.18 m (camera)`처럼 어느 쪽인지 나옵니다. (초음파는 빔이 손에 든 마커를 비껴 뒤쪽 물체를 재는 일이 있어 2026-11-17에 바꿈)
- 초음파와 로봇견 응답은 모니터 서버의 `/api/dogs`에서 읽습니다. `--monitor`가 없으면 카메라만으로 판단합니다. **서버를 2026-11-13 이후 코드로 다시 켜야** `/api/dogs`가 있습니다.
- 준비 확인 없이 출발하려면 `--no-ready-check`.

### 조정할 값

| 증상 | 바꿀 값 |
|---|---|
| 마커 쪽으로 돌지 않고 반대로 돔 | `--turn-sign` 부호 바꾸기 (기본 -1 ↔ 1) |
| 좌우로 흔들리며 지그재그로 걸음 | `--k-turn`을 낮춤 (예: 40) |
| 마커가 옆에 있어도 잘 안 돎 | `--k-turn`을 높임 (예: 80) |
| 멈췄다 섰다를 반복 | `--min-speed` 조정, 목표 거리 근처 흔들림이면 `--target-m` 확인 |
| 너무 바짝 붙거나 너무 멀리 섬 | `--hfov` 또는 `--marker-cm` 확인 (화면의 `dist`를 줄자와 비교) |
| 찾는 동안 제자리 회전이 안 됨 | `--search-speed 40` (조금 전진하며 회전) |

`move(속도, 회전)`의 숫자 단위는 Hiwonder 라이브러리 기준이라 실제 m/s, °/s는 측정해 봐야 합니다(가이드 11절 0단계).

### 걷다 서다 줄이기, 2단 행렬 (#12, #11)
- **잠깐 놓쳐도 계속 걷기 (`--hold-sec`, 기본 0.3초):** 걸을 때 카메라가 흔들려 마커를 한두 장 놓치면 하던 명령을 0.3초까지 이어서 보냅니다. 그보다 오래 놓치면 예전처럼 정지(`hold`) → 찾기(`search`) → 포기(`gave_up`). 0이면 바로 정지.
- **서 있을 때 제자리걸음 막기 (`--turn-deadband`, 기본 0.15 ≈ ±5°):** 목표 거리 안이라 속도가 0일 때 작은 회전값이 나가면 MechDog이 제자리걸음을 하다 마커가 화면 밖으로 벗어납니다(11-26 실측). 마커가 화면 가운데 근처면 회전도 0으로 보내 완전히 섭니다. 걸을 때는 영향 없음.
- **뒤 개 기다리기 (`--behind`, B 는 기본 C):** 뒤 개가 마커를 놓치면(`search`·`gave_up`) 앞 개는 멈추고 `wait_back`(뒤 개 기다리는 중)이 됩니다. 뒤 개가 다시 따라가면 이어서 출발하고, `--wait-alert-sec`(기본 5초) 넘게 기다리면 대시보드에 경고. 뒤 개의 `marker_follow.py`가 꺼져 있거나 2초 넘게 소식이 없으면 기다리지 않습니다. `--monitor` 필요, 끄려면 `--behind none`.
- **정찰견 A (사람 조종):** `python brain/teleop.py --dog <A IP> --name A --monitor <허브 IP> --behind B` 로 켜면 B가 마커를 놓쳤거나 C를 기다리는 동안 **전진만 막히고**(회전·후진은 됨) 창에 `WAIT: Dog B lost marker`가 뜹니다.

## 다음 단계

- 행렬: A를 `teleop.py`로 조종하고 B가 마커 1, C가 마커 2를 따라가기 (`marker_follow.py`를 두 개 실행)
- 접근 + 거리 유지 + 증거 촬영: 같은 코드에서 침입자 마커 ID로 바꾸고 스냅샷 추가
- 초음파 거리를 따라가기 속도 계산에 합치기 (지금은 거리를 마커 크기로만 재고, 초음파는 로봇견 쪽 안전 정지에만 씀)

## 포위·재정렬 연습 (`contain_demo.py`)

LiDAR 없이 정해 둔 경로를 시간으로 움직입니다. A가 침입자 앞에 멈춰 있고, B는 오른쪽(침입자 둘레 시계 반대 방향), 맨 뒤 C는 왼쪽(시계 방향)으로 빠집니다. MechDog는 옆걸음을 못 하므로 "제자리 90° → 옆으로 `--out`만큼 직진 → 제자리 90° → 앞으로 `--forward`만큼 직진"으로 움직입니다(기본 B 0.2 m × 0.2 m, C 0.6 m × 0.6 m). `--face`를 주면 도착 후 침입자 쪽으로 돕니다. 곡선 하나로 가는 `--path arc`도 있지만 MechDog가 좁은 곡선을 잘 못 걸어 기본에서 뺐습니다(`calibrate --arc` 필요).

```bash
# 1. 보정 (개마다 한 번): 제자리 한 바퀴(왼쪽, 오른쪽) 시간과 1 m 직진 시간을 Enter로 잼
python brain/contain_demo.py calibrate --dog 192.168.0.19 --name B
#    (--path arc를 쓸 때만) 곡선 보정: 걸으면서 반 바퀴, 왼쪽·오른쪽
python brain/contain_demo.py calibrate --dog 192.168.0.19 --name B --arc

# 2. 연습 모드: 명령 없이 경로와 시간만 출력 (대시보드에 추측 위치)
python brain/contain_demo.py run --name B --monitor 127.0.0.1

# 3. 실제: Enter = 포위 시작 → (HOLD에서 확인) → Enter = 재정렬
python brain/contain_demo.py run --dog 192.168.0.19 --name B --monitor 127.0.0.1 \
    --camera http://localhost:8000/api/camera/B/stream   # 복귀 끝에 마커로 방향 맞추기

# C (맨 뒤, 왼쪽으로 크게)
python brain/contain_demo.py run --dog <C의 IP> --name C --monitor 127.0.0.1

# B·C 함께: Enter 한 번에 동시에 포위 → HOLD → Enter 한 번에 B 복귀·정렬이 끝난 뒤 C 복귀·정렬
python brain/contain_demo.py run --name B,C --dog <B IP>,<C IP> --monitor 127.0.0.1 \
    --camera http://localhost:8000/api/camera/B/stream,http://localhost:8000/api/camera/C/stream

# 한 마리로 다듬은 경로 값을 저장해 두기 (다음부터, 두 마리 모드에서도 이 값을 씀)
python brain/contain_demo.py run --dog <C IP> --name C --out 0.5 --forward 0.4 --turn-in 95 --save
```

- 개마다 다른 값(회전 방향 `turn_sign`, 경로 `path`)은 `brain/calibration.json`의 그 개 항목에 저장됩니다. 우선순위: 명령줄 > `calibration.json` > 이름별 기본값.
- **포위 전에 `marker_follow.py`는 반드시 `q`로 끄세요.** 일시정지 상태에서도 정지 명령을 계속 보내서 포위 명령과 섞입니다.

- 바닥 배치: 침입자(상자)를 두고, A(또는 A 대신 마커를 붙인 상자)를 침입자 앞 `--radius` 0.8 m, B를 그 뒤 `--spacing` 0.45 m(개 중심 사이)에 같은 방향으로 세웁니다.
- 공간: B는 출발 위치에서 오른쪽·앞으로 0.2 m씩 움직입니다. 처음에는 손으로 잡을 수 있게 옆에서 따라가세요. Ctrl+C면 바로 정지합니다.
- HOLD 중에는 명령을 보내지 않으므로 `teleop.py`로 살짝 조정할 수 있습니다. 조정한 뒤 teleop을 끄고 Enter를 누르세요(복귀 경로는 계산상 목표 지점 기준).
- 복귀는 같은 길을 거꾸로 밟습니다. 추측 항법이라 오차가 쌓이므로 `--camera`를 주면 복귀 끝에 앞 개 마커(B는 1, C는 2)가 화면 가운데 오도록 제자리에서 조금씩 돌아 방향을 맞춥니다(짧게 돌고 → 멈춰서 보고 → 반복). 복귀 방향에서 최대 ±15°(`--center-max`)까지만 돌고, 가운데 허용 범위는 `--center-tol`(기본 0.1 ≈ 3°)입니다.
- 실측 보정: 포위 3번째 회전은 `--turn-in`(기본 100°), 복귀 첫 회전은 `--turn-back`(기본 190°)으로 계산값(90°, 180°) 대신 직접 정합니다. 오차가 크면 이 값들과 `--forward`, `--out`, 보정값(`calibrate`)을 확인하세요.

## frontier 자동 탐사 (#14, 1·2·3단계 + 시뮬레이터)
정찰견 A가 SLAM 지도에서 **아직 안 본 곳과 맞닿은 빈 칸(frontier)** 을 골라 스스로 가게 하는 기능입니다. 1단계(frontier 찾기), 2단계(경로 계획), 3단계(실물 로봇견 조종, `frontier_drive.py`)까지 있습니다.

- **`frontier.py`**: 지도를 4 cm 칸으로 줄이고 벽을 몸 반지름(0.18 m)만큼 부풀린 다음, frontier 덩어리를 찾습니다. 로봇 위치에서 Dijkstra(목표가 여러 개인 A*)로 경로 비용을 구하고, `경로 길이 − gain × 덩어리 길이`가 가장 작은 곳을 목표로 고릅니다. 벽 가까운 칸은 비용을 더 줘서 가운데로 다니게 하고, 경로는 꺾이는 점(waypoint)만 남깁니다. 한 번 계획하는 데 약 10 ms 걸립니다.
  ```bash
  python brain/frontier.py maps/slam_20261123_145614.npy              # 저장된 지도에서 확인 (그림 저장)
  python brain/frontier.py maps/slam_....npy --at 5.2 6.4              # 로봇 위치를 지도 m 로
  ```
- **`frontier_sim.py`**: 가짜 세상에 가짜 C1을 쏴서 지도를 쌓고, 실물 계획처럼 "0.6 m 걷기 → 0.8초 멈춤·스캔 → 다시 계획"을 반복합니다. 목표에 가도 frontier가 안 사라지거나 막히면 그 자리는 빼고 다른 곳으로 갑니다. 기본 세상은 방 3개와 상자 4개, `--world`를 주면 저장한 SLAM 지도를 세상으로 씁니다. 위치는 정확하다고 가정합니다(SLAM 오차 없음).
  ```bash
  uvicorn server.app:app --port 8000                          # 허브
  python brain/frontier_sim.py --hub 127.0.0.1 --speed 2      # 창 + 대시보드 (지도, Dog A, 주황 점선 경로, 탐사 목표)
  python brain/frontier_sim.py --no-window                    # 창 없이 최대 속도, 결과 그림만 maps/ 에
  python brain/frontier_sim.py --world maps/slam_....npy      # 실제 지도를 세상으로
  ```
- 결과 (2026-11-26): 기본 세상에서 목표 8곳, 108초(0.2 m/s 가정), 걸은 거리 15.4 m, 지도 99%. 11-23 실제 지도 3장을 세상으로 쓰면 83~90%이고, 남은 칸은 몸이 못 들어가는 빔 틈입니다.
- 결과 (2026-11-27): 실측 속도(0.057 m/s, 회전 13°/s)로 기본값을 바꾸면 같은 세상이 324초.
- **`frontier_drive.py` (3단계)**: C1을 개 등에 얹고(정면 = 머리 방향) USB로 이 컴퓨터에 꽂으면, `lidar_slam.py`와 같은 SLAM 위치로 계획하고 로봇견에 `move`를 보냅니다. 대기 상태로 시작하고, 지도 창에서 `g` = 시작, 스페이스 = 멈춤, `s` = 저장, `q` = 끝입니다. "멈춰서 스캔 1.5초 → 계획 → 0.4 m 걷기"를 반복합니다. 25° 넘게 틀어지면 제자리 회전합니다. 12초 동안 5 cm도 못 가면 그 목표는 뺍니다. SLAM 위치가 1초 넘게 멈추면 서고, 제자리 회전에서 목표와 더 멀어지면 회전 부호가 반대라고 보고 섭니다. `--dog`를 빼면 명령은 보내지 않고 계산과 표시만 합니다(C1을 손에 들고 확인).
  ```bash
  python brain/frontier_drive.py --hub 127.0.0.1                                  # 명령 없이 계산·표시만
  python brain/frontier_drive.py --dog 192.168.0.18 --name B --hub 127.0.0.1      # 실물 (이름은 로봇견 secrets.py 의 NAME)
  ```
- 남은 단계: 4) B·C가 SLAM 지도에 벽으로 찍히지 않게 하기, 5) EXPLORE일 때만 탐사하고 SUSPECT이거나 B가 기다리면(#11) 정지.

