"""사람 인식 (YOLO11n): 카메라 영상에서 사람을 찾아 모니터 서버로 detect 메시지를 보낸다.

사람이 보이는 프레임마다 {"type": "detect", "from": "A", "label": "person", "confidence": 0.82, "boxes": [...]} 를 UDP 9000 으로 보낸다
(docs/message_schema.md). 서버는 새로 발견할 때만 타임라인에 남기고, 탐사(EXPLORE) 중이면 SUSPECT 로 바꿔 사이렌을 울린다.

카메라는 ESP32 에 직접 붙지 말고 모니터 서버가 다시 내보내는 스트림을 읽는다 (ESP32 는 스트림을 한 곳에만 보낼 수 있음).

노트북 웹캠으로 연습 (메시지는 터미널에만 표시):
    python brain/person_detect.py --camera 0 --show

Pi 에서 (모니터 서버가 CAMERAS="A=http://<카메라-IP>:81/stream" 으로 떠 있을 때):
    python brain/person_detect.py --camera http://<서버-IP>:8000/api/camera/A/stream --monitor <서버-IP>

Pi 에서는 NCNN 으로 바꾼 모델이 몇 배 빠르다 (처음 한 번):
    yolo export model=yolo11n.pt format=ncnn imgsz=320
    python brain/person_detect.py --model yolo11n_ncnn_model ...
"""
import argparse
import json
import socket
import time

import cv2
from ultralytics import YOLO

from marker_follow import LatestFrame, MONITOR_PORT

PERSON = 0   # COCO 클래스 번호


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="YOLO11n 사람 인식 → detect 메시지")
    p.add_argument("--camera", default="0", help="웹캠 번호(0), 영상 파일, 또는 MJPEG 스트림 URL")
    p.add_argument("--name", default="A", help="어느 개의 카메라인지 (메시지의 from)")
    p.add_argument("--monitor", help="모니터 서버 IP. 없으면 메시지를 터미널에만 표시")
    p.add_argument("--model", default="yolo11n.pt", help="yolo11n.pt 또는 NCNN 으로 바꾼 폴더 (yolo11n_ncnn_model)")
    p.add_argument("--imgsz", type=int, default=320, help="추론 입력 크기. 작을수록 빠르고 먼 사람을 놓친다")
    p.add_argument("--conf", type=float, default=0.5, help="이보다 신뢰도가 낮은 사람은 무시")
    p.add_argument("--rate", type=float, default=5.0, help="최대 추론 횟수 (Hz). Pi 가 뜨거워지면 낮춘다")
    p.add_argument("--show", action="store_true", help="카메라 화면 창 띄우기 (q: 종료)")
    return p.parse_args()


def main() -> None:
    a = parse_args()
    model = YOLO(a.model, task="detect")
    cam = LatestFrame(a.camera)
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    print(f"카메라 {a.camera} → " + (f"{a.monitor}:{MONITOR_PORT}" if a.monitor else "터미널만 (--monitor 없음)"))

    seen, last_new, last_log, runs = 0, time.time(), time.time(), 0
    while True:
        t0 = time.time()
        seq, frame = cam.get()
        if seq == seen:
            if t0 - last_new > 3:   # 영상이 멈췄으면 이유를 가끔 알려 준다
                print("영상 없음:", cam.error or "기다리는 중")
                last_new = t0
            time.sleep(0.02)
            continue
        seen, last_new = seq, t0

        boxes = model.predict(frame, imgsz=a.imgsz, conf=a.conf, classes=[PERSON], verbose=False)[0].boxes
        runs += 1
        if len(boxes):
            conf = float(boxes.conf.max())
            # 박스는 화면 비율 0~1 [x1, y1, x2, y2, 신뢰도]: 대시보드가 영상 위에 그린다
            box_list = [[round(v, 3) for v in xyxy] + [round(c, 2)] for xyxy, c in zip(boxes.xyxyn.tolist(), boxes.conf.tolist())]
            msg = {"type": "detect", "from": a.name, "label": "person", "confidence": round(conf, 2), "count": len(boxes),
                   "boxes": box_list}
            if a.monitor:
                sock.sendto(json.dumps(msg).encode(), (a.monitor, MONITOR_PORT))
            print(f"사람 {len(boxes)}명 · 신뢰도 {conf:.2f}")

        if t0 - last_log >= 10:   # 성능 확인용
            print(f"[{runs / (t0 - last_log):.1f} 회/초 추론]")
            last_log, runs = t0, 0

        if a.show:
            view = frame.copy()
            for (x1, y1, x2, y2), c in zip(boxes.xyxy.int().tolist(), boxes.conf.tolist()):
                cv2.rectangle(view, (x1, y1), (x2, y2), (0, 200, 0), 2)
                cv2.putText(view, f"person {c:.2f}", (x1, max(y1 - 6, 14)), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 200, 0), 2)
            cv2.imshow("person_detect", view)
            if cv2.waitKey(1) & 0xFF == ord("q"):
                break
        time.sleep(max(0.0, 1 / a.rate - (time.time() - t0)))   # --rate 보다 자주 돌지 않게 해 Pi 부하를 줄인다


if __name__ == "__main__":
    main()
