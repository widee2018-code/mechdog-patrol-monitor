# 텔레메트리 메시지 스키마

모든 메시지는 `type` 필드가 있는 JSON 객체 하나입니다. 모니터로 보낼 때는 **UDP 9000번 포트**(작고 자주 보내는 메시지) 또는 **HTTP `POST /api/ingest`**(카메라 JPEG처럼 큰 데이터, 또는 메시지들의 JSON 배열)를 사용합니다. `ts`(Unix 초)가 없으면 서버가 붙입니다. HTTP로 보낼 때 서버에 `INGEST_TOKEN`이 설정돼 있으면 인증 헤더가 필요합니다([아래](#http-수신-인증)).

좌표는 `web/arena.json` 기준의 아레나 미터 단위입니다. 원점은 왼쪽 아래 모서리, x는 오른쪽, y는 위쪽입니다. 각도는 도(degree) 단위이며 0 = +x 방향, 반시계 방향이 양수입니다.

| type | 보내는 쪽 | 주기 | 필드 |
|---|---|---|---|
| `dog` | Pi(위치) **와** 각 로봇견(자체 센서) | 10 Hz | `from`(A/B/C), `x`, `y`, `heading_deg`, `state`, `battery_v`, `front_cm`, `tilt`(`ok`/`fallen`), `tilt_deg`(가장 많이 기운 축, 도), `rssi`. 로봇견 `state` 는 `idle`/`moving`/`stopped`/`blocked`/`fallen`. 일부만 보내도 됩니다. 서버가 로봇견별로 부분 업데이트를 합칩니다. **위치 출처:** A는 `brain/lidar_slam.py --dog A`(SLAM). B·C는 `marker_follow.py`가 5 Hz로 `marker`(`[거리 m, 방향 °]`, 카메라 정면 기준 반시계라 왼쪽 +, 못 찾으면 `null`)를 보내고, **서버가** 앞 개 위치(1초 이내)와 합쳐 `x`, `y`, `heading_deg`(앞 개와 같다고 가정), `pos_src: "marker"`를 계산해 다시 `dog` 메시지로 보냄 (B는 A 기준, C는 방금 계산한 B 기준). 몸 치수 `MARKER_BACK_M`·`CAM_FWD_M`(각 0.10 m, 실측 전)은 `server/app.py`. 로봇견 연결 여부는 `front_cm`·`rssi`가 있는 메시지(로봇견 자신)로만 판단 |
| `lidar` | Pi | 5 Hz | `from:"pi"`, `origin:[x,y]`, `points:[[angle_deg, dist_m], ...]`. SLAM 중(`brain/lidar_slam.py`)에는 `origin`이 C1 현재 위치를 따라 움직이고 `heading_deg`도 붙음 |
| `face` | 얼굴 사진 (`brain/pose_detect.py`) | 사람마다 한 장 (얼굴이 1.3배 이상 크게 잡히면 바꿔서 최대 3장, 2초 간격), **HTTP** (UDP에 안 들어감) | `from`(카메라 A/B/C), `track`(추적 번호), `px`(얼굴 너비 픽셀), `box`(잘라 낸 정사각형, 화면 비율 0–1), `image`(`data:image/jpeg;base64,...` 160×160). 머리 몸 점(코·눈·귀)으로 자르고 얼굴이 40 px 미만이거나 흐린 프레임이면 안 보냄. 판정(범인·미등록) 없음. 서버가 최근 30장을 **메모리에만** 기억해 새로 연 추론 탭에도 보냄. **서버를 끄면 사라지고, recorder 는 저장하지 않음 (Supabase 기록 없음)**. `--no-faces`로 끔 |
| `map` | Pi (`brain/lidar_slam.py`) | 1 Hz, **HTTP** (UDP에 안 들어감) | SLAM 격자 지도. `from:"pi"`, `png`(`data:image/png;base64,...` RGBA: 투명 = 아직 안 봄, 옅은 하늘색 = 빈 공간, 밝은 회백색 = 벽·물체), `res`(칸 한 변 m), `w`·`h`(칸 수), `origin:[x,y]`(이미지 왼쪽 아래 모서리의 아레나 좌표), `rot`(이미지를 origin 기준 반시계로 돌릴 각도), `pose:[x,y,heading_deg]`(C1 현재 위치), `updates`(SLAM 갱신 횟수). 대시보드가 지도 레이어와 하늘색 삼각형(C1)으로 그림. recorder 는 저장하지 않음 |
| `intruder` | Pi | 추적 중 5 Hz | `x`, `y`, `confidence`(0–1) |
| `detect` | 사람 인식 (YOLO11n, Pi) | 사람이 보이는 동안 프레임마다 (2–10 Hz) | `from`(어느 개 카메라: A/B/C), `label`(`person`), `confidence`(0–1, 가장 높은 값), `count`(사람 수), `boxes`(`[[x1, y1, x2, y2, 신뢰도], ...]`, 화면 비율 0–1, 대시보드 영상 위에 박스로 그림). 대시보드 "👤 발견" 불은 마지막 메시지 뒤 1.5초 동안 켜짐. 새로 발견할 때만 서버가 `event`를 남김. **탐사(EXPLORE) 중이면 서버가 `mission` SUSPECT로 바꿔 사이렌을 울림** (대기 중에는 불만) |
| `pose` | 행동 추론 (YOLO11n-pose, Pi `brain/pose_detect.py`) | 사람마다 프레임마다 (2–10 Hz) | `from`, `track`(추적 번호), `level`(`none`/`low`/`mid`/`high`), `action`(위험 행동이 있을 때만 가장 위험한 것: `fall` `still` `strike` `kick` `rush` `reach` `crouch` `handsup` `loiter`), `why`(판단 근거), `actions`(`[{id, level, why}]`, 유지 시간을 채우는 중이면 `level: "pending"`), `confidence`, `box`, `keypoints`(`[[x, y, 신뢰도] x 17]`, COCO 순서). 좌표는 화면 비율 0–1. recorder 가 `action` 이 바뀔 때만 `inference_log` 에 저장 |
| `sound` | Pi (ReSpeaker) | 1 Hz + 경보 시 | `label`(예: `glass_break`, `door`, `ambient`), `doa_deg`, `level_db`, `confidence`, `alert`(bool) |
| `camera` | Dog A (또는 대신 보내는 Pi) | 약 1 Hz | `from`, `image`(data URL: `data:image/jpeg;base64,...`), `evidence`(bool: 증거 갤러리에 추가), `note` |
| `mission` | Pi 상태 머신 | 변경 시 | `state`: STANDBY / EXPLORE / SUSPECT / CONFIRM / CONTAIN / HANDOVER / REGROUP (옛 이름 PATROL / ALERT / INTERCEPT / RETURN도 받음). 사건 중이면 서버가 `incident`(사건 번호)를 붙여 대시보드로 보낸다 |
| `event` | Pi | 발생 시 | `level`(`info` / `warn` / `alert`), `text` |
| `alarm` | 모니터 서버 (`from: "monitor"`) | 사이렌이 켜지고 꺼질 때 | `state`(`on`/`off`), `reason`(켤 때 `detect`(사람 발견, SUSPECT) / `confirm`, 끌 때 `ack` / `regroup` / `false_alarm`(포위 전 대기·탐사로) / `stop`(포위 뒤 대기로)), `incident`(사건 번호), `x`, `y`(울릴 때 마지막 침입자 위치). 서버가 `mission` 변화로 만든다. 직접 보낼 필요 없음 |

## 예시

```json
{"type": "dog", "from": "B", "battery_v": 7.5, "front_cm": 41, "tilt": "ok", "state": "blocking"}
{"type": "dog", "from": "B", "x": 1.20, "y": 0.35, "heading_deg": 90}
{"type": "lidar", "from": "pi", "origin": [3.0, 0.15], "points": [[0, 2.93], [1, 2.94]]}
{"type": "detect", "from": "A", "label": "person", "confidence": 0.82, "count": 1, "boxes": [[0.31, 0.12, 0.58, 0.97, 0.82]]}
{"type": "pose", "from": "A", "track": 3, "level": "high", "action": "fall", "why": "몸 기울기 72° · 엉덩이 40% 내려감", "confidence": 0.81, "box": [0.3, 0.2, 0.6, 0.95], "keypoints": [[0.45, 0.25, 0.97], "... 17개"]}
{"type": "intruder", "from": "pi", "x": 2.41, "y": 1.98, "confidence": 0.95}
{"type": "sound", "from": "pi", "label": "glass_break", "doa_deg": 147.8, "level_db": 78, "confidence": 0.88, "alert": true}
{"type": "camera", "from": "A", "image": "data:image/jpeg;base64,/9j/4AAQ...", "evidence": true, "note": "intercept"}
{"type": "mission", "from": "pi", "state": "ALERT"}
{"type": "event", "from": "pi", "level": "alert", "text": "카메라: 사람 확인 + LiDAR + 소리 일치 -> ALERT"}
```

## 실제 하드웨어 참고 사항

- **LiDAR 각도 기준.** RPLIDAR는 센서 정면에서 시계 방향으로 각도를 보고합니다. Pi에서 변환하세요: `angle_ccw = (mount_deg - angle_cw) % 360`. 여기서 `mount_deg` 는 센서 정면이 아레나 좌표에서 향하는 방향입니다.
- **UDP 크기.** Wi-Fi에서는 데이터그램 하나를 약 8 KB 이하로 유지하세요. 360점 스캔은 약 4 KB입니다. 카메라 JPEG는 HTTP POST로 보내세요.
- **명령 메시지**(Pi → 로봇견, 예: `{"to": "B", "cmd": "goto", ...}`)는 이 스키마에 포함되지 않습니다. Pi 두뇌에서 로봇견으로 직접 갑니다. 대시보드에 보이게 하려면 `event` 메시지로 같이 보내세요.

## 탐사 시작 / 대기로 (대시보드 버튼)

- `POST /api/mission/start`: 미션이 STANDBY이고 B·C의 `marker_follow.py`가 최근 2초 안에 `ready: true`를 보냈을 때만 `mission` EXPLORE를 만듭니다. 아니면 `409`와 이유(`detail`).
- `POST /api/mission/contain`: "◎ 포위" 버튼. SUSPECT(사람 발견) 중일 때만 `mission` CONTAIN. 범인인지는 관제가 판단합니다. B·C의 `marker_follow.py`가 로봇견을 멈추고 일시정지하며 `teleop.py` 창을 띄웁니다.
- `POST /api/mission/standby`: "⏸ 대기" 버튼. 대기가 아니면 언제든 `mission` STANDBY. 탐사 중이면 B·C 일시정지, 사람 발견 중이면 오탐(사이렌 `false_alarm`).
- `POST /api/snapshot`: "📷 촬영" 버튼. 실시간 영상이 연결된 로봇견마다 지금 프레임(마커 표시 없는 원본)으로 `camera` 메시지(`evidence: true`, `note: "촬영"` 또는 `"촬영 · 사건 #N"`)를 만듭니다. 대시보드 증거 사진 칸에 뜨고 recorder가 Supabase Storage `evidence` 버킷에 올립니다. 연결된 카메라가 없으면 `409`.
- 모두 공개 주소(Cloudflare 터널)로 온 요청은 `403`.
- `GET /api/dogs`는 `mission`(지금 단계)과 `mission_ts`(바뀐 시각)도 돌려줍니다. `marker_follow.py`가 0.5초마다 읽고, 프로그램을 켠 뒤 EXPLORE로 바뀌면 출발(teleop 창 닫음), STANDBY·CONTAIN으로 바뀌면 일시정지(teleop 창 열림)합니다.

## HTTP 수신 인증

대시보드를 외부(Cloudflare Tunnel)에 공개할 때는 서버의 `.env`에 `INGEST_TOKEN`을 설정합니다. 그러면 `POST /api/ingest`는 다음 헤더가 있어야 받고, 없거나 틀리면 `401`을 돌려줍니다.

```
Authorization: Bearer <INGEST_TOKEN>
```

```bash
MSG='{"type": "event", "from": "pi", "level": "info", "text": "테스트"}'
curl -X POST http://<모니터-IP>:8000/api/ingest -H "Content-Type: application/json" -H "Authorization: Bearer $INGEST_TOKEN" -d "$MSG"  # allow-secret: 환경 변수 참조
```

- `tools/camera_relay.py`는 `--token` 또는 `INGEST_TOKEN` 환경 변수로 같은 값을 받습니다.
- **UDP 9000은 인증하지 않습니다.** 터널을 거치지 않아 같은 Wi-Fi 안에서만 닿기 때문입니다. 그래서 로봇견과 Pi 코드는 바꿀 필요가 없습니다.
- `INGEST_TOKEN`이 비어 있으면 예전처럼 인증 없이 받습니다(로컬 개발용).

## 받는 쪽: WebSocket `/ws`

서버는 받은 메시지를 그대로 `/ws`에 연결된 모든 클라이언트에 보냅니다. 대시보드와 Supabase 기록기(`recorder/`)가 이 구독자입니다.

- 새로 접속하면 먼저 **지금까지의 상태를 다시 보냅니다**: 최근 `event` 최대 200개, 증거 `camera` 최대 24개, 그리고 종류·보낸 쪽별 마지막 메시지. 그래서 구독자가 재접속하면 같은 메시지를 또 받습니다. 기록하는 쪽은 `(type, from, ts)`로 중복을 걸러야 합니다.
- 서버가 3초 동안 소식이 없는 개를 발견하면 `{"type": "event", "from": "monitor", "level": "warn", ...}`을 스스로 만들어 보냅니다. `from: "monitor"`는 서버가 만든 메시지입니다.

Supabase에 무엇을 어떻게 기록하는지는 [recorder/README.md](../recorder/README.md)와 [cloud/supabase/schema.sql](../cloud/supabase/schema.sql)에 있습니다.
