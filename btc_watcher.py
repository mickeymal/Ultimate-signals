"""
Bitcoin price signal watcher for the Polymarket BTC Up/Down 15m market.

Uses Binance public API (no key needed) — the same data professional traders use.
Signals only fire when MULTIPLE indicators agree, preventing false positives.

Indicators:
  1. Order Book Imbalance (OBI) — bid vs ask pressure within 1% of price
  2. Funding Rate — perpetual futures longs/shorts imbalance (reversal signal)
  3. Large Trade Detection — block trades >5 BTC in same direction
  4. Price Momentum — consecutive 1m candles + volume surge
"""

import logging
import time
import requests
from statistics import mean, stdev

logger = logging.getLogger(__name__)

BINANCE_SPOT   = "https://api.binance.com/api/v3"
BINANCE_FUTURE = "https://fapi.binance.com/fapi/v1"
SYMBOL = "BTCUSDT"
HEADERS = {"User-Agent": "Mozilla/5.0"}

# ── Thresholds (empirically reasonable for 15m BTC) ─────────────────────────
OBI_STRONG      = 0.30    # |OBI| above this = clear directional pressure
OBI_MODERATE    = 0.20    # |OBI| above this = mild pressure (needs another signal)
FUNDING_HIGH    = 0.0008  # > 0.08% per 8h → longs overbought → DOWN
FUNDING_LOW     = -0.0004 # < -0.04% per 8h → shorts overbought → UP
LARGE_TRADE_BTC = 5.0     # single trade ≥ 5 BTC is "large"
LARGE_TRADE_MIN = 3       # need ≥ 3 large trades in one direction
MOMENTUM_BARS   = 3       # consecutive 1m candles same direction
VOL_SURGE_X     = 2.0     # latest candle volume ≥ 2× recent average
SIGNAL_COOLDOWN = 240     # seconds between signals


def _get(base: str, path: str, params: dict | None = None):
    try:
        resp = requests.get(f"{base}/{path}", params=params, headers=HEADERS, timeout=8)
        if resp.ok:
            return resp.json()
        logger.debug("Binance %s %s → %d", base, path, resp.status_code)
    except Exception as e:
        logger.debug("Binance request failed: %s", e)
    return None


# ── Individual indicator functions ───────────────────────────────────────────

def _order_book_imbalance() -> float | None:
    """
    OBI = (total bid qty − total ask qty) / (total bid qty + total ask qty)
    Range −1 to +1.  > 0 = buy pressure,  < 0 = sell pressure.
    Only counts levels within 1% of mid price.
    """
    data = _get(BINANCE_SPOT, "depth", {"symbol": SYMBOL, "limit": 50})
    if not data:
        return None
    try:
        bids = [(float(p), float(q)) for p, q in data["bids"]]
        asks = [(float(p), float(q)) for p, q in data["asks"]]
        if not bids or not asks:
            return None
        mid = (bids[0][0] + asks[0][0]) / 2
        band = mid * 0.01
        bid_vol = sum(q for p, q in bids if p >= mid - band)
        ask_vol = sum(q for p, q in asks if p <= mid + band)
        total = bid_vol + ask_vol
        return (bid_vol - ask_vol) / total if total > 0 else None
    except Exception as e:
        logger.debug("OBI calc error: %s", e)
        return None


def _funding_rate() -> float | None:
    """Current perpetual funding rate. Positive = longs pay shorts."""
    data = _get(BINANCE_FUTURE, "premiumIndex", {"symbol": SYMBOL})
    if not data:
        return None
    try:
        return float(data.get("lastFundingRate", 0))
    except (ValueError, TypeError):
        return None


def _large_trade_direction() -> tuple[int, int] | None:
    """
    Returns (large_buys, large_sells) — count of trades ≥ LARGE_TRADE_BTC
    from the last 500 trades.
    """
    data = _get(BINANCE_SPOT, "trades", {"symbol": SYMBOL, "limit": 500})
    if not data:
        return None
    try:
        buys = sells = 0
        for t in data:
            qty = float(t["qty"])
            if qty < LARGE_TRADE_BTC:
                continue
            if t["isBuyerMaker"] is False:   # taker is buyer = aggressive buy
                buys += 1
            else:
                sells += 1
        return buys, sells
    except Exception as e:
        logger.debug("Large trade error: %s", e)
        return None


def _price_momentum() -> dict | None:
    """
    Returns {direction: 'UP'|'DOWN'|None, volume_surge: bool}
    based on last MOMENTUM_BARS 1-minute candles.
    """
    data = _get(BINANCE_SPOT, "klines", {
        "symbol": SYMBOL, "interval": "1m", "limit": MOMENTUM_BARS + 3,
    })
    if not data or len(data) < MOMENTUM_BARS + 1:
        return None
    try:
        # Each kline: [open_time, open, high, low, close, volume, ...]
        candles = [(float(k[1]), float(k[4]), float(k[5])) for k in data]
        # Use the MOMENTUM_BARS most recent CLOSED candles (exclude last which is open)
        recent = candles[-(MOMENTUM_BARS + 1):-1]
        directions = ["UP" if c > o else "DOWN" for o, c, v in recent]
        volumes = [v for o, c, v in recent]

        if len(set(directions)) == 1:
            direction = directions[0]
        else:
            direction = None

        avg_vol = mean(volumes[:-1]) if len(volumes) > 1 else volumes[0]
        surge = volumes[-1] >= avg_vol * VOL_SURGE_X if avg_vol > 0 else False
        return {"direction": direction, "volume_surge": surge, "volumes": volumes}
    except Exception as e:
        logger.debug("Momentum error: %s", e)
        return None


# ── Signal aggregator ────────────────────────────────────────────────────────

class BTCWatcher:
    """
    Call check() every ~30s.
    Returns a signal dict only when ≥2 independent indicators agree.
    Returns None when evidence is mixed or insufficient.
    """

    def __init__(self):
        self._last_signal_at: float = 0

    def _cooldown_ok(self) -> bool:
        return time.time() - self._last_signal_at >= SIGNAL_COOLDOWN

    def check(self) -> dict | None:
        if not self._cooldown_ok():
            return None

        # Gather all indicators
        obi       = _order_book_imbalance()
        funding   = _funding_rate()
        trades    = _large_trade_direction()
        momentum  = _price_momentum()

        votes_up   = []
        votes_down = []

        # ── Order book imbalance ─────────────────────────────────────────────
        if obi is not None:
            if obi >= OBI_STRONG:
                votes_up.append(f"Order book: {obi:+.2f} (strong buy pressure)")
            elif obi <= -OBI_STRONG:
                votes_down.append(f"Order book: {obi:+.2f} (strong sell pressure)")
            elif obi >= OBI_MODERATE:
                votes_up.append(f"Order book: {obi:+.2f} (mild buy pressure)")
            elif obi <= -OBI_MODERATE:
                votes_down.append(f"Order book: {obi:+.2f} (mild sell pressure)")

        # ── Funding rate ─────────────────────────────────────────────────────
        if funding is not None:
            if funding >= FUNDING_HIGH:
                votes_down.append(f"Funding: {funding*100:.3f}% (longs overbought → reversal risk)")
            elif funding <= FUNDING_LOW:
                votes_up.append(f"Funding: {funding*100:.3f}% (shorts overbought → squeeze risk)")

        # ── Large trades ─────────────────────────────────────────────────────
        if trades:
            big_buys, big_sells = trades
            if big_buys >= LARGE_TRADE_MIN and big_buys > big_sells * 1.5:
                votes_up.append(f"Large trades: {big_buys} big buys vs {big_sells} sells")
            elif big_sells >= LARGE_TRADE_MIN and big_sells > big_buys * 1.5:
                votes_down.append(f"Large trades: {big_sells} big sells vs {big_buys} buys")

        # ── Price momentum ────────────────────────────────────────────────────
        if momentum and momentum["direction"]:
            surge_tag = " + volume surge" if momentum["volume_surge"] else ""
            if momentum["direction"] == "UP":
                votes_up.append(f"{MOMENTUM_BARS} consecutive UP candles{surge_tag}")
            else:
                votes_down.append(f"{MOMENTUM_BARS} consecutive DOWN candles{surge_tag}")

        # ── Decide — require ≥2 votes in same direction ───────────────────────
        direction = None
        evidence  = []

        if len(votes_up) >= 2 and len(votes_up) > len(votes_down):
            direction = "UP"
            evidence  = votes_up
        elif len(votes_down) >= 2 and len(votes_down) > len(votes_up):
            direction = "DOWN"
            evidence  = votes_down

        if not direction:
            logger.debug(
                "No signal — up_votes=%d down_votes=%d obi=%s funding=%s",
                len(votes_up), len(votes_down),
                f"{obi:+.2f}" if obi is not None else "N/A",
                f"{funding*100:.3f}%" if funding is not None else "N/A",
            )
            return None

        self._last_signal_at = time.time()
        logger.info(
            "BTC signal: %s | %d evidence points | obi=%s funding=%s",
            direction, len(evidence),
            f"{obi:+.2f}" if obi is not None else "N/A",
            f"{funding*100:.4f}%" if funding is not None else "N/A",
        )

        return {
            "direction": direction,
            "indicator": f"{len(evidence)} SIGNALS AGREE",
            "reason": " · ".join(evidence),
            "obi": obi,
            "funding": funding,
            "large_buys": trades[0] if trades else 0,
            "large_sells": trades[1] if trades else 0,
        }
