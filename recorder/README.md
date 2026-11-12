# 기록기 (recorder/) — 초안

hub의 `/ws`에 클라이언트로 붙어 텔레메트리를 받고, 필요한 것만 골라 Supabase에 기록하는 별도 컨테이너입니다. hub 코드는 바꾸지 않습니다. Supabase나 인터넷에 문제가 생겨도 실시간 대시보드는 영향을 받지 않습니다.

```
[로봇견/Pi] ──UDP──▶ hub ──/ws──▶ 대시보드
                       └──/ws──▶ recorder ──HTTPS──▶ Supabase (cloud/supabase/schema.sql)
```

## 무엇을 기록하나

| type | 기록 | 비고 |
|---|---|---|
| `event`, `mission`, `intruder` | 전부 | |
| `sound` | `alert: true` 만 | |
| `dog` | 로봇견별 1초에 1번 | 10 Hz 전부 저장하면 무료 용량(500 MB)이 금방 참 |
| `camera` | `evidence: true` 만 | JPEG는 Storage 버킷에 올리고 DB에는 경로만 |
| `lidar`, 실시간 카메라 프레임 | 기록 안 함 | |

## 할 일

- [ ] Supabase 프로젝트 생성, `cloud/supabase/schema.sql` 실행, `evidence` 버킷 생성
- [ ] `recorder.py`: `ws://hub:8000/ws` 접속 → 걸러내기 → 묶어서 `POST /rest/v1/telemetry_log`
- [ ] 중복 무시: hub는 새로 접속한 클라이언트에 이전 기록을 다시 보낸다. `Prefer: resolution=ignore-duplicates` + unique(type, source, ts)
- [ ] 실패 시 버퍼에 쌓아 두고 재시도 (참고: `safety-edge-ai/pi/uploader/`)
- [ ] `Dockerfile`, `docker-compose.yml`에 `record` 프로필로 추가
- [ ] `.env.example`에 `SUPABASE_URL`(`https://<ref>.supabase.co`까지만), `SUPABASE_SERVICE_KEY` 추가

`service_role` 키는 recorder에만 넣습니다. `web/`(브라우저)에는 절대 넣지 마세요.
