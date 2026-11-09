# Telemetry message schema

Every message is one JSON object with a `type` field. Send it to the monitor by **UDP to port 9000** (small, frequent messages) or by **HTTP `POST /api/ingest`** (large payloads such as camera JPEGs, or a JSON array of messages). The server adds `ts` (Unix seconds) if it is missing.

Coordinates are arena metres from `web/arena.json`: origin at the bottom-left corner, x to the right, y up. Angles are degrees, 0 = +x, counter-clockwise.

| type | Sent by | Rate | Fields |
|---|---|---|---|
| `dog` | Pi (position) **and** each dog (own sensors) | 10 Hz | `from` (A/B/C), `x`, `y`, `heading_deg`, `state`, `battery_v`, `front_cm`, `tilt`, `rssi`. Any subset. The server merges partial updates per dog. |
| `lidar` | Pi | 5 Hz | `from:"pi"`, `origin:[x,y]`, `points:[[angle_deg, dist_m], ...]` |
| `intruder` | Pi | 5 Hz while tracked | `x`, `y`, `confidence` (0–1) |
| `sound` | Pi (ReSpeaker) | 1 Hz + on alert | `label` (e.g. `glass_break`, `door`, `ambient`), `doa_deg`, `level_db`, `confidence`, `alert` (bool) |
| `camera` | Dog A (or the Pi on its behalf) | ~1 Hz | `from`, `image` (data URL: `data:image/jpeg;base64,...`), `evidence` (bool: add to the evidence gallery), `note` |
| `mission` | Pi state machine | on change | `state`: PATROL / SUSPECT / ALERT / INTERCEPT / CONTAIN / HANDOVER / RETURN |
| `event` | Pi | on occurrence | `level` (`info` / `warn` / `alert`), `text` |

## Examples

```json
{"type": "dog", "from": "B", "battery_v": 7.5, "front_cm": 41, "tilt": "ok", "state": "blocking"}
{"type": "dog", "from": "B", "x": 1.20, "y": 0.35, "heading_deg": 90}
{"type": "lidar", "from": "pi", "origin": [3.0, 0.15], "points": [[0, 2.93], [1, 2.94]]}
{"type": "intruder", "from": "pi", "x": 2.41, "y": 1.98, "confidence": 0.95}
{"type": "sound", "from": "pi", "label": "glass_break", "doa_deg": 147.8, "level_db": 78, "confidence": 0.88, "alert": true}
{"type": "camera", "from": "A", "image": "data:image/jpeg;base64,/9j/4AAQ...", "evidence": true, "note": "intercept"}
{"type": "mission", "from": "pi", "state": "ALERT"}
{"type": "event", "from": "pi", "level": "alert", "text": "Camera: person confirmed + LiDAR + sound agree -> ALERT"}
```

## Notes for real hardware

- **LiDAR angle convention.** RPLIDAR reports angles clockwise from the sensor's front. Convert on the Pi: `angle_ccw = (mount_deg - angle_cw) % 360`, where `mount_deg` is the direction the sensor's front faces in arena coordinates.
- **UDP size.** Keep a datagram under ~8 KB on Wi-Fi. A 360-point scan is ~4 KB. Send camera JPEGs with HTTP POST.
- **Command messages** (Pi → dog, e.g. `{"to": "B", "cmd": "goto", ...}`) are not part of this schema. They go from the Pi brain directly to the dogs. If you want them on the dashboard, mirror them as `event` messages.
