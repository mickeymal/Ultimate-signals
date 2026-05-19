"""
Bitcoin on-chain watcher.
When the Polymarket leaderboard is unavailable, this module monitors the
Bitcoin mempool for patterns that tend to precede short-term price moves.
Only used to generate signals for the BTC Up/Down 15m market.

Free data source: mempool.space public API (no key needed).

Signals watched:
  - Fee spike  : sudden rise in sat/vB → network stress → likely DOWN
  - Whale flood: multiple large unconfirmed txns (>50 BTC each) → volatility
  - Mempool clearing: congestion drains rapidly after being heavy → UP
  - Low-fee quiet: sub-5 sat/vB after a high period → market calming → UP
"""

import logging
import time
import requests

logger = logging.getLogger(__name__)

MEMPOOL_API = "https://mempool.space/api"
HEADERS = {"User-Agent": "Mozilla/5.0"}

# Tunable thresholds
FEE_SPIKE_MULTIPLIER = 2.5   # fast fee doubles = spike
FEE_HIGH_SAT = 40            # sat/vB above this = network stress
FEE_CALM_SAT = 5             # sat/vB below this = quiet network
WHALE_BTC = 30               # single unconfirmed tx above this = whale
WHALE_TOTAL_BTC = 200        # aggregate whale BTC in mempool = big signal
MEMPOOL_LARGE = 80_000       # txn count above this = congested
MEMPOOL_DRAIN_RATIO = 0.4    # drops to 40% of previous = rapid clear
SIGNAL_COOLDOWN = 240        # seconds between signals (avoid spam)


def _get(path: str):
    try:
        resp = requests.get(f"{MEMPOOL_API}/{path}", headers=HEADERS, timeout=10)
        if resp.ok:
            return resp.json()
    except Exception as e:
        logger.debug("mempool.space %s failed: %s", path, e)
    return None


class BTCWatcher:
    """
    Call check() every ~30s from a background thread.
    Returns a signal dict when conditions suggest an imminent BTC price move,
    None otherwise.
    """

    def __init__(self):
        self._prev_fee: float | None = None
        self._prev_mempool_count: int | None = None
        self._last_signal_at: float = 0

    def _cooldown_ok(self) -> bool:
        return time.time() - self._last_signal_at >= SIGNAL_COOLDOWN

    def check(self) -> dict | None:
        fees = _get("v1/fees/recommended")
        mempool = _get("mempool")
        recent_txns = _get("mempool/recent") or []

        if not fees:
            return None

        fast_fee = float(fees.get("fastestFee", 0))
        current_count = int((mempool or {}).get("count", 0))

        signal = None

        # ── 1. Fee spike ─────────────────────────────────────────────────────
        if (
            self._prev_fee
            and fast_fee >= self._prev_fee * FEE_SPIKE_MULTIPLIER
            and fast_fee >= FEE_HIGH_SAT
            and self._cooldown_ok()
        ):
            signal = {
                "direction": "DOWN",
                "reason": (
                    f"Fee spike: {self._prev_fee:.0f} → {fast_fee:.0f} sat/vB "
                    f"({fast_fee / max(self._prev_fee, 1):.1f}x). "
                    "Network stress usually precedes sell pressure."
                ),
                "indicator": "FEE SPIKE",
            }

        # ── 2. Whale transactions in mempool ─────────────────────────────────
        if not signal and self._cooldown_ok():
            whales = []
            for tx in recent_txns:
                sats = tx.get("value", 0)
                btc = sats / 1e8
                if btc >= WHALE_BTC:
                    whales.append(round(btc, 1))

            whale_total = sum(whales)
            if whale_total >= WHALE_TOTAL_BTC:
                signal = {
                    "direction": "DOWN",
                    "reason": (
                        f"{len(whales)} whale txn(s) totalling {whale_total:.0f} BTC "
                        "just hit the mempool. Large moves often signal selling."
                    ),
                    "indicator": "WHALE MOVEMENT",
                }

        # ── 3. Mempool rapidly clearing ──────────────────────────────────────
        if (
            not signal
            and self._prev_mempool_count
            and self._prev_mempool_count >= MEMPOOL_LARGE
            and current_count > 0
            and current_count <= self._prev_mempool_count * MEMPOOL_DRAIN_RATIO
            and self._cooldown_ok()
        ):
            signal = {
                "direction": "UP",
                "reason": (
                    f"Mempool drained: {self._prev_mempool_count:,} → {current_count:,} txns. "
                    "Rapid clearing after congestion often signals sell exhaustion."
                ),
                "indicator": "MEMPOOL CLEAR",
            }

        # ── 4. Very low fees after a high period ─────────────────────────────
        if (
            not signal
            and self._prev_fee
            and self._prev_fee >= FEE_HIGH_SAT
            and fast_fee <= FEE_CALM_SAT
            and self._cooldown_ok()
        ):
            signal = {
                "direction": "UP",
                "reason": (
                    f"Fees dropped to {fast_fee:.0f} sat/vB (was {self._prev_fee:.0f}). "
                    "Calm network after stress often precedes a bounce."
                ),
                "indicator": "FEE CALM",
            }

        # ── update state ─────────────────────────────────────────────────────
        self._prev_fee = fast_fee
        if current_count:
            self._prev_mempool_count = current_count

        if signal:
            self._last_signal_at = time.time()
            signal.update({
                "fast_fee": fast_fee,
                "mempool_count": current_count,
            })
            logger.info(
                "BTC signal: %s → %s | fee=%s sat/vB | mempool=%s txns",
                signal["indicator"], signal["direction"], fast_fee, current_count,
            )

        return signal
