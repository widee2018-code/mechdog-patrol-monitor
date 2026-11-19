"""추론 탭(얼굴·행동) 기록이 Supabase inference_log 까지 가는지 확인한다.

hub 에 가짜 `face` / `pose` 메시지를 보내고, recorder 가 inference_log 에 적었는지 Supabase 에서 읽어 본다.
hub 와 recorder 가 먼저 떠 있어야 한다 (docker compose --profile record up --build -d).
테스트 행은 source = TEST 로 적혀서 실제 카메라(A/B/C) 기록과 섞이지 않는다.

    python tools/infer_test.py                    # 보내고 확인 (기본: 127.0.0.1:9000)
    python tools/infer_test.py --host 192.168.0.11
    python tools/infer_test.py --no-check         # 보내기만 (Supabase 키 없이)
    python tools/infer_test.py --cleanup          # source = TEST 테스트 행 지우기
"""
import argparse, json, os, socket, time, urllib.parse, urllib.request
from datetime import datetime, timezone

ENV = os.path.join(os.path.dirname(__file__), "..", "cloud", "supabase", ".env")
TABLE = "inference_log"
FACE = ["unknown", "hold", "culprit"]
POSE = ["still", "reach", "strike", "fall"]

p = argparse.ArgumentParser()
p.add_argument("--host", default="127.0.0.1")
p.add_argument("--port", type=int, default=9000)
p.add_argument("--source", default="TEST", help="카메라 이름 대신 적을 값 (확인·삭제에 쓴다)")
p.add_argument("--wait", type=float, default=15, help="Supabase 에 행이 보일 때까지 기다릴 최대 초")
p.add_argument("--no-check", action="store_true", help="보내기만 하고 Supabase 확인은 안 함")
p.add_argument("--cleanup", action="store_true", help="source 가 --source 인 테스트 행을 지우고 끝냄")
a = p.parse_args()


def load_env():
    """cloud/supabase/.env 의 SUPABASE_URL, SUPABASE_SERVICE_KEY (recorder 와 같은 파일)."""
    env = {}
    with open(ENV, encoding="utf-8") as f:
        for line in f:
            k, sep, v = line.strip().partition("=")
            if sep and not k.startswith("#"):
                env[k.strip()] = "".join(c for c in v if c.isascii() and c.isprintable() and c != " ")
    return env["SUPABASE_URL"].rstrip("/"), env["SUPABASE_SERVICE_KEY"]


def supabase(method, query):
    url, key = load_env()
    req = urllib.request.Request(f"{url}/rest/v1/{TABLE}?{query}", method=method,
                                 headers={"apikey": key, "Authorization": f"Bearer {key}",
                                          "Prefer": "return=representation"})  # DELETE 도 지운 행을 돌려받아 개수를 센다
    with urllib.request.urlopen(req, timeout=15) as r:
        body = r.read()
    return json.loads(body) if body else None


src = urllib.parse.quote(a.source)

if a.cleanup:
    rows = supabase("DELETE", f"source=eq.{src}&select=id") or []
    print(f"{TABLE} 에서 source = {a.source} 인 행 {len(rows)}개를 지웠습니다.")
    raise SystemExit

# 메시지 형식은 web/mockups/danger_actions.html 의 예시와 같다.
# 얼굴은 verdict, 행동은 action 필드에 클래스가 있다. 클래스가 바뀔 때만 기록되므로 매번 다른 클래스를 보낸다.
msgs = [{"type": "face", "from": a.source, "track": 1, "verdict": v, "score": 0.5 + i * 0.1} for i, v in enumerate(FACE)]
msgs += [{"type": "pose", "from": a.source, "track": 1, "action": v, "level": "high", "confidence": 0.6 + i * 0.1}
         for i, v in enumerate(POSE)]
msgs.append(dict(msgs[-1]))  # 같은 클래스가 또 오면 기록되지 않아야 한다

sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
start = datetime.now(timezone.utc)
print(f"{a.host}:{a.port} 로 face {len(FACE)}개, pose {len(POSE) + 1}개(마지막은 중복) 전송")
for m in msgs:
    m["ts"] = time.time()
    sock.sendto(json.dumps(m).encode(), (a.host, a.port))
    print("  →", m["type"], m.get("verdict") or m.get("action"))
    time.sleep(0.2)

if a.no_check:
    raise SystemExit

want = len(FACE) + len(POSE)
since = urllib.parse.quote(start.isoformat())
query = f"source=eq.{src}&ts=gte.{since}&select=ts,kind,label,track,confidence&order=ts.asc"
print(f"\nSupabase {TABLE} 확인 중 (최대 {a.wait:.0f}초)...")
deadline = time.time() + a.wait
rows = []
while time.time() < deadline:
    rows = supabase("GET", query)
    if len(rows) >= want:
        break
    time.sleep(1)

for r in rows:
    print(f"  {r['ts']}  {r['kind']:<4}  {r['label']:<8}  track={r['track']}  conf={r['confidence']}")
if len(rows) == want:
    print(f"\n성공: {want}행이 기록됐고 중복 메시지는 건너뛰었습니다. 지우려면 --cleanup")
elif len(rows) > want:
    print(f"\n주의: {want}행이어야 하는데 {len(rows)}행입니다 (중복 건너뛰기 확인 필요)")
else:
    print(f"\n실패: {want}행 중 {len(rows)}행만 보입니다. hub 와 recorder 가 떠 있는지, "
          "recorder 로그(docker compose logs recorder)에 insert failed 가 있는지 확인하세요.")
    raise SystemExit(1)
