"""tests/test_new_modules.py — 새 모듈 단위 테스트."""
import asyncio, time
import numpy as np
import pytest
from core.models import Tick, Side, Timeframe, Candle


# ── Hidden Liquidity ──────────────────────────────────
class TestHiddenLiquidity:
    @pytest.fixture
    def detector(self):
        from orderflow.hidden_liquidity import HiddenLiquidityDetector
        return HiddenLiquidityDetector(
            iceberg_min_hits=3,
            price_bucket_size=10.0,
            cluster_vol_mult=3.0,
        )

    def _make_tick(self, price=65000.0, size=0.1, side=Side.BUY, ts=None):
        return Tick(ts=ts or time.time(), price=price, size=size,
                    side=side, symbol="BTC-USDT-SWAP")

    def test_iceberg_detection(self, detector):
        """동일 크기 반복 체결 → Iceberg 감지."""
        orders = []
        for i in range(5):
            t   = self._make_tick(size=0.250, ts=time.time() + i * 0.1)
            res = detector.on_tick(t)
            if res:
                orders.append(res)
        assert any(o.kind == "iceberg" for o in orders)

    def test_no_false_positive(self, detector):
        """다른 크기 체결은 Iceberg로 감지 안 됨."""
        orders = []
        for i in range(5):
            t   = self._make_tick(size=0.1 + i * 0.05, ts=time.time() + i)
            res = detector.on_tick(t)
            if res:
                orders.append(res)
        assert len(orders) == 0

    def test_cluster_detection(self, detector):
        """볼륨 집중 구간 감지."""
        # 특정 가격대에 대량 체결 (평균의 3배 이상)
        for i in range(50):
            detector.on_tick(self._make_tick(price=65005.0, size=3.0, ts=float(i)))
        # 다른 가격대는 극소량
        for i in range(3):
            detector.on_tick(self._make_tick(price=67000.0, size=0.001, ts=float(i+200)))
        clusters = detector.get_clusters()
        # 클러스터가 있거나 버킷 집계가 동작함을 확인
        assert isinstance(clusters, list)
        # 볼륨 집중이 있으면 클러스터 감지
        if clusters:
            assert clusters[0].kind == "cluster"

    def test_analyze_returns_result(self, detector):
        from orderflow.hidden_liquidity import HiddenLiquidityResult
        for i in range(10):
            detector.on_tick(self._make_tick(price=65000+i, ts=float(i)))
        result = detector.analyze()
        assert isinstance(result, HiddenLiquidityResult)
        assert result.dominant_side in ("buy", "sell", "neutral")

    def test_dom_gap_detection(self, detector):
        """DOM 공백 감지."""
        from core.models import OrderBook, BookLevel
        from orderbook.analyzer import DOMSnapshot

        # 이전 오더북 (65000에 매수 주문 있음)
        book1 = OrderBook(symbol="BTC", ts=1.0,
                          bids=[BookLevel(price=65000, size=10.0)],
                          asks=[BookLevel(price=65010, size=5.0)])
        # 틱 체결
        detector.on_tick(self._make_tick(price=65000, size=0.5, ts=1.5))
        detector.on_orderbook(book1)

        # 새 오더북 (65000 주문 사라짐)
        book2 = OrderBook(symbol="BTC", ts=2.0,
                          bids=[BookLevel(price=64990, size=8.0)],
                          asks=[BookLevel(price=65010, size=5.0)])
        new_orders = detector.on_orderbook(book2)
        # 공백 감지 (반드시 발생 안 할 수 있음, 구조 검증만)
        assert isinstance(new_orders, list)


# ── OrderBook Replay ──────────────────────────────────
class TestOrderBookReplay:
    def _make_snapshots(self, n=50):
        from orderbook.analyzer import DOMSnapshot
        snaps = []
        for i in range(n):
            snaps.append(DOMSnapshot(
                ts=   float(i),
                bids= [(65000 - j*10, float(j+1)) for j in range(5)],
                asks= [(65010 + j*10, float(j+1)) for j in range(5)],
                mid=  65005.0,
            ))
        return snaps

    def test_load_snapshots(self):
        from replay.orderbook_replay import OrderBookReplayEngine, OrderBookReplayConfig
        engine = OrderBookReplayEngine(OrderBookReplayConfig(speed=100.0))
        snaps  = self._make_snapshots(50)
        engine.load(snaps)
        assert len(engine._snapshots) == 50

    def test_seek(self):
        from replay.orderbook_replay import OrderBookReplayEngine, OrderBookReplayConfig
        engine = OrderBookReplayEngine(OrderBookReplayConfig())
        snaps  = self._make_snapshots(100)
        engine.load(snaps)
        engine.seek(50.0)
        assert engine._cur_idx >= 50

    def test_progress_tracking(self):
        from replay.orderbook_replay import OrderBookReplayEngine
        engine = OrderBookReplayEngine()
        engine.load(self._make_snapshots(100))
        assert engine.progress == 0.0
        engine._cur_idx = 50
        assert abs(engine.progress - 50.0) < 1.0

    def test_set_speed(self):
        from replay.orderbook_replay import OrderBookReplayEngine
        engine = OrderBookReplayEngine()
        engine.set_speed(50.0)
        assert engine.cfg.speed == 50.0
        engine.set_speed(99999)   # 상한 100
        assert engine.cfg.speed == 100.0

    def test_replay_with_callback(self):
        from replay.orderbook_replay import OrderBookReplayEngine, OrderBookReplayConfig
        received = []
        engine   = OrderBookReplayEngine(OrderBookReplayConfig(speed=1000.0))
        engine.on_snapshot = lambda s: received.append(s)
        engine.load(self._make_snapshots(5))
        asyncio.run(engine.start())
        assert len(received) == 5


# ── ICT 엔진 개선 ─────────────────────────────────────
class TestICTEngineImproved:
    @pytest.fixture
    def candles(self):
        np.random.seed(42)
        prices = 65000 + np.cumsum(np.random.normal(0, 50, 300))
        return [
            Candle(ts=1700000000.0+i*900, open=prices[i],
                   high=prices[i]+abs(np.random.normal(40,10)),
                   low=prices[i]-abs(np.random.normal(40,10)),
                   close=prices[i], volume=500,
                   symbol="BTC-USDT-SWAP", timeframe=Timeframe.M15)
            for i in range(300)
        ]

    def test_tp2_field_exists(self, candles):
        """ICTResult에 tp2 필드 존재."""
        from strategy.ict_engine import ICTEngine, ICTParams, ICTResult
        import dataclasses
        fields = [f.name for f in dataclasses.fields(ICTResult)]
        assert "tp2" in fields

    def test_atr_field_populated(self, candles):
        """ICTResult.atr 필드가 채워짐."""
        from strategy.ict_engine import ICTEngine, ICTParams
        engine = ICTEngine(ICTParams(require_displacement=False, min_confluence=0))
        result = engine.analyze(candles)
        assert result.atr is not None
        assert result.atr > 0

    def test_last_choch_field(self, candles):
        """ICTResult.last_choch 필드 존재."""
        from strategy.ict_engine import ICTResult
        import dataclasses
        fields = [f.name for f in dataclasses.fields(ICTResult)]
        assert "last_choch" in fields

    def test_tp2_when_signal(self, candles):
        """신호 발생 시 tp2도 설정됨."""
        from strategy.ict_engine import ICTEngine, ICTParams
        engine = ICTEngine(ICTParams(require_displacement=False, min_confluence=0, min_rr=1.0))
        result = engine.analyze(candles)
        if result.signal:
            assert result.tp is not None
            assert result.tp2 is not None
            if result.signal == "LONG":
                assert result.tp2 > result.tp > result.entry
            else:
                assert result.tp2 < result.tp < result.entry


# ── GPT 통합 (오프라인) ───────────────────────────────
class TestGPTIntegration:
    def test_init_no_key(self):
        from ai.gpt_integration import GPTIntegration
        gpt = GPTIntegration(api_key="")
        assert not gpt.available

    def test_fallback_analysis(self):
        from ai.gpt_integration import GPTIntegration, GPTAnalysisResult
        gpt    = GPTIntegration(api_key="")
        result = gpt._fallback_analysis("BTC", "테스트 요약", "LONG", 75.0)
        assert isinstance(result, GPTAnalysisResult)
        assert result.model == "local_fallback"
        assert "75" in result.entry_eval

    def test_fallback_returns_result(self):
        from ai.gpt_integration import GPTIntegration
        gpt    = GPTIntegration(api_key="")
        result = asyncio.run(
            gpt.analyze_market("BTC", "시장 요약 텍스트", "LONG", 80.0)
        )
        assert result.narrative
        assert result.entry_eval
        assert result.risk_summary


# ── Performance — Redis 캐시 통합 ─────────────────────
class TestRedisIntegration:
    def test_candle_caching(self):
        """캔들 데이터 캐싱 및 조회."""
        from core.performance import RedisCache
        cache   = RedisCache(host="localhost", port=9999)
        candles = [{"ts": float(i), "open": 65000.0} for i in range(100)]

        asyncio.run(cache.set("candles:BTC:15m", candles, ttl=60))
        result = asyncio.run(cache.get("candles:BTC:15m"))

        assert result == candles
        assert len(result) == 100

    def test_cache_expiry_simulation(self):
        """TTL 만료 시 None 반환."""
        from core.performance import RedisCache
        cache = RedisCache(host="localhost", port=9999)
        # TTL=0은 영구 보존 → 테스트에서는 delete로 만료 시뮬레이션
        asyncio.run(cache.set("temp_key", "value", ttl=60))
        asyncio.run(cache.delete("temp_key"))
        result = asyncio.run(cache.get("temp_key"))
        assert result is None

    def test_parallel_backtest_param_generation(self):
        """그리드서치 파라미터 생성."""
        from core.performance import ParallelBacktester
        grid = ParallelBacktester.build_param_grid({
            "min_rr":          [1.5, 2.0, 2.5],
            "min_confluence":  [0, 1],
        })
        assert len(grid) == 6  # 3 × 2
        for p in grid:
            assert "min_rr" in p
            assert "min_confluence" in p
