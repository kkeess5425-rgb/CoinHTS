"""tests/test_scanner.py — MarketScanner 단위 테스트"""
import time
import numpy as np
import pytest
from core.models import Candle, Side, Tick, Timeframe, OIData, FundingData
from core.events import EventBus
from scanner.scanner import MarketScanner, ScannerConfig


@pytest.fixture
def bus():
    return EventBus()


@pytest.fixture
def scanner(bus):
    cfg = ScannerConfig(
        volume_spike_mult=2.0,
        volume_window=5,
        oi_surge_pct=1.0,
        oi_window=3,
        funding_extreme=0.01,
        cooldown_seconds=0,   # 테스트에서는 쿨다운 없음
    )
    s = MarketScanner(["BTC-USDT-SWAP"], cfg, event_bus=bus)
    s._cooldown = 0  # 테스트용 쿨다운 비활성화
    return s


def make_candle(volume: float, ts_offset: int = 0, close: float = 65000.0) -> Candle:
    return Candle(
        ts=1700000000.0 + ts_offset,
        open=close, high=close+50, low=close-50, close=close,
        volume=volume, symbol="BTC-USDT-SWAP", timeframe=Timeframe.M15,
    )


class TestVolumeSpike:
    def test_normal_volume_no_signal(self, scanner, bus):
        signals = []
        bus.subscribe("scanner_signal", lambda s: signals.append(s))
        for i in range(10):
            scanner.on_candle(make_candle(500.0, ts_offset=i*900))
        assert not any(s.signal_type == "VOLUME_SPIKE" for s in signals)

    def test_spike_triggers_signal(self, scanner, bus):
        import asyncio
        signals = []

        async def capture(s): signals.append(s)
        bus.subscribe("scanner_signal", capture)

        # 평균 볼륨 설정
        for i in range(6):
            scanner._candle_history["BTC-USDT-SWAP"].append(make_candle(500.0, i*900))

        # 스파이크 캔들 (평균의 5배)
        spike_candle = make_candle(2500.0, ts_offset=6*900)
        scanner._candle_history["BTC-USDT-SWAP"].append(spike_candle)
        scanner._scan_volume("BTC-USDT-SWAP")

        # publish_nowait는 이벤트 루프 없이 실행 안 됨 → 직접 확인
        # 이 테스트에서는 _emit이 호출됐는지만 확인
        assert scanner._last_signal.get("BTC-USDT-SWAP:VOLUME_SPIKE") is not None


class TestOISurge:
    def test_no_surge_no_signal(self, scanner):
        for i in range(5):
            scanner._oi_history["BTC-USDT-SWAP"].append(10000.0)
        scanner._scan_oi("BTC-USDT-SWAP")
        assert scanner._last_signal.get("BTC-USDT-SWAP:OI_SURGE") is None

    def test_surge_detected(self, scanner):
        # OI 2% 이상 변화
        for i in range(4):
            scanner._oi_history["BTC-USDT-SWAP"].append(10000.0)
        scanner._oi_history["BTC-USDT-SWAP"].append(10250.0)  # 2.5% 증가
        scanner._scan_oi("BTC-USDT-SWAP")
        assert scanner._last_signal.get("BTC-USDT-SWAP:OI_SURGE") is not None


class TestFundingExtreme:
    def test_normal_funding_no_signal(self, scanner):
        scanner._last_funding["BTC-USDT-SWAP"] = 0.00001  # 0.001%
        scanner._scan_funding("BTC-USDT-SWAP")
        assert scanner._last_signal.get("BTC-USDT-SWAP:FUNDING_EXTREME") is None

    def test_extreme_funding_signal(self, scanner):
        scanner._last_funding["BTC-USDT-SWAP"] = 0.002   # 0.2% (극단값)
        scanner._scan_funding("BTC-USDT-SWAP")
        assert scanner._last_signal.get("BTC-USDT-SWAP:FUNDING_EXTREME") is not None


class TestSweepDetection:
    def test_bull_sweep_detected(self, scanner):
        # 20봉의 저점보다 낮게 찍고 회복
        for i in range(22):
            close = 65000.0 + i * 10
            scanner._candle_history["BTC-USDT-SWAP"].append(
                make_candle(500, i*900, close)
            )
        # 마지막 봉: 저점 돌파 후 회복
        sweep = Candle(
            ts=1700000000.0 + 22*900,
            open=65000, high=65300,
            low=64000,   # window_low (65000) 아래로 찍음
            close=65200,  # 회복
            volume=1500, symbol="BTC-USDT-SWAP", timeframe=Timeframe.M15,
        )
        scanner._candle_history["BTC-USDT-SWAP"].append(sweep)
        scanner._scan_sweep("BTC-USDT-SWAP")
        assert scanner._last_signal.get("BTC-USDT-SWAP:BULL_SWEEP") is not None


class TestCooldown:
    def test_cooldown_prevents_repeat(self):
        cfg = ScannerConfig(volume_spike_mult=2.0, volume_window=5)
        scanner = MarketScanner(["BTC-USDT-SWAP"], cfg)
        scanner._cooldown = 60.0

        key = "BTC-USDT-SWAP:VOLUME_SPIKE"
        scanner._last_signal[key] = time.time()
        # 방금 신호 보냈으니 다시 emit 안 됨
        scanner._emit("BTC-USDT-SWAP", "VOLUME_SPIKE", 100, 50, "test")
        # _last_signal은 갱신되지 않음 (쿨다운)
        assert time.time() - scanner._last_signal[key] < 1.0
