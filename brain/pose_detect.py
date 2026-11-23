"""행동 추론 (YOLO11n-pose): 사람마다 몸 점 17개를 뽑아 위험 행동 9가지를 판정하고 모니터 서버로 보낸다.

사람 박스도 같이 나오므로 person_detect.py 대신 이것 하나만 돌리면 된다 (detect 메시지도 같이 보냄).
사람마다 추적 번호(track)를 붙여, 같은 사람의 1초 전 자세와 비교한다. 규칙은 pose_rules.py.

보내는 메시지 (UDP 9000, docs/message_schema.md):
    detect  사람이 보이는 프레임마다 (person_detect.py 와 같음 + boxes 에 track)
    pose    사람마다 프레임마다. 위험 행동이 있으면 action(가장 위험한 것) 과 level, 없으면 action 없음
            {"type": "pose", "from": "A", "track": 3, "action": "fall", "level": "high", "why": "몸 기울기 72° ...",
             "actions": [...], "confidence": 0.81, "box": [x1, y1, x2, y2], "keypoints": [[x, y, 신뢰도] x 17]}
    좌표는 모두 화면 비율 0~1.

노트북 웹캠으로 연습 (메시지는 터미널에만 표시):
    python brain/pose_detect.py --camera 0 --show

Pi 에서 (모니터 서버가 CAMERAS="A=http://<카메라-IP>:81/stream" 으로 떠 있을 때):
    yolo export model=yolo11n-pose.pt format=ncnn imgsz=320     # 처음 한 번
    python brain/pose_detect.py --model yolo11n-pose_ncnn_model \\
        --camera http://<서버-IP>:8000/api/camera/A/stream --monitor <서버-IP>
"""
import argparse
import json
import socket
import threading
import time
import urllib.request

import cv2
from ultralytics import YOLO

from marker_follow import MONITOR_HTTP, LatestFrame, MONITOR_PORT
from pose_rules import RANK, Classifier

EDGES = [(5, 6), (5, 7), (7, 9), (6, 8), (8, 10), (5, 11), (6, 12), (11, 12), (11, 13), (13, 15), (12, 14), (14, 16),
         (0, 1), (0, 2), (1, 3), (2, 4)]
LEVEL_COLOR = {"none": (0, 200, 0), "low": (0, 200, 255), "mid": (0, 140, 255), "high": (40, 40, 230)}
FORGET_SEC = 5.0   # 이 시간 동안 안 보인 추적 번호는 판정 기록을 버린다


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="YOLO11n-pose 행동 추론 → pose · detect 메시지")
    p.add_argument("--camera", default="0", help="웹캠 번호(0), 영상 파일, 또는 MJPEG 스트림 URL")
    p.add_argument("--name", default="A", help="어느 개의 카메라인지 (메시지의 from)")
    p.add_argument("--monitor", help="모니터 서버 IP. 없으면 메시지를 터미널에만 표시")
    p.add_argument("--model", default="yolo11n-pose.pt", help="yolo11n-pose.pt 또는 NCNN 으로 바꾼 폴더 (yolo11n-pose_ncnn_model)")
    p.add_argument("--imgsz", type=int, default=320, help="추론 입력 크기. 작을수록 빠르고 먼 사람을 놓친다")
    p.add_argument("--conf", type=float, default=0.5, help="이보다 신뢰도가 낮은 사람은 무시")
    p.add_argument("--kp-conf", type=float, default=0.5, help="몸 점 신뢰도가 이보다 낮으면 안 보이는 점으로 보고, 그 점을 쓰는 규칙은 건너뜀")
    p.add_argument("--rate", type=float, default=8.0, help="최대 추론 횟수 (Hz). 속도 규칙은 시각으로 계산해서 낮춰도 된다")
    p.add_argument("--no-detect", action="store_true", help="detect 메시지는 보내지 않음 (person_detect.py 를 따로 돌릴 때)")
    p.add_argument("--blur-ratio", type=float, default=0.5,
                   help="선명도가 최근 프레임 중간값의 이 비율보다 낮으면 흐린 프레임으로 보고 행동 판단에서 뺌 (0 = 끔)")
    p.add_argument("--settle", type=float, default=1.0,
                   help="카메라를 단 로봇견이 멈춘 뒤 이 시간(초) 동안은 아직 움직이는 중으로 봄 (흔들림이 가라앉을 때까지)")
    p.add_argument("--show", action="store_true", help="카메라 화면 창 띄우기 (q: 종료)")
    return p.parse_args()


class Sharpness:
    """흐린 프레임 고르기. 선명도 = 라플라시안 분산 (가장자리가 뚜렷할수록 큼).
    카메라·조명·장면마다 값의 크기가 달라서 고정값 대신 최근 프레임들의 중간값과 비교한다."""

    def __init__(self, ratio: float, keep: int = 30):
        self.ratio, self.keep, self.recent = ratio, keep, []

    def blurry(self, frame) -> tuple[bool, float]:
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        if gray.shape[1] > 320:   # 줄여서 재면 빠르고, 흔들림 블러를 보기에는 충분하다
            gray = cv2.resize(gray, (320, int(gray.shape[0] * 320 / gray.shape[1])), interpolation=cv2.INTER_AREA)
        s = float(cv2.Laplacian(gray, cv2.CV_64F).var())
        ref = sorted(self.recent)[len(self.recent) // 2] if len(self.recent) >= 5 else None
        self.recent = (self.recent + [s])[-self.keep:]
        return bool(self.ratio > 0 and ref is not None and s < self.ratio * ref), s


class CamMotion:
    """카메라를 단 로봇견이 움직이는 중인지, 모니터 서버 /api/dogs 의 state 로 0.3초마다 확인한다.
    moving(dog_cmd.py) · follow·search(marker_follow.py) 를 움직임으로 보고, 멈춘 뒤 settle 초까지 움직이는 중으로 둔다.
    --monitor 가 없거나 서버가 응답하지 않으면 모름 → 움직이지 않는 것으로 본다 (예전과 같은 판단)."""

    MOVING = {"moving", "follow", "search"}

    def __init__(self, monitor: str | None, name: str, settle: float):
        self.url = f"http://{monitor}:{MONITOR_HTTP}/api/dogs" if monitor else None
        self.name, self.settle = name, settle
        self.until = 0.0
        if self.url:
            threading.Thread(target=self._run, daemon=True).start()

    def _run(self) -> None:
        while True:
            try:
                with urllib.request.urlopen(self.url, timeout=1) as r:
                    state = json.load(r)["dogs"].get(self.name, {}).get("state")
                if state in self.MOVING:
                    self.until = time.time() + self.settle
            except Exception:
                pass
            time.sleep(0.3)

    def moving(self) -> bool:
        return time.time() < self.until


def top_action(active):
    """확정된 위험 행동 중 가장 위험한 것. 같으면 danger_actions.html 순서가 앞선 것."""
    real = [a for a in active if a["level"] != "pending"]
    return max(real, key=lambda a: RANK[a["level"]], default=None)


def draw(view, kps, vis, box, track, risk, label):
    h, w = view.shape[:2]
    color = LEVEL_COLOR[risk]
    pt = lambda i: (int(kps[i][0] * w), int(kps[i][1] * h))
    for a, b in EDGES:
        if vis[a] and vis[b]:
            cv2.line(view, pt(a), pt(b), color, 2)
    for i in range(17):
        if vis[i]:
            cv2.circle(view, pt(i), 3, color, -1)
    x1, y1 = int(box[0] * w), int(box[1] * h)
    cv2.rectangle(view, (x1, y1), (int(box[2] * w), int(box[3] * h)), color, 1)
    cv2.putText(view, f"#{track} {label}", (x1, max(y1 - 6, 14)), cv2.FONT_HERSHEY_SIMPLEX, 0.5, color, 2)


def main() -> None:
    a = parse_args()
    model = YOLO(a.model, task="pose")
    cam = LatestFrame(a.camera)
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    send = (lambda m: sock.sendto(json.dumps(m).encode(), (a.monitor, MONITOR_PORT))) if a.monitor else (lambda m: None)
    print(f"카메라 {a.camera} → " + (f"{a.monitor}:{MONITOR_PORT}" if a.monitor else "터미널만 (--monitor 없음)"), flush=True)

    people: dict[int, Classifier] = {}   # 추적 번호 -> 판정기
    last_seen: dict[int, float] = {}
    last_label: dict[int, str] = {}      # 터미널에는 행동이 바뀔 때만 찍는다
    sharp = Sharpness(a.blur_ratio)
    cam_motion = CamMotion(a.monitor, a.name, a.settle)
    seen, last_new, last_log, runs, blurred = 0, time.time(), time.time(), 0, 0
    while True:
        t0 = time.time()
        seq, frame = cam.get()
        if seq == seen:
            if t0 - last_new > 3:
                print("영상 없음:", cam.error or "기다리는 중", flush=True)
                last_new = t0
            time.sleep(0.02)
            continue
        seen, last_new = seq, t0
        fh, fw = frame.shape[:2]
        # 흐린 프레임: 사람 박스·추적은 계속하되, 몸 점이 튀어 속도가 잘못 나오므로 행동 판단(pose)에는 넣지 않는다
        blurry, _ = sharp.blurry(frame)
        blurred += blurry
        moving = cam_motion.moving()

        # persist=True: 프레임 사이에 추적 상태를 이어 가서 같은 사람에게 같은 번호를 붙인다
        r = model.track(frame, imgsz=a.imgsz, conf=a.conf, persist=True, verbose=False)[0]
        runs += 1
        view = frame.copy() if a.show else None
        boxes = []
        if r.keypoints is not None and len(r.boxes):
            ids = r.boxes.id.int().tolist() if r.boxes.id is not None else [None] * len(r.boxes)
            for track, box, conf, xyn, kc in zip(ids, r.boxes.xyxyn.tolist(), r.boxes.conf.tolist(),
                                                 r.keypoints.xyn.tolist(), r.keypoints.conf.tolist()):
                boxes.append([round(v, 3) for v in box] + [round(conf, 2), track])
                if track is None or blurry:   # 추적 번호가 붙기 전(처음 한두 프레임)은 이전 자세와 비교할 수 없다
                    continue
                vis = [c >= a.kp_conf for c in kc]
                # 규칙은 화면 높이 단위 좌표를 쓴다 (가로세로 비율 때문에 각도가 틀어지지 않게)
                k = [(x * fw / fh, y) for x, y in xyn]
                out = people.setdefault(track, Classifier()).classify(t0, k, vis, cam_moving=moving)
                last_seen[track] = t0
                top = top_action(out["active"])
                msg = {"type": "pose", "from": a.name, "track": track, "level": out["risk"], "confidence": round(conf, 2),
                       "actions": [{"id": x["id"], "level": x["level"], "why": x["why"]} for x in out["active"]],
                       "box": [round(v, 3) for v in box],
                       "keypoints": [[round(x, 3), round(y, 3), round(c, 2)] for (x, y), c in zip(xyn, kc)]}
                if top:
                    msg.update(action=top["id"], why=top["why"])
                send(msg)
                label = f"{top['ko']} ({top['why']})" if top else "정상"
                if last_label.get(track) != label:
                    print(f"#{track} {label}", flush=True)
                    last_label[track] = label
                if view is not None:
                    draw(view, xyn, vis, box, track, out["risk"], top["id"] if top else "ok")

        if boxes and not a.no_detect:
            send({"type": "detect", "from": a.name, "label": "person", "confidence": max(b[4] for b in boxes),
                  "count": len(boxes), "boxes": [b[:5] for b in boxes]})

        for track in [n for n, ts in last_seen.items() if t0 - ts > FORGET_SEC]:
            people.pop(track, None), last_seen.pop(track, None), last_label.pop(track, None)

        if t0 - last_log >= 10:   # 성능 확인용
            print(f"[{runs / (t0 - last_log):.1f} 회/초 추론 · 추적 중 {len(people)}명 · 흐린 프레임 {blurred}장 제외"
                  + (" · 개 이동 중: 돌진·배회 판단 끔" if moving else "") + "]", flush=True)
            last_log, runs, blurred = t0, 0, 0

        if view is not None:
            cv2.imshow("pose_detect", view)
            if cv2.waitKey(1) & 0xFF == ord("q"):
                break
        time.sleep(max(0.0, 1 / a.rate - (time.time() - t0)))   # --rate 보다 자주 돌지 않게 해 Pi 부하를 줄인다


if __name__ == "__main__":
    main()
