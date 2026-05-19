#!/usr/bin/env python3
"""
Polymarket Signal Bot
Scans all short-term Polymarket markets, analyses each with Groq/Llama,
and sends Telegram alerts when AI confidence >= CONFIDENCE_THRESHOLD.
If a scan finds no signals, it rescans after RESCAN_DELAY_MINUTES.
"""

import logging
import sys
import time
import schedule

from config import (
    TELEGRAM_BOT_TOKEN,
    TELEGRAM_CHAT_ID,
    GROQ_API_KEY,
    CONFIDENCE_THRESHOLD,
    SCAN_INTERVAL_MINUTES,
    RESCAN_DELAY_MINUTES,
    MAX_DAYS_TO_EXPIRY,
)
from polymarket import fetch_all_markets, get_short_term_markets
from analyzer import analyse_markets
from telegram_bot import send_message, send_signals

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
    ok = True
    if not TELEGRAM_BOT_TOKEN or TELEGRAM_BOT_TOKEN == "your_bot_token_here":
        logger.error("TELEGRAM_BOT_TOKEN is not set.")
        ok = False
    if not TELEGRAM_CHAT_ID or TELEGRAM_CHAT_ID == "your_chat_id_here":
        logger.error("TELEGRAM_CHAT_ID is not set.")
        ok = False
    if not GROQ_API_KEY or GROQ_API_KEY == "your_groq_key_here":
        logger.error("GROQ_API_KEY is not set. Get a free key at console.groq.com")
        ok = False
    return ok


def _do_scan() -> int:
    """
    Run one full scan cycle. Returns the number of signals found.
    Returns -1 on a hard error (API down etc).
    """
    try:
        all_markets = fetch_all_markets()
        if not all_markets:
            send_message("⚠️ Polymarket returned 0 markets — API may be down.")
            return -1

        short_term = get_short_term_markets(all_markets)
        logger.info(
            "%d total markets → %d short-term candidates (≤%dd)",
            len(all_markets), len(short_term), MAX_DAYS_TO_EXPIRY,
        )

        if not short_term:
            logger.info("No short-term markets found this scan.")
            return 0

        signals = analyse_markets(short_term)
        send_signals(signals, len(all_markets), len(short_term))
        return len(signals)

    except Exception:
        logger.exception("Unexpected error during scan")
        send_message("❌ Bot encountered an error during scan. Check logs.")
        return -1


def run_scan() -> None:
    """Scheduled entry point. Rescans after a short delay if nothing was found."""
    logger.info("Starting scan cycle")
    found = _do_scan()

    if found == 0:
        logger.info(
            "No signals found — rescanning in %d minutes", RESCAN_DELAY_MINUTES
        )
        time.sleep(RESCAN_DELAY_MINUTES * 60)
        logger.info("Rescanning now...")
        _do_scan()


def main() -> None:
    if not validate_config():
        sys.exit(1)

    logger.info("Polymarket Signal Bot starting")
    logger.info("Confidence threshold: %.0f%%", CONFIDENCE_THRESHOLD)
    logger.info(
        "Max expiry: %d days | Scan every %d min | Rescan delay: %d min",
        MAX_DAYS_TO_EXPIRY, SCAN_INTERVAL_MINUTES, RESCAN_DELAY_MINUTES,
    )

    send_message(
        f"🤖 <b>Polymarket Signal Bot Online</b>\n\n"
        f"Confidence threshold: <b>{CONFIDENCE_THRESHOLD:.0f}%</b>\n"
        f"Short-term trades ≤ <b>{MAX_DAYS_TO_EXPIRY} days</b>\n"
        f"Scans every <b>{SCAN_INTERVAL_MINUTES} min</b> · "
        f"Rescans in <b>{RESCAN_DELAY_MINUTES} min</b> if nothing found\n\n"
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
