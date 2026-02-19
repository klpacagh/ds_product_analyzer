"""LLM-based product name extraction and filtering.

Uses Claude Haiku to extract clean, purchasable product names from raw signal
texts (Reddit titles, Amazon listings, TikTok captions) and filters out
non-product posts.  Disabled gracefully when no API key is configured.
"""

from __future__ import annotations

import json
import logging
from dataclasses import replace
from typing import NamedTuple

import anthropic

from ds_product_analyzer.collectors.base import RawSignalData
from ds_product_analyzer.config import settings

logger = logging.getLogger(__name__)

# Sources that already have clean product names and skip LLM extraction.
_PASSTHROUGH_SOURCES = {"google_trends"}


class ExtractionResult(NamedTuple):
    name: str | None
    relevant: bool
    confidence: float


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


async def extract_and_filter(signals: list[RawSignalData]) -> list[RawSignalData]:
    """Extract clean product names via LLM and drop non-product signals.

    * Google Trends signals pass through unchanged (already clean keywords).
    * If no ``ANTHROPIC_API_KEY`` is configured the function is a no-op.
    * On any LLM/parse error the affected batch passes through unchanged.
    """
    if not settings.anthropic_api_key:
        return signals

    passthrough: list[RawSignalData] = []
    to_extract: list[RawSignalData] = []

    for sig in signals:
        if sig.source in _PASSTHROUGH_SOURCES:
            passthrough.append(sig)
        else:
            to_extract.append(sig)

    if not to_extract:
        return passthrough

    client = anthropic.Anthropic(api_key=settings.anthropic_api_key)
    batch_size = settings.llm_extraction_batch_size

    processed: list[RawSignalData] = []
    filtered_count = 0

    for i in range(0, len(to_extract), batch_size):
        batch = to_extract[i : i + batch_size]
        results = _extract_batch(client, batch)

        for sig, res in zip(batch, results):
            if not res.relevant or not res.name:
                filtered_count += 1
                logger.debug("Filtered out non-product signal: '%s'", sig.product_name)
                continue
            processed.append(replace(sig, product_name=res.name))

    logger.info(
        "LLM extraction: %d processed, %d filtered out",
        len(processed),
        filtered_count,
    )
    return passthrough + processed


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------

_SYSTEM_PROMPT = (
    "You extract purchasable product names from text. For each numbered item:\n"
    "- Extract the specific, purchasable product name (2-6 words).\n"
    "- Strip marketing fluff, dimensions, and color variants from Amazon titles.\n"
    "- For Reddit/TikTok posts, identify the specific product mentioned if any.\n"
    "- Mark items as not relevant if they don't reference a specific purchasable product.\n"
    "\n"
    "Respond with ONLY a JSON array (no markdown fencing). Each element:\n"
    '{"name": "Product Name" or null, "relevant": true/false, "confidence": 0.0-1.0}'
)


def _build_prompt(items: list[tuple[str, str]]) -> str:
    """Build the user prompt from (source, text) pairs."""
    lines = []
    for idx, (source, text) in enumerate(items, 1):
        lines.append(f"{idx}. [{source}] {text}")
    return "\n".join(lines)


def _extract_batch(
    client: anthropic.Anthropic,
    batch: list[RawSignalData],
) -> list[ExtractionResult]:
    """Call Haiku for one batch. Falls back to pass-through on any error."""
    items = [(sig.source, sig.product_name) for sig in batch]
    user_prompt = _build_prompt(items)

    try:
        message = client.messages.create(
            model=settings.llm_extraction_model,
            max_tokens=1024,
            system=_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": user_prompt}],
        )
        raw_text = message.content[0].text
        return _parse_response(raw_text, len(batch))
    except Exception:
        logger.exception("LLM extraction batch failed, passing through unchanged")
        return [ExtractionResult(name=sig.product_name, relevant=True, confidence=0.0) for sig in batch]


def _parse_response(raw_text: str, count: int) -> list[ExtractionResult]:
    """Parse Haiku's JSON response. Falls back to pass-through on failure."""
    text = raw_text.strip()

    # Strip markdown code fencing if present
    if text.startswith("```"):
        lines = text.split("\n")
        # Remove first line (```json) and last line (```)
        lines = [l for l in lines if not l.strip().startswith("```")]
        text = "\n".join(lines)

    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        logger.warning("Failed to parse LLM response as JSON, passing through")
        return [ExtractionResult(name=None, relevant=True, confidence=0.0)] * count

    if not isinstance(data, list):
        logger.warning("LLM response is not a JSON array, passing through")
        return [ExtractionResult(name=None, relevant=True, confidence=0.0)] * count

    if len(data) != count:
        logger.warning(
            "LLM response length %d != expected %d, passing through",
            len(data),
            count,
        )
        return [ExtractionResult(name=None, relevant=True, confidence=0.0)] * count

    results = []
    for item in data:
        if not isinstance(item, dict):
            results.append(ExtractionResult(name=None, relevant=True, confidence=0.0))
            continue
        name = item.get("name") or None
        relevant = item.get("relevant", True)
        confidence = float(item.get("confidence", 0.0))
        # Empty string name treated as not relevant
        if name is not None and not name.strip():
            name = None
        results.append(ExtractionResult(name=name, relevant=relevant, confidence=confidence))

    return results
