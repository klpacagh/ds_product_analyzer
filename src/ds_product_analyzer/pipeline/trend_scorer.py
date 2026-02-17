import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ds_product_analyzer.db.models import Product, RawSignal, TrendScore

logger = logging.getLogger(__name__)

# Weights for composite score
W_GOOGLE_VELOCITY = 0.35
W_REDDIT_ACCEL = 0.30
W_PLATFORM_COUNT = 0.20
W_RECENCY = 0.15


async def score_product(session: AsyncSession, product: Product) -> TrendScore:
    """Compute a trend score for a product based on its raw signals."""
    now = datetime.now(timezone.utc)
    cutoff_7d = now - timedelta(days=7)
    cutoff_24h = now - timedelta(hours=24)

    signals = (
        await session.execute(
            select(RawSignal)
            .where(RawSignal.product_id == product.id)
            .where(RawSignal.collected_at >= cutoff_7d)
        )
    ).scalars().all()

    # Google trend velocity
    google_signals = [s for s in signals if s.source == "google_trends" and s.signal_type == "search_velocity"]
    google_velocity = max((s.value for s in google_signals), default=0.0)
    # Normalize to 0-100 range (velocity can range from -100 to 5000+)
    google_norm = min(max(google_velocity / 50, 0), 100)

    # Reddit upvote acceleration
    reddit_signals = [s for s in signals if s.source == "reddit" and s.signal_type == "upvote_velocity"]
    reddit_accel = max((s.value for s in reddit_signals), default=0.0)
    # Normalize: 100+ upvotes/hr is very high
    reddit_norm = min(reddit_accel / 1, 100)  # 1 upvote/hr = score 1, cap at 100

    # Cross-platform count: how many distinct sources mention this product
    sources = {s.source for s in signals}
    platform_count = len(sources)
    platform_norm = min(platform_count / 4 * 100, 100)  # 4 platforms = 100

    # Recency bias: products with signals in last 24h score higher
    recent_signals = [s for s in signals if s.collected_at.replace(tzinfo=timezone.utc) >= cutoff_24h]
    recency_norm = min(len(recent_signals) / 5 * 100, 100)  # 5+ recent signals = 100

    # Composite score
    score = (
        W_GOOGLE_VELOCITY * google_norm
        + W_REDDIT_ACCEL * reddit_norm
        + W_PLATFORM_COUNT * platform_norm
        + W_RECENCY * recency_norm
    )
    score = round(min(score, 100), 2)

    trend = TrendScore(
        product_id=product.id,
        score=score,
        google_velocity=round(google_norm, 2),
        reddit_accel=round(reddit_norm, 2),
        amazon_accel=0.0,  # Phase 2
        platform_count=platform_count,
        sentiment=0.0,  # Phase 2
    )
    session.add(trend)
    return trend


async def score_all_products(session: AsyncSession) -> int:
    """Score all products that have raw signals. Returns count of scored products."""
    # Get products that have at least one signal
    stmt = (
        select(Product)
        .where(
            Product.id.in_(
                select(RawSignal.product_id)
                .where(RawSignal.product_id.is_not(None))
                .distinct()
            )
        )
    )
    products = (await session.execute(stmt)).scalars().all()

    count = 0
    for product in products:
        await score_product(session, product)
        count += 1

    await session.commit()
    logger.info("Scored %d products", count)
    return count
