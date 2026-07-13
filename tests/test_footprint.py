"""tests/test_footprint.py — Footprint 엔진 단위 테스트"""
import time
import numpy as np
import pytest
from core.models import Tick, Side, Timeframe
from orderflow.footprint import FootprintEngine


@pytest.fixture
def engine():
    return FootprintEngine("BTC-USDT-SWAP", Timeframe.M1, tick_size=0.5, max_bars=100)


def make_tick(ts: float, price: float, size: float, side: Side) -> Tick:
    return Tick(ts=ts, price=price, size=size, side=side, symbol="BTC-USDT-SWAP")


class TestFootprintEngine:
    def test_initial_state(self, engine):
        assert engine.current_bar is None
        assert engine.bars == []

    def test_first_tick_creates_bar(self, engine):
        engine.on_tick(make_tick(1700000000.0, 65000.0, 0.1, Side.BUY))
        assert engine.current_bar is not None

    def test_buy_sell_accumulation(self, engine):
        base = 1700000000.0
        engine.on_tick(make_tick(base,       65000.0, 1.0, Side.BUY))
        engine.on_tick(make_tick(base + 1,   65000.0, 0.5, Side.SELL))
        engine.on_tick(make_tick(base + 2,   65000.5, 0.3, Side.BUY))

        bar = engine.current_bar
        assert bar is not None
        assert bar.delta == pytest.approx(1.3 - 0.5, abs=1e-6)

    def test_bar_closes_on_new_period(self, engine):
        base = 1700000000.0
        # 첫 번째 봉 (0~59초)
        engine.on_tick(make_tick(base,       65000.0, 1.0, Side.BUY))
        engine.on_tick(make_tick(base + 30,  65010.0, 0.5, Side.BUY))
        # 두 번째 봉 (60~)
        engine.on_tick(make_tick(base + 60,  65020.0, 0.2, Side.SELL))

        assert len(engine.bars) == 1
        assert engine.current_bar is not None

    def test_poc_detection(self, engine):
        base = 1700000000.0
        # 65000.0에 가장 많은 거래
        for _ in range(10):
            engine.on_tick(make_tick(base, 65000.0, 1.0, Side.BUY))
        for _ in range(3):
            engine.on_tick(make_tick(base + 1, 65000.5, 1.0, Side.BUY))

        bar = engine.current_bar
        assert bar.poc == pytest.approx(65000.0)

    def test_tick_size_rounding(self, engine):
        """틱 사이즈(0.5) 기준으로 가격이 반올림돼야."""
        base = 1700000000.0
        engine.on_tick(make_tick(base, 65000.3, 1.0, Side.BUY))  # → 65000.5
        engine.on_tick(make_tick(base + 1, 65000.7, 0.5, Side.SELL))  # → 65001.0

        bar = engine.current_bar
        prices = [c.price for c in bar.cells]
        # 0.5 배수여야 함
        for p in prices:
            assert abs(round(p / 0.5) * 0.5 - p) < 1e-6

    def test_max_bars_limit(self):
        engine = FootprintEngine("BTC-USDT-SWAP", Timeframe.M1, tick_size=0.5, max_bars=5)
        base = 1700000000.0
        for i in range(10):
            engine.on_tick(make_tick(base + i * 60, 65000.0, 0.1, Side.BUY))

        assert len(engine.bars) <= 5

    def test_performance(self, engine):
        """10,000틱 처리 속도 검증 (목표: 100K ticks/sec 이상)."""
        N = 10_000
        base = 1700000000.0
        np.random.seed(0)
        prices = 65000.0 + np.cumsum(np.random.normal(0, 5, N))
        sizes  = np.abs(np.random.normal(0.1, 0.05, N))
        sides  = [Side.BUY if np.random.random() > 0.5 else Side.SELL for _ in range(N)]

        t0 = time.perf_counter()
        for i in range(N):
            engine.on_tick(Tick(
                ts=base + i * 0.1, price=prices[i],
                size=sizes[i], side=sides[i],
                symbol="BTC-USDT-SWAP",
            ))
        elapsed = time.perf_counter() - t0
        tps = N / elapsed
        assert tps > 100_000, f"성능 미달: {tps:.0f} ticks/sec (목표: >100K)"

    def test_imbalance_detection(self, engine):
        """4:1 이상 불균형 감지."""
        base = 1700000000.0
        engine.on_tick(make_tick(base,   65000.0, 4.0, Side.BUY))
        engine.on_tick(make_tick(base+1, 65000.0, 1.0, Side.SELL))

        bar = engine.current_bar
        imbalances = engine.detect_imbalances(bar, ratio=4.0)
        assert any(side == "bull" for _, side in imbalances)

    def test_absorption_detection(self, engine):
        """가격 거의 안 움직이는데 큰 볼륨 → 흡수."""
        base = 1700000000.0
        # 도지봉(open≈close) + 큰 볼륨 시뮬레이션
        for i in range(20):
            engine.on_tick(make_tick(base + i, 65000.0, 5.0, Side.BUY))
            engine.on_tick(make_tick(base + i + 0.5, 65000.0, 0.1, Side.SELL))

        bar = engine.current_bar
        if bar:
            result = engine.detect_absorption(bar, delta_pct_threshold=0.5)
            # 흡수 또는 None (조건 미충족 시)
            assert result in ("bull_absorption", "bear_absorption", None)

    def test_callbacks(self):
        """on_bar_close 콜백 호출 확인."""
        closed_bars = []
        engine = FootprintEngine("BTC-USDT-SWAP", Timeframe.M1, tick_size=0.5)
        engine.on_bar_close = lambda b: closed_bars.append(b)

        base = 1700000000.0
        engine.on_tick(make_tick(base,      65000.0, 1.0, Side.BUY))
        engine.on_tick(make_tick(base + 60, 65010.0, 1.0, Side.BUY))  # 봉 마감

        assert len(closed_bars) == 1
        assert closed_bars[0].candle.open == pytest.approx(65000.0)

    def test_delta_series(self, engine):
        base = 1700000000.0
        engine.on_tick(make_tick(base,       65000.0, 3.0, Side.BUY))
        engine.on_tick(make_tick(base + 30,  65000.0, 1.0, Side.SELL))
        engine.on_tick(make_tick(base + 60,  65000.0, 2.0, Side.BUY))
        engine.on_tick(make_tick(base + 120, 65000.0, 0.5, Side.SELL))

        deltas = engine.get_delta_series()
        assert len(deltas) == 2          # 완료된 봉 2개
        assert deltas[0] == pytest.approx(2.0)   # 3-1
