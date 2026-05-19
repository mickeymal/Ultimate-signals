"""
AI-powered market analyser using Groq (free tier, no web search).

Anti-hallucination design:
- Model must cite a SPECIFIC fact, figure, or named event in its reason.
- Vague/generic reasons are detected and rejected before any signal fires.
- Markets with no-data boilerplate in the reason are silently dropped.
- Sports/un-googleable markets are pre-filtered by question pattern.
"""

import logging
import time
from groq import Groq, RateLimitError

from config import GROQ_API_KEY, CONFIDENCE_THRESHOLD, MAX_ANALYSIS_PER_SCAN, EXCLUDED_CATEGORIES

logger = logging.getLogger(__name__)

_client = Groq(api_key=GROQ_API_KEY)

# More capable model — still free on Groq, better at following strict instructions
MODEL = "llama-3.3-70b-versatile"

SYSTEM_PROMPT = """\
You are a prediction market analyst. Your job is to assess whether a market will resolve YES or NO.

STRICT ANTI-HALLUCINATION RULES — follow every one or your output is invalid:
1. Only signal if you have SPECIFIC, CONCRETE knowledge supporting your answer.
2. Your REASON must name a specific fact, figure, law, person, price, date, or named event.
3. NEVER use vague language: "typically", "usually", "generally", "often", "tend to", "likely", "probably", "no information", "insufficient data", "cannot determine", "uncertain".
4. If you have no specific knowledge about this exact topic, you MUST output CONFIDENCE: 0.
5. Do NOT guess. A confident wrong signal is far worse than no signal.
6. If the market is about an event you have no data on (future news, recent match results, current prices you don't know), output CONFIDENCE: 0.

EXAMPLE of a BAD reason (will be rejected):
  "No recent information available; teams typically don't win away games."
  "Elon Musk is known to post frequently on social media."
  "This is unlikely based on historical patterns."

EXAMPLE of a GOOD reason (specific named fact):
  "The Fed held rates at 5.25-5.5% across 8 consecutive FOMC meetings through Dec 2023."
  "US Congress passed the debt ceiling bill 97-1 in the Senate in June 2023."

OUTPUT FORMAT — output exactly these three lines, nothing else:
DIRECTION: YES or NO
CONFIDENCE: <integer 0-100>
REASON: <one sentence with a specific named fact, max 25 words>"""

# Phrases in a reason that indicate the model is guessing with no data.
# Any signal whose reason contains one of these is immediately rejected.
_VAGUE_PHRASES = (
    "no recent information",
    "no information available",
    "no data",
    "no specific",
    "no knowledge",
    "not enough information",
    "insufficient",
    "cannot determine",
    "unable to determine",
    "uncertain",
    "don't have",
    "do not have",
    "i don't",
    "i do not",
    "typically",
    "usually",
    "generally",
    "often ",
    "tend to",
    "historical pattern",
    "based on pattern",
    "past performance",
    "no results",
    "search unavailable",
    "not available",
    "unknown outcome",
    "hard to say",
    "difficult to predict",
    "not enough data",
    "lack of information",
    "no evidence",
)

# Sports / un-googleable question patterns
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


def _is_vague(reason: str) -> bool:
    low = reason.lower()
    return any(phrase in low for phrase in _VAGUE_PHRASES)


def _call_llm(market: dict) -> tuple[str | None, float, str]:
    prompt = (
        f"Market question: {market['question']}\n"
        f"Resolves by: {market['end_date']} ({market['days_left']}d remaining)\n\n"
        f"Do you have specific, concrete knowledge to assess this? If not, output CONFIDENCE: 0."
    )
    try:
        resp = _client.chat.completions.create(
            model=MODEL,
            max_tokens=150,
            temperature=0.0,
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
        if line.startswith("DIRECTION:"):
            val = line.split(":", 1)[1].strip().upper()
            if val in ("YES", "NO"):
                direction = val
        elif line.startswith("CONFIDENCE:"):
            try:
                confidence = float(line.split(":", 1)[1].strip().replace("%", ""))
            except ValueError:
                pass
        elif line.startswith("REASON:"):
            reason = line.split(":", 1)[1].strip()

    return direction, confidence, reason


def analyse_markets(markets: list[dict]) -> list[dict]:
    eligible = [m for m in markets if not _should_skip(m)]
    skipped_sports = len(markets) - len(eligible)
    candidates = eligible[:MAX_ANALYSIS_PER_SCAN]
    total = len(candidates)

    logger.info(
        "Analysing %d markets (skipped %d sports/excluded)...",
        total, skipped_sports,
    )

    signals = []
    rejected_vague = 0

    for i, market in enumerate(candidates, 1):
        question = market["question"]
        logger.info("[%d/%d] %s", i, total, question[:70])

        direction, confidence, reason = _call_llm(market)

        if direction is None or confidence < CONFIDENCE_THRESHOLD:
            time.sleep(0.2)
            continue

        if _is_vague(reason):
            logger.info("Rejected vague signal: %s (%.0f%% %s)", reason[:60], confidence, direction)
            rejected_vague += 1
            time.sleep(0.2)
            continue

        signals.append({
            **market,
            "direction": direction,
            "confidence": confidence,
            "reason": reason,
            "trade": "BUY YES ↑" if direction == "YES" else "BUY NO ↓",
        })
        logger.info("SIGNAL: %s | %s %.0f%% | %s", question[:50], direction, confidence, reason)
        time.sleep(0.2)

    logger.info(
        "Done: %d real signals | %d vague rejected | %d analysed",
        len(signals), rejected_vague, total,
    )
    return signals
