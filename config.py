import os
from dotenv import load_dotenv

load_dotenv()

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")

GROQ_API_KEY = os.getenv("GROQ_API_KEY", "")

# Only signal when AI confidence is at or above this percentage
CONFIDENCE_THRESHOLD = float(os.getenv("CONFIDENCE_THRESHOLD", "35"))

# Minutes to wait before rescanning when a scan finds zero signals
RESCAN_DELAY_MINUTES = int(os.getenv("RESCAN_DELAY_MINUTES", "10"))

# Max markets to run AI analysis on per scan (controls API cost)
MAX_ANALYSIS_PER_SCAN = int(os.getenv("MAX_ANALYSIS_PER_SCAN", "100"))

# Comma-separated Polymarket categories to skip (sports have no AI edge)
EXCLUDED_CATEGORIES = [
    c.strip().lower()
    for c in os.getenv("EXCLUDED_CATEGORIES", "sports").split(",")
    if c.strip()
]

SCAN_INTERVAL_MINUTES = int(os.getenv("SCAN_INTERVAL_MINUTES", "60"))

MIN_LIQUIDITY = float(os.getenv("MIN_LIQUIDITY", "500"))

# Only analyse markets resolving within this many days
MAX_DAYS_TO_EXPIRY = int(os.getenv("MAX_DAYS_TO_EXPIRY", "7"))

GAMMA_API_BASE = "https://gamma-api.polymarket.com"

PAGE_SIZE = 100
