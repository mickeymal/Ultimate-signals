"""
AI-powered market analyser using Groq (free tier) + DuckDuckGo web search.

Key rules:
- If web search fails or returns nothing, the market is SKIPPED — no guessing.
- Sports markets are excluded by default (no AI edge without live form/stats).
- Only signals when confidence >= CONFIDENCE_THRESHOLD based on real search data.
"""

import logging
import time
from groq import Groq, RateLimitError
from duckduckgo_search import DDGS

from config import GROQ_API_KEY, CONFIDENCE_THRESHOLD, MAX_ANALYSIS_PER_SCAN, EXCLUDED_CATEGORIES

logger = logging.getLogger(__name__)

_client = Groq(api_key=GROQ_API_KEY)

MODEL = "llama-3.1-8b-instant"

SYSTEM_PROMPT = """\
You are a prediction market analyst. You will be given a market question, its deadline, \
and real web search results about the topic.

STRICT RULES:
1. You MUST base your assessment only on the provided search results.
2. If the search results do not contain relevant information, output CONFIDENCE: 0. Do NOT guess.
3. Never invent a reason. If you have no evidence, say so and set CONFIDENCE: 0.
4. Only output the three lines below. Nothing else.

OUTPUT FORMAT (exactly):
DIRECTION: YES or NO
CONFIDENCE: <integer 0-100>
REASON: <one sentence citing the search evidence, max 20 words>"""

# Phrases that indicate the AI has no real data (used to reject hallucinated signals)
_NO_DATA_PHRASES = (
    "no recent information",
    "no information available",
    "search unavailable",
    "no results",
    "typical outcome",
    "typically",
    "not enough information",
    "insufficient information",
    "cannot determine",
    "unable to determine",
)

# Seconds to wait between DDG calls to stay under rate limit
_SEARCH_DELAY = 3.0


def _web_search(query: str) -> str:
    """
    Search DuckDuckGo. Returns non-empty string on success, empty string on failure.
    Empty string = caller must skip this market.
    """
    time.sleep(_SEARCH_DELAY)
    try:
        with DDGS() as ddgs:
            results = list(ddgs.text(query, max_results=5))
        if not results:
            logger.debug("DDG returned no results for: %s", query[:60])
            return ""
        return "\n".join(f"- {r.get('title','')}: {r.get('body','')}" for r in results)
    except Exception as exc:
        logger.warning("Search failed for '%s': %s", query[:60], exc)
        return ""


def _call_llm(market: dict, search_context: str) -> tuple[str | None, float, str]:
    prompt = (
        f"Market: {market['question']}\n"
        f"Resolves by: {market['end_date']} ({market['days_left']}d left)\n\n"
        f"Web search results:\n{search_context}\n\n"
        f"Assess this market based strictly on the search results above."
    )
    try:
        resp = _client.chat.completions.create(
            model=MODEL,
            max_tokens=120,
            temperature=0.1,
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


def _is_hallucinated(reason: str) -> bool:
    """Return True if the reason contains no-data boilerplate — reject it."""
    lower = reason.lower()
    return any(phrase in lower for phrase in _NO_DATA_PHRASES)


def _is_excluded_category(market: dict) -> bool:
    cat = str(market.get("category", "")).lower()
    return any(excl in cat for excl in EXCLUDED_CATEGORIES)


def analyse_markets(markets: list[dict]) -> list[dict]:
    # Filter out excluded categories before we start
    eligible = [m for m in markets if not _is_excluded_category(m)]
    excluded_count = len(markets) - len(eligible)
    if excluded_count:
        logger.info("Skipped %d markets in excluded categories (%s)", excluded_count, EXCLUDED_CATEGORIES)

    candidates = eligible[:MAX_ANALYSIS_PER_SCAN]
    total = len(candidates)
    signals = []
    skipped_no_data = 0

    logger.info("Analysing %d markets (excluded %d sports/other)...", total, excluded_count)

    for i, market in enumerate(candidates, 1):
        question = market["question"]
        logger.info("[%d/%d] %s", i, total, question[:70])

        context = _web_search(question)
        if not context:
            logger.debug("No search data — skipping (no guessing)")
            skipped_no_data += 1
            continue

        direction, confidence, reason = _call_llm(market, context)

        if direction is None or confidence < CONFIDENCE_THRESHOLD:
            continue

        if _is_hallucinated(reason):
            logger.warning("Rejected hallucinated signal: '%s' (%.0f%% %s)", reason, confidence, direction)
            continue

        signals.append({
            **market,
            "direction": direction,
            "confidence": confidence,
            "reason": reason,
            "trade": "BUY YES ↑" if direction == "YES" else "BUY NO ↓",
        })
        logger.info("SIGNAL: %s | %s %.0f%% | %s", question[:50], direction, confidence, reason)

    logger.info(
        "Done: %d signals | %d analysed | %d skipped (no search data)",
        len(signals), total - skipped_no_data, skipped_no_data,
    )
    return signals
