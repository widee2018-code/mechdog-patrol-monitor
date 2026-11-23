// 추론 화면: hub /ws 의 pose · face 메시지와 /api/camera/<이름>/stream 영상으로 그린다 (docs/message_schema.md).
// 위험 행동 목록 · 그리기 도구는 시안의 cam_mock.js 를 같이 쓴다 (가짜 데이터 부분은 쓰지 않음).
// 얼굴: face 메시지(brain/pose_detect.py 가 머리 몸 점으로 자른 사진)를 사람마다 한 장씩 '얼굴 기록'에 보여 준다.
//       사진은 허브 메모리(최근 30장)와 이 화면에만 잠시 있다. 서버를 끄면 사라지고, Supabase 에는 저장하지 않는다.
//       범인·미등록 판정은 하지 않는다.
(function () {
  "use strict";
  const M = window.CamMock;
  const CAM = new URLSearchParams(location.search).get("cam") || "A";   // infer.html?cam=B
  const STALE_MS = 1000;      // 이 시간 동안 pose 가 안 오면 그 사람은 화면에서 지운다
  const KP_CONF = 0.5;        // 몸 점 신뢰도가 이보다 낮으면 안 보이는 점 (brain/pose_detect.py --kp-conf)
  const FACE_MIN_PX = 40;     // 얼굴이 이보다 작으면 사진을 남기지 않음 (brain/pose_detect.py --face-min-px)
  const SAME_PERSON_SEC = 60; // 같은 추적 번호라도 이보다 오래 떨어져 온 얼굴은 다른 사람(새 카드)으로 본다
  const RISK_COL = { none: "#3ecf8e", low: "#f5b942", mid: "#ff8a3d", high: "#ff5a52" };
  const $ = (id) => document.getElementById(id);
  const esc = (s) => String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" })[c]);
  const fmt = (ms) => new Date(ms).toLocaleTimeString("en-GB", { hour12: false });   // 17:22:38 (관제실 app.js 와 같음)

  const S = {
    connected: false, camOnline: false,
    pose: new Map(),     // track -> { m(마지막 pose 메시지), seen }
    lastAct: new Map(),  // track -> 마지막으로 기록한 action (바뀔 때만 행동 기록에 한 줄)
    acts: new Map(),     // track -> 켜졌던 위험 행동 id Set (얼굴 카드에 같이 보여 줌)
    faces: new Map(),    // 카드 키 -> { track, img, px, first, last }  ('얼굴 기록', 사람마다 한 장)
    faceVer: 0,          // 얼굴 기록이 바뀔 때마다 1씩 (목록을 바뀔 때만 다시 그리려고)
    log: [],             // { ms, level, text }
  };
  $("camName").textContent = CAM;

  // ── 메시지 ─────────────────────────────────────────
  function onPose(m) {
    const now = Date.now();
    S.pose.set(m.track, { m, seen: now });
    const prev = S.lastAct.get(m.track);
    if (m.action && m.action !== prev) {
      const d = M.DANGER_BY_ID[m.action];
      addLog(now, m.level, `사람 #${m.track} ${d ? d.ko : m.action}${M.RESCUE_IDS.includes(m.action) ? " · 구조 필요할 수 있음" : ""}${m.why ? ` (${m.why})` : ""}`);
      if (!S.acts.has(m.track)) S.acts.set(m.track, new Set());
      S.acts.get(m.track).add(m.action);
      S.faceVer++;
    }
    S.lastAct.set(m.track, m.action || null);
  }
  // 얼굴 사진: 같은 사람(추적 번호, 1분 안)이면 카드의 사진을 더 좋은(큰) 것으로 바꾸고, 아니면 새 카드
  function onFace(m) {
    const ms = (m.ts ?? Date.now() / 1000) * 1000;
    let key = null;
    for (const [k, f] of S.faces) if (f.track === m.track && Math.abs(ms - f.last) < SAME_PERSON_SEC * 1000) key = k;
    const f = key ? S.faces.get(key) : { track: m.track, first: ms };
    // 사진은 순서가 바뀌어 도착할 수 있으므로 늦게 온 것이 아니라 얼굴이 더 크게(선명하게) 잡힌 것을 남긴다
    if (!f.img || (m.px ?? 0) >= (f.px ?? 0)) Object.assign(f, { img: m.image, px: m.px });
    f.first = Math.min(f.first, ms);
    f.last = Math.max(f.last ?? ms, ms);
    S.faces.set(key || `${m.track}@${ms}`, f);
    while (S.faces.size > 30) S.faces.delete(S.faces.keys().next().value);   // 허브 MAX_FACES 와 같음
    S.faceVer++;
  }
  function addLog(ms, level, text) {
    S.log.unshift({ ms, level, text });
    if (S.log.length > 60) S.log.pop();
  }

  function connect() {
    const ws = new WebSocket(`${location.protocol === "https:" ? "wss" : "ws"}://${location.host}/ws`);
    // 다시 연결되면 얼굴 기록을 비우고 허브가 가진 것만 다시 받는다: 서버를 껐다 켜면 열려 있던 화면에서도 사라지게
    ws.onopen = () => { S.connected = true; S.faces.clear(); S.faceVer++; };
    ws.onclose = () => { S.connected = false; setTimeout(connect, 1500); };
    ws.onmessage = (e) => {
      const m = JSON.parse(e.data);
      if (m.from !== CAM) return;
      // 새로 연결하면 서버가 마지막 메시지를 다시 보낸다. 오래된 pose 는 지금 상태가 아니므로 건너뛴다
      // (얼굴 사진은 기록이라 지난 것도 받는다: 서버가 최근 30장을 다시 보내 줌)
      if (m.type === "pose" && Date.now() / 1000 - (m.ts ?? 0) > 2) return;
      if (m.type === "pose" && m.track != null) onPose(m);
      else if (m.type === "face" && m.track != null && m.image) onFace(m);
    };
  }

  // ── 카메라 영상: 서버가 스트림을 받고 있을 때만 연결 (관제실 app.js 와 같음) ──
  function pollCamera() {
    fetch("/api/cameras").then((r) => r.json()).then((cams) => {
      const online = cams[CAM]?.online === true, img = $("vid");
      if (online && !S.camOnline) img.src = `/api/camera/${CAM}/stream`;
      if (!online && S.camOnline) img.removeAttribute("src");
      S.camOnline = online;
      $("vidEmpty").hidden = online;
      $("vidEmpty").textContent = cams[CAM] ? "영상 연결 끊김" : `카메라 ${CAM} 설정 안 됨 (CAMERAS)`;
    }).catch(() => {}).finally(() => setTimeout(pollCamera, 3000));
  }
  // 영상 비율에 맞춰 두 화면 크기를 맞춘다 (박스가 어긋나지 않게). MJPEG 은 load 이벤트가 안 오므로 그릴 때마다 확인한다
  let aspect = "";
  function fitAspect() {
    const img = $("vid"), a = img.naturalWidth ? `${img.naturalWidth} / ${img.naturalHeight}` : "";
    if (a && a !== aspect) { aspect = a; document.querySelectorAll(".cam").forEach((c) => { c.style.aspectRatio = a; }); }
  }

  // ── 그리기 ─────────────────────────────────────────
  const live = () => [...S.pose.values()].filter((p) => Date.now() - p.seen < STALE_MS).map((p) => p.m);
  const rank = (lv) => M.LEVEL[lv]?.rank ?? 0;
  // 지금 화면의 주인공: 가장 위험한 사람, 같으면 가장 크게(가까이) 보이는 사람
  function focus(ps) {
    const area = (m) => (m.box[2] - m.box[0]) * (m.box[3] - m.box[1]);
    return ps.slice().sort((a, b) => rank(b.level) - rank(a.level) || area(b) - area(a))[0];
  }
  function size(cv) {
    const r = cv.getBoundingClientRect(), dpr = window.devicePixelRatio || 1;
    const w = Math.round(r.width * dpr), h = Math.round(r.height * dpr);
    if (cv.width !== w || cv.height !== h) { cv.width = w; cv.height = h; }
    return { w, h };
  }

  function drawPose(ps) {
    const cv = $("ovP"), { w, h } = size(cv), ctx = cv.getContext("2d");
    ctx.clearRect(0, 0, w, h);
    const lw = Math.max(2, h * 0.008);
    for (const m of ps) {
      const col = RISK_COL[m.level] || RISK_COL.none, k = m.keypoints || [];
      const ok = (i) => k[i] && k[i][2] >= KP_CONF;
      ctx.strokeStyle = ctx.fillStyle = col; ctx.lineWidth = lw; ctx.lineCap = "round";
      for (const [a, b] of M.EDGES) {
        if (!ok(a) || !ok(b)) continue;
        ctx.beginPath(); ctx.moveTo(k[a][0] * w, k[a][1] * h); ctx.lineTo(k[b][0] * w, k[b][1] * h); ctx.stroke();
      }
      for (let i = 0; i < 17; i++) if (ok(i)) { ctx.beginPath(); ctx.arc(k[i][0] * w, k[i][1] * h, lw * 1.2, 0, Math.PI * 2); ctx.fill(); }
      const on = (m.actions || []).filter((a) => a.level !== "pending").map((a) => M.DANGER_BY_ID[a.id]?.ko.split(" · ")[0] || a.id);
      M.drawBox(ctx, m.box, w, h, col, `#${m.track} ${on.join(" · ") || "사람"}`, false);
    }
  }

  // 얼굴 화면: 머리 몸 점(코 · 눈 · 귀)으로 얼굴 부분을 영상에서 잘라 크게 (pose_detect.py FaceShots.crop 과 같은 계산)
  function faceRect(m, iw, ih) {
    const k = m.keypoints || [], pts = [0, 1, 2, 3, 4].filter((i) => k[i] && k[i][2] >= KP_CONF).map((i) => [k[i][0] * iw, k[i][1] * ih]);
    if (pts.length < 2) return null;
    const xs = pts.map((p) => p[0]), ys = pts.map((p) => p[1]);
    const cx = (Math.min(...xs) + Math.max(...xs)) / 2, cy = (Math.min(...ys) + Math.max(...ys)) / 2;
    const span = Math.max(Math.max(...xs) - Math.min(...xs), Math.max(...ys) - Math.min(...ys));
    const px = span * 1.6;   // 귀~귀(또는 눈~눈) 거리로 어림한 얼굴 너비
    return { x: cx - px * 0.8, y: cy - px * 0.75, side: px * 1.6, px };
  }
  function drawFace(m) {
    const cv = $("cvF"), { w, h } = size(cv), ctx = cv.getContext("2d"), img = $("vid");
    ctx.fillStyle = "#0b1017"; ctx.fillRect(0, 0, w, h);
    const say = (t) => { ctx.fillStyle = "#4a5870"; ctx.textAlign = "center"; ctx.font = `600 ${Math.round(h * 0.06)}px system-ui, sans-serif`; ctx.fillText(t, w / 2, h / 2); ctx.textAlign = "start"; };
    if (!S.camOnline || !img.naturalWidth) return say("영상 없음"), "—";
    if (!m) return say("사람 없음"), "—";
    const iw = img.naturalWidth, ih = img.naturalHeight;
    const r = faceRect(m, iw, ih);
    if (!r) return say(`#${m.track} 얼굴 안 보임`), `#${m.track} 얼굴 안 보임`;
    const side = Math.min(w, h) * 0.86, x0 = (w - side) / 2, y0 = (h - side) / 2;
    ctx.imageSmoothingEnabled = r.px >= 60;   // 작은 얼굴은 픽셀이 깨진 그대로 보여 준다 (판정이 어려운 이유가 보이게)
    ctx.drawImage(img, r.x, r.y, r.side, r.side, x0, y0, side, side);
    const small = r.px < FACE_MIN_PX, saved = [...S.faces.values()].some((f) => f.track === m.track);
    const col = small ? "#7d8ba1" : saved ? "#3ecf8e" : "#4ea1ff";
    const tag = `#${m.track} 얼굴 ${Math.round(r.px)}px${small ? " · 작아서 기록 안 함" : saved ? " · 기록됨" : ""}`;
    M.drawBox(ctx, [x0 / w, y0 / h, (x0 + side) / w, (y0 + side) / h], w, h, col, tag, small);
    return tag;
  }

  // ── 목록 ───────────────────────────────────────────
  function band(m) {
    const now = !m ? "카메라에 사람이 없어요"
      : M.RESCUE_IDS.includes(m.action) ? `🩹 사람 #${m.track} ${M.DANGER_BY_ID[m.action].ko} · 구조가 필요할 수 있어요`
      : m.action ? `사람 #${m.track} ${M.DANGER_BY_ID[m.action]?.ko || m.action}`
      : `사람 #${m.track} · 위험 행동 없음`;
    $("band").className = "band";
    $("band").innerHTML = `<span class="st">${esc(now)}</span>`;
  }

  function dangerList(ps) {
    const el = $("dl");
    if (!el.dataset.ready) {
      el.className = "dl9";
      el.innerHTML = M.DANGERS.map((d) => `<span class="led" data-led="${d.id}"></span><span data-row="${d.id}">${d.ko}</span>
        <span class="chip ${d.level}">${M.LEVEL[d.level].ko}</span><span class="cr">${M.CULPRIT_IDS.includes(d.id) ? "위험행동" : M.RESCUE_IDS.includes(d.id) ? '<span style="color:var(--info)">구조</span>' : ""}</span>`).join("");
      el.dataset.ready = "1";
    }
    const acts = ps.flatMap((m) => (m.actions || []).map((a) => ({ ...a, track: m.track })));
    for (const d of M.DANGERS) {
      const mine = acts.filter((a) => a.id === d.id), a = mine.find((x) => x.level !== "pending") || mine[0];
      el.querySelector(`[data-led="${d.id}"]`).className = `led${a ? (a.level === "pending" ? " pending" : ` on ${a.level}`) : ""}`;
      const row = el.querySelector(`[data-row="${d.id}"]`);
      row.className = a && a.level !== "pending" ? "on-row" : "";
      row.title = a ? `#${a.track} ${a.why}` : d.rule;
    }
  }

  // 얼굴 기록: 사람마다 사진 한 장 (최근 것 먼저). 사진이 바뀌거나 지금 화면의 사람이 바뀔 때만 다시 그린다
  let drawnFaces = "";
  function faceList(focusTrack) {
    const sig = `${S.faceVer}|${focusTrack}`;
    if (sig === drawnFaces) return;
    drawnFaces = sig;
    const list = [...S.faces.values()].sort((a, b) => b.last - a.last);
    $("faceN").textContent = `${list.length}명`;
    $("faceHint").hidden = list.length > 0;
    $("faces").innerHTML = list.map((f) => {
      const acts = [...(S.acts.get(f.track) || [])].map((id) => M.DANGER_BY_ID[id]?.ko.split(" · ")[0] || id);
      return `<figure class="fc${f.track === focusTrack ? " now" : ""}" style="margin:0">
        <img src="${esc(f.img)}" alt="사람 #${esc(f.track)} 얼굴">
        <div class="n"><span>사람 #${esc(f.track)}</span><span class="muted">${esc(f.px ?? "—")}px</span></div>
        <div class="s">${fmt(f.first)}${f.last - f.first > 1000 ? `~${fmt(f.last)}` : ""}${acts.length ? ` · ${esc(acts.join(", "))}` : ""}</div></figure>`;
    }).join("");
  }

  function logList() {
    $("evN").textContent = `${S.log.length}건`;
    $("ev").innerHTML = S.log.slice(0, 40).map((e) => `<li><time>${fmt(e.ms)}</time><span class="chip ${esc(e.level)}">${M.LEVEL[e.level]?.ko || esc(e.level)}</span><span>${esc(e.text)}</span></li>`).join("")
      || '<li class="muted">아직 위험 행동이 없어요.</li>';
  }

  // 영상 위 그림은 매 화면, 목록은 0.3초마다 (탭이 뒤로 가 화면 갱신이 멈춰도 목록은 계속 쌓이게 따로 돈다)
  function frame() {
    fitAspect();
    const ps = live(), m = focus(ps);
    drawPose(ps);
    $("tagF").textContent = drawFace(m);
    $("tagP").textContent = ps.length ? `사람 ${ps.length}명` : "사람 없음";
    requestAnimationFrame(frame);
  }
  function lists() {
    const ps = live(), m = focus(ps);
    band(m); dangerList(ps); faceList(m?.track); logList();
    $("conn").textContent = S.connected ? "연결됨" : "연결 끊김";
    $("conn").classList.toggle("on", S.connected);
    $("camLive").textContent = S.camOnline ? `● 영상 ${CAM}` : `영상 ${CAM} 끊김`;
    $("camLive").classList.toggle("on", S.camOnline);
  }

  connect();
  pollCamera();
  lists();
  setInterval(lists, 300);
  requestAnimationFrame(frame);
})();
