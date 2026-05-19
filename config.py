import os
from dotenv import load_dotenv

load_dotenv()

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")

ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")

# Only signal when AI confidence is at or above this percentage
CONFIDENCE_THRESHOLD = float(os.getenv("CONFIDENCE_THRESHOLD", "85"))

# Max markets to run AI analysis on per scan (controls API cost)
MAX_ANALYSIS_PER_SCAN = int(os.getenv("MAX_ANALYSIS_PER_SCAN", "300"))

SCAN_INTERVAL_MINUTES = int(os.getenv("SCAN_INTERVAL_MINUTES", "60"))

MIN_LIQUIDITY = float(os.getenv("MIN_LIQUIDITY", "500"))

# Only analyse markets resolving within this many days
MAX_DAYS_TO_EXPIRY = int(os.getenv("MAX_DAYS_TO_EXPIRY", "7"))

GAMMA_API_BASE = "https://gamma-api.polymarket.com"

PAGE_SIZE = 100
