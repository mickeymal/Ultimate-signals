"""
AI-powered market analyser using Groq (free tier) + DuckDuckGo web search.
Analyses each short-term market and only signals when confidence >= CONFIDENCE_THRESHOLD.
"""

import logging
import time
from groq import Groq, RateLimitError
from duckduckgo_search import DDGS

from config import GROQ_API_KEY, CONFIDENCE_THRESHOLD, MAX_ANALYSIS_PER_SCAN

logger = logging.getLogger(__name__)

_client = Groq(api_key=GROQ_API_KEY)

# llama-3.1-8b-instant: fast, free, 14,400 req/day on Groq free tier
MODEL = "llama-3.1-8b-instant"

SYSTEM_PROMPT = """\
You are a prediction market analyst. Given a market question, its deadline, and recent news, \
decide if it resolves YES or NO and how confident you are.

RULES:
- Base your answer on the search results and your knowledge. Ignore the market's current price.
- Only output the three lines below. Nothing else. No explanation outside REASON.
- If truly uncertain, set CONFIDENCE below 85.

OUTPUT FORMAT (exact):
DIRECTION: YES or NO
CONFIDENCE: <integer 0-100>
REASON: <one sentence, max 20 words>"""


def _web_search(query: str) -> str:
    try:
        with DDGS() as ddgs:
            results = list(ddgs.text(query, max_results=5))
        if not results:
            return "No results found."
        return "\n".join(f"- {r.get('title','')}: {r.get('body','')}" for r in results)
    except Exception as exc:
        logger.warning("Search failed for '%s': %s", query[:60], exc)
        return "Search unavailable."


def _call_llm(market: dict, search_context: str) -> tuple[str | None, float, str]:
    prompt = (
        f"Market: {market['question']}\n"
        f"Resolves by: {market['end_date']} ({market['days_left']}d left)\n\n"
        f"Recent web results:\n{search_context}\n\n"
        f"Assess this market."
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
        logger.warning("Groq rate limit hit — sleeping 60s")
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
    candidates = markets[:MAX_ANALYSIS_PER_SCAN]
    total = len(candidates)
    signals = []

    logger.info("Analysing %d short-term markets with Groq/Llama...", total)

    for i, market in enumerate(candidates, 1):
        question = market["question"]
        logger.info("[%d/%d] %s", i, total, question[:70])

        context = _web_search(question)
        direction, confidence, reason = _call_llm(market, context)

        if direction is None or confidence < CONFIDENCE_THRESHOLD:
            time.sleep(0.3)
            continue

        signals.append({
            **market,
            "direction": direction,
            "confidence": confidence,
            "reason": reason,
            "trade": "BUY YES ↑" if direction == "YES" else "BUY NO ↓",
        })
        logger.info("SIGNAL: %s | %s %.0f%% | %s", question[:50], direction, confidence, reason)
        time.sleep(0.3)

    logger.info("Done: %d signals from %d markets", len(signals), total)
    return signals
