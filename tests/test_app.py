"""
tests/test_app.py
=================
CoinHTSApp 오케스트레이터 통합 테스트.
실제 네트워크 없이 모의(mock) 데이터로 전체 파이프라인 검증.
"""
import asyncio
import time
import numpy as np
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from core.config import AppConfig
from core.events import EventBus
from core.models import Candle, Tick, Side, Timeframe, OIData, FundingData
from orderflow.footprint import FootprintEngine
from scanner.scanner import MarketScanner, ScannerConfig
from strategy.ict_engine import ICTEngine, ICTParams
from ai.score_engine import AIScoreEngine, ScoreContext
from risk.risk_manager import RiskManager, RiskParams, TradePosition


# ── EventBus 테스트 ───────────────────────────────────
class TestEventBus:
    def test_subscribe_and_publish(self):
        bus = EventBus()
        received = []
        bus.subscribe("test", lambda d: received.append(d))
        asyncio.run(bus.publish("test", "hello"))
        assert received == ["hello"]

    def test_multiple_subscribers(self):
        bus = EventBus()
        a, b = [], []
        bus.subscribe("evt", lambda d: a.append(d))
        bus.subscribe("evt", lambda d: b.append(d))
        asyncio.run(bus.publish("evt", 42))
        assert a == [42] and b == [42]

    def test_unsubscribe(self):
        bus = EventBus()
        received = []
        handler = lambda d: received.append(d)
        bus.subscribe("evt", handler)
        bus.unsubscribe("evt", handler)
        asyncio.run(bus.publish("evt", "x"))
        assert received == []

    def test_no_event_no_crash(self):
        bus = EventBus()
        asyncio.run(bus.publish("nonexistent", None))

    def test_async_handler(self):
        bus = EventBus()
        received = []
        async def async_handler(d):
            received.append(d)
        bus.subscribe("async_evt", async_handler)
        asyncio.run(bus.publish("async_evt", "data"))
        assert received == ["data"]

    def test_handler_exception_doesnt_stop_others(self):
        bus = EventBus()
        second = []
        def bad_handler(d): raise ValueError("의도적 오류")
        def good_handler(d): second.append(d)
        bus.subscribe("evt", bad_handler)
        bus.subscribe("evt", good_handler)
        asyncio.run(bus.publish("evt", "test"))
        assert second == ["test"]   # bad_handler 오류에도 good_handler 실행됨


# ── 전체 파이프라인 통합 테스트 ──────────────────────────
class TestPipeline:
    """틱 → Footprint → Scanner → ICT → AI 전체 파이프라인 테스트."""

    @pytest.fixture
    def bus(self):
        return EventBus()

    @pytest.fixture
    def fp_engine(self, bus):
        return FootprintEngine("BTC-USDT-SWAP", Timeframe.M1, tick_size=0.5, event_bus=bus)

    @pytest.fixture
    def scanner(self, bus):
        return MarketScanner(
            ["BTC-USDT-SWAP"],
            ScannerConfig(cooldown_seconds=0),
            event_bus=bus,
        )

    def _make_ticks(self, n=500, seed=42):
        np.random.seed(seed)
        price = 65000.0
        ticks = []
        for i in range(n):
            price += np.random.normal(0, 5)
            side = Side.BUY if np.random.random() > 0.48 else Side.SELL
            ticks.append(Tick(
                ts=1700000000.0 + i, price=price,
                size=abs(np.random.normal(0.1, 0.05)),
                side=side, symbol="BTC-USDT-SWAP",
            ))
        return ticks

    def test_ticks_create_footprint_bars(self, fp_engine):
        ticks = self._make_ticks(200)
        for t in ticks:
            fp_engine.on_tick(t)
        assert len(fp_engine.bars) >= 1
        assert fp_engine.current_bar is not None

    def test_footprint_bars_have_valid_data(self, fp_engine):
        for t in self._make_ticks(200):
            fp_engine.on_tick(t)
        for bar in fp_engine.bars:
            assert bar.candle.volume > 0
            assert len(bar.cells) > 0
            total = sum(c.buy_vol + c.sell_vol for c in bar.cells)
            assert abs(total - bar.candle.volume) < 1e-6

    def test_scanner_detects_signals(self, scanner):
        # OI 급증 시뮬레이션 — _emit 내부에서 publish_nowait 호출
        # 이벤트 루프 없어도 _last_signal에 키가 등록되는지 확인
        for _ in range(5):  # oi_window+1 개 필요
            scanner._oi_history["BTC-USDT-SWAP"].append(10000.0)
        scanner._oi_history["BTC-USDT-SWAP"].append(10300.0)  # 3% 급증
        scanner._scan_oi("BTC-USDT-SWAP")
        # _last_signal은 publish_nowait 전에 등록됨
        assert "BTC-USDT-SWAP:OI_SURGE" in scanner._last_signal

    def test_ict_analyzes_candles(self, btc_candles_300):
        params = ICTParams(require_displacement=False, min_confluence=0, min_rr=1.0)
        engine = ICTEngine(params)
        result = engine.analyze(btc_candles_300)
        assert isinstance(result.bull_ms, bool)
        assert 0.0 <= result.score <= 100.0

    def test_ai_score_with_context(self, btc_candles_300, tick_stream_1000):
        fp = FootprintEngine("BTC-USDT-SWAP", Timeframe.M1, tick_size=0.5)
        for t in tick_stream_1000:
            fp.on_tick(t)

        params = ICTParams(require_displacement=False, min_confluence=0)
        ict_result = ICTEngine(params).analyze(btc_candles_300)
        fp_bar     = fp.current_bar

        ctx = ScoreContext(
            ict_result=   ict_result,
            fp_bar=       fp_bar,
            cur_price=    65000.0,
            ema20=        65100.0,
            ema50=        64900.0,
            volume_spike= True,
            avg_volume=   300.0,
            cur_volume=   900.0,
        )
        score_eng = AIScoreEngine()
        result    = score_eng.score(ctx, "LONG")

        assert 0.0 <= result.total <= 100.0
        assert result.signal in ("BUY", "SELL", "NEUTRAL")
        assert result.confidence in ("HIGH", "MEDIUM", "LOW")
        assert isinstance(result.reasons, list)

    def test_risk_validates_signal(self):
        risk  = RiskManager(RiskParams(account_size=10000, risk_per_trade=1.0))
        ok, r = risk.validate_signal("LONG", 65000, 64500, 66000)
        assert ok and r == "OK"

    def test_full_pipeline_throughput(self, fp_engine, scanner):
        """전체 파이프라인 처리 속도 검증 (목표: 50K+ ticks/sec)."""
        N      = 5000
        ticks  = self._make_ticks(N, seed=99)
        t0     = time.perf_counter()
        for tick in ticks:
            fp_engine.on_tick(tick)
            scanner.on_tick(tick)
        elapsed = time.perf_counter() - t0
        tps     = N / elapsed
        assert tps > 50_000, f"파이프라인 처리 속도 미달: {tps:.0f} t/s"

    def test_event_propagation(self, bus, fp_engine, scanner):
        """틱 → EventBus → 구독자 전파 검증."""
        events_received = []
        bus.subscribe("tick", lambda t: events_received.append(t))

        async def run():
            tick = Tick(ts=1700000000.0, price=65000.0, size=0.1,
                        side=Side.BUY, symbol="BTC-USDT-SWAP")
            await bus.publish("tick", tick)

        asyncio.run(run())
        assert len(events_received) == 1
        assert events_received[0].price == 65000.0


# ── AI 스코어 엔진 상세 테스트 ───────────────────────────
class TestAIScoreEngine:
    @pytest.fixture
    def engine(self):
        return AIScoreEngine()

    def test_empty_context_low_score(self, engine):
        ctx    = ScoreContext(cur_price=65000.0)
        result = engine.score(ctx, "LONG")
        assert result.total < 20.0

    def test_high_score_on_all_conditions(self, engine):
        from strategy.ict_engine import ICTResult
        ict = ICTResult(
            bull_ms=True, bull_sweep_active=True,
            displacement=True, score=80.0,
        )
        from core.models import FootprintCell, FootprintBar
        cells  = [FootprintCell(price=65000.0, buy_vol=10.0, sell_vol=2.0)]
        from core.models import Candle, Timeframe
        candle = Candle(ts=0, open=64900, high=65200, low=64800,
                        close=65100, volume=100, symbol="BTC", timeframe=Timeframe.M15)
        bar    = FootprintBar(candle=candle, cells=cells, delta=8.0, cvd=8.0)

        ctx = ScoreContext(
            ict_result=   ict,
            fp_bar=       bar,
            volume_spike= True,
            avg_volume=   100.0,
            cur_volume=   400.0,
            cur_price=    65100.0,
            ema20=        65000.0,
            ema50=        64500.0,
        )
        result = engine.score(ctx, "LONG")
        assert result.total > 50.0

    def test_sell_signal_on_low_score(self, engine):
        ctx    = ScoreContext(cur_price=65000.0)
        result = engine.score(ctx, "SHORT")
        assert result.signal in ("SELL", "NEUTRAL")

    def test_funding_penalty_on_long(self, engine):
        from core.models import FundingData
        funding = FundingData(symbol="BTC-USDT-SWAP", ts=0, funding_rate=0.001)
        ctx     = ScoreContext(cur_price=65000.0, funding_data=funding)
        result1 = engine.score(ctx, "LONG")    # 롱 + 양수 펀딩 = 감점
        ctx2    = ScoreContext(cur_price=65000.0)
        result2 = engine.score(ctx2, "LONG")   # 펀딩 없음
        # 펀딩 과열 시 점수가 더 낮아야 함
        assert result1.total <= result2.total

    def test_breakdown_keys_present(self, engine):
        ctx    = ScoreContext(cur_price=65000.0, volume_spike=True, avg_volume=100, cur_volume=500)
        result = engine.score(ctx, "LONG")
        assert isinstance(result.breakdown, dict)

    def test_score_clamped_0_100(self, engine):
        """어떤 상황에서도 점수는 0~100 범위."""
        ctx = ScoreContext(cur_price=65000.0)
        for _ in range(10):
            result = engine.score(ctx, "LONG")
            assert 0.0 <= result.total <= 100.0


# ── Risk Manager 추가 엣지 케이스 ────────────────────────
class TestRiskEdgeCases:
    def test_kelly_criterion(self):
        params = RiskParams(
            account_size=10000, risk_per_trade=2.0,
            use_kelly=True, win_rate_estimate=0.6,
        )
        risk = RiskManager(params)
        size = risk.calc_position_size(65000, 64500)
        assert size > 0

    def test_multiple_positions_tracked(self):
        risk = RiskManager(RiskParams(account_size=10000, max_open_trades=5))
        positions = []
        for i in range(3):
            pos = TradePosition(
                symbol=f"SYM{i}", direction="LONG",
                entry=65000, sl=64500, tp=66000, size=0.1,
                entry_ts=float(i),
            )
            risk.open_position(pos)
            positions.append(pos)
        assert len(risk.open_trades) == 3
        risk.close_position(positions[0], 65500)
        assert len(risk.open_trades) == 2

    def test_trailing_updates_only_favorably(self):
        risk = RiskManager(RiskParams(trailing_enabled=True, trailing_atr_mult=1.0))
        pos  = TradePosition(
            symbol="BTC", direction="LONG",
            entry=65000, sl=64000, tp=67000,
            size=0.1, entry_ts=0.0, peak_price=65000,
        )
        # 유리한 방향 (가격 상승) → SL 올라가야
        risk.update_trailing_stop(pos, current_price=66000, atr_cur=100)
        assert pos.sl > 64000  # SL이 원래 64000보다 올라가야 함
        # 한번 올라간 SL은 가격 하락해도 내려가지 않아야
        sl_after_rise = pos.sl
        risk.update_trailing_stop(pos, current_price=65500, atr_cur=100)
        assert pos.sl >= sl_after_rise  # SL이 내려가지 않아야
