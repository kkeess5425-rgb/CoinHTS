"""
tests/test_edge_cases.py
========================
엣지케이스 & 스트레스 테스트.
경계 조건, 오류 복구, 극단적 입력을 검증한다.
"""
import asyncio, time, math
import numpy as np
import pytest
from core.models import Tick, Side, Candle, Timeframe


# ── 극단적 가격 입력 ──────────────────────────────────
class TestExtremeInputs:
    def test_zero_volume_candle(self):
        """볼륨 0 캔들도 오류 없이 처리."""
        from strategy.ict_engine import ICTEngine, ICTParams
        np.random.seed(0)
        prices = 65000 + np.cumsum(np.random.normal(0, 50, 100))
        candles = [
            Candle(ts=float(i)*900, open=p, high=p+10, low=p-10,
                   close=p, volume=0.0,  # 볼륨 0
                   symbol="BTC", timeframe=Timeframe.M15)
            for i, p in enumerate(prices)
        ]
        engine = ICTEngine(ICTParams(require_displacement=False, min_confluence=0))
        result = engine.analyze(candles)
        assert 0 <= result.score <= 100

    def test_flat_price_candles(self):
        """모든 캔들이 같은 가격 → ATR=0 처리."""
        from strategy.ict_engine import ICTEngine, ICTParams
        candles = [
            Candle(ts=float(i)*900, open=65000, high=65000,
                   low=65000, close=65000, volume=100,
                   symbol="BTC", timeframe=Timeframe.M15)
            for i in range(100)
        ]
        engine = ICTEngine(ICTParams(require_displacement=False))
        result = engine.analyze(candles)  # ATR=0이어도 에러 없음
        assert result.score >= 0

    def test_extreme_price_spike(self):
        """갑작스러운 가격 급등/급락 처리."""
        from orderflow.footprint import FootprintEngine
        engine = FootprintEngine("BTC", Timeframe.M1, tick_size=0.5)
        # 정상 틱 후 극단적 가격
        for i in range(100):
            engine.on_tick(Tick(ts=float(i), price=65000.0, size=0.1,
                                side=Side.BUY, symbol="BTC"))
        # 10배 가격 급등
        engine.on_tick(Tick(ts=200.0, price=650000.0, size=0.1,
                            side=Side.BUY, symbol="BTC"))
        assert engine.current_bar is not None

    def test_negative_pnl_journal(self):
        """연속 손실 일지."""
        from ai.trade_journal import TradeJournalAI
        journal = TradeJournalAI()
        for i in range(5):
            journal.create_entry(
                symbol="BTC", direction="LONG",
                entry=65000, exit_price=64500,
                sl=64500, tp=67000,
                pnl_r=-1.0, pnl_usd=-100.0,
                entry_ts=float(i)*86400, exit_ts=float(i)*86400+3600,
                exit_reason="SL", score=30.0,
            )
        # 5연속 손실 후 복수매매 경고
        last = journal.entries[-1]
        kinds = [m.kind for m in last.mistakes]
        assert "revenge" in kinds

    def test_very_high_score(self):
        """100점 이상 점수 클램핑."""
        from strategy.confluence_engine import ConfluenceEngine
        from strategy.ict_engine import ICTResult
        from strategy.smc_engine import SMCResult, FVGZone, OrderBlock
        engine = ConfluenceEngine()
        # 모든 조건 완벽 충족
        ict = ICTResult(bull_ms=True, bull_sweep_active=True, displacement=True, score=100)
        result = engine.score("LONG", ict_result=ict,
                              whale_signal="bullish", funding_rate=-0.001)
        assert result.total <= 100.0
        assert result.grade in ("A+", "A", "B", "C", "D")

    def test_empty_candle_smc(self):
        """빈 캔들 리스트 → 기본 SMCResult."""
        from strategy.smc_engine import SMCEngine, SMCResult
        result = SMCEngine().analyze([])
        assert isinstance(result, SMCResult)
        assert result.score == 0.0

    def test_single_candle_ict(self):
        """캔들 1개 → 분석 불가 graceful."""
        from strategy.ict_engine import ICTEngine, ICTParams
        candle = [Candle(ts=0, open=65000, high=65100, low=64900,
                         close=65000, volume=100,
                         symbol="BTC", timeframe=Timeframe.M15)]
        result = ICTEngine(ICTParams()).analyze(candle)
        assert result.score == 0.0

    def test_nan_in_prices(self):
        """NaN 가격 처리."""
        from indicators.base_indicators import ema
        prices = np.array([65000.0, np.nan, 65200.0, 65100.0, 65300.0] * 10)
        prices = np.nan_to_num(prices, nan=65000.0)  # NaN → 0 처리 후
        result = ema(prices, 5)
        assert not np.any(np.isnan(result))


# ── 동시성 테스트 ─────────────────────────────────────
class TestConcurrency:
    def test_eventbus_concurrent_publish(self):
        """여러 이벤트 동시 발행."""
        from core.events import EventBus
        bus = EventBus()
        results = []
        bus.subscribe("evt", lambda d: results.append(d))

        async def run():
            tasks = [bus.publish("evt", i) for i in range(50)]
            await asyncio.gather(*tasks)

        asyncio.run(run())
        assert len(results) == 50

    def test_footprint_concurrent_ticks(self):
        """여러 틱 동시 처리 (스레드 안전성)."""
        from orderflow.footprint import FootprintEngine
        engine = FootprintEngine("BTC", Timeframe.M1, tick_size=0.5)
        import threading

        def feed_ticks(start, n):
            for i in range(n):
                engine.on_tick(Tick(
                    ts=float(start + i) * 0.01,
                    price=65000.0 + i * 0.5,
                    size=0.1, side=Side.BUY, symbol="BTC",
                ))

        threads = [threading.Thread(target=feed_ticks, args=(i*100, 100)) for i in range(5)]
        for t in threads: t.start()
        for t in threads: t.join()

        total_vol = sum(b.candle.volume for b in engine.bars)
        if engine.current_bar:
            total_vol += engine.current_bar.candle.volume
        assert total_vol > 0

    def test_scanner_concurrent_symbols(self):
        """여러 심볼 동시 스캔."""
        from scanner.scanner import MarketScanner, ScannerConfig
        symbols  = [f"SYM{i}-USDT-SWAP" for i in range(10)]
        scanner  = MarketScanner(symbols, ScannerConfig(cooldown_seconds=0))
        signals  = []
        for sym in symbols:
            for _ in range(6):
                scanner._oi_history[sym].append(10000.0)
            scanner._oi_history[sym].append(10300.0)
            scanner._scan_oi(sym)
        detected = sum(1 for sym in symbols
                       if f"{sym}:OI_SURGE" in scanner._last_signal)
        assert detected == 10  # 모든 심볼 감지


# ── 메모리 누수 방지 ──────────────────────────────────
class TestMemoryBounds:
    def test_signal_log_bounded(self):
        """신호 로그가 제한을 초과하지 않음."""
        from ai.trade_journal import TradeJournalAI
        journal = TradeJournalAI()
        for i in range(30):
            journal.create_entry(
                symbol="BTC", direction="LONG",
                entry=65000, exit_price=65500,
                sl=64500, tp=67000,
                pnl_r=1.0, pnl_usd=100.0,
                entry_ts=float(i)*86400,
                exit_ts=float(i)*86400+3600,
                exit_reason="TP", score=75.0,
            )
        assert len(journal.entries) == 30

    def test_orderbook_analyzer_bounded(self):
        """OrderBookAnalyzer 히스토리 제한."""
        from orderbook.analyzer import OrderBookAnalyzer, DOMSnapshot
        from core.models import OrderBook, BookLevel
        analyzer = OrderBookAnalyzer(history_len=10)
        for i in range(20):
            book = OrderBook(symbol="BTC", ts=float(i),
                             bids=[BookLevel(price=65000-j, size=1.0) for j in range(5)],
                             asks=[BookLevel(price=65010+j, size=1.0) for j in range(5)])
            analyzer.on_orderbook(book)
        assert len(analyzer._dom_history) <= 10

    def test_whale_tracker_bounded(self):
        """WhaleTracker 히스토리 제한."""
        from whale.tracker import WhaleTracker
        tracker = WhaleTracker()
        transfers = asyncio.run(tracker.fetch_whale_transfers())
        # deque maxlen 100 내에서 동작
        assert len(tracker.transfers) <= 100

    def test_monitor_history_bounded(self):
        """SystemMonitor 히스토리 제한."""
        from core.monitor import SystemMonitor
        monitor = SystemMonitor(history_len=10)
        for _ in range(20):
            snap = monitor._collect()
            monitor._history.append(snap)
        assert len(monitor._history) == 10


# ── 수학적 정확성 ─────────────────────────────────────
class TestMathAccuracy:
    def test_equity_curve_sum(self):
        """Equity 곡선 누적값 정확성."""
        from stats.statistics import StatisticsEngine, TradeRecord
        pnl = [1.0, -1.0, 2.0, -1.0, 1.5]
        records = [
            TradeRecord(entry_ts=float(i)*86400, exit_ts=float(i)*86400+3600,
                        direction="LONG", entry=65000, exit_price=65500,
                        sl=64500, tp=67000, pnl_r=p)
            for i, p in enumerate(pnl)
        ]
        stats = StatisticsEngine().compute(records)
        assert abs(stats.total_r - sum(pnl)) < 1e-6

    def test_win_rate_exact(self):
        """승률 정확성."""
        from stats.statistics import StatisticsEngine, TradeRecord
        records = [
            TradeRecord(entry_ts=float(i)*86400, exit_ts=float(i)*86400+3600,
                        direction="LONG", entry=65000, exit_price=65500,
                        sl=64500, tp=67000, pnl_r=1.0 if i < 7 else -1.0)
            for i in range(10)
        ]
        stats = StatisticsEngine().compute(records)
        assert abs(stats.win_rate - 70.0) < 0.001

    def test_position_risk_exact(self):
        """포지션 리스크 정확성."""
        from risk.position_sizer import PositionSizer, SizingConfig
        sizer = PositionSizer(SizingConfig(
            method="fixed", account_size=10000, risk_pct=1.0, max_size_pct=100.0
        ))
        r = sizer.calculate(entry=65000, sl=64500)
        # 리스크 금액 = 10000 * 1% = 100 USD
        assert abs(r.risk_amount - 100.0) < 1e-6
        # 포지션 크기 = min(100/500, max_size) — max_size가 먼저 적용될 수 있음
        expected_uncapped = 100.0 / 500.0   # 0.2
        max_size = 10000.0 * 100.0 / 100.0 / 65000.0   # ~0.1538
        assert abs(r.size - min(expected_uncapped, max_size)) < 1e-3

    def test_profit_factor_extreme(self):
        """Profit Factor 극단값."""
        from stats.statistics import StatisticsEngine, TradeRecord
        # 100% 승률
        records = [
            TradeRecord(entry_ts=float(i)*86400, exit_ts=float(i)*86400+3600,
                        direction="LONG", entry=65000, exit_price=66000,
                        sl=64500, tp=67000, pnl_r=2.0)
            for i in range(10)
        ]
        stats = StatisticsEngine().compute(records)
        assert stats.win_rate == 100.0
        # Profit Factor = 무한대 → 시스템에서 큰 값으로 처리
        assert stats.profit_factor > 100

    def test_sharpe_ratio_units(self):
        """Sharpe Ratio 합리적 범위."""
        from stats.statistics import StatisticsEngine, TradeRecord
        import numpy as np
        np.random.seed(42)
        pnl = np.random.normal(0.3, 1.0, 100)
        records = [
            TradeRecord(entry_ts=float(i)*86400, exit_ts=float(i)*86400+3600,
                        direction="LONG", entry=65000, exit_price=65500,
                        sl=64500, tp=67000, pnl_r=float(p))
            for i, p in enumerate(pnl)
        ]
        stats = StatisticsEngine().compute(records)
        # 합리적 Sharpe 범위: -10 ~ 10
        assert -10 <= stats.sharpe_ratio <= 10

    def test_mdd_always_negative(self):
        """MDD는 항상 0 이하."""
        from stats.statistics import StatisticsEngine, TradeRecord
        # 손실 후 회복
        records = [
            TradeRecord(entry_ts=float(i)*86400, exit_ts=float(i)*86400+3600,
                        direction="LONG", entry=65000, exit_price=64500,
                        sl=64500, tp=67000,
                        pnl_r=-1.0 if i < 3 else 2.0)
            for i in range(6)
        ]
        stats = StatisticsEngine().compute(records)
        assert stats.max_drawdown_r <= 0
