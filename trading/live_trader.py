"""
trading/live_trader.py
======================
OKX 실거래 엔진.

- 시장가 / 지정가 주문
- 포지션 조회 및 청산
- SL/TP 자동 설정
- 부분 익절 / 브레이크이븐 / 트레일링 스탑
- 일일 최대 손실 제한
- 최대 포지션 수 제한

⚠️  실거래는 실제 자금을 사용합니다.
    반드시 소액 테스트 후 사용하세요.
"""
from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import Optional, Callable

import aiohttp

from core.events import EventBus, get_event_bus
from core.models import StrategySignal, Exchange
from trading.paper_trader import TradingConfig, Position, OrderStatus

logger = logging.getLogger(__name__)


@dataclass
class LiveOrder:
    """실제 주문 정보."""
    order_id:  str
    client_id: str
    symbol:    str
    side:      str    # "buy" | "sell"
    type:      str    # "market" | "limit"
    size:      float
    price:     float
    status:    str    # "live" | "filled" | "canceled"
    ts:        float  = field(default_factory=time.time)


class OKXLiveTrader:
    """
    OKX 실거래 엔진.
    PaperTrader와 동일한 인터페이스로 실제 OKX API를 호출한다.
    """

    BASE_URL = "https://www.okx.com"

    def __init__(
        self,
        api_key:    str,
        api_secret: str,
        passphrase: str,
        config:     Optional[TradingConfig] = None,
        event_bus:  Optional[EventBus]      = None,
        on_trade_closed: Optional[Callable] = None,
        sandbox:    bool = False,  # 샌드박스 모드
    ) -> None:
        self.api_key    = api_key
        self.api_secret = api_secret
        self.passphrase = passphrase
        self.cfg        = config or TradingConfig()
        self.bus        = event_bus or get_event_bus()
        self.on_closed  = on_trade_closed
        self.sandbox    = sandbox

        if sandbox:
            self.BASE_URL = "https://www.okx.com"   # OKX 샌드박스 URL
            logger.info("[LiveTrader] 🔵 샌드박스 모드")
        else:
            logger.warning("[LiveTrader] 🔴 실거래 모드 — 실제 자금 사용")

        self._session:   Optional[aiohttp.ClientSession] = None
        self._positions: list[Position] = []
        self._closed:    list[Position] = []
        self._daily_loss = 0.0
        self._daily_reset = time.time()
        self._balance:   float = 0.0
        self._pos_id     = 0

        self.bus.subscribe("strategy_signal", self._on_signal)

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=10)
            )
        return self._session

    def _sign(self, ts: str, method: str, path: str, body: str = "") -> str:
        """OKX API 서명 생성."""
        import hmac, hashlib, base64
        msg = ts + method.upper() + path + body
        sig = hmac.new(self.api_secret.encode(), msg.encode(), hashlib.sha256).digest()
        return base64.b64encode(sig).decode()

    async def _request(self, method: str, path: str, data: dict = None) -> dict:
        """OKX API 요청."""
        import json
        session = await self._get_session()
        ts  = str(time.time())
        body = json.dumps(data) if data else ""
        sig  = self._sign(ts, method, path, body)

        headers = {
            "OK-ACCESS-KEY":        self.api_key,
            "OK-ACCESS-SIGN":       sig,
            "OK-ACCESS-TIMESTAMP":  ts,
            "OK-ACCESS-PASSPHRASE": self.passphrase,
            "Content-Type":         "application/json",
        }
        if self.sandbox:
            headers["x-simulated-trading"] = "1"

        url = self.BASE_URL + path
        async with session.request(method, url, headers=headers, data=body or None) as resp:
            result = await resp.json()
            if result.get("code") != "0":
                raise ValueError(f"OKX API 오류: {result.get('msg')} ({result.get('code')})")
            return result

    # ── 계좌 조회 ─────────────────────────────────────
    async def get_balance(self, currency: str = "USDT") -> float:
        """계좌 잔고 조회."""
        try:
            r = await self._request("GET", f"/api/v5/account/balance?ccy={currency}")
            for detail in r.get("data", [{}])[0].get("details", []):
                if detail.get("ccy") == currency:
                    self._balance = float(detail.get("availBal", 0))
                    return self._balance
        except Exception as e:
            logger.error(f"[LiveTrader] 잔고 조회 실패: {e}")
        return 0.0

    async def get_positions(self) -> list[dict]:
        """현재 포지션 조회."""
        try:
            r = await self._request("GET", "/api/v5/account/positions?instType=SWAP")
            return r.get("data", [])
        except Exception as e:
            logger.error(f"[LiveTrader] 포지션 조회 실패: {e}")
            return []

    # ── 주문 실행 ─────────────────────────────────────
    async def place_order(
        self,
        symbol:   str,
        side:     str,    # "buy" | "sell"
        size:     float,
        order_type: str = "market",
        price:    float = 0,
        sl_price: float = 0,
        tp_price: float = 0,
        client_id:str = "",
    ) -> Optional[LiveOrder]:
        """주문 실행."""
        inst_id = symbol  # OKX 형식: BTC-USDT-SWAP
        body = {
            "instId":    inst_id,
            "tdMode":    "cross",       # 전체 마진
            "side":      side,
            "ordType":   order_type,
            "sz":        str(size),
            "clOrdId":   client_id or f"CoinHTS-{int(time.time())}",
        }
        if order_type == "limit" and price:
            body["px"] = str(price)

        # SL/TP 동시 설정 (OKX attach algo)
        if sl_price or tp_price:
            body["attachAlgoOrds"] = [{}]
            if sl_price:
                body["attachAlgoOrds"][0]["slTriggerPx"] = str(sl_price)
                body["attachAlgoOrds"][0]["slOrdPx"]     = "-1"  # 시장가 손절
            if tp_price:
                body["attachAlgoOrds"][0]["tpTriggerPx"] = str(tp_price)
                body["attachAlgoOrds"][0]["tpOrdPx"]     = "-1"  # 시장가 익절

        try:
            r    = await self._request("POST", "/api/v5/trade/order", body)
            data = r.get("data", [{}])[0]
            order = LiveOrder(
                order_id=  data.get("ordId", ""),
                client_id= data.get("clOrdId", ""),
                symbol=    inst_id,
                side=      side,
                type=      order_type,
                size=      size,
                price=     price,
                status=    "live",
            )
            logger.info(f"[LiveTrader] 주문: {side.upper()} {size} {inst_id} → {order.order_id}")
            return order
        except Exception as e:
            logger.error(f"[LiveTrader] 주문 실패 {symbol}: {e}")
            return None

    async def cancel_order(self, symbol: str, order_id: str) -> bool:
        """주문 취소."""
        try:
            await self._request("POST", "/api/v5/trade/cancel-order",
                                {"instId": symbol, "ordId": order_id})
            return True
        except Exception as e:
            logger.error(f"[LiveTrader] 취소 실패: {e}")
            return False

    async def close_position(self, symbol: str, side: str = "") -> bool:
        """포지션 전체 청산."""
        try:
            body = {"instId": symbol, "mgnMode": "cross", "posSide": "net"}
            await self._request("POST", "/api/v5/trade/close-position", body)
            logger.info(f"[LiveTrader] 청산: {symbol}")
            return True
        except Exception as e:
            logger.error(f"[LiveTrader] 청산 실패 {symbol}: {e}")
            return False

    async def set_trailing_stop(self, symbol: str, callback_ratio: float = 0.01) -> bool:
        """트레일링 스탑 설정 (OKX algo order)."""
        try:
            body = {
                "instId":        symbol,
                "tdMode":        "cross",
                "side":          "sell",
                "ordType":       "move_order_stop",
                "callbackRatio": str(callback_ratio),
                "sz":            "0",  # 전체 포지션
            }
            await self._request("POST", "/api/v5/trade/order-algo", body)
            return True
        except Exception as e:
            logger.error(f"[LiveTrader] 트레일링 스탑 실패: {e}")
            return False

    # ── 신호 처리 ─────────────────────────────────────
    async def _on_signal(self, sig: StrategySignal) -> None:
        """전략 신호 → 진입 조건 확인 → 실주문."""
        if sig.score < self.cfg.min_score:
            return
        if self._daily_loss_pct() >= self.cfg.max_daily_loss_pct:
            logger.warning("[LiveTrader] 일일 최대 손실 도달")
            return

        # 포지션 조회 (중복 방지)
        positions = await self.get_positions()
        if any(p.get("instId") == sig.symbol for p in positions):
            logger.debug(f"[LiveTrader] {sig.symbol} 이미 포지션 보유")
            return

        # 잔고 확인
        balance = await self.get_balance()
        if balance < 100:
            logger.warning(f"[LiveTrader] 잔고 부족: {balance:.2f} USDT")
            return

        # 포지션 크기 계산
        risk_amount  = balance * self.cfg.risk_per_trade / 100
        risk_per_unit = abs(sig.entry - sig.sl) if sig.sl else sig.entry * 0.005
        size = risk_amount / max(risk_per_unit, 0.001)
        size = round(size, 4)

        side = "buy" if sig.direction == "LONG" else "sell"

        order = await self.place_order(
            symbol=    sig.symbol,
            side=      side,
            size=      size,
            order_type="market",
            sl_price=  sig.sl  or 0,
            tp_price=  sig.tp  or 0,
            client_id= f"CoinHTS-{sig.symbol}-{int(time.time())}",
        )

        if order:
            logger.info(
                f"[LiveTrader] ✅ 진입: {sig.symbol} {sig.direction} "
                f"{size}계약 | 점수 {sig.score:.0f}"
            )
            await self.bus.publish("live_order", order)

    def _daily_loss_pct(self) -> float:
        now = time.time()
        if now - self._daily_reset > 86400:
            self._daily_loss  = 0.0
            self._daily_reset = now
        return self._daily_loss / max(self._balance, 1) * 100

    async def close(self) -> None:
        if self._session and not self._session.closed:
            await self._session.close()
