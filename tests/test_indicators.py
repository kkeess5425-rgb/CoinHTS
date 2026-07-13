"""tests/test_indicators.py — Numba 기반 지표 단위 테스트"""
import numpy as np
import pytest
from indicators.base_indicators import (
    ema, atr, rsi, cvd, vwap,
    build_volume_profile, compute_poc_vah_val,
    pivot_high, pivot_low,
)


@pytest.fixture
def price_series():
    np.random.seed(42)
    return 65000.0 + np.cumsum(np.random.normal(0, 50, 200))


@pytest.fixture
def ohlcv(price_series):
    h = price_series + np.abs(np.random.normal(0, 30, 200))
    l = price_series - np.abs(np.random.normal(0, 30, 200))
    v = np.abs(np.random.normal(500, 100, 200))
    return price_series, h, l, v


class TestEMA:
    def test_length(self, price_series):
        result = ema(price_series, 20)
        assert len(result) == len(price_series)

    def test_first_value(self, price_series):
        result = ema(price_series, 20)
        assert result[0] == pytest.approx(price_series[0])

    def test_smoothing(self, price_series):
        ema20 = ema(price_series, 20)
        ema50 = ema(price_series, 50)
        # EMA50이 EMA20보다 더 부드러움 (분산이 작음)
        assert np.std(np.diff(ema50)) <= np.std(np.diff(ema20))

    def test_empty(self):
        result = ema(np.array([]), 20)
        assert len(result) == 0


class TestATR:
    def test_length(self, ohlcv):
        c, h, l, _ = ohlcv
        result = atr(h, l, c, 14)
        assert len(result) == len(c)

    def test_positive(self, ohlcv):
        c, h, l, _ = ohlcv
        result = atr(h, l, c, 14)
        assert np.all(result >= 0)

    def test_high_vol_increases_atr(self):
        """변동성 높은 구간에서 ATR이 더 높아야 함."""
        c_low  = np.ones(100) * 65000
        c_high = 65000 + np.cumsum(np.random.normal(0, 200, 100))
        h_low  = c_low  + 10
        l_low  = c_low  - 10
        h_high = c_high + 500
        l_high = c_high - 500
        atr_low  = atr(h_low,  l_low,  c_low,  14)[-1]
        atr_high = atr(h_high, l_high, c_high, 14)[-1]
        assert atr_high > atr_low


class TestRSI:
    def test_range(self, price_series):
        result = rsi(price_series, 14)
        valid  = result[~np.isnan(result)]
        assert np.all(valid >= 0) and np.all(valid <= 100)

    def test_nan_warmup(self, price_series):
        result = rsi(price_series, 14)
        assert np.isnan(result[0])
        assert not np.isnan(result[14])

    def test_overbought_oversold(self):
        """강한 상승 → RSI 높음, 강한 하락 → RSI 낮음."""
        up_trend   = np.linspace(60000, 70000, 100)
        down_trend = np.linspace(70000, 60000, 100)
        rsi_up   = rsi(up_trend,   14)
        rsi_down = rsi(down_trend, 14)
        assert rsi_up[-1]   > 60
        assert rsi_down[-1] < 40


class TestCVD:
    def test_zero_when_balanced(self):
        buy_vol  = np.ones(100) * 1.0
        sell_vol = np.ones(100) * 1.0
        result   = cvd(buy_vol, sell_vol)
        assert np.all(result == 0.0)

    def test_positive_when_buy_dominant(self):
        buy_vol  = np.ones(100) * 2.0
        sell_vol = np.ones(100) * 1.0
        result   = cvd(buy_vol, sell_vol)
        assert result[-1] == pytest.approx(100.0)

    def test_cumulative(self):
        buy_vol  = np.array([1.0, 2.0, 3.0])
        sell_vol = np.array([0.5, 0.5, 0.5])
        result   = cvd(buy_vol, sell_vol)
        assert result[0] == pytest.approx(0.5)
        assert result[1] == pytest.approx(2.0)
        assert result[2] == pytest.approx(4.5)


class TestVWAP:
    def test_length(self, ohlcv):
        c, h, l, v = ohlcv
        result = vwap(h, l, c, v)
        assert len(result) == len(c)

    def test_between_high_low(self, ohlcv):
        c, h, l, v = ohlcv
        result = vwap(h, l, c, v)
        # VWAP은 누적 평균이므로 개별 봉 H/L 범위 밖일 수 있음 — 전체 범위 기준으로 체크
        assert np.all(result >= l.min() * 0.99) and np.all(result <= h.max() * 1.01)


class TestVolumeProfile:
    def test_output_shape(self):
        prices  = np.random.uniform(64000, 66000, 1000)
        volumes = np.abs(np.random.normal(0.5, 0.1, 1000))
        sides   = np.where(np.random.random(1000) > 0.5, 1, -1).astype(float)
        bins, buy, sell, bw = build_volume_profile(prices, volumes, sides, n_bins=50)
        assert len(bins) == 50
        assert len(buy)  == 50
        assert len(sell) == 50

    def test_poc_in_range(self):
        prices  = np.random.uniform(64000, 66000, 1000)
        volumes = np.abs(np.random.normal(0.5, 0.1, 1000))
        sides   = np.ones(1000)
        bins, buy, sell, _ = build_volume_profile(prices, volumes, sides, n_bins=50)
        poc, vah, val = compute_poc_vah_val(bins, buy, sell)
        assert prices.min() <= poc <= prices.max()
        assert val <= poc <= vah


class TestPivots:
    def test_pivot_high_exists(self, price_series):
        highs  = price_series + 50
        result = pivot_high(highs, 5)
        assert np.any(~np.isnan(result))

    def test_pivot_low_exists(self, price_series):
        lows   = price_series - 50
        result = pivot_low(lows, 5)
        assert np.any(~np.isnan(result))

    def test_pivot_high_is_local_max(self, price_series):
        highs  = price_series + 50
        result = pivot_high(highs, 5)
        for i in range(5, len(result) - 5):
            if not np.isnan(result[i]):
                assert highs[i] == max(highs[i-5:i+6])

    def test_symmetry(self):
        """피봇 고점/저점이 대칭적이어야 함."""
        prices = np.array([1,2,3,4,5,4,3,2,1,2,3,2,1], dtype=float)
        ph = pivot_high(prices, 2)
        pl = pivot_low(prices,  2)
        # 인덱스 4 (최고점)에 pivot_high (length=2이므로 2~len-2 범위)
        assert not np.isnan(ph[4])
        # 인덱스 8 (1의 값)에 pivot_low
        assert not np.isnan(pl[8])
