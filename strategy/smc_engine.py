"""
strategy/smc_engine.py
======================
Smart Money Concept (SMC) 완전 구현.

- BOS  (Break of Structure)
- CHoCH (Change of Character)
- FVG  (Fair Value Gap) — 미체결 FVG 필터링
- Order Block — 강화 (스윕 직전 마지막 반대 캔들)
- Breaker Block — 무효화된 OB가 반대 역할로 전환
- Mitigation Block — 미처리 주문 잔재 구간
- Liquidity Sweep — 유동성 사냥 감지
- Equal High / Equal Low — 이중 천장/바닥
- Premium / Discount Zone — 피보나치 기반
- SMT Divergence — 상관 심볼 간 구조 불일치
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Optional

import numpy as np

from core.models import Candle, Timeframe
from indicators.base_indicators import atr, pivot_high, pivot_low

logger = logging.getLogger(__name__)


# ── 데이터 구조 ───────────────────────────────────────
@dataclass
class StructurePoint:
    """시장 구조 포인트 (스윙 고점/저점)."""
    ts:    float
    price: float
    kind:  str        # "HH" | "LH" | "HL" | "LL"
    index: int


@dataclass
class FVGZone:
    """Fair Value Gap (미체결 갭 구간)."""
    ts:        float
    top:       float
    bottom:    float
    direction: str    # "bull" | "bear"
    filled_pct: float = 0.0

    @property
    def midpoint(self) -> float:
        return (self.top + self.bottom) / 2

    @property
    def is_valid(self) -> bool:
        return self.filled_pct < 0.5


@dataclass
class OrderBlock:
    """Order Block — 스윕 직전 마지막 반대 방향 캔들."""
    ts:        float
    high:      float
    low:       float
    direction: str    # "bull" | "bear"
    broken:    bool   = False   # 가격이 OB를 완전히 통과하면 무효화

    @property
    def midpoint(self) -> float:
        return (self.high + self.low) / 2


@dataclass
class BreakerBlock:
    """Breaker Block — 무효화된 OB가 반대 역할로 전환."""
    ts:        float
    high:      float
    low:       float
    direction: str    # 원래 방향과 반대로 작용


@dataclass
class MitigationBlock:
    """Mitigation Block — 미처리 주문 잔재 구간."""
    ts:        float
    high:      float
    low:       float
    direction: str
    mitigated: bool = False


@dataclass
class EqualLevel:
    """Equal High / Equal Low."""
    ts1:       float
    ts2:       float
    price:     float
    kind:      str    # "EQH" | "EQL"
    tolerance: float = 0.001   # 0.1% 이내면 동일 레벨


@dataclass
class PremiumDiscountZone:
    """Premium / Discount Zone (피보나치 50% 기준)."""
    swing_high:   float
    swing_low:    float
    equilibrium:  float   # 50% 레벨
    premium_top:  float   # 상단 (Premium: 50% ~ 100%)
    discount_bot: float   # 하단 (Discount: 0% ~ 50%)
    current_zone: str     # "premium" | "discount" | "equilibrium"


@dataclass
class SMTDivergence:
    """SMT Divergence — 두 심볼의 구조 불일치."""
    ts:        float
    symbol_a:  str
    symbol_b:  str
    kind:      str    # "bullish_smt" | "bearish_smt"
    # A가 새 고점(저점)인데 B가 그렇지 않은 경우


@dataclass
class LiquiditySweep:
    """유동성 스윕."""
    ts:           float
    swept_level:  float
    direction:    str       # "bull_sweep" | "bear_sweep"
    recovery_dist: float    # 회복 거리 (ATR 배수)
    confirmed:    bool


@dataclass
class SMCResult:
    """SMC 전체 분석 결과."""
    # 시장 구조
    bull_ms:          bool               = False
    structure_points: list[StructurePoint] = field(default_factory=list)
    last_bos:         Optional[float]    = None
    last_choch:       Optional[float]    = None

    # 핵심 레벨
    fvg_zones:        list[FVGZone]      = field(default_factory=list)
    order_blocks:     list[OrderBlock]   = field(default_factory=list)
    breaker_blocks:   list[BreakerBlock] = field(default_factory=list)
    mitigation_blocks:list[MitigationBlock] = field(default_factory=list)
    equal_highs:      list[EqualLevel]   = field(default_factory=list)
    equal_lows:       list[EqualLevel]   = field(default_factory=list)
    liquidity_sweeps: list[LiquiditySweep] = field(default_factory=list)

    # 존
    premium_discount: Optional[PremiumDiscountZone] = None

    # SMT
    smt_divergences:  list[SMTDivergence] = field(default_factory=list)

    # 신호
    signal:           Optional[str] = None   # "LONG" | "SHORT"
    score:            float         = 0.0
    confluence:       list[str]     = field(default_factory=list)


class SMCEngine:
    """
    완전한 SMC 분석 엔진.
    단일 심볼 캔들 데이터로 전체 SMC 구조를 분석한다.
    """

    def __init__(
        self,
        swing_length:    int   = 10,
        fvg_min_pct:     float = 0.07,
        eq_tolerance:    float = 0.002,  # 0.2% 이내 → Equal High/Low
        ob_lookback:     int   = 20,
        sweep_lookback:  int   = 30,
        sweep_atr_mult:  float = 0.2,
    ) -> None:
        self.swing_length   = swing_length
        self.fvg_min_pct    = fvg_min_pct
        self.eq_tolerance   = eq_tolerance
        self.ob_lookback    = ob_lookback
        self.sweep_lookback = sweep_lookback
        self.sweep_atr_mult = sweep_atr_mult

    def analyze(
        self,
        candles: list[Candle],
        corr_candles: Optional[list[Candle]] = None,  # SMT용 상관 심볼
    ) -> SMCResult:
        if len(candles) < 50:
            return SMCResult()

        h = np.array([c.high   for c in candles])
        l = np.array([c.low    for c in candles])
        c = np.array([c.close  for c in candles])
        o = np.array([c.open   for c in candles])
        ts = np.array([c.ts    for c in candles])
        n  = len(candles)

        atr14    = atr(h, l, c, 14)
        atr_cur  = float(atr14[-1])
        cur_price = float(c[-1])

        result = SMCResult()

        # 1. 시장 구조
        self._analyze_structure(result, h, l, c, ts, n)

        # 2. FVG
        self._find_fvg(result, h, l, c, ts, cur_price, atr_cur)

        # 3. Order Block
        self._find_ob(result, h, l, c, o, ts, cur_price)

        # 4. Breaker / Mitigation Block
        self._find_breaker_mitigation(result, h, l, c, ts, cur_price)

        # 5. Equal High / Low
        self._find_equal_levels(result, h, l, ts, atr_cur)

        # 6. Liquidity Sweep
        self._find_sweeps(result, h, l, c, ts, atr_cur, n)

        # 7. Premium / Discount Zone
        self._calc_premium_discount(result, h, l, cur_price, n)

        # 8. SMT Divergence
        if corr_candles and len(corr_candles) >= 50:
            self._find_smt(result, candles, corr_candles)

        # 9. 종합 신호
        self._compute_signal(result, cur_price)

        return result

    # ── 1. 시장 구조 ──────────────────────────────────
    def _analyze_structure(self, result, h, l, c, ts, n):
        ph = pivot_high(h, self.swing_length)
        pl = pivot_low(l,  self.swing_length)

        highs = [(i, float(ph[i]), float(ts[i])) for i in range(n) if not np.isnan(ph[i])]
        lows  = [(i, float(pl[i]), float(ts[i])) for i in range(n) if not np.isnan(pl[i])]

        # 고점/저점 분류 (HH/LH/HL/LL)
        pts = []
        prev_h = prev_l = None
        for idx, price, t in sorted(highs + lows, key=lambda x: x[0]):
            if price == float(ph[idx]) if not np.isnan(ph[idx]) else False:
                kind = "HH" if (prev_h is None or price > prev_h) else "LH"
                prev_h = price
            else:
                kind = "HL" if (prev_l is None or price > prev_l) else "LL"
                prev_l = price
            pts.append(StructurePoint(ts=t, price=price, kind=kind, index=idx))

        result.structure_points = pts[-20:]   # 최근 20개

        # bull_ms: 최근 HL > 이전 HL and HH > 이전 HH
        all_hh = [p for p in pts if p.kind == "HH"]
        all_hl = [p for p in pts if p.kind == "HL"]
        if len(all_hh) >= 2 and len(all_hl) >= 2:
            result.bull_ms = (all_hh[-1].price > all_hh[-2].price and
                              all_hl[-1].price > all_hl[-2].price)

        # BOS: 이전 스윙 고/저를 돌파하는 종가
        cur_c = float(c[-1])
        if highs:
            last_sh = max(highs[-3:], key=lambda x: x[1])[1]
            if cur_c > last_sh:
                result.last_bos = last_sh
        if lows:
            last_sl = min(lows[-3:], key=lambda x: x[1])[1]
            if cur_c < last_sl:
                result.last_bos = last_sl

        # CHoCH: 추세 전환 시그널
        if len(pts) >= 4:
            last4 = pts[-4:]
            if result.bull_ms:
                # 불리시 → LL 발생 시 CHoCH
                if any(p.kind == "LL" for p in last4):
                    result.last_choch = min(p.price for p in last4 if p.kind in ("LL","HL"))
            else:
                if any(p.kind == "HH" for p in last4):
                    result.last_choch = max(p.price for p in last4 if p.kind in ("HH","LH"))

    # ── 2. FVG ───────────────────────────────────────
    def _find_fvg(self, result, h, l, c, ts, cur_price, atr_cur):
        n = len(c)
        fvg_min = cur_price * self.fvg_min_pct / 100

        for i in range(2, min(40, n)):
            # 불리시 FVG: bar[i].low > bar[i-2].high
            fl = float(l[-i]); fh = float(h[-i-2])
            if fl > fh and fl - fh >= fvg_min:
                # 부분 채움 계산
                fill = max(0, float(l[-1]) - fh) / (fl - fh) if fl > fh else 1
                zone = FVGZone(ts=float(ts[-i]), top=fl, bottom=fh,
                               direction="bull", filled_pct=fill)
                if zone.is_valid and zone.bottom <= cur_price <= zone.top:
                    result.fvg_zones.append(zone)

            # 베어리시 FVG
            fl2 = float(l[-i-2]); fh2 = float(h[-i])
            if fh2 < fl2 and fl2 - fh2 >= fvg_min:
                fill2 = max(0, fl2 - float(h[-1])) / (fl2 - fh2) if fl2 > fh2 else 1
                zone2 = FVGZone(ts=float(ts[-i]), top=fl2, bottom=fh2,
                                direction="bear", filled_pct=fill2)
                if zone2.is_valid and zone2.bottom <= cur_price <= zone2.top:
                    result.fvg_zones.append(zone2)

    # ── 3. Order Block ────────────────────────────────
    def _find_ob(self, result, h, l, c, o, ts, cur_price):
        n = len(c)
        for i in range(1, min(self.ob_lookback, n - 1)):
            idx = -(i+1)
            oh = float(h[idx]); ol = float(l[idx])
            oc = float(c[idx]); oo = float(o[idx])
            broken = False

            # 불리시 OB (하락 캔들 → 이후 상승으로 스윕 직전)
            if oc < oo:
                broken = float(l[-1]) < ol
                if ol <= cur_price <= oh:
                    result.order_blocks.append(OrderBlock(
                        ts=float(ts[idx]), high=oh, low=ol,
                        direction="bull", broken=broken
                    ))

            # 베어리시 OB (상승 캔들)
            elif oc > oo:
                broken = float(h[-1]) > oh
                if ol <= cur_price <= oh:
                    result.order_blocks.append(OrderBlock(
                        ts=float(ts[idx]), high=oh, low=ol,
                        direction="bear", broken=broken
                    ))

    # ── 4. Breaker / Mitigation Block ─────────────────
    def _find_breaker_mitigation(self, result, h, l, c, ts, cur_price):
        """무효화된 OB를 Breaker Block으로, 미처리 구간을 Mitigation Block으로."""
        for ob in result.order_blocks:
            if ob.broken:
                # 방향 반전 → Breaker Block
                result.breaker_blocks.append(BreakerBlock(
                    ts=ob.ts, high=ob.high, low=ob.low,
                    direction="bear" if ob.direction == "bull" else "bull"
                ))
            elif abs(cur_price - ob.midpoint) / ob.midpoint < 0.005:
                # 현재가가 OB 중간선 근처 → Mitigation Block
                result.mitigation_blocks.append(MitigationBlock(
                    ts=ob.ts, high=ob.high, low=ob.low,
                    direction=ob.direction
                ))

    # ── 5. Equal High / Low ───────────────────────────
    def _find_equal_levels(self, result, h, l, ts, atr_cur):
        n = len(h)
        tol = atr_cur * 0.3

        # Equal High 탐색
        highs_i = [(i, float(h[i])) for i in range(max(0,n-50), n)]
        for a in range(len(highs_i)):
            for b in range(a+3, len(highs_i)):
                ia, pa = highs_i[a]
                ib, pb = highs_i[b]
                if abs(pa - pb) <= tol:
                    result.equal_highs.append(EqualLevel(
                        ts1=float(ts[ia]), ts2=float(ts[ib]),
                        price=(pa+pb)/2, kind="EQH"
                    ))

        # Equal Low 탐색
        lows_i = [(i, float(l[i])) for i in range(max(0,n-50), n)]
        for a in range(len(lows_i)):
            for b in range(a+3, len(lows_i)):
                ia, pa = lows_i[a]
                ib, pb = lows_i[b]
                if abs(pa - pb) <= tol:
                    result.equal_lows.append(EqualLevel(
                        ts1=float(ts[ia]), ts2=float(ts[ib]),
                        price=(pa+pb)/2, kind="EQL"
                    ))

        # 최근 5개만 유지
        result.equal_highs = result.equal_highs[-5:]
        result.equal_lows  = result.equal_lows[-5:]

    # ── 6. Liquidity Sweep ────────────────────────────
    def _find_sweeps(self, result, h, l, c, ts, atr_cur, n):
        lookback = min(self.sweep_lookback, n-1)
        confirm  = atr_cur * self.sweep_atr_mult

        for i in range(1, lookback):
            bp = -(i)
            wh = float(h[-lookback:-i].max()) if lookback > i else 0
            wl = float(l[-lookback:-i].min()) if lookback > i else 0
            bl = float(l[bp]); bh = float(h[bp]); bc = float(c[bp])

            if bl < wl and bc > wl + confirm:
                result.liquidity_sweeps.append(LiquiditySweep(
                    ts=float(ts[bp]), swept_level=wl,
                    direction="bull_sweep",
                    recovery_dist=(bc - wl) / atr_cur,
                    confirmed=True
                ))
            if bh > wh and bc < wh - confirm:
                result.liquidity_sweeps.append(LiquiditySweep(
                    ts=float(ts[bp]), swept_level=wh,
                    direction="bear_sweep",
                    recovery_dist=(wh - bc) / atr_cur,
                    confirmed=True
                ))

        result.liquidity_sweeps = result.liquidity_sweeps[-10:]

    # ── 7. Premium / Discount Zone ────────────────────
    def _calc_premium_discount(self, result, h, l, cur_price, n):
        lookback = min(50, n)
        sh = float(h[-lookback:].max())
        sl = float(l[-lookback:].min())
        eq = (sh + sl) / 2

        if sh > sl:
            zone = "premium" if cur_price > eq else ("discount" if cur_price < eq else "equilibrium")
            result.premium_discount = PremiumDiscountZone(
                swing_high=sh, swing_low=sl,
                equilibrium=eq,
                premium_top=sh,
                discount_bot=sl,
                current_zone=zone,
            )

    # ── 8. SMT Divergence ─────────────────────────────
    def _find_smt(self, result, candles_a, candles_b):
        """두 심볼의 고점/저점 구조 불일치 감지."""
        ha = np.array([c.high for c in candles_a[-30:]])
        hb = np.array([c.high for c in candles_b[-30:]])
        la = np.array([c.low  for c in candles_a[-30:]])
        lb = np.array([c.low  for c in candles_b[-30:]])
        ts = candles_a[-1].ts

        # Bearish SMT: A는 새 고점, B는 못 만듦
        if ha[-1] > ha[:-1].max() and hb[-1] < hb[:-1].max():
            result.smt_divergences.append(SMTDivergence(
                ts=ts, symbol_a=candles_a[0].symbol,
                symbol_b=candles_b[0].symbol, kind="bearish_smt"
            ))
        # Bullish SMT: A는 새 저점, B는 못 만듦
        if la[-1] < la[:-1].min() and lb[-1] > lb[:-1].min():
            result.smt_divergences.append(SMTDivergence(
                ts=ts, symbol_a=candles_a[0].symbol,
                symbol_b=candles_b[0].symbol, kind="bullish_smt"
            ))

    # ── 9. 종합 신호 ──────────────────────────────────
    def _compute_signal(self, result, cur_price):
        conf = []
        score = 0.0

        # 시장 구조
        if result.bull_ms:
            conf.append("[BOS] 불리시 구조"); score += 20
        if result.last_choch:
            conf.append("[CHoCH] 구조 전환 감지"); score += 15

        # FVG 컨플루언스
        bull_fvg = [z for z in result.fvg_zones if z.direction == "bull"]
        bear_fvg = [z for z in result.fvg_zones if z.direction == "bear"]
        if bull_fvg:
            conf.append(f"[FVG] 불리시 FVG {len(bull_fvg)}개"); score += 15
        if bear_fvg:
            conf.append(f"[FVG] 베어리시 FVG {len(bear_fvg)}개"); score += 15

        # Order Block
        bull_ob = [ob for ob in result.order_blocks if ob.direction == "bull" and not ob.broken]
        bear_ob = [ob for ob in result.order_blocks if ob.direction == "bear" and not ob.broken]
        if bull_ob:
            conf.append("[OB] 불리시 Order Block"); score += 15
        if bear_ob:
            conf.append("[OB] 베어리시 Order Block"); score += 15

        # Breaker Block
        if result.breaker_blocks:
            conf.append(f"[BB] Breaker Block {len(result.breaker_blocks)}개"); score += 10

        # Sweep
        bull_sw = [s for s in result.liquidity_sweeps if s.direction == "bull_sweep"]
        bear_sw = [s for s in result.liquidity_sweeps if s.direction == "bear_sweep"]
        if bull_sw:
            conf.append("[SWEEP] 불리시 유동성 스윕"); score += 15
        if bear_sw:
            conf.append("[SWEEP] 베어리시 유동성 스윕"); score += 15

        # Equal Levels
        if result.equal_highs:
            conf.append(f"[EQH] Equal High {len(result.equal_highs)}개 (유동성 위)"); score += 5
        if result.equal_lows:
            conf.append(f"[EQL] Equal Low {len(result.equal_lows)}개 (유동성 아래)"); score += 5

        # Premium/Discount
        if result.premium_discount:
            z = result.premium_discount.current_zone
            conf.append(f"[PD] 현재 {z.upper()} 존")
            if z == "discount": score += 10
            if z == "premium":  score -= 5

        # SMT
        if result.smt_divergences:
            conf.append(f"[SMT] 다이버전스 감지"); score += 10

        # 신호 결정
        score = min(100, max(0, score))
        result.score     = score
        result.confluence = conf

        if bull_sw and result.bull_ms and (bull_fvg or bull_ob):
            result.signal = "LONG"
        elif bear_sw and not result.bull_ms and (bear_fvg or bear_ob):
            result.signal = "SHORT"
