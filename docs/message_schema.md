# 텔레메트리 메시지 스키마

모든 메시지는 `type` 필드가 있는 JSON 객체 하나입니다. 모니터로 보낼 때는 **UDP 9000번 포트**(작고 자주 보내는 메시지) 또는 **HTTP `POST /api/ingest`**(카메라 JPEG처럼 큰 데이터, 또는 메시지들의 JSON 배열)를 사용합니다. `ts`(Unix 초)가 없으면 서버가 붙입니다. HTTP로 보낼 때 서버에 `INGEST_TOKEN`이 설정돼 있으면 인증 헤더가 필요합니다([아래](#http-수신-인증)).

좌표는 `web/arena.json` 기준의 아레나 미터 단위입니다. 원점은 왼쪽 아래 모서리, x는 오른쪽, y는 위쪽입니다. 각도는 도(degree) 단위이며 0 = +x 방향, 반시계 방향이 양수입니다.

| type | 보내는 쪽 | 주기 | 필드 |
|---|---|---|---|
| `dog` | Pi(위치) **와** 각 로봇견(자체 센서) | 10 Hz | `from`(A/B/C), `x`, `y`, `heading_deg`, `state`, `battery_v`, `front_cm`, `tilt`, `rssi`. 일부만 보내도 됩니다. 서버가 로봇견별로 부분 업데이트를 합칩니다. |
| `lidar` | Pi | 5 Hz | `from:"pi"`, `origin:[x,y]`, `points:[[angle_deg, dist_m], ...]` |
| `intruder` | Pi | 추적 중 5 Hz | `x`, `y`, `confidence`(0–1) |
| `sound` | Pi (ReSpeaker) | 1 Hz + 경보 시 | `label`(예: `glass_break`, `door`, `ambient`), `doa_deg`, `level_db`, `confidence`, `alert`(bool) |
| `camera` | Dog A (또는 대신 보내는 Pi) | 약 1 Hz | `from`, `image`(data URL: `data:image/jpeg;base64,...`), `evidence`(bool: 증거 갤러리에 추가), `note` |
| `mission` | Pi 상태 머신 | 변경 시 | `state`: PATROL / SUSPECT / ALERT / INTERCEPT / CONTAIN / HANDOVER / RETURN |
| `event` | Pi | 발생 시 | `level`(`info` / `warn` / `alert`), `text` |

## 예시

```json
{"type": "dog", "from": "B", "battery_v": 7.5, "front_cm": 41, "tilt": "ok", "state": "blocking"}
{"type": "dog", "from": "B", "x": 1.20, "y": 0.35, "heading_deg": 90}
{"type": "lidar", "from": "pi", "origin": [3.0, 0.15], "points": [[0, 2.93], [1, 2.94]]}
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
