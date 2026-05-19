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
        f"🚨 <b>POLYMARKET SIGNAL ALERT</b> 🚨\n\n"
        f"Scanned <b>{total_scanned}</b> markets\n"
        f"Found <b>{len(signals)}</b> extreme-probability signals\n"
        f"Thresholds: &lt;{low}% (long shot) | &gt;{high}% (near certain)\n"
        f"{'─' * 30}"
    )
    send_message(header)
    time.sleep(0.5)

    # Group and send in batches of 10
    batch_size = 10
    for i in range(0, len(signals), batch_size):
        batch = signals[i:i + batch_size]
        lines = []
        for s in batch:
            end = f" | Ends: {s['end_date'][:10]}" if s.get("end_date") else ""
            cat = f" [{html.escape(str(s['category']))}]" if s.get("category") else ""
            question = html.escape(str(s["question"]))
            lines.append(
                f"{s['signal_type']}\n"
                f"<b>{question}</b>{cat}\n"
                f"Probability: <b>{s['probability']}%</b> | "
                f"Liquidity: ${s['liquidity']:,.0f}{end}\n"
                f"🔗 <a href=\"{s['url']}\">View Market</a>\n"
                f"{'─' * 30}"
            )
        send_message("\n".join(lines))
        time.sleep(0.8)
