-- 추론 탭(얼굴·행동) 기록 테이블 (적용됨 2026-11-18, 마이그레이션 inference_log_single_table).
-- 대시보드 [추론] 탭의 클래스만 한 테이블에 적는다. kind 가 face(얼굴 판정) 또는 pose(행동)이다.
-- 얼굴 이미지와 특징값은 저장하지 않는다 (개인정보). 관제실 기록(telemetry_log)과 별도 테이블이라
-- recorder 의 기존 기록과 incidents 뷰에 영향이 없다.
-- 쓰기는 recorder 의 service_role 키만 한다. RLS 를 켜고 정책을 만들지 않아 anon 은 읽기도 쓰기도 안 된다.
-- unique 는 recorder 재접속 때 다시 오는 메시지를 중복 저장하지 않기 위한 것이고, recorder 의 on_conflict 와 같아야 한다.

create table if not exists public.inference_log (
  id         bigint generated always as identity primary key,
  ts         timestamptz not null,   -- 감지 시각
  kind       text not null,          -- face(얼굴 판정) / pose(행동)
  source     text not null,          -- 어느 카메라인지: A, B, C
  label      text not null,          -- 클래스: face = culprit·unknown·hold / pose = fall·still·strike·kick·rush·reach·crouch·handsup·loiter
  track      integer,                -- 추적 번호 (있을 때만)
  confidence real,                   -- 신뢰도 0~1 (있을 때만)
  incident   integer,                -- 사건 번호 (있을 때만)
  unique (kind, source, ts, label)
);

create index if not exists inference_log_label_idx on public.inference_log (kind, label, ts desc);
create index if not exists inference_log_ts_idx on public.inference_log (ts desc);

alter table public.inference_log enable row level security;
revoke all on public.inference_log from anon, authenticated;
