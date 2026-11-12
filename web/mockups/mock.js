/* 대시보드 시안 공통 코드
   - 가짜 시나리오 시뮬레이터: MAP → PATROL → SUSPECT → CONTAIN → HANDOVER → REGROUP → PATROL ... 반복
   - 지도 그리기, 카메라(모의 영상) 그리기
   실제 서버(/ws, /api/camera/*) 없이 파일만 열어도 움직인다. 시안이 정해지면 이 가짜 데이터 자리에
   실제 WebSocket 메시지를 연결한다. */
(function (G) {
  "use strict";

  const ARENA = {
    width: 6, height: 4,
    exits: [{ id: "출입구 1", a: [0, 1.5], b: [0, 2.3] }, { id: "출입구 2", a: [6, 1.5], b: [6, 2.3] }],
    obstacles: [{ x: 1.6, y: 2.6, w: 0.5, h: 0.5, name: "책상" }, { x: 3.9, y: 0.9, w: 0.6, h: 0.4, name: "선반" }],
  };
  const MISSIONS = [
    { key: "MAP", ko: "지도 만들기" },
    { key: "PATROL", ko: "순찰" },
    { key: "SUSPECT", ko: "의심·확인" },
    { key: "CONTAIN", ko: "포위" },
    { key: "HANDOVER", ko: "인계" },
    { key: "REGROUP", ko: "줄 다시 서기" },
  ];
  const HOT = new Set(["SUSPECT", "CONTAIN", "HANDOVER"]);
  const ROLE = { A: "정찰견", B: "확인견 1", C: "확인견 2" };
  const ROLE_SUB = { A: "LiDAR · 앞장", B: "A의 마커 따라감", C: "B의 마커 따라감" };
  const STATE_KO = {
    walking: "걷는 중", idle: "대기", moving: "포위 이동", watching: "감시 중",
    returning: "복귀 중", stopped: "비상 정지", paused: "일시정지",
  };
  const DOGS = ["A", "B", "C"];
  const AHEAD = { B: "A", C: "B" };
  const MARKER_ID = { A: 1, B: 2 };
  const FONT = '"Pretendard","Apple SD Gothic Neo","Malgun Gothic","Noto Sans KR",system-ui,sans-serif';
  const V = 0.35, GAP = 0.6, CELL = 0.1, GW = 60, GH = 40, MAP_T = 20;

  const rad = (d) => (d * Math.PI) / 180;
  const deg = (r) => (r * 180) / Math.PI;
  const clamp = (x, a, b) => Math.max(a, Math.min(b, x));
  const lerp = (a, b, k) => a + (b - a) * k;
  const ease = (k) => (k < 0.5 ? 2 * k * k : 1 - Math.pow(-2 * k + 2, 2) / 2);
  const angTo = (a, b) => deg(Math.atan2(b.y - a.y, b.x - a.x));
  const angDiff = (a, b) => ((((b - a) % 360) + 540) % 360) - 180;
  const polar = (p, a, r) => ({ x: p.x + Math.cos(rad(a)) * r, y: p.y + Math.sin(rad(a)) * r });
  const fmtTime = (ts) => new Date(ts).toLocaleTimeString("en-GB", { hour12: false });
  const fmtDur = (s) => `${String(Math.floor(s / 60)).padStart(2, "0")}:${String(Math.floor(s % 60)).padStart(2, "0")}`;
  const battPct = (v) => clamp(((v - 6.4) / (8.4 - 6.4)) * 100, 0, 100);
  const battLevel = (v) => (v < 6.6 ? "alert" : v < 6.8 ? "warn" : "ok");

  // ---------------------------------------------------------------- 순찰 경로
  // 테두리만 도는 대신 방 안을 곡선으로 누비며 장애물 사이를 지나간다.
  // 경유점을 Catmull-Rom 곡선으로 이어 촘촘한 점 목록(PATH)으로 만든다.
  const WAYPOINTS = [
    [0.8, 0.8], [2.3, 0.6], [3.3, 1.7], [4.45, 1.97], [5.4, 1.2], [5.45, 2.92],
    [4.13, 3.57], [3.2, 2.7], [2.5, 3.45], [1.2, 3.45], [0.8, 2.4], [1.5, 1.6],
  ];
  function smoothLoop(pts, n) {
    const out = [], L = pts.length;
    for (let i = 0; i < L; i++) {
      const p0 = pts[(i - 1 + L) % L], p1 = pts[i], p2 = pts[(i + 1) % L], p3 = pts[(i + 2) % L];
      for (let j = 0; j < n; j++) {
        const t = j / n, t2 = t * t, t3 = t2 * t;
        out.push([0, 1].map((k) => 0.5 * (2 * p1[k] + (-p0[k] + p2[k]) * t + (2 * p0[k] - 5 * p1[k] + 4 * p2[k] - p3[k]) * t2 + (-p0[k] + 3 * p1[k] - 3 * p2[k] + p3[k]) * t3)));
      }
    }
    return out;
  }
  const PATH = smoothLoop(WAYPOINTS, 16);
  const CONTAIN_R = 0.6;   // 포위 자리: 침입자에서 0.6 m
  const OUTER_R = 0.8;     // 자리로 갈 때 도는 바깥 원 (다른 개·침입자와 거리를 둠)
  const SEGS = PATH.map((p, i) => {
    const q = PATH[(i + 1) % PATH.length];
    return { p, q, len: Math.hypot(q[0] - p[0], q[1] - p[1]) };
  });
  const LOOP = SEGS.reduce((a, s) => a + s.len, 0);
  function along(s) {
    s = ((s % LOOP) + LOOP) % LOOP;
    for (const g of SEGS) {
      if (s <= g.len) {
        const k = s / g.len;
        return { x: lerp(g.p[0], g.q[0], k), y: lerp(g.p[1], g.q[1], k), h: deg(Math.atan2(g.q[1] - g.p[1], g.q[0] - g.p[0])) };
      }
      s -= g.len;
    }
    return { x: PATH[0][0], y: PATH[0][1], h: 0 };
  }

  // ---------------------------------------------------------------- 벽, 광선
  function outerWalls() {
    const W = ARENA.width, H = ARENA.height;
    const segs = [[[0, 0], [W, 0]], [[0, H], [W, H]]];
    for (const sx of [0, W]) {
      const gaps = ARENA.exits.filter((e) => e.a[0] === sx).map((e) => [e.a[1], e.b[1]]).sort((p, q) => p[0] - q[0]);
      let y = 0;
      for (const [g0, g1] of gaps) { segs.push([[sx, y], [sx, g0]]); y = g1; }
      segs.push([[sx, y], [sx, H]]);
    }
    return segs;
  }
  const OUTER = outerWalls();
  const WALLS = [...OUTER];
  for (const o of ARENA.obstacles) {
    const c = [[o.x, o.y], [o.x + o.w, o.y], [o.x + o.w, o.y + o.h], [o.x, o.y + o.h]];
    for (let i = 0; i < 4; i++) WALLS.push([c[i], c[(i + 1) % 4]]);
  }
  const inObstacle = (x, y, m) => ARENA.obstacles.some((o) => x > o.x - m && x < o.x + o.w + m && y > o.y - m && y < o.y + o.h + m);
  const obsDist = (x, y) => Math.min(...ARENA.obstacles.map((o) => Math.hypot(Math.max(o.x - x, 0, x - o.x - o.w), Math.max(o.y - y, 0, y - o.y - o.h))));
  const wallDist = (x, y) => Math.min(x, ARENA.width - x, y, ARENA.height - y);

  function raySeg(o, d, a, b) {
    const ex = b[0] - a[0], ey = b[1] - a[1];
    const den = d[0] * ey - d[1] * ex;
    if (Math.abs(den) < 1e-9) return null;
    const t = ((a[0] - o[0]) * ey - (a[1] - o[1]) * ex) / den;
    const u = ((a[0] - o[0]) * d[1] - (a[1] - o[1]) * d[0]) / den;
    return t > 0 && u >= 0 && u <= 1 ? t : null;
  }
  function rayCircle(o, d, c, r) {
    const fx = o[0] - c[0], fy = o[1] - c[1];
    const b = fx * d[0] + fy * d[1];
    const disc = b * b - (fx * fx + fy * fy - r * r);
    if (disc < 0) return null;
    const t = -b - Math.sqrt(disc);
    return t > 0 ? t : null;
  }
  function cast(S, x, y, h, max = 6) {
    const d = [Math.cos(rad(h)), Math.sin(rad(h))], o = [x, y];
    let best = max;
    for (const [a, b] of WALLS) { const t = raySeg(o, d, a, b); if (t !== null && t < best) best = t; }
    if (S.target) { const t = rayCircle(o, d, [S.target.x, S.target.y], 0.18); if (t !== null && t < best) best = t; }
    return best;
  }

  // ---------------------------------------------------------------- 시나리오 시간표
  const CYCLE = [["PATROL", 12], ["SUSPECT", 5], ["CONTAIN", 10], ["HANDOVER", 9], ["REGROUP", 7], ["PATROL", 14]];
  const CYC = CYCLE.reduce((a, c) => a + c[1], 0);
  const PHASE_DUR = Object.fromEntries(CYCLE.filter(([m]) => m !== "PATROL"));
  function phaseAt(t) {
    if (t < MAP_T) return { m: "MAP", pt: t, dur: MAP_T };
    let c = (t - MAP_T) % CYC;
    for (const [m, d] of CYCLE) { if (c < d) return { m, pt: c, dur: d }; c -= d; }
    return { m: "PATROL", pt: 0, dur: 1 };
  }

  // ---------------------------------------------------------------- 시뮬레이터
  function createSim() {
    const S = {
      t: 0, s: 1.2, mission: "MAP", pt: 0, dur: MAP_T, paused: false, estop: false,
      dogs: {}, target: null, remembered: [], events: [], evidence: [], evSeq: 0,
      known: new Uint8Array(GW * GH), knownPct: 0, mapDone: false, incident: 0, incidentStart: 0,
      scan: [], scanT: 0, slots: null, routes: null, lineH: 0, lowWarned: false,
    };
    const batt = { A: 7.62, B: 7.41, C: 6.86 };
    DOGS.forEach((n, i) => {
      const p = along(S.s - i * GAP);
      S.dogs[n] = {
        name: n, x: p.x, y: p.y, h: p.h, state: "idle", stride: 0, turn: 0,
        battery_v: batt[n], rssi: -55, front_cm: 100, tilt: "ok", online: true,
        follow: AHEAD[n] ? { id: MARKER_ID[AHEAD[n]], dist: GAP, seen: true } : null,
      };
    });

    function ev(level, text) {
      S.events.unshift({ ts: Date.now(), level, text, incident: S.target ? S.target.id : 0 });
      if (S.events.length > 80) S.events.length = 80;
      S.evSeq++;
    }

    function enter(m, prev) {
      const D = S.dogs;
      if (m === "PATROL") {
        if (prev === "MAP") { S.mapDone = true; S.known.fill(1); S.knownPct = 100; ev("info", "지도 완성 · 지도를 고정하고 순찰을 시작합니다"); }
        else if (prev === "REGROUP") ev("info", "줄 다시 서기 완료 · 멈췄던 곳부터 순찰 재개");
      } else if (m === "SUSPECT") {
        S.incident += 1; S.incidentStart = S.t; S.lineH = D.A.h;
        // 포위할 공간(바깥 원 + 여유)이 있고 A가 직접 볼 수 있는 빈 바닥 중, A와 가까운 곳에 침입자가 나타난다.
        const need = OUTER_R + 0.15;
        const seen = (p) => cast(S, D.A.x, D.A.y, angTo(D.A, p)) >= Math.hypot(p.x - D.A.x, p.y - D.A.y) - 0.05;
        const cands = [];
        for (let gx = 0.5; gx < ARENA.width; gx += 0.1) for (let gy = 0.5; gy < ARENA.height; gy += 0.1) {
          const p = { x: gx, y: gy }, dA = Math.hypot(p.x - D.A.x, p.y - D.A.y);
          if (dA < 0.9 || wallDist(gx, gy) < need || obsDist(gx, gy) < need || !seen(p)) continue;
          if (["B", "C"].some((n) => Math.hypot(D[n].x - gx, D[n].y - gy) < 0.8)) continue;
          cands.push({ x: gx, y: gy, dA, old: S.remembered.some((r) => Math.hypot(r.x - gx, r.y - gy) < 0.8) });
        }
        const pool = cands.some((c) => !c.old) ? cands.filter((c) => !c.old) : cands;
        let best = pool.sort((p, q) => p.dA - q.dA)[0] || polar(D.A, D.A.h + 90, 1.0);
        const { x, y } = best;
        S.target = { x, y, id: S.incident, kind: "확인 중", conf: 0 };
        ev("warn", `정찰견 A: 지도에 없는 물체 발견 (${Math.hypot(x - D.A.x, y - D.A.y).toFixed(1)} m 앞) → 멈추고 확인`);
      } else if (m === "CONTAIN") {
        const T = S.target;
        const plan = planContain(T, D);
        S.slots = plan.slots; S.routes = plan.routes; S.sched = plan.sched;
        T.kind = "사람"; T.conf = 0.91;
        ev("alert", `사건 #${T.id} 침입자 확인 (사람 · 신뢰도 0.91) → A·B·C 3방향 포위 이동 (120° 간격, 0.6 m)`);
      } else if (m === "HANDOVER") {
        ev("info", "포위 완료 · 0.6 m 거리 유지 (사람이 30 cm 안으로 오면 물러남)");
        for (const n of DOGS) S.evidence.unshift({ id: S.target.id, dog: n, ts: Date.now(), img: snapshot(S, n) });
        if (S.evidence.length > 12) S.evidence.length = 12;
        ev("info", "증거 사진 3장 저장 · 관리자 인계 대기");
      } else if (m === "REGROUP") {
        const T = S.target;
        ev("info", `사건 #${T.id} 인계 완료 · 이 위치는 기억하고 다시 알리지 않음`);
        S.routes = planRegroup(T, D); S.sched = S.routes.sched;
        S.remembered.push({ x: T.x, y: T.y, id: T.id });
        S.target = null;
      }
    }

    function setDog(d, x, y, h, state, stride, turn) { Object.assign(d, { x, y, h, state, stride, turn }); }

    // ---- 포위 경로 ------------------------------------------------------------
    // 자리 3곳: 침입자 둘레 0.6 m 원 위에 120° 간격. 벽·장애물에 걸리면 전체를 조금씩 돌려서 다시 찾음.
    // 이동: 10 cm 격자 길찾기(A*)로 벽·장애물·침입자(0.5 m 안)·이미 자리 잡은 개를 피해 간다.
    const clearAt = (p, m) => wallDist(p.x, p.y) > m + 0.1 && obsDist(p.x, p.y) > m;
    const NO_GO = 0.5;      // 침입자에게 이보다 가까이 가지 않음
    const DOG_R = 0.32;     // 다른 개(자리)와 이만큼 떨어져서 지나감
    function freeAt(x, y, circles) {
      if (wallDist(x, y) < 0.18 || obsDist(x, y) < 0.18) return false;
      return circles.every((c) => Math.hypot(x - c.x, y - c.y) >= c.r);
    }
    function segFree(p, q, circles) {
      const n = Math.ceil(Math.hypot(q.x - p.x, q.y - p.y) / 0.04);
      for (let i = 1; i < n; i++) if (!freeAt(lerp(p.x, q.x, i / n), lerp(p.y, q.y, i / n), circles)) return false;
      return true;
    }
    function findPath(from, to, circles) {
      const C = 0.1, NX = Math.round(ARENA.width / C), NY = Math.round(ARENA.height / C);
      const cell = (p) => [clamp(Math.floor(p.x / C), 0, NX - 1), clamp(Math.floor(p.y / C), 0, NY - 1)];
      const [sx, sy] = cell(from), [gx, gy] = cell(to), key = (x, y) => y * NX + x;
      const free = new Uint8Array(NX * NY);
      for (let y = 0; y < NY; y++) for (let x = 0; x < NX; x++) free[key(x, y)] = freeAt((x + 0.5) * C, (y + 0.5) * C, circles) ? 1 : 0;
      free[key(sx, sy)] = free[key(gx, gy)] = 1;
      const g = new Float32Array(NX * NY).fill(Infinity), par = new Int32Array(NX * NY).fill(-1), done = new Uint8Array(NX * NY);
      const h = (x, y) => Math.hypot(x - gx, y - gy);
      const open = [[h(sx, sy), sx, sy]]; g[key(sx, sy)] = 0;
      while (open.length) {
        let bi = 0;
        for (let i = 1; i < open.length; i++) if (open[i][0] < open[bi][0]) bi = i;
        const [, x, y] = open.splice(bi, 1)[0], k = key(x, y);
        if (done[k]) continue;
        done[k] = 1;
        if (x === gx && y === gy) break;
        for (let dx = -1; dx <= 1; dx++) for (let dy = -1; dy <= 1; dy++) {
          if (!dx && !dy) continue;
          const nx = x + dx, ny = y + dy;
          if (nx < 0 || ny < 0 || nx >= NX || ny >= NY) continue;
          const nk = key(nx, ny);
          if (!free[nk] || done[nk] || (dx && dy && (!free[key(x + dx, y)] || !free[key(x, y + dy)]))) continue;
          const ng = g[k] + (dx && dy ? Math.SQRT2 : 1);
          if (ng < g[nk]) { g[nk] = ng; par[nk] = k; open.push([ng + h(nx, ny), nx, ny]); }
        }
      }
      const pts = [];
      if (par[key(gx, gy)] < 0 && !(sx === gx && sy === gy)) return [from, to];   // 길이 없으면 직선(드묾)
      for (let k = key(gx, gy); k >= 0; k = par[k]) pts.push({ x: ((k % NX) + 0.5) * C, y: (Math.floor(k / NX) + 0.5) * C });
      pts.reverse(); pts[0] = from; pts[pts.length - 1] = to;
      // 직선으로 갈 수 있는 구간은 펴서 자연스러운 경로로
      const out = [pts[0]];
      let i = 0;
      while (i < pts.length - 1) {
        let j = pts.length - 1;
        while (j > i + 1 && !segFree(pts[i], pts[j], circles)) j--;
        out.push(pts[j]); i = j;
      }
      return out;
    }
    function makeRoute(pts) {
      const cum = [0];
      for (let i = 1; i < pts.length; i++) cum.push(cum[i - 1] + Math.hypot(pts[i].x - pts[i - 1].x, pts[i].y - pts[i - 1].y));
      return { pts, cum, len: cum[cum.length - 1] || 1e-6 };
    }
    function routeAt(R, s) {
      s = clamp(s, 0, R.len);
      let i = 1;
      while (i < R.cum.length - 1 && R.cum[i] < s) i++;
      const p = R.pts[i - 1], q = R.pts[i], seg = R.cum[i] - R.cum[i - 1] || 1e-6, u = clamp((s - R.cum[i - 1]) / seg, 0, 1);
      return { x: lerp(p.x, q.x, u), y: lerp(p.y, q.y, u), h: angTo(p, q) };
    }

    function planContain(T, D) {
      const ok = (ang) => clearAt(polar(T, ang, CONTAIN_R), 0.15);
      const base = angTo(T, D.A);
      let angs = null;
      for (let off = 0; off <= 90 && !angs; off += 10) {
        for (const o of off ? [off, -off] : [0]) {
          const r = [0, 120, -120].map((k) => base + o + k);
          if (r.every(ok)) { angs = r; break; }
        }
      }
      if (!angs) angs = [0, 120, -120].map((k) => base + k);
      const tgt = { x: T.x, y: T.y, r: NO_GO };
      const plan = (asg) => {
        // A 먼저 자리 잡고, B는 A 자리를 피하고, C는 A·B 자리를 피해서 간다.
        const slots = {}, routes = {}, taken = [];
        for (const n of DOGS) {
          slots[n] = { ...polar(T, asg[n], CONTAIN_R), ang: asg[n] };
          routes[n] = makeRoute(findPath({ x: D[n].x, y: D[n].y }, slots[n], [tgt, ...taken]));
          taken.push({ x: slots[n].x, y: slots[n].y, r: DOG_R });
        }
        return { slots, routes };
      };
      const p1 = plan({ A: angs[0], B: angs[1], C: angs[2] }), p2 = plan({ A: angs[0], B: angs[2], C: angs[1] });
      const cost = (p) => p.routes.B.len + p.routes.C.len;
      const best = cost(p1) <= cost(p2) ? p1 : p2;
      best.sched = schedule(best.routes, DOGS, PHASE_DUR.CONTAIN - 1.0);
      return best;
    }
    function planRegroup(T, D) {
      // A가 먼저 줄 맨 앞으로, 그다음 B, C. 아직 자리에 있는 개는 피해서 간다.
      const routes = {}, tgt = { x: T.x, y: T.y, r: NO_GO }, arrived = [];
      DOGS.forEach((n, j) => {
        const end = along(S.s - j * GAP);
        const waiting = DOGS.slice(j + 1).map((m) => ({ x: S.slots[m].x, y: S.slots[m].y, r: DOG_R }));
        routes[n] = makeRoute(findPath(S.slots[n], end, [tgt, ...waiting, ...arrived]));
        routes[n].end = end;
        arrived.push({ x: end.x, y: end.y, r: DOG_R });
      });
      routes.sched = schedule(routes, DOGS, PHASE_DUR.REGROUP - 1.0);
      return routes;
    }
    // 순서대로 움직여서 서로 엉키지 않게 함: A가 도착하면 B 출발, C는 B 뒤 0.6 m 간격으로.
    // 정해진 단계 시간 안에 끝나도록 속도를 맞춤(기본 0.5 m/s).
    function schedule(routes, order, budget) {
      const [p, q, r] = order.map((n) => routes[n].len);
      const v = Math.max(0.5, (p + q) / budget, (p + 0.6 + r) / budget);
      return { v, [order[0]]: 0, [order[1]]: p / v, [order[2]]: (p + 0.6) / v };
    }

    function update(dt) {
      const D = S.dogs;
      if (S.paused || S.estop) {
        for (const n of DOGS) Object.assign(D[n], { stride: 0, turn: 0, state: S.estop ? "stopped" : "paused" });
        return;
      }
      S.t += dt;
      const ph = phaseAt(S.t);
      if (ph.m !== S.mission) { const prev = S.mission; S.mission = ph.m; enter(ph.m, prev); }
      S.pt = ph.pt; S.dur = ph.dur;
      const m = ph.m, k = ph.pt;

      if (m === "MAP" || m === "PATROL") {
        S.s += V * dt;
        DOGS.forEach((n, i) => { const p = along(S.s - i * GAP); setDog(D[n], p.x, p.y, p.h, "walking", 60, 0); });
      } else if (m === "SUSPECT") {
        const a = ease(clamp(k / 1.5, 0, 1));
        D.A.h = S.lineH + angDiff(S.lineH, angTo(D.A, S.target)) * a;
        Object.assign(D.A, { state: "watching", stride: 0, turn: a < 1 ? 20 : 0 });
        for (const n of ["B", "C"]) Object.assign(D[n], { state: "idle", stride: 0, turn: 0 });
      } else if (m === "CONTAIN") {
        for (const n of DOGS) {
          const R = S.routes[n], d = (k - S.sched[n]) * S.sched.v;
          const p = routeAt(R, d), go = d > 0 && d < R.len;
          const h = go ? p.h : d <= 0 ? D[n].h : angTo(p, S.target);
          setDog(D[n], p.x, p.y, h, d >= R.len ? "watching" : "moving", go ? 50 : 0, 0);
        }
      } else if (m === "HANDOVER") {
        for (const n of DOGS) Object.assign(D[n], { state: "watching", stride: 0, turn: 0 });
      } else if (m === "REGROUP") {
        for (const n of DOGS) {
          const R = S.routes[n], d = (k - S.sched[n]) * S.sched.v;
          const p = routeAt(R, d), go = d > 0 && d < R.len;
          setDog(D[n], p.x, p.y, go ? p.h : d <= 0 ? D[n].h : R.end.h, d >= R.len ? "idle" : "returning", go ? 50 : 0, 0);
        }
      }

      for (const n of DOGS) {
        const d = D[n];
        d.battery_v = Math.max(6.55, d.battery_v - 0.0012 * dt);
        d.rssi = Math.round(-52 - (n === "C" ? 12 : n === "B" ? 6 : 0) + Math.sin(S.t * 0.7 + n.charCodeAt(0)) * 4);
        d.front_cm = Math.max(5, Math.round(Math.min(300, cast(S, d.x, d.y, d.h) * 100 - 12)));
        if (d.follow) {
          const a = D[AHEAD[n]];
          d.follow.dist = Math.hypot(a.x - d.x, a.y - d.y);
          d.follow.seen = m === "MAP" || m === "PATROL" || (m === "REGROUP" && k > 5.5);
        }
      }
      if (!S.lowWarned && D.C.battery_v < 6.8) {
        S.lowWarned = true;
        ev("warn", `확인견 C 배터리 낮음 (${D.C.battery_v.toFixed(2)} V) · 시연 후 충전 필요`);
      }

      if (!S.mapDone) {
        const R = 2.6, A = D.A;
        let cnt = 0;
        for (let iy = Math.max(0, Math.floor((A.y - R) / CELL)); iy < Math.min(GH, Math.ceil((A.y + R) / CELL)); iy++)
          for (let ix = Math.max(0, Math.floor((A.x - R) / CELL)); ix < Math.min(GW, Math.ceil((A.x + R) / CELL)); ix++)
            if (Math.hypot((ix + 0.5) * CELL - A.x, (iy + 0.5) * CELL - A.y) < R) S.known[iy * GW + ix] = 1;
        for (let i = 0; i < S.known.length; i++) cnt += S.known[i];
        S.knownPct = Math.round((cnt / S.known.length) * 100);
      }

      S.scanT += dt;
      if (S.scanT > 0.2) {
        S.scanT = 0;
        const A = D.A;
        S.scan = [];
        for (let a = 0; a < 360; a += 3) {
          const r = cast(S, A.x, A.y, a, 6);
          if (r < 5.9) S.scan.push([A.x + Math.cos(rad(a)) * r, A.y + Math.sin(rad(a)) * r]);
        }
      }
    }

    ev("info", "시안 모드: 가짜 데이터로 시나리오를 반복 재생합니다");
    ev("info", "정찰견 A가 앞장서서 지도 만들기 시작 (MAP)");

    return {
      S, update,
      togglePause() { S.paused = !S.paused; ev("info", S.paused ? "대시보드에서 일시정지" : "순찰 재개"); },
      toggleEstop() {
        S.estop = !S.estop;
        ev(S.estop ? "alert" : "info", S.estop ? "비상 정지! 모든 로봇견에 stop 명령 전송" : "비상 정지 해제");
      },
      handoverDone() { if (S.mission === "HANDOVER") S.t += S.dur - S.pt + 0.01; },
      elapsed() { return S.target ? S.t - S.incidentStart : 0; },
    };
  }

  // ---------------------------------------------------------------- 그리기 공통
  function fit(c) {
    const dpr = (typeof window !== "undefined" && window.devicePixelRatio) || 1;
    const w = Math.round(c.clientWidth * dpr), h = Math.round(c.clientHeight * dpr);
    if (w && h && (c.width !== w || c.height !== h)) { c.width = w; c.height = h; }
    return dpr;
  }
  const line = (g, a, b) => { g.beginPath(); g.moveTo(a[0], a[1]); g.lineTo(b[0], b[1]); g.stroke(); };
  const circ = (g, x, y, r) => { g.beginPath(); g.arc(x, y, r, 0, Math.PI * 2); };

  /* 지도. T = 색 테마, opt.focus = {x,y} 이면 그 주변만 확대 (사건 화면용) */
  function drawMap(c, S, T, opt = {}) {
    const px = fit(c), g = c.getContext("2d"), W = c.width, H = c.height;
    if (!W || !H || !c.clientWidth) return;
    let vx = -0.3, vy = -0.3, vw = ARENA.width + 0.6, vh = ARENA.height + 0.6;
    if (opt.focus) { const R = opt.radius || 1.2; vx = opt.focus.x - R; vy = opt.focus.y - R; vw = vh = 2 * R; }
    const sc = Math.min(W / vw, H / vh);
    const left = (W - vw * sc) / 2, bottom = (H - vh * sc) / 2;
    const P = (x, y) => [left + (x - vx) * sc, H - bottom - (y - vy) * sc];
    const D = S.dogs;
    g.setTransform(1, 0, 0, 1, 0, 0);
    g.lineCap = "round";
    g.fillStyle = T.bg; g.fillRect(0, 0, W, H);
    const [fx, fy] = P(0, ARENA.height);
    g.fillStyle = T.floor; g.fillRect(fx, fy, ARENA.width * sc, ARENA.height * sc);

    g.strokeStyle = T.grid; g.lineWidth = 1;
    for (let x = 0; x <= ARENA.width + 1e-6; x += 0.5) line(g, P(x, 0), P(x, ARENA.height));
    for (let y = 0; y <= ARENA.height + 1e-6; y += 0.5) line(g, P(0, y), P(ARENA.width, y));

    g.setLineDash([4 * px, 6 * px]); g.strokeStyle = T.path; g.lineWidth = 1.5 * px;
    g.beginPath(); PATH.forEach((p, i) => (i ? g.lineTo(...P(...p)) : g.moveTo(...P(...p)))); g.closePath(); g.stroke();
    g.setLineDash([]);

    g.font = `${10 * px}px ${FONT}`;
    for (const o of ARENA.obstacles) {
      const [x0, y0] = P(o.x, o.y + o.h);
      g.fillStyle = T.obstacle; g.fillRect(x0, y0, o.w * sc, o.h * sc);
      g.fillStyle = T.muted; g.textAlign = "center"; g.textBaseline = "middle";
      g.fillText(o.name, x0 + (o.w * sc) / 2, y0 + (o.h * sc) / 2);
    }
    g.textAlign = "left"; g.textBaseline = "alphabetic";

    g.strokeStyle = T.wall; g.lineWidth = 3 * px;
    for (const [a, b] of OUTER) line(g, P(...a), P(...b));
    g.strokeStyle = T.exit; g.lineWidth = 2 * px; g.setLineDash([3 * px, 4 * px]);
    for (const e of ARENA.exits) line(g, P(...e.a), P(...e.b));
    g.setLineDash([]);
    g.fillStyle = T.exit;
    for (const e of ARENA.exits) {
      const [tx, ty] = P(e.a[0], e.b[1] + 0.12);
      g.textAlign = e.a[0] === 0 ? "left" : "right";
      g.fillText(e.id, tx + (e.a[0] === 0 ? 6 : -6) * px, ty);
    }
    g.textAlign = "left";

    if (!S.mapDone) {
      g.fillStyle = T.fog;
      for (let iy = 0; iy < GH; iy++)
        for (let ix = 0; ix < GW; ix++)
          if (!S.known[iy * GW + ix]) {
            const [x0, y0] = P(ix * CELL, (iy + 1) * CELL);
            g.fillRect(x0, y0, CELL * sc + 0.8, CELL * sc + 0.8);
          }
    }

    g.fillStyle = T.lidar;
    for (const [x, y] of S.scan) { const [X, Y] = P(x, y); g.fillRect(X - 1.5 * px, Y - 1.5 * px, 3 * px, 3 * px); }

    g.font = `${10 * px}px ${FONT}`;
    for (const r of S.remembered) {
      const [X, Y] = P(r.x, r.y);
      g.strokeStyle = T.muted; g.lineWidth = 1.5 * px; circ(g, X, Y, 6 * px); g.stroke();
      line(g, [X - 3 * px, Y], [X + 3 * px, Y]);
      g.fillStyle = T.muted; g.fillText(`#${r.id} 알림 완료`, X + 9 * px, Y + 3 * px);
    }

    if (S.target) {
      const t = S.target, [X, Y] = P(t.x, t.y);
      g.strokeStyle = T.alert; g.lineWidth = 1.5 * px; g.setLineDash([3 * px, 4 * px]);
      circ(g, X, Y, 0.6 * sc); g.stroke(); g.setLineDash([]);
      g.fillStyle = T.alertSoft; circ(g, X, Y, 0.3 * sc); g.fill();
      const pulse = 1 + 0.25 * Math.sin(Date.now() / 160);
      g.fillStyle = t.kind === "사람" ? T.alert : T.warn; circ(g, X, Y, 7 * px * pulse); g.fill();
      g.font = `700 ${11 * px}px ${FONT}`;
      g.fillText(`${t.kind === "사람" ? "침입자" : "확인 중"} #${t.id}`, X + 12 * px, Y - 10 * px);
    }

    if (S.slots && S.target && (S.mission === "CONTAIN" || S.mission === "HANDOVER")) {
      // 포위 자리 3곳(점선 원) + 세 마리를 잇는 포위망 삼각형 + 침입자를 향한 시선
      g.setLineDash([3 * px, 3 * px]); g.lineWidth = 2 * px;
      if (S.mission === "CONTAIN") for (const n of DOGS) { const [X, Y] = P(S.slots[n].x, S.slots[n].y); g.strokeStyle = T.dog[n]; circ(g, X, Y, 12 * px); g.stroke(); }
      g.setLineDash([]);
      const tri = DOGS.map((n) => P(D[n].x, D[n].y)), [TX, TY] = P(S.target.x, S.target.y);
      const done = S.mission === "HANDOVER";
      g.beginPath(); tri.forEach(([X, Y], i) => (i ? g.lineTo(X, Y) : g.moveTo(X, Y))); g.closePath();
      g.globalAlpha = done ? 0.16 : 0.08; g.fillStyle = T.alert; g.fill();
      g.globalAlpha = done ? 0.9 : 0.45; g.strokeStyle = T.alert; g.lineWidth = (done ? 2 : 1.5) * px;
      if (!done) g.setLineDash([5 * px, 4 * px]);
      g.stroke(); g.setLineDash([]);
      g.globalAlpha = 0.35; g.lineWidth = 1 * px;
      for (const [X, Y] of tri) line(g, [X, Y], [TX, TY]);
      g.globalAlpha = 1;
    }

    if (S.mission === "MAP" || S.mission === "PATROL") {
      g.strokeStyle = T.link; g.lineWidth = 1.5 * px; g.setLineDash([2 * px, 4 * px]);
      for (const n of ["B", "C"]) line(g, P(D[n].x, D[n].y), P(D[AHEAD[n]].x, D[AHEAD[n]].y));
      g.setLineDash([]);
    }

    const r = Math.max(9 * px, Math.min(15 * px, 0.13 * sc));
    for (const n of [...DOGS].reverse()) {
      const d = D[n], [X, Y] = P(d.x, d.y), h = rad(d.h);
      g.strokeStyle = T.dog[n]; g.lineWidth = 2.5 * px;
      line(g, [X, Y], [X + Math.cos(h) * r * 2, Y - Math.sin(h) * r * 2]);
      if (n === "A") { g.lineWidth = 1.5 * px; circ(g, X, Y, r + 4 * px); g.stroke(); }
      g.fillStyle = T.dog[n]; circ(g, X, Y, r); g.fill();
      g.fillStyle = T.onDog; g.font = `700 ${Math.round(r * 1.05)}px ${FONT}`;
      g.textAlign = "center"; g.textBaseline = "middle"; g.fillText(n, X, Y + 0.5 * px);
      g.textAlign = "left"; g.textBaseline = "alphabetic";
    }

    const len = opt.focus ? 0.5 : 1;
    const [sx, sy] = P(opt.focus ? vx + 0.15 : 0, opt.focus ? vy + 0.12 : 0);
    g.strokeStyle = T.muted; g.lineWidth = 2 * px;
    line(g, [sx, sy + (opt.focus ? 0 : 12 * px)], [sx + len * sc, sy + (opt.focus ? 0 : 12 * px)]);
    g.fillStyle = T.muted; g.font = `${10 * px}px ${FONT}`;
    g.fillText(`${len} m`, sx + len * sc + 5 * px, sy + (opt.focus ? 4 : 16) * px);
  }

  // ---------------------------------------------------------------- 카메라 모의 영상
  const BITS = { 1: [1, 0, 1, 1, 0, 1, 0, 0, 1, 1, 0, 1, 0, 0, 1, 0], 2: [0, 1, 1, 0, 1, 0, 0, 1, 0, 1, 0, 1, 1, 1, 0, 0] };

  function rrect(g, x, y, w, h, r) {
    g.beginPath();
    if (g.roundRect) g.roundRect(x, y, w, h, r); else g.rect(x, y, w, h);
  }
  function tag(g, x, y, text, bg) {
    g.font = `600 11px ${FONT}`;
    const w = g.measureText(text).width + 10;
    x = clamp(x, 2, 318 - w); y = clamp(y, 2, 222);
    g.fillStyle = bg; g.fillRect(x, y, w, 16);
    g.fillStyle = "#fff"; g.fillText(text, x + 5, y + 12);
  }
  function person(g, x, base, s, T, early) {
    g.fillStyle = "#15181e";
    circ(g, x, base - 118 * s, 13 * s); g.fill();
    rrect(g, x - 22 * s, base - 102 * s, 44 * s, 66 * s, 10 * s); g.fill();
    g.fillRect(x - 17 * s, base - 40 * s, 13 * s, 40 * s);
    g.fillRect(x + 4 * s, base - 40 * s, 13 * s, 40 * s);
    const col = early ? "#f5b942" : "#ff4d4f";
    g.strokeStyle = col; g.lineWidth = 2;
    g.strokeRect(x - 32 * s, base - 138 * s, 64 * s, 142 * s);
    tag(g, x - 32 * s, base - 138 * s - 17, early ? "확인 중…" : `사람 ${T.conf.toFixed(2)}`, col);
  }
  function dogBack(g, x, base, s, id, dist) {
    g.fillStyle = "#2a2e35";
    for (const lx of [-46, -30, 22, 38]) g.fillRect(x + lx * s, base - 24 * s, 8 * s, 24 * s);
    g.fillStyle = "#c9ced6";
    rrect(g, x - 56 * s, base - 58 * s, 112 * s, 38 * s, 10 * s); g.fill();
    const m = 40 * s, mx = x - m / 2, my = base - 58 * s - m - 2 * s;
    g.fillStyle = "#fff"; g.fillRect(mx - 4 * s, my - 4 * s, m + 8 * s, m + 8 * s);
    const cell = m / 6, bits = BITS[id] || BITS[1];
    g.fillStyle = "#000"; g.fillRect(mx, my, m, m);
    g.fillStyle = "#fff";
    for (let i = 0; i < 16; i++) if (bits[i]) g.fillRect(mx + (1 + (i % 4)) * cell, my + (1 + Math.floor(i / 4)) * cell, cell + 0.3, cell + 0.3);
    g.strokeStyle = "#3ecf8e"; g.lineWidth = 2;
    g.strokeRect(mx - 6 * s, my - 6 * s, m + 12 * s, m + 12 * s);
    tag(g, mx - 6 * s, my - 6 * s - 17, `ID ${id} · ${dist.toFixed(2)} m`, "#1f9d63");
  }

  /* 카메라 한 대의 모의 영상. 실제로는 <img src="/api/camera/A/stream"> 자리 */
  function drawCam(c, n, S) {
    if (c.isConnected) fit(c);
    const g = c.getContext("2d"), W = c.width, H = c.height;
    if (!W || !H) return;
    const k = Math.max(W / 320, H / 240);
    g.setTransform(k, 0, 0, k, (W - 320 * k) / 2, (H - 240 * k) / 2);
    const d = S.dogs[n];
    const sway = Math.sin(S.t * 3 + n.charCodeAt(0)) * (d.state === "walking" ? 3 : 0.4);
    const sky = g.createLinearGradient(0, 0, 0, 112);
    sky.addColorStop(0, "#5a6473"); sky.addColorStop(1, "#444c59");
    g.fillStyle = sky; g.fillRect(0, 0, 320, 112);
    const fl = g.createLinearGradient(0, 112, 0, 240);
    fl.addColorStop(0, "#3a404a"); fl.addColorStop(1, "#22262d");
    g.fillStyle = fl; g.fillRect(0, 112, 320, 128);
    g.strokeStyle = "rgba(255,255,255,.07)"; g.lineWidth = 1;
    for (let i = -5; i <= 5; i++) line(g, [160 + i * 14 + sway, 112], [160 + i * 80 + sway * 2, 240]);
    for (const yy of [124, 142, 170, 210]) line(g, [0, yy], [320, yy]);

    const T = S.target;
    let drew = false;
    if (T && (S.mission === "SUSPECT" || S.mission === "CONTAIN" || S.mission === "HANDOVER")) {
      const dist = Math.hypot(T.x - d.x, T.y - d.y), off = angDiff(d.h, angTo(d, T));
      if (Math.abs(off) < 33) {
        person(g, 160 - (off / 33) * 160 + sway, 214, clamp(1.0 / dist, 0.55, 1.35), T, T.kind !== "사람");
        drew = true;
      }
    }
    if (!drew && d.follow && d.follow.seen) dogBack(g, 160 + sway * 2, 206, clamp(0.6 / d.follow.dist, 0.55, 1.5), d.follow.id, d.follow.dist);
    if (S.estop) { g.fillStyle = "rgba(0,0,0,.45)"; g.fillRect(0, 0, 320, 240); }
    g.setTransform(1, 0, 0, 1, 0, 0);
  }

  function snapshot(S, n) {
    if (typeof document === "undefined") return null;
    const c = document.createElement("canvas");
    c.width = 320; c.height = 240;
    drawCam(c, n, S);
    const g = c.getContext("2d");
    g.fillStyle = "rgba(0,0,0,.6)"; g.fillRect(0, 218, 320, 22);
    g.fillStyle = "#fff"; g.font = `12px ${FONT}`;
    g.fillText(`사건 #${S.incident} · Dog ${n} · ${fmtTime(Date.now())}`, 8, 233);
    return c.toDataURL("image/jpeg", 0.85);
  }

  // ---------------------------------------------------------------- 반복 실행 + 시안 이동 메뉴
  function run(sim, onFrame, onDom) {
    let last = performance.now(), domT = 1;
    function loop(now) {
      const dt = Math.min(0.1, (now - last) / 1000);
      last = now;
      sim.update(dt);
      onFrame(dt);
      domT += dt;
      if (domT > 0.25) { domT = 0; onDom(); }
      requestAnimationFrame(loop);
    }
    requestAnimationFrame(loop);
  }

  function nav(current) {
    const el = document.createElement("nav");
    el.setAttribute("aria-label", "시안 이동");
    el.style.cssText = "position:fixed;right:14px;bottom:14px;z-index:50;display:flex;gap:4px;padding:5px;border-radius:999px;" +
      "background:rgba(20,24,32,.88);box-shadow:0 4px 18px rgba(0,0,0,.3);font:600 12px " + FONT;
    const items = [["index.html", "진단"], ["design1.html", "1안"], ["design2.html", "2안"], ["design3.html", "3안"]];
    el.innerHTML = items.map(([href, label]) =>
      `<a href="${href}" style="padding:6px 12px;border-radius:999px;text-decoration:none;color:${href === current ? "#0b0f15" : "#d6dde8"};` +
      `background:${href === current ? "#ffffff" : "transparent"}">${label}</a>`).join("");
    document.body.appendChild(el);
  }

  G.Mock = {
    ARENA, MISSIONS, HOT, ROLE, ROLE_SUB, STATE_KO, DOGS, FONT,
    createSim, drawMap, drawCam, run, nav, fmtTime, fmtDur, battPct, battLevel,
  };
})(typeof window !== "undefined" ? window : globalThis);
