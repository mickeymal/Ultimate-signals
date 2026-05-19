import json
import time
import logging
import requests
from datetime import datetime, timezone, timedelta
from config import GAMMA_API_BASE, PAGE_SIZE, MIN_LIQUIDITY, MAX_DAYS_TO_EXPIRY

logger = logging.getLogger(__name__)

SESSION = requests.Session()
SESSION.headers.update({
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "Origin": "https://polymarket.com",
    "Referer": "https://polymarket.com/",
})


def _get(url: str, params: dict) -> dict | None:
    for attempt in range(4):
        try:
            resp = SESSION.get(url, params=params, timeout=30)
            # 422 at high offsets means we've hit the API's pagination limit — not an error
            if resp.status_code == 422:
                logger.info("Reached API pagination limit at offset=%s", params.get("offset"))
                return None
            resp.raise_for_status()
            return resp.json()
        except requests.RequestException as e:
            wait = 2 ** attempt
            logger.warning("Request failed (attempt %d): %s — retrying in %ds", attempt + 1, e, wait)
            time.sleep(wait)
    logger.error("All retries exhausted for %s", url)
    return None


def fetch_all_markets() -> list[dict]:
    """Fetch every active, non-closed binary market from Polymarket Gamma API."""
    markets = []
    offset = 0

    logger.info("Starting full Polymarket scan...")

    while True:
        params = {
            "active": "true",
            "closed": "false",
            "limit": PAGE_SIZE,
            "offset": offset,
            "order": "createdAt",
            "ascending": "false",
        }
        data = _get(f"{GAMMA_API_BASE}/markets", params)
        if not data:
            break

        # Gamma API returns a list directly
        if isinstance(data, list):
            batch = data
        else:
            batch = data.get("data", data.get("markets", []))

        if not batch:
            break

        markets.extend(batch)
        logger.info("Fetched %d markets so far (page offset=%d)", len(markets), offset)

        if len(batch) < PAGE_SIZE:
            # Last page
            break

        offset += PAGE_SIZE
        time.sleep(0.3)  # polite rate limiting

    logger.info("Total markets fetched: %d", len(markets))
    return markets


def extract_probability(market: dict) -> float | None:
    """
    Return the YES probability (0-100) for a binary market.
    Returns None if the market is not binary or price data is missing.
    """
    # outcomePrices is a JSON-encoded list of prices e.g. '["0.95", "0.05"]'
    outcome_prices = market.get("outcomePrices")
    if not outcome_prices:
        return None

    if isinstance(outcome_prices, str):
        try:
            outcome_prices = json.loads(outcome_prices)
        except (ValueError, TypeError):
            return None

    if not isinstance(outcome_prices, list) or len(outcome_prices) < 1:
        return None

    try:
        yes_price = float(outcome_prices[0])
        return round(yes_price * 100, 2)
    except (ValueError, TypeError):
        return None


def extract_liquidity(market: dict) -> float:
    """Return liquidity in USD, defaulting to 0."""
    for key in ("liquidity", "liquidityNum", "volume"):
        val = market.get(key)
        if val is not None:
            try:
                return float(val)
            except (ValueError, TypeError):
                pass
    return 0.0


def build_market_url(market: dict) -> str:
    slug = market.get("slug") or market.get("conditionId", "")
    return f"https://polymarket.com/event/{slug}" if slug else "https://polymarket.com"


def _parse_end_date(market: dict) -> datetime | None:
    raw = market.get("endDate") or market.get("endDateIso") or market.get("end_date_iso")
    if not raw:
        return None
    # Strip trailing Z, handle +00:00
    raw = str(raw).replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(raw).astimezone(timezone.utc)
    except (ValueError, TypeError):
        return None


def days_until_expiry(market: dict) -> float | None:
    end = _parse_end_date(market)
    if end is None:
        return None
    delta = end - datetime.now(timezone.utc)
    return delta.total_seconds() / 86400


def filter_signal_markets(markets: list[dict], low: float, high: float) -> list[dict]:
    """
    Return short-term markets (≤ MAX_DAYS_TO_EXPIRY) whose YES probability is
    extreme — either a long shot (≤ low%) or near-certain (≥ high%).
    """
    now = datetime.now(timezone.utc)
    cutoff = now + timedelta(days=MAX_DAYS_TO_EXPIRY)
    signals = []

    for m in markets:
        prob = extract_probability(m)
        if prob is None:
            continue

        liquidity = extract_liquidity(m)
        if liquidity < MIN_LIQUIDITY:
            continue

        end_dt = _parse_end_date(m)
        # Skip markets with no end date or expiring too far out
        if end_dt is None or end_dt > cutoff:
            continue
        # Skip already-expired markets
        if end_dt <= now:
            continue

        if prob <= low or prob >= high:
            days_left = (end_dt - now).total_seconds() / 86400
            is_long_shot = prob <= low

            signals.append({
                "question": m.get("question", "Unknown"),
                "probability": prob,
                "liquidity": liquidity,
                "url": build_market_url(m),
                "category": m.get("category", m.get("tags", [""])[0] if m.get("tags") else ""),
                "end_date": end_dt.strftime("%Y-%m-%d"),
                "days_left": round(days_left, 1),
                # Trade direction: long shot → BUY YES (expect up), near certain → BUY NO (expect down)
                "direction": "BUY YES ↑" if is_long_shot else "BUY NO ↓",
                "signal_type": "LONG SHOT 🎰" if is_long_shot else "NEAR CERTAIN ✅",
            })

    # Sort by days remaining (soonest first), then by probability
    signals.sort(key=lambda x: (x["days_left"], x["probability"]))
    return signals
