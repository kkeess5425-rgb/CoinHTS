"""
main.py — CoinHTS 앱 시작점

사용법:
  python main.py              # 기본 데스크탑 UI
  python main.py --web        # 웹 서버 모드
  python main.py --headless   # UI 없이 봇/자동매매만
  python main.py --replay     # 리플레이 모드

환경변수:
  OKX_API_KEY, OKX_API_SECRET, OKX_PASSPHRASE
  TELEGRAM_TOKEN, TELEGRAM_CHAT_ID
  OPENAI_API_KEY (선택: GPT 연동)
  REDIS_URL      (선택: Redis 캐시)
"""
import argparse
import asyncio
import logging
import os
import sys

# 로그 설정
log_level = os.environ.get("LOG_LEVEL", "INFO").upper()
logging.basicConfig(
    level=getattr(logging, log_level, logging.INFO),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.StreamHandler(),
    ]
)
logger = logging.getLogger("CoinHTS")


def parse_args():
    parser = argparse.ArgumentParser(
        description="CoinHTS — Professional Crypto Trading Terminal",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
예시:
  python main.py                  데스크탑 UI 실행
  python main.py --web            웹 서버 (http://localhost:8000)
  python main.py --headless       UI 없이 봇/자동매매만
  python main.py --paper          페이퍼 트레이딩 활성화
  python main.py --live           실거래 활성화 (주의!)
  python main.py --web --port 9000 다른 포트로 웹 서버
        """
    )
    parser.add_argument("--web",      action="store_true", help="웹 서버 모드")
    parser.add_argument("--headless", action="store_true", help="헤드리스 모드 (봇 전용)")
    parser.add_argument("--paper",    action="store_true", help="페이퍼 트레이딩 활성화")
    parser.add_argument("--live",     action="store_true", help="실거래 활성화 (⚠️ 실제 자금)")
    parser.add_argument("--sandbox",  action="store_true", help="OKX 샌드박스 모드")
    parser.add_argument("--port",     type=int, default=8000, help="웹 서버 포트 (기본: 8000)")
    parser.add_argument("--symbols",  nargs="+", default=["BTC-USDT-SWAP", "ETH-USDT-SWAP"],
                        help="거래 심볼 목록")
    parser.add_argument("--log-level", default="INFO",
                        choices=["DEBUG","INFO","WARNING","ERROR"])
    return parser.parse_args()


# ── 웹 서버 모드 ──────────────────────────────────────
def run_web(args):
    import uvicorn
    logger.info(f"🌐 웹 서버 시작: http://0.0.0.0:{args.port}")
    uvicorn.run(
        "web.backend.server:app",
        host="0.0.0.0",
        port=args.port,
        reload=False,
        log_level="info",
    )


# ── 헤드리스 모드 ─────────────────────────────────────
async def run_headless(args):
    """UI 없이 봇/자동매매만 실행."""
    from core.app import CoinHTSApp
    from core.config import get_config

    config = get_config()
    config.default_symbols = args.symbols

    app = CoinHTSApp(config)
    logger.info("🤖 헤드리스 모드 시작 (Ctrl+C로 종료)")

    try:
        await app.start()
        while True:
            await asyncio.sleep(60)
            status = app.get_status()
            logger.info(
                f"[상태] 포지션={status['paper_trader']['open_pos']} | "
                f"일지={status['journal_count']}건 | "
                f"플러그인={len(status['plugins'])}개"
            )
    except KeyboardInterrupt:
        logger.info("종료 중...")
    finally:
        await app.stop()


# ── 데스크탑 UI 모드 ──────────────────────────────────
def run_desktop(args):
    try:
        from PySide6.QtWidgets import QApplication
        from PySide6.QtCore import Qt
        from qasync import QEventLoop
        from ui.main_window import MainWindow, apply_dark_theme
        from core.app import CoinHTSApp
        from core.config import get_config
    except ImportError as e:
        logger.error(f"PySide6 패키지 필요: {e}")
        logger.info("pip install PySide6 pyqtgraph qasync")
        sys.exit(1)

    import asyncio
    from qasync import QEventLoop

    qt_app = QApplication(sys.argv)
    qt_app.setAttribute(Qt.ApplicationAttribute.AA_UseHighDpiPixmaps)
    apply_dark_theme(qt_app)

    loop = QEventLoop(qt_app)
    asyncio.set_event_loop(loop)

    # CoinHTS 앱 초기화
    config = get_config()
    config.default_symbols = args.symbols
    hts_app = CoinHTSApp(config)

    # 메인 윈도우
    window = MainWindow(config)
    window.set_trader(hts_app.paper_trader)
    window.show()

    async def main():
        await hts_app.start()
        # SMC 분석 결과 → UI 업데이트 연결
        async def on_candle_close(candle):
            sym = candle.symbol
            if sym in hts_app._candle_cache:
                try:
                    smc = hts_app.smc_engine.analyze(hts_app._candle_cache[sym])
                    window.update_smc_result(smc, hts_app._candle_cache[sym])
                except Exception:
                    pass
        hts_app.bus.subscribe("candle", on_candle_close)

    with loop:
        loop.run_until_complete(main())
        loop.run_forever()


# ── 메인 ──────────────────────────────────────────────
if __name__ == "__main__":
    args = parse_args()

    # 로그 레벨 재설정
    logging.getLogger().setLevel(getattr(logging, args.log_level))

    logger.info(f"""
╔══════════════════════════════════════════════╗
║  CoinHTS — Professional Crypto Terminal      ║
║  버전: v1.5  |  심볼: {', '.join(args.symbols[:2])}       ║
╚══════════════════════════════════════════════╝
""")

    if args.live and not args.sandbox:
        logger.warning("⚠️  실거래 모드 — 실제 자금이 사용됩니다!")
        confirm = input("계속하려면 'yes' 입력: ")
        if confirm.strip().lower() != "yes":
            sys.exit(0)

    if args.web:
        run_web(args)
    elif args.headless:
        asyncio.run(run_headless(args))
    else:
        run_desktop(args)
