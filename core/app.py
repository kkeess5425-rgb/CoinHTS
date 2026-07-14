"""
core/app.py  ─ CoinHTS v1.0 완전 통합 오케스트레이터

데이터 흐름:
  OKX WebSocket
      ↓ tick
  FootprintEngine  → AdvancedOrderFlowAnalyzer → EventBus("footprint")
      ↓ bar_close
  MarketScanner + AdvancedScanner  → EventBus("scanner_signal")
  SMCEngine + ICTEngine (15분봉)   → AdvancedScoringEngine → EventBus("strategy_signal")
  PaperTrader                      → EventBus("position_opened/closed")
  WhaleTracker + NewsAggregator    → 주기적 갱신
  AlertManager                     → Telegram / Discord
  DataStorage                      → SQLite 저장
  TradeJournalAI                   → 매매일지 자동 생성
"""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Optional

from core.config import AppConfig, get_config
from core.events import EventBus, get_event_bus
from core.models import Candle, Timeframe, Tick, StrategySignal, Exchange
from exchange.okx import OKXExchange
from websocket.okx_feed import OKXWebSocketFeed
from orderflow.footprint import FootprintEngine
from orderflow.advanced import AdvancedOrderFlowAnalyzer
from orderbook.analyzer import OrderBookAnalyzer
from scanner.scanner import MarketScanner, ScannerConfig
from scanner.advanced_scanner import AdvancedScanner
from strategy.ict_engine import ICTEngine, ICTParams
from strategy.smc_engine import SMCEngine
from ai.score_engine import AIScoreEngine, ScoreContext
from ai.advanced_scorer import AdvancedScoringEngine
from ai.trade_journal import TradeJournalAI
from alert.alert_manager import AlertManager
from database.storage import DataStorage
from risk.risk_manager import RiskManager, RiskParams
from trading.paper_trader import PaperTrader, TradingConfig
from whale.tracker import WhaleTracker
from news.news_aggregator import NewsAggregator
from stats.statistics import StatisticsEngine
from plugins.plugin_system import PluginManager

logger = logging.getLogger(__name__)


class CoinHTSApp:
    """
    완전 통합 오케스트레이터.
    모든 모듈을 초기화하고 EventBus로 연결한다.
    """

    def __init__(self, config: Optional[AppConfig] = None) -> None:
        self.config   = config or get_config()
        self.bus      = get_event_bus()
        self._running = False
        symbols       = self.config.default_symbols

        # ── 거래소 / WebSocket ──────────────────────
        self.exchange = OKXExchange(
            api_key=    self.config.exchange.api_key,
            api_secret= self.config.exchange.api_secret,
            passphrase= self.config.exchange.passphrase,
        )
        self.feed = OKXWebSocketFeed(symbols=symbols, event_bus=self.bus, depth=50)

        # ── 오더플로우 ──────────────────────────────
        self.footprint_engines: dict[str, FootprintEngine] = {
            sym: FootprintEngine(sym, Timeframe.M1,
                                 tick_size=self.config.default_tick_size(sym),
                                 event_bus=self.bus)
            for sym in symbols
        }
        self.of_analyzers: dict[str, AdvancedOrderFlowAnalyzer] = {
            sym: AdvancedOrderFlowAnalyzer() for sym in symbols
        }
        self.ob_analyzers: dict[str, OrderBookAnalyzer] = {
            sym: OrderBookAnalyzer() for sym in symbols
        }

        # ── 스캐너 ──────────────────────────────────
        self.scanner          = MarketScanner(symbols, ScannerConfig(), event_bus=self.bus)
        self.advanced_scanner = AdvancedScanner(symbols, event_bus=self.bus)

        # ── 전략 엔진 ───────────────────────────────
        self.ict_engine   = ICTEngine(ICTParams())
        self.smc_engine   = SMCEngine()
        self.score_engine = AIScoreEngine()
        self.adv_scorer   = AdvancedScoringEngine()

        # ── AI / 통계 ───────────────────────────────
        self.journal   = TradeJournalAI()
        self.stats_eng = StatisticsEngine()

        # ── 알림 / 저장 / 리스크 ────────────────────
        self.alert   = AlertManager(
            telegram_token=   self.config.alert.telegram_token,
            telegram_chat_id= self.config.alert.telegram_chat_id,
            discord_webhook=  self.config.alert.discord_webhook,
            event_bus=        self.bus,
        )
        self.storage = DataStorage(self.config.database.sqlite_path)
        self.risk    = RiskManager(RiskParams())

        # ── 자동매매 ────────────────────────────────
        self.paper_trader = PaperTrader(
            config=TradingConfig(min_score=70.0),
            event_bus=self.bus,
            on_trade_closed=self._on_trade_closed,
        )

        # ── 외부 데이터 ─────────────────────────────
        self.whale_tracker    = WhaleTracker()
        self.news_aggregator  = NewsAggregator()

        # ── 플러그인 ────────────────────────────────
        self.plugins = PluginManager(event_bus=self.bus)

        # 캔들 캐시
        self._candle_cache:   dict[str, list[Candle]] = {s: [] for s in symbols}
        self._oi_history:     dict[str, list[float]]  = {s: [] for s in symbols}
        self._smc_cache:      dict[str, object]       = {}

        self._wire_events()

    # ── 이벤트 배선 ──────────────────────────────────
    def _wire_events(self) -> None:
        self.bus.subscribe("tick",             self._on_tick)
        self.bus.subscribe("orderbook",        self._on_orderbook)
        self.bus.subscribe("footprint",        self._on_footprint_bar)
        self.bus.subscribe("strategy_signal",  self._on_strategy_signal)
        self.bus.subscribe("connected",        self._on_connected)
        self.bus.subscribe("disconnected",     self._on_disconnected)

    # ── 틱 처리 ──────────────────────────────────────
    async def _on_tick(self, tick: Tick) -> None:
        fp  = self.footprint_engines.get(tick.symbol)
        ofa = self.of_analyzers.get(tick.symbol)
        if fp:  fp.on_tick(tick)
        if ofa: ofa.on_tick(tick)
        self.scanner.on_tick(tick)
        self.storage.add_tick(tick)
        # 페이퍼 트레이더 가격 업데이트
        self.paper_trader.on_price_update(tick.symbol, tick.price)

    # ── 오더북 처리 ───────────────────────────────────
    async def _on_orderbook(self, book) -> None:
        oba = self.ob_analyzers.get(book.symbol)
        if oba:
            oba.on_orderbook(book)

    # ── Footprint 봉 완성 ─────────────────────────────
    async def _on_footprint_bar(self, bar) -> None:
        sym = bar.candle.symbol
        ofa = self.of_analyzers.get(sym)
        of_result = ofa.on_bar(bar) if ofa else None
        self.scanner.on_footprint_bar(bar)
        if of_result:
            self.advanced_scanner.on_footprint_bar(sym, bar, of_result)

    # ── 전략 신호 처리 ────────────────────────────────
    async def _on_strategy_signal(self, sig: StrategySignal) -> None:
        """신호 발생 → 리스크 검증 → 알림."""
        ok, reason = self.risk.validate_signal(
            sig.direction, sig.entry or 0, sig.sl or 0, sig.tp or 0
        )
        if not ok:
            logger.debug(f"[Risk] {sig.symbol} 신호 거부: {reason}")
            return
        logger.info(f"[Signal] {sig.symbol} {sig.direction} 점수={sig.score:.0f} | {reason}")

    # ── 트레이드 청산 콜백 ────────────────────────────
    def _on_trade_closed(self, pos) -> None:
        """페이퍼 트레이더 청산 → 매매일지 자동 기록."""
        smc  = self._smc_cache.get(pos.symbol)
        ict  = self.ict_engine
        entry = self.journal.create_entry(
            symbol=pos.symbol, direction=pos.direction,
            entry=pos.entry, exit_price=pos.exit_price,
            sl=pos.sl, tp=pos.tp,
            pnl_r=pos.pnl_r, pnl_usd=pos.pnl_usd,
            entry_ts=pos.entry_ts, exit_ts=pos.exit_ts,
            exit_reason="TP" if pos.pnl_r > 0 else "SL",
            score=0.0,
            smc_info={"bull_ms": smc.bull_ms, "fvg_zones": smc.fvg_zones} if smc else None,
        )
        if entry.mistakes:
            logger.warning(f"[Journal] 실수 감지: {[m.kind for m in entry.mistakes]}")

    async def _on_connected(self, info: dict) -> None:
        logger.info(f"[App] 거래소 연결됨: {info}")

    async def _on_disconnected(self, info: dict) -> None:
        logger.warning(f"[App] 거래소 연결 끊김: {info}")

    # ── ICT + SMC 분석 루프 ──────────────────────────
    async def _strategy_loop(self) -> None:
        """15분봉 마감 시 ICT + SMC 통합 분석."""
        while self._running:
            period = 900
            wait   = period - (time.time() % period)
            await asyncio.sleep(max(10, wait))

            for sym in self.config.default_symbols:
                await self._run_strategy(sym)

    async def _run_strategy(self, symbol: str) -> None:
        try:
            candles = await self.exchange.get_candles(symbol, Timeframe.M15, 300)
            if not candles:
                return
            self._candle_cache[symbol] = candles

            # ICT 분석
            ict_result = self.ict_engine.analyze(candles)

            # SMC 분석
            smc_result = self.smc_engine.analyze(candles)
            self._smc_cache[symbol] = smc_result

            # Advanced Scanner로 SMC 신호 발행
            self.advanced_scanner.scan_smc(symbol, candles, candles[-1].ts)

            # 신호가 없으면 종료
            signal_dir = ict_result.signal or smc_result.signal
            if not signal_dir:
                return

            # 고급 AI 점수
            fp  = self.footprint_engines.get(symbol)
            oba = self.ob_analyzers.get(symbol)
            ofa = self.of_analyzers.get(symbol)

            adv_score = self.adv_scorer.score(
                candles=      candles,
                ict_result=   ict_result,
                smc_result=   smc_result,
                fp_bar=       fp.current_bar if fp else None,
                ob_imbalance= list(oba.imbalance_log)[-1] if oba and oba.imbalance_log else None,
                direction=    signal_dir,
            )

            # 기본 AI 점수 (기존 엔진)
            ctx = ScoreContext(
                ict_result=smc_result, smc_result=smc_result,
                fp_bar=fp.current_bar if fp else None,
                cur_price=candles[-1].close,
            )
            base_score = self.score_engine.score(ctx, signal_dir)

            final_score = (adv_score.entry_score * 0.6 + base_score.total * 0.4)

            sig = StrategySignal(
                symbol=    symbol,
                ts=        candles[-1].ts,
                direction= signal_dir,
                score=     round(final_score, 1),
                entry=     ict_result.entry or candles[-1].close,
                sl=        ict_result.sl    or 0.0,
                tp=        ict_result.tp    or 0.0,
                reasons=   (ict_result.reasons or []) + adv_score.entry_reasons[:3],
                exchange=  Exchange.OKX,
            )
            await self.bus.publish("strategy_signal", sig)
            self.storage.add_strategy_signal(sig)

            logger.info(
                f"[Strategy] {symbol} {sig.direction} "
                f"점수={sig.score:.0f} | {adv_score.entry_narrative}"
            )

        except Exception as e:
            logger.exception(f"[Strategy] {symbol} 분석 오류: {e}")

    # ── OI / 펀딩 / 고래 / 뉴스 루프 ────────────────
    async def _market_data_loop(self) -> None:
        while self._running:
            for sym in self.config.default_symbols:
                try:
                    oi = await self.exchange.get_oi(sym)
                    self.scanner.on_oi(oi)
                    self._oi_history[sym].append(oi.oi_ccy)
                    if len(self._oi_history[sym]) > 20:
                        self._oi_history[sym] = self._oi_history[sym][-20:]
                except Exception: pass
                try:
                    funding = await self.exchange.get_funding(sym)
                    self.scanner.on_funding(funding)
                except Exception: pass
            await asyncio.sleep(60)

    async def _whale_loop(self) -> None:
        while self._running:
            try:
                await self.whale_tracker.fetch_whale_transfers()
                await self.whale_tracker.fetch_exchange_netflow("BTC")
                await self.whale_tracker.fetch_stablecoin_flow()
            except Exception as e:
                logger.debug(f"[Whale] 갱신 오류: {e}")
            await asyncio.sleep(300)

    async def _news_loop(self) -> None:
        while self._running:
            try:
                await self.news_aggregator.fetch_economic_calendar()
                await self.news_aggregator.fetch_crypto_news()
            except Exception as e:
                logger.debug(f"[News] 갱신 오류: {e}")
            await asyncio.sleep(3600)

    # ── Numba 워밍업 ──────────────────────────────────
    @staticmethod
    def _warmup_numba() -> None:
        import numpy as np
        from indicators.base_indicators import ema, atr, rsi, vwap
        p = np.random.randn(50) + 65000.0
        h = p + 10; l = p - 10; v = np.abs(np.random.randn(50)) * 100
        ema(p, 5); atr(h, l, p, 5); rsi(p, 5); vwap(h, l, p, v)
        logger.info("[App] Numba JIT 워밍업 완료")

    # ── 초기 캔들 로드 ────────────────────────────────
    async def load_initial_data(self) -> dict[str, list[Candle]]:
        result: dict[str, list[Candle]] = {}
        for sym in self.config.default_symbols:
            try:
                candles = await self.exchange.get_candles_paged(
                    sym, Timeframe.M15, total=self.config.candle_history
                )
                self._candle_cache[sym] = candles
                result[sym]             = candles
                logger.info(f"[Init] {sym} {len(candles)}봉 로드")
            except Exception as e:
                logger.warning(f"[Init] {sym} 로드 실패: {e}")
        return result

    # ── 시작 / 종료 ───────────────────────────────────
    async def start(self) -> None:
        self._running = True

        # Numba 워밍업
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, self._warmup_numba)

        # DB 초기화
        await self.storage.initialize()

        # 플러그인 로드
        self.plugins.load_all()
        await self.plugins.startup_all()

        # 초기 데이터
        await self.load_initial_data()

        # 백그라운드 태스크
        asyncio.create_task(self.feed.start(),         name="ws_feed")
        asyncio.create_task(self._strategy_loop(),     name="strategy")
        asyncio.create_task(self._market_data_loop(),  name="market_data")
        asyncio.create_task(self._whale_loop(),        name="whale")
        asyncio.create_task(self._news_loop(),         name="news")

        logger.info("✅ CoinHTS v1.0 시작됨 (72개 파일, 12,000줄)")

    async def stop(self) -> None:
        self._running = False
        await self.plugins.shutdown_all()
        await self.feed.stop()
        await self.storage.close()
        await self.alert.close()
        await self.exchange.close()
        await self.whale_tracker.close()
        await self.news_aggregator.close()
        logger.info("CoinHTS 종료됨")

    # ── 상태 조회 API ─────────────────────────────────
    def get_status(self) -> dict:
        """현재 앱 상태 요약."""
        return {
            "running":        self._running,
            "symbols":        self.config.default_symbols,
            "paper_trader":   self.paper_trader.get_status(),
            "whale_sentiment":self.whale_tracker.get_market_sentiment(),
            "upcoming_events":[
                {"title": e.title, "impact": e.impact, "ts": e.ts}
                for e in self.news_aggregator.get_upcoming_events(24)
            ],
            "journal_count":  len(self.journal.entries),
            "mistake_stats":  self.journal.get_mistake_stats(),
            "plugins":        self.plugins.get_list(),
        }
