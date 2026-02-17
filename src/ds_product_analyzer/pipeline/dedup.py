import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from thefuzz import fuzz

from ds_product_analyzer.db.models import Product, ProductAlias

from .normalizer import normalize_product_name

logger = logging.getLogger(__name__)

MATCH_THRESHOLD = 80


async def find_or_create_product(
    session: AsyncSession,
    raw_name: str,
    source: str,
    category: str | None = None,
) -> Product:
    """Find an existing product by fuzzy name match, or create a new one."""
    normalized = normalize_product_name(raw_name)

    # 1. Exact match on canonical_name
    stmt = select(Product).where(Product.canonical_name == normalized)
    product = (await session.execute(stmt)).scalar_one_or_none()
    if product:
        return product

    # 2. Exact match on alias
    stmt = select(ProductAlias).where(ProductAlias.alias_name == normalized)
    alias = (await session.execute(stmt)).scalar_one_or_none()
    if alias:
        return await session.get(Product, alias.product_id)

    # 3. Fuzzy match against all known names
    all_products = (await session.execute(select(Product))).scalars().all()
    best_match: Product | None = None
    best_score = 0

    for p in all_products:
        score = fuzz.token_sort_ratio(normalized, p.canonical_name)
        if score > best_score:
            best_score = score
            best_match = p

        # Also check aliases
        for a in await _get_aliases(session, p.id):
            alias_score = fuzz.token_sort_ratio(normalized, a.alias_name)
            if alias_score > best_score:
                best_score = alias_score
                best_match = p

    if best_match and best_score >= MATCH_THRESHOLD:
        # Add as alias for this product
        session.add(ProductAlias(
            product_id=best_match.id,
            alias_name=normalized,
            source=source,
        ))
        logger.debug("Matched '%s' to '%s' (score=%d)", normalized, best_match.canonical_name, best_score)
        return best_match

    # 4. Create new product
    product = Product(canonical_name=normalized, category=category)
    session.add(product)
    await session.flush()  # get the ID
    session.add(ProductAlias(product_id=product.id, alias_name=normalized, source=source))
    logger.info("New product: '%s' (source=%s)", normalized, source)
    return product


async def _get_aliases(session: AsyncSession, product_id: int) -> list[ProductAlias]:
    stmt = select(ProductAlias).where(ProductAlias.product_id == product_id)
    return list((await session.execute(stmt)).scalars().all())
