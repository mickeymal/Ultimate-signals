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

        batch = data if isinstance(data, list) else data.get("data", data.get("markets", []))
        if not batch:
            break

        markets.extend(batch)
        logger.info("Fetched %d markets (offset=%d)", len(markets), offset)

        if len(batch) < PAGE_SIZE:
            break

        offset += PAGE_SIZE
        time.sleep(0.3)

    logger.info("Total markets fetched: %d", len(markets))
    return markets


def _extract_probability(market: dict) -> float | None:
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
        return round(float(outcome_prices[0]) * 100, 2)
    except (ValueError, TypeError):
        return None


def _extract_liquidity(market: dict) -> float:
    for key in ("liquidity", "liquidityNum", "volume"):
        val = market.get(key)
        if val is not None:
            try:
                return float(val)
            except (ValueError, TypeError):
                pass
    return 0.0


def _parse_end_date(market: dict) -> datetime | None:
    raw = market.get("endDate") or market.get("endDateIso") or market.get("end_date_iso")
    if not raw:
        return None
    try:
        return datetime.fromisoformat(str(raw).replace("Z", "+00:00")).astimezone(timezone.utc)
    except (ValueError, TypeError):
        return None


def _build_url(market: dict) -> str:
    slug = market.get("slug") or market.get("conditionId", "")
    return f"https://polymarket.com/event/{slug}" if slug else "https://polymarket.com"


def get_short_term_markets(markets: list[dict]) -> list[dict]:
    """
    Filter to active markets expiring within MAX_DAYS_TO_EXPIRY with
    sufficient liquidity. Sorted by liquidity desc so the most active
    markets are analysed first.
    """
    now = datetime.now(timezone.utc)
    cutoff = now + timedelta(days=MAX_DAYS_TO_EXPIRY)
    result = []

    for m in markets:
        liquidity = _extract_liquidity(m)
        if liquidity < MIN_LIQUIDITY:
            continue

        end_dt = _parse_end_date(m)
        if end_dt is None or end_dt > cutoff or end_dt <= now:
            continue

        days_left = (end_dt - now).total_seconds() / 86400
        result.append({
            "question": m.get("question", "Unknown"),
            "polymarket_prob": _extract_probability(m),
            "liquidity": liquidity,
            "url": _build_url(m),
            "category": m.get("category", (m.get("tags") or [""])[0]),
            "end_date": end_dt.strftime("%Y-%m-%d"),
            "days_left": round(days_left, 1),
        })

    result.sort(key=lambda x: x["liquidity"], reverse=True)
    return result
