// 시안 1 · 관제실형: 큰 지도 + 카메라 3대 + 로봇견 카드 + 이벤트
(() => {
  const M = Mock;
  const COLOR = { A: "#4da3ff", B: "#3ecf8e", C: "#f5b942" };
  const THEME = {
    bg: "#0b1017", floor: "#0f1620", grid: "#18212d", fog: "rgba(46,56,72,.95)", wall: "#8a98ad",
    obstacle: "#223042", exit: "#ff7b75", path: "rgba(120,150,190,.35)", lidar: "#5ee1ff",
    muted: "#7d8ba1", alert: "#ff5a52", warn: "#f5b942", alertSoft: "rgba(255,90,82,.16)",
    link: "rgba(210,220,235,.35)", dog: COLOR, onDog: "#0a0e14",
  };
  const sim = M.createSim(), S = sim.S;
  const $ = (id) => document.getElementById(id);

  $("steps").innerHTML = M.MISSIONS.map((m) => `<li data-k="${m.key}"><span class="en">${m.key}</span>${m.ko}</li>`).join("");
  $("cams").innerHTML = M.DOGS.map((n) => `
    <figure class="cam"><canvas id="cam${n}"></canvas>
      <figcaption><span class="badge" style="background:${COLOR[n]}">${n}</span>${M.ROLE[n]}<span class="live">● LIVE</span></figcaption>
      <span class="note">모의 영상</span>
    </figure>`).join("");
  $("pause").onclick = () => { sim.togglePause(); $("pause").textContent = S.paused ? "재개" : "일시정지"; };
  $("estop").onclick = () => { sim.toggleEstop(); $("estop").textContent = S.estop ? "정지 해제" : "■ 전체 정지"; };

  function chip(d) {
    if (d.state === "stopped") return `<span class="chip stop">비상 정지</span>`;
    const cls = d.state === "walking" || d.state === "moving" || d.state === "returning" ? "walk" : d.state === "watching" ? "watch" : "";
    return `<span class="chip ${cls}">${M.STATE_KO[d.state]}</span>`;
  }

  function dogCard(d) {
    const lv = M.battLevel(d.battery_v);
    const extra = d.follow
      ? `<div class="kv"><span>앞 개 마커</span><b class="${d.follow.seen ? "ok" : "warn"}">${d.follow.seen ? `ID ${d.follow.id} · ${d.follow.dist.toFixed(2)} m` : "안 보임 (포위 중)"}</b></div>`
      : `<div class="kv"><span>LiDAR</span><b>${S.scan.length}점 · 5 Hz</b></div>`;
    return `<article class="dog" style="--c:${COLOR[d.name]}">
      <header><span class="badge">${d.name}</span><div><b>${M.ROLE[d.name]}</b><small>${M.ROLE_SUB[d.name]}</small></div>${chip(d)}</header>
      <div class="kv"><span>배터리</span><b class="${lv}">${d.battery_v.toFixed(2)} V</b></div>
      <div class="batt ${lv}"><i style="width:${M.battPct(d.battery_v)}%"></i></div>
      <div class="kv"><span>앞 장애물</span><b class="${d.front_cm < 15 ? "warn" : ""}">${d.front_cm} cm</b></div>
      <div class="kv"><span>보폭 · 회전</span><b>${d.stride} · ${d.turn}</b></div>
      <div class="kv"><span>기울기 · Wi-Fi</span><b>정상 · ${d.rssi} dBm</b></div>
      ${extra}
    </article>`;
  }

  function banner() {
    const b = $("banner");
    let html = "", cls = "";
    if (S.estop) { cls = "stop"; html = `<span class="ico">■</span>비상 정지됨 · 모든 로봇견이 멈췄습니다<button class="btn" id="bx">정지 해제</button>`; }
    else if (S.mission === "SUSPECT") { cls = "warn"; html = `<span class="ico">▲</span>정찰견 A가 지도에 없는 물체를 확인하고 있습니다 · 사건 #${S.target.id}`; }
    else if (S.mission === "CONTAIN") { cls = "alert"; html = `<span class="ico">●</span>사건 #${S.target.id} 침입자 확인 · A·B·C 3방향 포위 중 · 경과 ${M.fmtDur(sim.elapsed())}`; }
    else if (S.mission === "HANDOVER") { cls = "alert"; html = `<span class="ico">●</span>포위 완료 · 증거 사진 3장 저장 · 관리자 인계 대기 (${M.fmtDur(sim.elapsed())})<button class="btn" id="bx">인계 완료</button>`; }
    if (!html) { b.hidden = true; b.dataset.k = ""; return; }
    const key = cls + (S.mission === "CONTAIN" || S.mission === "HANDOVER" ? Math.floor(sim.elapsed()) : S.mission);
    if (b.dataset.k === key) return;
    b.dataset.k = key; b.hidden = false; b.className = `banner ${cls}`; b.innerHTML = html;
    const bx = $("bx");
    if (bx) bx.onclick = () => (S.estop ? $("estop").click() : sim.handoverDone());
  }

  let evSeen = -1;
  function renderDom() {
    const idx = M.MISSIONS.findIndex((m) => m.key === S.mission);
    [...$("steps").children].forEach((li, i) => { li.className = i === idx ? (M.HOT.has(S.mission) ? "now hot" : "now") : ""; });
    $("clock").textContent = M.fmtTime(Date.now());
    $("mapmeta").textContent = S.mapDone ? `지도 고정됨 · 알림 완료 물체 ${S.remembered.length}개` : `지도 만드는 중 · ${S.knownPct}%`;
    $("dogs").innerHTML = M.DOGS.map((n) => dogCard(S.dogs[n])).join("");
    banner();
    if (evSeen !== S.evSeq) {
      evSeen = S.evSeq;
      $("evcount").textContent = `${S.events.length}건`;
      $("log").innerHTML = S.events.slice(0, 40).map((e) =>
        `<li><time>${M.fmtTime(e.ts)}</time><span class="lv ${e.level}"></span><span>${e.text}</span></li>`).join("");
    }
  }

  let camT = 0;
  M.run(sim, (dt) => {
    M.drawMap($("map"), S, THEME);
    camT += dt;
    if (camT > 0.08) { camT = 0; for (const n of M.DOGS) M.drawCam($(`cam${n}`), n, S); }
  }, renderDom);
  M.nav("design1.html");
})();
