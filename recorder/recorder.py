"""hub /ws 를 구독해 필요한 텔레메트리만 Supabase 에 기록한다. (recorder/README.md 참고)"""
import asyncio
import base64
import json
import os
import re
import time
import urllib.error
import urllib.request
from collections import deque
from datetime import datetime, timezone

import websockets

HUB_WS = os.environ.get("HUB_WS", "ws://hub:8000/ws")


def _clean(v):
    """복사·붙여넣기로 섞인 공백/비ASCII 문자(예:  )를 제거한다. URL·키에는 쓰이지 않는 문자다."""
    return "".join(c for c in v if c.isascii() and c.isprintable() and c != " ")


SUPABASE_URL = _clean(os.environ.get("SUPABASE_URL", "")).rstrip("/")
SERVICE_KEY = _clean(os.environ.get("SUPABASE_SERVICE_KEY", ""))
BUCKET = "evidence"
FLUSH_SEC = 2.0
BATCH_MAX = 200
BUFFER_MAX = 5000  # 오프라인일 때 메모리에 쌓아 둘 최대 행 수 (넘으면 오래된 것부터 버림)

_last_dog = {}  # 로봇견별 마지막 기록 시각 (1초 간격으로 솎아내기)
_last_ready = {}  # 보낸 쪽별 마지막 all_ready 값 (ready는 1초마다 오므로 값이 바뀔 때만 기록)
INFER_TABLE = "inference_log"  # 추론 탭(얼굴·행동): 클래스만 이 테이블 하나에 적는다 (kind = face / pose)
INFER_KINDS = ("face", "pose")  # 메시지 type 이름
INFER_LABEL_KEYS = ("label", "verdict", "action", "class")  # 클래스가 들어 있을 수 있는 필드, 앞에서부터 찾는다
_last_infer = {}  # (종류, 카메라, 추적번호)별 마지막 클래스 (프레임마다 오므로 클래스가 바뀔 때만 기록)
_data_url = re.compile(r"^data:image/jpeg;base64,(.*)$", re.S)


def log(*a):
    print(time.strftime("%H:%M:%S"), *a, flush=True)


def _request(method, path, body=None, headers=None):
    h = {"apikey": SERVICE_KEY, "Authorization": f"Bearer {SERVICE_KEY}"}
    h.update(headers or {})
    req = urllib.request.Request(SUPABASE_URL + path, data=body, method=method, headers=h)
    with urllib.request.urlopen(req, timeout=15) as r:
        return r.status


def iso(ts):
    return datetime.fromtimestamp(ts, timezone.utc).isoformat()


def upload_evidence(source, ts, jpeg):
    path = f"{source}/{int(ts * 1000)}.jpg"
    _request("POST", f"/storage/v1/object/{BUCKET}/{path}", jpeg,
             {"Content-Type": "image/jpeg", "x-upsert": "true"})
    return path


async def to_row(msg):
    """기록할 메시지면 DB 행(dict), 아니면 None."""
    t = msg.get("type")
    source = str(msg.get("from") or "unknown")
    ts = float(msg.get("ts") or time.time())
    if t in ("event", "mission", "intruder", "command", "alarm"):  # command: start / stop / regroup (누른 시각 = ts), alarm: 사이렌 on / off
        pass
    elif t == "ready":  # STANDBY 준비 확인: all_ready 가 바뀔 때만 저장
        if "all_ready" not in msg:
            return None
        ready = bool(msg["all_ready"])
        if _last_ready.get(source) == ready:
            return None
        _last_ready[source] = ready
    elif t == "sound":
        if not msg.get("alert"):
            return None
    elif t == "dog":
        if ts - _last_dog.get(source, 0) < 1.0:
            return None
        _last_dog[source] = ts
    elif t == "camera":
        if not msg.get("evidence"):
            return None
        msg = dict(msg)
        m = _data_url.match(msg.pop("image", "") or "")
        if m:
            try:
                msg["storage_path"] = await asyncio.to_thread(
                    upload_evidence, source, ts, base64.b64decode(m.group(1)))
            except Exception as e:  # 사진 업로드 실패해도 기록 행은 남긴다
                log("evidence upload failed:", e)
    else:
        return None
    return {"ts": iso(ts), "type": t, "source": source, "level": msg.get("level"), "payload": msg}


def _num(v):
    return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else None


def _int(v):
    return int(v) if isinstance(v, int) and not isinstance(v, bool) else None


def to_inference(msg):
    """추론 탭 메시지(face, pose)면 inference_log 행(dict), 아니면 None. 클래스만 적고 얼굴 이미지는 저장하지 않는다."""
    kind = msg.get("type")
    if kind not in INFER_KINDS:
        return None
    label = next((msg[k].strip() for k in INFER_LABEL_KEYS if isinstance(msg.get(k), str) and msg[k].strip()), None)
    source = str(msg.get("from") or "unknown")
    key = (kind, source, msg.get("track"))
    if not label:
        # 위험 행동이 없는 pose(정상)는 적지 않지만, 마지막 클래스를 지워서 같은 행동이 다시 나오면 새로 기록한다
        _last_infer.pop(key, None)
        return None
    if _last_infer.get(key) == label:
        return None
    if len(_last_infer) > 1000:  # 추적 번호가 계속 늘어도 메모리가 커지지 않게
        _last_infer.clear()
    _last_infer[key] = label
    return {"ts": iso(float(msg.get("ts") or time.time())), "kind": kind, "source": source, "label": label,
            "track": _int(msg.get("track")), "confidence": _num(msg.get("confidence")),
            "incident": _int(msg.get("incident"))}


def post_infer(rows):
    _request("POST", f"/rest/v1/{INFER_TABLE}?on_conflict=kind,source,ts,label",
             json.dumps(rows).encode(),
             {"Content-Type": "application/json",
              "Prefer": "resolution=ignore-duplicates,return=minimal"})


def post_rows(rows):
    _request("POST", "/rest/v1/telemetry_log?on_conflict=type,source,ts",
             json.dumps(rows).encode(),
             {"Content-Type": "application/json",
              "Prefer": "resolution=ignore-duplicates,return=minimal"})


async def flusher(buf, post=None, key=lambda r: (r["type"], r["source"], r["ts"]), name="telemetry_log"):
    post = post or post_rows
    while True:
        await asyncio.sleep(FLUSH_SEC)
        while buf:
            batch = [buf[i] for i in range(min(BATCH_MAX, len(buf)))]
            # 한 요청 안에 같은 키가 있으면 충돌하므로 미리 제거
            uniq = list({key(r): r for r in batch}.values())
            try:
                await asyncio.to_thread(post, uniq)
            except urllib.error.HTTPError as e:
                log(f"insert failed ({name}):", e.code, e.read()[:200])
                if 400 <= e.code < 500:  # 재시도해도 소용없는 요청이면 버린다
                    for _ in batch:
                        buf.popleft()
                    continue
                break
            except Exception as e:
                log(f"insert failed ({name}, will retry):", e)
                break
            for _ in batch:
                buf.popleft()
            log(f"recorded {len(uniq)} rows" + ("" if name == "telemetry_log" else f" -> {name}"))


async def listen(buf, ibuf):
    delay = 1
    while True:
        try:
            async with websockets.connect(HUB_WS, max_size=16 * 1024 * 1024) as ws:
                log("connected to", HUB_WS)
                delay = 1
                async for raw in ws:
                    try:
                        msg = json.loads(raw)
                        row = await to_row(msg) if isinstance(msg, dict) else None
                        infer = to_inference(msg) if isinstance(msg, dict) else None
                    except Exception as e:
                        log("bad message:", e)
                        continue
                    if row:
                        if len(buf) >= BUFFER_MAX:
                            buf.popleft()
                        buf.append(row)
                    if infer:
                        if len(ibuf) >= BUFFER_MAX:
                            ibuf.popleft()
                        ibuf.append(infer)
        except Exception as e:
            log(f"hub connection lost ({e}); retry in {delay}s")
        await asyncio.sleep(delay)
        delay = min(delay * 2, 30)


async def main():
    if not SUPABASE_URL or not SERVICE_KEY:
        raise SystemExit("SUPABASE_URL 과 SUPABASE_SERVICE_KEY 가 필요합니다 (.env)")
    buf, ibuf = deque(), deque()
    await asyncio.gather(
        listen(buf, ibuf), flusher(buf),
        flusher(ibuf, post_infer, lambda r: (r["kind"], r["source"], r["ts"], r["label"]), INFER_TABLE))


if __name__ == "__main__":
    asyncio.run(main())
