"""Quick test: hit the Polymarket API and print the top signals without sending Telegram messages."""
import json
import logging
import sys
import os

# Stub out dotenv/config so we don't need a .env file
os.environ.setdefault("TELEGRAM_BOT_TOKEN", "test")
os.environ.setdefault("TELEGRAM_CHAT_ID", "test")
os.environ.setdefault("LOW_THRESHOLD", "5")
os.environ.setdefault("HIGH_THRESHOLD", "95")
os.environ.setdefault("MIN_LIQUIDITY", "100")

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

from polymarket import fetch_all_markets, filter_signal_markets

def main():
    markets = fetch_all_markets()
    print(f"\nTotal markets fetched: {len(markets)}")

    signals = filter_signal_markets(markets, low=5.0, high=95.0)
    print(f"Signals found: {len(signals)}\n")

    print("=" * 60)
    print("LONG SHOTS (prob <= 5%):")
    print("=" * 60)
    long_shots = [s for s in signals if s["signal_type"].startswith("LONG")]
    for s in long_shots[:15]:
        print(f"  {s['probability']:5.1f}% | ${s['liquidity']:>8,.0f} | {s['question'][:70]}")

    print("\n" + "=" * 60)
    print("NEAR CERTAIN (prob >= 95%):")
    print("=" * 60)
    near_certain = [s for s in signals if s["signal_type"].startswith("NEAR")]
    for s in near_certain[:15]:
        print(f"  {s['probability']:5.1f}% | ${s['liquidity']:>8,.0f} | {s['question'][:70]}")

if __name__ == "__main__":
    main()
