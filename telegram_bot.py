import html
import logging
import time
import requests
from config import TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID

logger = logging.getLogger(__name__)

API_URL = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}"
MAX_MESSAGE_LENGTH = 4096


def _send_raw(text: str) -> bool:
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
            logger.warning("Telegram send failed (attempt %d): %s", attempt + 1, e)
            time.sleep(2 ** attempt)
    return False


def send_message(text: str) -> bool:
    if len(text) <= MAX_MESSAGE_LENGTH:
        return _send_raw(text)
    chunks = []
    while text:
        chunk = text[:MAX_MESSAGE_LENGTH]
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


def send_signals(signals: list[dict], total_scanned: int, total_analysed: int) -> None:
    if not signals:
        send_message(
            f"🔍 <b>Scan complete</b>\n"
            f"Scanned {total_scanned} markets · Analysed {total_analysed} short-term\n"
            f"No high-confidence signals this round."
        )
        return

    send_message(
        f"📊 <b>{len(signals)} SIGNAL{'S' if len(signals) > 1 else ''} FOUND</b>\n"
        f"Scanned {total_scanned} markets · Analysed {total_analysed} · "
        f"Signalled {len(signals)}"
    )
    time.sleep(0.5)

    for s in signals:
        direction = s["direction"]
        emoji = "🟢" if direction == "YES" else "🔴"
        days = s["days_left"]
        expires = f"{days}d" if days >= 1 else f"{round(days * 24)}h"
        poly_prob = s.get("polymarket_prob")
        poly_str = f"Polymarket: {poly_prob}%  |  " if poly_prob is not None else ""

        send_message(
            f"{emoji} <b>{html.escape(s['trade'])}</b>  —  <b>{s['confidence']:.0f}% confident</b>\n"
            f"<b>{html.escape(s['question'])}</b>\n"
            f"📝 {html.escape(s['reason'])}\n"
            f"{poly_str}Liquidity: ${s['liquidity']:,.0f}  |  Expires: {expires}\n"
            f"🔗 <a href=\"{s['url']}\">{s['url']}</a>"
        )
        time.sleep(0.4)
