// 카메라 추론 시안(얼굴 · 행동)이 함께 쓰는 가짜 데이터와 그리기 도구.
// 진짜 카메라 없이 화면을 확인하려고 만든 것이다. 실제로는 Pi에서 YOLO11n(-pose) 결과가 허브로 오고,
// 아래 classify()와 같은 규칙(후처리)을 Pi 쪽에서 돌려 결과만 보내게 된다.
//
// 좌표: 화면 비율 0~1 (왼쪽 위가 0,0). 키포인트 17개는 COCO 순서 (YOLO11n-pose와 같음).
(function () {
  "use strict";

  // ---------------- 위험 행동 목록 (danger_actions.html 에도 같은 내용) ----------------
  const LEVEL = { high: { ko: "높음", rank: 3 }, mid: { ko: "중간", rank: 2 }, low: { ko: "주의", rank: 1 }, none: { ko: "안전", rank: 0 } };
  const DANGERS = [
    { id: "fall", ko: "넘어짐 · 쓰러짐", level: "high", hold: 0,
      rule: "몸통 기울기 55° 이상 + 엉덩이 높이가 1초 안에 키의 25% 이상 내려감",
      why: "다친 사람일 수도, 로봇과 부딪힌 사고일 수도 있어요. 바로 사람이 확인해야 해요." },
    { id: "still", ko: "쓰러진 채 움직임 없음", level: "high", hold: 3,
      rule: "몸통 기울기 55° 이상이 3초 넘게 이어지고, 관절이 거의 안 움직임",
      why: "의식을 잃었을 수 있어요. 가장 급한 상황이에요." },
    { id: "strike", ko: "팔 휘두름 · 때리려는 자세", level: "high", hold: 0,
      rule: "손목이 어깨보다 위에서 빠르게(초당 키의 1.2배 이상) 2초 안에 2번 이상 움직임",
      why: "로봇이나 사람을 공격하는 행동이에요." },
    { id: "kick", ko: "발차기", level: "high", hold: 0,
      rule: "한쪽 발목이 반대쪽 무릎보다 높이 올라가고 빠르게 움직임",
      why: "로봇견은 낮아서 발차기에 가장 쉽게 맞아요." },
    { id: "rush", ko: "돌진 · 빠르게 다가옴", level: "mid", hold: 0,
      rule: "몸 박스 높이가 1초에 25% 이상 커짐 (카메라 쪽으로 빠르게 옴)",
      why: "로봇에 부딪히거나 붙잡으려는 행동일 수 있어요." },
    { id: "reach", ko: "로봇에 손 뻗음", level: "mid", hold: 1,
      rule: "몸이 화면 높이의 75% 이상(아주 가까움) + 한쪽 팔을 앞으로 쭉 뻗은 상태가 1초",
      why: "로봇을 잡거나 들어 올리려는 행동이에요." },
    { id: "crouch", ko: "웅크림 · 숨기", level: "mid", hold: 2,
      rule: "양쪽 무릎 각도 110° 미만 + 몸통은 세운 채 2초 넘게 멈춤",
      why: "'숨어 있는 침입자' 시나리오와 같아요. 몸을 낮춰 피하려는 행동이에요." },
    { id: "handsup", ko: "양손 들기 (항복 · 도움 요청)", level: "low", hold: 1.5,
      rule: "양쪽 손목이 코보다 높이 1.5초 넘게 있음",
      why: "위험하진 않지만 포위 뒤 '항복' 신호일 수 있어 관제에 알려요." },
    { id: "loiter", ko: "배회 · 서성임", level: "low", hold: 0,
      rule: "같은 사람이 20초 안에 걷는 방향을 3번 넘게 바꿈",
      why: "목적 없이 오가며 살피는 행동이에요. 침입 전 단계일 수 있어요." },
  ];
  const DANGER_BY_ID = Object.fromEntries(DANGERS.map((d) => [d.id, d]));
  const DANGER_KPS = { fall: [5, 6, 11, 12], still: [5, 6, 11, 12], strike: [5, 6, 9, 10], kick: [13, 14, 15, 16], rush: [5, 6, 15, 16],
    reach: [5, 6, 9, 10], crouch: [11, 12, 13, 14, 15, 16], handsup: [0, 9, 10], loiter: [11, 12] };

  // ---------------- 몸 만들기 (키포인트 17개) ----------------
  // 0 코, 1·2 눈, 3·4 귀, 5·6 어깨, 7·8 팔꿈치, 9·10 손목, 11·12 엉덩이, 13·14 무릎, 15·16 발목 (왼쪽, 오른쪽)
  const EDGES = [[5, 6], [5, 7], [7, 9], [6, 8], [8, 10], [5, 11], [6, 12], [11, 12], [11, 13], [13, 15], [12, 14], [14, 16], [0, 1], [0, 2], [1, 3], [2, 4]];
  const KP_KO = ["코", "왼눈", "오른눈", "왼귀", "오른귀", "왼어깨", "오른어깨", "왼팔꿈치", "오른팔꿈치", "왼손목", "오른손목",
    "왼엉덩이", "오른엉덩이", "왼무릎", "오른무릎", "왼발목", "오른발목"];

  // p: { x(화면 가로 위치 0~1), ground(발 높이 0~1), size(키, 화면 높이 비율), tilt(몸 기울기 rad),
  //      legL/legR: [엉덩이각, 무릎각], armL/armR: [어깨각, 팔꿈치각] (0 = 아래로, + = 바깥/위로) }
  function buildPose(p) {
    const T = 0.25, S = 0.25, UA = 0.16, FA = 0.15;
    const pts = new Array(17);
    const leg = (side, [a, k]) => {
      const hip = [side * 0.055, 0];
      const knee = [hip[0] + side * T * Math.sin(a), hip[1] - T * Math.cos(a)];
      const ankle = [knee[0] + side * S * Math.sin(a - k), knee[1] - S * Math.cos(a - k)];
      return [hip, knee, ankle];
    };
    const [lh, lk, la] = leg(-1, p.legL || [0, 0]);
    const [rh, rk, ra] = leg(1, p.legR || [0, 0]);
    const lift = -Math.min(la[1], ra[1]);   // 낮은 발이 바닥(0)에 닿게 올린다
    const ls = [-0.095, 0.3], rs = [0.095, 0.3];
    const arm = (side, sh, [s, e]) => {
      const el = [sh[0] + side * UA * Math.sin(s), sh[1] - UA * Math.cos(s)];
      const wr = [el[0] + side * FA * Math.sin(s + e), el[1] - FA * Math.cos(s + e)];
      return [el, wr];
    };
    const [le, lw] = arm(-1, ls, p.armL || [0.15, 0.1]);
    const [re, rw] = arm(1, rs, p.armR || [0.15, 0.1]);
    const local = [[0, 0.43], [-0.022, 0.452], [0.022, 0.452], [-0.048, 0.44], [0.048, 0.44], ls, rs, le, re, lw, rw, lh, rh, lk, rk, la, ra];
    const tilt = p.tilt || 0, c = Math.cos(tilt), s = Math.sin(tilt);
    for (let i = 0; i < 17; i++) {
      const x0 = local[i][0], y0 = local[i][1] + lift;    // 발바닥이 0 이 되게 올린 좌표
      const xr = x0 * c + y0 * s, yr = -x0 * s + y0 * c;  // 발을 축으로 몸을 기울임 (넘어짐)
      pts[i] = [p.x + xr * p.size, p.ground - yr * p.size, 0.82 + 0.12 * Math.random()];   // [x, y, 신뢰도]
    }
    return pts;
  }

  // ---------------- 행동 시나리오 (가짜 사람이 하는 행동 순서) ----------------
  const SCRIPT = [
    ["walk", 6], ["loiter", 12], ["crouch", 6], ["handsup", 4], ["rush", 3.5], ["reach", 4],
    ["strike", 5], ["kick", 4], ["fall", 1.5], ["still", 6], ["getup", 2.5],
  ];
  const SCRIPT_LEN = SCRIPT.reduce((s, [, d]) => s + d, 0);
  const TRUTH_KO = { walk: "걷기", loiter: "서성임", crouch: "웅크림", handsup: "양손 들기", rush: "돌진", reach: "손 뻗음",
    strike: "팔 휘두름", kick: "발차기", fall: "넘어짐", still: "쓰러져 있음", getup: "일어남" };

  function scriptAt(t) {
    let u = ((t % SCRIPT_LEN) + SCRIPT_LEN) % SCRIPT_LEN;
    for (const [name, dur] of SCRIPT) { if (u < dur) return [name, u, dur]; u -= dur; }
    return ["walk", 0, 1];
  }

  function poseFor(t, base) {
    const [b, u, dur] = scriptAt(t);
    return poseOf(b, u, dur, base);
  }
  // 행동 b 를 시작한 지 u 초 (전체 dur 초) 일 때의 몸
  function poseOf(b, u, dur, base) {
    const w = Math.sin(u * 7), w2 = Math.sin(u * 7 + Math.PI);
    const p = { x: base.x + 0.12, ground: 0.92, size: base.size, tilt: 0, legL: [0, 0], legR: [0, 0], armL: [0.15, 0.1], armR: [0.15, 0.1] };
    const walkLegs = (amp) => { p.legL = [amp * w, Math.max(0, -amp * w) * 1.2]; p.legR = [amp * w2, Math.max(0, -amp * w2) * 1.2];
      p.armL = [0.15 + 0.25 * w2, 0.2]; p.armR = [0.15 + 0.25 * w, 0.2]; };
    switch (b) {
      case "walk": walkLegs(0.3); p.x = base.x - 0.12 + 0.24 * (u / dur); break;
      case "loiter": { walkLegs(0.28); const ph = (u / dur) * 4; const tri = 1 - Math.abs(((ph % 2) + 2) % 2 - 1); p.x = base.x + 0.12 - 0.3 * tri; break; }
      case "crouch": { const k = Math.min(1, u / 0.8); p.legL = [-0.9 * k, -1.9 * k]; p.legR = [-0.9 * k, -1.9 * k];
        p.armL = [0.5 * k, 0.9 * k]; p.armR = [0.5 * k, 0.9 * k]; break; }
      case "handsup": { const k = Math.min(1, u / 0.6); p.armL = [2.9 * k, 0.1]; p.armR = [2.9 * k, 0.1]; break; }
      case "rush": walkLegs(0.5); p.size = base.size * (1 + 0.55 * (u / dur) ** 2); p.ground = 0.92 + 0.05 * (u / dur); break;
      case "reach": p.size = base.size * 1.55; p.ground = 0.98; p.armR = [1.5, 0.05 + 0.05 * Math.sin(u * 3)]; p.armL = [0.3, 0.4]; break;
      case "strike": { const sw = Math.sin(u * 9); p.armR = [1.7 + 1.1 * sw, 0.5 - 0.4 * sw]; p.armL = [0.6, 1.2]; p.legR = [0.15, 0]; break; }
      case "kick": { const kk = Math.max(0, Math.sin(u * 5)); p.legR = [-1.7 * kk, -0.6 * kk]; p.armL = [0.7, 0.3]; p.armR = [0.7, 0.3]; break; }
      case "fall": { const k = Math.min(1, u / 0.9); p.tilt = 1.5 * k * k; p.armL = [0.8 * k, 0.4]; p.armR = [1.2 * k, 0.3]; p.legL = [0.2 * k, 0.3 * k]; break; }
      case "still": p.tilt = 1.5; p.armL = [0.8, 0.4]; p.armR = [1.2, 0.3]; p.legL = [0.2, 0.3]; break;
      case "getup": { const k = 1 - Math.min(1, u / dur); p.tilt = 1.5 * k; p.legL = [-0.6 * k, -1.2 * k]; p.legR = [-0.6 * k, -1.2 * k]; break; }
    }
    return { truth: b, kps: buildPose(p) };
  }

  // ---------------- 후처리: 키포인트 → 위험 행동 판정 (규칙) ----------------
  const mid = (a, b) => [(a[0] + b[0]) / 2, (a[1] + b[1]) / 2];
  const dist = (a, b) => Math.hypot(a[0] - b[0], a[1] - b[1]);
  function angleAt(a, b, c) {   // b 에서의 각도 (도)
    const v1 = [a[0] - b[0], a[1] - b[1]], v2 = [c[0] - b[0], c[1] - b[1]];
    const d = Math.hypot(...v1) * Math.hypot(...v2) || 1;
    return (Math.acos(Math.max(-1, Math.min(1, (v1[0] * v2[0] + v1[1] * v2[1]) / d))) * 180) / Math.PI;
  }
  function boxOf(k) {
    let x0 = 1, y0 = 1, x1 = 0, y1 = 0;
    for (const [x, y] of k) { x0 = Math.min(x0, x); y0 = Math.min(y0, y); x1 = Math.max(x1, x); y1 = Math.max(y1, y); }
    const padY = (y1 - y0) * 0.12 + 0.02, padX = (x1 - x0) * 0.15 + 0.02;
    return [Math.max(0, x0 - padX), Math.max(0, y0 - padY), Math.min(1, x1 + padX), Math.min(1, y1 + 0.02)];
  }
  function features(k) {
    const sh = mid(k[5], k[6]), hp = mid(k[11], k[12]);
    const torso = (Math.atan2(Math.abs(sh[0] - hp[0]), hp[1] - sh[1]) * 180) / Math.PI;   // 0 = 똑바로 섬
    const box = boxOf(k);
    const body = dist(sh, mid(k[15], k[16]));   // 어깨~발목 길이: 팔을 들어도 안 변하는 몸 크기
    return { torso, hipY: hp[1], hipX: hp[0], box, body, h: box[3] - box[1], w: box[2] - box[0], cx: (box[0] + box[2]) / 2,
      kneeL: angleAt(k[11], k[13], k[15]), kneeR: angleAt(k[12], k[14], k[16]), height: dist(k[0], mid(k[15], k[16])) || 0.3 };
  }

  function Classifier() {
    const hist = [];   // {t, k, f}
    const since = {};
    let lastDir = 0, dirChanges = [];
    return function classify(t, k) {
      const f = features(k);
      hist.push({ t, k, f });
      while (hist.length && t - hist[0].t > 20) hist.shift();
      const ago = (sec) => { for (let i = hist.length - 1; i >= 0; i--) if (t - hist[i].t >= sec) return hist[i]; return hist[0]; };
      const p1 = ago(1), p03 = ago(0.25);
      const dt = Math.max(1e-3, t - p03.t);
      const H = Math.max(0.1, f.body);
      const upright = f.torso < 40 && p1.f.torso < 40;   // 서 있는 동안만 판단하는 동작용
      const speed = (i) => dist(k[i], p03.k[i]) / dt / H;   // 초당 키의 몇 배
      const motion = [5, 6, 9, 10, 11, 12, 15, 16].reduce((s, i) => s + speed(i), 0) / 8;
      const raw = {};
      const why = {};

      raw.fall = f.torso > 55 && p1.f.torso < 40 && (f.hipY - p1.f.hipY) > 0.25 * p1.f.h;
      why.fall = `몸 기울기 ${f.torso.toFixed(0)}° · 엉덩이 ${((f.hipY - p1.f.hipY) / Math.max(0.05, p1.f.h) * 100).toFixed(0)}% 내려감`;
      raw.still = f.torso > 55 && motion < 0.15;
      why.still = `몸 기울기 ${f.torso.toFixed(0)}° · 움직임 ${motion.toFixed(2)}`;
      const wristUp = (w, s) => k[w][1] < k[s][1];
      const fastUp = upright && ((wristUp(9, 5) && speed(9) > 1.2) || (wristUp(10, 6) && speed(10) > 1.2));
      const prevHit = hist.length > 1 && hist[hist.length - 2].hit;
      hist[hist.length - 1].hit = fastUp;
      hist[hist.length - 1].swing = fastUp && !prevHit;   // 휘두름 한 번이 시작된 순간
      const hits = hist.filter((h) => t - h.t < 2 && h.swing).length;
      raw.strike = fastUp && hits >= 2;
      why.strike = `손목 속도 ${Math.max(speed(9), speed(10)).toFixed(1)} · 2초 안 ${hits}번`;
      raw.kick = upright && ((k[16][1] < k[13][1] && speed(16) > 0.8) || (k[15][1] < k[14][1] && speed(15) > 0.8));
      why.kick = `발목 높이 > 무릎 · 발목 속도 ${Math.max(speed(15), speed(16)).toFixed(1)}`;
      const grow = (f.body - p1.f.body) / Math.max(0.05, p1.f.body);
      const legsStraight = p1.f.kneeL > 140 && p1.f.kneeR > 140;   // 웅크렸다 일어서는 것과 구분
      raw.rush = grow > 0.25 && upright && legsStraight;
      why.rush = `몸 크기 1초에 ${(grow * 100).toFixed(0)}% 커짐`;
      const armOut = (s, w) => Math.abs(k[w][0] - k[s][0]) / H;
      raw.reach = f.h > 0.75 && Math.max(armOut(5, 9), armOut(6, 10)) > 0.22;
      why.reach = `몸 높이 ${(f.h * 100).toFixed(0)}% · 팔 뻗음 ${Math.max(armOut(5, 9), armOut(6, 10)).toFixed(2)}`;
      raw.crouch = f.kneeL < 110 && f.kneeR < 110 && f.torso < 35 && motion < 0.6;
      why.crouch = `무릎 ${f.kneeL.toFixed(0)}° / ${f.kneeR.toFixed(0)}°`;
      raw.handsup = k[9][1] < k[0][1] && k[10][1] < k[0][1];
      why.handsup = "양 손목이 코보다 위";
      // 배회: 가로 방향이 바뀐 횟수
      const vx = f.hipX - p1.f.hipX;   // 팔다리 흔들림이 아닌 골반 위치로 걷는 방향을 본다
      const dir = Math.abs(vx) < 0.02 ? 0 : Math.sign(vx);
      if (dir && lastDir && dir !== lastDir) dirChanges.push(t);
      if (dir) lastDir = dir;
      dirChanges = dirChanges.filter((x) => t - x < 20);
      const walking = Math.abs(vx) > 0.03 && upright;   // 지금도 옆으로 걷는 중일 때만
      raw.loiter = dirChanges.length >= 3 && walking;
      why.loiter = `20초 안 방향 바꿈 ${dirChanges.length}번`;

      const active = [];
      for (const d of DANGERS) {
        if (raw[d.id]) { since[d.id] = since[d.id] ?? t; } else if (d.id !== "fall" && d.id !== "strike" && d.id !== "kick" && d.id !== "rush") { delete since[d.id]; }
        // 순간 동작(넘어짐·휘두름·발차기·돌진)은 1.5초 동안 표시를 유지
        if ((d.id === "fall" || d.id === "strike" || d.id === "kick" || d.id === "rush") && since[d.id] !== undefined && !raw[d.id] && t - since[d.id] > 1.5) delete since[d.id];
        const held = since[d.id] !== undefined ? t - since[d.id] : -1;
        if (held >= 0 && held >= d.hold) active.push({ id: d.id, ko: d.ko, level: d.level, why: why[d.id], held });
        else if (held >= 0) active.push({ id: d.id, ko: d.ko, level: "pending", why: `${why[d.id]} · ${held.toFixed(1)}/${d.hold}초`, held });
      }
      // 쓰러진 채 움직임 없음이 뜨면 넘어짐은 내린다
      const real = active.filter((a) => a.level !== "pending");
      if (real.some((a) => a.id === "still")) { const i = active.findIndex((a) => a.id === "fall"); if (i >= 0) active.splice(i, 1); }
      const risk = real.reduce((m, a) => (LEVEL[a.level].rank > LEVEL[m].rank ? a.level : m), "none");
      // 규칙에 쓴 숫자들 (행동 4·6안에서 기준선과 함께 보여 준다)
      const m = { torso: f.torso, knee: Math.min(f.kneeL, f.kneeR), wrist: Math.max(speed(9), speed(10)), ankle: Math.max(speed(15), speed(16)),
        grow: grow * 100, size: f.h * 100, arm: Math.max(armOut(5, 9), armOut(6, 10)), motion, turns: dirChanges.length };
      return { f, active, risk, motion, m };
    };
  }

  // 한 카메라의 가짜 행동 추론
  function poseSim(opts = {}) {
    const base = { x: opts.x ?? 0.5, size: opts.size ?? 0.5 };
    const off = opts.offset ?? 0;
    const classify = Classifier();
    let t = 0;
    return {
      step(dt) {
        t += dt;
        const { truth, kps } = poseFor(t + off, base);
        const r = classify(t, kps);
        return { t, truth, truthKo: TRUTH_KO[truth], kps, box: r.f.box, f: r.f, m: r.m, active: r.active, risk: r.risk,
          conf: 0.78 + 0.15 * Math.sin(t * 1.3 + off) };
      },
    };
  }

  // ---------------- 얼굴 추론 (가짜) ----------------
  // 허용 = 팀원(등록), 경고 = 범인 역할(등록), 어느 쪽과도 0.60 미만이면 미등록.
  const REGISTRY = {
    allow: [{ name: "팀원 1", hue: 205 }, { name: "팀원 2", hue: 150 }, { name: "팀원 3", hue: 40 }],
    watch: [{ name: "범인 역할 1", hue: 355 }, { name: "범인 역할 2", hue: 290 }],
  };
  const FACE_RULE = { match: 0.6, votes: 3, window: 5, minPx: 40 };
  const VERDICT = {
    allow: { ko: "허용 (팀원)", color: "#3ecf8e" }, watch: { ko: "경고 (범인 역할)", color: "#ff5a52" },
    unknown: { ko: "미등록 (침입자 의심)", color: "#f5b942" }, hold: { ko: "판정 보류", color: "#7d8ba1" },
  };
  // 사람이 지나가는 순서: [누구(이름 또는 null=모르는 사람), 화면에 있는 시간, 얼굴 크기]
  const FACE_SCRIPT = [
    ["팀원 1", 7, 0.16], [null, 8, 0.15], ["범인 역할 1", 8, 0.17], ["팀원 2", 6, 0.14], [null, 5, 0.06],
    ["범인 역할 2", 7, 0.18], ["팀원 3", 6, 0.15], [null, 7, 0.19],
  ];
  const ALL = [...REGISTRY.allow.map((r) => ({ ...r, list: "allow" })), ...REGISTRY.watch.map((r) => ({ ...r, list: "watch" }))];

  function faceSim(opts = {}) {
    const off = opts.offset ?? 0;
    const rule = opts.rule || FACE_RULE;
    let t = 0, idx = -1, seg = null, trackNo = 0;
    const log = [];
    const total = FACE_SCRIPT.reduce((s, x) => s + x[1] + 2, 0);
    function segAt(tt) {
      let u = ((tt % total) + total) % total;
      for (let i = 0; i < FACE_SCRIPT.length; i++) {
        const [who, dur, sz] = FACE_SCRIPT[i];
        if (u < dur) return { i, who, dur, sz, u };
        u -= dur;
        if (u < 2) return null;   // 2초 동안 아무도 없음
        u -= 2;
      }
      return null;
    }
    return {
      rule, log,
      step(dt) {
        t += dt;
        const s = segAt(t + off);
        if (!s) { if (seg) { seg = null; } return { t, faces: [], log }; }
        if (!seg || s.i !== idx) {
          idx = s.i; trackNo += 1;
          const who = ALL.find((a) => a.name === s.who);
          seg = { track: trackNo, who, sz: s.sz, votes: [], verdict: "hold", announced: false, hue: who ? who.hue : 25 + ((trackNo * 47) % 60) };
        }
        // 얼굴 위치: 왼쪽에서 오른쪽으로 천천히
        const p = s.u / s.dur;
        const cx = 0.25 + 0.5 * p + 0.02 * Math.sin(t * 2), cy = 0.38 + 0.03 * Math.sin(t * 1.3);
        const size = s.sz * (0.9 + 0.2 * Math.sin(p * Math.PI));
        const box = [cx - size / 2, cy - size * 0.62, cx + size / 2, cy + size * 0.62];
        const px = size * (opts.frameW || 640);
        // 등록된 얼굴과의 일치율 (가짜 임베딩 비교)
        const sims = {};
        for (const a of ALL) {
          const base = seg.who && a.name === seg.who.name ? 0.78 : 0.18 + 0.2 * Math.abs(Math.sin(a.hue + seg.track));
          sims[a.name] = Math.max(0, Math.min(0.99, base + 0.06 * Math.sin(t * 3.1 + a.hue) - (px < rule.minPx ? 0.25 : 0)));
        }
        const best = Object.entries(sims).sort((a, b) => b[1] - a[1])[0];
        const bestRow = ALL.find((a) => a.name === best[0]);
        // 후처리 ①: 얼굴이 너무 작으면 판정 보류
        let frameVerdict;
        if (px < rule.minPx) frameVerdict = "hold";
        else if (best[1] >= rule.match) frameVerdict = bestRow.list;
        else frameVerdict = "unknown";
        // 후처리 ②: 최근 N장 중 같은 판정이 M장 이상이어야 확정 (깜빡임 방지)
        seg.votes.push(frameVerdict);
        if (seg.votes.length > rule.window) seg.votes.shift();
        const count = (v) => seg.votes.filter((x) => x === v).length;
        let verdict = "hold";
        for (const v of ["watch", "unknown", "allow"]) if (count(v) >= rule.votes) { verdict = v; break; }
        if (verdict !== seg.verdict && verdict !== "hold") {
          log.unshift({ t, track: seg.track, verdict, name: verdict === "unknown" ? "모르는 사람" : best[0], score: best[1] });
          if (log.length > 40) log.pop();
        }
        seg.verdict = verdict;
        return {
          t, log,
          faces: [{ track: seg.track, box, px, sims, best: best[0], score: best[1], frameVerdict, verdict,
            votes: seg.votes.slice(), hue: seg.hue, name: verdict === "unknown" ? "모르는 사람" : verdict === "hold" ? "확인 중" : best[0], truth: seg.who ? seg.who.name : "모르는 사람" }],
        };
      },
    };
  }


  // ---------------- 얼굴 + 행동 함께 (같은 카메라, 같은 사람) ----------------
  // 사람이 한 명씩 와서 몇 가지 행동을 하고 간다. 얼굴 박스는 몸 점(코·귀)에서 만든다.
  // 팀 결정 (2026-11-18):
  //  - 얼굴 판정은 범인 / 미등록 / 보류 세 가지뿐 (팀원 등록 없음).
  //  - 미등록인 사람이 범인 조건 행동(돌진 · 손 뻗음 · 웅크림 · 팔 휘두름 · 발차기)을 하면 범인으로 등록하고 얼굴을 기억한다.
  //  - 넘어짐 · 쓰러짐은 다친 사람일 수 있어 범인 조건에서 빼고 "구조 필요"로만 표시한다.
  //  - 범인 얼굴과 일치하면(0.60 이상) 다시 와도 바로 범인으로 표시한다.
  const COMBO_VERDICT = {
    unknown: { ko: "미등록", short: "미등록", color: "#ff8a3d" },
    hold: { ko: "판정 보류", short: "보류", color: "#7d8ba1" }, culprit: { ko: "범인", short: "범인", color: "#ff5a52" },
  };
  const CULPRIT_IDS = ["rush", "reach", "crouch", "strike", "kick"];
  const RESCUE_IDS = ["fall", "still"];
  // id: 같은 사람인지 (가짜 얼굴 정체). 마지막은 범인이 다시 오는 장면.
  const VISITS = [
    { id: "P1", hue: 205, acts: [["walk", 5], ["handsup", 3], ["walk", 4]] },
    { id: "P2", hue: 25, acts: [["walk", 4], ["loiter", 9], ["crouch", 5], ["walk", 3]] },
    { id: "P3", hue: 150, acts: [["walk", 4], ["fall", 1.5], ["still", 4], ["getup", 2.5], ["walk", 2]] },
    { id: "P4", hue: 40, far: true, acts: [["walk", 7]] },
    { id: "P5", hue: 290, acts: [["walk", 4], ["rush", 3.5], ["strike", 4], ["kick", 3.5], ["walk", 2]] },
    { id: "P2", hue: 25, acts: [["walk", 7]] },
  ];

  function comboSim(opts = {}) {
    const rule = opts.rule || { ...FACE_RULE };
    const frameW = opts.frameW || 640;
    const GAP = 2;
    const plan = VISITS.map((v) => ({ ...v, dur: v.acts.reduce((s, a) => s + a[1], 0) }));
    const total = plan.reduce((s, v) => s + v.dur + GAP, 0);
    let t = opts.offset || 0, cur = -1, track = 0, person = null, classify = null;
    const log = [], culprits = [], people = new Map();
    const faceBook = [];   // 범인 얼굴 명단 { id(정체), no(범인 번호), hue }
    function at(tt) {
      let u = ((tt % total) + total) % total;
      for (let i = 0; i < plan.length; i++) {
        const v = plan[i];
        if (u < v.dur) { for (const [b, d] of v.acts) { if (u < d) return { i, v, b, u, d }; u -= d; } }
        u -= v.dur; if (u < GAP) return null; u -= GAP;
      }
      return null;
    }
    function note(kind, text, extra) { log.unshift({ t, kind, text, ...extra }); if (log.length > 60) log.pop(); }
    return {
      rule, log, culprits, people, faceBook,
      step(dt) {
        t += dt;
        const s = at(t);
        if (!s) { if (person) { person.gone = t; person = null; } cur = -1; return { t, person: null, log, culprits, people }; }
        if (s.i !== cur || !person) {
          cur = s.i; track += 1; classify = Classifier();
          person = { track, id: s.v.id, hue: s.v.hue, first: t, last: t, frames: 0, votes: [], verdict: "hold",
            culprit: false, culpritWhy: "", culpritNo: 0, acts: new Map(), far: !!s.v.far, rescue: false };
          people.set(track, person);
          if (people.size > 14) { const old = [...people.values()].filter((p) => p !== person).sort((a, b) => a.last - b.last)[0]; if (old) people.delete(old.track); }
        }
        const base = { x: 0.5, size: s.v.far ? 0.3 : 0.5 };
        const kps = poseOf(s.b, s.u, s.d, base).kps;
        const r = classify(t, kps);
        const head = kps[0], ew = Math.max(0.025, Math.abs(kps[3][0] - kps[4][0]) * 1.6);
        const fb = [head[0] - ew / 2, head[1] - ew * 0.75, head[0] + ew / 2, head[1] + ew * 0.55];
        const px = ew * frameW;
        const facing = r.f.torso < 45;   // 쓰러지면 얼굴이 정면이 아니다
        // 범인 얼굴 명단과 비교 (가짜 임베딩)
        let best = null, score = 0;
        for (const c of faceBook) {
          const sim = Math.max(0, Math.min(0.99, (c.id === person.id ? 0.8 : 0.22 + 0.15 * Math.abs(Math.sin(c.hue + track))) + 0.05 * Math.sin(t * 3.1 + c.hue)));
          if (sim > score) { score = sim; best = c; }
        }
        const fv = px < rule.minPx || !facing ? "hold" : best && score >= rule.match ? "culprit" : "unknown";
        person.votes.push(fv); if (person.votes.length > rule.window) person.votes.shift();
        const cnt = (v) => person.votes.filter((x) => x === v).length;
        let verdict = person.verdict;   // 한 번 확정되면 보류로 돌아가지 않는다
        for (const v of ["culprit", "unknown"]) if (cnt(v) >= rule.votes) { verdict = v; break; }
        // 얼굴로 범인을 알아봄 (다시 온 범인)
        if (verdict === "culprit" && !person.culprit) {
          person.culprit = true; person.culpritNo = best.no; person.culpritWhy = `범인 #${best.no} 얼굴과 일치 (${score.toFixed(2)})`; person.culpritAt = t;
          culprits.unshift({ t, track: person.track, no: best.no, why: person.culpritWhy, again: true });
          note("culprit", `🚨 사람 #${person.track} = 범인 #${best.no} 다시 나타남 · 얼굴 ${score.toFixed(2)}`, { track: person.track });
        } else if (verdict !== person.verdict && verdict === "unknown") note("face", `사람 #${person.track} 미등록 (범인 명단에 없음)`, { verdict, track: person.track });
        person.verdict = verdict;
        const real = r.active.filter((a) => a.level !== "pending");
        for (const a of real) {
          const rec = person.acts.get(a.id) || { n: 0, on: false };
          if (!rec.on) { rec.n += 1; note("act", `사람 #${person.track} ${a.ko}${RESCUE_IDS.includes(a.id) ? " · 구조 필요할 수 있음" : ""}`, { level: a.level, id: a.id, track: person.track }); }
          rec.on = true; person.acts.set(a.id, rec);
        }
        for (const [id, rec] of person.acts) if (!real.some((a) => a.id === id)) rec.on = false;
        person.rescue = real.some((a) => RESCUE_IDS.includes(a.id));
        // 범인 규칙: 미등록 + 범인 조건 행동 → 범인 등록, 얼굴을 명단에 저장
        const bad = real.find((a) => CULPRIT_IDS.includes(a.id));
        if (!person.culprit && verdict === "unknown" && bad) {
          const no = faceBook.length + 1;
          faceBook.push({ id: person.id, no, hue: person.hue });
          person.culprit = true; person.culpritNo = no; person.culpritWhy = `미등록 + ${bad.ko}`; person.culpritAt = t;
          culprits.unshift({ t, track: person.track, no, why: person.culpritWhy });
          note("culprit", `🚨 사람 #${person.track} 범인 #${no} 등록 · ${person.culpritWhy} · 얼굴 저장`, { track: person.track });
        }
        person.last = t; person.frames += 1;
        person.state = person.culprit ? "culprit" : verdict;
        person.name = person.culprit ? `범인 #${person.culpritNo}` : verdict === "unknown" ? `미등록 #${person.track}` : `확인 중 #${person.track}`;
        person.score = score; person.best = best ? `범인 #${best.no}` : "없음"; person.px = px; person.facing = facing;
        return { t, person, kps, box: r.f.box, faceBox: fb, active: r.active, risk: r.risk, f: r.f, m: r.m, frameVerdict: fv, truth: s.b, truthKo: TRUTH_KO[s.b], log, culprits, people, faceBook };
      },
    };
  }

  // ---------------- 그리기 ----------------
  function fitCanvas(cv) {
    const r = cv.getBoundingClientRect(), dpr = window.devicePixelRatio || 1;
    const w = Math.max(1, Math.round(r.width * dpr)), h = Math.max(1, Math.round(r.height * dpr));
    if (cv.width !== w || cv.height !== h) { cv.width = w; cv.height = h; }
    return { w, h, dpr };
  }
  // 로봇견 높이(바닥 가까이)에서 본 실내: 바닥, 벽, 상자
  function drawScene(ctx, w, h, label, t) {
    const g = ctx.createLinearGradient(0, 0, 0, h);
    g.addColorStop(0, "#1b2433"); g.addColorStop(0.55, "#222c3b"); g.addColorStop(0.56, "#2a2622"); g.addColorStop(1, "#3a332b");
    ctx.fillStyle = g; ctx.fillRect(0, 0, w, h);
    ctx.strokeStyle = "rgba(255,255,255,.05)"; ctx.lineWidth = 1;
    for (let i = -6; i <= 6; i++) { ctx.beginPath(); ctx.moveTo(w / 2 + i * w * 0.04, h * 0.56); ctx.lineTo(w / 2 + i * w * 0.3, h); ctx.stroke(); }
    ctx.fillStyle = "#2f3a4a"; ctx.fillRect(w * 0.05, h * 0.42, w * 0.12, h * 0.16);
    ctx.fillStyle = "#283241"; ctx.fillRect(w * 0.82, h * 0.38, w * 0.1, h * 0.2);
    // 노이즈 줄 (저해상도 카메라 느낌)
    ctx.fillStyle = "rgba(255,255,255,.025)";
    for (let y = (t * 40) % 4; y < h; y += 4) ctx.fillRect(0, y, w, 1);
    if (label) {
      ctx.font = `600 ${Math.round(h * 0.045)}px system-ui, sans-serif`;
      ctx.fillStyle = "rgba(0,0,0,.45)"; ctx.fillRect(8, 8, ctx.measureText(label).width + 16, h * 0.07);
      ctx.fillStyle = "#e6edf3"; ctx.fillText(label, 16, 8 + h * 0.052);
    }
  }
  function drawSkeleton(ctx, kps, w, h, color, opts = {}) {
    const lw = Math.max(2, h * 0.008);
    ctx.lineCap = "round";
    ctx.strokeStyle = color; ctx.lineWidth = lw;
    for (const [a, b] of EDGES) { ctx.beginPath(); ctx.moveTo(kps[a][0] * w, kps[a][1] * h); ctx.lineTo(kps[b][0] * w, kps[b][1] * h); ctx.stroke(); }
    for (let i = 0; i < 17; i++) {
      ctx.beginPath(); ctx.arc(kps[i][0] * w, kps[i][1] * h, lw * (opts.hot && opts.hot.includes(i) ? 2 : 1.2), 0, Math.PI * 2);
      ctx.fillStyle = opts.hot && opts.hot.includes(i) ? "#fff" : color; ctx.fill();
    }
  }
  // 사람 몸 실루엣 (스켈레톤 아래에 깔아서 '사람이 있다'는 느낌만 준다)
  function drawBody(ctx, kps, w, h) {
    ctx.strokeStyle = "rgba(160,175,195,.35)"; ctx.lineWidth = Math.max(6, h * 0.035); ctx.lineCap = "round";
    for (const [a, b] of EDGES.slice(0, 12)) { ctx.beginPath(); ctx.moveTo(kps[a][0] * w, kps[a][1] * h); ctx.lineTo(kps[b][0] * w, kps[b][1] * h); ctx.stroke(); }
    const head = kps[0], r = Math.max(5, h * 0.035);
    ctx.fillStyle = "rgba(160,175,195,.35)"; ctx.beginPath(); ctx.arc(head[0] * w, head[1] * h, r, 0, Math.PI * 2); ctx.fill();
  }
  function drawBox(ctx, box, w, h, color, label, dash) {
    const [x0, y0, x1, y1] = box;
    ctx.save();
    ctx.strokeStyle = color; ctx.lineWidth = Math.max(2, h * 0.006);
    if (dash) ctx.setLineDash([6, 5]);
    ctx.strokeRect(x0 * w, y0 * h, (x1 - x0) * w, (y1 - y0) * h);
    ctx.restore();
    if (label) {
      ctx.font = `700 ${Math.round(Math.max(11, h * 0.038))}px system-ui, sans-serif`;
      const tw = ctx.measureText(label).width + 12, th = Math.max(16, h * 0.055);
      const ly = y0 * h - th < 0 ? y1 * h : y0 * h - th;
      ctx.fillStyle = color; ctx.fillRect(x0 * w, ly, tw, th);
      ctx.fillStyle = "#0a0e14"; ctx.fillText(label, x0 * w + 6, ly + th * 0.74);
    }
  }
  // 실제 얼굴 대신 그리는 단순한 얼굴 그림 (사진을 쓰지 않는다)
  function drawFaceAvatar(ctx, box, w, h, hue, noShoulders) {
    const [x0, y0, x1, y1] = box, cx = ((x0 + x1) / 2) * w, cy = ((y0 + y1) / 2) * h, rw = ((x1 - x0) / 2) * w, rh = ((y1 - y0) / 2) * h;
    ctx.fillStyle = `hsl(${hue} 25% 22%)`;
    if (!noShoulders) { ctx.beginPath(); ctx.ellipse(cx, cy + rh * 1.9, rw * 1.7, rh * 1.1, 0, Math.PI, 0); ctx.fill(); }   // 어깨
    ctx.fillStyle = "#c9a68b"; ctx.beginPath(); ctx.ellipse(cx, cy + rh * 0.1, rw * 0.82, rh * 0.92, 0, 0, Math.PI * 2); ctx.fill();
    ctx.fillStyle = `hsl(${hue} 35% 18%)`; ctx.beginPath(); ctx.ellipse(cx, cy - rh * 0.45, rw * 0.88, rh * 0.5, 0, Math.PI, 0); ctx.fill(); // 머리카락
    ctx.fillStyle = "#2a2a2a";
    ctx.beginPath(); ctx.arc(cx - rw * 0.3, cy, Math.max(1.5, rw * 0.08), 0, Math.PI * 2); ctx.arc(cx + rw * 0.3, cy, Math.max(1.5, rw * 0.08), 0, Math.PI * 2); ctx.fill();
    ctx.strokeStyle = "#7a5848"; ctx.lineWidth = Math.max(1, rw * 0.06);
    ctx.beginPath(); ctx.moveTo(cx - rw * 0.22, cy + rh * 0.45); ctx.lineTo(cx + rw * 0.22, cy + rh * 0.45); ctx.stroke();
  }

  function loop(fn) {
    let last = performance.now();
    function f(now) { const dt = Math.min(0.1, (now - last) / 1000); last = now; fn(dt); requestAnimationFrame(f); }
    requestAnimationFrame(f);
  }
  const clock = (t) => `${String(Math.floor(t / 60)).padStart(2, "0")}:${String(Math.floor(t % 60)).padStart(2, "0")}`;

  // 위쪽 이동 메뉴: 시안끼리 바로 넘어가기
  function nav(now) {
    const links = [["index.html", "시안 목록"], ["design1.html", "관제실 1안"], "|",
      ["face1.html", "얼굴 1안"], ["face2.html", "얼굴 2안"], ["face3.html", "얼굴 3안"], ["face4.html", "얼굴 4안"], ["face5.html", "얼굴 5안"], "|",
      ["pose1.html", "행동 1안"], ["pose2.html", "행동 2안"], ["pose3.html", "행동 3안"],
      ["pose4.html", "행동 4안"], ["pose5.html", "행동 5안"], ["pose6.html", "행동 6안"], "|",
      ["combo1.html", "반반 1안"], ["combo2.html", "반반 2안"], ["combo3.html", "반반 3안"], ["combo4.html", "반반 4안"], ["combo5.html", "반반 5안"], "|", ["danger_actions.html", "위험 행동 목록"]];
    const el = document.querySelector(".nav");
    if (!el) return;
    el.innerHTML = links.map((l) => (l === "|" ? '<span class="sep"></span>'
      : `<a href="${l[0]}"${l[0].startsWith(now) ? ' class="now" aria-current="page"' : ""}>${l[1]}</a>`)).join("");
  }

  window.CamMock = { nav, DANGER_KPS, COMBO_VERDICT, CULPRIT_IDS, RESCUE_IDS, comboSim, poseOf, LEVEL, DANGERS, DANGER_BY_ID, EDGES, KP_KO, REGISTRY, FACE_RULE, VERDICT, TRUTH_KO, SCRIPT,
    poseSim, faceSim, buildPose, poseFor, fitCanvas, drawScene, drawSkeleton, drawBody, drawBox, drawFaceAvatar, loop, clock };
})();
