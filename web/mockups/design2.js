// 시안 2 · 카드형: 로봇견마다 카드 하나 (카메라 + 큰 숫자), 아래에 지도·증거·이벤트
(() => {
  const M = Mock;
  const COLOR = { A: "#2f7bf5", B: "#12a36b", C: "#d99a0b" };
  const THEME = {
    bg: "#ffffff", floor: "#f7f9fc", grid: "#e9eef5", fog: "rgba(213,220,230,.95)", wall: "#3b4656",
    obstacle: "#d5dce6", exit: "#e0453c", path: "rgba(47,123,245,.35)", lidar: "#0aa5c9",
    muted: "#98a2b3", alert: "#e0453c", warn: "#d99a0b", alertSoft: "rgba(224,69,60,.12)",
    link: "rgba(60,70,90,.35)", dog: COLOR, onDog: "#ffffff",
  };
  const sim = M.createSim(), S = sim.S;
  const $ = (id) => document.getElementById(id);

  $("prog").innerHTML = M.MISSIONS.map((m, i) => `<li><span class="n">${i + 1}</span>${m.ko}</li>`).join("");
  $("dogs").innerHTML = M.DOGS.map((n) => `
    <article class="card dog">
      <div class="cam"><canvas id="cam${n}"></canvas>
        <div class="over"><span class="pill live">● 실시간</span><span class="pill">모의 영상</span></div></div>
      <div class="body" id="body${n}"></div>
    </article>`).join("");
  $("pause").onclick = () => { sim.togglePause(); $("pause").textContent = S.paused ? "재개" : "일시정지"; };
  $("estop").onclick = () => { sim.toggleEstop(); $("estop").textContent = S.estop ? "정지 해제" : "전체 정지"; };

  function body(d) {
    const lv = M.battLevel(d.battery_v);
    const st = d.state === "stopped" ? "stop" : ["walking", "moving", "returning"].includes(d.state) ? "walk" : d.state === "watching" ? "watch" : "";
    const follow = d.follow
      ? `<div class="follow ${d.follow.seen ? "" : "off"}"><i></i>${d.follow.seen ? `앞 개 마커 ID ${d.follow.id}를 ${d.follow.dist.toFixed(2)} m 거리에서 따라가는 중` : "마커 따라가기 멈춤 (포위 위치로 이동)"}</div>`
      : `<div class="follow"><i></i>LiDAR ${S.scan.length}점 · ${S.mapDone ? "지도 고정, 새 물체 감시 중" : `지도 ${S.knownPct}% 완성`}</div>`;
    return `
      <div class="head"><span class="badge" style="background:${COLOR[d.name]}">${d.name}</span>
        <div><b>${M.ROLE[d.name]}</b><small>${M.ROLE_SUB[d.name]}</small></div>
        <span class="state ${st}">${M.STATE_KO[d.state]}</span></div>
      <div class="metrics">
        <div class="m ${lv}"><span>배터리</span><b>${d.battery_v.toFixed(2)}<small>V</small></b><div class="bar2"><i style="width:${M.battPct(d.battery_v)}%"></i></div></div>
        <div class="m ${d.front_cm < 15 ? "warn" : ""}"><span>앞 장애물</span><b>${d.front_cm}<small>cm</small></b></div>
        <div class="m"><span>기울기</span><b>정상</b></div>
        <div class="m"><span>Wi-Fi</span><b>${d.rssi}<small>dBm</small></b></div>
      </div>${follow}`;
  }

  function banner() {
    const b = $("banner");
    let html = "", cls = "";
    if (S.estop) { cls = "stop"; html = `<span class="ico">■</span>비상 정지됨 · 모든 로봇견이 멈췄습니다<button class="btn" id="bx">정지 해제</button>`; }
    else if (S.mission === "SUSPECT") { cls = "warn"; html = `<span class="ico">!</span>정찰견 A가 지도에 없는 물체를 발견해 카메라로 확인하고 있습니다`; }
    else if (S.mission === "CONTAIN") { cls = "alert"; html = `<span class="ico">!</span>사건 #${S.target.id} · 침입자 확인, A·B·C가 세 방향에서 둘러싸는 중 · ${M.fmtDur(sim.elapsed())}`; }
    else if (S.mission === "HANDOVER") { cls = "alert"; html = `<span class="ico">✓</span>포위 완료 · 증거 사진 3장 저장됨 · 관리자에게 인계해 주세요<button class="btn" id="bx">인계 완료</button>`; }
    if (!html) { b.hidden = true; b.dataset.k = ""; return; }
    const key = cls + (S.mission === "CONTAIN" ? Math.floor(sim.elapsed()) : S.mission);
    if (b.dataset.k === key) return;
    b.dataset.k = key; b.hidden = false; b.className = `banner ${cls}`; b.innerHTML = html;
    const bx = $("bx");
    if (bx) bx.onclick = () => (S.estop ? $("estop").click() : sim.handoverDone());
  }

  let evSeen = -1, evidSeen = -1;
  function renderDom() {
    const idx = M.MISSIONS.findIndex((m) => m.key === S.mission);
    [...$("prog").children].forEach((li, i) => { li.className = i === idx ? (M.HOT.has(S.mission) ? "now hot" : "now") : ""; });
    for (const n of M.DOGS) $(`body${n}`).innerHTML = body(S.dogs[n]);
    $("mapmeta").textContent = S.mapDone ? `지도 고정 · 알림 완료 ${S.remembered.length}개` : `만드는 중 ${S.knownPct}%`;
    banner();
    if (evidSeen !== S.evidence.length + S.incident) {
      evidSeen = S.evidence.length + S.incident;
      $("evmeta").textContent = `${S.evidence.length}장`;
      $("ev").innerHTML = S.evidence.slice(0, 6).map((e) =>
        `<figure><img src="${e.img}" alt="사건 ${e.id} Dog ${e.dog} 증거"><figcaption>#${e.id} · Dog ${e.dog} · ${M.fmtTime(e.ts)}</figcaption></figure>`).join("");
    }
    if (evSeen !== S.evSeq) {
      evSeen = S.evSeq;
      $("logmeta").textContent = `최근 ${Math.min(30, S.events.length)}건`;
      $("log").innerHTML = S.events.slice(0, 30).map((e) =>
        `<li><span class="lv ${e.level}"></span><span>${e.text}</span><time>${M.fmtTime(e.ts)}</time></li>`).join("");
    }
  }

  let camT = 0;
  M.run(sim, (dt) => {
    M.drawMap($("map"), S, THEME);
    camT += dt;
    if (camT > 0.08) { camT = 0; for (const n of M.DOGS) M.drawCam($(`cam${n}`), n, S); }
  }, renderDom);
  M.nav("design2.html");
})();
