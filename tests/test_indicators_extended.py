"""tests/test_indicators_extended.py — BB/MACD/Stoch/WR/OBV 테스트."""
import time
import numpy as np
import pytest
from indicators.base_indicators import (
    bollinger_bands, macd, stochastic, williams_r, obv, ema
)


@pytest.fixture(autouse=True)
def warmup():
    """Numba JIT 사전 컴파일."""
    p = np.random.randn(50).cumsum() + 100
    h = p + 1; l = p - 1; v = np.abs(np.random.randn(50)) + 1
    bollinger_bands(p, 5, 2.0)
    macd(p, 5, 10, 3)
    stochastic(h, l, p, 5, 3)
    williams_r(h, l, p, 5)
    obv(p, v)


class TestBollingerBands:
    @pytest.fixture
    def prices(self):
        np.random.seed(0)
        return np.random.randn(200).cumsum() + 65000

    def test_returns_three_arrays(self, prices):
        mid, up, lo = bollinger_bands(prices, 20, 2.0)
        assert len(mid) == len(up) == len(lo) == len(prices)

    def test_upper_above_middle(self, prices):
        mid, up, lo = bollinger_bands(prices, 20, 2.0)
        valid = ~np.isnan(mid) & ~np.isnan(up)
        assert np.all(up[valid] >= mid[valid])

    def test_lower_below_middle(self, prices):
        mid, up, lo = bollinger_bands(prices, 20, 2.0)
        valid = ~np.isnan(mid) & ~np.isnan(lo)
        assert np.all(lo[valid] <= mid[valid])

    def test_warmup_period_nan(self, prices):
        mid, up, lo = bollinger_bands(prices, 20, 2.0)
        assert np.all(np.isnan(mid[:19]))
        assert not np.isnan(mid[19])

    def test_speed(self, prices):
        t0 = time.perf_counter()
        for _ in range(1000):
            bollinger_bands(prices, 20, 2.0)
        elapsed = (time.perf_counter() - t0) / 1000 * 1000
        assert elapsed < 1.0, f"BB 속도: {elapsed:.2f}ms"

    def test_wider_band_larger_std(self, prices):
        _, up1, lo1 = bollinger_bands(prices, 20, 1.0)
        _, up2, lo2 = bollinger_bands(prices, 20, 3.0)
        valid = ~np.isnan(up1) & ~np.isnan(up2)
        assert np.all(up2[valid] >= up1[valid])

    def test_flat_price_zero_bandwidth(self):
        flat = np.full(50, 65000.0)
        mid, up, lo = bollinger_bands(flat, 20, 2.0)
        valid = ~np.isnan(mid)
        # 평탄 가격: 밴드폭 = 0
        assert np.allclose(up[valid], mid[valid])
        assert np.allclose(lo[valid], mid[valid])


class TestMACD:
    @pytest.fixture
    def prices(self):
        np.random.seed(1)
        return np.random.randn(300).cumsum() + 65000

    def test_returns_three_arrays(self, prices):
        m, s, h = macd(prices, 12, 26, 9)
        assert len(m) == len(s) == len(h) == len(prices)

    def test_histogram_is_macd_minus_signal(self, prices):
        m, s, h = macd(prices, 12, 26, 9)
        valid = ~np.isnan(m) & ~np.isnan(s)
        np.testing.assert_allclose(h[valid], m[valid] - s[valid], atol=1e-10)

    def test_zero_crossover_exists(self, prices):
        """트렌딩 데이터에서 MACD 0선 크로스오버 존재."""
        # 상승 후 하락
        t = np.concatenate([
            np.linspace(65000, 70000, 150),
            np.linspace(70000, 63000, 150)
        ])
        m, s, h = macd(t, 12, 26, 9)
        valid = ~np.isnan(h)
        # 히스토그램이 양수와 음수를 모두 가져야
        assert np.any(h[valid] > 0) and np.any(h[valid] < 0)

    def test_speed(self, prices):
        t0 = time.perf_counter()
        for _ in range(500):
            macd(prices, 12, 26, 9)
        elapsed = (time.perf_counter() - t0) / 500 * 1000
        assert elapsed < 2.0, f"MACD 속도: {elapsed:.2f}ms"


class TestStochastic:
    @pytest.fixture
    def ohlc(self):
        np.random.seed(2)
        c = np.random.randn(200).cumsum() + 65000
        h = c + np.abs(np.random.randn(200)) * 30
        l = c - np.abs(np.random.randn(200)) * 30
        return h, l, c

    def test_returns_two_arrays(self, ohlc):
        h, l, c = ohlc
        k, d = stochastic(h, l, c, 14, 3)
        assert len(k) == len(d) == len(c)

    def test_k_range_0_100(self, ohlc):
        h, l, c = ohlc
        k, _ = stochastic(h, l, c, 14, 3)
        valid = ~np.isnan(k)
        assert np.all(k[valid] >= 0) and np.all(k[valid] <= 100)

    def test_overbought_oversold(self, ohlc):
        h, l, c = ohlc
        k, _ = stochastic(h, l, c, 14, 3)
        valid = ~np.isnan(k)
        # %K는 0~100 범위 내 다양한 값을 가져야
        k_valid = k[valid]
        assert k_valid.max() > k_valid.min()  # 변동이 있어야 함
        assert k_valid.min() >= 0 and k_valid.max() <= 100


class TestWilliamsR:
    @pytest.fixture
    def ohlc(self):
        np.random.seed(3)
        c = np.random.randn(100).cumsum() + 65000
        h = c + np.abs(np.random.randn(100)) * 20
        l = c - np.abs(np.random.randn(100)) * 20
        return h, l, c

    def test_range_minus100_to_0(self, ohlc):
        h, l, c = ohlc
        wr = williams_r(h, l, c, 14)
        valid = ~np.isnan(wr)
        assert np.all(wr[valid] >= -100) and np.all(wr[valid] <= 0)

    def test_warmup_nan(self, ohlc):
        h, l, c = ohlc
        wr = williams_r(h, l, c, 14)
        assert np.all(np.isnan(wr[:13]))
        assert not np.isnan(wr[13])


class TestOBV:
    def test_increases_on_up_day(self):
        closes  = np.array([100.0, 101.0, 102.0])
        volumes = np.array([1000.0, 500.0, 800.0])
        result  = obv(closes, volumes)
        assert result[1] == result[0] + 500.0
        assert result[2] == result[1] + 800.0

    def test_decreases_on_down_day(self):
        closes  = np.array([100.0, 99.0, 98.0])
        volumes = np.array([1000.0, 500.0, 700.0])
        result  = obv(closes, volumes)
        assert result[1] == result[0] - 500.0
        assert result[2] == result[1] - 700.0

    def test_flat_unchanged(self):
        closes  = np.array([100.0, 100.0, 100.0])
        volumes = np.array([1000.0, 500.0, 700.0])
        result  = obv(closes, volumes)
        assert result[0] == result[1] == result[2] == 0.0

    def test_length_matches(self):
        n = 100
        c = np.random.randn(n).cumsum() + 100
        v = np.abs(np.random.randn(n)) * 100
        result = obv(c, v)
        assert len(result) == n

    def test_speed(self):
        n = 10000
        c = np.random.randn(n).cumsum() + 100
        v = np.abs(np.random.randn(n)) * 100
        t0 = time.perf_counter()
        for _ in range(100):
            obv(c, v)
        elapsed = (time.perf_counter() - t0) / 100 * 1000
        assert elapsed < 5.0, f"OBV 속도: {elapsed:.2f}ms"


class TestIndicatorCombinations:
    """여러 지표 조합 테스트."""

    def test_all_indicators_on_same_data(self):
        """동일 데이터에 모든 지표 적용."""
        np.random.seed(42)
        n = 300
        c = np.random.randn(n).cumsum() + 65000
        h = c + np.abs(np.random.randn(n)) * 30
        l = c - np.abs(np.random.randn(n)) * 30
        v = np.abs(np.random.randn(n)) * 500

        # 전부 에러 없이 실행
        bb_mid, bb_up, bb_lo = bollinger_bands(c, 20, 2.0)
        ml, ms, mh            = macd(c, 12, 26, 9)
        k, d                  = stochastic(h, l, c, 14, 3)
        wr                    = williams_r(h, l, c, 14)
        ob                    = obv(c, v)

        # 모든 결과 길이 일치
        for arr in [bb_mid, ml, k, wr, ob]:
            assert len(arr) == n

    def test_combined_signal_generation(self):
        """BB + MACD + Stoch 조합 신호."""
        np.random.seed(99)
        # 상승 추세 데이터
        c = np.linspace(63000, 68000, 200) + np.random.randn(200) * 100
        h = c + 50; l = c - 50
        v = np.abs(np.random.randn(200)) * 500

        bb_mid, bb_up, bb_lo = bollinger_bands(c, 20, 2.0)
        ml, ms, mh            = macd(c, 12, 26, 9)
        k, d                  = stochastic(h, l, c, 14, 3)

        # 상승 추세: MACD 히스토그램 양수 비율이 높아야
        valid = ~np.isnan(mh)
        positive_ratio = (mh[valid] > 0).mean()
        assert positive_ratio > 0.4   # 최소 40%

    def test_performance_all_together(self):
        """전체 지표 처리 속도 (1000봉)."""
        n = 1000
        c = np.random.randn(n).cumsum() + 65000
        h = c + 30; l = c - 30; v = np.abs(np.random.randn(n)) * 500

        t0 = time.perf_counter()
        for _ in range(100):
            bollinger_bands(c, 20, 2.0)
            macd(c, 12, 26, 9)
            stochastic(h, l, c, 14, 3)
            williams_r(h, l, c, 14)
            obv(c, v)
        elapsed = (time.perf_counter() - t0) / 100 * 1000
        assert elapsed < 10.0, f"전체 지표 속도: {elapsed:.2f}ms/iter"
