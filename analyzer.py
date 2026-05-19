"""
AI-powered market analyser using Groq (free tier, no web search).

Approach: give the AI the market question + current Polymarket price and ask
it to decide whether that price seems fair or mispriced. Direction + reason
come from the AI; the Polymarket price is context, not the signal trigger.

Sports/match markets are pre-filtered since the AI has no edge there.
"""

import logging
import time
from groq import Groq, RateLimitError

from config import GROQ_API_KEY, CONFIDENCE_THRESHOLD, MAX_ANALYSIS_PER_SCAN, EXCLUDED_CATEGORIES

logger = logging.getLogger(__name__)

_client = Groq(api_key=GROQ_API_KEY)

MODEL = "llama-3.1-70b-versatile"

SYSTEM_PROMPT = """\
You are a prediction market trader. For each market you must output a direction (YES or NO) and your confidence (0-100).

Rules:
- You are given the market question, current market price, and days until resolution.
- Pick YES if you think the market resolves YES. Pick NO if you think it resolves NO.
- Set confidence based on how sure you are. 50 = coin flip, 100 = certain.
- Always output a direction. Never refuse. Even a slight lean counts.
- Keep the reason to one short sentence.

Output exactly:
DIRECTION: YES or NO
CONFIDENCE: <0-100>
REASON: <one sentence, max 15 words>"""

# Sports/match-outcome patterns the AI has no edge on
_SPORTS_PATTERNS = (
    " vs. ", " vs ",
    "end in a draw", "end in a tie",
    "win on 20", "win on 2025",
    "total goals", "score more than", "score less than",
    "first goal", "clean sheet", "yellow card", "red card",
    "half-time", "halftime",
    " fc ", " afc ", " fc win",
    "nba ", "nfl ", "mlb ", "nhl ",
    "ufc ", "boxing", "grand prix",
    "formula 1", " f1 ", "tennis",
    "super bowl", "world cup",
    "champions league", "premier league",
    "la liga", "bundesliga", "serie a", "ligue 1",
)


def _should_skip(market: dict) -> bool:
    cat = str(market.get("category", "")).lower()
    if any(excl in cat for excl in EXCLUDED_CATEGORIES):
        return True
    q = str(market.get("question", "")).lower()
    return any(pat in q for pat in _SPORTS_PATTERNS)


def _call_llm(market: dict) -> tuple[str | None, float, str]:
    prob = market.get("polymarket_prob")
    prob_str = f"{prob:.0f}% YES" if prob is not None else "unknown"

    prompt = (
        f"Market: {market['question']}\n"
        f"Current market price: {prob_str}\n"
        f"Resolves in: {market['days_left']} days ({market['end_date']})\n\n"
        f"Will this resolve YES or NO?"
    )

    try:
        resp = _client.chat.completions.create(
            model=MODEL,
            max_tokens=100,
            temperature=0.2,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": prompt},
            ],
        )
        text = resp.choices[0].message.content.strip()
    except RateLimitError:
        logger.warning("Groq rate limit — sleeping 60s")
        time.sleep(60)
        return None, 0.0, ""
    except Exception as exc:
        logger.warning("LLM call failed: %s", exc)
        return None, 0.0, ""

    direction, confidence, reason = None, 0.0, ""
    for line in text.splitlines():
        line = line.strip()
        if line.upper().startswith("DIRECTION:"):
            val = line.split(":", 1)[1].strip().upper()
            if val in ("YES", "NO"):
                direction = val
        elif line.upper().startswith("CONFIDENCE:"):
            try:
                confidence = float(line.split(":", 1)[1].strip().replace("%", ""))
            except ValueError:
                pass
        elif line.upper().startswith("REASON:"):
            reason = line.split(":", 1)[1].strip()

    return direction, confidence, reason


def analyse_markets(markets: list[dict]) -> list[dict]:
    eligible = [m for m in markets if not _should_skip(m)]
    skipped = len(markets) - len(eligible)
    candidates = eligible[:MAX_ANALYSIS_PER_SCAN]
    total = len(candidates)

    logger.info("Analysing %d markets (skipped %d sports)...", total, skipped)

    signals = []
    for i, market in enumerate(candidates, 1):
        question = market["question"]
        logger.info("[%d/%d] %s", i, total, question[:70])

        direction, confidence, reason = _call_llm(market)

        if direction is None or confidence < CONFIDENCE_THRESHOLD:
            time.sleep(0.2)
            continue

        signals.append({
            **market,
            "direction": direction,
            "confidence": confidence,
            "reason": reason,
            "trade": "BUY YES ↑" if direction == "YES" else "BUY NO ↓",
        })
        logger.info(
            "SIGNAL: %s | %s %.0f%% | %s",
            question[:50], direction, confidence, reason,
        )
        time.sleep(0.2)

    logger.info("Done: %d signals from %d markets analysed", len(signals), total)
    return signals
