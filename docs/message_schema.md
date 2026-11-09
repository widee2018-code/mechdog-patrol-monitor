# 텔레메트리 메시지 스키마

모든 메시지는 `type` 필드가 있는 JSON 객체 하나입니다. 모니터로 보낼 때는 **UDP 9000번 포트**(작고 자주 보내는 메시지) 또는 **HTTP `POST /api/ingest`**(카메라 JPEG처럼 큰 데이터, 또는 메시지들의 JSON 배열)를 사용합니다. `ts`(Unix 초)가 없으면 서버가 붙입니다.

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
