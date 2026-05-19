#!/usr/bin/env python3
"""
Polymarket Signal Bot
- Scans BTC short-term markets (Up/Down 15m etc) every SCAN_INTERVAL_MINUTES.
- Tracks top-25 traders; if leaderboard unavailable, falls back to BTC mempool watching.
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
from btc_watcher import BTCWatcher
from telegram_bot import (
    send_message,
    send_market_signal,
    send_batch_header,
    send_no_signals,
    send_trader_signal,
    send_btc_signal,
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
_btc_watcher = BTCWatcher()
_use_btc_watcher = False   # flips True if leaderboard fails


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
    logger.info("Running market scan (≤%s day markets)...", MAX_DAYS_TO_EXPIRY)
    try:
        markets = fetch_short_term_markets()
        if not markets:
            logger.info("No short-term markets found.")
            return

        signals = get_signal_markets(markets)
        logger.info("%d signal markets from %d fetched", len(signals), len(markets))

        if not signals:
            send_no_signals(len(markets))
            return

        send_batch_header(len(signals), len(markets))
        time.sleep(0.5)
        for s in signals:
            send_market_signal(s)
            time.sleep(0.4)

    except Exception:
        logger.exception("Error during market scan")


# ── Trader / BTC watcher loop ─────────────────────────────────────────────────

def _watcher_loop() -> None:
    """
    Background thread: either polls top traders OR watches the BTC mempool.
    Switches to BTC watcher automatically if the leaderboard never loads.
    """
    global _use_btc_watcher

    btc_poll_interval = 30   # seconds between mempool checks

    while True:
        if _use_btc_watcher:
            signal = _btc_watcher.check()
            if signal:
                # Find the current BTC 15m market URL to attach
                _send_btc_onchain_signal(signal)
            time.sleep(btc_poll_interval)
        else:
            # Try trader check
            try:
                new_trades = _trader_tracker.check_new_trades()
                for trade in new_trades:
                    send_trader_signal(trade)
                    time.sleep(0.3)
            except Exception:
                logger.exception("Trader check error")
            time.sleep(TRACKER_POLL_MINUTES * 60)


def _send_btc_onchain_signal(signal: dict) -> None:
    """Fetch the live BTC 15m market URL then send the on-chain signal."""
    try:
        markets = fetch_short_term_markets()
        btc_market = next(
            (m for m in get_signal_markets(markets)
             if "btc" in m["question"].lower() and
             any(kw in m["question"].lower() for kw in ("up or down", "up/down", "15m", "15 min"))),
            None,
        )
        url = btc_market["url"] if btc_market else "https://polymarket.com"
        expires = btc_market["expires_str"] if btc_market else "~15m"
    except Exception:
        url = "https://polymarket.com"
        expires = "~15m"

    send_btc_signal(signal, url, expires)


# ── Entry point ───────────────────────────────────────────────────────────────

def main() -> None:
    global _use_btc_watcher

    if not validate_config():
        sys.exit(1)

    logger.info("Polymarket Signal Bot starting up")

    # Try to load top traders
    logger.info("Loading top %d traders...", TOP_TRADERS_COUNT)
    _trader_tracker.refresh_traders()

    if not _trader_tracker._traders:
        _use_btc_watcher = True
        logger.info("Leaderboard unavailable — switching to BTC mempool watcher")
        send_message(
            f"🤖 <b>Polymarket Signal Bot Online</b>\n\n"
            f"📊 Market signals: every <b>{SCAN_INTERVAL_MINUTES} min</b>\n"
            f"⛓ Leaderboard unavailable — watching <b>BTC mempool</b> instead\n"
            f"   (fee spikes, whale txns, mempool drains → BTC 15m signals)\n\n"
            f"Starting scan now..."
        )
    else:
        send_message(
            f"🤖 <b>Polymarket Signal Bot Online</b>\n\n"
            f"📊 Market signals: every <b>{SCAN_INTERVAL_MINUTES} min</b>\n"
            f"👤 Tracking top <b>{len(_trader_tracker._traders)}</b> traders "
            f"every <b>{TRACKER_POLL_MINUTES} min</b>\n\n"
            f"Starting scan now..."
        )

    run_market_scan()

    schedule.every(SCAN_INTERVAL_MINUTES).minutes.do(run_market_scan)
    schedule.every(60).minutes.do(_trader_tracker.refresh_traders)

    t = threading.Thread(target=_watcher_loop, daemon=True)
    t.start()
    logger.info("Background watcher started (mode: %s)", "BTC mempool" if _use_btc_watcher else "trader tracker")

    while True:
        schedule.run_pending()
        time.sleep(15)


if __name__ == "__main__":
    main()
