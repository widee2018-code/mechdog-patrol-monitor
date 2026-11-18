// 얼굴 + 행동 반반 시안(combo1~5)이 같이 쓰는 화면 조각.
// 왼쪽 = 얼굴 추론(범인 / 미등록 / 보류), 오른쪽 = 행동 추론(위험 행동 9가지), 위쪽 띠 = 범인 판정.
(function () {
  "use strict";
  const M = window.CamMock;
  const V = M.COMBO_VERDICT;
  const RISK_COL = { none: "#3ecf8e", low: "#f5b942", mid: "#ff8a3d", high: "#ff5a52" };
  const ORDER = { culprit: 0, unknown: 1, hold: 2 };
  const thumbs = new Map();

  function thumb(p, n) {
    const key = `${p.track}:${n}`;
    if (!thumbs.has(key)) {
      const c = document.createElement("canvas"); c.width = n * 2; c.height = n * 2;
      const x = c.getContext("2d"); x.fillStyle = "#1b2433"; x.fillRect(0, 0, n * 2, n * 2);
      M.drawFaceAvatar(x, [0.28, 0.18, 0.72, 0.7], n * 2, n * 2, p.hue);
      thumbs.set(key, c);
    }
    const src = thumbs.get(key), c = document.createElement("canvas");
    c.width = src.width; c.height = src.height; c.getContext("2d").drawImage(src, 0, 0);
    return c;
  }

  // 카메라: 왼쪽은 얼굴 박스, 오른쪽은 몸 점. 같은 영상(같은 사람)을 쓴다.
  function drawFaceView(cv, st, label) {
    const { w, h } = M.fitCanvas(cv), ctx = cv.getContext("2d");
    M.drawScene(ctx, w, h, label || "CAM A · 얼굴", st.t);
    if (!st.person) return;
    M.drawBody(ctx, st.kps, w, h);
    M.drawFaceAvatar(ctx, st.faceBox, w, h, st.person.hue, true);
    const p = st.person, v = V[p.state];
    const tag = p.state === "hold" ? (p.facing ? `#${p.track} 얼굴 작음` : `#${p.track} 얼굴 안 보임`) : p.state === "culprit" ? p.name : `#${p.track} 미등록`;
    M.drawBox(ctx, st.faceBox, w, h, v.color, tag, p.state === "hold");
  }
  function drawPoseView(cv, st, label, opts = {}) {
    const { w, h } = M.fitCanvas(cv), ctx = cv.getContext("2d");
    M.drawScene(ctx, w, h, label || "CAM A · 행동", st.t);
    if (!st.person) return;
    M.drawBody(ctx, st.kps, w, h);
    const on = st.active.filter((a) => a.level !== "pending");
    if (opts.box !== false) M.drawBox(ctx, st.box, w, h, RISK_COL[st.risk], on.map((a) => a.ko).join(" · ") || "사람", false);
    const hot = opts.hot ? [...new Set(st.active.flatMap((a) => M.DANGER_KPS[a.id] || []))] : null;
    M.drawSkeleton(ctx, st.kps, w, h, RISK_COL[st.risk], hot ? { hot } : {});
  }

  // 위쪽 띠: 범인 규칙과 지금 상태
  function band(el, st) {
    const p = st.person, alarm = !!(p && p.culprit);
    el.className = `band${alarm ? " alarm" : ""}`;
    const now = !p ? "카메라에 사람이 없어요"
      : p.culprit ? `🚨 ${p.name} · 사람 #${p.track} (${p.culpritWhy})`
      : p.rescue ? `🩹 사람 #${p.track} 넘어짐 · 구조가 필요할 수 있어요 (범인 아님)`
      : p.state === "unknown" ? `미등록 사람 #${p.track} · 범인 조건 행동을 하면 범인으로 등록돼요`
      : `사람 #${p.track} 얼굴 확인 중`;
    el.innerHTML = `<span class="st">${now}</span>
      <span class="rule">범인 규칙: <b>미등록</b> + <b>범인 조건 행동</b> = 범인 등록 · 얼굴 저장, 다시 오면 얼굴로 바로 범인</span>
      <span class="cul">${(st.faceBook || []).length ? st.faceBook.map((c) => `<span class="chip culprit">범인 #${c.no} 얼굴 저장됨</span>`).join("") : '<span class="chip hold">범인 명단 비어 있음</span>'}</span>`;
  }

  // 위험 행동 9가지 목록 (오른쪽 절반)
  function dangerList(el, st, opts = {}) {
    if (!el.dataset.ready) {
      el.className = "dl9";
      el.innerHTML = M.DANGERS.map((d) => `<span class="led" data-led="${d.id}"></span><span data-row="${d.id}">${d.ko}</span>
        <span class="chip ${d.level}">${M.LEVEL[d.level].ko}</span><span class="cr">${M.CULPRIT_IDS.includes(d.id) ? "범인 조건" : M.RESCUE_IDS.includes(d.id) ? '<span style="color:var(--info)">구조</span>' : ""}</span>`).join("");
      el.dataset.ready = "1";
    }
    for (const d of M.DANGERS) {
      const a = st.active.find((x) => x.id === d.id);
      el.querySelector(`[data-led="${d.id}"]`).className = `led${a ? (a.level === "pending" ? " pending" : ` on ${a.level}`) : ""}`;
      const row = el.querySelector(`[data-row="${d.id}"]`);
      row.className = a && a.level !== "pending" ? "on-row" : "";
      row.title = a ? a.why : d.rule;
    }
  }

  function card(p, now, size) {
    const el = document.createElement("div");
    el.className = `pc ${p.state}${now ? " now" : ""}`;
    const acts = [...p.acts.keys()].map((id) => M.DANGER_BY_ID[id].ko.split(" · ")[0]);
    el.innerHTML = `<span class="ph"></span><div><div class="n">${p.name}</div>
      <span class="chip ${p.state}">${V[p.state].ko}</span>
      <div class="s">${M.clock(p.first)}~${M.clock(p.last)}${p.culprit ? ` · ${p.culpritWhy}` : acts.length ? ` · ${acts.join(", ")}` : ""}</div></div>`;
    if (p.rescue) el.querySelector(".chip").insertAdjacentHTML("afterend", ' <span class="chip" style="background:rgba(77,163,255,.15);color:var(--info)">구조 필요</span>');
    el.querySelector(".ph").replaceWith(thumb(p, size));
    return el;
  }
  const sorted = (people) => [...people.values()].sort((a, b) => ORDER[a.state] - ORDER[b.state] || b.last - a.last);

  // 얼굴 2안 방식: 갤러리 (범인 → 미등록 → 보류 순)
  function gallery(el, st, opts = {}) {
    const list = sorted(st.people).slice(0, opts.max || 12);
    if (!list.length) { el.innerHTML = '<p class="muted" style="margin:0">아직 지나간 사람이 없어요.</p>'; return; }
    el.replaceChildren(...list.map((p) => card(p, st.person && p.track === st.person.track, 24)));
  }
  // 얼굴 4안 방식: 칸반 (범인 · 미등록 · 보류)
  const COLS = [["culprit", "범인"], ["unknown", "미등록"], ["hold", "보류"]];
  function kanban(el, st) {
    if (!el.dataset.ready) {
      el.className = "kb";
      el.innerHTML = COLS.map(([k, t]) => `<section class="col ${k}"><header><span>${t}</span><span class="num" data-c="${k}">0</span></header><div data-l="${k}"></div></section>`).join("");
      el.dataset.ready = "1";
    }
    for (const [k] of COLS) {
      const list = sorted(st.people).filter((p) => p.state === k);
      el.querySelector(`[data-c="${k}"]`).textContent = list.length;
      const box = el.querySelector(`[data-l="${k}"]`);
      if (!list.length) { box.innerHTML = '<div class="empty">없음</div>'; continue; }
      box.replaceChildren(...list.map((p) => card(p, st.person && p.track === st.person.track, 17)));
    }
  }
  function counts(people) {
    const c = { culprit: 0, unknown: 0, hold: 0 };
    for (const p of people.values()) c[p.state] += 1;
    return c;
  }
  function logList(el, st, max = 14) {
    el.innerHTML = st.log.slice(0, max).map((e) => {
      const chip = e.kind === "culprit" ? '<span class="chip culprit">범인</span>' : e.kind === "face" ? `<span class="chip ${e.verdict}">${V[e.verdict].short}</span>` : `<span class="chip ${e.level}">${M.LEVEL[e.level].ko}</span>`;
      return `<li><time>${M.clock(e.t)}</time>${chip}<span>${e.text}</span></li>`;
    }).join("") || '<li class="muted">아직 기록이 없어요.</li>';
  }

  window.ComboUI = { V, RISK_COL, drawFaceView, drawPoseView, band, dangerList, gallery, kanban, counts, logList, thumb };
})();
