-- 적용됨 (마이그레이션 incidents_view, 2026-11-13): 사건(incident) 뷰. 이슈 #19 "사건 단위로 묶어 보기", #20 "시연 기록 조회".
-- telemetry_log 의 mission 상태 변화로 사건을 나누고, 사건 시간 범위 안의 intruder / alarm / camera 행을 붙인다.
-- 데이터를 새로 쓰지 않는 뷰라서 recorder 와 스키마는 그대로다. Supabase SQL Editor 에서 실행한다.
--
-- 사건 = 발견 계열 상태로 들어간 때부터, 대기/탐사 상태(idle)로 돌아오기 직전까지.
--   새 상태: STANDBY / EXPLORE (idle) → SUSPECT → CONFIRM → CONTAIN → HANDOVER → REGROUP → EXPLORE
--   옛 상태: PATROL (idle) → SUSPECT → ALERT → INTERCEPT → CONTAIN → HANDOVER → RETURN → PATROL
--   SUSPECT 다음에 바로 idle 로 돌아가면 오탐(false_alarm).
--
-- 상태 이름이 바뀌면 아래 state_map 의 값만 고치면 된다. 여기에 없는 상태 이름은 무시된다.
-- 사이렌은 이슈 #8 의 예시(alarm 메시지, payload 의 alarm 이 'on' / 'off')를 가정했다. 형식이 확정되면 맞춘다.
-- recorder 가 alarm 을 아직 기록하지 않으므로, 그전까지 siren_* 컬럼은 비어 있다.

create or replace view public.incidents
with (security_invoker = true) as   -- 호출한 사람의 RLS 를 적용한다 (anon 으로는 아무것도 안 보임)
with state_map(state, in_incident, stage) as (
  values
    ('STANDBY',   false, 'idle'),
    ('EXPLORE',   false, 'idle'),
    ('PATROL',    false, 'idle'),
    ('SUSPECT',   true,  'detect'),
    ('CONFIRM',   true,  'confirm'),
    ('ALERT',     true,  'confirm'),
    ('CONTAIN',   true,  'contain'),
    ('INTERCEPT', true,  'contain'),
    ('HANDOVER',  true,  'handover'),
    ('REGROUP',   true,  'regroup'),
    ('RETURN',    true,  'regroup')
),
missions as (   -- mission 상태 변화를 시간순으로
  select t.ts, m.in_incident, m.stage,
         lag(m.in_incident, 1, false) over w as prev_in,
         lead(t.ts) over w as next_ts
  from telemetry_log t
  join state_map m on m.state = t.payload->>'state'
  where t.type = 'mission'
  window w as (order by t.ts)
),
islands as (    -- 사건에 들어간 때마다 번호를 올려서 연속 구간을 묶는다
  select *,
         sum(case when in_incident and not prev_in then 1 else 0 end)
           over (order by ts rows unbounded preceding) as grp
  from missions
),
inc as (
  select grp,
         min(ts)                                      as detected_at,    -- 발견 시각 (첫 SUSPECT)
         min(ts) filter (where stage = 'confirm')     as confirmed_at,
         min(ts) filter (where stage = 'contain')     as contained_at,
         min(ts) filter (where stage = 'regroup')     as regroup_at,     -- 재정렬 시작
         (array_agg(next_ts order by ts desc))[1]     as ended_at,       -- 다음 idle 상태가 된 시각 (아직 진행 중이면 비어 있음)
         bool_or(stage <> 'detect')                   as confirmed
  from islands
  where in_incident
  group by grp
)
select
  row_number() over (order by i.detected_at)                      as incident_no,
  i.detected_at,
  i.confirmed_at,
  i.contained_at,
  i.regroup_at,
  i.ended_at,
  extract(epoch from i.regroup_at - i.detected_at)::int           as sec_to_regroup,  -- 발견 → 재정렬 시작
  extract(epoch from i.ended_at   - i.detected_at)::int           as sec_total,       -- 발견 → 탐사 재개
  case when i.ended_at is null then 'in_progress'
       when i.confirmed        then 'confirmed'
       else 'false_alarm' end                                     as outcome,
  pos.x                                                           as intruder_x,      -- 사건 중 처음 잡힌 침입자 위치
  pos.y                                                           as intruder_y,
  siren.started_at                                                as siren_start,
  siren.ended_at                                                  as siren_end,
  ea.path                                                         as evidence_a,      -- Storage evidence 버킷의 경로
  eb.path                                                         as evidence_b,
  ec.path                                                         as evidence_c
from inc i
left join lateral (
  select (payload->>'x')::float as x, (payload->>'y')::float as y
  from telemetry_log
  where type = 'intruder' and payload ? 'x'
    and ts >= i.detected_at and ts <= coalesce(i.ended_at, 'infinity'::timestamptz)
  order by ts limit 1
) pos on true
left join lateral (
  select s.ts as started_at,
         (select min(o.ts) from telemetry_log o
          where o.type = 'alarm' and o.payload->>'alarm' = 'off'
            and o.ts > s.ts and o.ts <= coalesce(i.ended_at, 'infinity'::timestamptz)) as ended_at
  from telemetry_log s
  where s.type = 'alarm' and s.payload->>'alarm' = 'on'
    and s.ts >= i.detected_at and s.ts <= coalesce(i.ended_at, 'infinity'::timestamptz)
  order by s.ts limit 1
) siren on true
left join lateral (
  select payload->>'storage_path' as path from telemetry_log
  where type = 'camera' and source = 'A' and payload ? 'storage_path'
    and ts >= i.detected_at and ts <= coalesce(i.ended_at, 'infinity'::timestamptz)
  order by ts limit 1
) ea on true
left join lateral (
  select payload->>'storage_path' as path from telemetry_log
  where type = 'camera' and source = 'B' and payload ? 'storage_path'
    and ts >= i.detected_at and ts <= coalesce(i.ended_at, 'infinity'::timestamptz)
  order by ts limit 1
) eb on true
left join lateral (
  select payload->>'storage_path' as path from telemetry_log
  where type = 'camera' and source = 'C' and payload ? 'storage_path'
    and ts >= i.detected_at and ts <= coalesce(i.ended_at, 'infinity'::timestamptz)
  order by ts limit 1
) ec on true;

-- 뷰는 기본으로 anon 에게도 열리므로 닫아 둔다. (조회 페이지가 필요해지면 그때 정책과 함께 연다)
revoke all on public.incidents from anon, authenticated;
