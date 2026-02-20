"""Walmart bestseller products collector.

Two-stage strategy:

1. **Direct API (httpx)** — tries Walmart's internal search API with
   ``sort=best_seller``. Returns structured JSON if not blocked.

2. **Playwright fallback** — navigates to bestseller category pages and
   intercepts XHR/JSON responses containing product lists.

Returns empty list gracefully if all approaches fail.
"""

import asyncio
import logging
import os
from pathlib import Path

import httpx

from ds_product_analyzer.config import settings

from .base import BaseCollector, RawSignalData

logger = logging.getLogger(__name__)

WALMART_CATEGORIES = {
    "electronics": "https://www.walmart.com/cp/electronics/3944",
    "home":        "https://www.walmart.com/cp/home/4044",
    "clothing":    "https://www.walmart.com/cp/clothing/5438",
    "beauty":      "https://www.walmart.com/cp/beauty/1085666",
    "sports":      "https://www.walmart.com/cp/sports-outdoors/4125",
    "toys":        "https://www.walmart.com/cp/toys/4171",
}

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/131.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://www.walmart.com/",
}


def _parse_item(item: dict, rank: int) -> dict | None:
    """Extract product info from a single Walmart API item dict."""
    name = (
        item.get("name")
        or item.get("title")
        or (item.get("item") or {}).get("name")
        or (item.get("item") or {}).get("title")
    )
    if not name:
        return None

    price_data = item.get("price") or item.get("priceInfo") or {}
    price = None
    if isinstance(price_data, dict):
        price = (
            price_data.get("current", {}).get("price")
            or price_data.get("currentPrice")
            or price_data.get("price")
        )
    elif isinstance(price_data, (int, float)):
        price = float(price_data)
    try:
        price = float(price) if price is not None else None
    except (TypeError, ValueError):
        price = None

    image_url = item.get("image") or item.get("imageInfo", {}).get("thumbnailUrl")
    canonical_url = item.get("canonicalUrl") or item.get("productPageUrl")
    if canonical_url and not canonical_url.startswith("http"):
        canonical_url = f"https://www.walmart.com{canonical_url}"

    rank_score = max(0.0, 100.0 - rank + 1)

    return {
        "name": name,
        "price": price,
        "image_url": image_url,
        "product_url": canonical_url,
        "rank_score": rank_score,
    }


def _extract_items_from_json(data: dict) -> list[dict]:
    """Walk known Walmart API envelope shapes to find the product list."""
    # Shape 1: search API envelope
    items = (data.get("query") or {}).get("searchResult", {}).get("itemStacks", [])
    if items:
        for stack in items:
            products = stack.get("items", [])
            if products:
                return products

    # Shape 2: direct items array
    for key in ("items", "products", "results", "data"):
        val = data.get(key)
        if isinstance(val, list) and val:
            return val
        if isinstance(val, dict):
            for inner in ("items", "products", "results"):
                inner_val = val.get(inner)
                if isinstance(inner_val, list) and inner_val:
                    return inner_val

    return []


class WalmartCollector(BaseCollector):
    source_name = "walmart"

    async def collect(self, keywords: list[str]) -> list[RawSignalData]:
        """Fetch bestselling products from Walmart."""
        signals = await self._try_api()
        if signals:
            return signals

        signals = await self._try_playwright()
        if signals:
            return signals

        logger.warning("Walmart collector: all approaches failed, returning empty")
        return []

    # ------------------------------------------------------------------
    # Strategy 1: Direct search API via httpx
    # ------------------------------------------------------------------
    async def _try_api(self) -> list[RawSignalData]:
        """Attempt to fetch bestsellers via Walmart's search API."""
        signals: list[RawSignalData] = []

        async with httpx.AsyncClient(headers=HEADERS, timeout=20.0) as client:
            for cat_name in WALMART_CATEGORIES:
                url = (
                    f"https://www.walmart.com/search"
                    f"?q={cat_name}&sort=best_seller&affinityOverride=default"
                )
                try:
                    resp = await client.get(url, follow_redirects=True)
                except httpx.HTTPError as e:
                    logger.debug("Walmart API HTTP error for %s: %s", cat_name, e)
                    continue

                if resp.status_code != 200:
                    logger.debug("Walmart API %s returned %d", cat_name, resp.status_code)
                    continue

                content_type = resp.headers.get("content-type", "")
                if "json" not in content_type:
                    logger.debug("Walmart API %s returned non-JSON", cat_name)
                    continue

                try:
                    data = resp.json()
                except Exception:
                    continue

                items = _extract_items_from_json(data)
                if not items:
                    logger.debug("Walmart API %s: no items in response", cat_name)
                    continue

                logger.info("Walmart API %s: found %d items", cat_name, len(items))
                for rank, item in enumerate(items, start=1):
                    parsed = _parse_item(item, rank)
                    if not parsed:
                        continue
                    signals.extend(_make_signals(parsed, cat_name))

                await asyncio.sleep(settings.walmart_rate_limit_secs)

        return signals

    # ------------------------------------------------------------------
    # Strategy 2: Playwright page navigation + response interception
    # ------------------------------------------------------------------
    async def _try_playwright(self) -> list[RawSignalData]:
        """Navigate to Walmart bestseller pages and intercept JSON API calls."""
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
            if not (
                "best_seller" in url
                or "bestSeller" in url
                or "pagination/api" in url
                or ("search" in url and "walmart.com" in url)
            ):
                return
            try:
                body = await response.json()
                items = _extract_items_from_json(body)
                if items:
                    for item in items:
                        all_items.append((cat_name, item))
                    logger.debug(
                        "Walmart Playwright intercepted %d items from %s",
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

                for cat_name, base_url in WALMART_CATEGORIES.items():
                    url = f"{base_url}?sort=best_seller"
                    page.on("response", lambda r, c=cat_name: asyncio.ensure_future(_on_response(c, r)))
                    try:
                        await page.goto(url, wait_until="networkidle", timeout=30000)
                        for _ in range(5):
                            await asyncio.sleep(1)
                    except Exception as e:
                        logger.debug("Walmart Playwright page %s failed: %s", cat_name, e)

                await browser.close()

        except Exception as e:
            logger.warning("Walmart Playwright scrape failed: %s", e)
            return []

        if not all_items:
            logger.warning("Walmart Playwright: no items captured")
            return []

        # Group by category and assign ranks
        cat_items: dict[str, list[dict]] = {}
        for cat_name, item in all_items:
            cat_items.setdefault(cat_name, []).append(item)

        signals: list[RawSignalData] = []
        for cat_name, items in cat_items.items():
            logger.info("Walmart Playwright %s: %d items", cat_name, len(items))
            for rank, item in enumerate(items, start=1):
                parsed = _parse_item(item, rank)
                if not parsed:
                    continue
                signals.extend(_make_signals(parsed, cat_name))

        return signals


def _make_signals(parsed: dict, cat_name: str) -> list[RawSignalData]:
    return [
        RawSignalData(
            source="walmart",
            product_name=parsed["name"],
            signal_type="walmart_bestseller",
            value=parsed["rank_score"],
            metadata={
                "category": cat_name,
                "price": parsed.get("price"),
                "image_url": parsed.get("image_url"),
                "product_url": parsed.get("product_url"),
            },
        ),
        RawSignalData(
            source="walmart",
            product_name=parsed["name"],
            signal_type="mention",
            value=1.0,
            metadata={
                "category": cat_name,
                "price": parsed.get("price"),
                "product_url": parsed.get("product_url"),
            },
        ),
    ]
