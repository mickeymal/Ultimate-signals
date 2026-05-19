#!/usr/bin/env python3
"""
Polymarket Signal Bot
Scans all short-term Polymarket markets, analyses each with Claude + web search,
and sends Telegram alerts only when AI confidence >= CONFIDENCE_THRESHOLD.
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


def run_scan() -> None:
    logger.info("Starting scan cycle")
    try:
        all_markets = fetch_all_markets()
        if not all_markets:
            send_message("⚠️ Polymarket returned 0 markets — API may be down.")
            return

        short_term = get_short_term_markets(all_markets)
        logger.info(
            "%d total markets → %d short-term candidates (≤%dd, ≥$%.0f liquidity)",
            len(all_markets), len(short_term), MAX_DAYS_TO_EXPIRY, 500,
        )

        if not short_term:
            send_message("🔍 No short-term markets found this scan.")
            return

        signals = analyse_markets(short_term)
        send_signals(signals, len(all_markets), len(short_term))

    except Exception:
        logger.exception("Unexpected error during scan")
        send_message("❌ Bot encountered an error during scan. Check logs.")


def main() -> None:
    if not validate_config():
        sys.exit(1)

    logger.info("Polymarket Signal Bot starting")
    logger.info("Confidence threshold: %.0f%%", CONFIDENCE_THRESHOLD)
    logger.info("Max expiry: %d days | Scan every %d min", MAX_DAYS_TO_EXPIRY, SCAN_INTERVAL_MINUTES)

    send_message(
        f"🤖 <b>Polymarket Signal Bot Online</b>\n\n"
        f"AI-powered analysis · {CONFIDENCE_THRESHOLD:.0f}%+ confidence only\n"
        f"Short-term trades ≤ {MAX_DAYS_TO_EXPIRY} days · Scans every {SCAN_INTERVAL_MINUTES} min\n\n"
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
