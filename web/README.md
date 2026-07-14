# CoinHTS Web

CoinHTS 웹 버전 — React + FastAPI + WebSocket

## 실행 방법

### 백엔드
```bash
cd CoinHTS
pip install -r web/requirements.txt
pip install -r requirements.txt
python web/backend/server.py
```

### 프론트엔드 개발 모드
```bash
cd web/frontend
npm install
npm run dev
# http://localhost:5173 접속
```

### 프론트엔드 빌드 (배포용)
```bash
cd web/frontend
npm run build
# dist/ 폴더 생성 → FastAPI가 자동 서빙
```

## 배포 (Render)

1. `web/backend/server.py`를 시작 커맨드로 설정
2. 환경변수: `OKX_API_KEY`, `OKX_API_SECRET`, `OKX_PASSPHRASE`
3. 프론트엔드 빌드 후 `dist/` 폴더를 커밋에 포함

## 기능

| 기능 | 설명 |
|------|------|
| 실시간 캔들차트 | lightweight-charts, EMA20/50 오버레이 |
| 호가창 | 매수/매도 잔량 히스토그램 |
| Time & Sales | 실시간 체결 + Whale 하이라이트 |
| 스캐너 | 볼륨/OI/펀딩/Delta/Sweep 신호 |
| ICT 신호 로그 | BOS/FVG/OB/OTE 기반 AI 100점 신호 |
| 과거차트 | 왼쪽 스크롤 시 자동 과거 데이터 로드 |
