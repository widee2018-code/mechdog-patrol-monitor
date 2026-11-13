# Supabase — 지금 상태

마지막 확인: 2026-11-13. 프로젝트 `mechdog_patrol_monitor` (도쿄 리전).
**데이터베이스 구축은 완료됐다.** 2절은 필요할 때 참고하는 선택 사항이다.

## 1. 지금 되는 것 (확인 완료)

| 항목 | 결과 |
|---|---|
| 테이블 | `public.telemetry_log` (RLS 켜짐). 마이그레이션 `telemetry_log_and_evidence_bucket` 적용됨. |
| 로봇견 움직임 | `dog` 행에 `state`(`moving`/`stopped`), `front_cm`, `rssi`가 1초마다 기록된다. 걸을 때 `moving`이 찍히고 앞 거리가 바뀌는 것을 확인했다. |
| 이벤트 | 온라인/오프라인, 명령 대기, "카메라 실시간 영상 연결됨"이 `event` 행으로 기록된다. |
| 증거 사진 | `evidence: true`인 `camera` 메시지의 JPEG가 Storage `evidence` 버킷(비공개)에 올라가고, 행에는 `storage_path`만 남는다(수동 테스트로 확인). |
| 보안 점검 | 경고 1건(INFO): RLS는 켜져 있고 정책이 없다. 쓰기는 recorder(service_role)만 하므로 의도한 설정이다. |
| 증거 버킷 RLS (이슈 #19) | 확인 완료(아래 표). 지금 상태가 맞다. |

코드와 설명서는 `recorder/`, `recorder/README.md`에 있다.

### 증거 버킷 RLS 확인 결과 (2026-11-13)

설정: `evidence` 버킷은 비공개(`public=false`), `storage.objects`와 `telemetry_log` 모두 RLS 켜짐, 정책은 하나도 없음(기본이 전부 거부).

`anon` 키로 실제로 접근해 본 결과:

| 시도 | 결과 |
|---|---|
| `telemetry_log` 읽기 | 200, 빈 목록 `[]` (행이 있어도 보이지 않음) |
| `telemetry_log` 쓰기 | 401, RLS 위반 |
| `evidence` 파일 목록 | 200, 빈 목록 `[]` |
| 증거 사진 내려받기 (인증 경로 / 공개 경로) | 404 / 404 |
| `evidence`에 파일 올리기 | 403 |

- `anon`, `authenticated` 역할에 테이블 권한(GRANT)이 넓게 부여돼 있다(Supabase 기본값). 지금은 RLS가 막아 주지만 **RLS가 유일한 방어선**이다. 정책을 추가할 때 `using (true)`처럼 넓게 열지 말고, 필요한 `select` 하나만 조건을 좁혀서 추가한다.
- 브라우저에 증거 사진을 보여야 하면(이슈 #20) 버킷을 공개로 바꾸지 말고 서버가 만든 서명된 URL(signed URL)을 쓴다. 사진에 사람이나 장소가 찍힐 수 있다.

## 2. 나중에 필요할 때 (DB 구축은 끝났고, 아래는 선택 사항)

### A. 실제 시나리오 증거 저장 확인  (시나리오를 돌릴 때)
- 지금까지는 수동으로 만든 증거 1건만 확인했다. Dog A가 ALERT / INTERCEPT / CONTAIN 중에 보내는 증거도 같은 경로로 저장되는지 본다.
- 확인 쿼리(SQL Editor):
  ```sql
  select ts, source, payload->>'note' as note, payload->>'storage_path' as path
  from telemetry_log where type='camera' order by ts desc limit 10;
  ```
  Storage → `evidence`에도 파일이 생겼는지 본다.
- 테스트로 올린 `A/1794491578126.jpg`(`note: manual snapshot test`)와 그 `camera` 행은 필요 없으면 지운다.

### B. 키 점검  (키가 노출됐을 때)
- `cloud/supabase/.env`의 `SUPABASE_SERVICE_KEY`는 RLS를 우회하는 키다. 이 파일에만 두고, 채팅·깃·`web/`에 올리지 않는다(`.env`는 `.gitignore`로 제외돼 있다).
- 한 번이라도 노출됐다면 Project Settings → API Keys에서 재발급하고 `.env`를 갱신한다.

### C. 대시보드가 DB를 직접 읽을지 결정
- 지금은 hub 데이터로 대시보드를 그린다. 그대로면 할 일 없음.
- 브라우저가 Supabase에서 과거 기록을 읽어야 하면 `telemetry_log`에 anon `select` 정책을 추가한다. 쓰기는 계속 recorder만 한다.
- 증거 사진을 브라우저에 보이려면 버킷이 비공개라서 서명된 URL(signed URL)이나 별도 정책이 필요하다.

### D. 데이터 보관 정책
- `dog`가 로봇견 1대당 1초에 1건이라 하루 약 8만 6천 행(3대면 약 26만 행)이다.
- 정할 것: 보관 기간, 오래된 `dog` 행과 사진 삭제 방법(예: 30일 지난 `dog` 행 삭제). `event`와 증거는 더 오래 둔다.
- 무료 용량(DB 500 MB)을 넘기기 전에 정한다.

### E. 프로젝트 관리
- **일시 중지**: 무료 프로젝트는 사용이 없으면 중지된다. 중지되면 recorder가 `insert failed`를 낸다. 대시보드에서 Restore 한다.
- **다른 프로젝트와 혼동 주의**: 계정에 `esp32s3-dashboard`, `261027_BE project DB`도 있다(둘 다 일시 중지 상태). 이 프로젝트는 `mechdog_patrol_monitor`다.
- **스키마 파일 동기화**: `cloud/supabase/schema.sql`이 실제 DB와 같은지 팀원이 쓰기 전에 한 번 비교한다.

## 3. Supabase에서 직접 확인하는 법

1. https://supabase.com/dashboard → 프로젝트 `mechdog_patrol_monitor`.
2. **Table Editor → `telemetry_log`**: 행 확인. `type`으로 `dog`/`event`/`camera`를 골라 본다.
3. **Storage → `evidence` → `A/`**: 증거 사진 미리보기.
4. **SQL Editor**:
   ```sql
   select type, source, count(*), max(ts)
   from telemetry_log group by 1, 2 order by 4 desc;
   ```
