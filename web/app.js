// MechDog Patrol Monitor: 대시보드 클라이언트.
// /ws 로 메시지를 받아 docs/message_schema.md 의 모든 메시지 종류를 화면에 그린다.

const STATES = ["PATROL", "SUSPECT", "ALERT", "INTERCEPT", "CONTAIN", "HANDOVER", "RETURN"];
const HOT = new Set(["ALERT", "INTERCEPT", "CONTAIN"]);
const DOG_COLOR = { A: "#4da3ff", B: "#3ecf8e", C: "#f5b942" };
const DOG_ROLE = { A: "추적견", B: "봉쇄견 1", C: "봉쇄견 2" };
const STALE_MS = 2000;
const TRAIL_LEN = 150;

const S = {
  arena: null,
  mission: "PATROL",
  dogs: {},          // name -> {msg, seen}
  lidar: null,       // 마지막 LiDAR 메시지
  intruder: null,    // 마지막 침입자 메시지
  trail: [],         // [[x, y], ...]
  sound: null,
  lastAlertSound: null,
  evidenceKeys: new Set(),
  camLive: false,    // 서버가 카메라 실시간 스트림을 받고 있는지
};
const LIVE_CAM = "A";   // 실시간 영상을 보여 줄 로봇견 (서버의 CAMERAS 이름)

const $ = (id) => document.getElementById(id);
const num = (v, d) => (typeof v === "number" ? v.toFixed(d) : "—");
const fmtTime = (ts) => new Date(ts * 1000).toLocaleTimeString("en-GB");

// ------------------------------------------------------------------ 미션 단계 표시

$("pipeline").innerHTML = STATES.map((s) => `<li data-s="${s}">${s}</li>`).join("");
function renderPipeline() {
  for (const li of $("pipeline").children) {
    const on = li.dataset.s === S.mission;
    li.classList.toggle("active", on);
    li.classList.toggle("hot", on && HOT.has(S.mission));
  }
}
renderPipeline();

// ------------------------------------------------------------------ 메시지 처리

const handlers = {
  mission(m) { S.mission = m.state; renderPipeline(); if (m.state === "PATROL") S.trail = []; },
  dog(m) {
    const prev = S.dogs[m.from];
    const first = !prev;
    // 부분 업데이트를 합친다: Pi는 x/y/heading, 로봇견은 battery/front_cm/tilt/state를 보낸다.
    S.dogs[m.from] = { msg: { ...prev?.msg, ...m }, seen: Date.now() };
    if (first || Date.now() - lastDogRender > 250) renderDogs();
  },
  lidar(m) {
    S.lidar = m;
    $("lidar-meta").textContent = `LiDAR 점 ${m.points.length}개 · ${fmtTime(m.ts)}`;
  },
  intruder(m) {
    S.intruder = { ...m, seen: Date.now() };
    S.trail.push([m.x, m.y]);
    if (S.trail.length > TRAIL_LEN) S.trail.shift();
    $("intruder-meta").textContent = `침입자 (${m.x.toFixed(2)}, ${m.y.toFixed(2)}) m · 신뢰도 ${m.confidence}`;
  },
  sound(m) {
    if (m.alert) {
      S.lastAlertSound = m;
      $("sound-last").textContent = `${m.label} · ${fmtTime(m.ts)}`;
    }
    if (S.sound && S.sound.ts > m.ts) return;  // 다시 보낸 경보가 실시간 값보다 오래된 경우
    S.sound = m;
    $("sound-label").textContent = m.label;
    $("sound-label").classList.toggle("alert", !!m.alert);
    $("sound-doa").textContent = `${m.doa_deg.toFixed(0)}°`;
    $("sound-level").textContent = `${m.level_db.toFixed(1)} dB`;
    $("sound-conf").textContent = m.confidence.toFixed(2);
  },
  camera(m) {
    $("cam").src = m.image;
    $("cam-empty").hidden = true;
    if (!S.camLive) $("cam-meta").textContent = `${fmtTime(m.ts)} · ${m.note ?? ""}`;
    if (m.evidence) addEvidence(m);
  },
  event(m) {
    const li = document.createElement("li");
    li.innerHTML = `<time>${fmtTime(m.ts)}</time><span class="lv ${m.level}"></span><span></span>`;
    li.lastChild.textContent = m.text;
    $("events").prepend(li);
    while ($("events").children.length > 200) $("events").lastChild.remove();
  },
};

function addEvidence(m) {
  const key = `${m.from}:${m.ts}`;
  if (S.evidenceKeys.has(key)) return;
  S.evidenceKeys.add(key);
  const fig = document.createElement("figure");
  fig.innerHTML = `<img alt="증거"><figcaption>Dog ${m.from} · ${fmtTime(m.ts)} · ${m.note ?? ""}</figcaption>`;
  fig.firstChild.src = m.image;
  $("evidence").prepend(fig);
  $("ev-count").textContent = S.evidenceKeys.size;
}

// ------------------------------------------------------------------ 로봇견 패널

let lastDogRender = 0;
function renderDogs() {
  const now = (lastDogRender = Date.now());
  $("dogs").innerHTML = ["A", "B", "C"].map((n) => {
    const d = S.dogs[n];
    if (!d) return `<div class="dog stale"><span class="badge" style="background:${DOG_COLOR[n]}">${n}</span>
      <span class="role">${DOG_ROLE[n]}</span><span></span><span class="stats">데이터 없음</span></div>`;
    const m = d.msg;
    const stale = now - d.seen > STALE_MS;
    const pct = Math.max(0, Math.min(100, (((m.battery_v ?? 6.4) - 6.4) / (8.4 - 6.4)) * 100));
    return `<div class="dog ${stale ? "stale" : ""}">
      <span class="badge" style="background:${DOG_COLOR[n]}">${n}</span>
      <span class="role">${DOG_ROLE[n]} · ${stale ? "연결 끊김" : m.state ?? "—"}</span>
      <span class="batt" title="${m.battery_v} V"><i style="width:${pct}%"></i></span>
      <span class="stats">(${num(m.x, 2)}, ${num(m.y, 2)}) m · ${num(m.heading_deg, 0)}° · 전방 ${m.front_cm ?? "—"} cm · 기울기 ${m.tilt ?? "—"}</span>
      <span class="stats">${num(m.battery_v, 2)} V · Wi-Fi ${m.rssi ?? "—"} dBm</span>
    </div>`;
  }).join("");
}

// ------------------------------------------------------------------ 지도

const canvas = $("map");
const ctx = canvas.getContext("2d");
const PAD = 0.3;

function fitCanvas() {
  const r = canvas.getBoundingClientRect();
  canvas.width = r.width * devicePixelRatio;
  canvas.height = r.height * devicePixelRatio;
}
addEventListener("resize", fitCanvas);

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
  const a = S.arena;
  if (!a) return;
  const W = canvas.width, H = canvas.height;
  const scale = Math.min(W / (a.width + 2 * PAD), H / (a.height + 2 * PAD));
  const ox = (W - a.width * scale) / 2, oy = (H - a.height * scale) / 2;
  const P = (x, y) => [ox + x * scale, H - oy - y * scale];
  ctx.clearRect(0, 0, W, H);
  const px = devicePixelRatio;

  // 격자 (0.5 m)
  ctx.strokeStyle = "#151c27"; ctx.lineWidth = 1;
  for (let x = 0; x <= a.width + 1e-6; x += 0.5) { ctx.beginPath(); ctx.moveTo(...P(x, 0)); ctx.lineTo(...P(x, a.height)); ctx.stroke(); }
  for (let y = 0; y <= a.height + 1e-6; y += 0.5) { ctx.beginPath(); ctx.moveTo(...P(0, y)); ctx.lineTo(...P(a.width, y)); ctx.stroke(); }

  // 순찰 구역
  for (const z of a.zones) {
    const [x0, y0] = P(z.x, z.y + z.h);
    ctx.fillStyle = DOG_COLOR[z.dog] + "10";
    ctx.fillRect(x0, y0, z.w * scale, z.h * scale);
    ctx.fillStyle = DOG_COLOR[z.dog] + "70";
    ctx.font = `${11 * px}px sans-serif`;
    ctx.fillText(`구역 ${z.dog}`, x0 + 6 * px, y0 + 14 * px);
  }

  // 벽, 장애물, 출구
  ctx.strokeStyle = "#5b6b82"; ctx.lineWidth = 3 * px;
  for (const [p, q] of wallSegments(a)) { ctx.beginPath(); ctx.moveTo(...P(...p)); ctx.lineTo(...P(...q)); ctx.stroke(); }
  ctx.fillStyle = "#2a3446";
  for (const o of a.obstacles) { const [x0, y0] = P(o.x, o.y + o.h); ctx.fillRect(x0, y0, o.w * scale, o.h * scale); }
  ctx.strokeStyle = "#ff4d4f"; ctx.setLineDash([4 * px, 4 * px]); ctx.lineWidth = 2 * px;
  ctx.fillStyle = "#ff8a8b"; ctx.font = `600 ${11 * px}px sans-serif`;
  for (const e of a.exits) {
    ctx.beginPath(); ctx.moveTo(...P(...e.a)); ctx.lineTo(...P(...e.b)); ctx.stroke();
    const [tx, ty] = P(e.a[0], (e.a[1] + e.b[1]) / 2);
    ctx.textAlign = e.a[0] === 0 ? "left" : "right";
    ctx.fillText(e.id, tx + (e.a[0] === 0 ? 8 : -8) * px, ty - 26 * px);
  }
  ctx.setLineDash([]); ctx.textAlign = "left";

  // 소리 방향 광선 (경보 후 8초간 표시)
  const lo = a.lidar_origin;
  const snd = S.lastAlertSound && Date.now() / 1000 - S.lastAlertSound.ts < 8 ? S.lastAlertSound : null;
  if (snd) {
    const r = Math.hypot(a.width, a.height);
    const rad = (snd.doa_deg * Math.PI) / 180;
    const g = ctx.createLinearGradient(...P(...lo), ...P(lo[0] + Math.cos(rad) * r, lo[1] + Math.sin(rad) * r));
    g.addColorStop(0, "#c084fcdd"); g.addColorStop(1, "#c084fc00");
    ctx.strokeStyle = g; ctx.lineWidth = 6 * px;
    ctx.beginPath(); ctx.moveTo(...P(...lo)); ctx.lineTo(...P(lo[0] + Math.cos(rad) * r, lo[1] + Math.sin(rad) * r)); ctx.stroke();
  }

  // LiDAR 점
  if (S.lidar) {
    const [lx, ly] = S.lidar.origin;
    ctx.fillStyle = "#7ee0ff";
    for (const [deg, d] of S.lidar.points) {
      const rad = (deg * Math.PI) / 180;
      const [x, y] = P(lx + d * Math.cos(rad), ly + d * Math.sin(rad));
      ctx.fillRect(x - 1.5 * px, y - 1.5 * px, 3 * px, 3 * px);
    }
  }
  // Pi / LiDAR 위치
  const [pX, pY] = P(...lo);
  ctx.fillStyle = "#e6edf3"; ctx.beginPath(); ctx.arc(pX, pY, 6 * px, 0, 7); ctx.fill();
  ctx.fillStyle = "#8b98a9"; ctx.font = `${11 * px}px sans-serif`; ctx.fillText("Pi + LiDAR", pX + 9 * px, pY - 4 * px);

  // 침입자 이동 경로 + 표시
  if (S.trail.length > 1) {
    ctx.strokeStyle = "#ff4d4f88"; ctx.lineWidth = 2 * px; ctx.beginPath();
    S.trail.forEach(([x, y], i) => (i ? ctx.lineTo(...P(x, y)) : ctx.moveTo(...P(x, y))));
    ctx.stroke();
  }
  if (S.intruder && Date.now() - S.intruder.seen < STALE_MS) {
    const [x, y] = P(S.intruder.x, S.intruder.y);
    const pulse = 1 + 0.25 * Math.sin(Date.now() / 150);
    ctx.strokeStyle = "#ff4d4f"; ctx.lineWidth = 2 * px;
    ctx.beginPath(); ctx.arc(x, y, 0.5 * scale, 0, 7); ctx.setLineDash([3 * px, 5 * px]); ctx.stroke(); ctx.setLineDash([]);
    ctx.fillStyle = "#ff4d4f"; ctx.beginPath(); ctx.arc(x, y, 9 * px * pulse, 0, 7); ctx.fill();
    ctx.fillStyle = "#ff8a8b"; ctx.font = `600 ${11 * px}px sans-serif`; ctx.fillText("침입자", x + 12 * px, y - 10 * px);
  }

  // 로봇견
  for (const [n, d] of Object.entries(S.dogs)) {
    const m = d.msg;
    if (typeof m.x !== "number") continue;
    const [x, y] = P(m.x, m.y);
    const rad = ((m.heading_deg ?? 0) * Math.PI) / 180;
    ctx.globalAlpha = Date.now() - d.seen > STALE_MS ? 0.35 : 1;
    ctx.strokeStyle = DOG_COLOR[n]; ctx.lineWidth = 2.5 * px;
    ctx.beginPath(); ctx.moveTo(x, y); ctx.lineTo(x + Math.cos(rad) * 22 * px, y - Math.sin(rad) * 22 * px); ctx.stroke();
    ctx.fillStyle = DOG_COLOR[n]; ctx.beginPath(); ctx.arc(x, y, 11 * px, 0, 7); ctx.fill();
    ctx.fillStyle = "#0d1117"; ctx.font = `700 ${12 * px}px sans-serif`; ctx.textAlign = "center"; ctx.textBaseline = "middle";
    ctx.fillText(n, x, y + 0.5 * px);
    ctx.textAlign = "left"; ctx.textBaseline = "alphabetic"; ctx.globalAlpha = 1;
  }

  // 축척 막대
  const [sx, sy] = P(0, 0);
  ctx.strokeStyle = "#8b98a9"; ctx.lineWidth = 2 * px;
  ctx.beginPath(); ctx.moveTo(sx, sy + 14 * px); ctx.lineTo(sx + scale, sy + 14 * px); ctx.stroke();
  ctx.fillStyle = "#8b98a9"; ctx.font = `${10 * px}px sans-serif`; ctx.fillText("1 m", sx + scale + 4 * px, sy + 17 * px);
}

// ------------------------------------------------------------------ 나침반

function drawCompass() {
  const c = $("compass"), g = c.getContext("2d");
  const r = 52, cx = 60, cy = 60;
  g.clearRect(0, 0, 120, 120);
  g.strokeStyle = "#263041"; g.lineWidth = 2; g.beginPath(); g.arc(cx, cy, r, 0, 7); g.stroke();
  g.fillStyle = "#8b98a9"; g.font = "10px sans-serif"; g.textAlign = "center";
  g.fillText("+x", cx + r - 8, cy + 3); g.fillText("+y", cx, cy - r + 12);
  if (!S.sound) return;
  const rad = (S.sound.doa_deg * Math.PI) / 180;
  g.strokeStyle = S.sound.alert ? "#ff4d4f" : "#c084fc"; g.lineWidth = 4; g.lineCap = "round";
  g.beginPath(); g.moveTo(cx, cy); g.lineTo(cx + Math.cos(rad) * (r - 6), cy - Math.sin(rad) * (r - 6)); g.stroke();
  const lvl = Math.max(0, Math.min(1, (S.sound.level_db - 30) / 60));
  g.fillStyle = "#c084fc44"; g.beginPath(); g.arc(cx, cy, 6 + lvl * 30, 0, 7); g.fill();
}

// ------------------------------------------------------------------ 루프 + 소켓

function frame() {
  drawMap();
  drawCompass();
  requestAnimationFrame(frame);
}
renderDogs();
setInterval(renderDogs, 1000);  // 메시지가 끊긴 로봇견을 '연결 끊김'으로 표시하는 역할도 한다

function connect() {
  const ws = new WebSocket(`${location.protocol === "https:" ? "wss" : "ws"}://${location.host}/ws`);
  ws.onopen = () => { $("conn").textContent = "연결됨"; $("conn").className = "conn on"; };
  ws.onclose = () => { $("conn").textContent = "연결 끊김"; $("conn").className = "conn off"; setTimeout(connect, 1500); };
  ws.onmessage = (e) => {
    const m = JSON.parse(e.data);
    handlers[m.type]?.(m);
  };
}

// 서버가 카메라 스트림을 받고 있으면 스냅샷 위에 실시간 영상을 겹쳐 보여 준다.
// 끊기면 src를 지워서 아래의 camera 메시지 스냅샷이 다시 보이게 한다.
function pollLive() {
  fetch("/api/cameras").then((r) => r.json()).then((cams) => {
    const live = cams[LIVE_CAM]?.online === true;
    const img = $("cam-live");
    if (live && !img.getAttribute("src")) img.src = `/api/camera/${LIVE_CAM}/stream`;
    if (!live) img.removeAttribute("src");
    S.camLive = live;
    if (live) {
      $("cam-empty").hidden = true;
      $("cam-meta").textContent = "실시간";
    }
  }).catch(() => {}).finally(() => setTimeout(pollLive, 3000));
}
pollLive();

fetch("arena.json").then((r) => r.json()).then((a) => {
  S.arena = a;
  fitCanvas();
  requestAnimationFrame(frame);
  connect();
});
