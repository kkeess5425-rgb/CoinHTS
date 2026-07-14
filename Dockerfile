FROM python:3.13-slim

# 빌드 의존성
RUN apt-get update && apt-get install -y \
    build-essential curl nodejs npm \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Python 의존성 (UI 제외 — 서버 전용)
COPY requirements.txt .
RUN pip install --no-cache-dir \
    aiohttp aiosqlite asyncpg websockets \
    fastapi "uvicorn[standard]" \
    numpy msgspec numba scipy feedparser \
    pytest

# 프론트엔드 빌드
COPY web/frontend/package.json web/frontend/
RUN cd web/frontend && npm install

COPY web/frontend/ web/frontend/
RUN cd web/frontend && npm run build

# 소스 복사
COPY . .

# 환경변수
ENV PORT=8000
ENV PYTHONUNBUFFERED=1

EXPOSE 8000

CMD ["python", "web/backend/server.py"]
