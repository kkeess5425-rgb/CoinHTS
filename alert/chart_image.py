"""
alert/chart_image.py
====================
Telegram 알림용 차트 이미지 생성.
matplotlib으로 캔들 + SMC 레벨 + 진입/SL/TP를
PNG 이미지로 렌더링해서 Telegram에 전송한다.
"""
from __future__ import annotations

import io
import logging
import time
from dataclasses import dataclass
from typing import Optional

logger = logging.getLogger(__name__)


@dataclass
class ChartImageConfig:
    """차트 이미지 설정."""
    width:      int   = 12     # 인치
    height:     int   = 6
    dpi:        int   = 100
    style:      str   = "dark_background"
    candles:    int   = 80     # 표시 캔들 수
    show_ema:   bool  = True
    show_vol:   bool  = True
    show_smc:   bool  = True


def build_chart_image(
    symbol:     str,
    candles:    list,
    entry:      Optional[float] = None,
    sl:         Optional[float] = None,
    tp:         Optional[float] = None,
    tp2:        Optional[float] = None,
    direction:  str = "LONG",
    score:      float = 0.0,
    smc_result  = None,
    config:     Optional[ChartImageConfig] = None,
) -> Optional[bytes]:
    """
    캔들 차트 PNG 이미지를 바이트로 반환.
    matplotlib 없으면 None 반환.
    """
    try:
        import matplotlib
        matplotlib.use("Agg")   # 헤드리스 렌더링
        import matplotlib.pyplot as plt
        import matplotlib.patches as mpatches
        import numpy as np
    except ImportError:
        logger.debug("[ChartImage] matplotlib 없음 — pip install matplotlib")
        return None

    cfg = config or ChartImageConfig()
    if not candles:
        return None

    # 최근 N봉
    recent = candles[-cfg.candles:]
    n      = len(recent)

    opens  = [c.open   for c in recent]
    highs  = [c.high   for c in recent]
    lows   = [c.low    for c in recent]
    closes = [c.close  for c in recent]
    vols   = [c.volume for c in recent]
    xs     = list(range(n))

    plt.style.use(cfg.style)
    fig, axes = plt.subplots(
        2, 1, figsize=(cfg.width, cfg.height),
        gridspec_kw={"height_ratios": [4, 1]},
        facecolor="#0d1117",
    )
    ax, ax_vol = axes

    # ── 캔들 ──────────────────────────────────────
    for i in range(n):
        color = "#26a641" if closes[i] >= opens[i] else "#f85149"
        # 몸통
        bottom = min(opens[i], closes[i])
        height = abs(closes[i] - opens[i]) or 0.01
        ax.bar(i, height, bottom=bottom, color=color, width=0.7, alpha=0.9)
        # 꼬리
        ax.plot([i, i], [lows[i], highs[i]], color=color, linewidth=0.8)

    # ── EMA ───────────────────────────────────────
    if cfg.show_ema and n >= 20:
        arr = np.array(closes)

        def ema(arr, p):
            k, out = 2 / (p + 1), [arr[0]]
            for v in arr[1:]: out.append(v * k + out[-1] * (1 - k))
            return out

        ema20 = ema(arr, 20)
        ema50 = ema(arr, 50) if n >= 50 else None
        ax.plot(xs, ema20, color="#e3b341", linewidth=1, label="EMA20", alpha=0.8)
        if ema50:
            ax.plot(xs, ema50, color="#bc8cff", linewidth=1, label="EMA50", alpha=0.8)

    # ── 진입/SL/TP 수평선 ─────────────────────────
    line_kw = dict(linewidth=1.5, alpha=0.9)
    if entry:
        ax.axhline(entry, color="#58a6ff", linestyle="--", **line_kw, label=f"진입 {entry:.0f}")
    if sl:
        ax.axhline(sl, color="#f85149", linestyle="--", **line_kw, label=f"SL {sl:.0f}")
    if tp:
        ax.axhline(tp, color="#3fb950", linestyle="--", **line_kw, label=f"TP1 {tp:.0f}")
    if tp2:
        ax.axhline(tp2, color="#3fb95066", linestyle=":", **line_kw, label=f"TP2 {tp2:.0f}")

    # ── SL/TP 구간 색칠 ───────────────────────────
    if entry and sl and tp:
        if direction == "LONG":
            ax.axhspan(sl, entry, alpha=0.05, color="#f85149")
            ax.axhspan(entry, tp, alpha=0.05, color="#3fb950")
        else:
            ax.axhspan(entry, sl, alpha=0.05, color="#f85149")
            ax.axhspan(tp, entry, alpha=0.05, color="#3fb950")

    # ── SMC 레벨 ──────────────────────────────────
    if cfg.show_smc and smc_result:
        # FVG
        for fvg in getattr(smc_result, "fvg_zones", [])[:3]:
            color = "#26a64130" if fvg.direction == "bull" else "#f8514930"
            ax.axhspan(fvg.bottom, fvg.top, alpha=0.15, color=color, linewidth=0)
        # EQH/EQL
        for eqh in getattr(smc_result, "equal_highs", [])[-2:]:
            ax.axhline(eqh.price, color="#e3b341", linewidth=0.7, linestyle=":")
        for eql in getattr(smc_result, "equal_lows", [])[-2:]:
            ax.axhline(eql.price, color="#58a6ff", linewidth=0.7, linestyle=":")

    # ── 볼륨 바 ───────────────────────────────────
    if cfg.show_vol:
        vol_colors = ["#26a64160" if closes[i] >= opens[i] else "#f8514960" for i in range(n)]
        ax_vol.bar(xs, vols, color=vol_colors, width=0.7)
        ax_vol.set_facecolor("#0d1117")
        ax_vol.tick_params(colors="#484f58", labelsize=7)
        ax_vol.set_xlim(-1, n)
        ax_vol.spines[:].set_color("#21262d")
        ax_vol.set_ylabel("Vol", color="#484f58", fontsize=7)

    # ── 스타일 ────────────────────────────────────
    ax.set_facecolor("#0d1117")
    ax.tick_params(colors="#484f58", labelsize=8)
    ax.spines[:].set_color("#21262d")
    ax.set_xlim(-1, n)
    ax.legend(fontsize=7, loc="upper left",
              facecolor="#161b22", edgecolor="#30363d", labelcolor="#8b949e")

    # ── 제목 ──────────────────────────────────────
    grade_color = "#3fb950" if score >= 75 else "#e3b341" if score >= 55 else "#f85149"
    dir_icon    = "🔼" if direction == "LONG" else "🔽"
    title = f"{dir_icon} {symbol.replace('-USDT-SWAP','')}  |  AI 점수: {score:.0f}/100  |  {time.strftime('%m/%d %H:%M UTC', time.gmtime())}"
    ax.set_title(title, color="#c9d1d9", fontsize=10, pad=6)

    # ── 점수 텍스트 ───────────────────────────────
    ax.text(0.99, 0.97, f"Score: {score:.0f}", transform=ax.transAxes,
            ha="right", va="top", fontsize=11, fontweight="bold", color=grade_color)

    plt.tight_layout(pad=0.5)

    buf = io.BytesIO()
    plt.savefig(buf, format="png", dpi=cfg.dpi, facecolor="#0d1117",
                bbox_inches="tight")
    plt.close(fig)
    buf.seek(0)
    return buf.read()


async def send_chart_to_telegram(
    token:    str,
    chat_id:  str,
    image:    bytes,
    caption:  str = "",
) -> bool:
    """PNG 이미지를 Telegram에 전송."""
    try:
        import aiohttp
        url  = f"https://api.telegram.org/bot{token}/sendPhoto"
        data = aiohttp.FormData()
        data.add_field("chat_id", chat_id)
        data.add_field("caption", caption[:1024], content_type="text/plain")
        data.add_field("photo", image, filename="chart.png",
                       content_type="image/png")
        data.add_field("parse_mode", "Markdown")

        async with aiohttp.ClientSession() as sess:
            async with sess.post(url, data=data, timeout=aiohttp.ClientTimeout(total=30)) as resp:
                ok = resp.status == 200
                if not ok:
                    logger.warning(f"[ChartImage] Telegram 전송 실패: {resp.status}")
                return ok
    except Exception as e:
        logger.error(f"[ChartImage] 전송 오류: {e}")
        return False
