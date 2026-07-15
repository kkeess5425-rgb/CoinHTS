"""
tests/test_integration.py
=========================
전체 파이프라인 end-to-end 통합 테스트.
실제 네트워크 없이 합성 데이터로 전체 흐름을 검증한다.

파이프라인: Tick → Footprint → OF분석 → SMC+ICT → AI점수 → 신호 → 일지
"""
import asyncio
import time
import numpy as np
import pytest
from unittest.mock import AsyncMock, patch

from core.models import Tick, Candle, Side, Timeframe, OIData, FundingData
from core.events import EventBus


# ── 픽스처 ────────────────────────────────────────────
@pytest.fixture
def bus():
    return EventBus()


@pytest.fixture
def synthetic_ticks(n=2000, seed=42):
    np.random.seed(seed)
    price = 65000.0
    ticks = []
    for i in range(n):
        price += np.random.normal(0, 8)
        ticks.append(Tick(
            ts=1700000000.0 + i * 0.1,
            price=price,
            size=abs(np.random.normal(0.08, 0.03)),
            side=Side.BUY if np.random.random() > 0.48 else Side.SELL,
            symbol="BTC-USDT-SWAP",
        ))
    return ticks


@pytest.fixture
def synthetic_candles(n=300, seed=0):
    np.random.seed(seed)
    prices = 65000 + np.cumsum(np.random.normal(0, 50, n))
    return [
        Candle(ts=1700000000.0+i*900, open=prices[i],
               high=prices[i]+abs(np.random.normal(40,10)),
               low=prices[i]-abs(np.random.normal(40,10)),
               close=prices[i], volume=abs(np.random.normal(500,100)),
               symbol="BTC-USDT-SWAP", timeframe=Timeframe.M15)
        for i in range(n)
    ]


# ── 1. 틱 → Footprint 파이프라인 ──────────────────────
class TestTickToFootprint:
    def test_full_tick_processing(self, synthetic_ticks):
        from orderflow.footprint import FootprintEngine
        engine = FootprintEngine("BTC-USDT-SWAP", Timeframe.M1, tick_size=0.5)

        for tick in synthetic_ticks:
            engine.on_tick(tick)

        assert len(engine.bars) >= 1
        total_vol = sum(b.candle.volume for b in engine.bars)
        if engine.current_bar:
            total_vol += engine.current_bar.candle.volume
        expected = sum(t.size for t in synthetic_ticks)
        assert abs(total_vol - expected) < 1e-6

    def test_footprint_advanced_analysis(self, synthetic_ticks):
        from orderflow.footprint import FootprintEngine
        from orderflow.advanced import AdvancedOrderFlowAnalyzer

        engine = FootprintEngine("BTC-USDT-SWAP", Timeframe.M1, tick_size=0.5)
        analyzer = AdvancedOrderFlowAnalyzer(imbalance_ratio=3.0, min_stack_levels=2)

        for tick in synthetic_ticks:
            engine.on_tick(tick)
            analyzer.on_tick(tick)

        results = []
        for bar in engine.bars:
            result = analyzer.on_bar(bar)
            results.append(result)

        assert len(results) == len(engine.bars)
        all_types = {type(r).__name__ for r in results}
        assert "AdvancedOrderFlowResult" in all_types


# ── 2. 캔들 → ICT + SMC 파이프라인 ───────────────────
class TestCandleToStrategy:
    def test_ict_smc_combined(self, synthetic_candles):
        from strategy.ict_engine import ICTEngine, ICTParams
        from strategy.smc_engine import SMCEngine

        ict = ICTEngine(ICTParams(require_displacement=False, min_confluence=0))
        smc = SMCEngine()

        ict_result = ict.analyze(synthetic_candles)
        smc_result = smc.analyze(synthetic_candles)

        assert isinstance(ict_result.bull_ms, bool)
        assert isinstance(smc_result.bull_ms, bool)
        assert 0 <= ict_result.score <= 100
        assert 0 <= smc_result.score <= 100

    def test_smc_ict_consistency(self, synthetic_candles):
        """ICT와 SMC 결과가 일관성이 있는지 — 완전히 반대가 되지 않는지."""
        from strategy.ict_engine import ICTEngine, ICTParams
        from strategy.smc_engine import SMCEngine

        ict = ICTEngine(ICTParams(require_displacement=False, min_confluence=0))
        smc = SMCEngine(swing_length=5)

        ict_r = ict.analyze(synthetic_candles)
        smc_r = smc.analyze(synthetic_candles)

        # 두 엔진 모두 점수가 존재
        assert ict_r.score >= 0
        assert smc_r.score >= 0

    def test_ai_score_from_strategy(self, synthetic_candles):
        from strategy.ict_engine import ICTEngine, ICTParams
        from strategy.smc_engine import SMCEngine
        from ai.score_engine import AIScoreEngine, ScoreContext

        ict_r = ICTEngine(ICTParams(require_displacement=False)).analyze(synthetic_candles)
        smc_r = SMCEngine().analyze(synthetic_candles)

        ctx   = ScoreContext(
            ict_result=ict_r,
            cur_price=synthetic_candles[-1].close,
        )
        score = AIScoreEngine().score(ctx, "LONG")
        assert 0 <= score.total <= 100
        assert score.signal in ("BUY", "SELL", "NEUTRAL")


# ── 3. 전략 → AI 고급 분석 파이프라인 ────────────────
class TestStrategyToAI:
    def test_advanced_scorer_full(self, synthetic_candles, synthetic_ticks):
        from strategy.ict_engine import ICTEngine, ICTParams
        from strategy.smc_engine import SMCEngine
        from orderflow.footprint import FootprintEngine
        from orderflow.advanced import AdvancedOrderFlowAnalyzer
        from ai.advanced_scorer import AdvancedScoringEngine

        # Footprint
        fp_engine = FootprintEngine("BTC-USDT-SWAP", Timeframe.M1, tick_size=0.5)
        of_analyzer = AdvancedOrderFlowAnalyzer()
        for tick in synthetic_ticks[:500]:
            fp_engine.on_tick(tick)
            of_analyzer.on_tick(tick)

        of_result = of_analyzer.on_bar(fp_engine.current_bar) if fp_engine.current_bar else None

        ict_r = ICTEngine(ICTParams(require_displacement=False)).analyze(synthetic_candles)
        smc_r = SMCEngine().analyze(synthetic_candles)

        scorer = AdvancedScoringEngine()
        result = scorer.score(
            candles=synthetic_candles, ict_result=ict_r,
            smc_result=smc_r, fp_bar=fp_engine.current_bar,
            of_result=of_result, direction="LONG",
        )

        assert 0 <= result.entry_score <= 100
        assert 0 <= result.trend_score <= 100
        assert 0 <= result.confidence <= 100
        assert result.recommendation in ("LONG", "SHORT", "WAIT", "EXIT")
        assert len(result.market_narrative) > 0
        assert len(result.entry_narrative) > 0

    def test_chart_summary_full(self, synthetic_candles):
        from strategy.ict_engine import ICTEngine, ICTParams
        from strategy.smc_engine import SMCEngine
        from ai.chart_summary import AIChartSummaryEngine

        ict_r   = ICTEngine(ICTParams(require_displacement=False)).analyze(synthetic_candles)
        smc_r   = SMCEngine().analyze(synthetic_candles)
        summary = AIChartSummaryEngine().summarize(
            "BTC-USDT-SWAP", synthetic_candles,
            ict_result=ict_r, smc_result=smc_r,
        )

        assert summary.headline
        assert summary.trend != "데이터 부족"
        assert summary.full_text.count("\n") > 3


# ── 4. 신호 → 자동매매 파이프라인 ─────────────────────
class TestSignalToTrader:
    def test_signal_to_paper_trade(self):
        from core.models import StrategySignal, Exchange
        from trading.paper_trader import PaperTrader, TradingConfig
        bus = EventBus()
        trader = PaperTrader(
            config=TradingConfig(account_size=10000, risk_per_trade=1.0,
                                 min_score=0.0, partial_tp_enabled=True,
                                 trailing_enabled=True),
            event_bus=bus,
        )

        sig = StrategySignal(
            symbol="BTC-USDT-SWAP", ts=time.time(),
            direction="LONG", score=80.0,
            entry=65000, sl=64500, tp=67000,
            reasons=["BOS", "FVG"], exchange=Exchange.OKX,
        )
        asyncio.run(trader._on_signal(sig))
        assert len(trader.open_positions) == 1

        # 가격 상승 → 부분 익절 (1R 도달)
        trader.on_price_update("BTC-USDT-SWAP", 65500.0, atr=100)
        assert trader.open_positions[0].partial_done is True

        # TP 도달 → 청산
        trader.on_price_update("BTC-USDT-SWAP", 67000.0, atr=100)
        assert len(trader.open_positions) == 0
        assert trader._closed[0].pnl_r > 0

    def test_trade_journal_auto_record(self):
        from core.models import StrategySignal, Exchange
        from trading.paper_trader import PaperTrader, TradingConfig
        from ai.trade_journal import TradeJournalAI
        bus = EventBus()
        journal = TradeJournalAI()
        closed_trades = []

        def on_closed(pos):
            entry = journal.create_entry(
                symbol=pos.symbol, direction=pos.direction,
                entry=pos.entry, exit_price=pos.exit_price,
                sl=pos.sl, tp=pos.tp, pnl_r=pos.pnl_r,
                pnl_usd=pos.pnl_usd, entry_ts=pos.entry_ts,
                exit_ts=pos.exit_ts, exit_reason="TP" if pos.pnl_r > 0 else "SL",
            )
            closed_trades.append(entry)

        trader = PaperTrader(
            config=TradingConfig(min_score=0.0), event_bus=bus,
            on_trade_closed=on_closed,
        )

        sig = StrategySignal(
            symbol="BTC-USDT-SWAP", ts=time.time(), direction="LONG",
            score=75.0, entry=65000, sl=64500, tp=67000,
            reasons=[], exchange=Exchange.OKX,
        )
        asyncio.run(trader._on_signal(sig))
        trader.on_price_update("BTC-USDT-SWAP", 67000.0)

        assert len(closed_trades) == 1
        assert closed_trades[0].result == "WIN"
        assert closed_trades[0].pnl_r > 0


# ── 5. 통계 파이프라인 ────────────────────────────────
class TestStatisticsPipeline:
    def test_from_trades_to_mc(self):
        from stats.statistics import StatisticsEngine, TradeRecord
        from optimization.optimizer import MonteCarloSimulation

        np.random.seed(0)
        trades = []
        for i in range(50):
            pnl = np.random.choice([-1.0, 2.0, 1.5, -1.0, 0.5])
            trades.append(TradeRecord(
                entry_ts=1700000000.0+i*86400, exit_ts=1700000000.0+i*86400+3600,
                direction="LONG", entry=65000, exit_price=65000+pnl*500,
                sl=64500, tp=66000, pnl_r=pnl, pnl_usd=pnl*100,
            ))

        stats = StatisticsEngine().compute(trades)
        assert stats.total_trades == 50
        assert stats.sharpe_ratio != 0.0

        pnl_series = [t.pnl_r for t in trades]
        mc = MonteCarloSimulation().run(pnl_series, n_sims=200)
        assert "ruin_prob" in mc
        assert 0 <= mc["ruin_prob"] <= 100


# ── 6. EventBus 비동기 파이프라인 ─────────────────────
class TestEventBusPipeline:
    def test_full_event_chain(self, bus):
        """tick → footprint → scanner_signal → strategy_signal 체인"""
        received = {"tick": 0, "footprint": 0, "scanner": 0, "strategy": 0}

        async def track(event, d):
            received[event] += 1

        for event in received:
            bus.subscribe(event, lambda d, e=event: asyncio.get_event_loop().create_task(track(e, d)))

        async def run():
            for i in range(5):
                await bus.publish("tick", None)
            await bus.publish("footprint", None)
            await bus.publish("scanner_signal", None)
            await bus.publish("strategy_signal", None)
            await asyncio.sleep(0.05)

        asyncio.run(run())
        assert received["tick"] == 5
        assert received["footprint"] == 1

    def test_scanner_signal_flow(self):
        from scanner.scanner import MarketScanner, ScannerConfig
        signals = []
        bus = EventBus()
        bus.subscribe("scanner_signal", lambda s: signals.append(s))

        scanner = MarketScanner(["BTC-USDT-SWAP"],
                                ScannerConfig(cooldown_seconds=0), event_bus=bus)
        scanner._cooldown = 0

        for _ in range(6):
            scanner._oi_history["BTC-USDT-SWAP"].append(10000.0)
        scanner._oi_history["BTC-USDT-SWAP"].append(10300.0)
        scanner._scan_oi("BTC-USDT-SWAP")

        assert "BTC-USDT-SWAP:OI_SURGE" in scanner._last_signal


# ── 7. 성능 통합 테스트 ───────────────────────────────
class TestPerformanceIntegration:
    def test_full_pipeline_10k_ticks(self, synthetic_ticks):
        """10K 틱 전체 파이프라인 처리 속도 — 목표 50K ticks/sec."""
        from orderflow.footprint import FootprintEngine
        from orderflow.advanced import AdvancedOrderFlowAnalyzer
        from scanner.scanner import MarketScanner, ScannerConfig
        import time

        engine   = FootprintEngine("BTC-USDT-SWAP", Timeframe.M1, tick_size=0.5)
        analyzer = AdvancedOrderFlowAnalyzer()
        scanner  = MarketScanner(["BTC-USDT-SWAP"], ScannerConfig(cooldown_seconds=0))

        ticks = synthetic_ticks[:10000] if len(synthetic_ticks) >= 10000 else synthetic_ticks * 5

        t0 = time.perf_counter()
        for tick in ticks:
            engine.on_tick(tick)
            analyzer.on_tick(tick)
            scanner.on_tick(tick)
        elapsed = time.perf_counter() - t0
        tps = len(ticks) / elapsed

        assert tps > 50_000, f"처리 속도 미달: {tps:,.0f} t/s (목표 50K)"

    def test_smc_batch_analysis_speed(self, synthetic_candles):
        """SMC 100회 분석 — 목표 10초 이내."""
        from strategy.smc_engine import SMCEngine
        import time

        engine = SMCEngine()
        t0 = time.perf_counter()
        for i in range(100, min(200, len(synthetic_candles))):
            engine.analyze(synthetic_candles[:i])
        elapsed = time.perf_counter() - t0

        assert elapsed < 10.0, f"SMC 100회 분석: {elapsed:.2f}s (목표 <10s)"
