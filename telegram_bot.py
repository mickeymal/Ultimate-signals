import html
import logging
import time
import requests
from config import TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID

logger = logging.getLogger(__name__)

API_URL = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}"

MAX_MESSAGE_LENGTH = 4096


def _send_raw(text: str) -> bool:
    """Send a single message via Telegram Bot API with retry."""
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }
    for attempt in range(4):
        try:
            resp = requests.post(f"{API_URL}/sendMessage", json=payload, timeout=15)
            if resp.status_code == 429:
                retry_after = resp.json().get("parameters", {}).get("retry_after", 5)
                logger.warning("Telegram rate limited — waiting %ds", retry_after)
                time.sleep(retry_after)
                continue
            resp.raise_for_status()
            return True
        except requests.RequestException as e:
            wait = 2 ** attempt
            logger.warning("Telegram send failed (attempt %d): %s", attempt + 1, e)
            time.sleep(wait)
    return False


def send_message(text: str) -> bool:
    """Send text, splitting into chunks if it exceeds Telegram's limit."""
    if len(text) <= MAX_MESSAGE_LENGTH:
        return _send_raw(text)

    chunks = []
    while text:
        chunk = text[:MAX_MESSAGE_LENGTH]
        # Try to break at a newline so messages aren't mid-line
        split_at = chunk.rfind("\n")
        if split_at > MAX_MESSAGE_LENGTH // 2:
            chunk = chunk[:split_at]
        chunks.append(chunk)
        text = text[len(chunk):]

    success = True
    for chunk in chunks:
        if not _send_raw(chunk):
            success = False
        time.sleep(0.5)
    return success


def send_scan_summary(signals: list[dict], low: float, high: float, total_scanned: int) -> None:
    """Send a header message then one message per signal batch."""
    if not signals:
        send_message(
            f"✅ <b>Polymarket Scan Complete</b>\n\n"
            f"Scanned <b>{total_scanned}</b> markets.\n"
            f"No signals found outside {low}%–{high}% range right now."
        )
        return

    header = (
        f"📊 <b>POLYMARKET SIGNALS</b>\n"
        f"{len(signals)} short-term trades found | {total_scanned} markets scanned"
    )
    send_message(header)
    time.sleep(0.5)

    # Send one signal per message so each is clean and actionable
    for s in signals:
        question = html.escape(str(s["question"]))
        days = s.get("days_left", "?")
        days_str = f"{days}d" if isinstance(days, float) and days >= 1 else f"{round(days * 24)}h"
        send_message(
            f"{s['signal_type']}  <b>{s['direction']}</b>\n"
            f"<b>{question}</b>\n"
            f"Prob: <b>{s['probability']}%</b>  |  "
            f"Liquidity: <b>${s['liquidity']:,.0f}</b>  |  "
            f"Expires: <b>{days_str}</b>\n"
            f"🔗 <a href=\"{s['url']}\">{s['url']}</a>"
        )
        time.sleep(0.4)
