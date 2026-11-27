# web/

MechDog 순찰 관제 대시보드 (담당: 이용석)

- `index.html` — 대시보드 화면 (1안 관제실형, `mockups/design1.html`을 실제 데이터에 연결)
- `app.js` — WebSocket(`/ws`)과 `/api/cameras`로 받은 데이터를 화면에 그림. 사이렌(#6)은 CONFIRM·CONTAIN·HANDOVER에서 울림
- `style.css` — 스타일
- `mockups/` — 가짜 데이터로 움직이는 시안 (`DESIGN_LOG.md`에 결정 기록)
- `arena.json` — 아레나 크기, 출구, 장애물, 구역, LiDAR 위치

로컬 실행: 저장소 루트에서 `uvicorn server.app:app --port 8000` 후 http://localhost:8000

## 기본 화면 (지난 탐사 지도)
실제 데이터(SLAM 지도·LiDAR 스캔·로봇견 위치)가 하나도 안 들어왔을 때만 `sample_map.json`을 보여 줘요 (`arena.json`의 `"sample"`, 지우면 안 보임). 탐사를 끝내고 **허브를 끄기 전에** 아래를 실행하면 그때의 SLAM 지도, 초음파 장애물(빨간 점), 로봇견 마지막 위치가 다음부터 기본 화면이 돼요. 이전 파일은 `sample_map.prev.json`으로 남아요.
```bash
python tools/snapshot_map.py            # 허브 127.0.0.1 에서 읽어 저장 (--no-dogs: 위치 빼기, --dry-run: 미리 보기)
```
