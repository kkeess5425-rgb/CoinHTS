"""
web/backend/server.py
=====================
CoinHTS 웹 버전 FastAPI 서버.
- WebSocket: 실시간 틱/오더북/Footprint/신호 스트리밍
- REST API: 과거 캔들, 백테스트, 스캐너, 설정
- CoinHTS 핵심 모듈 재사용 (exchange, indicators, strategy 등)
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
import time
from typing import Optional

import uvicorn
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse

# CoinHTS 모듈 경로 추가
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../.."))

from core.config import AppConfig, get_config
from core.events import EventBus, get_event_bus
from core.models import Candle, Timeframe, Side, Tick, OIData, FundingData
from exchange.okx import OKXExchange
from websocket.okx_feed import OKXWebSocketFeed
from orderflow.footprint import FootprintEngine
from scanner.scanner import MarketScanner, ScannerConfig
from strategy.ict_engine import ICTEngine, ICTParams
from ai.score_engine import AIScoreEngine, ScoreContext
from ai.trade_journal import TradeJournalAI
from whale.tracker import WhaleTracker
from news.news_aggregator import NewsAggregator
from stats.statistics import StatisticsEngine, TradeRecord
from indicators.base_indicators import ema, atr, rsi
import numpy as np

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger(__name__)

# ── 앱 초기화 ──────────────────────────────────────────
app    = FastAPI(title="CoinHTS Web API", version="1.0.0")
config = get_config()
bus    = get_event_bus()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── 전역 상태 ─────────────────────────────────────────
SYMBOLS = config.default_symbols or ["BTC-USDT-SWAP", "ETH-USDT-SWAP"]

exchange = OKXExchange()
feed     = OKXWebSocketFeed(SYMBOLS, event_bus=bus, depth=50)
scanner  = MarketScanner(SYMBOLS, ScannerConfig(), event_bus=bus)
ict      = ICTEngine(ICTParams())
score_eng     = AIScoreEngine()
journal_ai    = TradeJournalAI()
whale_tracker = WhaleTracker()
news_agg      = NewsAggregator()
stats_eng     = StatisticsEngine()
from ai.chart_summary import AIChartSummaryEngine
chart_summary_engine = AIChartSummaryEngine()

fp_engines: dict[str, FootprintEngine] = {
    sym: FootprintEngine(sym, Timeframe.M1, tick_size=0.5 if "BTC" in sym else 0.01)
    for sym in SYMBOLS
}

# WebSocket 구독자 관리
ws_clients: list[WebSocket] = []

# 최근 데이터 캐시
_candle_cache:  dict[str, list[Candle]] = {}
_latest_prices: dict[str, float]        = {}
_latest_oi:     dict[str, float]        = {}
_latest_funding:dict[str, float]        = {}
_signal_log:    list[dict]              = []
_scanner_log:   list[dict]              = []


# ── EventBus 핸들러 ───────────────────────────────────
async def broadcast(event_type: str, data: dict) -> None:
    """모든 WebSocket 클라이언트에 브로드캐스트."""
    msg = json.dumps({"type": event_type, "data": data})
    dead = []
    for ws in ws_clients:
        try:
            await ws.send_text(msg)
        except Exception:
            dead.append(ws)
    for ws in dead:
        ws_clients.remove(ws)


async def on_tick(tick: Tick) -> None:
    _latest_prices[tick.symbol] = tick.price
    fp = fp_engines.get(tick.symbol)
    if fp:
        fp.on_tick(tick)
    scanner.on_tick(tick)
    await broadcast("tick", {
        "symbol": tick.symbol,
        "price":  tick.price,
        "size":   tick.size,
        "side":   tick.side.value,
        "ts":     tick.ts,
    })


async def on_orderbook(book) -> None:
    await broadcast("orderbook", {
        "symbol": book.symbol,
        "bids":   [[b.price, b.size] for b in book.bids[:20]],
        "asks":   [[a.price, a.size] for a in book.asks[:20]],
        "ts":     book.ts,
    })


async def on_scanner_signal(sig) -> None:
    data = {
        "symbol":      sig.symbol,
        "signal_type": sig.signal_type,
        "value":       sig.value,
        "message":     sig.message,
        "ts":          sig.ts,
    }
    _scanner_log.insert(0, data)
    if len(_scanner_log) > 200:
        _scanner_log.pop()
    await broadcast("scanner_signal", data)


async def on_strategy_signal(sig) -> None:
    data = {
        "symbol":    sig.symbol,
        "direction": sig.direction,
        "score":     sig.score,
        "entry":     sig.entry,
        "sl":        sig.sl,
        "tp":        sig.tp,
        "rr":        sig.rr,
        "reasons":   sig.reasons,
        "ts":        sig.ts,
    }
    _signal_log.insert(0, data)
    if len(_signal_log) > 100:
        _signal_log.pop()
    await broadcast("strategy_signal", data)


bus.subscribe("tick",            on_tick)
bus.subscribe("orderbook",       on_orderbook)
bus.subscribe("scanner_signal",  on_scanner_signal)
bus.subscribe("strategy_signal", on_strategy_signal)


# ── 앱 시작/종료 ──────────────────────────────────────
@app.get("/api/whale")
async def get_whale():
    transfers = await whale_tracker.fetch_whale_transfers()
    flows     = await whale_tracker.fetch_exchange_netflow("BTC")
    sentiment = whale_tracker.get_market_sentiment()
    return {
        "transfers":     [{"ts":t.ts,"from_addr":t.from_addr,"to_addr":t.to_addr,
                            "amount":t.amount,"symbol":t.symbol,"usd_value":t.usd_value,"kind":t.kind}
                           for t in transfers],
        "exchange_flows":[{"ts":f.ts,"exchange":f.exchange,"symbol":f.symbol,
                            "netflow":f.netflow,"inflow":f.inflow,"outflow":f.outflow}
                           for f in flows],
        "sentiment":    sentiment,
    }

@app.get("/api/news")
async def get_news():
    events = await news_agg.fetch_economic_calendar()
    news   = await news_agg.fetch_crypto_news()
    return {
        "events":     [{"ts":e.ts,"title":e.title,"currency":e.currency,
                         "impact":e.impact,"forecast":e.forecast,"previous":e.previous,"actual":e.actual}
                        for e in events[:20]],
        "news":       [{"ts":n.ts,"title":n.title,"source":n.source,"tags":n.tags,"sentiment":n.sentiment}
                        for n in news[:10]],
        "ai_summary": news_agg.ai_summarize(news),
    }

@app.get("/api/journal")
async def get_journal():
    return {
        "entries":       [{"id":e.id,"symbol":e.symbol,"direction":e.direction,
                            "entry":e.entry,"exit_price":e.exit_price,"sl":e.sl,"tp":e.tp,
                            "pnl_r":e.pnl_r,"pnl_usd":e.pnl_usd,
                            "entry_ts":e.entry_ts,"exit_ts":e.exit_ts,
                            "entry_reason":e.entry_reason,"exit_reason":e.exit_reason,
                            "mistakes":[{"kind":m.kind,"message":m.message} for m in e.mistakes]}
                           for e in journal_ai.entries[-50:]],
        "mistake_stats": journal_ai.get_mistake_stats(),
    }

@app.get("/api/stats")
async def get_stats(symbol: str = "BTC-USDT-SWAP"):
    records = [
        TradeRecord(entry_ts=float(i)*86400, exit_ts=float(i)*86400+3600,
                    direction="LONG", entry=65000, exit_price=65200,
                    sl=64500, tp=66000, pnl_r=0.4*i-2.0, symbol=symbol)
        for i in range(10)
    ]
    s = stats_eng.compute(records)
    return {
        "total_trades":     s.total_trades,
        "win_rate":         s.win_rate,
        "profit_factor":    s.profit_factor,
        "expectancy":       s.expectancy,
        "sharpe_ratio":     s.sharpe_ratio,
        "sortino_ratio":    s.sortino_ratio,
        "max_drawdown":     s.max_drawdown,
        "max_consec_losses":s.max_consec_losses,
        "avg_hold_minutes": s.avg_hold_minutes,
        "daily_wr":         s.daily_wr,
    }

@app.get("/api/chart-summary")
async def get_chart_summary(symbol: str = "BTC-USDT-SWAP"):
    try:
        candles = _candle_cache.get(symbol, [])
        if not candles:
            candles = await exchange.get_candles(symbol, Timeframe.M15, 200)
        from strategy.ict_engine import ICTEngine, ICTParams
        from strategy.smc_engine import SMCEngine
        ict_r = ICTEngine(ICTParams()).analyze(candles)
        smc_r = SMCEngine().analyze(candles)
        fp    = fp_engines.get(symbol)
        summary = chart_summary_engine.summarize(
            symbol=symbol, candles=candles,
            ict_result=ict_r, smc_result=smc_r,
            fp_bar=fp.current_bar if fp else None,
        )
        return {
            "headline":   summary.headline,
            "trend":      summary.trend,
            "structure":  summary.structure,
            "orderflow":  summary.orderflow,
            "key_levels": summary.key_levels,
            "risk":       summary.risk,
            "watchfor":   summary.watchfor,
            "full_text":  summary.full_text,
        }
    except Exception as e:
        return {"error": str(e), "headline": "분석 실패", "full_text": ""}

@app.get("/api/status")
async def get_status():
    return {
        "symbols":  SYMBOLS,
        "ws_clients":len(ws_clients),
        "signals":   len(_signal_log),
        "scanner":   len(_scanner_log),
    }

@app.on_event("startup")
async def startup() -> None:
    # Numba JIT 워밍업
    p = np.random.randn(50) + 65000.0
    h = p + 10; l = p - 10
    ema(p, 5); atr(h, l, p, 5); rsi(p, 5)
    logger.info("Numba JIT 워밍업 완료")

    # 초기 캔들 로드
    for sym in SYMBOLS:
        try:
            candles = await exchange.get_candles(sym, Timeframe.M15, 300)
            _candle_cache[sym] = candles
            logger.info(f"{sym} 초기 캔들 {len(candles)}봉 로드")
        except Exception as e:
            logger.warning(f"{sym} 캔들 로드 실패: {e}")

    # WebSocket 피드 시작
    asyncio.create_task(feed.start())

    # ICT 분석 루프 시작
    asyncio.create_task(_ict_loop())

    # OI/펀딩 루프
    asyncio.create_task(_market_data_loop())
    logger.info("CoinHTS Web 서버 시작됨")


@app.on_event("shutdown")
async def shutdown() -> None:
    await feed.stop()
    await exchange.close()


# ── ICT 분석 루프 ─────────────────────────────────────
async def _ict_loop() -> None:
    while True:
        # 15분 봉 마감 타이밍에 실행
        now    = time.time()
        period = 900
        wait   = period - (now % period)
        await asyncio.sleep(wait)

        for sym in SYMBOLS:
            try:
                candles = await exchange.get_candles(sym, Timeframe.M15, 300)
                _candle_cache[sym] = candles
                result = ict.analyze(candles)
                if not result.signal:
                    continue

                fp  = fp_engines.get(sym)
                ctx = ScoreContext(
                    ict_result=result,
                    fp_bar=    fp.current_bar if fp else None,
                    cur_price= candles[-1].close if candles else 0,
                )
                score = score_eng.score(ctx, result.signal)

                from core.models import StrategySignal, Exchange
                sig = StrategySignal(
                    symbol=result.signal and sym or sym,
                    ts=time.time(), direction=result.signal,
                    score=score.total, entry=result.entry,
                    sl=result.sl, tp=result.tp,
                    reasons=result.reasons + score.reasons,
                    exchange=Exchange.OKX,
                )
                await bus.publish("strategy_signal", sig)
            except Exception as e:
                logger.error(f"ICT 루프 오류 {sym}: {e}")


async def _market_data_loop() -> None:
    while True:
        for sym in SYMBOLS:
            try:
                oi      = await exchange.get_oi(sym)
                funding = await exchange.get_funding(sym)
                _latest_oi[sym]      = oi.oi_ccy
                _latest_funding[sym] = funding.funding_rate
                scanner.on_oi(oi)
                scanner.on_funding(funding)
                await broadcast("market_data", {
                    "symbol":       sym,
                    "oi":           oi.oi_ccy,
                    "funding_rate": funding.funding_rate,
                })
            except Exception as e:
                logger.debug(f"시장 데이터 오류 {sym}: {e}")
        await asyncio.sleep(60)


# ── REST API ──────────────────────────────────────────
@app.get("/api/symbols")
async def get_symbols():
    return {"symbols": SYMBOLS}


@app.get("/api/candles")
async def get_candles(
    symbol:    str = Query(default="BTC-USDT-SWAP"),
    timeframe: str = Query(default="15m"),
    limit:     int = Query(default=300),
    before:    Optional[int] = Query(default=None),
):
    tf_map = {
        "1m": Timeframe.M1, "3m": Timeframe.M3, "5m": Timeframe.M5,
        "15m": Timeframe.M15, "1H": Timeframe.H1, "4H": Timeframe.H4,
    }
    tf = tf_map.get(timeframe, Timeframe.M15)
    try:
        candles = await exchange.get_candles(symbol, tf, limit, before)
    except Exception as e:
        return {"error": str(e), "candles": []}

    # 지표 계산
    if candles:
        closes = np.array([c.close for c in candles])
        highs  = np.array([c.high  for c in candles])
        lows   = np.array([c.low   for c in candles])
        e20 = ema(closes, 20).tolist() if len(closes) >= 20 else []
        e50 = ema(closes, 50).tolist() if len(closes) >= 50 else []
        a14 = atr(highs, lows, closes, 14).tolist() if len(closes) >= 14 else []
        r14 = rsi(closes, 14).tolist() if len(closes) >= 14 else []
    else:
        e20 = e50 = a14 = r14 = []

    return {
        "symbol": symbol,
        "timeframe": timeframe,
        "candles": [
            {"t": c.ts, "o": c.open, "h": c.high, "l": c.low, "c": c.close, "v": c.volume}
            for c in candles
        ],
        "ema20": e20,
        "ema50": e50,
        "atr14": a14,
        "rsi14": r14,
    }


@app.get("/api/signals")
async def get_signals(limit: int = Query(default=50)):
    return {"signals": _signal_log[:limit]}


@app.get("/api/scanner")
async def get_scanner(limit: int = Query(default=100)):
    return {"signals": _scanner_log[:limit]}


@app.get("/api/market")
async def get_market():
    return {
        sym: {
            "price":   _latest_prices.get(sym, 0),
            "oi":      _latest_oi.get(sym, 0),
            "funding": _latest_funding.get(sym, 0),
        }
        for sym in SYMBOLS
    }


@app.get("/api/footprint/{symbol}")
async def get_footprint(symbol: str, limit: int = Query(default=20)):
    fp = fp_engines.get(symbol)
    if not fp:
        return {"bars": []}
    bars = fp.bars[-limit:] + ([fp.current_bar] if fp.current_bar else [])
    return {
        "bars": [
            {
                "ts":    b.candle.ts,
                "open":  b.candle.open,
                "high":  b.candle.high,
                "low":   b.candle.low,
                "close": b.candle.close,
                "vol":   b.candle.volume,
                "delta": b.delta,
                "cvd":   b.cvd,
                "poc":   b.poc,
                "cells": [
                    {"p": c.price, "bv": c.buy_vol, "sv": c.sell_vol}
                    for c in b.cells
                ],
            }
            for b in bars
        ]
    }


@app.get("/api/backtest")
async def backtest(
    symbol:    str = Query(default="BTC-USDT-SWAP"),
    timeframe: str = Query(default="15m"),
    limit:     int = Query(default=500),
):
    tf_map = {"1m":Timeframe.M1,"15m":Timeframe.M15,"1H":Timeframe.H1}
    tf = tf_map.get(timeframe, Timeframe.M15)
    try:
        candles = await exchange.get_candles_paged(symbol, tf, total=limit)
    except Exception as e:
        return {"error": str(e)}

    if len(candles) < 100:
        return {"error": f"데이터 부족 ({len(candles)}봉)"}

    params = ICTParams(require_displacement=True, min_confluence=1, min_rr=2.0)
    engine = ICTEngine(params)

    trades, open_pos = [], []
    cum_r = 0.0
    wins = losses = 0
    warmup = 120

    for i in range(warmup, len(candles)):
        window = candles[:i+1]
        bh = window[-1].high
        bl = window[-1].low
        bt = window[-1].ts

        still_open = []
        for pos in open_pos:
            hit_sl = (pos["dir"]=="LONG" and bl<=pos["sl"]) or (pos["dir"]=="SHORT" and bh>=pos["sl"])
            hit_tp = (pos["dir"]=="LONG" and bh>=pos["tp"]) or (pos["dir"]=="SHORT" and bl<=pos["tp"])
            if hit_sl or hit_tp:
                result = "WIN" if hit_tp and not hit_sl else "LOSS"
                r = pos["rr"] if result=="WIN" else -1.0
                cum_r += r
                wins += 1 if result=="WIN" else 0
                losses += 0 if result=="WIN" else 1
                trades.append({**pos, "result":result, "r":r, "exit_ts":bt})
            else:
                still_open.append(pos)
        open_pos = still_open

        try:
            res = engine.analyze(window)
        except Exception:
            continue

        if not res.signal or any(p["dir"]==res.signal for p in open_pos):
            continue
        if res.entry and res.sl and res.tp:
            open_pos.append({
                "dir": res.signal, "entry": res.entry,
                "sl": res.sl, "tp": res.tp,
                "rr": res.rr or 2.0,
                "score": res.score, "ts": bt,
                "result": "OPEN", "r": 0,
            })

    for pos in open_pos:
        trades.append({**pos, "result":"OPEN"})

    closed  = [t for t in trades if t["result"] in ("WIN","LOSS")]
    wr      = wins/max(len(closed),1)*100
    avg_r   = cum_r/max(len(closed),1)

    return {
        "summary": {
            "total": len(trades), "closed": len(closed),
            "wins": wins, "losses": losses,
            "win_rate": round(wr,1), "cum_r": round(cum_r,2),
            "avg_r": round(avg_r,3),
        },
        "trades": trades[-50:],
    }


# ── WebSocket 엔드포인트 ───────────────────────────────
@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket):
    await ws.accept()
    ws_clients.append(ws)
    logger.info(f"WebSocket 연결: 총 {len(ws_clients)}명")

    # 연결 즉시 현재 시장 데이터 전송
    await ws.send_text(json.dumps({
        "type": "init",
        "data": {
            "symbols": SYMBOLS,
            "prices":  _latest_prices,
        }
    }))

    try:
        while True:
            # 클라이언트 메시지 수신 (심볼 변경 등)
            msg = await ws.receive_text()
            try:
                data = json.loads(msg)
                if data.get("type") == "ping":
                    await ws.send_text(json.dumps({"type": "pong"}))
            except Exception:
                pass
    except WebSocketDisconnect:
        ws_clients.remove(ws)
        logger.info(f"WebSocket 해제: 총 {len(ws_clients)}명")


# ── 프론트엔드 서빙 ───────────────────────────────────
frontend_dist = os.path.join(os.path.dirname(__file__), "../frontend/dist")
if os.path.exists(frontend_dist):
    app.mount("/", StaticFiles(directory=frontend_dist, html=True), name="frontend")
else:
    @app.get("/")
    async def root():
        return {"message": "CoinHTS Web API", "docs": "/docs", "ws": "/ws"}


if __name__ == "__main__":
    uvicorn.run(
        "server:app",
        host="0.0.0.0",
        port=int(os.environ.get("PORT", 8000)),
        reload=False,
        log_level="info",
    )
