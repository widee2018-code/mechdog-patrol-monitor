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
  $("start").onclick = () => sim.start() && renderDom();
  $("regroup").onclick = () => sim.regroupNow() && renderDom();
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
    else if (S.mission === "HANDOVER") { cls = "alert"; html = `<span class="ico">●</span>포위 유지 중 · 증거 사진 3장 저장 · 상황이 끝나면 재정렬을 누르세요 (${M.fmtDur(sim.elapsed())})<button class="btn hold" id="bx">↺ 재정렬</button>`; }
    if (!html) { b.hidden = true; b.dataset.k = ""; return; }
    const key = cls + (S.mission === "CONTAIN" || S.mission === "HANDOVER" ? Math.floor(sim.elapsed()) : S.mission);
    if (b.dataset.k === key) return;
    b.dataset.k = key; b.hidden = false; b.className = `banner ${cls}`; b.innerHTML = html;
    const bx = $("bx");
    if (bx) bx.onclick = () => (S.estop ? $("estop").click() : $("regroup").click());
  }

  // ── 사이렌 (#6) ─────────────────────────────────────────────
  // 알리는 방법 4가지: 사이렌 소리 · 경광등 그림 깜빡임 · 경보 배너(위 banner) · 브라우저 알림 팝업
  // 울림: 범인 확인(CONFIRM) 단계가 시작될 때 (팀 결정, #6)
  // 멈춤: "확인" 버튼 또는 REGROUP 시작 중 먼저 오는 것 · 비상 정지 · 오탐으로 탐사 복귀
  // 이 시안의 가짜 시나리오는 "의심·확인"(SUSPECT)이 한 단계라서, 그 단계의 뒤쪽 절반을 확인 단계로 봄
  const RING_MISSIONS = new Set(["CONFIRM", "CONTAIN", "HANDOVER"]);
  const inConfirm = () => RING_MISSIONS.has(S.mission) || (S.mission === "SUSPECT" && S.pt >= S.dur * 0.5);
  const siren = { armed: false, ringing: false, ackedId: null, notifiedId: null, ctx: null, osc: null, gain: null, timer: null,
    ringId: null, ringAt: 0, ringArmed: false };

  // 사이렌 기록: 오른쪽 아래 "이벤트" 목록에 남김 (못 봤을 때 나중에 확인용)
  function sirenLog(level, text) {
    S.events.unshift({ ts: Date.now(), level, text, incident: S.target ? S.target.id : 0 });
    if (S.events.length > 80) S.events.length = 80;
    S.evSeq++;
  }
  const since = (t) => M.fmtDur((Date.now() - t) / 1000);

  function sirenSoundOn() {
    if (!siren.ctx || siren.osc) return;
    const ctx = siren.ctx, osc = ctx.createOscillator(), gain = ctx.createGain();
    osc.type = "square"; gain.gain.value = 0.05;
    osc.connect(gain).connect(ctx.destination); osc.start();
    let hi = false;
    const flip = () => { hi = !hi; osc.frequency.setValueAtTime(hi ? 960 : 720, ctx.currentTime); };   // 삐-뽀 두 음
    flip(); siren.timer = setInterval(flip, 450);
    siren.osc = osc; siren.gain = gain;
  }
  function sirenSoundOff() {
    clearInterval(siren.timer); siren.timer = null;
    if (siren.osc) { try { siren.osc.stop(); } catch (e) {} siren.osc.disconnect(); siren.osc = null; }
  }
  function sirenNotify(id) {
    if (siren.notifiedId === id || !("Notification" in window) || Notification.permission !== "granted") return;
    siren.notifiedId = id;
    try { new Notification("🚨 침입자 확인", { body: `사건 #${id} · A·B·C가 포위 중입니다. 대시보드를 확인하세요.`, tag: `incident-${id}` }); } catch (e) {}
  }

  $("sirenArm").onclick = async () => {
    if (siren.armed) {                       // 다시 누르면 알림 끄기
      siren.armed = false; sirenSoundOff();
      sirenLog("warn", "🔕 사이렌 알림 꺼짐 (소리 · 팝업 안 나옴)");
    } else {
      sirenLog("info", "🔔 사이렌 알림 켜짐");
      siren.armed = true;
      try { siren.ctx = siren.ctx || new (window.AudioContext || window.webkitAudioContext)(); await siren.ctx.resume(); } catch (e) {}
      if ("Notification" in window && Notification.permission === "default") { try { await Notification.requestPermission(); } catch (e) {} }
    }
    updateSiren();
  };
  $("sirenAck").onclick = () => {
    siren.ackedId = S.target && S.target.id;
    sirenLog("info", `✅ 사이렌 확인 · 사건 #${siren.ackedId} · 관제가 확인 버튼을 누름 (울린 지 ${since(siren.ringAt)})`);
    siren.ringId = null;
    updateSiren();
  };

  function updateSiren() {
    const id = S.target && S.target.id;
    const ringing = inConfirm() && !S.estop && id != null && siren.ackedId !== id;
    // 울림 시작 / 확인 없이 멈춤 기록
    if (ringing && siren.ringId !== id) {
      siren.ringId = id; siren.ringAt = Date.now();
      sirenLog("alert", `🚨 사이렌 울림 · 사건 #${id} · 범인 확인 단계 시작` + (siren.armed ? "" : " (알림 꺼져 있어 소리 · 팝업 안 나옴)"));
    } else if (!ringing && siren.ringId != null) {
      const why = S.estop ? "비상 정지" : S.mission === "REGROUP" ? "재정렬 신호 → 줄 다시 서기(REGROUP) 시작"
        : (S.mission === "EXPLORE" || S.mission === "PATROL") ? "오탐 · 탐사로 복귀" : S.mission;
      sirenLog("warn", `⚠ 사이렌 자동 멈춤 · 사건 #${siren.ringId} · ${why} · 아무도 확인하지 않음 (울린 시간 ${since(siren.ringAt)})`);
      siren.ringId = null;
    }
    siren.ringing = ringing;
    $("siren").classList.toggle("on", ringing);
    $("sirenAck").hidden = !ringing;
    const arm = $("sirenArm");
    arm.textContent = siren.armed ? "🔔 알림 켜짐" : "🔔 알림 켜기";
    arm.classList.toggle("on", siren.armed);
    if (ringing && siren.armed) { sirenSoundOn(); sirenNotify(id); } else sirenSoundOff();
    $("sirenState").textContent = ringing ? `사이렌 울림 · 사건 #${id}` : "사이렌 대기";
    $("sirenSub").textContent = ringing
      ? (siren.armed ? "확인을 누르면 멈춰요" : "소리 꺼짐 · 알림 켜기를 누르세요")
      : (siren.armed ? "소리 · 팝업 준비됨" : "알림이 꺼져 있어요");
  }

  // ── 시작 준비 (STANDBY) ─────────────────────────────────────
  function renderReady() {
    $("ready").hidden = S.started;
    if (S.started) return;
    $("readyGrid").innerHTML = M.DOGS.map((n) => {
      const items = S.ready[n] || [], ok = items.filter((r) => r.ok).length;
      return `<article class="rcard" style="--c:${COLOR[n]}">
        <header><span class="badge">${n}</span>${M.ROLE[n]}<span class="sum ${ok === items.length ? "ok" : "warn"}">${ok}/${items.length}</span></header>
        ${items.map((r) => `<div class="ritem ${r.ok ? "ok" : ""}"><i></i><span>${r.label}</span><b>${r.val}</b></div>`).join("")}
      </article>`;
    }).join("");
    $("start").disabled = !S.allReady || S.estop;
    $("readySub").textContent = S.estop ? "비상 정지 중에는 시작할 수 없어요"
      : S.allReady ? "모두 준비됐어요 · 탐사 시작을 누르세요" : "세 마리를 일렬로 세우고 모든 항목이 초록이 되면 탐사를 시작할 수 있어요";
  }

  let evSeen = -1;
  function renderDom() {
    updateSiren();
    renderReady();
    $("regroup").disabled = S.mission !== "HANDOVER" || S.estop;
    const idx = M.MISSIONS.findIndex((m) => m.key === S.mission);
    [...$("steps").children].forEach((li, i) => { li.className = i === idx ? (M.HOT.has(S.mission) ? "now hot" : "now") : ""; });
    $("clock").textContent = M.fmtTime(Date.now());
    $("mapmeta").textContent = !S.started ? "대기 중 · 탐사를 시작하면 지도를 만들어요" : S.mapDone ? `지도 고정됨 · 알림 완료 물체 ${S.remembered.length}개` : `지도 만드는 중 · ${S.knownPct}%`;
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
