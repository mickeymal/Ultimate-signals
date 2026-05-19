import json
import time
import logging
import requests
from datetime import datetime, timezone, timedelta
from config import GAMMA_API_BASE, MIN_LIQUIDITY, MAX_DAYS_TO_EXPIRY

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


def _build_url(market: dict) -> str:
    slug = market.get("_event_slug") or market.get("slug", "")
    return f"https://polymarket.com/event/{slug}" if slug else ""


def _expires_str(seconds: float) -> str:
    if seconds < 60:
        return f"{int(seconds)}s"
    if seconds < 3600:
        return f"{int(seconds // 60)}m"
    if seconds < 86400:
        h, m = int(seconds // 3600), int((seconds % 3600) // 60)
        return f"{h}h {m}m"
    return f"{round(seconds / 86400, 1)}d"


def fetch_short_term_markets() -> list[dict]:
    """
    Fetch ONLY the soonest-expiring active markets by sorting endDate ascending
    and stopping as soon as we've passed the MAX_DAYS_TO_EXPIRY cutoff.
    Typically returns in 1-2 API calls instead of scanning 10k+ markets.
    """
    now = datetime.now(timezone.utc)
    cutoff = now + timedelta(days=MAX_DAYS_TO_EXPIRY)
    markets = []
    offset = 0

    logger.info("Fetching short-term markets (≤%.1f days)...", MAX_DAYS_TO_EXPIRY)

    while True:
        params = {
            "active": "true",
            "closed": "false",
            "limit": 100,
            "offset": offset,
            "order": "endDate",       # sort soonest-ending first
            "ascending": "true",
        }
        data = _get(f"{GAMMA_API_BASE}/events", params)
        if not data:
            break

        batch = data if isinstance(data, list) else data.get("data", [])
        if not batch:
            break

        hit_cutoff = False
        for event in batch:
            slug = event.get("slug", "")
            url = f"https://polymarket.com/event/{slug}" if slug else ""
            for m in event.get("markets", [event]):
                end_dt = _parse_end_date(m)
                if end_dt is None:
                    continue
                if end_dt > cutoff:
                    hit_cutoff = True
                    break
                if end_dt > now:
                    m["_event_url"] = url
                    m["_event_slug"] = slug
                    markets.append(m)
            if hit_cutoff:
                break

        logger.info("Fetched %d short-term markets (offset=%d)", len(markets), offset)

        if hit_cutoff or len(batch) < 100:
            break

        offset += 100
        time.sleep(0.3)

    logger.info("Done — %d short-term markets found", len(markets))
    return markets


def get_signal_markets(markets: list[dict]) -> list[dict]:
    """
    Convert raw markets into signal dicts.
    Keeps all with sufficient liquidity; highlights the cheapest outcome.
    """
    now = datetime.now(timezone.utc)
    result = []

    for m in markets:
        end_dt = _parse_end_date(m)
        if end_dt is None or end_dt <= now:
            continue

        if _extract_liquidity(m) < MIN_LIQUIDITY:
            continue

        url = m.get("_event_url") or _build_url(m)
        if not url:
            continue

        outcomes = _parse_outcomes(m)
        if not outcomes:
            continue

        seconds_left = (end_dt - now).total_seconds()

        # Cheapest outcome = highest payout = the signal
        cheapest = min(outcomes, key=lambda x: x[1])
        most_likely = max(outcomes, key=lambda x: x[1])

        result.append({
            "question": m.get("question", "Unknown"),
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
