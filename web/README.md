# web/

MechDog 순찰 관제 대시보드 (담당: 이용석)

- `index.html` — 대시보드 화면 (1안 관제실형, `mockups/design1.html`을 실제 데이터에 연결)
- `app.js` — WebSocket(`/ws`)과 `/api/cameras`로 받은 데이터를 화면에 그림. 사이렌(#6)은 CONFIRM·CONTAIN·HANDOVER에서 울림
- `style.css` — 스타일
- `mockups/` — 가짜 데이터로 움직이는 시안 (`DESIGN_LOG.md`에 결정 기록)
- `arena.json` — 아레나 크기, 출구, 장애물, 구역, LiDAR 위치

로컬 실행: 저장소 루트에서 `uvicorn server.app:app --port 8000` 후 http://localhost:8000
