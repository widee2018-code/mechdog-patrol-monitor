# web/

MechDog 순찰 관제 대시보드 (담당: 이용석)

- `index.html` — 대시보드 화면
- `app.js` — WebSocket(`/ws`)으로 텔레메트리를 받아 화면 갱신
- `style.css` — 스타일
- `arena.json` — 아레나 크기, 출구, 장애물, 구역, LiDAR 위치

로컬 실행: 저장소 루트에서 `uvicorn server.app:app --port 8000` 후 http://localhost:8000
