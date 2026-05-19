"""
AI-powered market analyser.
For each short-term Polymarket market, searches the web for current context
then asks Claude to assess whether it will resolve YES or NO and with what
confidence. Only markets where confidence >= CONFIDENCE_THRESHOLD are returned.
"""

import logging
import time
import anthropic
from duckduckgo_search import DDGS

from config import ANTHROPIC_API_KEY, CONFIDENCE_THRESHOLD, MAX_ANALYSIS_PER_SCAN

logger = logging.getLogger(__name__)

_client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

SYSTEM_PROMPT = """\
You are a sharp prediction market analyst. You will be given a market question,
its resolution deadline, and recent web search results about the topic.

Your job: decide whether this market resolves YES or NO and state your confidence.

Rules:
- Base your assessment on the search results and your knowledge, NOT on the market's current price.
- Be direct. Only output the three fields below, nothing else.
- If you genuinely cannot determine the outcome with confidence, set CONFIDENCE below 85.

Output format (exactly):
DIRECTION: YES or NO
CONFIDENCE: <integer 0-100>
REASON: <one sentence, max 20 words>
"""


def _web_search(query: str) -> str:
    """Return top 5 DuckDuckGo results as a plain-text block."""
    try:
        with DDGS() as ddgs:
            results = list(ddgs.text(query, max_results=5))
        if not results:
            return "No search results found."
        return "\n".join(
            f"- {r.get('title', '')}: {r.get('body', '')}" for r in results
        )
    except Exception as exc:
        logger.warning("Web search failed for '%s': %s", query[:60], exc)
        return "Search unavailable."


def _call_claude(market: dict, search_context: str) -> tuple[str | None, float, str]:
    """
    Returns (direction, confidence, reason).
    direction is 'YES', 'NO', or None on parse failure.
    """
    prompt = (
        f"Market: {market['question']}\n"
        f"Resolves by: {market['end_date']} ({market['days_left']}d remaining)\n\n"
        f"Recent web results:\n{search_context}\n\n"
        f"Assess this market."
    )
    try:
        response = _client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=120,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": prompt}],
        )
        text = response.content[0].text.strip()
    except anthropic.RateLimitError:
        logger.warning("Anthropic rate limit hit — sleeping 30s")
        time.sleep(30)
        return None, 0.0, ""
    except Exception as exc:
        logger.warning("Claude call failed: %s", exc)
        return None, 0.0, ""

    direction = None
    confidence = 0.0
    reason = ""
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
    """
    Run AI analysis on up to MAX_ANALYSIS_PER_SCAN markets.
    Returns only those where AI confidence >= CONFIDENCE_THRESHOLD.
    """
    candidates = markets[:MAX_ANALYSIS_PER_SCAN]
    total = len(candidates)
    signals = []

    logger.info("Analysing %d short-term markets with AI...", total)

    for i, market in enumerate(candidates, 1):
        question = market["question"]
        logger.info("[%d/%d] Analysing: %s", i, total, question[:70])

        search_context = _web_search(question)
        direction, confidence, reason = _call_claude(market, search_context)

        if direction is None or confidence < CONFIDENCE_THRESHOLD:
            logger.debug("No signal (direction=%s, confidence=%.0f%%)", direction, confidence)
            time.sleep(0.5)
            continue

        signals.append({
            **market,
            "direction": direction,
            "confidence": confidence,
            "reason": reason,
            "trade": f"BUY YES ↑" if direction == "YES" else "BUY NO ↓",
        })

        logger.info(
            "SIGNAL: %s | %s %.0f%% | %s",
            question[:50], direction, confidence, reason,
        )
        time.sleep(0.5)

    logger.info("Analysis complete: %d signals from %d markets", len(signals), total)
    return signals
