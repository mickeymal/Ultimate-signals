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


def _get(url: str, params: dict) -> list | dict | None:
    for attempt in range(3):
        try:
            resp = SESSION.get(url, params=params, timeout=30)
            if resp.status_code in (422, 404):
                return None
            if not resp.ok:
                logger.debug("API %s → HTTP %d", url.split("?")[0], resp.status_code)
                return None
            return resp.json()
        except requests.RequestException as e:
            wait = 2 ** attempt
            logger.warning("Request failed (attempt %d): %s — retrying in %ds", attempt + 1, e, wait)
            time.sleep(wait)
    return None


def _is_btc_updown(question: str) -> bool:
    q = question.lower()
    has_btc = "btc" in q or "bitcoin" in q
    has_updown = "up or down" in q or "up/down" in q or "higher or lower" in q
    has_timeframe = any(kw in q for kw in ("15m", "5m", "10m", "15 min", "5 min", "10 min", "15-min", "5-min"))
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


def _scan_markets_batch(batch: list[dict], seen_ids: set, now: datetime, slug_override: str = "") -> list[dict]:
    found = []
    for m in batch:
        question = m.get("question", "")
        if not _is_btc_updown(question):
            continue
        end_dt = _parse_end_date(m)
        if end_dt is None or end_dt <= now:
            continue
        mid = str(m.get("id") or m.get("conditionId") or question)
        if mid in seen_ids:
            continue
        seen_ids.add(mid)
        m["_slug"] = slug_override or m.get("slug") or m.get("eventSlug") or m.get("groupSlug") or ""
        found.append(m)
    return found


def fetch_btc_updown_markets() -> list[dict]:
    """
    Search for active BTC Up/Down short-term markets.
    Tries multiple API approaches with detailed logging for debugging.
    """
    now = datetime.now(timezone.utc)
    seen_ids: set[str] = set()

    # ── Attempt 1: /markets sorted by endDate ascending ─────────────────────
    # BTC 15m markets expire soonest, so they appear first in this sort
    data = _get(f"{GAMMA_API_BASE}/markets", {
        "active": "true", "closed": "false",
        "order": "endDate", "ascending": "true", "limit": 50,
    })
    if data:
        batch = data if isinstance(data, list) else data.get("data", [])
        logger.info("[A1] /markets?order=endDate&asc → %d markets | first: %s",
                    len(batch), batch[0].get("question", "?")[:60] if batch else "none")
        found = _scan_markets_batch(batch, seen_ids, now)
        if found:
            logger.info("Found %d BTC up/down markets via attempt 1", len(found))
            return found
    else:
        logger.info("[A1] /markets?order=endDate&asc → no response")

    # ── Attempt 2: /events sorted by endDate ascending ───────────────────────
    data = _get(f"{GAMMA_API_BASE}/events", {
        "active": "true", "closed": "false",
        "order": "endDate", "ascending": "true", "limit": 30,
    })
    if data:
        batch = data if isinstance(data, list) else data.get("data", [])
        logger.info("[A2] /events?order=endDate&asc → %d events | first: %s",
                    len(batch), batch[0].get("title", batch[0].get("slug", "?"))[:60] if batch else "none")
        found = []
        for event in batch:
            slug = event.get("slug", "")
            markets = event.get("markets", [event])
            found += _scan_markets_batch(markets, seen_ids, now, slug)
        if found:
            logger.info("Found %d BTC up/down markets via attempt 2", len(found))
            return found
    else:
        logger.info("[A2] /events?order=endDate&asc → no response")

    # ── Attempt 3: /events first page, no sort ───────────────────────────────
    data = _get(f"{GAMMA_API_BASE}/events", {
        "active": "true", "closed": "false", "limit": 100,
    })
    if data:
        batch = data if isinstance(data, list) else data.get("data", [])
        logger.info("[A3] /events (no sort) → %d events | first: %s",
                    len(batch), batch[0].get("title", batch[0].get("slug", "?"))[:60] if batch else "none")
        found = []
        for event in batch:
            slug = event.get("slug", "")
            markets = event.get("markets", [event])
            found += _scan_markets_batch(markets, seen_ids, now, slug)
        if found:
            logger.info("Found %d BTC up/down markets via attempt 3", len(found))
            return found
    else:
        logger.info("[A3] /events (no sort) → no response")

    # ── Attempt 4: /markets first 3 pages ────────────────────────────────────
    for offset in (0, 100, 200):
        data = _get(f"{GAMMA_API_BASE}/markets", {
            "active": "true", "closed": "false", "limit": 100, "offset": offset,
        })
        if not data:
            logger.info("[A4] /markets offset=%d → no response", offset)
            break
        batch = data if isinstance(data, list) else data.get("data", [])
        logger.info("[A4] /markets offset=%d → %d markets | first: %s",
                    offset, len(batch),
                    batch[0].get("question", "?")[:60] if batch else "none")
        found = _scan_markets_batch(batch, seen_ids, now)
        if found:
            logger.info("Found %d BTC up/down markets via attempt 4 (offset=%d)", len(found), offset)
            return found
        if len(batch) < 100:
            break
        time.sleep(0.3)

    logger.warning("fetch_btc_updown_markets → 0 markets found across all attempts")
    return []


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
