import os
from dotenv import load_dotenv

load_dotenv()

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")

LOW_THRESHOLD = float(os.getenv("LOW_THRESHOLD", "5"))
HIGH_THRESHOLD = float(os.getenv("HIGH_THRESHOLD", "95"))

SCAN_INTERVAL_MINUTES = int(os.getenv("SCAN_INTERVAL_MINUTES", "60"))

MIN_LIQUIDITY = float(os.getenv("MIN_LIQUIDITY", "100"))

GAMMA_API_BASE = "https://gamma-api.polymarket.com"
POLYMARKET_BASE_URL = "https://polymarket.com/event"

PAGE_SIZE = 100
