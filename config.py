import os
from dotenv import load_dotenv

load_dotenv()

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")

# Market scanning — every 5 min to catch fresh short-term markets fast
SCAN_INTERVAL_MINUTES = int(os.getenv("SCAN_INTERVAL_MINUTES", "5"))

# Only show markets expiring within this many days (1 = today only)
MAX_DAYS_TO_EXPIRY = int(os.getenv("MAX_DAYS_TO_EXPIRY", "1"))

# Minimum liquidity in USD
MIN_LIQUIDITY = float(os.getenv("MIN_LIQUIDITY", "100"))

# Manually tracked trader addresses (comma-separated proxy wallet addresses)
# Used as fallback if the leaderboard API is unavailable
TRADER_ADDRESSES = os.getenv("TRADER_ADDRESSES", "")

# Top trader tracking
TOP_TRADERS_COUNT = int(os.getenv("TOP_TRADERS_COUNT", "25"))
TRACKER_POLL_MINUTES = int(os.getenv("TRACKER_POLL_MINUTES", "3"))

# Polymarket API bases
GAMMA_API_BASE = "https://gamma-api.polymarket.com"
DATA_API_BASE = "https://data-api.polymarket.com"
CLOB_API_BASE = "https://clob.polymarket.com"

PAGE_SIZE = 100
