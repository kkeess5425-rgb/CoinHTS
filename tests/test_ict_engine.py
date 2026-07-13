"""tests/test_ict_engine.py — ICT 전략 엔진 단위 테스트"""
import numpy as np
import pytest
from core.models import Candle, Timeframe
from strategy.ict_engine import ICTEngine, ICTParams, ICTResult


def make_candles(prices, highs=None, lows=None, volumes=None) -> list[Candle]:
    n = len(prices)
    highs   = highs   or [p + 50  for p in prices]
    lows    = lows    or [p - 50  for p in prices]
    volumes = volumes or [500.0]   * n
    return [
        Candle(
            ts=1700000000.0 + i * 900,
            open=prices[i], high=highs[i],
            low=lows[i],    close=prices[i],
            volume=volumes[i],
            symbol="BTC-USDT-SWAP",
            timeframe=Timeframe.M15,
        )
        for i in range(n)
    ]


@pytest.fixture
def minimal_params():
    return ICTParams(
        require_displacement=False,
        min_confluence=0,
        min_rr=1.0,
        min_risk_pct=0.01,
    )


@pytest.fixture
def candles_200():
    np.random.seed(42)
    prices = 65000.0 + np.cumsum(np.random.normal(0, 50, 200))
    return make_candles(list(prices))


class TestICTEngine:
    def test_insufficient_data(self, minimal_params):
        engine  = ICTEngine(minimal_params)
        candles = make_candles([65000.0] * 50)
        result  = engine.analyze(candles)
        assert result.signal is None
        assert result.score  == 0.0

    def test_returns_ict_result(self, minimal_params, candles_200):
        engine = ICTEngine(minimal_params)
        result = engine.analyze(candles_200)
        assert isinstance(result, ICTResult)

    def test_market_structure_detected(self, minimal_params, candles_200):
        engine = ICTEngine(minimal_params)
        result = engine.analyze(candles_200)
        # bull_ms 는 True 또는 False (bool)
        assert isinstance(result.bull_ms, bool)

    def test_score_range(self, minimal_params, candles_200):
        engine = ICTEngine(minimal_params)
        result = engine.analyze(candles_200)
        assert 0.0 <= result.score <= 100.0

    def test_signal_with_entry_sl_tp(self, minimal_params, candles_200):
        engine = ICTEngine(minimal_params)
        result = engine.analyze(candles_200)
        if result.signal:
            assert result.entry is not None
            assert result.sl    is not None
            assert result.tp    is not None
            assert result.rr    is not None
            assert result.rr >= minimal_params.min_rr

    def test_long_signal_has_positive_rr(self, minimal_params, candles_200):
        engine = ICTEngine(minimal_params)
        result = engine.analyze(candles_200)
        if result.signal == "LONG":
            assert result.entry > result.sl
            assert result.tp    > result.entry

    def test_short_signal_has_positive_rr(self, minimal_params, candles_200):
        engine = ICTEngine(minimal_params)
        result = engine.analyze(candles_200)
        if result.signal == "SHORT":
            assert result.entry < result.sl
            assert result.tp    < result.entry

    def test_htf_bias_filters_long(self, candles_200):
        """HTF 베어리시이면 LONG 신호 차단."""
        params = ICTParams(require_displacement=False, min_confluence=0, min_rr=1.0)
        engine = ICTEngine(params)
        result = engine.analyze(candles_200, htf_bias=False)
        assert result.signal != "LONG"

    def test_htf_bias_filters_short(self, candles_200):
        """HTF 불리시이면 SHORT 신호 차단."""
        params = ICTParams(require_displacement=False, min_confluence=0, min_rr=1.0)
        engine = ICTEngine(params)
        result = engine.analyze(candles_200, htf_bias=True)
        assert result.signal != "SHORT"

    def test_reasons_populated_on_signal(self, minimal_params, candles_200):
        engine = ICTEngine(minimal_params)
        result = engine.analyze(candles_200)
        if result.signal:
            assert isinstance(result.reasons, list)

    def test_displacement_required(self, candles_200):
        """REQUIRE_DISPLACEMENT=True이면 displacement 없는 신호 차단."""
        params = ICTParams(
            require_displacement=True,
            displacement_atr_mult=99.0,   # 사실상 불가능한 기준
            min_confluence=0, min_rr=1.0,
        )
        engine = ICTEngine(params)
        result = engine.analyze(candles_200)
        if result.displacement is False:
            assert result.signal is None

    def test_different_symbols_independent(self):
        """다른 심볼 데이터를 독립적으로 분석."""
        params = ICTParams(require_displacement=False, min_confluence=0, min_rr=1.0)
        np.random.seed(0)
        p1 = 65000 + np.cumsum(np.random.normal(0, 50, 200))
        np.random.seed(99)
        p2 = 1800  + np.cumsum(np.random.normal(0, 5,  200))

        c1 = make_candles(list(p1))
        c2 = [Candle(ts=1700000000.0+i*900, open=p2[i], high=p2[i]+5,
                     low=p2[i]-5, close=p2[i], volume=500,
                     symbol="ETH-USDT-SWAP", timeframe=Timeframe.M15)
              for i in range(200)]

        engine  = ICTEngine(params)
        result1 = engine.analyze(c1)
        result2 = engine.analyze(c2)
        # 두 결과가 독립적
        assert isinstance(result1, ICTResult)
        assert isinstance(result2, ICTResult)


class TestICTParams:
    def test_default_params(self):
        p = ICTParams()
        assert p.min_rr          == 2.0
        assert p.min_confluence  == 1
        assert p.ote_fib_min     == pytest.approx(0.618)
        assert p.ote_fib_max     == pytest.approx(0.786)
        assert p.require_displacement is True

    def test_custom_params(self):
        p = ICTParams(min_rr=3.0, min_confluence=2)
        assert p.min_rr         == 3.0
        assert p.min_confluence == 2
