import asyncio
import json
import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ds_product_analyzer.collectors.base import RawSignalData
from ds_product_analyzer.collectors.google_trends import GoogleTrendsCollector
from ds_product_analyzer.collectors.reddit import RedditCollector
from ds_product_analyzer.collectors.amazon import AmazonMoversCollector
from ds_product_analyzer.collectors.tiktok import TikTokCollector
from ds_product_analyzer.collectors.etsy import EtsyCollector
from ds_product_analyzer.collectors.walmart import WalmartCollector
from ds_product_analyzer.collectors.target import TargetCollector
from ds_product_analyzer.collectors.shopify import ShopifyCollector
from ds_product_analyzer.collectors.aliexpress import AliExpressCollector
from ds_product_analyzer.config import settings
from ds_product_analyzer.db.models import Category, PriceHistory, Product, RawSignal
from ds_product_analyzer.db.session import async_session_factory

from .dedup import find_or_create_product
from .llm_extract import extract_and_filter
from .normalizer import normalize_product_name
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
        category = sig.metadata.get("category") if sig.metadata else None
        product = await find_or_create_product(
            session, sig.product_name, sig.source, category=category
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
    signals = await extract_and_filter(signals)

    async with async_session_factory() as session:
        stored = await store_signals(session, signals)
        await _enrich_source_urls(session, signals)
    logger.info("Stored %d Reddit signals", stored)
    return stored


async def run_amazon_collection():
    """Run Amazon Movers & Shakers collection for all categories."""
    logger.info("Starting Amazon collection...")
    keywords_by_cat = await get_all_keywords()
    all_keywords = [kw for kws in keywords_by_cat.values() for kw in kws]

    collector = AmazonMoversCollector()
    signals = await collector.collect(all_keywords)
    logger.info("Amazon collected %d signals", len(signals))
    signals = await extract_and_filter(signals)

    async with async_session_factory() as session:
        stored = await store_signals(session, signals)
        await _enrich_products_from_amazon(session, signals)
    logger.info("Stored %d Amazon signals", stored)
    return stored


async def run_tiktok_collection():
    """Run TikTok collection."""
    logger.info("Starting TikTok collection...")
    keywords_by_cat = await get_all_keywords()
    all_keywords = [kw for kws in keywords_by_cat.values() for kw in kws]

    collector = TikTokCollector()
    signals = await collector.collect(all_keywords)
    logger.info("TikTok collected %d signals", len(signals))
    signals = await extract_and_filter(signals)

    async with async_session_factory() as session:
        stored = await store_signals(session, signals)
        await _record_price_history(session, signals, "tiktok")
        await _enrich_source_urls(session, signals)
    logger.info("Stored %d TikTok signals", stored)
    return stored


async def run_etsy_collection():
    """Run Etsy collection for all categories."""
    logger.info("Starting Etsy collection...")
    keywords_by_cat = await get_all_keywords()
    all_keywords = [kw for kws in keywords_by_cat.values() for kw in kws]

    collector = EtsyCollector()
    signals = await collector.collect(all_keywords)
    logger.info("Etsy collected %d signals", len(signals))
    signals = await extract_and_filter(signals)

    async with async_session_factory() as session:
        stored = await store_signals(session, signals)
        await _record_price_history(session, signals, "etsy")
        await _enrich_source_urls(session, signals)
    logger.info("Stored %d Etsy signals", stored)
    return stored


async def run_walmart_collection():
    """Run Walmart bestseller collection."""
    logger.info("Starting Walmart collection...")
    keywords_by_cat = await get_all_keywords()
    all_keywords = [kw for kws in keywords_by_cat.values() for kw in kws]

    collector = WalmartCollector()
    signals = await collector.collect(all_keywords)
    logger.info("Walmart collected %d signals", len(signals))
    signals = await extract_and_filter(signals)

    async with async_session_factory() as session:
        stored = await store_signals(session, signals)
        await _record_price_history(session, signals, "walmart")
        await _enrich_source_urls(session, signals)
    logger.info("Stored %d Walmart signals", stored)
    return stored


async def run_target_collection():
    """Run Target trending collection."""
    logger.info("Starting Target collection...")
    keywords_by_cat = await get_all_keywords()
    all_keywords = [kw for kws in keywords_by_cat.values() for kw in kws]

    collector = TargetCollector()
    signals = await collector.collect(all_keywords)
    logger.info("Target collected %d signals", len(signals))
    signals = await extract_and_filter(signals)

    async with async_session_factory() as session:
        stored = await store_signals(session, signals)
        await _record_price_history(session, signals, "target")
        await _enrich_source_urls(session, signals)
    logger.info("Stored %d Target signals", stored)
    return stored


async def run_shopify_collection():
    """Run Shopify bestseller collection."""
    logger.info("Starting Shopify collection...")
    keywords_by_cat = await get_all_keywords()
    all_keywords = [kw for kws in keywords_by_cat.values() for kw in kws]

    collector = ShopifyCollector()
    signals = await collector.collect(all_keywords)
    logger.info("Shopify collected %d signals", len(signals))

    async with async_session_factory() as session:
        stored = await store_signals(session, signals)
        await _record_price_history(session, signals, "shopify")
        await _enrich_source_urls(session, signals)
    logger.info("Stored %d Shopify signals", stored)
    return stored


async def run_aliexpress_collection():
    """Run AliExpress hot products collection."""
    logger.info("Starting AliExpress collection...")
    keywords_by_cat = await get_all_keywords()
    all_keywords = [kw for kws in keywords_by_cat.values() for kw in kws]

    collector = AliExpressCollector()
    signals = await collector.collect(all_keywords)
    logger.info("AliExpress collected %d signals", len(signals))

    async with async_session_factory() as session:
        stored = await store_signals(session, signals)
        await _record_price_history(session, signals, "aliexpress")
        await _enrich_source_urls(session, signals)
    logger.info("Stored %d AliExpress signals", stored)
    return stored


async def _write_price_for_product(
    session: AsyncSession, product: Product, price_val: float, source: str
) -> None:
    session.add(PriceHistory(product_id=product.id, price=price_val, source=source))
    if product.price_low is None or price_val < product.price_low:
        product.price_low = price_val
    if product.price_high is None or price_val > product.price_high:
        product.price_high = price_val


async def _record_price_history(
    session: AsyncSession, signals: list[RawSignalData], source: str
) -> None:
    """Record price history from signals and update Product price range."""
    for sig in signals:
        if not sig.metadata:
            continue

        price = sig.metadata.get("price")
        if price is None:
            continue

        product = (
            await session.execute(
                select(Product).where(Product.canonical_name == normalize_product_name(sig.product_name))
            )
        ).scalar_one_or_none()

        if not product:
            continue

        await _write_price_for_product(session, product, float(price), source)

    await session.commit()


async def _enrich_products_from_amazon(
    session: AsyncSession, signals: list[RawSignalData]
) -> None:
    """Update Product image_url from Amazon metadata and record prices."""
    await _record_price_history(session, signals, "amazon")

    for sig in signals:
        if not sig.metadata:
            continue

        image_url = sig.metadata.get("image_url")
        if not image_url:
            continue

        product = (
            await session.execute(
                select(Product).where(Product.canonical_name == normalize_product_name(sig.product_name))
            )
        ).scalar_one_or_none()

        if not product:
            continue

        if product.image_url is None:
            product.image_url = image_url
            logger.debug("Enriched product '%s' image from Amazon", product.canonical_name)

        product_url = sig.metadata.get("product_url")
        if product_url and product.source_url is None:
            product.source_url = product_url

    await session.commit()


async def _enrich_source_urls(session: AsyncSession, signals: list[RawSignalData]) -> None:
    """Set Product.source_url from signal metadata (write-once)."""
    for sig in signals:
        if not sig.metadata:
            continue
        url = sig.metadata.get("product_url") or sig.metadata.get("url")
        if not url:
            continue
        product = (
            await session.execute(
                select(Product).where(Product.canonical_name == normalize_product_name(sig.product_name))
            )
        ).scalar_one_or_none()
        if product and product.source_url is None:
            product.source_url = url
    await session.commit()


async def run_price_enrichment():
    """Fetch current prices for products with known Amazon URLs."""
    from ds_product_analyzer.collectors.amazon import fetch_product_price

    async with async_session_factory() as session:
        products = (
            await session.execute(
                select(Product).where(
                    Product.source_url.like("%amazon.com/dp/%")
                )
            )
        ).scalars().all()

    logger.info("Price enrichment: %d products with Amazon URLs", len(products))
    enriched = 0
    for product in products:
        price = await asyncio.to_thread(fetch_product_price, product.source_url)
        if price is None:
            continue
        async with async_session_factory() as session:
            product_row = await session.get(Product, product.id)
            if product_row:
                await _write_price_for_product(session, product_row, price, "amazon")
                await session.commit()
                enriched += 1
        await asyncio.sleep(settings.amazon_rate_limit_secs)

    logger.info("Price enrichment complete: %d prices updated", enriched)
    return enriched


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
    amazon_count = 0
    tiktok_count = 0
    etsy_count = 0
    walmart_count = 0
    target_count = 0
    shopify_count = 0
    aliexpress_count = 0

    try:
        google_count = await run_google_collection()
    except Exception as e:
        logger.error("Google collection failed: %s", e)

    try:
        reddit_count = await run_reddit_collection()
    except Exception as e:
        logger.error("Reddit collection failed: %s", e)

    try:
        amazon_count = await run_amazon_collection()
    except Exception as e:
        logger.error("Amazon collection failed: %s", e)

    try:
        tiktok_count = await run_tiktok_collection()
    except Exception as e:
        logger.error("TikTok collection failed: %s", e)

    try:
        etsy_count = await run_etsy_collection()
    except Exception as e:
        logger.error("Etsy collection failed: %s", e)

    try:
        walmart_count = await run_walmart_collection()
    except Exception as e:
        logger.error("Walmart collection failed: %s", e)

    try:
        target_count = await run_target_collection()
    except Exception as e:
        logger.error("Target collection failed: %s", e)

    try:
        shopify_count = await run_shopify_collection()
    except Exception as e:
        logger.error("Shopify collection failed: %s", e)

    try:
        aliexpress_count = await run_aliexpress_collection()
    except Exception as e:
        logger.error("AliExpress collection failed: %s", e)

    scored = await run_scoring()
    logger.info(
        "=== Pipeline complete: google=%d reddit=%d amazon=%d tiktok=%d "
        "etsy=%d walmart=%d target=%d shopify=%d aliexpress=%d scored=%d ===",
        google_count, reddit_count, amazon_count, tiktok_count,
        etsy_count, walmart_count, target_count, shopify_count, aliexpress_count, scored,
    )
    return {
        "google": google_count,
        "reddit": reddit_count,
        "amazon": amazon_count,
        "tiktok": tiktok_count,
        "etsy": etsy_count,
        "walmart": walmart_count,
        "target": target_count,
        "shopify": shopify_count,
        "aliexpress": aliexpress_count,
        "scored": scored,
    }
