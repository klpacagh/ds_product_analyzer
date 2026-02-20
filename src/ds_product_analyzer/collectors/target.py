"""Target trending products collector.

Uses Playwright to navigate Target category pages and intercepts responses
from the RedSky API (redsky.target.com) to extract product lists.

Returns empty list gracefully if all approaches fail.
"""

import asyncio
import logging
import os
from pathlib import Path

from ds_product_analyzer.config import settings

from .base import BaseCollector, RawSignalData

logger = logging.getLogger(__name__)

TARGET_CATEGORIES = {
    "electronics": "https://www.target.com/c/electronics/-/N-5xtg6",
    "home":        "https://www.target.com/c/home/-/N-5xtnr",
    "clothing":    "https://www.target.com/c/clothing-accessories-shoes/-/N-5xtbj",
    "beauty":      "https://www.target.com/c/beauty/-/N-5xu1e",
    "sports":      "https://www.target.com/c/sports-outdoors/-/N-5xt5q",
    "toys":        "https://www.target.com/c/toys/-/N-5xtb3",
}


def _parse_product(item: dict, rank: int) -> dict | None:
    """Extract product info from a single RedSky API product dict."""
    item_data = item.get("item") or {}
    desc = item_data.get("product_description") or {}
    enrichment = item_data.get("enrichment") or {}
    images = enrichment.get("images") or {}

    name = (
        desc.get("title")
        or item_data.get("product_description", {}).get("title")
        or item.get("name")
        or item.get("title")
    )
    if not name:
        return None

    price_data = item.get("price") or {}
    price = None
    if isinstance(price_data, dict):
        price = price_data.get("current_retail") or price_data.get("reg_retail")
    try:
        price = float(price) if price is not None else None
    except (TypeError, ValueError):
        price = None

    image_url = (
        images.get("primary_image_url")
        or images.get("image_url")
        or item.get("image_url")
    )

    tcin = item.get("tcin") or item_data.get("tcin")
    product_url = f"https://www.target.com/p/-/A-{tcin}" if tcin else None

    rank_score = max(0.0, 100.0 - rank + 1)

    return {
        "name": name,
        "price": price,
        "image_url": image_url,
        "product_url": product_url,
        "rank_score": rank_score,
    }


def _extract_products_from_json(data: dict) -> list[dict]:
    """Walk known Target/RedSky API envelope shapes to find the product list."""
    # RedSky shape: data.search.products
    search = (data.get("data") or {}).get("search") or {}
    products = search.get("products")
    if isinstance(products, list) and products:
        return products

    # Alternative: data.products
    products = (data.get("data") or {}).get("products")
    if isinstance(products, list) and products:
        return products

    # Fallback: top-level products / items
    for key in ("products", "items", "results"):
        val = data.get(key)
        if isinstance(val, list) and val:
            return val

    return []


class TargetCollector(BaseCollector):
    source_name = "target"

    async def collect(self, keywords: list[str]) -> list[RawSignalData]:
        """Fetch trending products from Target via RedSky API interception."""
        signals = await self._try_playwright()
        if signals:
            return signals

        logger.warning("Target collector: Playwright approach failed, returning empty")
        return []

    async def _try_playwright(self) -> list[RawSignalData]:
        """Navigate Target category pages and capture RedSky API responses."""
        from playwright.async_api import async_playwright

        local_libs = Path(__file__).resolve().parents[3] / ".local-libs" / "lib"
        if local_libs.is_dir():
            existing = os.environ.get("LD_LIBRARY_PATH", "")
            if str(local_libs) not in existing:
                os.environ["LD_LIBRARY_PATH"] = (
                    f"{local_libs}:{existing}" if existing else str(local_libs)
                )

        all_items: list[tuple[str, dict]] = []  # (cat_name, item)

        async def _on_response(cat_name: str, response):
            url = response.url
            if "redsky.target.com" not in url and "api.target.com" not in url:
                return
            try:
                body = await response.json()
                items = _extract_products_from_json(body)
                if items:
                    for item in items:
                        all_items.append((cat_name, item))
                    logger.debug(
                        "Target Playwright intercepted %d products from %s",
                        len(items), url[:100],
                    )
            except Exception:
                pass

        try:
            async with async_playwright() as p:
                browser = await p.chromium.launch(headless=True)
                context = await browser.new_context(
                    user_agent=(
                        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) "
                        "Chrome/131.0.0.0 Safari/537.36"
                    ),
                    viewport={"width": 1280, "height": 720},
                    locale="en-US",
                )
                page = await context.new_page()

                for cat_name, base_url in TARGET_CATEGORIES.items():
                    url = f"{base_url}?sortBy=bestselling"
                    page.on("response", lambda r, c=cat_name: asyncio.ensure_future(_on_response(c, r)))
                    try:
                        await page.goto(url, wait_until="networkidle", timeout=35000)
                        for _ in range(5):
                            await asyncio.sleep(1)
                    except Exception as e:
                        logger.debug("Target Playwright page %s failed: %s", cat_name, e)
                    await asyncio.sleep(settings.target_rate_limit_secs)

                await browser.close()

        except Exception as e:
            logger.warning("Target Playwright scrape failed: %s", e)
            return []

        if not all_items:
            logger.warning("Target Playwright: no items captured")
            return []

        # Group by category and assign ranks
        cat_items: dict[str, list[dict]] = {}
        for cat_name, item in all_items:
            cat_items.setdefault(cat_name, []).append(item)

        signals: list[RawSignalData] = []
        for cat_name, items in cat_items.items():
            logger.info("Target Playwright %s: %d products", cat_name, len(items))
            for rank, item in enumerate(items, start=1):
                parsed = _parse_product(item, rank)
                if not parsed:
                    continue
                signals.append(RawSignalData(
                    source=self.source_name,
                    product_name=parsed["name"],
                    signal_type="target_trending",
                    value=parsed["rank_score"],
                    metadata={
                        "category": cat_name,
                        "price": parsed.get("price"),
                        "image_url": parsed.get("image_url"),
                        "product_url": parsed.get("product_url"),
                    },
                ))
                signals.append(RawSignalData(
                    source=self.source_name,
                    product_name=parsed["name"],
                    signal_type="mention",
                    value=1.0,
                    metadata={
                        "category": cat_name,
                        "price": parsed.get("price"),
                        "product_url": parsed.get("product_url"),
                    },
                ))

        logger.info(
            "Target Playwright: %d signals from %d categories",
            len(signals), len(cat_items),
        )
        return signals
