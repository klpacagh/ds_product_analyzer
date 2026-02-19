"""Sentiment analysis for product signals.

Lazy-loads a DistilBERT sentiment model and scores product-related text
(Reddit comments, Amazon reviews, titles) on a 0-100 scale.
"""

import asyncio
import json
import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ds_product_analyzer.config import settings
from ds_product_analyzer.db.models import Product, RawSignal

logger = logging.getLogger(__name__)

# Module-level singleton for the sentiment pipeline
_pipeline = None
_pipeline_lock = asyncio.Lock()


def _get_pipeline():
    """Lazy-load the sentiment model as a module-level singleton."""
    global _pipeline
    if _pipeline is None:
        logger.info("Loading sentiment model: %s", settings.sentiment_model)
        from transformers import pipeline

        _pipeline = pipeline(
            "sentiment-analysis",
            model=settings.sentiment_model,
            truncation=True,
            max_length=512,
        )
        logger.info("Sentiment model loaded")
    return _pipeline


def _run_inference(texts: list[str]) -> float:
    """Run batch sentiment inference. Returns 0-100 score."""
    if not texts:
        return 50.0

    pipe = _get_pipeline()

    # Truncate texts to 512 chars each
    truncated = [t[:512] for t in texts]

    results = pipe(truncated, batch_size=32)

    # Convert to 0-100 scale: POSITIVE -> 50-100, NEGATIVE -> 0-50
    scores = []
    for r in results:
        label = r["label"].upper()
        confidence = r["score"]
        if label == "POSITIVE":
            scores.append(50 + confidence * 50)
        else:
            scores.append(50 - confidence * 50)

    return sum(scores) / len(scores)


async def compute_product_sentiment(session: AsyncSession, product: Product) -> float:
    """Analyze sentiment for a product based on recent signal text data.

    Extracts text from Reddit and Amazon signal metadata (titles, comments,
    review text) and runs batch inference.

    Returns:
        Float 0-100 (0=negative, 50=neutral, 100=positive).
        Returns 50.0 when no text data is available.
    """
    cutoff = datetime.now(timezone.utc) - timedelta(days=31)

    signals = (
        await session.execute(
            select(RawSignal)
            .where(RawSignal.product_id == product.id)
            .where(RawSignal.collected_at >= cutoff)
            .where(RawSignal.source.in_(["reddit", "amazon"]))
        )
    ).scalars().all()

    texts = []
    for sig in signals:
        if not sig.metadata_json:
            continue
        try:
            meta = json.loads(sig.metadata_json)
        except (json.JSONDecodeError, TypeError):
            continue

        # Extract text fields from metadata
        if title := meta.get("title"):
            texts.append(str(title))
        if comments := meta.get("top_comments"):
            if isinstance(comments, list):
                texts.extend(str(c) for c in comments[:5])
            elif isinstance(comments, str):
                texts.append(comments)
        if review_text := meta.get("review_text"):
            texts.append(str(review_text))

    if not texts:
        return 50.0

    # Run inference in a thread to avoid blocking the event loop
    score = await asyncio.to_thread(_run_inference, texts)
    return round(score, 2)
