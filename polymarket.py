import json
import time
import logging
import requests
from datetime import datetime, timezone
from config import GAMMA_API_BASE, MIN_LIQUIDITY

logger = logging.getLogger(__name__)

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept": "application/json, text/plain, */*",
    "Origin": "https://polymarket.com",
    "Referer": "https://polymarket.com/",
}
SESSION = requests.Session()
SESSION.headers.update(HEADERS)

# Queries tried in order — stop at first that returns BTC up/down markets
_SEARCH_QUERIES = [
    "BTC Up or Down",
    "up or down",
    "BTC",
]


def _get(url: str, params: dict) -> list | dict | None:
    for attempt in range(4):
        try:
            resp = SESSION.get(url, params=params, timeout=30)
            if resp.status_code in (422, 404):
                return None
            resp.raise_for_status()
            return resp.json()
        except requests.RequestException as e:
            wait = 2 ** attempt
            logger.warning("Request failed (attempt %d): %s — retrying in %ds", attempt + 1, e, wait)
            time.sleep(wait)
    return None


def _is_btc_updown(question: str) -> bool:
    q = question.lower()
    has_btc = "btc" in q or "bitcoin" in q
    has_updown = "up or down" in q or "up/down" in q
    has_timeframe = any(kw in q for kw in ("15m", "5m", "10m", "15 min", "5 min", "10 min"))
    return has_btc and (has_updown or has_timeframe)


def _parse_outcomes(market: dict) -> list[tuple[str, float]]:
    names_raw = market.get("outcomes", '["Up","Down"]')
    prices_raw = market.get("outcomePrices", '["0.5","0.5"]')
    if isinstance(names_raw, str):
        try:
            names_raw = json.loads(names_raw)
        except Exception:
            names_raw = ["Up", "Down"]
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


def _parse_end_date(market: dict) -> datetime | None:
    raw = market.get("endDate") or market.get("endDateIso")
    if not raw:
        return None
    try:
        return datetime.fromisoformat(str(raw).replace("Z", "+00:00")).astimezone(timezone.utc)
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


def _expires_str(seconds: float) -> str:
    if seconds < 60:
        return f"{int(seconds)}s"
    if seconds < 3600:
        return f"{int(seconds // 60)}m"
    h, m = int(seconds // 3600), int((seconds % 3600) // 60)
    return f"{h}h {m}m"


def fetch_btc_updown_markets() -> list[dict]:
    """Search Polymarket for active BTC Up/Down short-term markets only."""
    now = datetime.now(timezone.utc)
    found: list[dict] = []
    seen_ids: set[str] = set()

    for query in _SEARCH_QUERIES:
        params = {
            "active": "true",
            "closed": "false",
            "q": query,
            "limit": 50,
        }
        data = _get(f"{GAMMA_API_BASE}/markets", params)
        if not data:
            logger.debug("No data for query %r", query)
            continue

        batch = data if isinstance(data, list) else data.get("data", [])
        logger.debug("Query %r → %d raw markets", query, len(batch))

        for m in batch:
            question = m.get("question", "")
            if not _is_btc_updown(question):
                continue

            end_dt = _parse_end_date(m)
            if end_dt is None or end_dt <= now:
                continue

            market_id = str(m.get("id") or m.get("conditionId") or question)
            if market_id in seen_ids:
                continue
            seen_ids.add(market_id)

            # Build event URL from slug or groupItemTitle
            slug = m.get("slug") or m.get("eventSlug") or ""
            if not slug:
                # Try to derive from market groupSlug or market slug
                slug = m.get("groupSlug") or ""
            m["_slug"] = slug
            found.append(m)

        if found:
            logger.info("Found %d BTC up/down markets via query %r", len(found), query)
            break

        time.sleep(0.3)

    if not found:
        # Last-ditch: try /events endpoint with keyword
        params = {"active": "true", "closed": "false", "q": "BTC up or down", "limit": 20}
        data = _get(f"{GAMMA_API_BASE}/events", params)
        if data:
            batch = data if isinstance(data, list) else data.get("data", [])
            for event in batch:
                slug = event.get("slug", "")
                for m in event.get("markets", []):
                    question = m.get("question", "")
                    if not _is_btc_updown(question):
                        continue
                    end_dt = _parse_end_date(m)
                    if end_dt is None or end_dt <= now:
                        continue
                    market_id = str(m.get("id") or m.get("conditionId") or question)
                    if market_id in seen_ids:
                        continue
                    seen_ids.add(market_id)
                    m["_slug"] = slug
                    found.append(m)

    logger.info("fetch_btc_updown_markets → %d active markets", len(found))
    return found


def get_signal_markets(markets: list[dict]) -> list[dict]:
    """Convert raw markets into signal dicts, sorted soonest-expiring first."""
    now = datetime.now(timezone.utc)
    result = []

    for m in markets:
        end_dt = _parse_end_date(m)
        if end_dt is None or end_dt <= now:
            continue

        if _extract_liquidity(m) < MIN_LIQUIDITY:
            continue

        slug = m.get("_slug") or m.get("slug") or m.get("eventSlug") or ""
        url = f"https://polymarket.com/event/{slug}" if slug else "https://polymarket.com"

        outcomes = _parse_outcomes(m)
        if not outcomes:
            continue

        seconds_left = (end_dt - now).total_seconds()
        cheapest = min(outcomes, key=lambda x: x[1])
        most_likely = max(outcomes, key=lambda x: x[1])

        result.append({
            "question": m.get("question", "BTC Up or Down"),
            "outcomes": outcomes,
            "cheapest_name": cheapest[0],
            "cheapest_price": cheapest[1],
            "most_likely_name": most_likely[0],
            "most_likely_price": most_likely[1],
            "liquidity": _extract_liquidity(m),
            "url": url,
            "expires_str": _expires_str(seconds_left),
            "seconds_left": seconds_left,
        })

    result.sort(key=lambda x: x["seconds_left"])
    return result
