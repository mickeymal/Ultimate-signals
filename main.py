#!/usr/bin/env python3
"""
Polymarket Signal Bot
- Scans all short-term markets every SCAN_INTERVAL_MINUTES and sends signals immediately.
- Tracks the top 25 most profitable traders every TRACKER_POLL_MINUTES and alerts on every trade.
"""

import logging
import sys
import time
import threading
import schedule

from config import (
    TELEGRAM_BOT_TOKEN,
    TELEGRAM_CHAT_ID,
    SCAN_INTERVAL_MINUTES,
    TRACKER_POLL_MINUTES,
    MAX_DAYS_TO_EXPIRY,
    TOP_TRADERS_COUNT,
)
from polymarket import fetch_short_term_markets, get_signal_markets
from tracker import TraderTracker
from telegram_bot import (
    send_message,
    send_market_signal,
    send_batch_header,
    send_no_signals,
    send_trader_signal,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler("polymarket_bot.log"),
    ],
)
logger = logging.getLogger(__name__)

_trader_tracker = TraderTracker()


def validate_config() -> bool:
    ok = True
    if not TELEGRAM_BOT_TOKEN or TELEGRAM_BOT_TOKEN == "your_bot_token_here":
        logger.error("TELEGRAM_BOT_TOKEN is not set.")
        ok = False
    if not TELEGRAM_CHAT_ID or TELEGRAM_CHAT_ID == "your_chat_id_here":
        logger.error("TELEGRAM_CHAT_ID is not set.")
        ok = False
    return ok


# ── Market scanner ────────────────────────────────────────────────────────────

def run_market_scan() -> None:
    logger.info("Running market scan (≤%d day markets)...", MAX_DAYS_TO_EXPIRY)
    try:
        all_markets = fetch_short_term_markets()
        if not all_markets:
            logger.warning("No short-term markets returned from Polymarket API.")
            return

        signals = get_signal_markets(all_markets)
        logger.info("%d signal markets found from %d short-term", len(signals), len(all_markets))

        if not signals:
            send_no_signals(len(all_markets))
            return

        send_batch_header(len(signals), len(all_markets))
        time.sleep(0.5)

        for s in signals:
            send_market_signal(s)
            time.sleep(0.4)

    except Exception:
        logger.exception("Error during market scan")


# ── Trader tracker ────────────────────────────────────────────────────────────

def run_trader_check() -> None:
    logger.info("Checking top trader activity...")
    try:
        new_trades = _trader_tracker.check_new_trades()
        if not new_trades:
            logger.info("No new trader activity")
            return

        logger.info("%d new trader trades found — alerting now", len(new_trades))
        for trade in new_trades:
            send_trader_signal(trade)
            time.sleep(0.3)

    except Exception:
        logger.exception("Error during trader check")


def _trader_loop() -> None:
    """Run the trader tracker on its own tight loop in a background thread."""
    while True:
        run_trader_check()
        time.sleep(TRACKER_POLL_MINUTES * 60)


# ── Entry point ───────────────────────────────────────────────────────────────

def main() -> None:
    if not validate_config():
        sys.exit(1)

    logger.info("Polymarket Signal Bot starting up")
    logger.info(
        "Market scan: every %d min | Trader poll: every %d min | Top traders: %d",
        SCAN_INTERVAL_MINUTES, TRACKER_POLL_MINUTES, TOP_TRADERS_COUNT,
    )

    send_message(
        f"🤖 <b>Polymarket Signal Bot Online</b>\n\n"
        f"📊 Market signals: every <b>{SCAN_INTERVAL_MINUTES} min</b> "
        f"(≤{MAX_DAYS_TO_EXPIRY}d markets)\n"
        f"👤 Top <b>{TOP_TRADERS_COUNT}</b> traders monitored every <b>{TRACKER_POLL_MINUTES} min</b>\n\n"
        f"Starting now..."
    )

    # Load leaderboard at startup
    logger.info("Loading top %d traders...", TOP_TRADERS_COUNT)
    _trader_tracker.refresh_traders()

    # First market scan immediately
    run_market_scan()

    # First trader check immediately
    run_trader_check()

    # Schedule regular market scans on the main thread
    schedule.every(SCAN_INTERVAL_MINUTES).minutes.do(run_market_scan)

    # Refresh leaderboard once an hour
    schedule.every(60).minutes.do(_trader_tracker.refresh_traders)

    # Trader checks run on a background thread so they never block market scans
    t = threading.Thread(target=_trader_loop, daemon=True)
    t.start()
    logger.info("Trader tracker running in background thread")

    while True:
        schedule.run_pending()
        time.sleep(15)


if __name__ == "__main__":
    main()
