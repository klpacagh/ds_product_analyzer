"""Dropshipping recommendation engine.

Applies a DS-specific composite score on top of existing TrendScore components,
filters out fads, calls Claude Sonnet 4.6 for structured analysis, and caches
results for 4 hours.
"""

from __future__ import annotations

import json
import logging
import time
from collections import defaultdict
from dataclasses import dataclass

import anthropic
from sqlalchemy import desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ds_product_analyzer.config import settings
from ds_product_analyzer.db.models import Product, RawSignal, TrendScore

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Cache (in-memory, 4-hour TTL)
# ---------------------------------------------------------------------------

_CACHE_TTL = 4 * 3600  # seconds

_cache: dict[int, tuple[float, list["DropshippingRecommendation"]]] = {}


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------


@dataclass
class DropshippingRecommendation:
    product: Product
    trend_score: TrendScore
    ds_score: float
    verdict: str               # Strong / Moderate / Speculative
    strengths: list[str]
    risks: list[str]
    strategy: str
    target_channel: str
    sparkline: list[float]     # Last 7 scores, chronological


# ---------------------------------------------------------------------------
# DS-specific composite formula
# ---------------------------------------------------------------------------


def _compute_ds_score(ts: TrendScore) -> float:
    platform_score = (ts.platform_count / 7.0) * 100.0
    return (
        0.30 * ts.trend_shape
        + 0.25 * ts.price_fit
        + 0.20 * ts.sentiment
        + 0.15 * ts.social_velocity
        + 0.10 * platform_score
    )


# ---------------------------------------------------------------------------
# Claude analysis
# ---------------------------------------------------------------------------

_SYSTEM_PROMPT = (
    "You are a dropshipping product analyst. Analyze the provided products and "
    "return a JSON array (no markdown fencing). Each element must have exactly "
    "these fields: name (string), verdict (one of: Strong, Moderate, Speculative), "
    "strengths (array of 2-4 short strings), risks (array of 2-3 short strings), "
    "strategy (1-2 sentence string), target_channel (string, e.g. 'TikTok Ads', "
    "'Google Shopping', 'Instagram Influencers'). Order must match input order."
)


def _build_analysis_prompt(products_data: list[dict]) -> str:
    lines = []
    for i, p in enumerate(products_data, 1):
        price_str = ""
        if p.get("price_low") and p.get("price_high"):
            price_str = f"${p['price_low']:.0f}–${p['price_high']:.0f}"
        elif p.get("price_high"):
            price_str = f"up to ${p['price_high']:.0f}"
        elif p.get("price_low"):
            price_str = f"from ${p['price_low']:.0f}"
        else:
            price_str = "unknown"

        lines.append(
            f"{i}. {p['name']} | category: {p['category']} | price: {price_str} | "
            f"ds_score: {p['ds_score']:.1f} | trend_shape: {p['trend_shape']:.1f} | "
            f"price_fit: {p['price_fit']:.1f} | sentiment: {p['sentiment']:.1f} | "
            f"social_velocity: {p['social_velocity']:.1f} | platform_count: {p['platform_count']} | "
            f"search_accel: {p['search_accel']:.1f} | purchase_intent: {p['purchase_intent']:.1f} | "
            f"platforms: {p['sources']} | first_seen: {p['first_seen']}"
        )
    return "\n".join(lines)


def _call_claude(products_data: list[dict]) -> list[dict] | None:
    """Call Claude Sonnet 4.6 synchronously. Returns parsed list or None."""
    if not settings.anthropic_api_key:
        return None

    client = anthropic.Anthropic(api_key=settings.anthropic_api_key)
    user_prompt = _build_analysis_prompt(products_data)

    try:
        message = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=2048,
            system=_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": user_prompt}],
        )
        raw_text = message.content[0].text.strip()

        # Strip markdown fencing if present
        if raw_text.startswith("```"):
            lines = [l for l in raw_text.split("\n") if not l.strip().startswith("```")]
            raw_text = "\n".join(lines)

        data = json.loads(raw_text)
        if isinstance(data, list) and len(data) == len(products_data):
            return data
        logger.warning("Claude returned unexpected structure, using fallback")
        return None
    except Exception:
        logger.exception("Claude analysis failed, using fallback")
        return None


def _fallback_analysis(p: dict) -> dict:
    """Template-based analysis when Claude is unavailable."""
    ds = p["ds_score"]
    if ds >= 60:
        verdict = "Strong"
    elif ds >= 40:
        verdict = "Moderate"
    else:
        verdict = "Speculative"

    return {
        "name": p["name"],
        "verdict": verdict,
        "strengths": [
            f"Trend shape score: {p['trend_shape']:.0f}/100",
            f"Price fit score: {p['price_fit']:.0f}/100",
            f"Present on {p['platform_count']} platform(s)",
        ],
        "risks": [
            "Analysis unavailable (no API key configured)",
            "Verify margin before sourcing",
        ],
        "strategy": "Research suppliers and validate margins before launching ad campaigns.",
        "target_channel": "TikTok Ads" if p["platform_count"] >= 3 else "Google Shopping",
    }


# ---------------------------------------------------------------------------
# Main public function
# ---------------------------------------------------------------------------


async def generate_dropshipping_recommendations(
    session: AsyncSession,
    top_n: int = 5,
) -> list[DropshippingRecommendation]:
    """Return top-N dropshipping recommendations, using 4-hour cache."""
    now = time.monotonic()
    cached = _cache.get(top_n)
    if cached:
        ts_cached, recs = cached
        if now - ts_cached < _CACHE_TTL:
            logger.info("Returning cached recommendations for top_n=%d", top_n)
            return recs

    recs = await _build_recommendations(session, top_n)
    _cache[top_n] = (now, recs)
    return recs


async def _build_recommendations(
    session: AsyncSession,
    top_n: int,
) -> list[DropshippingRecommendation]:
    # 1. Fetch all (Product, TrendScore) pairs using latest-score subquery
    latest_score_sq = (
        select(
            TrendScore.product_id,
            func.max(TrendScore.id).label("max_id"),
        )
        .group_by(TrendScore.product_id)
        .subquery()
    )

    stmt = (
        select(Product, TrendScore)
        .join(latest_score_sq, Product.id == latest_score_sq.c.product_id)
        .join(TrendScore, TrendScore.id == latest_score_sq.c.max_id)
    )

    all_results = (await session.execute(stmt)).all()

    if not all_results:
        return []

    # 2. Compute DS score for each
    scored = [
        (p, ts, _compute_ds_score(ts))
        for p, ts in all_results
    ]

    # 3. Filter: score >= 30 AND trend_shape > 15 (exclude fads)
    filtered = [
        (p, ts, ds) for p, ts, ds in scored
        if ts.score >= 30 and ts.trend_shape > 15
    ]

    # 4. Fall back to relaxed filter if fewer than top_n pass
    if len(filtered) < top_n:
        logger.info(
            "Strict filter yielded %d products (< %d), relaxing to score >= 20",
            len(filtered), top_n,
        )
        filtered = [
            (p, ts, ds) for p, ts, ds in scored
            if ts.score >= 20
        ]

    # 5. Sort descending by DS composite, take top N
    filtered.sort(key=lambda x: x[2], reverse=True)
    top = filtered[:top_n]

    if not top:
        # Last resort: just take top_n by general score
        scored.sort(key=lambda x: x[1].score, reverse=True)
        top = scored[:top_n]

    # 6. Fetch sparklines (last 7 TrendScore rows per product, chronological)
    product_ids = [p.id for p, ts, ds in top]
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
        history_map = {pid: list(reversed(scores)) for pid, scores in history_map.items()}

    # 7. Fetch platform sources per product
    sources_map: dict[int, list[str]] = defaultdict(list)
    if product_ids:
        sources_rows = (await session.execute(
            select(RawSignal.product_id, RawSignal.source)
            .where(RawSignal.product_id.in_(product_ids))
            .distinct()
        )).all()
        for pid, src in sources_rows:
            sources_map[pid].append(src)

    # 8. Build products_data for Claude
    products_data = [
        {
            "name": p.canonical_name,
            "category": p.category or "unknown",
            "price_low": p.price_low,
            "price_high": p.price_high,
            "ds_score": ds,
            "trend_shape": ts.trend_shape,
            "price_fit": ts.price_fit,
            "sentiment": ts.sentiment,
            "social_velocity": ts.social_velocity,
            "platform_count": ts.platform_count,
            "search_accel": ts.search_accel,
            "purchase_intent": ts.purchase_intent,
            "sources": sources_map.get(p.id, []),
            "first_seen": p.first_seen.strftime("%Y-%m-%d") if p.first_seen else "unknown",
        }
        for p, ts, ds in top
    ]

    # 9. Call Claude (or fallback)
    claude_results = _call_claude(products_data)

    # 10. Build DropshippingRecommendation list
    recommendations = []
    for i, (p, ts, ds) in enumerate(top):
        if claude_results and i < len(claude_results):
            analysis = claude_results[i]
        else:
            analysis = _fallback_analysis(products_data[i])

        recommendations.append(
            DropshippingRecommendation(
                product=p,
                trend_score=ts,
                ds_score=round(ds, 1),
                verdict=analysis.get("verdict", "Speculative"),
                strengths=analysis.get("strengths", []),
                risks=analysis.get("risks", []),
                strategy=analysis.get("strategy", ""),
                target_channel=analysis.get("target_channel", ""),
                sparkline=history_map.get(p.id, []),
            )
        )

    logger.info("Generated %d dropshipping recommendations", len(recommendations))
    return recommendations
