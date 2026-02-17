import json
import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ds_product_analyzer.collectors.base import RawSignalData
from ds_product_analyzer.collectors.google_trends import GoogleTrendsCollector
from ds_product_analyzer.collectors.reddit import RedditCollector
from ds_product_analyzer.db.models import Category, RawSignal
from ds_product_analyzer.db.session import async_session_factory

from .dedup import find_or_create_product
from .trend_scorer import score_all_products

logger = logging.getLogger(__name__)


async def get_all_keywords() -> dict[str, list[str]]:
    """Load keywords from all active categories."""
    async with async_session_factory() as session:
        cats = (
            await session.execute(select(Category).where(Category.active.is_(True)))
        ).scalars().all()
        result = {}
        for cat in cats:
            result[cat.name] = json.loads(cat.seed_keywords)
        return result


async def store_signals(session: AsyncSession, signals: list[RawSignalData]) -> int:
    """Store raw signals in the database and link to products via dedup."""
    count = 0
    for sig in signals:
        product = await find_or_create_product(
            session, sig.product_name, sig.source
        )
        raw = RawSignal(
            product_id=product.id,
            source=sig.source,
            signal_type=sig.signal_type,
            value=sig.value,
            metadata_json=json.dumps(sig.metadata) if sig.metadata else None,
            collected_at=sig.collected_at,
            processed=False,
            product_name=sig.product_name,
        )
        session.add(raw)
        count += 1

    await session.commit()
    return count


async def run_google_collection():
    """Run Google Trends collection for all categories."""
    logger.info("Starting Google Trends collection...")
    keywords_by_cat = await get_all_keywords()
    all_keywords = [kw for kws in keywords_by_cat.values() for kw in kws]

    collector = GoogleTrendsCollector()
    signals = await collector.collect(all_keywords)
    logger.info("Google Trends collected %d signals", len(signals))

    async with async_session_factory() as session:
        stored = await store_signals(session, signals)
    logger.info("Stored %d Google Trends signals", stored)
    return stored


async def run_reddit_collection():
    """Run Reddit collection for all categories."""
    logger.info("Starting Reddit collection...")
    keywords_by_cat = await get_all_keywords()
    all_keywords = [kw for kws in keywords_by_cat.values() for kw in kws]

    collector = RedditCollector()
    signals = await collector.collect(all_keywords)
    logger.info("Reddit collected %d signals", len(signals))

    async with async_session_factory() as session:
        stored = await store_signals(session, signals)
    logger.info("Stored %d Reddit signals", stored)
    return stored


async def run_scoring():
    """Score all products."""
    logger.info("Starting scoring pipeline...")
    async with async_session_factory() as session:
        count = await score_all_products(session)
    logger.info("Scoring complete: %d products scored", count)
    return count


async def run_full_pipeline():
    """Run full collection + scoring pipeline."""
    logger.info("=== Full pipeline run starting ===")
    google_count = 0
    reddit_count = 0

    try:
        google_count = await run_google_collection()
    except Exception as e:
        logger.error("Google collection failed: %s", e)

    try:
        reddit_count = await run_reddit_collection()
    except Exception as e:
        logger.error("Reddit collection failed: %s", e)

    scored = await run_scoring()
    logger.info(
        "=== Pipeline complete: google=%d reddit=%d scored=%d ===",
        google_count, reddit_count, scored,
    )
    return {"google": google_count, "reddit": reddit_count, "scored": scored}
