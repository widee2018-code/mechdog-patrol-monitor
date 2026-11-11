"""출력용 ArUco 마커 A4 시트를 만든다 (DICT_4X4_50, marker_follow.py와 같은 사전).

    python brain/make_markers.py                        # ID 1, 2를 7 cm로 → markers.png
    python brain/make_markers.py --ids 1 2 3 4 --cm 6
    python brain/make_markers.py --ids 10 11 12 13 --out intruder_cube.png

반드시 "실제 크기(100%)"로 출력하고, 출력된 검은 사각형 한 변을 자로 재서
marker_follow.py의 --marker-cm 에 넣는다.
"""
import argparse

import cv2
import numpy as np

DPI = 300
A4_PX = (int(21.0 / 2.54 * DPI), int(29.7 / 2.54 * DPI))   # (가로, 세로)

p = argparse.ArgumentParser()
p.add_argument("--ids", type=int, nargs="+", default=[1, 2])
p.add_argument("--cm", type=float, default=7.0, help="검은 사각형 한 변 길이 (cm)")
p.add_argument("--out", default="markers.png")
a = p.parse_args()

side = int(a.cm / 2.54 * DPI)
quiet = side // 4                       # 마커 둘레 흰 여백: 이게 없으면 인식이 잘 안 된다
cell_w, cell_h = side + 2 * quiet, side + 2 * quiet + 60
cols = max(1, A4_PX[0] // cell_w)
rows = max(1, A4_PX[1] // cell_h)
if len(a.ids) > cols * rows:
    raise SystemExit(f"A4 한 장에 {cols * rows}개까지 들어갑니다. --cm 을 줄이거나 나눠서 만드세요.")

dictionary = cv2.aruco.getPredefinedDictionary(cv2.aruco.DICT_4X4_50)
sheet = np.full((A4_PX[1], A4_PX[0]), 255, np.uint8)
for n, marker_id in enumerate(a.ids):
    r, c = divmod(n, cols)
    x, y = c * cell_w + quiet, r * cell_h + quiet
    sheet[y:y + side, x:x + side] = cv2.aruco.generateImageMarker(dictionary, marker_id, side)
    cv2.putText(sheet, f"ID {marker_id}  ({a.cm:g} cm)", (x, y + side + 50),
                cv2.FONT_HERSHEY_SIMPLEX, 1.2, 0, 2)

cv2.imwrite(a.out, sheet)
print(f"{a.out} 저장: ID {a.ids}, 한 변 {a.cm:g} cm. 실제 크기(100%)로 출력하세요.")
