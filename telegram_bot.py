import html
import logging
import time
import requests
from config import TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID

logger = logging.getLogger(__name__)
API_URL = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}"
MAX_LEN = 4096


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
                wait = resp.json().get("parameters", {}).get("retry_after", 5)
                time.sleep(wait)
                continue
            resp.raise_for_status()
            return True
        except requests.RequestException as e:
            logger.warning("Telegram send failed (attempt %d): %s", attempt + 1, e)
            time.sleep(2 ** attempt)
    return False


def send_message(text: str) -> bool:
    if len(text) <= MAX_LEN:
        return _send_raw(text)
    chunks, remaining = [], text
    while remaining:
        chunk = remaining[:MAX_LEN]
        split = chunk.rfind("\n")
        if split > MAX_LEN // 2:
            chunk = chunk[:split]
        chunks.append(chunk)
        remaining = remaining[len(chunk):]
    ok = True
    for chunk in chunks:
        if not _send_raw(chunk):
            ok = False
        time.sleep(0.4)
    return ok


def _outcome_line(outcomes: list[tuple[str, float]]) -> str:
    parts = []
    for name, cents in outcomes:
        name_up = name.upper()
        if name_up in ("UP", "YES"):
            emoji = "⬆️"
        elif name_up in ("DOWN", "NO"):
            emoji = "⬇️"
        else:
            emoji = "🔘"
        parts.append(f"{emoji} <b>{html.escape(name)}</b> {cents:.0f}¢")
    return "  |  ".join(parts)


def send_market_signal(market: dict) -> bool:
    """Send one market signal message formatted like the Polymarket app."""
    outcomes = market["outcomes"]
    direction = html.escape(market["signal_direction"])
    price = market["signal_price"]
    question = html.escape(market["question"])
    expires = market["expires_str"]
    liquidity = market["liquidity"]
    url = market["url"]

    arrow = "⬆️" if direction in ("YES", "UP") else "⬇️"

    text = (
        f"⚡ <b>MARKET SIGNAL</b>\n"
        f"<b>{question}</b>\n\n"
        f"{_outcome_line(outcomes)}\n\n"
        f"Signal: {arrow} <b>BUY {direction}</b> @ <b>{price:.0f}¢</b>\n"
        f"💧 Liquidity: <b>${liquidity:,.0f}</b>  |  ⏱ Expires: <b>{expires}</b>\n"
        f"🔗 <a href=\"{url}\">{url}</a>"
    )
    return send_message(text)


def send_batch_header(count: int, total_scanned: int) -> bool:
    return send_message(
        f"📊 <b>{count} MARKET SIGNAL{'S' if count != 1 else ''}</b>  "
        f"<i>({total_scanned} markets scanned)</i>"
    )


def send_trader_signal(trade: dict) -> bool:
    """Send an immediate alert when a top trader makes a move."""
    pnl = trade["trader_pnl"]
    pnl_str = f"${pnl:,.0f}" if pnl >= 0 else f"-${abs(pnl):,.0f}"

    side = trade["side"]
    outcome = trade["outcome"]
    price = trade["price_cents"]
    size = trade["size_usd"]
    question = html.escape(trade["question"])
    name = html.escape(trade["trader_name"])
    url = trade["market_url"]

    if side == "BUY":
        action_emoji = "🟢"
        action = f"BUY {outcome}"
    else:
        action_emoji = "🔴"
        action = f"SELL {outcome}"

    size_str = f"${size:,.0f}" if size >= 1 else f"{size:.2f}"

    text = (
        f"👤 <b>TOP TRADER ALERT</b>\n"
        f"{action_emoji} <b>{name}</b>  <i>(All-time P&L: {pnl_str})</i>\n\n"
        f"<b>{action}</b> @ <b>{price:.0f}¢</b>  |  Size: <b>{size_str}</b>\n"
        f"📋 {question}\n"
        f"🔗 <a href=\"{url}\">{url}</a>"
    )
    return send_message(text)


def send_no_signals(total_scanned: int) -> bool:
    return send_message(
        f"🔍 Scan complete — {total_scanned} markets checked, no short-term signals right now."
    )
