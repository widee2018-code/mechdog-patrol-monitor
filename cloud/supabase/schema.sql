-- 초안: MechDog 텔레메트리 기록 (recorder/README.md 참고)
-- Supabase SQL Editor에서 실행한다.

create table if not exists telemetry_log (
  id      bigint generated always as identity primary key,
  ts      timestamptz not null,
  type    text not null,   -- event / mission / sound / dog / intruder / camera / command / ready / alarm
  source  text not null,   -- 메시지의 from: A, B, C, pi, monitor
  level   text,            -- event 메시지의 info / warn / alert
  payload jsonb not null,  -- 원본 메시지 (이미지는 빼고 Storage 경로로 바꿔 넣음)
  unique (type, source, ts)  -- recorder 재접속 때 다시 오는 메시지 중복 방지
);

create index if not exists telemetry_log_ts_idx on telemetry_log (ts desc);

-- RLS: 쓰기는 recorder의 service_role 키만 (RLS를 우회함). 웹에서 조회가 필요해지면 anon 읽기 정책을 추가한다.
alter table telemetry_log enable row level security;

-- 증거 사진 버킷 (비공개)
insert into storage.buckets (id, name, public) values ('evidence', 'evidence', false)
on conflict (id) do nothing;
