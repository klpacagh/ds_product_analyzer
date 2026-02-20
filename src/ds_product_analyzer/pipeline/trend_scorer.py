import json
import logging
import re
from datetime import datetime, timedelta, timezone

from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from ds_product_analyzer.db.models import Product, RawSignal, TrendScore

from .sentiment import compute_product_sentiment

logger = logging.getLogger(__name__)

# Enhanced scoring weights (sum = 1.00)
W_SEARCH_ACCEL = 0.25
W_SOCIAL_VELOCITY = 0.18
W_RETAIL_MOMENTUM = 0.12
W_PRICE_FIT = 0.10
W_SENTIMENT = 0.10
W_TREND_SHAPE = 0.08
W_PLATFORM_COUNT = 0.07
W_PURCHASE_INTENT = 0.05
W_RECENCY = 0.05
# Future placeholders (weight = 0 until collectors exist)
W_AD_LONGEVITY = 0.0
W_REVIEW_GROWTH = 0.0
W_SATURATION = 0.0

# Purchase intent patterns
_INTENT_PATTERNS = re.compile(
    r"where (?:can i|to|do i) buy|"
    r"need this|take my money|shut up and take|"
    r"link\??|just bought|how much|"
    r"where.{0,10}get (?:this|one|it)|"
    r"added to (?:cart|wishlist)|"
    r"in stock|buy (?:this|one|it)|"
    r"price\??|cost\??|"
    r"want (?:this|one|it) so bad",
    re.IGNORECASE,
)


def _compute_search_accel(signals: list[RawSignal]) -> float:
    """Enhanced Google Trends score: velocity + breakout + rising signals."""
    google_velocity_signals = [
        s for s in signals
        if s.source == "google_trends" and s.signal_type == "search_velocity"
    ]
    velocity = max((s.value for s in google_velocity_signals), default=0.0)
    base = min(max(velocity / 50, 0), 100)

    breakout_signals = [
        s for s in signals
        if s.source == "google_trends" and s.signal_type == "breakout"
    ]
    breakout_bonus = 30.0 if breakout_signals else 0.0

    rising_signals = [
        s for s in signals
        if s.source == "google_trends" and s.signal_type == "rising"
    ]
    rising_bonus = min(len(rising_signals) * 5, 20)

    return min(base + breakout_bonus + rising_bonus, 100)


def _compute_social_velocity(signals: list[RawSignal]) -> float:
    """TikTok + Reddit + Etsy combined social velocity with creator diversity bonus."""
    # TikTok component (55% weight)
    tiktok_signals = [
        s for s in signals
        if s.source == "tiktok" and s.signal_type == "tiktok_popularity"
    ]
    tiktok_raw = max((s.value for s in tiktok_signals), default=0.0)

    # TikTok: use individual counts when present (Playwright path), else fall back
    plays = likes = shares = comments_t = 0.0
    has_detailed = False
    for s in tiktok_signals:
        if not s.metadata_json:
            continue
        try:
            meta = json.loads(s.metadata_json)
        except (json.JSONDecodeError, TypeError):
            continue
        if "play_count" in meta or "like_count" in meta:
            has_detailed = True
            plays      = max(plays,      meta.get("play_count",    0) or 0)
            likes      = max(likes,      meta.get("like_count",    0) or 0)
            shares     = max(shares,     meta.get("share_count",   0) or 0)
            comments_t = max(comments_t, meta.get("comment_count", 0) or 0)

    if has_detailed:
        views_norm  = min(plays      / 100_000, 100)
        likes_norm  = min(likes      /  10_000, 100)
        shares_norm = min(shares     /   1_000, 100)
        cmt_t_norm  = min(comments_t /     500, 100)
        tiktok_norm = (0.30 * views_norm + 0.40 * likes_norm
                       + 0.20 * shares_norm + 0.10 * cmt_t_norm)
    else:
        tiktok_norm = min(max(tiktok_raw / 100, 0), 100)

    # Reddit component (35% weight)
    reddit_signals = [
        s for s in signals
        if s.source == "reddit" and s.signal_type == "upvote_velocity"
    ]
    reddit_raw = max((s.value for s in reddit_signals), default=0.0)

    # Reddit: upvote velocity (80%) + comment volume (20%)
    _reddit_cmt_vals = []
    for s in reddit_signals:
        if not s.metadata_json:
            continue
        try:
            _reddit_cmt_vals.append(json.loads(s.metadata_json).get("num_comments", 0) or 0)
        except (json.JSONDecodeError, TypeError):
            pass
    reddit_comments = max(_reddit_cmt_vals, default=0.0)
    comment_norm = min(reddit_comments / 100, 100)
    reddit_norm  = min(0.80 * min(reddit_raw, 100) + 0.20 * comment_norm, 100)

    # Etsy component (10% weight): favorites count, normalised /500, cap 100
    etsy_raw = max(
        (s.value for s in signals if s.source == "etsy" and s.signal_type == "etsy_trending"),
        default=0.0,
    )
    etsy_norm = min(etsy_raw / 500, 100)

    # Creator diversity bonus: count distinct authors from TikTok metadata
    authors = set()
    for s in tiktok_signals:
        if s.metadata_json:
            try:
                meta = json.loads(s.metadata_json)
                author = meta.get("author") or meta.get("creator") or meta.get("username")
                if author:
                    authors.add(author)
            except (json.JSONDecodeError, TypeError):
                pass
    diversity_bonus = min(len(authors) * 5, 15)

    return min(0.55 * tiktok_norm + 0.35 * reddit_norm + 0.10 * etsy_norm + diversity_bonus, 100)


def _compute_price_fit(product: Product) -> float:
    """Score based on $20-$60 sweet spot from existing price data."""
    low = product.price_low
    high = product.price_high

    if low is None and high is None:
        return 50.0  # neutral when no price data

    # Use midpoint if both available, otherwise whichever we have
    if low is not None and high is not None:
        price = (low + high) / 2
    elif low is not None:
        price = low
    else:
        price = high

    # $20-$60 sweet spot = 100
    if 20 <= price <= 60:
        return 100.0
    # $10-$20: linear 50-100
    elif 10 <= price < 20:
        return 50 + (price - 10) / 10 * 50
    # $60-$80: linear 100-50
    elif 60 < price <= 80:
        return 100 - (price - 60) / 20 * 50
    # $0-$10: linear 0-50
    elif 0 <= price < 10:
        return price / 10 * 50
    # $80-$150: linear 50-0
    elif 80 < price <= 150:
        return max(50 - (price - 80) / 70 * 50, 0)
    # > $150: 0
    else:
        return 0.0


async def _compute_trend_shape(session: AsyncSession, product_id: int) -> float:
    """Detect fad vs. incline from TrendScore history."""
    rows = (
        await session.execute(
            select(TrendScore.score)
            .where(TrendScore.product_id == product_id)
            .order_by(desc(TrendScore.scored_at))
            .limit(10)
        )
    ).scalars().all()

    # Reverse to chronological order
    scores = list(reversed(rows))

    if len(scores) < 3:
        return 50.0  # neutral — not enough history

    deltas = [scores[i + 1] - scores[i] for i in range(len(scores) - 1)]
    avg_delta = sum(deltas) / len(deltas)

    # Spike-then-drop detection: any delta > 15 followed by delta < -10
    for i in range(len(deltas) - 1):
        if deltas[i] > 15 and deltas[i + 1] < -10:
            return 15.0  # fad warning

    # Declining trend
    if avg_delta < -2:
        # Map avg_delta from -2..-20 to 30..0
        return max(30 + (avg_delta + 2) / 18 * 30, 0)

    # Gradual incline: avg_delta > 0 and no single jump > 20
    if avg_delta > 0 and all(d <= 20 for d in deltas):
        # Map avg_delta from 0..10 to 70..100
        return min(70 + avg_delta / 10 * 30, 100)

    # Flat or mixed
    return 50.0


def _compute_purchase_intent(signals: list[RawSignal]) -> float:
    """Regex scan of Reddit/Amazon/TikTok text for purchase intent phrases."""
    texts: list[str] = []
    for s in signals:
        if s.source not in ("reddit", "amazon", "tiktok"):
            continue
        if not s.metadata_json:
            continue
        try:
            meta = json.loads(s.metadata_json)
        except (json.JSONDecodeError, TypeError):
            continue

        # Collect title and comments text
        title = meta.get("title", "")
        if title:
            texts.append(title)
        for comment in meta.get("top_comments", []):
            if isinstance(comment, str):
                texts.append(comment)
            elif isinstance(comment, dict):
                texts.append(comment.get("body", ""))
        # Also check description / body fields
        body = meta.get("body") or meta.get("description") or ""
        if body:
            texts.append(body)

    if not texts:
        return 0.0

    matches = sum(1 for t in texts if _INTENT_PATTERNS.search(t))
    return min(matches / len(texts) * 100, 100)


def _compute_retail_momentum(signals: list[RawSignal]) -> float:
    """Retail bestseller momentum across Amazon, Walmart, and Target."""
    # Amazon: bsr_momentum values are % change — normalise /10, cap 100
    amazon_raw = max(
        (s.value for s in signals if s.source == "amazon" and s.signal_type == "bsr_momentum"),
        default=0.0,
    )
    amazon_norm = min(max(amazon_raw / 10, 0), 100)

    # Walmart: walmart_bestseller values are already rank scores 0-100
    walmart_norm = max(
        (s.value for s in signals if s.source == "walmart" and s.signal_type == "walmart_bestseller"),
        default=0.0,
    )

    # Target: target_trending values are already rank scores 0-100
    target_norm = max(
        (s.value for s in signals if s.source == "target" and s.signal_type == "target_trending"),
        default=0.0,
    )

    return max(amazon_norm, walmart_norm, target_norm)


async def score_product(session: AsyncSession, product: Product) -> TrendScore:
    """Compute an enhanced trend score for a product based on its raw signals."""
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(days=31)
    cutoff_24h = now - timedelta(hours=24)

    signals = (
        await session.execute(
            select(RawSignal)
            .where(RawSignal.product_id == product.id)
            .where(RawSignal.collected_at >= cutoff)
        )
    ).scalars().all()

    # --- Enhanced components ---
    search_accel = _compute_search_accel(signals)
    social_velocity = _compute_social_velocity(signals)

    retail_momentum = _compute_retail_momentum(signals)
    amazon_norm = retail_momentum  # stored in amazon_accel column for backward compat

    price_fit = _compute_price_fit(product)
    sentiment_score = await compute_product_sentiment(session, product)
    trend_shape = await _compute_trend_shape(session, product.id)

    # Cross-platform count
    sources = {s.source for s in signals}
    platform_count = len(sources)
    platform_norm = min(platform_count / 7 * 100, 100)

    purchase_intent = _compute_purchase_intent(signals)

    # Recency
    recent_signals = [s for s in signals if s.collected_at.replace(tzinfo=timezone.utc) >= cutoff_24h]
    recency_norm = min(len(recent_signals) / 5 * 100, 100)

    # --- Legacy component values (backward compat) ---
    google_velocity_signals = [s for s in signals if s.source == "google_trends" and s.signal_type == "search_velocity"]
    google_norm = min(max(max((s.value for s in google_velocity_signals), default=0.0) / 50, 0), 100)

    reddit_signals = [s for s in signals if s.source == "reddit" and s.signal_type == "upvote_velocity"]
    reddit_norm = min(max((s.value for s in reddit_signals), default=0.0), 100)

    tiktok_signals = [s for s in signals if s.source == "tiktok" and s.signal_type == "tiktok_popularity"]
    tiktok_norm = min(max(max((s.value for s in tiktok_signals), default=0.0) / 100, 0), 100)

    # --- Weighted composite score ---
    score = (
        W_SEARCH_ACCEL * search_accel
        + W_SOCIAL_VELOCITY * social_velocity
        + W_RETAIL_MOMENTUM * retail_momentum
        + W_PRICE_FIT * price_fit
        + W_SENTIMENT * sentiment_score
        + W_TREND_SHAPE * trend_shape
        + W_PLATFORM_COUNT * platform_norm
        + W_PURCHASE_INTENT * purchase_intent
        + W_RECENCY * recency_norm
    )
    score = round(min(score, 100), 2)

    trend = TrendScore(
        product_id=product.id,
        score=score,
        # Legacy columns (backward compat)
        google_velocity=round(google_norm, 2),
        reddit_accel=round(reddit_norm, 2),
        amazon_accel=round(amazon_norm, 2),
        tiktok_accel=round(tiktok_norm, 2),
        platform_count=platform_count,
        sentiment=round(sentiment_score, 2),
        # Enhanced columns
        search_accel=round(search_accel, 2),
        social_velocity=round(social_velocity, 2),
        price_fit=round(price_fit, 2),
        trend_shape=round(trend_shape, 2),
        purchase_intent=round(purchase_intent, 2),
        recency=round(recency_norm, 2),
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
