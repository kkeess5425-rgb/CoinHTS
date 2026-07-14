"""
core/app.py
===========
CoinHTS 애플리케이션 오케스트레이터.

데이터 흐름:
  OKX WebSocket
      ↓ tick
  FootprintEngine  →  EventBus("footprint")
      ↓ bar_close
  MarketScanner    →  EventBus("scanner_signal")
  ICTEngine (15분봉 주기) → EventBus("strategy_signal")
  AIScoreEngine    →  점수 부여
  AlertManager     →  Telegram / Discord
  DataStorage      →  SQLite 저장

모든 모듈은 EventBus를 통해 통신하므로 서로 직접 참조하지 않는다.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Optional

from core.config import AppConfig, get_config
from core.events import EventBus, get_event_bus
from core.models import Candle, Timeframe, Side, Tick
from exchange.okx import OKXExchange
from websocket.okx_feed import OKXWebSocketFeed
from orderflow.footprint import FootprintEngine
from scanner.scanner import MarketScanner, ScannerConfig
from strategy.ict_engine import ICTEngine, ICTParams
from ai.score_engine import AIScoreEngine, ScoreContext
from alert.alert_manager import AlertManager
from database.storage import DataStorage
from risk.risk_manager import RiskManager, RiskParams

logger = logging.getLogger(__name__)


class CoinHTSApp:
    """
    전체 애플리케이션 오케스트레이터.
    모든 모듈을 초기화하고 EventBus로 연결한다.
    """

    def __init__(self, config: Optional[AppConfig] = None) -> None:
        self.config  = config or get_config()
        self.bus     = get_event_bus()
        self._running = False

        symbols = self.config.default_symbols

        # ── 모듈 초기화 ────────────────────────────
        self.exchange = OKXExchange(
            api_key=    self.config.exchange.api_key,
            api_secret= self.config.exchange.api_secret,
            passphrase= self.config.exchange.passphrase,
        )

        self.feed = OKXWebSocketFeed(
            symbols=   symbols,
            event_bus= self.bus,
            depth=     50,
        )

        # 심볼별 Footprint 엔진
        self.footprint_engines: dict[str, FootprintEngine] = {
            sym: FootprintEngine(
                symbol=    sym,
                timeframe= Timeframe.M1,
                tick_size= self.config.default_tick_size(sym),
                event_bus= self.bus,
            )
            for sym in symbols
        }

        self.scanner = MarketScanner(
            symbols=   symbols,
            config=    ScannerConfig(),
            event_bus= self.bus,
        )

        self.ict_engine   = ICTEngine(ICTParams())
        self.score_engine = AIScoreEngine()

        self.alert = AlertManager(
            telegram_token=   self.config.alert.telegram_token,
            telegram_chat_id= self.config.alert.telegram_chat_id,
            discord_webhook=  self.config.alert.discord_webhook,
            event_bus=        self.bus,
        )

        self.storage = DataStorage(self.config.database.sqlite_path)
        self.risk    = RiskManager(RiskParams())

        # 심볼별 캔들 캐시 (ICT 분석용)
        self._candle_cache: dict[str, list[Candle]] = {s: [] for s in symbols}
        self._oi_increasing: dict[str, Optional[bool]] = {s: None for s in symbols}

        # 이벤트 배선
        self._wire_events()

    def _wire_events(self) -> None:
        """EventBus 이벤트 배선 — 모든 데이터 흐름 연결."""
        # 틱 → Footprint + 스캐너 + DB
        self.bus.subscribe("tick", self._on_tick)

        # 오더북 → 스캐너 (히트맵은 UI에서 직접 구독)
        self.bus.subscribe("orderbook", self._on_orderbook)

        # Footprint 봉 완성 → 스캐너
        self.bus.subscribe("footprint", self._on_footprint_bar)

        # 연결 이벤트
        self.bus.subscribe("connected",    self._on_connected)
        self.bus.subscribe("disconnected", self._on_disconnected)

    # ── 이벤트 핸들러 ─────────────────────────────
    async def _on_tick(self, tick: Tick) -> None:
        """틱 수신 → Footprint 엔진 + 스캐너 + DB."""
        fp_eng = self.footprint_engines.get(tick.symbol)
        if fp_eng:
            fp_eng.on_tick(tick)
        self.scanner.on_tick(tick)
        self.storage.add_tick(tick)

    async def _on_orderbook(self, book) -> None:
        self.scanner.on_oi(book)   # OI는 별도 호출이지만 오더북 흐름 태워서 처리

    async def _on_footprint_bar(self, bar) -> None:
        """Footprint 봉 완성 → 스캐너 델타/Absorption 분석."""
        self.scanner.on_footprint_bar(bar)

    async def _on_connected(self, info: dict) -> None:
        logger.info(f"[App] 거래소 연결됨: {info}")

    async def _on_disconnected(self, info: dict) -> None:
        logger.warning(f"[App] 거래소 연결 끊김: {info}")

    # ── ICT 분석 루프 (15분봉 주기) ───────────────
    async def _ict_analysis_loop(self) -> None:
        """15분마다 각 심볼에 대해 ICT 분석 실행."""
        while self._running:
            next_run = self._next_bar_time(Timeframe.M15)
            await asyncio.sleep(max(0, next_run - asyncio.get_event_loop().time()))

            for sym in self.config.default_symbols:
                await self._run_ict(sym)

    async def _run_ict(self, symbol: str) -> None:
        """단일 심볼 ICT 분석 + AI 스코어 → 신호 발행."""
        try:
            # 최신 캔들 갱신
            candles = await self.exchange.get_candles(symbol, Timeframe.M15, 300)
            if not candles:
                return
            self._candle_cache[symbol] = candles

            # ICT 분석
            result = self.ict_engine.analyze(candles)
            if not result.signal:
                return

            # AI 스코어 보정
            fp_eng = self.footprint_engines.get(symbol)
            fp_bar = fp_eng.current_bar if fp_eng else None

            ctx = ScoreContext(
                ict_result=  result,
                fp_bar=      fp_bar,
                oi_increasing= self._oi_increasing.get(symbol),
                cur_price=   candles[-1].close,
                ema20=       candles[-1].close,
                ema50=       candles[-1].close,
            )
            score_result = self.score_engine.score(ctx, result.signal)

            # 신호 발행
            from core.models import StrategySignal, Exchange
            sig = StrategySignal(
                symbol=    symbol,
                ts=        candles[-1].ts,
                direction= result.signal,
                score=     score_result.total,
                entry=     result.entry,
                sl=        result.sl,
                tp=        result.tp,
                reasons=   result.reasons + score_result.reasons,
                exchange=  Exchange.OKX,
            )
            await self.bus.publish("strategy_signal", sig)
            self.storage.add_strategy_signal(sig)
            logger.info(f"[ICT] {symbol} {sig.direction} 신호 | 점수 {sig.score:.0f}")

        except Exception as e:
            logger.exception(f"[ICT] {symbol} 분석 오류: {e}")

    @staticmethod
    def _next_bar_time(tf: Timeframe) -> float:
        """다음 봉 마감 시각 계산 (asyncio 기준 시간)."""
        import time
        period = tf.seconds
        now    = time.time()
        return asyncio.get_event_loop().time() + (period - now % period)

    # ── OI 주기적 조회 ────────────────────────────
    async def _oi_loop(self) -> None:
        """60초마다 OI 조회 → 스캐너 전달."""
        while self._running:
            for sym in self.config.default_symbols:
                try:
                    oi = await self.exchange.get_oi(sym)
                    self.scanner.on_oi(oi)
                    prev = self._oi_increasing.get(sym)
                    # 간단히 이전 값과 비교 (실제론 deque로 추적)
                    self._oi_increasing[sym] = True  # 갱신 있으면 True 표시
                except Exception as e:
                    logger.debug(f"OI 조회 실패 {sym}: {e}")
            await asyncio.sleep(60)

    # ── 펀딩비 주기적 조회 ────────────────────────
    async def _funding_loop(self) -> None:
        """300초마다 펀딩비 조회 → 스캐너 전달."""
        while self._running:
            for sym in self.config.default_symbols:
                try:
                    funding = await self.exchange.get_funding(sym)
                    self.scanner.on_funding(funding)
                except Exception as e:
                    logger.debug(f"펀딩비 조회 실패 {sym}: {e}")
            await asyncio.sleep(300)

    # ── 초기 캔들 로드 ────────────────────────────
    async def load_initial_data(self) -> dict[str, list[Candle]]:
        """시작 시 과거 캔들 로드 → 차트 초기화용."""
        result: dict[str, list[Candle]] = {}
        for sym in self.config.default_symbols:
            try:
                candles = await self.exchange.get_candles_paged(
                    sym, Timeframe.M15,
                    total=self.config.candle_history,
                )
                self._candle_cache[sym] = candles
                result[sym]             = candles
                logger.info(f"[Init] {sym} 캔들 {len(candles)}봉 로드")
            except Exception as e:
                logger.warning(f"[Init] {sym} 캔들 로드 실패: {e}")
        return result

    # ── 시작 / 종료 ───────────────────────────────
    async def start(self) -> None:
        """앱 시작 — 모든 백그라운드 태스크 실행."""
        self._running = True

        # Numba JIT 사전 컴파일 (첫 사용 지연 방지)
        await asyncio.get_event_loop().run_in_executor(None, self._warmup_numba)

        # DB 초기화
        await self.storage.initialize()

        # 초기 데이터 로드
        await self.load_initial_data()

    @staticmethod
    def _warmup_numba() -> None:
        """Numba JIT 사전 컴파일 — 앱 시작 시 1회만 실행."""
        import numpy as np
        from indicators.base_indicators import ema, atr, rsi, vwap
        p = np.random.randn(50) + 65000.0
        h = p + 10; l = p - 10; v = np.abs(np.random.randn(50)) * 100
        ema(p, 5); atr(h, l, p, 5); rsi(p, 5); vwap(h, l, p, v)
        logger.info("[App] Numba JIT 워밍업 완료")

        # 백그라운드 태스크
        asyncio.create_task(self.feed.start(),     name="ws_feed")
        asyncio.create_task(self._ict_analysis_loop(), name="ict_loop")
        asyncio.create_task(self._oi_loop(),           name="oi_loop")
        asyncio.create_task(self._funding_loop(),      name="funding_loop")

        logger.info("CoinHTS 앱 시작됨")

    async def stop(self) -> None:
        """앱 종료 — 리소스 정리."""
        self._running = False
        await self.feed.stop()
        await self.storage.close()
        await self.alert.close()
        await self.exchange.close()
        logger.info("CoinHTS 앱 종료됨")
