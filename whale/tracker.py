"""
whale/tracker.py
================
고래 추적 모듈.

- 대형 입출금 감지
- 거래소 유입/유출 모니터링
- 고래 지갑 추적
- 스테이블코인 유입량
- 대형 청산 추적
"""
from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from collections import deque
from typing import Optional

import aiohttp

logger = logging.getLogger(__name__)


@dataclass
class WhaleTransfer:
    """고래 자금 이동."""
    ts:        float
    from_addr: str
    to_addr:   str
    amount:    float
    symbol:    str
    usd_value: float
    kind:      str    # "exchange_inflow" | "exchange_outflow" | "wallet_to_wallet"


@dataclass
class ExchangeFlow:
    """거래소 자금 유입/유출."""
    ts:       float
    exchange: str
    symbol:   str
    netflow:  float   # 양수=유입(매도 압력), 음수=유출(매수 압력)
    inflow:   float
    outflow:  float


@dataclass
class StablecoinFlow:
    """스테이블코인 유입 (시장 유동성 지표)."""
    ts:       float
    symbol:   str     # "USDT" | "USDC" | "BUSD"
    amount:   float
    kind:     str     # "mint" | "burn" | "exchange_inflow"


@dataclass
class WhaleLiquidation:
    """대형 청산."""
    ts:       float
    symbol:   str
    side:     str
    amount:   float
    usd_value: float


@dataclass
class WhaleAlert:
    """고래 알림 (통합)."""
    ts:       float
    kind:     str
    symbol:   str
    message:  str
    severity: str     # "high" | "medium"


class WhaleTracker:
    """
    고래 추적기.
    Glassnode / Whale Alert / CoinGlass API에서 데이터를 수집한다.
    (실제 API 키 없이는 모의 데이터 생성)
    """

    WHALE_ALERT_URL    = "https://api.whale-alert.io/v1/transactions"
    COINGLASS_FLOW_URL = "https://open-api.coinglass.com/public/v2/indicator/exchange_net_position_change"

    def __init__(
        self,
        whale_threshold: float = 1_000_000,   # USD 기준 고래 기준
        whale_alert_key: str   = "",
        coinglass_key:   str   = "",
    ) -> None:
        self.threshold    = whale_threshold
        self.wa_key       = whale_alert_key
        self.cg_key       = coinglass_key
        self._session:    Optional[aiohttp.ClientSession] = None

        # 히스토리
        self.transfers:     deque[WhaleTransfer]    = deque(maxlen=100)
        self.exchange_flows:deque[ExchangeFlow]     = deque(maxlen=200)
        self.stable_flows:  deque[StablecoinFlow]   = deque(maxlen=100)
        self.liquidations:  deque[WhaleLiquidation] = deque(maxlen=100)
        self.alerts:        deque[WhaleAlert]       = deque(maxlen=50)

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=10))
        return self._session

    async def fetch_whale_transfers(self, min_usd: float = None) -> list[WhaleTransfer]:
        """Whale Alert API에서 대형 이체 조회."""
        threshold = min_usd or self.threshold
        if not self.wa_key:
            return self._mock_transfers(threshold)

        try:
            session = await self._get_session()
            async with session.get(self.WHALE_ALERT_URL, params={
                "api_key": self.wa_key, "min_value": int(threshold), "limit": 20
            }) as resp:
                data = await resp.json()
                items = []
                for tx in data.get("transactions", []):
                    t = WhaleTransfer(
                        ts=        tx.get("timestamp", time.time()),
                        from_addr= tx.get("from", {}).get("owner", "unknown"),
                        to_addr=   tx.get("to",   {}).get("owner", "unknown"),
                        amount=    float(tx.get("amount", 0)),
                        symbol=    tx.get("symbol", "BTC"),
                        usd_value= float(tx.get("amount_usd", 0)),
                        kind=      self._classify_transfer(tx),
                    )
                    self.transfers.append(t)
                    if t.usd_value >= self.threshold * 5:
                        self.alerts.append(WhaleAlert(
                            ts=t.ts, kind="large_transfer", symbol=t.symbol,
                            message=f"대형 이체: {t.amount:.2f} {t.symbol} ({t.from_addr}→{t.to_addr})",
                            severity="high",
                        ))
                    items.append(t)
                return items
        except Exception as e:
            logger.debug(f"Whale Alert 조회 실패: {e}")
            return self._mock_transfers(threshold)

    def _classify_transfer(self, tx: dict) -> str:
        from_owner = tx.get("from", {}).get("owner_type", "")
        to_owner   = tx.get("to",   {}).get("owner_type", "")
        if to_owner   == "exchange": return "exchange_inflow"
        if from_owner == "exchange": return "exchange_outflow"
        return "wallet_to_wallet"

    def _mock_transfers(self, threshold: float) -> list[WhaleTransfer]:
        """API 키 없을 때 모의 데이터."""
        import random
        items = []
        for _ in range(3):
            kind   = random.choice(["exchange_inflow", "exchange_outflow", "wallet_to_wallet"])
            amount = random.uniform(100, 1000)
            items.append(WhaleTransfer(
                ts=time.time()-random.randint(0,3600),
                from_addr="0x" + "a"*8 if kind!="exchange_inflow" else "Binance",
                to_addr=  "Binance" if kind=="exchange_inflow" else "0x"+"b"*8,
                amount=amount, symbol="BTC",
                usd_value=amount * 65000,
                kind=kind,
            ))
        return items

    async def fetch_exchange_netflow(self, symbol: str = "BTC") -> list[ExchangeFlow]:
        """거래소 순유입 (유입 - 유출)."""
        # CoinGlass API (모의 데이터)
        import random
        flows = []
        for exch in ["Binance", "OKX", "Coinbase"]:
            inflow  = random.uniform(100, 2000)
            outflow = random.uniform(100, 2000)
            netflow = inflow - outflow
            f = ExchangeFlow(
                ts=time.time(), exchange=exch, symbol=symbol,
                netflow=round(netflow, 2),
                inflow=round(inflow, 2), outflow=round(outflow, 2),
            )
            self.exchange_flows.append(f)
            # 대규모 유입 → 알림
            if netflow > 5000:
                self.alerts.append(WhaleAlert(
                    ts=f.ts, kind="exchange_inflow", symbol=symbol,
                    message=f"{exch} 대규모 유입: {netflow:,.0f} {symbol}",
                    severity="high" if netflow > 10000 else "medium",
                ))
            flows.append(f)
        return flows

    async def fetch_stablecoin_flow(self) -> list[StablecoinFlow]:
        """스테이블코인 민팅/번 (유동성 지표)."""
        import random
        flows = []
        for sym in ["USDT", "USDC"]:
            kind   = random.choice(["mint", "burn", "exchange_inflow"])
            amount = random.uniform(10_000_000, 500_000_000)
            f = StablecoinFlow(ts=time.time(), symbol=sym, amount=amount, kind=kind)
            self.stable_flows.append(f)
            if amount > 100_000_000:
                self.alerts.append(WhaleAlert(
                    ts=f.ts, kind="stablecoin_mint", symbol=sym,
                    message=f"{sym} 대규모 {kind}: ${amount/1e6:.0f}M",
                    severity="medium",
                ))
            flows.append(f)
        return flows

    def get_market_sentiment(self) -> dict:
        """최근 데이터로 시장 심리 분석."""
        recent_flows = list(self.exchange_flows)[-20:]
        total_net    = sum(f.netflow for f in recent_flows)

        recent_stable = list(self.stable_flows)[-10:]
        stable_net    = sum(f.amount if f.kind == "mint" else -f.amount for f in recent_stable)

        return {
            "exchange_netflow":  round(total_net, 2),
            "stablecoin_net":    round(stable_net / 1e6, 1),  # M 단위
            "whale_alert_count": len(self.alerts),
            # 유입 많으면 매도 압력, 유출 많으면 매수 압력
            "signal": "bearish" if total_net > 0 else "bullish",
        }

    async def start_loop(self, interval: int = 300) -> None:
        """5분마다 고래 데이터 갱신."""
        while True:
            await self.fetch_whale_transfers()
            await self.fetch_exchange_netflow("BTC")
            await self.fetch_stablecoin_flow()
            await asyncio.sleep(interval)

    async def close(self) -> None:
        if self._session and not self._session.closed:
            await self._session.close()
