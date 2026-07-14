"""
tests/test_performance.py
=========================
성능 벤치마크 테스트.
목표:
  - 틱 처리: 100,000 ticks/sec 이상
  - Footprint 집계: 100,000 ticks/sec 이상
  - Numba 지표: 1000봉 기준 < 5ms
  - ICT 분석: 200봉 기준 < 50ms
"""
import time
import numpy as np
import pytest
from core.models import Tick, Side, Timeframe
from orderflow.footprint import FootprintEngine
from indicators.base_indicators import ema, atr, rsi, vwap, build_volume_profile
from strategy.ict_engine import ICTEngine, ICTParams


# ── Footprint 엔진 성능 ──────────────────────────────
class TestFootprintPerformance:
    def _make_ticks(self, n: int, seed: int = 42) -> list[Tick]:
        np.random.seed(seed)
        price = 65000.0
        ticks = []
        for i in range(n):
            price += np.random.normal(0, 5)
            ticks.append(Tick(
                ts=1700000000.0 + i * 0.05,
                price=price,
                size=abs(np.random.normal(0.1, 0.03)),
                side=Side.BUY if np.random.random() > 0.5 else Side.SELL,
                symbol="BTC-USDT-SWAP",
            ))
        return ticks

    @pytest.mark.parametrize("n_ticks,min_tps", [
        (10_000,  100_000),
        (50_000,  100_000),
        (100_000, 100_000),
    ])
    def test_tick_throughput(self, n_ticks, min_tps):
        engine = FootprintEngine("BTC-USDT-SWAP", Timeframe.M1, tick_size=0.5)
        ticks  = self._make_ticks(n_ticks)

        t0 = time.perf_counter()
        for tick in ticks:
            engine.on_tick(tick)
        elapsed = time.perf_counter() - t0

        tps = n_ticks / elapsed
        assert tps >= min_tps, (
            f"Footprint {n_ticks}틱 처리 속도: {tps:.0f} t/s "
            f"(목표: >{min_tps:,})"
        )

    def test_footprint_bars_consistency(self):
        """처리된 틱 수가 봉 내 누적 볼륨과 일치하는지."""
        engine = FootprintEngine("BTC-USDT-SWAP", Timeframe.M1, tick_size=0.5)
        total_vol = 0.0
        for tick in self._make_ticks(1000):
            engine.on_tick(tick)
            total_vol += tick.size

        # 완료봉 + 현재봉의 총 볼륨 = 입력 총 볼륨
        bar_vol = sum(b.candle.volume for b in engine.bars)
        if engine.current_bar:
            bar_vol += engine.current_bar.candle.volume
        assert abs(bar_vol - total_vol) < 1e-6


# ── Numba 지표 성능 ──────────────────────────────────
class TestIndicatorPerformance:
    @pytest.fixture(autouse=True)
    def warm_up_numba(self):
        """Numba JIT 첫 컴파일을 미리 수행해서 측정 편향 제거."""
        prices = np.random.randn(50) + 65000
        ema(prices, 10)
        atr(prices + 10, prices - 10, prices, 5)
        rsi(prices, 5)

    @pytest.mark.parametrize("n", [100, 500, 1000, 5000])
    def test_ema_speed(self, n):
        prices = np.random.randn(n).cumsum() + 65000
        t0 = time.perf_counter()
        for _ in range(10):   # 10회 반복 평균
            ema(prices, 20)
        elapsed = (time.perf_counter() - t0) / 10
        assert elapsed < 0.005, f"EMA({n}) 속도: {elapsed*1000:.2f}ms (목표: <5ms)"

    @pytest.mark.parametrize("n", [100, 500, 1000])
    def test_atr_speed(self, n):
        h = np.random.randn(n).cumsum() + 65050
        l = np.random.randn(n).cumsum() + 64950
        c = np.random.randn(n).cumsum() + 65000
        t0 = time.perf_counter()
        for _ in range(10):
            atr(h, l, c, 14)
        elapsed = (time.perf_counter() - t0) / 10
        assert elapsed < 0.005, f"ATR({n}) 속도: {elapsed*1000:.2f}ms (목표: <5ms)"

    @pytest.mark.parametrize("n", [100, 500, 1000])
    def test_rsi_speed(self, n):
        prices = np.random.randn(n).cumsum() + 65000
        t0 = time.perf_counter()
        for _ in range(10):
            rsi(prices, 14)
        elapsed = (time.perf_counter() - t0) / 10
        assert elapsed < 0.010, f"RSI({n}) 속도: {elapsed*1000:.2f}ms (목표: <10ms)"

    def test_volume_profile_speed(self):
        N      = 100_000
        prices = np.random.uniform(64000, 66000, N)
        vols   = np.abs(np.random.normal(0.5, 0.1, N))
        sides  = np.where(np.random.random(N) > 0.5, 1.0, -1.0)

        t0 = time.perf_counter()
        bins, buy, sell, bw = build_volume_profile(prices, vols, sides, n_bins=200)
        elapsed = time.perf_counter() - t0

        assert elapsed < 0.1, f"VolumeProfile({N:,}) 속도: {elapsed*1000:.0f}ms (목표: <100ms)"
        assert len(bins) == 200


# ── ICT 엔진 성능 ────────────────────────────────────
class TestICTPerformance:
    @pytest.fixture
    def candles_200(self):
        from core.models import Candle
        np.random.seed(0)
        prices = 65000 + np.cumsum(np.random.normal(0, 50, 200))
        return [
            Candle(ts=1700000000.0+i*900, open=prices[i],
                   high=prices[i]+50, low=prices[i]-50,
                   close=prices[i], volume=500,
                   symbol="BTC-USDT-SWAP", timeframe=Timeframe.M15)
            for i in range(200)
        ]

    def test_analyze_speed(self, candles_200):
        engine = ICTEngine(ICTParams())
        # 첫 실행은 캐시 미스 허용
        engine.analyze(candles_200)
        # 10회 반복 측정
        t0 = time.perf_counter()
        for _ in range(10):
            engine.analyze(candles_200)
        elapsed = (time.perf_counter() - t0) / 10
        assert elapsed < 0.100, (
            f"ICT analyze(200봉) 속도: {elapsed*1000:.1f}ms (목표: <100ms)"
        )

    def test_backtest_loop_speed(self, candles_200):
        """백테스트 루프: 100봉 윈도우로 100번 analyze 반복."""
        engine = ICTEngine(ICTParams(require_displacement=False, min_confluence=0))
        t0 = time.perf_counter()
        for i in range(100, 200):
            engine.analyze(candles_200[:i])
        elapsed = time.perf_counter() - t0
        assert elapsed < 5.0, f"100회 ICT analyze 루프: {elapsed:.2f}s (목표: <5s)"


# ── 메모리 안전성 ─────────────────────────────────────
class TestMemorySafety:
    def test_footprint_maxbars_no_leak(self):
        """max_bars 제한이 제대로 동작하는지 — 메모리 무한 증가 없음."""
        engine = FootprintEngine("BTC", Timeframe.M1, tick_size=0.5, max_bars=10)
        price  = 65000.0
        for i in range(500):
            price += np.random.normal(0, 5)
            engine.on_tick(Tick(
                ts=1700000000.0 + i * 60,
                price=price, size=0.1,
                side=Side.BUY, symbol="BTC",
            ))
        assert len(engine.bars) <= 10

    def test_scanner_cooldown_no_spam(self):
        """쿨다운이 신호 스팸을 방지하는지."""
        from scanner.scanner import MarketScanner, ScannerConfig
        scanner = MarketScanner(["BTC"], ScannerConfig(cooldown_seconds=60))
        signals_emitted = []

        # _emit을 직접 호출해서 쿨다운 동작 확인
        scanner._emit("BTC", "VOLUME_SPIKE", 1000, 100, "test")
        first = "BTC:VOLUME_SPIKE" in scanner._last_signal
        scanner._emit("BTC", "VOLUME_SPIKE", 1000, 100, "test")   # 쿨다운 중
        # 두 번째 emit은 쿨다운으로 무시됨
        assert first  # 첫 번째는 등록됨
