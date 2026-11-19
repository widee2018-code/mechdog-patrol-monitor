"""몸 점 17개(YOLO11n-pose, COCO 순서)로 위험 행동 9가지를 판정하는 규칙.

web/mockups/cam_mock.js 의 Classifier 를 그대로 옮긴 것이다. 숫자(기울기 55°, 속도 1.2 등)를 바꾸면 그쪽도 같이 바꾼다.
규칙 설명은 web/mockups/danger_actions.html.

좌표는 화면 높이 단위다: y 는 0~1, x 는 (픽셀 x / 화면 높이). 가로세로 비율이 달라도 각도가 틀어지지 않게 하려는 것이다.
cam_mock.js 의 가짜 데이터는 0~1 정사각형이라 그대로 넣으면 된다.
"""
import math

# id, 한국어 이름, 위험도, 유지 시간(초). 순서는 danger_actions.html 과 같다.
DANGERS = [
    ("fall", "넘어짐 · 쓰러짐", "high", 0),
    ("still", "쓰러진 채 움직임 없음", "high", 3),
    ("strike", "팔 휘두름 · 때리려는 자세", "high", 0),
    ("kick", "발차기", "high", 0),
    ("rush", "돌진 · 빠르게 다가옴", "mid", 0),
    ("reach", "로봇에 손 뻗음", "mid", 1),
    ("crouch", "웅크림 · 숨기", "mid", 2),
    ("handsup", "양손 들기 (항복 · 도움 요청)", "low", 1.5),
    ("loiter", "배회 · 서성임", "low", 0),
]
RANK = {"none": 0, "low": 1, "mid": 2, "high": 3}
MOMENTARY = {"fall", "strike", "kick", "rush"}   # 순간 동작: 시작부터 1.5초 동안 표시를 유지
# 규칙마다 꼭 보여야 하는 몸 점. 카메라가 낮아 다리만 보일 때 보이지 않는 점으로 잘못 판정하지 않게 한다
DANGER_KPS = {"fall": [5, 6, 11, 12], "still": [5, 6, 11, 12], "strike": [5, 6, 9, 10], "kick": [13, 14, 15, 16],
              "rush": [5, 6, 15, 16], "reach": [5, 6, 9, 10], "crouch": [11, 12, 13, 14, 15, 16], "handsup": [0, 9, 10],
              "loiter": [11, 12]}


def mid(a, b):
    return ((a[0] + b[0]) / 2, (a[1] + b[1]) / 2)


def dist(a, b):
    return math.hypot(a[0] - b[0], a[1] - b[1])


def angle_at(a, b, c):
    """b 에서의 각도 (도)."""
    v1, v2 = (a[0] - b[0], a[1] - b[1]), (c[0] - b[0], c[1] - b[1])
    d = math.hypot(*v1) * math.hypot(*v2) or 1
    return math.degrees(math.acos(max(-1, min(1, (v1[0] * v2[0] + v1[1] * v2[1]) / d))))


def box_of(k):
    xs, ys = [p[0] for p in k], [p[1] for p in k]
    x0, y0, x1, y1 = min(xs), min(ys), max(xs), max(ys)
    pad_y, pad_x = (y1 - y0) * 0.12 + 0.02, (x1 - x0) * 0.15 + 0.02
    return [max(0, x0 - pad_x), max(0, y0 - pad_y), x1 + pad_x, min(1, y1 + 0.02)]


def features(k):
    sh, hp = mid(k[5], k[6]), mid(k[11], k[12])
    box = box_of(k)
    return {"torso": math.degrees(math.atan2(abs(sh[0] - hp[0]), hp[1] - sh[1])),   # 0 = 똑바로 섬
            "hipY": hp[1], "hipX": hp[0], "box": box,
            "body": dist(sh, mid(k[15], k[16])),   # 어깨~발목 길이: 팔을 들어도 안 변하는 몸 크기
            "h": box[3] - box[1], "w": box[2] - box[0],
            "kneeL": angle_at(k[11], k[13], k[15]), "kneeR": angle_at(k[12], k[14], k[16])}


class Classifier:
    """한 사람(추적 번호 하나)의 위험 행동 판정. 프레임마다 classify(t, k) 를 부른다."""

    def __init__(self):
        self.hist = []   # {t, k, f, hit, swing}
        self.since = {}
        self.last_dir = 0
        self.dir_changes = []

    def classify(self, t, k, vis=None):
        """t: 초, k: 몸 점 17개 [(x, y)], vis: 몸 점마다 보이는지 (없으면 모두 보임).
        반환: {"active": [{id, ko, level, why, held}], "risk": 가장 높은 위험도, "f": 몸 특징, "m": 규칙에 쓴 숫자}"""
        f = features(k)
        hist = self.hist
        hist.append({"t": t, "k": k, "f": f})
        while hist and t - hist[0]["t"] > 20:
            hist.pop(0)

        def ago(sec):
            for h in reversed(hist):
                if t - h["t"] >= sec:
                    return h
            return hist[0]

        p1, p03 = ago(1), ago(0.25)
        dt = max(1e-3, t - p03["t"])
        H = max(0.1, f["body"])
        upright = f["torso"] < 40 and p1["f"]["torso"] < 40   # 서 있는 동안만 판단하는 동작용

        def speed(i):   # 초당 키의 몇 배
            return dist(k[i], p03["k"][i]) / dt / H

        motion = sum(speed(i) for i in (5, 6, 9, 10, 11, 12, 15, 16)) / 8
        raw, why = {}, {}

        raw["fall"] = f["torso"] > 55 and p1["f"]["torso"] < 40 and (f["hipY"] - p1["f"]["hipY"]) > 0.25 * p1["f"]["h"]
        why["fall"] = f"몸 기울기 {f['torso']:.0f}° · 엉덩이 {(f['hipY'] - p1['f']['hipY']) / max(0.05, p1['f']['h']) * 100:.0f}% 내려감"
        raw["still"] = f["torso"] > 55 and motion < 0.15
        why["still"] = f"몸 기울기 {f['torso']:.0f}° · 움직임 {motion:.2f}"

        def wrist_up(w, s):
            return k[w][1] < k[s][1]

        fast_up = upright and ((wrist_up(9, 5) and speed(9) > 1.2) or (wrist_up(10, 6) and speed(10) > 1.2))
        prev_hit = len(hist) > 1 and hist[-2].get("hit", False)
        hist[-1]["hit"] = fast_up
        hist[-1]["swing"] = fast_up and not prev_hit   # 휘두름 한 번이 시작된 순간
        hits = sum(1 for h in hist if t - h["t"] < 2 and h.get("swing"))
        raw["strike"] = fast_up and hits >= 2
        why["strike"] = f"손목 속도 {max(speed(9), speed(10)):.1f} · 2초 안 {hits}번"
        raw["kick"] = upright and ((k[16][1] < k[13][1] and speed(16) > 0.8) or (k[15][1] < k[14][1] and speed(15) > 0.8))
        why["kick"] = f"발목 높이 > 무릎 · 발목 속도 {max(speed(15), speed(16)):.1f}"
        grow = (f["body"] - p1["f"]["body"]) / max(0.05, p1["f"]["body"])
        legs_straight = p1["f"]["kneeL"] > 140 and p1["f"]["kneeR"] > 140   # 웅크렸다 일어서는 것과 구분
        raw["rush"] = grow > 0.25 and upright and legs_straight
        why["rush"] = f"몸 크기 1초에 {grow * 100:.0f}% 커짐"

        def arm_out(s, w):
            return abs(k[w][0] - k[s][0]) / H

        raw["reach"] = f["h"] > 0.75 and max(arm_out(5, 9), arm_out(6, 10)) > 0.22
        why["reach"] = f"몸 높이 {f['h'] * 100:.0f}% · 팔 뻗음 {max(arm_out(5, 9), arm_out(6, 10)):.2f}"
        raw["crouch"] = f["kneeL"] < 110 and f["kneeR"] < 110 and f["torso"] < 35 and motion < 0.6
        why["crouch"] = f"무릎 {f['kneeL']:.0f}° / {f['kneeR']:.0f}°"
        raw["handsup"] = k[9][1] < k[0][1] and k[10][1] < k[0][1]
        why["handsup"] = "양 손목이 코보다 위"
        # 배회: 가로 방향이 바뀐 횟수. 팔다리 흔들림이 아닌 골반 위치로 걷는 방향을 본다
        vx = f["hipX"] - p1["f"]["hipX"]
        d = 0 if abs(vx) < 0.02 else (1 if vx > 0 else -1)
        if d and self.last_dir and d != self.last_dir:
            self.dir_changes.append(t)
        if d:
            self.last_dir = d
        self.dir_changes = [x for x in self.dir_changes if t - x < 20]
        walking = abs(vx) > 0.03 and upright   # 지금도 옆으로 걷는 중일 때만
        raw["loiter"] = len(self.dir_changes) >= 3 and walking
        why["loiter"] = f"20초 안 방향 바꿈 {len(self.dir_changes)}번"

        if vis is not None:
            for i, need in DANGER_KPS.items():
                raw[i] = raw[i] and all(vis[j] for j in need)

        active = []
        since = self.since
        for i, ko, level, hold in DANGERS:
            if raw[i]:
                since.setdefault(i, t)
            elif i not in MOMENTARY:
                since.pop(i, None)
            if i in MOMENTARY and i in since and not raw[i] and t - since[i] > 1.5:
                del since[i]
            held = t - since[i] if i in since else -1
            if held >= 0 and held >= hold:
                active.append({"id": i, "ko": ko, "level": level, "why": why[i], "held": held})
            elif held >= 0:
                active.append({"id": i, "ko": ko, "level": "pending", "why": f"{why[i]} · {held:.1f}/{hold}초", "held": held})
        real = [a for a in active if a["level"] != "pending"]
        if any(a["id"] == "still" for a in real):   # 쓰러진 채 움직임 없음이 뜨면 넘어짐은 내린다
            active = [a for a in active if a["id"] != "fall"]
            real = [a for a in real if a["id"] != "fall"]
        risk = max((a["level"] for a in real), key=RANK.get, default="none")
        m = {"torso": f["torso"], "knee": min(f["kneeL"], f["kneeR"]), "wrist": max(speed(9), speed(10)),
             "ankle": max(speed(15), speed(16)), "grow": grow * 100, "size": f["h"] * 100,
             "arm": max(arm_out(5, 9), arm_out(6, 10)), "motion": motion, "turns": len(self.dir_changes)}
        return {"active": active, "risk": risk, "f": f, "m": m}
