"""
Top-25 profitable trader tracker.
Fetches the Polymarket leaderboard, then polls each trader's activity.
Returns new trades the moment they appear.
"""

import logging
import time
import requests
from config import DATA_API_BASE, CLOB_API_BASE, TOP_TRADERS_COUNT

logger = logging.getLogger(__name__)

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept": "application/json",
    "Origin": "https://polymarket.com",
    "Referer": "https://polymarket.com/",
}

_LEADERBOARD_URLS = [
    f"{DATA_API_BASE}/leaderboard?limit={TOP_TRADERS_COUNT}&window=all",
    f"{DATA_API_BASE}/leaderboard?limit={TOP_TRADERS_COUNT}",
    f"{DATA_API_BASE}/profiles?limit={TOP_TRADERS_COUNT}&sortBy=pnl&sortDirection=desc",
    f"{DATA_API_BASE}/profiles?limit={TOP_TRADERS_COUNT}&sort=profit",
    f"https://polymarket.com/api/profiles/leaderboard?limit={TOP_TRADERS_COUNT}&window=all",
    f"https://polymarket.com/api/leaderboard?limit={TOP_TRADERS_COUNT}",
    f"https://gamma-api.polymarket.com/leaderboard?limit={TOP_TRADERS_COUNT}",
]

_ACTIVITY_URLS = [
    f"{DATA_API_BASE}/activity?user={{addr}}&limit=20&sortBy=timestamp&sortDirection=desc",
    f"{CLOB_API_BASE}/trades?maker_address={{addr}}&limit=20",
    f"{DATA_API_BASE}/positions?user={{addr}}&limit=20&sortBy=updatedAt&sortDirection=desc",
]


def _get(url: str) -> list | dict | None:
    try:
        resp = requests.get(url, headers=HEADERS, timeout=20)
        if resp.ok:
            return resp.json()
        logger.debug("GET %s → %d", url, resp.status_code)
    except Exception as e:
        logger.debug("GET %s failed: %s", url, e)
    return None


def _normalize_traders(raw: list) -> list[dict]:
    result = []
    for t in raw:
        addr = (
            t.get("proxyWallet") or t.get("address") or
            t.get("walletAddress") or t.get("maker_address", "")
        )
        if not addr:
            continue
        name = t.get("name") or t.get("pseudonym") or t.get("username") or f"{addr[:6]}...{addr[-4:]}"
        pnl = float(t.get("pnl") or t.get("profitLoss") or t.get("profit") or 0)
        result.append({"address": addr, "name": name, "pnl": pnl})
    return result


def get_top_traders() -> list[dict]:
    for url in _LEADERBOARD_URLS:
        data = _get(url)
        if not data:
            continue
        raw = data if isinstance(data, list) else (
            data.get("data") or data.get("profiles") or
            data.get("results") or data.get("leaderboard") or []
        )
        traders = _normalize_traders(raw)
        if traders:
            logger.info("Loaded %d top traders from: %s", len(traders), url)
            return traders[:TOP_TRADERS_COUNT]
        logger.debug("URL returned data but no traders: %s | keys=%s", url, list(data.keys()) if isinstance(data, dict) else type(data))

    # Manual fallback: read addresses from TRADER_ADDRESSES env var
    # Set it in Railway as a comma-separated list of proxy wallet addresses
    import os
    manual = [a.strip() for a in os.getenv("TRADER_ADDRESSES", "").split(",") if a.strip()]
    if manual:
        logger.info("Using %d manually configured trader addresses", len(manual))
        return [{"address": a, "name": f"{a[:6]}...{a[-4:]}", "pnl": 0} for a in manual]

    logger.warning(
        "Leaderboard unavailable. Set TRADER_ADDRESSES in Railway with comma-separated "
        "Polymarket proxy wallet addresses to track specific traders."
    )
    return []


def _normalize_trade(raw: dict, trader: dict) -> dict:
    """Normalise a raw trade/activity record into a standard shape."""
    # Try to extract common fields across different API response shapes
    side = str(raw.get("side") or raw.get("type") or raw.get("action") or "buy").upper()
    if side not in ("BUY", "SELL"):
        side = "BUY" if "buy" in side.lower() else "SELL"

    outcome = str(raw.get("outcome") or raw.get("outcomeName") or raw.get("side") or "YES").upper()
    if outcome not in ("YES", "NO", "UP", "DOWN"):
        outcome_index = raw.get("outcomeIndex", raw.get("outcome_index", 0))
        outcome = "YES" if outcome_index == 0 else "NO"

    price = float(raw.get("price") or raw.get("avgPrice") or raw.get("averagePrice") or 0)
    size = float(raw.get("size") or raw.get("amount") or raw.get("usdcSize") or 0)

    question = (
        raw.get("title") or raw.get("question") or
        raw.get("market") or raw.get("marketTitle") or
        raw.get("conditionId", "Unknown market")
    )

    slug = raw.get("slug") or raw.get("eventSlug") or ""
    market_url = f"https://polymarket.com/event/{slug}" if slug else "https://polymarket.com"

    trade_id = (
        str(raw.get("id") or raw.get("tradeId") or raw.get("transactionHash") or
            raw.get("txHash") or f"{trader['address']}{raw.get('timestamp','')}{price}{size}")
    )

    return {
        "trade_id": trade_id,
        "trader_name": trader["name"],
        "trader_address": trader["address"],
        "trader_pnl": trader["pnl"],
        "side": side,
        "outcome": outcome,
        "price_cents": round(price * 100, 1) if price <= 1 else round(price, 1),
        "size_usd": round(size, 2),
        "question": question,
        "market_url": market_url,
        "timestamp": raw.get("timestamp") or raw.get("createdAt") or raw.get("matchTime") or "",
    }


class TraderTracker:
    """Poll top traders for new activity and emit new trade events."""

    def __init__(self):
        self._traders: list[dict] = []
        self._seen: dict[str, set] = {}

    def refresh_traders(self) -> None:
        traders = get_top_traders()
        if traders:
            self._traders = traders
            for t in traders:
                self._seen.setdefault(t["address"], set())
            logger.info("Now tracking %d traders", len(self._traders))

    def check_new_trades(self) -> list[dict]:
        new_trades = []

        for trader in self._traders:
            addr = trader["address"]
            activity = []

            for url_tmpl in _ACTIVITY_URLS:
                url = url_tmpl.format(addr=addr)
                data = _get(url)
                if not data:
                    continue
                raw_list = data if isinstance(data, list) else data.get("data", data.get("trades", data.get("activity", [])))
                if raw_list:
                    activity = raw_list
                    break

            for raw in activity:
                trade = _normalize_trade(raw, trader)
                if trade["trade_id"] not in self._seen[addr]:
                    self._seen[addr].add(trade["trade_id"])
                    new_trades.append(trade)

            time.sleep(0.15)

        return new_trades
