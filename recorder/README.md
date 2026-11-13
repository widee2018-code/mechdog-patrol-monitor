# recorder — Supabase 기록기

hub의 `/ws`를 구독해서 필요한 텔레메트리만 Supabase에 저장한다.
hub와 독립된 프로세스라서 recorder가 죽어도 대시보드는 영향이 없다.

```
로봇견/Pi/카메라 ──UDP 9000·HTTP──▶ hub(:8000) ──/ws──▶ recorder ──HTTPS──▶ Supabase
                                                                     ├─ telemetry_log (행)
                                                                     └─ Storage evidence (증거 사진)
```

## 무엇을 저장하나

| 메시지 `type` | 저장 여부 | 비고 |
|---|---|---|
| `event`, `mission`, `intruder` | 전부 | `mission`의 `STANDBY` 같은 새 상태 이름도 코드 수정 없이 그대로 저장 |
| `command` | 전부 | 대시보드 버튼(`start` / `stop` / `regroup`). 행의 `ts`가 누른 시각이라 "탐사 시작 시각"과 비상 정지 여부를 볼 수 있음 |
| `alarm` | 전부 | 사이렌 `on` / `off` (이슈 #8 예시 기준 가정. 이름이 다르면 `recorder.py`의 목록을 고친다). `incidents` 뷰의 사이렌 시작/끝에 쓰임 |
| `ready` | `all_ready`가 **바뀔 때만** | STANDBY 중 1초마다 오므로 전부 저장하지 않음. 첫 메시지는 저장하고, 같은 값이 계속 오면 건너뜀. `all_ready` 필드가 없으면 저장 안 함 |
| `sound` | `alert`가 참일 때만 | |
| `dog` | 로봇견별 **1초에 1건** | 나머지는 솎아냄 |
| `camera` | `evidence`가 참일 때만 | 사진은 Storage에, 행에는 경로(`storage_path`)만 |
| 그 외 | 저장 안 함 | |

- 사진은 `evidence` 버킷의 `<from>/<밀리초>.jpg`에 저장된다. 버킷은 **비공개**다.
- 같은 `(type, source, ts)`는 중복 저장되지 않는다(재접속 때 다시 오는 메시지 방지).
- Supabase에 연결이 안 되면 메모리에 최대 5000행까지 쌓아 두고 재시도한다. 넘으면 오래된 것부터 버린다.
- hub 연결이 끊기면 1초부터 최대 30초까지 늘려 가며 재접속한다.

## 처음 한 번: Supabase 준비

1. Supabase에서 프로젝트를 만든다(현재: `mechdog_patrol_monitor`, 도쿄 리전).
2. SQL Editor에서 `cloud/supabase/schema.sql`을 실행한다. `telemetry_log` 테이블, RLS, `evidence` 버킷이 만들어진다.
3. `cloud/supabase/.env` 파일을 만든다(`.gitignore`로 제외돼 있어 커밋되지 않는다).
   ```
   SUPABASE_URL=https://<프로젝트 ref>.supabase.co
   SUPABASE_SERVICE_KEY=<service_role 키>
   ```
   - 키 위치: Supabase 대시보드 → Project Settings → API Keys.
   - **service_role 키는 recorder에만 둔다.** `web/`, 깃, 채팅에 올리지 않는다. 이 키는 RLS를 우회하므로 노출되면 DB 전체가 열린다.
   - URL과 키를 복사하다가 공백이나 한글이 섞이는 경우가 있어, recorder가 비ASCII 문자와 공백을 자동으로 제거한다.

## 실행

```bash
docker compose --profile record up --build -d     # hub + recorder
docker compose logs -f recorder                   # 로그 보기
```

정상이면 로그에 이런 줄이 보인다.
```
connected to ws://hub:8000/ws
recorded 12 rows
```

Docker 없이 직접 실행할 수도 있다(Windows PowerShell 기준).
```powershell
pip install websockets
$env:SUPABASE_URL = "https://<ref>.supabase.co"
$env:SUPABASE_SERVICE_KEY = "<service_role 키>"
$env:HUB_WS = "ws://127.0.0.1:8000/ws"
python recorder/recorder.py
```
PowerShell에서는 `CAMERAS="..." 명령` 같은 bash식 한 줄 환경변수 문법이 안 된다. 위처럼 `$env:이름 = "값"`으로 먼저 지정한다.

## 잘 되는지 확인

Supabase 대시보드(프로젝트 `mechdog_patrol_monitor`)에서 본다.

- **Table Editor → `telemetry_log`**: 행이 계속 늘어나는지, `ts`가 방금 시각인지 본다.
- **Storage → `evidence` → `A/` 폴더**: 증거 사진이 있는지 본다.
- SQL Editor에서:
  ```sql
  select type, source, count(*), max(ts) from telemetry_log group by 1, 2 order by 4 desc;
  ```

손으로 증거 사진 한 장을 넣어 보는 테스트(카메라 IP는 상황에 맞게 바꾼다):
```python
import base64, json, time, urllib.request
jpg = urllib.request.urlopen("http://<카메라 IP>/capture", timeout=8).read()
msg = {"type": "camera", "from": "A", "note": "manual snapshot test", "evidence": True,
       "ts": time.time(), "image": "data:image/jpeg;base64," + base64.b64encode(jpg).decode()}
urllib.request.urlopen(urllib.request.Request("http://127.0.0.1:8000/api/ingest",
    json.dumps(msg).encode(), {"Content-Type": "application/json"}))
```
`INGEST_TOKEN`을 설정했다면 `Authorization: Bearer <토큰>` 헤더를 추가한다.

## 문제 해결

| 증상 | 원인과 해결 |
|---|---|
| `SUPABASE_URL 과 SUPABASE_SERVICE_KEY 가 필요합니다` | `cloud/supabase/.env`가 없거나 비어 있다. |
| `insert failed: 401` / `403` | 키가 틀렸다. anon 키가 아니라 **service_role** 키여야 한다. |
| `insert failed: 404` | 테이블이 없다. `schema.sql`을 실행했는지 본다. |
| `evidence upload failed` | `evidence` 버킷이 없다. `schema.sql`의 마지막 줄을 실행한다. 행은 사진 없이도 저장된다. |
| `hub connection lost` | hub가 꺼져 있다. 켜지면 자동 재접속한다. |
| `dog` 행이 안 늘어남 | 로봇견이 hub(`<이 컴퓨터 IP>:9000` UDP)로 보내는지 본다. 대시보드에 보이는지가 먼저다. |
| `camera` 행이 없음 | 정상이다. `evidence: true`인 메시지만 저장한다. 영상(`note: live`)은 저장하지 않는다. |

## 알아 둘 것

- **웹에서 조회하려면**: 지금은 RLS만 켜져 있고 정책이 없어서, anon 키로는 읽지도 쓰지도 못한다(의도한 설정). 대시보드가 Supabase에서 직접 읽어야 하면 `telemetry_log`에 anon `select` 정책을 추가한다. 쓰기는 항상 recorder(service_role)만 한다.
- **데이터가 계속 쌓인다**: `dog`가 로봇견당 1초에 1건이라 하루에 약 8만 6천 행이다. 오래된 행과 사진을 정리하는 계획이 필요하다(예: 30일 지난 `dog` 행 삭제).
- **Supabase 무료 프로젝트는 일정 기간 사용이 없으면 일시 중지된다.** 중지되면 recorder가 `insert failed`를 낸다. 대시보드에서 Restore 하면 된다.
