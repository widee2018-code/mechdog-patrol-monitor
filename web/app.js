// MechDog 관제실: 1안(관제실형, web/mockups/design1.html) 화면에 실제 데이터를 연결한 대시보드.
// /ws 로 docs/message_schema.md 의 메시지를 받고, /api/cameras 로 카메라 실시간 영상을 확인한다.
(() => {
  const DOGS = ["A", "B", "C"];
  const COLOR = { A: "#4da3ff", B: "#3ecf8e", C: "#f5b942" };
  const ROLE = { A: "정찰견", B: "확인견 1", C: "확인견 2" };
  const ROLE_SUB = { A: "LiDAR · 앞장", B: "A의 마커 따라감", C: "B의 마커 따라감" };
  const MARKER_OF = { B: 1, C: 2 };   // 뒤따르는 개가 보는 앞 개의 마커 ID
  const AHEAD = { B: "A", C: "B" };
  const MISSIONS = [
    { key: "STANDBY", ko: "대기" },
    { key: "EXPLORE", ko: "탐사" },
    { key: "SUSPECT", ko: "의심" },
    { key: "CONFIRM", ko: "범인 확인" },
    { key: "CONTAIN", ko: "포위" },
    { key: "HANDOVER", ko: "인계" },
    { key: "REGROUP", ko: "줄 다시 서기" },
  ];
  // 예전 스키마·시뮬레이터 상태 이름을 새 단계로 맞춘다
  const ALIAS = { MAP: "EXPLORE", PATROL: "EXPLORE", ALERT: "CONFIRM", INTERCEPT: "CONTAIN", RETURN: "REGROUP" };
  const HOT = new Set(["SUSPECT", "CONFIRM", "CONTAIN", "HANDOVER"]);
  const STATE_KO = {
    idle: "대기", moving: "움직이는 중", stopped: "멈춤", blocked: "앞이 막힘", tower: "관제탑",
    wait: "마커 기다림", follow: "따라가는 중", hold: "잠깐 놓침", search: "마커 찾는 중", gave_up: "따라가기 포기",
    no_video: "영상 없음",
  };
  const FOLLOW = new Set(["wait", "follow", "hold", "search", "gave_up", "no_video"]);   // marker_follow.py 상태
  const STALE_MS = 2500;
  const TRAIL_LEN = 150;
  const FONT = '"Pretendard","Apple SD Gothic Neo","Malgun Gothic","Noto Sans KR",system-ui,sans-serif';
  const T = {
    bg: "#0b1017", floor: "#0f1620", grid: "#18212d", wall: "#8a98ad", obstacle: "#223042", exit: "#ff7b75",
    lidar: "#5ee1ff", muted: "#7d8ba1", alert: "#ff5a52", alertSoft: "rgba(255,90,82,.16)", link: "rgba(210,220,235,.35)",
    onDog: "#0a0e14",
  };

  const S = {
    arena: null, connected: false, mission: "STANDBY", missionAt: Date.now(),
    incident: 0, incidentStart: 0,
    dogs: {},          // name -> {msg, seen}
    lidar: null, lidarSeen: 0,
    intruder: null, trail: [],
    cams: {},          // name -> {online, configured}
    events: [], eventKeys: new Set(),
    evidenceKeys: new Set(),
  };
  const $ = (id) => document.getElementById(id);
  const clamp = (x, a, b) => Math.max(a, Math.min(b, x));
  const num = (v, d) => (typeof v === "number" ? v.toFixed(d) : "—");
  const fmtTime = (ms) => new Date(ms).toLocaleTimeString("en-GB", { hour12: false });
  const fmtDur = (s) => `${String(Math.floor(s / 60)).padStart(2, "0")}:${String(Math.floor(s % 60)).padStart(2, "0")}`;
  const battPct = (v) => clamp(((v - 6.4) / (8.4 - 6.4)) * 100, 0, 100);
  const battLevel = (v) => (v < 6.6 ? "alert" : v < 6.8 ? "warn" : "ok");
  const esc = (s) => String(s).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" })[c]);
  const fresh = (d) => d && Date.now() - d.seen < STALE_MS;
  const elapsed = () => (S.incidentStart ? (Date.now() - S.incidentStart) / 1000 : 0);

  $("steps").innerHTML = MISSIONS.map((m) => `<li data-k="${m.key}"><span class="en">${m.key}</span>${m.ko}</li>`).join("");
  $("cams").innerHTML = DOGS.map((n) => `
    <figure class="cam" id="cam${n}"><img alt="Dog ${n} 카메라"><span class="empty">영상 없음</span>
      <figcaption><span class="badge" style="background:${COLOR[n]}">${n}</span>${ROLE[n]}<span class="live">—</span></figcaption>
      <span class="note"></span>
    </figure>`).join("");

  // ── 메시지 처리 ─────────────────────────────────────────────
  function setMission(raw, incident, ts) {
    const m = ALIAS[raw] || raw;
    if (incident) S.incident = incident;   // 사건 번호는 서버(server/app.py)가 붙여 보낸다
    if (m === S.mission) return;
    // 평소 단계 → 의심·확인·포위로 들어가면 새 사건
    if (HOT.has(m) && !HOT.has(S.mission)) { if (!incident) S.incident++; S.incidentStart = ts ? ts * 1000 : Date.now(); S.trail = []; }
    if (!HOT.has(m)) S.incidentStart = 0;
    S.mission = m; S.missionAt = Date.now();
    renderDom();
  }

  const handlers = {
    mission(m) { setMission(m.state, m.incident, m.ts); },
    alarm(m) { S.alarm = m; updateSiren(); },
    dog(m) {
      // 부분 업데이트를 합친다: brain은 x/y/heading·따라가기 상태, 로봇견은 front_cm/rssi/battery를 보낸다
      const prev = S.dogs[m.from];
      S.dogs[m.from] = { msg: { ...prev?.msg, ...m }, seen: Date.now() };
    },
    lidar(m) { S.lidar = m; S.lidarSeen = Date.now(); },
    intruder(m) {
      S.intruder = { ...m, seen: Date.now() };
      S.trail.push([m.x, m.y]);
      if (S.trail.length > TRAIL_LEN) S.trail.shift();
    },
    camera(m) {
      const fig = $(`cam${m.from}`);
      if (fig && !S.cams[m.from]?.online) {   // 실시간 영상이 없을 때만 스냅샷을 보여 준다
        fig.querySelector("img").src = m.image;
        fig.querySelector(".empty").hidden = true;
        fig.querySelector(".note").textContent = `스냅샷 ${fmtTime(m.ts * 1000)}`;
      }
      if (m.evidence) addEvidence(m);
    },
    event(m) { addEvent(m.ts * 1000, m.level, m.text, m.from); },
  };

  function addEvent(ms, level, text, from = "web") {
    const key = `${ms}|${from}|${text}`;
    if (S.eventKeys.has(key)) return;   // 다시 연결하면 서버가 최근 이벤트를 다시 보낸다
    S.eventKeys.add(key);
    S.events.push({ ms, level, text });
    S.events.sort((a, b) => b.ms - a.ms);
    if (S.events.length > 200) S.events.length = 200;
    renderLog();
  }
  function renderLog() {
    $("evcount").textContent = `${S.events.length}건`;
    $("log").innerHTML = S.events.slice(0, 80).map((e) =>
      `<li><time>${fmtTime(e.ms)}</time><span class="lv ${esc(e.level)}"></span><span>${esc(e.text)}</span></li>`).join("");
  }

  function addEvidence(m) {
    const key = `${m.from}:${m.ts}`;
    if (S.evidenceKeys.has(key)) return;
    S.evidenceKeys.add(key);
    const fig = document.createElement("figure");
    fig.innerHTML = `<img alt="증거"><figcaption></figcaption>`;
    fig.firstChild.src = m.image;
    fig.lastChild.textContent = `Dog ${m.from} · ${fmtTime(m.ts * 1000)}${m.note ? ` · ${m.note}` : ""}`;
    $("evidence").prepend(fig);
    $("evidenceCount").textContent = `${S.evidenceKeys.size}장`;
  }

  // ── 로봇견 카드 ─────────────────────────────────────────────
  function chip(d) {
    if (!d) return `<span class="chip off">데이터 없음</span>`;
    if (!fresh(d)) return `<span class="chip stop">연결 끊김</span>`;
    const st = d.msg.state;
    const cls = ["moving", "follow"].includes(st) ? "walk" : ["blocked", "search", "gave_up", "hold"].includes(st) ? "watch" : "";
    return `<span class="chip ${cls}">${esc(STATE_KO[st] || st || "—")}</span>`;
  }

  function dogCard(n) {
    const d = S.dogs[n], m = d?.msg || {};
    const v = m.battery_v;
    const lv = typeof v === "number" ? battLevel(v) : "";
    const extra = n === "A"
      ? `<div class="kv"><span>LiDAR</span><b>${S.lidar && Date.now() - S.lidarSeen < STALE_MS ? `${S.lidar.points.length}점` : "스캔 없음"}</b></div>`
      : `<div class="kv"><span>앞 개 마커</span><b class="${m.state === "follow" || m.ready ? "ok" : FOLLOW.has(m.state) ? "warn" : ""}">ID ${MARKER_OF[n]} · ${FOLLOW.has(m.state) ? STATE_KO[m.state] : "따라가기 꺼짐"}</b></div>`;
    return `<article class="dog ${d && !fresh(d) ? "stale" : ""}" style="--c:${COLOR[n]}">
      <header><span class="badge">${n}</span><div><b>${ROLE[n]}</b><small>${ROLE_SUB[n]}</small></div>${chip(d)}</header>
      <div class="kv"><span>배터리</span><b class="${lv}">${num(v, 2)} V</b></div>
      <div class="batt ${lv}"><i style="width:${typeof v === "number" ? battPct(v) : 0}%"></i></div>
      <div class="kv"><span>앞 장애물</span><b class="${m.front_cm != null && m.front_cm < 15 ? "warn" : ""}">${m.front_cm ?? "—"} cm</b></div>
      <div class="kv"><span>위치 · 방향</span><b>(${num(m.x, 2)}, ${num(m.y, 2)}) · ${num(m.heading_deg, 0)}°</b></div>
      <div class="kv"><span>기울기 · Wi-Fi</span><b>${esc(m.tilt ?? "—")} · ${m.rssi ?? "—"} dBm</b></div>
      ${extra}
    </article>`;
  }

  // ── 시작 준비 (STANDBY): 받은 데이터로 항목마다 준비(초록) / 아직(빨강) / 정보 없음(회색) ──
  function readyItems(n) {
    const d = S.dogs[n], m = d?.msg || {}, cam = S.cams[n];
    const items = [
      d ? { label: "로봇견 연결", st: fresh(d) ? "ok" : "bad", val: fresh(d) ? `${m.rssi ?? "—"} dBm` : "끊김" }
        : { label: "로봇견 연결", st: "bad", val: "데이터 없음" },
      typeof m.battery_v === "number"
        ? { label: "배터리", st: m.battery_v >= 6.8 ? "ok" : "bad", val: `${m.battery_v.toFixed(2)} V` }
        : { label: "배터리", st: "na", val: "값 없음" },
      !cam ? { label: "카메라", st: "na", val: "설정 안 됨" } : { label: "카메라", st: cam.online ? "ok" : "bad", val: cam.online ? "실시간" : "끊김" },
    ];
    if (n === "A") {
      items.push(!S.lidar ? { label: "LiDAR · SLAM", st: "na", val: "아직 없음" }
        : { label: "LiDAR · SLAM", st: Date.now() - S.lidarSeen < STALE_MS ? "ok" : "bad", val: Date.now() - S.lidarSeen < STALE_MS ? "수신 중" : "끊김" });
    } else {
      // marker_follow.py가 준비 확인 중에 ready(마커 + 거리 0.12~0.25 m)를 보낸다
      items.push(m.ready == null ? { label: `마커 ${MARKER_OF[n]} 보임`, st: "na", val: "따라가기 꺼짐" }
        : { label: `마커 ${MARKER_OF[n]} 보임`, st: m.ready ? "ok" : "bad", val: m.ready ? "준비됨" : "찾는 중" });
    }
    return items;
  }

  function renderReady() {
    const show = S.mission === "STANDBY";
    $("ready").hidden = !show;
    if (!show) return;
    let all = true;
    $("readyGrid").innerHTML = DOGS.map((n) => {
      const items = readyItems(n), known = items.filter((r) => r.st !== "na"), ok = known.filter((r) => r.st === "ok").length;
      if (ok < known.length) all = false;
      return `<article class="rcard" style="--c:${COLOR[n]}">
        <header><span class="badge">${n}</span>${ROLE[n]}<span class="sum ${ok === known.length ? "ok" : "warn"}">${ok}/${known.length}</span></header>
        ${items.map((r) => `<div class="ritem ${r.st === "ok" ? "ok" : r.st === "na" ? "na" : ""}"><i></i><span>${r.label}</span><b>${r.val}</b></div>`).join("")}
      </article>`;
    }).join("");
    $("readySub").textContent = all ? "확인할 수 있는 항목이 모두 준비됐어요 · 탐사를 시작하세요 (회색 = 정보 없음)"
      : "세 마리를 일렬로 세우고 모든 항목이 초록이 되면 탐사를 시작하세요 (회색 = 정보 없음)";
  }

  // ── 경보 배너 ───────────────────────────────────────────────
  function banner() {
    const b = $("banner");
    let html = "", cls = "";
    if (!S.connected) { cls = "stop"; html = `<span class="ico">■</span>모니터 서버와 연결이 끊겼습니다 · 다시 연결하는 중`; }
    else if (S.mission === "SUSPECT") { cls = "warn"; html = `<span class="ico">▲</span>정찰견 A가 이상한 물체를 확인하고 있습니다 · 사건 #${S.incident}`; }
    else if (S.mission === "CONFIRM") { cls = "alert"; html = `<span class="ico">●</span>사건 #${S.incident} 침입자 확인 · 경과 ${fmtDur(elapsed())}`; }
    else if (S.mission === "CONTAIN") { cls = "alert"; html = `<span class="ico">●</span>사건 #${S.incident} 침입자 확인 · A·B·C 3방향 포위 중 · 경과 ${fmtDur(elapsed())}`; }
    else if (S.mission === "HANDOVER") { cls = "alert"; html = `<span class="ico">●</span>포위 유지 중 · 증거 사진 ${S.evidenceKeys.size}장 · 상황이 끝나면 재정렬하세요 (${fmtDur(elapsed())})`; }
    if (!html) { b.hidden = true; b.dataset.k = ""; return; }
    if (b.dataset.k === html) return;
    b.dataset.k = html; b.hidden = false; b.className = `banner ${cls}`; b.innerHTML = html;
  }

  // ── 사이렌 (#6) ─────────────────────────────────────────────
  // 알리는 방법 4가지: 사이렌 소리 · 경광등 그림 깜빡임 · 경보 배너(위 banner) · 브라우저 알림 팝업
  // 울림·멈춤은 서버가 판단해 alarm 메시지로 알려 준다 (server/app.py, 기록은 recorder → Supabase).
  // 울림: 범인 확인(CONFIRM) 단계가 시작될 때 (팀 결정, #6). 포위·인계 중에도 확인 전까지 계속
  // 멈춤: "확인" 버튼 또는 REGROUP 시작 중 먼저 오는 것 · 오탐으로 탐사 복귀
  // 소리·팝업을 켜고 끄는 것은 화면마다 따로라서 이 화면의 이벤트 목록에만 남는다.
  const siren = { armed: false, notifiedId: null, ctx: null, osc: null, timer: null, ackError: "" };
  const sirenLog = (level, text) => addEvent(Date.now(), level, text);

  function sirenSoundOn() {
    if (!siren.ctx || siren.osc) return;
    const ctx = siren.ctx, osc = ctx.createOscillator(), gain = ctx.createGain();
    osc.type = "square"; gain.gain.value = 0.05;
    osc.connect(gain).connect(ctx.destination); osc.start();
    let hi = false;
    const flip = () => { hi = !hi; osc.frequency.setValueAtTime(hi ? 960 : 720, ctx.currentTime); };   // 삐-뽀 두 음
    flip(); siren.timer = setInterval(flip, 450);
    siren.osc = osc;
  }
  function sirenSoundOff() {
    clearInterval(siren.timer); siren.timer = null;
    if (siren.osc) { try { siren.osc.stop(); } catch (e) {} siren.osc.disconnect(); siren.osc = null; }
  }
  function sirenNotify(id) {
    if (siren.notifiedId === id || !("Notification" in window) || Notification.permission !== "granted") return;
    siren.notifiedId = id;
    try { new Notification("🚨 침입자 확인", { body: `사건 #${id} · 대시보드를 확인하세요.`, tag: `incident-${id}` }); } catch (e) {}
  }

  $("sirenArm").onclick = async () => {
    if (siren.armed) {                       // 다시 누르면 알림 끄기
      siren.armed = false; sirenSoundOff();
      sirenLog("warn", "🔕 사이렌 알림 꺼짐 (이 화면 · 소리 · 팝업 안 나옴)");
    } else {
      sirenLog("info", "🔔 사이렌 알림 켜짐 (이 화면)");
      siren.armed = true;
      try { siren.ctx = siren.ctx || new (window.AudioContext || window.webkitAudioContext)(); await siren.ctx.resume(); } catch (e) {}
      if ("Notification" in window && Notification.permission === "default") { try { await Notification.requestPermission(); } catch (e) {} }
    }
    updateSiren();
  };
  $("sirenAck").onclick = () => {
    // 서버가 끄고 alarm off 를 보내면 모든 화면의 사이렌이 같이 멈춘다
    fetch("/api/alarm/ack", { method: "POST" }).then((r) => {
      siren.ackError = r.ok ? "" : r.status === 403 ? "공개 주소에서는 끌 수 없어요" : `확인 실패 (${r.status})`;
    }).catch(() => { siren.ackError = "서버에 연결할 수 없어요"; }).finally(updateSiren);
  };

  function updateSiren() {
    const id = S.alarm?.incident ?? S.incident;
    const ringing = S.alarm?.state === "on";
    if (!ringing) siren.ackError = "";
    $("siren").classList.toggle("on", ringing);
    $("sirenAck").hidden = !ringing;
    const arm = $("sirenArm");
    arm.textContent = siren.armed ? "🔔 알림 켜짐" : "🔔 알림 켜기";
    arm.classList.toggle("on", siren.armed);
    if (ringing && siren.armed) { sirenSoundOn(); sirenNotify(id); } else sirenSoundOff();
    $("sirenState").textContent = ringing ? `사이렌 울림 · 사건 #${id}` : "사이렌 대기";
    $("sirenSub").textContent = ringing
      ? siren.ackError || (siren.armed ? "확인을 누르면 멈춰요" : "소리 꺼짐 · 알림 켜기를 누르세요")
      : (siren.armed ? "소리 · 팝업 준비됨" : "알림이 꺼져 있어요");
  }

  // ── 화면 갱신 (0.25초마다) ──────────────────────────────────
  function renderDom() {
    updateSiren();
    renderReady();
    const idx = MISSIONS.findIndex((m) => m.key === S.mission);
    [...$("steps").children].forEach((li, i) => { li.className = i === idx ? (HOT.has(S.mission) ? "now hot" : "now") : ""; });
    $("clock").textContent = fmtTime(Date.now());
    $("conn").textContent = S.connected ? "연결됨" : "연결 끊김";
    $("conn").classList.toggle("on", S.connected);
    const lidar = S.lidar && Date.now() - S.lidarSeen < STALE_MS ? `LiDAR 점 ${S.lidar.points.length}개` : "LiDAR 스캔 없음";
    const intr = S.intruder && Date.now() - S.intruder.seen < STALE_MS
      ? ` · 침입자 (${S.intruder.x.toFixed(2)}, ${S.intruder.y.toFixed(2)}) m · 신뢰도 ${num(S.intruder.confidence, 2)}` : "";
    $("mapmeta").textContent = lidar + intr;
    $("dogs").innerHTML = DOGS.map(dogCard).join("");
    banner();
  }

  // ── 지도 ────────────────────────────────────────────────────
  const line = (g, a, b) => { g.beginPath(); g.moveTo(a[0], a[1]); g.lineTo(b[0], b[1]); g.stroke(); };
  const circ = (g, x, y, r) => { g.beginPath(); g.arc(x, y, r, 0, Math.PI * 2); };

  function wallSegments(a) {
    const segs = [[[0, 0], [a.width, 0]], [[0, a.height], [a.width, a.height]]];
    for (const sx of [0, a.width]) {
      const gaps = a.exits.filter((e) => e.a[0] === sx).map((e) => [e.a[1], e.b[1]]).sort((p, q) => p[0] - q[0]);
      let y = 0;
      for (const [g0, g1] of gaps) { segs.push([[sx, y], [sx, g0]]); y = g1; }
      segs.push([[sx, y], [sx, a.height]]);
    }
    return segs;
  }

  function drawMap() {
    const c = $("map"), a = S.arena;
    const px = devicePixelRatio || 1;
    const w = Math.round(c.clientWidth * px), h = Math.round(c.clientHeight * px);
    if (!a || !w || !h) return;
    if (c.width !== w || c.height !== h) { c.width = w; c.height = h; }
    const g = c.getContext("2d"), W = w, H = h;
    const vx = -0.3, vy = -0.3, vw = a.width + 0.6, vh = a.height + 0.6;
    const sc = Math.min(W / vw, H / vh);
    const left = (W - vw * sc) / 2, bottom = (H - vh * sc) / 2;
    const P = (x, y) => [left + (x - vx) * sc, H - bottom - (y - vy) * sc];
    g.lineCap = "round";
    g.fillStyle = T.bg; g.fillRect(0, 0, W, H);
    const [fx, fy] = P(0, a.height);
    g.fillStyle = T.floor; g.fillRect(fx, fy, a.width * sc, a.height * sc);

    g.strokeStyle = T.grid; g.lineWidth = 1;
    for (let x = 0; x <= a.width + 1e-6; x += 0.5) line(g, P(x, 0), P(x, a.height));
    for (let y = 0; y <= a.height + 1e-6; y += 0.5) line(g, P(0, y), P(a.width, y));

    g.fillStyle = T.obstacle;
    for (const o of a.obstacles) { const [x0, y0] = P(o.x, o.y + o.h); g.fillRect(x0, y0, o.w * sc, o.h * sc); }

    g.strokeStyle = T.wall; g.lineWidth = 3 * px;
    for (const [p, q] of wallSegments(a)) line(g, P(...p), P(...q));
    g.strokeStyle = T.exit; g.lineWidth = 2 * px; g.setLineDash([3 * px, 4 * px]);
    for (const e of a.exits) line(g, P(...e.a), P(...e.b));
    g.setLineDash([]);
    g.fillStyle = T.exit; g.font = `${10 * px}px ${FONT}`;
    for (const e of a.exits) {
      const [tx, ty] = P(e.a[0], e.b[1] + 0.12);
      g.textAlign = e.a[0] === 0 ? "left" : "right";
      g.fillText(e.id, tx + (e.a[0] === 0 ? 6 : -6) * px, ty);
    }
    g.textAlign = "left";

    // LiDAR 점 (origin 기준 극좌표)
    if (S.lidar && Date.now() - S.lidarSeen < STALE_MS) {
      const [lx, ly] = S.lidar.origin;
      g.fillStyle = T.lidar;
      for (const [deg, d] of S.lidar.points) {
        const r = (deg * Math.PI) / 180;
        const [X, Y] = P(lx + d * Math.cos(r), ly + d * Math.sin(r));
        g.fillRect(X - 1.5 * px, Y - 1.5 * px, 3 * px, 3 * px);
      }
    }

    const pos = {};
    for (const n of DOGS) { const m = S.dogs[n]?.msg; if (m && typeof m.x === "number") pos[n] = P(m.x, m.y); }

    // 침입자: 이동 경로 + 포위 반경(0.6 m) + 깜빡이는 점
    const intr = S.intruder && Date.now() - S.intruder.seen < STALE_MS ? S.intruder : null;
    if (S.trail.length > 1) {
      g.strokeStyle = "rgba(255,90,82,.5)"; g.lineWidth = 2 * px; g.beginPath();
      S.trail.forEach(([x, y], i) => (i ? g.lineTo(...P(x, y)) : g.moveTo(...P(x, y))));
      g.stroke();
    }
    if (intr) {
      const [X, Y] = P(intr.x, intr.y);
      g.strokeStyle = T.alert; g.lineWidth = 1.5 * px; g.setLineDash([3 * px, 4 * px]);
      circ(g, X, Y, 0.6 * sc); g.stroke(); g.setLineDash([]);
      g.fillStyle = T.alertSoft; circ(g, X, Y, 0.3 * sc); g.fill();
      const pulse = 1 + 0.25 * Math.sin(Date.now() / 160);
      g.fillStyle = T.alert; circ(g, X, Y, 7 * px * pulse); g.fill();
      g.font = `700 ${11 * px}px ${FONT}`;
      g.fillText(`침입자${S.incident ? ` #${S.incident}` : ""}`, X + 12 * px, Y - 10 * px);

      // 포위망: 세 마리 위치를 잇는 삼각형 (포위 중 점선, 인계 때 실선) + 침입자를 향한 시선
      if ((S.mission === "CONTAIN" || S.mission === "HANDOVER") && DOGS.every((n) => pos[n])) {
        const tri = DOGS.map((n) => pos[n]), done = S.mission === "HANDOVER";
        g.beginPath(); tri.forEach(([x, y], i) => (i ? g.lineTo(x, y) : g.moveTo(x, y))); g.closePath();
        g.globalAlpha = done ? 0.16 : 0.08; g.fillStyle = T.alert; g.fill();
        g.globalAlpha = done ? 0.9 : 0.45; g.strokeStyle = T.alert; g.lineWidth = (done ? 2 : 1.5) * px;
        if (!done) g.setLineDash([5 * px, 4 * px]);
        g.stroke(); g.setLineDash([]);
        g.globalAlpha = 0.35; g.lineWidth = 1 * px;
        for (const p of tri) line(g, p, [X, Y]);
        g.globalAlpha = 1;
      }
    }

    // 행렬: 뒤 개 → 앞 개 점선 (탐사·대기 중)
    if (S.mission === "EXPLORE" || S.mission === "STANDBY") {
      g.strokeStyle = T.link; g.lineWidth = 1.5 * px; g.setLineDash([2 * px, 4 * px]);
      for (const n of ["B", "C"]) if (pos[n] && pos[AHEAD[n]]) line(g, pos[n], pos[AHEAD[n]]);
      g.setLineDash([]);
    }

    // 로봇견: 원 + 방향 선, A는 테두리 하나 더. 끊긴 개는 흐리게
    const r = Math.max(9 * px, Math.min(15 * px, 0.13 * sc));
    for (const n of [...DOGS].reverse()) {
      if (!pos[n]) continue;
      const d = S.dogs[n], [X, Y] = pos[n], hd = ((d.msg.heading_deg ?? 0) * Math.PI) / 180;
      g.globalAlpha = fresh(d) ? 1 : 0.35;
      g.strokeStyle = COLOR[n]; g.lineWidth = 2.5 * px;
      line(g, [X, Y], [X + Math.cos(hd) * r * 2, Y - Math.sin(hd) * r * 2]);
      if (n === "A") { g.lineWidth = 1.5 * px; circ(g, X, Y, r + 4 * px); g.stroke(); }
      g.fillStyle = COLOR[n]; circ(g, X, Y, r); g.fill();
      g.fillStyle = T.onDog; g.font = `700 ${Math.round(r * 1.05)}px ${FONT}`;
      g.textAlign = "center"; g.textBaseline = "middle"; g.fillText(n, X, Y + 0.5 * px);
      g.textAlign = "left"; g.textBaseline = "alphabetic"; g.globalAlpha = 1;
    }

    // 축척 막대
    const [sx, sy] = P(0, 0);
    g.strokeStyle = T.muted; g.lineWidth = 2 * px;
    line(g, [sx, sy + 12 * px], [sx + sc, sy + 12 * px]);
    g.fillStyle = T.muted; g.font = `${10 * px}px ${FONT}`;
    g.fillText("1 m", sx + sc + 5 * px, sy + 16 * px);
  }

  // ── 카메라: 서버가 스트림을 받고 있으면 실시간 영상, 끊기면 camera 메시지 스냅샷 ──
  function pollCameras() {
    fetch("/api/cameras").then((r) => r.json()).then((cams) => {
      for (const n of DOGS) {
        const fig = $(`cam${n}`), img = fig.querySelector("img"), live = fig.querySelector(".live");
        const cam = cams[n], online = cam?.online === true;
        const wasLive = S.cams[n]?.online;
        S.cams[n] = cam ? { online } : undefined;
        if (online && !wasLive) {
          img.src = `/api/camera/${n}/stream${cam.markers ? "?markers=1" : ""}`;
          fig.querySelector(".note").textContent = "";
        }
        if (!online && wasLive) img.removeAttribute("src");
        fig.querySelector(".empty").hidden = online || img.hasAttribute("src");
        fig.querySelector(".empty").textContent = cam ? "영상 연결 끊김" : "카메라 설정 안 됨 (CAMERAS)";
        live.textContent = online ? "● LIVE" : cam ? "끊김" : "—";
        live.classList.toggle("on", online);
      }
    }).catch(() => {}).finally(() => setTimeout(pollCameras, 3000));
  }

  // ── 소켓 ────────────────────────────────────────────────────
  function connect() {
    const ws = new WebSocket(`${location.protocol === "https:" ? "wss" : "ws"}://${location.host}/ws`);
    ws.onopen = () => { S.connected = true; renderDom(); };
    ws.onclose = () => { S.connected = false; renderDom(); setTimeout(connect, 1500); };
    ws.onmessage = (e) => {
      const m = JSON.parse(e.data);
      handlers[m.type]?.(m);
    };
  }

  function frame() { drawMap(); requestAnimationFrame(frame); }

  renderDom();
  renderLog();
  setInterval(renderDom, 250);
  pollCameras();
  fetch("arena.json").then((r) => r.json()).then((a) => {
    S.arena = a;
    requestAnimationFrame(frame);
    connect();
  });
})();
