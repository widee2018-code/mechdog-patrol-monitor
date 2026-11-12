# 텔레메트리 허브(대시보드 + WebSocket + UDP 수신). LiDAR·brain 처리는 Pi에서 직접 실행한다.
FROM python:3.12-slim
ENV PYTHONUNBUFFERED=1
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY server/ server/
COPY web/ web/
EXPOSE 8000 9000/udp
CMD ["uvicorn", "server.app:app", "--host", "0.0.0.0", "--port", "8000", "--timeout-graceful-shutdown", "2"]
