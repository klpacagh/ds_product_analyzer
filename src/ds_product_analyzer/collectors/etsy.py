"""Etsy trending products collector.

Uses Playwright to navigate Etsy's most-popular category pages and parse the
rendered HTML. Plain httpx is blocked by Etsy's bot detection (403), so a
real browser context is required.
"""

import asyncio
import logging
import os
import re
from pathlib import Path

import httpx
from bs4 import BeautifulSoup

from ds_product_analyzer.config import settings

from .base import BaseCollector, RawSignalData

logger = logging.getLogger(__name__)

ETSY_CATEGORIES = {
    "jewelry":        "jewelry",
    "clothing":       "clothing",
    "home-decor":     "home-and-living",
    "art":            "art-and-collectibles",
    "beauty":         "bath-and-beauty",
    "toys":           "toys-and-games",
    "bags":           "bags-and-purses",
    "craft-supplies": "craft-supplies-and-tools",
}

BASE_URL = "https://www.etsy.com/c/{slug}?sort_order=most_popular&ref=pagination"


def _parse_favorites(text: str) -> float | None:
    """Parse a favorites string like '1,203 favorites' into a float."""
    if not text:
        return None
    cleaned = re.sub(r"[^\d]", "", text.split()[0])
    try:
        return float(cleaned)
    except ValueError:
        return None


def _parse_price(text: str) -> float | None:
    """Parse a price string like '$29.99' into a float."""
    if not text:
        return None
    cleaned = re.sub(r"[^\d.]", "", text)
    try:
        return float(cleaned)
    except ValueError:
        return None


def _parse_products(html: str) -> list[dict]:
    """Extract product data from a rendered Etsy category page."""
    soup = BeautifulSoup(html, "html.parser")
    products = []

    cards = soup.select("[data-listing-id]")
    if not cards:
        cards = soup.select(".v2-listing-card")

    for card in cards:
        product = {}

        name_el = card.select_one(".v2-listing-card__info h3")
        if not name_el:
            name_el = card.select_one("h3")
        product["name"] = name_el.get_text(strip=True) if name_el else None

        price_el = card.select_one(".currency-value")
        product["price"] = _parse_price(price_el.get_text(strip=True) if price_el else None)

        fav_el = card.select_one(".social-count")
        product["favorites"] = _parse_favorites(fav_el.get_text(strip=True) if fav_el else None)

        img_el = card.select_one("img")
        product["image_url"] = img_el.get("src") if img_el else None

        link_el = card.select_one("a.listing-link") or card.select_one("a[href*='/listing/']")
        if link_el:
            href = link_el.get("href", "")
            product["product_url"] = href if href.startswith("http") else f"https://www.etsy.com{href}"
        else:
            product["product_url"] = None

        if product["name"]:
            products.append(product)

    return products


class EtsyCollector(BaseCollector):
    source_name = "etsy"

    def __init__(self):
        self._rate_limit = settings.etsy_rate_limit_secs

    async def collect(self, keywords: list[str]) -> list[RawSignalData]:
        signals = await self._try_api()
        if signals:
            return signals
        return await self._try_playwright()

    # ------------------------------------------------------------------
    # Strategy 1: Etsy Open API (requires ETSY_API_KEY in .env)
    # ------------------------------------------------------------------
    async def _try_api(self) -> list[RawSignalData]:
        """Fetch trending listings via the Etsy Open API v3.

        Requires ETSY_API_KEY (the keystring from developer.etsy.com) to be
        set in .env.  Falls back to Playwright scraping when the key is absent
        or the call fails.
        """
        if not settings.etsy_api_key:
            return []

        # Etsy v3 requires "keystring:shared_secret" in x-api-key
        api_key_header = (
            f"{settings.etsy_api_key}:{settings.etsy_shared_secret}"
            if settings.etsy_shared_secret
            else settings.etsy_api_key
        )

        signals: list[RawSignalData] = []
        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                resp = await client.get(
                    "https://openapi.etsy.com/v3/application/listings/active",
                    params={
                        "limit": 100,
                        "sort_on": "score",
                        "sort_order": "desc",
                        "includes": "Images",
                    },
                    headers={"x-api-key": api_key_header},
                )

            if resp.status_code != 200:
                logger.warning(
                    "Etsy API returned %d: %s", resp.status_code, resp.text[:200]
                )
                return []

            listings = resp.json().get("results", [])
            logger.info("Etsy API: %d listings returned", len(listings))

            for listing in listings:
                name = (listing.get("title") or "").strip()
                if not name:
                    continue

                favorites = float(listing.get("num_favorers") or 0)

                price: float | None = None
                price_info = listing.get("price") or {}
                try:
                    price = price_info["amount"] / price_info["divisor"]
                except (KeyError, TypeError, ZeroDivisionError):
                    pass

                url = listing.get("url") or ""

                image_url: str | None = None
                images = listing.get("images") or []
                if images:
                    img = images[0]
                    image_url = (
                        img.get("url_570xN")
                        or img.get("url_fullxfull")
                        or img.get("url_75x75")
                    )

                taxonomy_path = listing.get("taxonomy_path") or []
                category = taxonomy_path[0] if taxonomy_path else None

                signals.append(RawSignalData(
                    source=self.source_name,
                    product_name=name,
                    signal_type="etsy_trending",
                    value=favorites if favorites > 0 else 1.0,
                    metadata={
                        "category": category,
                        "price": price,
                        "image_url": image_url,
                        "product_url": url,
                    },
                ))
                signals.append(RawSignalData(
                    source=self.source_name,
                    product_name=name,
                    signal_type="mention",
                    value=1.0,
                    metadata={"category": category, "product_url": url},
                ))

        except Exception as e:
            logger.warning("Etsy API request failed: %s", e)
            return []

        logger.info("Etsy API: %d signals from %d listings", len(signals), len(signals) // 2)
        return signals

    async def _try_playwright(self) -> list[RawSignalData]:
        """Navigate Etsy category pages via Playwright and parse rendered HTML."""
        from playwright.async_api import async_playwright

        local_libs = Path(__file__).resolve().parents[3] / ".local-libs" / "lib"
        if local_libs.is_dir():
            existing = os.environ.get("LD_LIBRARY_PATH", "")
            if str(local_libs) not in existing:
                os.environ["LD_LIBRARY_PATH"] = (
                    f"{local_libs}:{existing}" if existing else str(local_libs)
                )

        signals: list[RawSignalData] = []

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

                for i, (cat_name, slug) in enumerate(ETSY_CATEGORIES.items()):
                    if i > 0:
                        await asyncio.sleep(self._rate_limit)

                    url = BASE_URL.format(slug=slug)
                    try:
                        await page.goto(url, wait_until="networkidle", timeout=30000)
                        html = await page.content()
                    except Exception as e:
                        logger.warning("Etsy Playwright error for %s: %s", cat_name, e)
                        continue

                    if len(html) < 1000:
                        logger.warning("Etsy empty page for %s", cat_name)
                        continue

                    products = _parse_products(html)
                    logger.info("Etsy %s: found %d products", cat_name, len(products))

                    for product in products:
                        name = product["name"]
                        favorites = product.get("favorites")
                        value = favorites if favorites is not None else 1.0

                        signals.append(RawSignalData(
                            source=self.source_name,
                            product_name=name,
                            signal_type="etsy_trending",
                            value=value,
                            metadata={
                                "category": cat_name,
                                "price": product.get("price"),
                                "image_url": product.get("image_url"),
                                "product_url": product.get("product_url"),
                            },
                        ))
                        signals.append(RawSignalData(
                            source=self.source_name,
                            product_name=name,
                            signal_type="mention",
                            value=1.0,
                            metadata={
                                "category": cat_name,
                                "price": product.get("price"),
                                "product_url": product.get("product_url"),
                            },
                        ))

                await browser.close()

        except Exception as e:
            logger.warning("Etsy Playwright scrape failed: %s", e)
            return []

        logger.info("Etsy Playwright: %d signals from %d categories", len(signals), len(ETSY_CATEGORIES))
        return signals
