import json
import logging
from collections import defaultdict
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse
from sqlalchemy import desc, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from ds_product_analyzer.api.app import templates
from ds_product_analyzer.db.models import Category, PriceHistory, Product, RawSignal, TrendScore
from ds_product_analyzer.db.session import get_session
from ds_product_analyzer.pipeline.runner import run_full_pipeline

logger = logging.getLogger(__name__)

router = APIRouter()


# --- HTML Routes ---


@router.get("/", response_class=HTMLResponse)
async def dashboard(
    request: Request,
    category: str | None = None,
    max_price: float = Query(100, ge=0),
    session: AsyncSession = Depends(get_session),
):
    """Main dashboard: ranked list of trending products."""
    # Get latest score per product via subquery
    latest_score = (
        select(
            TrendScore.product_id,
            func.max(TrendScore.id).label("max_id"),
        )
        .group_by(TrendScore.product_id)
        .subquery()
    )

    stmt = (
        select(Product, TrendScore)
        .join(latest_score, Product.id == latest_score.c.product_id)
        .join(TrendScore, TrendScore.id == latest_score.c.max_id)
        .order_by(desc(TrendScore.score))
    )

    if category:
        stmt = stmt.where(Product.category == category)

    # Price filter: show products with no price data OR price_high <= max_price
    stmt = stmt.where(
        or_(Product.price_high.is_(None), Product.price_high <= max_price)
    )
    stmt = stmt.limit(100)

    results = (await session.execute(stmt)).all()

    # Fetch last 7 scores per product for sparklines
    product_ids = [p.id for p, ts in results]
    history_map: dict[int, list[float]] = defaultdict(list)
    if product_ids:
        history_rows = (await session.execute(
            select(TrendScore.product_id, TrendScore.score, TrendScore.scored_at)
            .where(TrendScore.product_id.in_(product_ids))
            .order_by(TrendScore.product_id, desc(TrendScore.scored_at))
        )).all()
        for pid, score, _ in history_rows:
            if len(history_map[pid]) < 7:
                history_map[pid].append(score)
        # Reverse to chronological order (oldest first)
        history_map = {pid: list(reversed(scores)) for pid, scores in history_map.items()}

    products = [
        {
            "id": p.id,
            "name": p.canonical_name,
            "category": p.category or "unknown",
            "score": ts.score,
            "google_velocity": ts.google_velocity,
            "reddit_accel": ts.reddit_accel,
            "amazon_accel": ts.amazon_accel,
            "tiktok_accel": ts.tiktok_accel,
            "sentiment": ts.sentiment,
            "platform_count": ts.platform_count,
            "search_accel": ts.search_accel,
            "social_velocity": ts.social_velocity,
            "price_fit": ts.price_fit,
            "trend_shape": ts.trend_shape,
            "purchase_intent": ts.purchase_intent,
            "recency": ts.recency,
            "scored_at": ts.scored_at,
            "first_seen": p.first_seen,
            "image_url": p.image_url,
            "description": p.description,
            "price_low": p.price_low,
            "price_high": p.price_high,
            "sparkline": history_map.get(p.id, []),
        }
        for p, ts in results
    ]

    categories = (await session.execute(select(Category).where(Category.active.is_(True)))).scalars().all()
    cat_names = [c.name for c in categories]

    # Last collection time
    last_signal = (
        await session.execute(select(func.max(RawSignal.collected_at)))
    ).scalar()

    return templates.TemplateResponse(
        "index.html",
        {
            "request": request,
            "products": products,
            "categories": cat_names,
            "selected_category": category,
            "last_updated": last_signal,
            "total_products": len(products),
        },
    )


@router.get("/products/{product_id}", response_class=HTMLResponse)
async def product_detail(
    request: Request,
    product_id: int,
    session: AsyncSession = Depends(get_session),
):
    """Product detail page with score breakdown and history."""
    product = await session.get(Product, product_id)
    if not product:
        return HTMLResponse("<h1>Product not found</h1>", status_code=404)

    # Score history (last 30 entries)
    scores = (
        await session.execute(
            select(TrendScore)
            .where(TrendScore.product_id == product_id)
            .order_by(desc(TrendScore.scored_at))
            .limit(30)
        )
    ).scalars().all()

    # Raw signals (last 50)
    signals = (
        await session.execute(
            select(RawSignal)
            .where(RawSignal.product_id == product_id)
            .order_by(desc(RawSignal.collected_at))
            .limit(50)
        )
    ).scalars().all()

    # Price history (last 30 entries, chronological)
    price_history_rows = (
        await session.execute(
            select(PriceHistory)
            .where(PriceHistory.product_id == product_id)
            .order_by(desc(PriceHistory.recorded_at))
            .limit(30)
        )
    ).scalars().all()
    price_history_rows = list(reversed(price_history_rows))

    price_chart_labels = [ph.recorded_at.strftime("%m/%d %H:%M") for ph in price_history_rows]
    price_chart_values = [ph.price for ph in price_history_rows]

    latest_score = scores[0] if scores else None

    # Extract unique reference URLs from signal metadata
    references: list[dict[str, str]] = []
    seen_urls: set[str] = set()
    for s in signals:
        if not s.metadata_json:
            continue
        try:
            meta = json.loads(s.metadata_json)
        except (json.JSONDecodeError, TypeError):
            continue
        url = meta.get("product_url") or meta.get("url")
        if url and url not in seen_urls:
            seen_urls.add(url)
            references.append({"source": s.source, "url": url})

    # Build signal-level URL lookup for template linking
    signal_urls: dict[int, str] = {}
    for s in signals:
        if not s.metadata_json:
            continue
        try:
            meta = json.loads(s.metadata_json)
        except (json.JSONDecodeError, TypeError):
            continue
        url = meta.get("product_url") or meta.get("url")
        if url:
            signal_urls[s.id] = url

    # Prepare chart data (reversed for chronological order)
    chart_labels = [s.scored_at.strftime("%m/%d %H:%M") for s in reversed(scores)]
    chart_values = [s.score for s in reversed(scores)]

    return templates.TemplateResponse(
        "product.html",
        {
            "request": request,
            "product": product,
            "latest_score": latest_score,
            "scores": scores,
            "signals": signals,
            "references": references,
            "signal_urls": signal_urls,
            "chart_labels": json.dumps(chart_labels),
            "chart_values": json.dumps(chart_values),
            "price_history": price_history_rows,
            "price_chart_labels": json.dumps(price_chart_labels),
            "price_chart_values": json.dumps(price_chart_values),
        },
    )


# --- JSON API Routes ---


@router.get("/api/products")
async def api_products(
    limit: int = Query(50, ge=1, le=200),
    category: str | None = None,
    min_score: float = Query(0, ge=0),
    session: AsyncSession = Depends(get_session),
):
    """JSON endpoint: ranked product list."""
    latest_score = (
        select(
            TrendScore.product_id,
            func.max(TrendScore.id).label("max_id"),
        )
        .group_by(TrendScore.product_id)
        .subquery()
    )

    stmt = (
        select(Product, TrendScore)
        .join(latest_score, Product.id == latest_score.c.product_id)
        .join(TrendScore, TrendScore.id == latest_score.c.max_id)
        .where(TrendScore.score >= min_score)
        .order_by(desc(TrendScore.score))
        .limit(limit)
    )

    if category:
        stmt = stmt.where(Product.category == category)

    results = (await session.execute(stmt)).all()

    # Fetch last 7 scores per product for sparklines
    product_ids = [p.id for p, ts in results]
    api_history_map: dict[int, list[float]] = defaultdict(list)
    if product_ids:
        history_rows = (await session.execute(
            select(TrendScore.product_id, TrendScore.score, TrendScore.scored_at)
            .where(TrendScore.product_id.in_(product_ids))
            .order_by(TrendScore.product_id, desc(TrendScore.scored_at))
        )).all()
        for pid, score, _ in history_rows:
            if len(api_history_map[pid]) < 7:
                api_history_map[pid].append(score)
        api_history_map = {pid: list(reversed(scores)) for pid, scores in api_history_map.items()}

    return [
        {
            "id": p.id,
            "name": p.canonical_name,
            "category": p.category,
            "image_url": p.image_url,
            "description": p.description,
            "price_low": p.price_low,
            "price_high": p.price_high,
            "score": ts.score,
            "google_velocity": ts.google_velocity,
            "reddit_accel": ts.reddit_accel,
            "amazon_accel": ts.amazon_accel,
            "tiktok_accel": ts.tiktok_accel,
            "sentiment": ts.sentiment,
            "platform_count": ts.platform_count,
            "search_accel": ts.search_accel,
            "social_velocity": ts.social_velocity,
            "price_fit": ts.price_fit,
            "trend_shape": ts.trend_shape,
            "purchase_intent": ts.purchase_intent,
            "recency": ts.recency,
            "scored_at": ts.scored_at.isoformat(),
            "sparkline": api_history_map.get(p.id, []),
        }
        for p, ts in results
    ]


@router.get("/api/products/{product_id}")
async def api_product_detail(
    product_id: int,
    session: AsyncSession = Depends(get_session),
):
    """JSON endpoint: product detail + score history."""
    product = await session.get(Product, product_id)
    if not product:
        return JSONResponse({"error": "not found"}, status_code=404)

    scores = (
        await session.execute(
            select(TrendScore)
            .where(TrendScore.product_id == product_id)
            .order_by(desc(TrendScore.scored_at))
            .limit(30)
        )
    ).scalars().all()

    price_history_rows = (
        await session.execute(
            select(PriceHistory)
            .where(PriceHistory.product_id == product_id)
            .order_by(desc(PriceHistory.recorded_at))
            .limit(30)
        )
    ).scalars().all()

    return {
        "id": product.id,
        "name": product.canonical_name,
        "category": product.category,
        "image_url": product.image_url,
        "description": product.description,
        "source_url": product.source_url,
        "price_low": product.price_low,
        "price_high": product.price_high,
        "first_seen": product.first_seen.isoformat() if product.first_seen else None,
        "scores": [
            {
                "score": s.score,
                "google_velocity": s.google_velocity,
                "reddit_accel": s.reddit_accel,
                "amazon_accel": s.amazon_accel,
                "tiktok_accel": s.tiktok_accel,
                "sentiment": s.sentiment,
                "platform_count": s.platform_count,
                "search_accel": s.search_accel,
                "social_velocity": s.social_velocity,
                "price_fit": s.price_fit,
                "trend_shape": s.trend_shape,
                "purchase_intent": s.purchase_intent,
                "recency": s.recency,
                "scored_at": s.scored_at.isoformat(),
            }
            for s in scores
        ],
        "price_history": [
            {
                "price": ph.price,
                "source": ph.source,
                "recorded_at": ph.recorded_at.isoformat(),
            }
            for ph in price_history_rows
        ],
    }


@router.post("/api/collect/trigger")
async def trigger_collection():
    """Manually trigger a full collection + scoring run."""
    try:
        result = await run_full_pipeline()
        return {"status": "ok", **result}
    except Exception as e:
        logger.error("Manual trigger failed: %s", e)
        return JSONResponse({"status": "error", "detail": str(e)}, status_code=500)


@router.get("/api/health")
async def health(session: AsyncSession = Depends(get_session)):
    """Health check: DB connectivity + last collection time."""
    last_signal = (
        await session.execute(select(func.max(RawSignal.collected_at)))
    ).scalar()
    product_count = (
        await session.execute(select(func.count(Product.id)))
    ).scalar()
    return {
        "status": "ok",
        "last_collection": last_signal.isoformat() if last_signal else None,
        "product_count": product_count,
    }
