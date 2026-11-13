// 시안 3 · 사건 중심형: 평소엔 조용한 한 문장 + 지도, 사건이 나면 화면 전체가 사건 화면으로 바뀜
(() => {
  const M = Mock;
  const CALM = {
    bg: "#ffffff", floor: "#fbfaf7", grid: "#efece5", fog: "rgba(226,222,213,.95)", wall: "#3a3d44",
    obstacle: "#e2ded5", exit: "#d93a30", path: "rgba(47,111,223,.3)", lidar: "#1aa3c4",
    muted: "#9a9ea6", alert: "#d93a30", warn: "#c9870a", alertSoft: "rgba(217,58,48,.12)",
    link: "rgba(40,45,55,.3)", dog: { A: "#2f6fdf", B: "#138a5c", C: "#c9870a" }, onDog: "#ffffff",
  };
  const INC = {
    ...CALM, bg: "#1c1517", floor: "#221a1c", grid: "#2e2326", fog: "rgba(0,0,0,.6)", wall: "#c9b8ba",
    obstacle: "#3a2c30", path: "rgba(255,255,255,.12)", muted: "#b19c9f", alert: "#ff5248",
    alertSoft: "rgba(255,82,72,.18)", link: "rgba(255,255,255,.3)",
    dog: { A: "#6ea2ff", B: "#3fd29a", C: "#ffc24a" }, onDog: "#120d0e",
  };
  const sim = M.createSim(), S = sim.S;
  const $ = (id) => document.getElementById(id);

  $("roster").innerHTML = M.DOGS.map((n) => `
    <div class="rrow"><div class="thumb"><canvas id="cam${n}"></canvas><b style="background:${CALM.dog[n]}">${n}</b></div>
      <div id="r${n}"></div></div>`).join("");
  $("pause").onclick = () => { sim.togglePause(); $("pause").textContent = S.paused ? "재개" : "일시정지"; };
  $("estop").onclick = $("estop2").onclick = () => {
    sim.toggleEstop();
    $("estop").textContent = $("estop2").textContent = S.estop ? "정지 해제" : "전체 정지";
  };
  $("handover").onclick = () => sim.handoverDone();

  const isIncident = () => !!S.target;

  function calmTexts() {
    if (S.estop) return ["alert", "비상 정지됨", "모든 로봇견에 정지 명령을 보냈습니다. 다시 움직이려면 '정지 해제'를 누르세요."];
    if (S.paused) return ["warn", "일시정지", "로봇견이 제자리에 멈춰 있습니다."];
    const low = M.DOGS.filter((n) => M.battLevel(S.dogs[n].battery_v) !== "ok");
    const lowTxt = low.length ? ` · 배터리 낮음: ${low.join(", ")}` : "";
    if (S.mission === "MAP") return ["warn", `지도 만드는 중 · ${S.knownPct}%`, `정찰견 A가 앞장서서 사무실을 처음 훑고 있습니다${lowTxt}`];
    if (S.mission === "REGROUP") return ["warn", "줄 다시 서는 중", `인계가 끝나 A·B·C가 순찰 줄로 돌아오고 있습니다${lowTxt}`];
    return [low.length ? "warn" : "ok", "순찰 중 · 이상 없음",
      `A가 앞장서고 B·C가 마커를 따라 걷는 중 · 알림 완료 물체 ${S.remembered.length}개${lowTxt}`];
  }

  function rosterRow(d) {
    const lv = M.battLevel(d.battery_v);
    const follow = d.follow ? (d.follow.seen ? `마커 ${d.follow.dist.toFixed(2)} m` : "마커 안 보임") : `LiDAR ${S.scan.length}점`;
    return `<div class="t1"><b>${M.ROLE[d.name]}</b><span>${M.STATE_KO[d.state]}</span></div>
      <div class="t2"><span class="mini ${lv}"><i style="width:${M.battPct(d.battery_v)}%"></i></span>
        <span class="${lv === "ok" ? "" : "txt-" + lv}">${d.battery_v.toFixed(2)} V</span><span>· ${follow}</span></div>`;
  }

  const STEPS = [["발견", "정찰견 A가 지도에 없는 물체를 봄"], ["카메라 확인", "사람인지 확인"], ["포위", "A·B·C가 120° 간격으로 둘러쌈"], ["증거 저장", "사진 3장"], ["관리자 인계", "버튼을 눌러 마무리"]];
  function stepIndex() {
    if (S.mission === "SUSPECT") return 1;
    if (S.mission === "CONTAIN") return S.pt >= 7 ? 3 : 2;
    if (S.mission === "HANDOVER") return 4;
    return 0;
  }

  let evSeen = -1, mode = null, evidKey = "";
  function renderDom() {
    const inc = isIncident();
    if (inc !== mode) {
      mode = inc;
      document.body.classList.toggle("inc", inc);
      for (const id of ["calmHead", "chips", "normal"]) $(id).hidden = inc;
      for (const id of ["ihead", "incident"]) $(id).hidden = !inc;
      evSeen = -1;
    }
    if (!inc) {
      const [lv, h, sub] = calmTexts();
      $("dot").className = `dot ${lv === "ok" ? "" : lv}`;
      $("headline").textContent = h;
      $("sub").textContent = sub;
      $("chips").innerHTML = M.MISSIONS.map((m) => `<span class="${m.key === S.mission ? "now" : ""}">${m.ko}</span>`).join("");
      for (const n of M.DOGS) $(`r${n}`).innerHTML = rosterRow(S.dogs[n]);
      if (evSeen !== S.evSeq) {
        evSeen = S.evSeq;
        $("recent").innerHTML = S.events.slice(0, 6).map((e) => `<li><time>${M.fmtTime(e.ts)}</time><span>${e.text}</span></li>`).join("");
      }
      return;
    }
    const T = S.target, si = stepIndex();
    $("ihead").className = `ihead ${S.mission === "SUSPECT" ? "suspect" : ""}`;
    $("ititle").textContent = S.mission === "SUSPECT" ? `사건 #${T.id} · 수상한 물체 확인 중` : `사건 #${T.id} · 침입자 확인`;
    $("isub").textContent = S.estop ? "비상 정지됨 · 로봇견이 모두 멈춤"
      : S.mission === "SUSPECT" ? "정찰견 A가 멈춰서 카메라로 살펴보고 있습니다"
      : S.mission === "CONTAIN" ? "A·B·C가 침입자를 세 방향(120° 간격, 0.6 m)에서 둘러싸는 중입니다"
      : "포위 완료 · 관리자가 도착하면 '인계 완료'를 누르세요";
    $("itimer").textContent = M.fmtDur(sim.elapsed());
    $("check").innerHTML = STEPS.map(([t, d], i) =>
      `<li class="${i < si ? "done" : i === si ? "now" : ""}"><span class="c">${i < si ? "✓" : i + 1}</span><div><b>${t}</b><br><small>${d}</small></div></li>`).join("");
    $("handover").disabled = S.mission !== "HANDOVER";
    const D = S.dogs, dist = (n) => Math.hypot(D[n].x - T.x, D[n].y - T.y).toFixed(2);
    $("zcap").innerHTML = `
      <div><span>대상</span><b>${T.kind === "사람" ? `사람 · 신뢰도 ${T.conf.toFixed(2)}` : "확인 중"}</b></div>
      <div><span>A · B · C까지 (목표 0.60 m)</span><b>${dist("A")} · ${dist("B")} · ${dist("C")} m</b></div>
      <div><span>안전 규칙</span><b>30 cm 안으로 오면 물러남</b></div>`;
    const ek = S.evidence.filter((e) => e.id === T.id).length + ":" + T.id;
    if (ek !== evidKey) {
      evidKey = ek;
      $("iev").innerHTML = S.evidence.filter((e) => e.id === T.id).map((e) => `<img src="${e.img}" alt="Dog ${e.dog} 증거">`).join("");
    }
    if (evSeen !== S.evSeq) {
      evSeen = S.evSeq;
      $("ilog").innerHTML = S.events.filter((e) => e.incident === T.id).map((e) => `<li><time>${M.fmtTime(e.ts)}</time><span>${e.text}</span></li>`).join("");
    }
  }

  let camT = 0;
  M.run(sim, (dt) => {
    camT += dt;
    const camTick = camT > 0.08;
    if (camTick) camT = 0;
    if (isIncident()) {
      M.drawMap($("zmap"), S, INC, { focus: S.target, radius: 1.25 });
      if (camTick) for (const n of M.DOGS) M.drawCam($(`icam${n}`), n, S);
    } else {
      M.drawMap($("map"), S, CALM);
      if (camTick) for (const n of M.DOGS) M.drawCam($(`cam${n}`), n, S);
    }
  }, renderDom);
  M.nav("design3.html");
})();
