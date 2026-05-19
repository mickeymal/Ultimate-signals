#!/usr/bin/env python3
"""
Polymarket Signal Bot
Scans every active Polymarket market and alerts via Telegram when a market
has an unrealistically low or near-certain probability.
"""

import logging
import sys
import time
import schedule

from config import (
    TELEGRAM_BOT_TOKEN,
    TELEGRAM_CHAT_ID,
    LOW_THRESHOLD,
    HIGH_THRESHOLD,
    SCAN_INTERVAL_MINUTES,
)
from polymarket import fetch_all_markets, filter_signal_markets
from telegram_bot import send_message, send_scan_summary

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler("polymarket_bot.log"),
    ],
)
logger = logging.getLogger(__name__)


def validate_config() -> bool:
    if not TELEGRAM_BOT_TOKEN or TELEGRAM_BOT_TOKEN == "your_bot_token_here":
        logger.error("TELEGRAM_BOT_TOKEN is not set. Copy .env.example to .env and fill it in.")
        return False
    if not TELEGRAM_CHAT_ID or TELEGRAM_CHAT_ID == "your_chat_id_here":
        logger.error("TELEGRAM_CHAT_ID is not set.")
        return False
    return True


def run_scan() -> None:
    logger.info(
        "Running scan — thresholds: LOW=%.1f%% HIGH=%.1f%%",
        LOW_THRESHOLD, HIGH_THRESHOLD,
    )
    try:
        markets = fetch_all_markets()
        if not markets:
            logger.warning("No markets returned from Polymarket API.")
            send_message("⚠️ Polymarket scan returned 0 markets — API may be down.")
            return

        signals = filter_signal_markets(markets, LOW_THRESHOLD, HIGH_THRESHOLD)
        logger.info(
            "Scan complete: %d total markets, %d signals found",
            len(markets), len(signals),
        )
        send_scan_summary(signals, LOW_THRESHOLD, HIGH_THRESHOLD, len(markets))

    except Exception:
        logger.exception("Unexpected error during scan")
        send_message("❌ Polymarket bot encountered an error during scan. Check logs.")


def main() -> None:
    if not validate_config():
        sys.exit(1)

    logger.info("Polymarket Signal Bot starting up")
    logger.info("Scan interval: every %d minutes", SCAN_INTERVAL_MINUTES)
    logger.info("Signal thresholds: <%.1f%% or >%.1f%%", LOW_THRESHOLD, HIGH_THRESHOLD)

    send_message(
        f"🤖 <b>Polymarket Signal Bot Online</b>\n\n"
        f"Scanning <b>all</b> Polymarket markets every <b>{SCAN_INTERVAL_MINUTES} min</b>\n"
        f"Alerting when probability &lt; <b>{LOW_THRESHOLD}%</b> or &gt; <b>{HIGH_THRESHOLD}%</b>\n\n"
        f"Running first scan now..."
    )

    run_scan()

    schedule.every(SCAN_INTERVAL_MINUTES).minutes.do(run_scan)

    logger.info("Scheduler started. Next scan in %d minutes.", SCAN_INTERVAL_MINUTES)
    while True:
        schedule.run_pending()
        time.sleep(30)


if __name__ == "__main__":
    main()
