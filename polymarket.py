import json
import time
import logging
import requests
from datetime import datetime, timezone, timedelta
from config import GAMMA_API_BASE, PAGE_SIZE, MIN_LIQUIDITY, MAX_DAYS_TO_EXPIRY

logger = logging.getLogger(__name__)

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept": "application/json, text/plain, */*",
    "Origin": "https://polymarket.com",
    "Referer": "https://polymarket.com/",
}

SESSION = requests.Session()
SESSION.headers.update(HEADERS)


def _get(url: str, params: dict) -> list | dict | None:
    for attempt in range(4):
        try:
            resp = SESSION.get(url, params=params, timeout=30)
            if resp.status_code == 422:
                return None
            resp.raise_for_status()
            return resp.json()
        except requests.RequestException as e:
            wait = 2 ** attempt
            logger.warning("Request failed (attempt %d): %s — retrying in %ds", attempt + 1, e, wait)
            time.sleep(wait)
    return None


def fetch_all_markets() -> list[dict]:
    markets = []
    offset = 0
    logger.info("Scanning Polymarket for fresh short-term markets...")

    while True:
        params = {
            "active": "true",
            "closed": "false",
            "limit": PAGE_SIZE,
            "offset": offset,
            "order": "createdAt",
            "ascending": "false",
        }
        data = _get(f"{GAMMA_API_BASE}/events", params)
        if not data:
            break

        batch = data if isinstance(data, list) else data.get("data", [])
        if not batch:
            break

        for event in batch:
            slug = event.get("slug", "")
            url = f"https://polymarket.com/event/{slug}" if slug else ""
            for m in event.get("markets", [event]):
                m["_event_url"] = url
                m["_event_slug"] = slug
                markets.append(m)

        if len(batch) < PAGE_SIZE:
            break
        offset += PAGE_SIZE
        time.sleep(0.3)

    logger.info("Fetched %d total markets", len(markets))
    return markets


def _parse_outcomes(market: dict) -> list[tuple[str, float]]:
    """Return [(outcome_name, price_cents), ...] for all outcomes."""
    names_raw = market.get("outcomes", '["Yes","No"]')
    prices_raw = market.get("outcomePrices", '["0.5","0.5"]')

    if isinstance(names_raw, str):
        try:
            names_raw = json.loads(names_raw)
        except Exception:
            names_raw = ["Yes", "No"]

    if isinstance(prices_raw, str):
        try:
            prices_raw = json.loads(prices_raw)
        except Exception:
            prices_raw = ["0.5", "0.5"]

    result = []
    for name, price in zip(names_raw, prices_raw):
        try:
            result.append((str(name), round(float(price) * 100, 1)))
        except (ValueError, TypeError):
            pass
    return result


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
    raw = market.get("endDate") or market.get("endDateIso")
    if not raw:
        return None
    try:
        return datetime.fromisoformat(str(raw).replace("Z", "+00:00")).astimezone(timezone.utc)
    except (ValueError, TypeError):
        return None


def get_signal_markets(markets: list[dict]) -> list[dict]:
    """
    Return all active markets expiring within MAX_DAYS_TO_EXPIRY with
    sufficient liquidity, parsed for immediate signalling.
    Sorted soonest-expiring first.
    """
    now = datetime.now(timezone.utc)
    cutoff = now + timedelta(days=MAX_DAYS_TO_EXPIRY)
    result = []

    for m in markets:
        end_dt = _parse_end_date(m)
        if end_dt is None or end_dt > cutoff or end_dt <= now:
            continue

        liquidity = _extract_liquidity(m)
        if liquidity < MIN_LIQUIDITY:
            continue

        url = m.get("_event_url", "")
        if not url:
            slug = m.get("slug", "").strip()
            url = f"https://polymarket.com/event/{slug}" if slug else ""
        if not url:
            continue

        outcomes = _parse_outcomes(m)
        if not outcomes:
            continue

        seconds_left = (end_dt - now).total_seconds()
        if seconds_left < 60:
            expires_str = f"{int(seconds_left)}s"
        elif seconds_left < 3600:
            expires_str = f"{int(seconds_left // 60)}m"
        elif seconds_left < 86400:
            h = int(seconds_left // 3600)
            mn = int((seconds_left % 3600) // 60)
            expires_str = f"{h}h {mn}m"
        else:
            expires_str = f"{round(seconds_left / 86400, 1)}d"

        # Signal direction: cheapest outcome = highest return potential
        cheapest = min(outcomes, key=lambda x: x[1])
        signal_direction = cheapest[0].upper()

        result.append({
            "question": m.get("question", "Unknown"),
            "outcomes": outcomes,
            "signal_direction": signal_direction,
            "signal_price": cheapest[1],
            "liquidity": liquidity,
            "url": url,
            "expires_str": expires_str,
            "seconds_left": seconds_left,
            "end_date": end_dt.strftime("%Y-%m-%d %H:%M UTC"),
        })

    result.sort(key=lambda x: x["seconds_left"])
    return result
