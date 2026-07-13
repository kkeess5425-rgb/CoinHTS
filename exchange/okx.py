"""
exchange/okx.py
===============
OKX REST API 클라이언트.
공개 API(시세)와 인증 API(주문)를 모두 지원한다.
aiohttp 기반 비동기 HTTP, 자동 retry 및 rate limit 처리.
"""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Optional

import aiohttp

from core.models import (
    Candle, FundingData, InstType, LiquidationData,
    OIData, Side, SymbolInfo, Timeframe, Exchange
)
from exchange.base import BaseExchange

logger = logging.getLogger(__name__)

OKX_BASE = "https://www.okx.com"
RATE_LIMIT_DELAY = 0.05   # 초 (20req/s 제한)


class OKXExchange(BaseExchange):
    """OKX 거래소 REST API 클라이언트."""

    def __init__(
        self,
        api_key:    str = "",
        api_secret: str = "",
        passphrase: str = "",
        testnet:    bool = False,
    ) -> None:
        self._api_key    = api_key
        self._api_secret = api_secret
        self._passphrase = passphrase
        self._base_url   = "https://www.okx.com" if not testnet else "https://www.okx.com"  # OKX는 같은 URL, 헤더로 구분
        self._session: Optional[aiohttp.ClientSession] = None
        self._last_request: float = 0.0

    @property
    def name(self) -> str:
        return "OKX"

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=15),
                headers={"Content-Type": "application/json"},
            )
        return self._session

    async def _get(self, path: str, params: dict = {}) -> dict:
        """Rate limit 준수 GET 요청."""
        # rate limit
        elapsed = time.time() - self._last_request
        if elapsed < RATE_LIMIT_DELAY:
            await asyncio.sleep(RATE_LIMIT_DELAY - elapsed)
        self._last_request = time.time()

        session = await self._get_session()
        url = self._base_url + path
        for attempt in range(3):
            try:
                async with session.get(url, params=params) as resp:
                    data = await resp.json()
                    if data.get("code") != "0":
                        logger.warning(f"OKX API 오류: {data.get('msg')} ({path})")
                        return {}
                    return data
            except aiohttp.ClientError as e:
                logger.warning(f"OKX 요청 실패 ({attempt+1}/3): {e}")
                if attempt < 2:
                    await asyncio.sleep(1.0 * (attempt + 1))
        return {}

    async def ping(self) -> bool:
        data = await self._get("/api/v5/public/time")
        return bool(data.get("data"))

    # ── 캔들 ─────────────────────────────────────────
    async def get_candles(
        self,
        symbol: str,
        timeframe: Timeframe,
        limit: int = 300,
        after: Optional[int] = None,
    ) -> list[Candle]:
        """
        OKX 캔들 조회.
        after: 이 타임스탬프(ms) 이전 데이터 반환 (페이지네이션용).
        300봉 초과 시 자동으로 history-candles 폴백.
        """
        bar = timeframe.to_okx()
        params: dict = {"instId": symbol, "bar": bar, "limit": str(min(limit, 300))}
        if after is not None:
            params["after"] = str(after)

        # 1차: /candles (최근 1440봉까지)
        data = await self._get("/api/v5/market/candles", params)
        rows = data.get("data", [])

        # 2차 폴백: /history-candles (더 오래된 데이터)
        if not rows and after is not None:
            data2 = await self._get("/api/v5/market/history-candles", {**params, "limit": str(min(limit, 100))})
            rows = data2.get("data", [])

        return self._parse_candles(rows, symbol, timeframe)

    def _parse_candles(self, rows: list, symbol: str, tf: Timeframe) -> list[Candle]:
        candles = []
        for row in rows:
            try:
                candles.append(Candle(
                    ts=        int(row[0]) / 1000.0,
                    open=      float(row[1]),
                    high=      float(row[2]),
                    low=       float(row[3]),
                    close=     float(row[4]),
                    volume=    float(row[5]),
                    symbol=    symbol,
                    timeframe= tf,
                    confirmed= row[8] == "1" if len(row) > 8 else False,
                ))
            except (IndexError, ValueError) as e:
                logger.debug(f"캔들 파싱 오류: {e}")
        # OKX는 최신→과거 순으로 반환 → 역순 정렬
        candles.reverse()
        return candles

    async def get_candles_paged(
        self,
        symbol: str,
        timeframe: Timeframe,
        total: int = 1500,
    ) -> list[Candle]:
        """여러 페이지를 순차 요청해서 total 봉 수 확보."""
        all_candles: list[Candle] = []
        after: Optional[int] = None

        while len(all_candles) < total:
            batch = await self.get_candles(symbol, timeframe, 300, after)
            if not batch:
                break
            all_candles = batch + all_candles
            # 가장 오래된 캔들의 ts를 다음 after로
            after = int(batch[0].ts * 1000)
            if len(batch) < 300:
                break
            await asyncio.sleep(RATE_LIMIT_DELAY)

        return all_candles[-total:] if len(all_candles) > total else all_candles

    # ── 심볼 ─────────────────────────────────────────
    async def get_symbols(self) -> list[SymbolInfo]:
        results: list[SymbolInfo] = []
        for inst_type in ["SWAP", "SPOT"]:
            data = await self._get("/api/v5/public/instruments", {"instType": inst_type})
            for row in data.get("data", []):
                try:
                    results.append(SymbolInfo(
                        symbol=    row["instId"],
                        base=      row["baseCcy"] if inst_type == "SPOT" else row.get("ctValCcy",""),
                        quote=     row["quoteCcy"] if inst_type == "SPOT" else row.get("settleCcy",""),
                        inst_type= InstType.SWAP if inst_type == "SWAP" else InstType.SPOT,
                        exchange=  Exchange.OKX,
                        tick_size= float(row.get("tickSz", 0.01)),
                        lot_size=  float(row.get("lotSz",  0.01)),
                        min_size=  float(row.get("minSz",  0.01)),
                    ))
                except Exception:
                    pass
        return results

    # ── OI / 펀딩 / 청산 ──────────────────────────────
    async def get_oi(self, symbol: str) -> OIData:
        data = await self._get("/api/v5/public/open-interest", {"instId": symbol})
        row  = (data.get("data") or [{}])[0]
        return OIData(
            symbol=  symbol,
            ts=      int(row.get("ts", 0)) / 1000.0,
            oi=      float(row.get("oi", 0)),
            oi_ccy=  float(row.get("oiCcy", 0)),
        )

    async def get_funding(self, symbol: str) -> FundingData:
        data = await self._get("/api/v5/public/funding-rate", {"instId": symbol})
        row  = (data.get("data") or [{}])[0]
        return FundingData(
            symbol=       symbol,
            ts=           int(row.get("ts", 0)) / 1000.0,
            funding_rate= float(row.get("fundingRate", 0)),
            next_rate=    float(row["nextFundingRate"]) if row.get("nextFundingRate") else None,
            funding_time= int(row["fundingTime"]) / 1000.0 if row.get("fundingTime") else None,
        )

    async def get_liquidations(self, symbol: str, limit: int = 100) -> list[LiquidationData]:
        results: list[LiquidationData] = []
        for state in ["unfilled", "filled"]:
            data = await self._get("/api/v5/public/liquidation-orders", {
                "instType": "SWAP", "instId": symbol, "state": state, "limit": "100",
            })
            for record in data.get("data", []):
                for detail in record.get("details", []):
                    try:
                        results.append(LiquidationData(
                            symbol= symbol,
                            ts=     int(detail.get("ts", 0)) / 1000.0,
                            side=   Side.BUY if detail.get("side") == "buy" else Side.SELL,
                            price=  float(detail.get("bkPx", 0)),
                            size=   float(detail.get("sz", 0)),
                        ))
                    except Exception:
                        pass
        return results

    async def close(self) -> None:
        if self._session and not self._session.closed:
            await self._session.close()
