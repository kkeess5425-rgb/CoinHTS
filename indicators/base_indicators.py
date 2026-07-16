"""
indicators/base_indicators.py
==============================
EMA, ATR, RSI, VWAP 등 핵심 지표.
Numba JIT 컴파일로 대용량 데이터도 빠르게 처리.
배열 단위 벡터 연산 → 60FPS 차트 업데이트 가능.
"""
from __future__ import annotations

import numpy as np
import numba as nb


# ── EMA ──────────────────────────────────────────────────
@nb.njit(cache=True)
def ema(close: np.ndarray, period: int) -> np.ndarray:
    """지수이동평균 (Numba JIT)."""
    n = len(close)
    out = np.empty(n, dtype=np.float64)
    if n == 0:
        return out
    k = 2.0 / (period + 1)
    out[0] = close[0]
    for i in range(1, n):
        out[i] = close[i] * k + out[i - 1] * (1 - k)
    return out


# ── ATR ──────────────────────────────────────────────────
@nb.njit(cache=True)
def atr(high: np.ndarray, low: np.ndarray, close: np.ndarray, period: int) -> np.ndarray:
    """Average True Range (Numba JIT)."""
    n = len(close)
    tr  = np.empty(n, dtype=np.float64)
    out = np.empty(n, dtype=np.float64)

    tr[0] = high[0] - low[0]
    for i in range(1, n):
        hl = high[i] - low[i]
        hc = abs(high[i] - close[i - 1])
        lc = abs(low[i]  - close[i - 1])
        tr[i] = max(hl, hc, lc)

    # EMA 방식 ATR
    k = 1.0 / period
    out[0] = tr[0]
    for i in range(1, n):
        out[i] = tr[i] * k + out[i - 1] * (1 - k)
    return out


# ── RSI ──────────────────────────────────────────────────
@nb.njit(cache=True)
def rsi(close: np.ndarray, period: int = 14) -> np.ndarray:
    """Wilder's RSI (Numba JIT)."""
    n = len(close)
    out = np.full(n, np.nan)
    if n <= period:
        return out

    gains = np.zeros(n)
    losses = np.zeros(n)
    for i in range(1, n):
        diff = close[i] - close[i - 1]
        if diff >= 0: gains[i]  =  diff
        else:         losses[i] = -diff

    avg_g = np.mean(gains[1:period + 1])
    avg_l = np.mean(losses[1:period + 1])

    for i in range(period, n):
        if i == period:
            ag, al = avg_g, avg_l
        else:
            ag = (ag * (period - 1) + gains[i])  / period
            al = (al * (period - 1) + losses[i]) / period
        rs = ag / al if al > 0 else 1e9
        out[i] = 100.0 - (100.0 / (1.0 + rs))

    return out


# ── CVD (Cumulative Volume Delta) ────────────────────────
@nb.njit(cache=True)
def cvd(buy_vol: np.ndarray, sell_vol: np.ndarray) -> np.ndarray:
    """누적 볼륨 델타 (Numba JIT)."""
    delta = buy_vol - sell_vol
    return np.cumsum(delta)


# ── VWAP ─────────────────────────────────────────────────
@nb.njit(cache=True)
def vwap(high: np.ndarray, low: np.ndarray, close: np.ndarray, volume: np.ndarray) -> np.ndarray:
    """Volume Weighted Average Price."""
    n = len(close)
    out = np.empty(n, dtype=np.float64)
    cum_vol = 0.0
    cum_tpv = 0.0
    for i in range(n):
        tp = (high[i] + low[i] + close[i]) / 3.0
        cum_tpv += tp * volume[i]
        cum_vol  += volume[i]
        out[i] = cum_tpv / cum_vol if cum_vol > 0 else tp
    return out


# ── Volume Profile ────────────────────────────────────────
@nb.njit(cache=True)
def build_volume_profile(
    prices:   np.ndarray,
    volumes:  np.ndarray,
    sides:    np.ndarray,    # 1=buy, -1=sell
    n_bins:   int = 200,
) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """
    가격 범위를 n_bins개 구간으로 나눠서 매수/매도 볼륨 집계.
    반환: (bin_prices, buy_vols, sell_vols, bin_width)
    """
    if len(prices) == 0:
        empty = np.zeros(n_bins)
        return empty, empty, empty, np.zeros(1)

    p_min = prices.min()
    p_max = prices.max()
    if p_min == p_max:
        p_max = p_min * 1.001

    bin_width = (p_max - p_min) / n_bins
    buy_vols  = np.zeros(n_bins)
    sell_vols = np.zeros(n_bins)

    for i in range(len(prices)):
        idx = int((prices[i] - p_min) / bin_width)
        idx = min(idx, n_bins - 1)
        if sides[i] > 0: buy_vols[idx]  += volumes[i]
        else:             sell_vols[idx] += volumes[i]

    bin_prices = np.array([p_min + (i + 0.5) * bin_width for i in range(n_bins)])
    return bin_prices, buy_vols, sell_vols, np.array([bin_width])


@nb.njit(cache=True)
def compute_poc_vah_val(
    bin_prices: np.ndarray,
    buy_vols:   np.ndarray,
    sell_vols:  np.ndarray,
    value_area_pct: float = 0.70,
) -> tuple[float, float, float]:
    """
    POC (Point of Control), VAH (Value Area High), VAL (Value Area Low) 계산.
    반환: (poc_price, vah_price, val_price)
    """
    total_vols = buy_vols + sell_vols
    total = total_vols.sum()
    if total == 0:
        mid = bin_prices[len(bin_prices) // 2]
        return mid, mid, mid

    # POC: 가장 많은 거래 발생 가격대
    poc_idx = np.argmax(total_vols)
    poc = bin_prices[poc_idx]

    # VAH/VAL: POC 기준으로 70% 볼륨 포함 범위
    target = total * value_area_pct
    acc = total_vols[poc_idx]
    lo = hi = poc_idx

    while acc < target:
        lo_val = total_vols[lo - 1] if lo > 0 else 0.0
        hi_val = total_vols[hi + 1] if hi < len(total_vols) - 1 else 0.0
        if lo_val >= hi_val and lo > 0:
            lo  -= 1
            acc += lo_val
        elif hi < len(total_vols) - 1:
            hi  += 1
            acc += hi_val
        else:
            break

    return poc, bin_prices[hi], bin_prices[lo]


# ── Pivot High / Low (스윙 고점/저점) ─────────────────────
@nb.njit(cache=True)
def pivot_high(high: np.ndarray, length: int) -> np.ndarray:
    """스윙 고점: length봉 좌우보다 높은 지점 (NaN = 고점 아님)."""
    n = len(high)
    out = np.full(n, np.nan)
    for i in range(length, n - length):
        is_pivot = True
        for j in range(i - length, i + length + 1):
            if j != i and high[j] >= high[i]:
                is_pivot = False
                break
        if is_pivot:
            out[i] = high[i]
    return out


@nb.njit(cache=True)
def pivot_low(low: np.ndarray, length: int) -> np.ndarray:
    """스윙 저점."""
    n = len(low)
    out = np.full(n, np.nan)
    for i in range(length, n - length):
        is_pivot = True
        for j in range(i - length, i + length + 1):
            if j != i and low[j] <= low[i]:
                is_pivot = False
                break
        if is_pivot:
            out[i] = low[i]
    return out


# ── Imbalance 감지 ────────────────────────────────────────
@nb.njit(cache=True)
def detect_stacked_imbalances(
    buy_vols:  np.ndarray,
    sell_vols: np.ndarray,
    threshold: float = 4.0,
    min_stack: int   = 3,
) -> tuple[np.ndarray, np.ndarray]:
    """
    Stacked Imbalance 감지.
    연속 N개 이상의 가격 레벨에서 매수/매도 불균형이 threshold 이상일 때.
    반환: (bull_imbalance_mask, bear_imbalance_mask)
    """
    n = len(buy_vols)
    bull = np.zeros(n, dtype=nb.boolean)
    bear = np.zeros(n, dtype=nb.boolean)

    for i in range(n):
        if sell_vols[i] > 0 and buy_vols[i] / sell_vols[i] >= threshold:
            bull[i] = True
        elif buy_vols[i] > 0 and sell_vols[i] / buy_vols[i] >= threshold:
            bear[i] = True

    # 연속 스택 필터
    bull_stacked = np.zeros(n, dtype=nb.boolean)
    bear_stacked = np.zeros(n, dtype=nb.boolean)
    for i in range(n - min_stack + 1):
        if all(bull[i:i + min_stack]):
            for j in range(i, i + min_stack):
                bull_stacked[j] = True
        if all(bear[i:i + min_stack]):
            for j in range(i, i + min_stack):
                bear_stacked[j] = True

    return bull_stacked, bear_stacked

# ── 볼린저 밴드 ───────────────────────────────────────
@nb.njit(cache=True, fastmath=True)
def bollinger_bands(
    prices: np.ndarray,
    period: int   = 20,
    std_dev: float = 2.0,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """볼린저 밴드 (중간선, 상단, 하단)."""
    n   = len(prices)
    mid = np.full(n, np.nan)
    up  = np.full(n, np.nan)
    lo  = np.full(n, np.nan)
    for i in range(period - 1, n):
        window = prices[i - period + 1 : i + 1]
        m = window.mean()
        s = window.std()
        mid[i] = m
        up[i]  = m + std_dev * s
        lo[i]  = m - std_dev * s
    return mid, up, lo


# ── MACD ─────────────────────────────────────────────
@nb.njit(cache=True, fastmath=True)
def macd(
    prices:   np.ndarray,
    fast:     int = 12,
    slow:     int = 26,
    signal:   int = 9,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """MACD 라인, 시그널, 히스토그램."""
    n     = len(prices)
    e_f   = ema(prices, fast)
    e_s   = ema(prices, slow)
    macd_ = e_f - e_s
    sig_  = ema(macd_, signal)
    hist  = macd_ - sig_
    return macd_, sig_, hist


# ── Stochastic ────────────────────────────────────────
@nb.njit(cache=True, fastmath=True)
def stochastic(
    highs:  np.ndarray,
    lows:   np.ndarray,
    closes: np.ndarray,
    k_period: int = 14,
    d_period: int = 3,
) -> tuple[np.ndarray, np.ndarray]:
    """%K와 %D 스토캐스틱."""
    n  = len(closes)
    k  = np.full(n, np.nan)
    d  = np.full(n, np.nan)
    for i in range(k_period - 1, n):
        lo = lows[i - k_period + 1 : i + 1].min()
        hi = highs[i - k_period + 1 : i + 1].max()
        rng = hi - lo
        k[i] = (closes[i] - lo) / rng * 100.0 if rng > 0 else 50.0
    # %D = SMA(K, d_period)
    for i in range(k_period + d_period - 2, n):
        valid = k[i - d_period + 1 : i + 1]
        if not np.any(np.isnan(valid)):
            d[i] = valid.mean()
    return k, d


# ── Williams %R ──────────────────────────────────────
@nb.njit(cache=True, fastmath=True)
def williams_r(
    highs:   np.ndarray,
    lows:    np.ndarray,
    closes:  np.ndarray,
    period:  int = 14,
) -> np.ndarray:
    """Williams %R (-100 ~ 0, -80 이하: 과매도)."""
    n   = len(closes)
    wr  = np.full(n, np.nan)
    for i in range(period - 1, n):
        hi  = highs[i - period + 1 : i + 1].max()
        lo  = lows[i - period + 1 : i + 1].min()
        rng = hi - lo
        wr[i] = (hi - closes[i]) / rng * (-100.0) if rng > 0 else -50.0
    return wr


# ── OBV (On Balance Volume) ──────────────────────────
@nb.njit(cache=True, fastmath=True)
def obv(closes: np.ndarray, volumes: np.ndarray) -> np.ndarray:
    """On Balance Volume."""
    n      = len(closes)
    result = np.zeros(n)
    for i in range(1, n):
        if closes[i] > closes[i - 1]:
            result[i] = result[i - 1] + volumes[i]
        elif closes[i] < closes[i - 1]:
            result[i] = result[i - 1] - volumes[i]
        else:
            result[i] = result[i - 1]
    return result

