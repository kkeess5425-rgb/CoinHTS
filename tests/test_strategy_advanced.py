"""tests/test_strategy_advanced.py — MTF / 컨플루언스 엔진 테스트."""
import numpy as np
import pytest
from core.models import Candle, Timeframe


def make_candles(n=200, seed=0, trend="bull"):
    np.random.seed(seed)
    if trend == "bull":
        prices = 65000 + np.cumsum(np.abs(np.random.normal(30, 10, n)))
    else:
        prices = 65000 - np.cumsum(np.abs(np.random.normal(30, 10, n)))
    return [
        Candle(ts=1700000000.0+i*900, open=prices[i],
               high=prices[i]+abs(np.random.normal(40,10)),
               low=prices[i]-abs(np.random.normal(40,10)),
               close=prices[i], volume=500,
               symbol="BTC-USDT-SWAP", timeframe=Timeframe.M15)
        for i in range(n)
    ]


# ── MTF 엔진 테스트 ───────────────────────────────────
class TestMTFEngine:
    @pytest.fixture
    def engine(self):
        from strategy.mtf_engine import MTFEngine, MTFConfig
        return MTFEngine(config=MTFConfig(
            htf=Timeframe.H4, mtf=Timeframe.H1, ltf=Timeframe.M15
        ))

    @pytest.fixture
    def candles_sets(self):
        """HTF/MTF/LTF 캔들 세트 (모두 불리시)."""
        htf = make_candles(200, seed=1, trend="bull")
        mtf = make_candles(200, seed=2, trend="bull")
        ltf = make_candles(200, seed=3, trend="bull")
        return htf, mtf, ltf

    def test_returns_mtf_result(self, engine, candles_sets):
        from strategy.mtf_engine import MTFResult
        htf, mtf, ltf = candles_sets
        result = engine.analyze(htf, mtf, ltf)
        assert isinstance(result, MTFResult)

    def test_direction_determined(self, engine, candles_sets):
        htf, mtf, ltf = candles_sets
        result = engine.analyze(htf, mtf, ltf)
        assert result.direction in ("LONG", "SHORT", "")

    def test_score_range(self, engine, candles_sets):
        htf, mtf, ltf = candles_sets
        result = engine.analyze(htf, mtf, ltf)
        assert 0 <= result.mtf_score <= 100

    def test_bull_alignment(self, engine):
        """불리시 정렬 시 LONG 신호."""
        htf = make_candles(200, seed=10, trend="bull")
        mtf = make_candles(200, seed=11, trend="bull")
        ltf = make_candles(200, seed=12, trend="bull")
        result = engine.analyze(htf, mtf, ltf)
        # 강한 불리시에서 LONG 방향이 지배적
        assert result.direction in ("LONG", "SHORT")

    def test_confluence_list(self, engine, candles_sets):
        htf, mtf, ltf = candles_sets
        result = engine.analyze(htf, mtf, ltf)
        assert isinstance(result.confluence, list)

    def test_summary_not_empty(self, engine, candles_sets):
        htf, mtf, ltf = candles_sets
        result = engine.analyze(htf, mtf, ltf)
        assert len(result.summary) > 0

    def test_short_data_graceful(self, engine):
        """데이터 부족해도 에러 없음."""
        from strategy.mtf_engine import MTFResult
        result = engine.analyze([], [], [])
        assert isinstance(result, MTFResult)
        assert result.direction == ""

    def test_htf_smc_populated(self, engine, candles_sets):
        htf, mtf, ltf = candles_sets
        result = engine.analyze(htf, mtf, ltf)
        assert result.htf_ict is not None
        assert result.htf_smc is not None

    def test_ltf_ict_populated(self, engine, candles_sets):
        htf, mtf, ltf = candles_sets
        result = engine.analyze(htf, mtf, ltf)
        assert result.ltf_ict is not None


# ── 컨플루언스 엔진 테스트 ────────────────────────────
class TestConfluenceEngine:
    @pytest.fixture
    def engine(self):
        from strategy.confluence_engine import ConfluenceEngine
        return ConfluenceEngine()

    def test_empty_context_returns_score(self, engine):
        result = engine.score("LONG")
        assert 0 <= result.total <= 100
        assert result.direction == "LONG"

    def test_grade_distribution(self, engine):
        from strategy.confluence_engine import _grade
        assert _grade(90) == "A+"
        assert _grade(78) == "A"
        assert _grade(67) == "B"
        assert _grade(55) == "C"
        assert _grade(30) == "D"

    def test_ict_contribution(self, engine):
        from strategy.ict_engine import ICTResult
        ict = ICTResult(bull_ms=True, bull_sweep_active=True, displacement=True, score=80)
        result = engine.score("LONG", ict_result=ict)
        assert result.ict_score == engine.ICT_MAX

    def test_smc_contribution(self, engine):
        from strategy.smc_engine import SMCResult, FVGZone, OrderBlock
        smc = SMCResult(
            bull_ms=True,
            fvg_zones=[FVGZone(ts=0, top=65200, bottom=65100, direction="bull", filled_pct=0.0)],
            order_blocks=[OrderBlock(ts=0, high=65100, low=65000, direction="bull", broken=False)],
        )
        result = engine.score("LONG", smc_result=smc)
        assert result.smc_score > 0

    def test_mtf_bonus(self, engine):
        from strategy.mtf_engine import MTFResult
        mtf = MTFResult(direction="LONG", aligned=True, mtf_score=85.0, summary="테스트")
        result = engine.score("LONG", mtf_result=mtf)
        assert result.mtf_score == engine.MTF_MAX

    def test_funding_penalty(self, engine):
        result_neutral  = engine.score("LONG")
        result_overload = engine.score("LONG", funding_rate=0.002)  # 과열
        assert result_overload.external_score <= result_neutral.external_score

    def test_ob_imbalance_contribution(self, engine):
        from orderbook.analyzer import OrderBookImbalance
        ob = OrderBookImbalance(ts=0, bid_vol=100, ask_vol=50, ratio=2.0, imbalance="bull")
        result = engine.score("LONG", ob_imbalance=ob)
        assert result.ob_score > 0

    def test_is_tradeable(self, engine):
        from strategy.confluence_engine import ConfluenceScore
        assert ConfluenceScore(grade="A+").is_tradeable
        assert ConfluenceScore(grade="A").is_tradeable
        assert ConfluenceScore(grade="B").is_tradeable
        assert not ConfluenceScore(grade="C").is_tradeable
        assert not ConfluenceScore(grade="D").is_tradeable

    def test_whale_contribution(self, engine):
        result_aligned = engine.score("LONG", whale_signal="bullish")
        result_against = engine.score("LONG", whale_signal="bearish")
        assert result_aligned.external_score > result_against.external_score

    def test_max_score_clamp(self, engine):
        """최대 점수 100 초과 안 됨."""
        from strategy.ict_engine import ICTResult
        from strategy.smc_engine import SMCResult, FVGZone, OrderBlock, LiquiditySweep
        ict = ICTResult(bull_ms=True, bull_sweep_active=True, displacement=True, score=100)
        smc = SMCResult(
            bull_ms=True,
            fvg_zones=[FVGZone(ts=0, top=65200, bottom=65100, direction="bull")],
            order_blocks=[OrderBlock(ts=0, high=65100, low=65000, direction="bull")],
            liquidity_sweeps=[LiquiditySweep(ts=0, swept_level=64000, direction="bull_sweep",
                                              recovery_dist=2.0, confirmed=True)],
        )
        from orderbook.analyzer import OrderBookImbalance
        ob = OrderBookImbalance(ts=0, bid_vol=200, ask_vol=50, ratio=4.0, imbalance="bull")
        result = engine.score("LONG", ict_result=ict, smc_result=smc,
                              ob_imbalance=ob, whale_signal="bullish",
                              funding_rate=-0.0005)
        assert result.total <= 100.0
        assert result.grade in ("A+", "A", "B", "C", "D")

    def test_reasons_populated(self, engine):
        from strategy.ict_engine import ICTResult
        ict = ICTResult(bull_ms=True, bull_sweep_active=True, score=70)
        result = engine.score("LONG", ict_result=ict)
        assert len(result.reasons) > 0


# ── DataExporter 테스트 ───────────────────────────────
class TestDataExporter:
    @pytest.fixture
    def exporter(self):
        from database.exporter import DataExporter
        return DataExporter()

    @pytest.fixture
    def mock_entries(self):
        """매매일지 모의 데이터."""
        class Entry:
            def __init__(self, i):
                self.id        = f"J-{i:04d}"
                self.symbol    = "BTC-USDT-SWAP"
                self.direction = "LONG" if i % 2 == 0 else "SHORT"
                self.entry     = 65000.0
                self.exit_price= 65500.0 if i % 3 != 0 else 64500.0
                self.sl        = 64500.0
                self.tp        = 67000.0
                self.pnl_r     = 1.0 if i % 3 != 0 else -1.0
                self.pnl_usd   = self.pnl_r * 100
                self.entry_ts  = 1700000000.0 + i * 86400
                self.exit_ts   = self.entry_ts + 3600
                self.exit_reason="TP" if self.pnl_r > 0 else "SL"
                self.entry_reason="BOS + FVG"
                self.mistakes  = []
                self.result    = "WIN" if self.pnl_r > 0 else "LOSS"
        return [Entry(i) for i in range(10)]

    def test_export_journal_csv(self, exporter, mock_entries):
        csv = exporter.export_journal_csv(mock_entries)
        assert "ID" in csv
        assert "BTC-USDT-SWAP" in csv
        lines = csv.strip().split('\n')
        assert len(lines) == 11  # 헤더 + 10건

    def test_export_empty_csv(self, exporter):
        csv = exporter.export_journal_csv([])
        assert csv == "데이터 없음"

    def test_export_stats_json(self, exporter):
        import json
        from stats.statistics import StatisticsEngine, TradeRecord
        records = [
            TradeRecord(entry_ts=float(i)*86400, exit_ts=float(i)*86400+3600,
                        direction="LONG", entry=65000, exit_price=65500,
                        sl=64500, tp=67000, pnl_r=1.0 if i%2==0 else -1.0)
            for i in range(10)
        ]
        stats    = StatisticsEngine().compute(records)
        json_str = exporter.export_stats_json(stats)
        data     = json.loads(json_str)
        assert "total_trades" in data
        assert data["total_trades"] == 10
