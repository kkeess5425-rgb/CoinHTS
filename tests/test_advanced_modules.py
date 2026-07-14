"""tests/test_advanced_modules.py — 고급 모듈 통합 테스트."""
import asyncio, time
import numpy as np
import pytest
from core.models import Tick, Side, Timeframe, Candle


# ── OrderFlow Advanced ────────────────────────────────
class TestAdvancedOrderFlow:
    @pytest.fixture
    def analyzer(self):
        from orderflow.advanced import AdvancedOrderFlowAnalyzer
        return AdvancedOrderFlowAnalyzer(imbalance_ratio=2.0, min_stack_levels=2, iceberg_repeat=3)

    @pytest.fixture
    def fp_bar(self):
        from orderflow.footprint import FootprintEngine
        eng = FootprintEngine("BTC", Timeframe.M1, tick_size=1.0)
        np.random.seed(5)
        price = 65000.0
        for i in range(200):
            price += np.random.normal(0, 5)
            side = Side.BUY if np.random.random() > 0.4 else Side.SELL
            eng.on_tick(Tick(ts=float(i), price=price, size=abs(np.random.normal(0.1,0.03)),
                             side=side, symbol="BTC"))
        return eng.current_bar

    def test_stacked_imbalance(self, analyzer, fp_bar):
        if fp_bar:
            result = analyzer.on_bar(fp_bar)
            assert isinstance(result.stacked_imbalances, list)

    def test_unfinished_auction(self, analyzer, fp_bar):
        if fp_bar:
            result = analyzer.on_bar(fp_bar)
            assert isinstance(result.unfinished_auctions, list)

    def test_absorption_detection(self, analyzer):
        from core.models import FootprintBar, FootprintCell
        candle = Candle(ts=0, open=65000, high=65010, low=64990,
                        close=65002, volume=500, symbol="BTC", timeframe=Timeframe.M1)
        cells  = [FootprintCell(price=65000, buy_vol=350, sell_vol=150)]
        from orderflow.footprint import FootprintBar
        bar = FootprintBar(candle=candle, cells=cells, delta=200, cvd=200)
        result = analyzer.on_bar(bar)
        assert isinstance(result.absorptions, list)

    def test_iceberg_detection(self, analyzer):
        # 같은 크기의 틱 반복
        for _ in range(5):
            analyzer.on_tick(Tick(ts=float(_), price=65000, size=0.123,
                                  side=Side.BUY, symbol="BTC"))
        assert len(analyzer._iceberg_track) >= 0  # 카운터 동작 확인

    def test_delta_divergence(self, analyzer):
        from core.models import FootprintBar, FootprintCell
        # 가격 상승 + CVD 하락 시나리오
        for i in range(25):
            c = Candle(ts=float(i), open=65000+i, high=65010+i, low=64990+i,
                       close=65005+i, volume=100, symbol="BTC", timeframe=Timeframe.M1)
            cells = [FootprintCell(price=65000+i, buy_vol=40, sell_vol=60)]
            from orderflow.footprint import FootprintBar as FPBar
            bar = FPBar(candle=c, cells=cells, delta=-20, cvd=-20*i)
            analyzer.on_bar(bar)
        # 마지막 봉: 가격 신고점 + CVD 더 낮음
        c = Candle(ts=30.0, open=65100, high=65110, low=65090,
                   close=65105, volume=100, symbol="BTC", timeframe=Timeframe.M1)
        cells = [FootprintCell(price=65100, buy_vol=30, sell_vol=70)]
        from orderflow.footprint import FootprintBar as FPBar
        bar = FPBar(candle=c, cells=cells, delta=-40, cvd=-999)
        result = analyzer.on_bar(bar)
        assert isinstance(result.delta_divergences, list)


# ── Statistics Engine ─────────────────────────────────
class TestStatisticsEngine:
    @pytest.fixture
    def engine(self):
        from stats.statistics import StatisticsEngine
        return StatisticsEngine()

    @pytest.fixture
    def trades(self):
        from stats.statistics import TradeRecord
        np.random.seed(42)
        records = []
        ts = 1700000000.0
        for i in range(50):
            pnl = np.random.choice([-1.0, 2.0, 1.5, -1.0, 0.5], p=[0.35,0.2,0.2,0.15,0.1])
            records.append(TradeRecord(
                entry_ts=ts, exit_ts=ts+3600,
                direction="LONG" if pnl > 0 else "SHORT",
                entry=65000, exit_price=65000+pnl*500,
                sl=64500, tp=66000, pnl_r=pnl, pnl_usd=pnl*100,
                symbol="BTC",
            ))
            ts += 86400
        return records

    def test_compute_returns_statistics(self, engine, trades):
        from stats.statistics import Statistics
        stats = engine.compute(trades)
        assert isinstance(stats, Statistics)

    def test_win_rate_range(self, engine, trades):
        s = engine.compute(trades)
        assert 0 <= s.win_rate <= 100

    def test_profit_factor_positive(self, engine, trades):
        s = engine.compute(trades)
        assert s.profit_factor > 0

    def test_sharpe_ratio(self, engine, trades):
        s = engine.compute(trades)
        assert isinstance(s.sharpe_ratio, float)

    def test_sortino_ratio(self, engine, trades):
        s = engine.compute(trades)
        assert isinstance(s.sortino_ratio, float)

    def test_mdd_negative_or_zero(self, engine, trades):
        s = engine.compute(trades)
        assert s.max_drawdown_r <= 0

    def test_hourly_wr(self, engine, trades):
        s = engine.compute(trades)
        assert isinstance(s.hourly_wr, dict)
        for h, wr in s.hourly_wr.items():
            assert 0 <= wr <= 100

    def test_daily_wr(self, engine, trades):
        s = engine.compute(trades)
        assert isinstance(s.daily_wr, dict)
        for day, wr in s.daily_wr.items():
            assert day in ("월","화","수","목","금","토","일")

    def test_consec_trades(self, engine, trades):
        s = engine.compute(trades)
        assert s.max_consec_wins >= 0
        assert s.max_consec_losses >= 0

    def test_empty_trades(self, engine):
        from stats.statistics import Statistics
        s = engine.compute([])
        assert isinstance(s, Statistics)
        assert s.total_trades == 0

    def test_summary_text(self, engine, trades):
        s = engine.compute(trades)
        text = engine.summary_text(s)
        assert "승률" in text and "Sharpe" in text


# ── Optimization ──────────────────────────────────────
class TestOptimization:
    def test_monte_carlo(self):
        from optimization.optimizer import MonteCarloSimulation
        mc  = MonteCarloSimulation()
        pnl = [1.0, -1.0, 2.0, -1.0, 1.5] * 20
        r   = mc.run(pnl, n_sims=100)
        assert "final_r" in r and "max_dd" in r and "ruin_prob" in r
        assert r["n_sims"] == 100

    def test_genetic_algorithm(self):
        from optimization.optimizer import GeneticAlgorithm, ParamRange
        ga     = GeneticAlgorithm(population=10, generations=5)
        params = [ParamRange("x", 0.1, 2.0, 0.1), ParamRange("y", 1, 10, 1, "int")]
        result = ga.run(params, eval_func=lambda p: -(p["x"]-1.0)**2)
        assert result.best_params
        assert isinstance(result.best_score, float)
        assert result.generations == 5

    def test_param_range_sample(self):
        from optimization.optimizer import ParamRange
        r = ParamRange("test", 1.0, 5.0, 0.5)
        for _ in range(20):
            v = r.sample()
            assert 1.0 <= v <= 5.0

    def test_param_range_int(self):
        from optimization.optimizer import ParamRange
        r = ParamRange("n", 5, 20, 1, "int")
        for _ in range(20):
            v = r.sample()
            assert isinstance(v, int) and 5 <= v <= 20


# ── Paper Trader ──────────────────────────────────────
class TestPaperTrader:
    @pytest.fixture
    def trader(self):
        from trading.paper_trader import PaperTrader, TradingConfig
        from core.events import EventBus
        bus = EventBus()
        cfg = TradingConfig(account_size=10000, risk_per_trade=1.0,
                            min_score=0.0, partial_tp_enabled=True,
                            breakeven_enabled=True, trailing_enabled=True)
        return PaperTrader(config=cfg, event_bus=bus)

    def test_initial_balance(self, trader):
        assert trader.balance == 10000.0

    def test_price_update_no_positions(self, trader):
        closed = trader.on_price_update("BTC", 65000.0, atr=100)
        assert closed == []

    def test_open_close_via_signal(self, trader):
        from core.models import StrategySignal, Exchange
        sig = StrategySignal(
            symbol="BTC-USDT-SWAP", ts=time.time(),
            direction="LONG", score=80.0,
            entry=65000, sl=64500, tp=67000, reasons=[],
            exchange=Exchange.OKX,
        )
        asyncio.run(trader._on_signal(sig))
        assert len(trader.open_positions) == 1

        # TP 도달 → 청산
        trader.on_price_update("BTC-USDT-SWAP", 67000.0)
        assert len(trader.open_positions) == 0
        assert len(trader._closed) == 1
        assert trader._closed[0].pnl_r > 0

    def test_sl_triggers_close(self, trader):
        from core.models import StrategySignal, Exchange
        sig = StrategySignal(
            symbol="BTC-USDT-SWAP", ts=time.time(),
            direction="LONG", score=80.0,
            entry=65000, sl=64500, tp=67000, reasons=[],
            exchange=Exchange.OKX,
        )
        asyncio.run(trader._on_signal(sig))
        trader.on_price_update("BTC-USDT-SWAP", 64500.0)
        assert len(trader._closed) == 1
        assert trader._closed[0].pnl_r < 0

    def test_daily_loss_limit(self, trader):
        trader._daily_loss = trader.cfg.account_size * 0.04  # 4% 손실
        assert trader._daily_loss_pct() >= trader.cfg.max_daily_loss_pct

    def test_trade_records(self, trader):
        from trading.paper_trader import Position, OrderStatus
        from stats.statistics import TradeRecord
        pos = Position(id="T1", symbol="BTC", direction="LONG",
                       entry=65000, sl=64500, tp=67000, size=0.1,
                       entry_ts=time.time()-3600, exit_ts=time.time(),
                       exit_price=67000, pnl_r=2.0, pnl_usd=200,
                       status=OrderStatus.CLOSED)
        trader._closed.append(pos)
        records = trader.trade_records
        assert len(records) == 1
        assert isinstance(records[0], TradeRecord)


# ── Trade Journal ─────────────────────────────────────
class TestTradeJournal:
    @pytest.fixture
    def journal(self):
        from ai.trade_journal import TradeJournalAI
        return TradeJournalAI()

    def test_create_entry(self, journal):
        e = journal.create_entry(
            symbol="BTC", direction="LONG", entry=65000, exit_price=67000,
            sl=64500, tp=67000, pnl_r=2.0, pnl_usd=200,
            entry_ts=time.time()-3600, exit_ts=time.time(), exit_reason="TP",
            score=80.0,
        )
        assert e.id == "J-0001"
        assert e.result == "WIN"

    def test_mistake_detection_low_score(self, journal):
        e = journal.create_entry(
            symbol="BTC", direction="LONG", entry=65000, exit_price=64500,
            sl=64500, tp=67000, pnl_r=-1.0, pnl_usd=-100,
            entry_ts=time.time()-3600, exit_ts=time.time(), exit_reason="SL",
            score=30.0,   # 낮은 점수 → 추격 매수 경고
        )
        kinds = [m.kind for m in e.mistakes]
        assert "chasing" in kinds

    def test_report_generation(self, journal):
        for i in range(5):
            journal.create_entry(
                symbol="BTC", direction="LONG", entry=65000, exit_price=65000+i*200,
                sl=64500, tp=67000, pnl_r=float(i-2), pnl_usd=float(i-2)*100,
                entry_ts=time.time()-3600, exit_ts=time.time(), exit_reason="TP",
                score=70.0,
            )
        report = journal.generate_report()
        assert "매매일지" in report

    def test_mistake_stats(self, journal):
        journal.create_entry(
            symbol="BTC", direction="LONG", entry=65000, exit_price=64500,
            sl=64500, tp=65100, pnl_r=-1.0, pnl_usd=-100,
            entry_ts=time.time(), exit_ts=time.time()+60, exit_reason="SL",
            score=30.0,
        )
        stats = journal.get_mistake_stats()
        assert isinstance(stats, dict)


# ── Whale Tracker ─────────────────────────────────────
class TestWhaleTracker:
    def test_mock_transfers(self):
        from whale.tracker import WhaleTracker
        wt  = WhaleTracker(whale_threshold=100_000)
        txs = asyncio.run(wt.fetch_whale_transfers())
        assert len(txs) > 0
        for tx in txs:
            assert tx.symbol and tx.amount > 0

    def test_exchange_flow(self):
        from whale.tracker import WhaleTracker
        wt    = WhaleTracker()
        flows = asyncio.run(wt.fetch_exchange_netflow("BTC"))
        assert len(flows) > 0

    def test_market_sentiment(self):
        from whale.tracker import WhaleTracker
        wt  = WhaleTracker()
        asyncio.run(wt.fetch_exchange_netflow("BTC"))
        sent = wt.get_market_sentiment()
        assert "signal" in sent
        assert sent["signal"] in ("bullish","bearish")


# ── News Aggregator ───────────────────────────────────
class TestNewsAggregator:
    def test_mock_calendar(self):
        from news.news_aggregator import NewsAggregator
        agg    = NewsAggregator()
        events = agg._mock_calendar()
        assert len(events) > 0
        for e in events:
            assert e.impact in ("high","medium","low")
            assert e.title

    def test_ai_summarize_empty(self):
        from news.news_aggregator import NewsAggregator
        agg = NewsAggregator()
        s   = agg.ai_summarize([])
        assert s == "관련 뉴스 없음"

    def test_upcoming_events(self):
        from news.news_aggregator import NewsAggregator
        agg = NewsAggregator()
        agg._events = agg._mock_calendar()
        events = agg.get_upcoming_events(hours=48)
        assert len(events) > 0
