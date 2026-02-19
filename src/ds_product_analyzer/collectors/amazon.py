"""Amazon Movers & Shakers collector.

Scrapes Amazon's Movers & Shakers pages to detect BSR momentum signals.
Uses httpx + BeautifulSoup with proven selectors from feasibility probes.
"""

import asyncio
import logging
import re
import time

import httpx
from bs4 import BeautifulSoup

from ds_product_analyzer.config import settings

from .base import BaseCollector, RawSignalData

logger = logging.getLogger(__name__)

# Map our 6 categories to Amazon M&S URL slugs
CATEGORY_SLUGS = {
    "electronics": "electronics",
    "home-kitchen": "home-garden",
    "beauty": "beauty",
    "sports": "sports-outdoors",
    "pets": "pet-supplies",
    "toys": "toys-and-games",
}

BASE_URL = "https://www.amazon.com/gp/movers-and-shakers/{slug}/"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/131.0.0.0 Safari/537.36"
    ),
    "Accept": (
        "text/html,application/xhtml+xml,application/xml;q=0.9,"
        "image/avif,image/webp,image/apng,*/*;q=0.8"
    ),
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip, deflate, br",
    "Referer": "https://www.amazon.com/",
    "DNT": "1",
    "Connection": "keep-alive",
    "Upgrade-Insecure-Requests": "1",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "same-origin",
    "Sec-Fetch-User": "?1",
    "Cache-Control": "max-age=0",
}


def _parse_percent_change(text: str) -> float | None:
    """Parse a percent change string like '1,200%' into a float."""
    if not text:
        return None
    cleaned = re.sub(r"[^\d.]", "", text.replace(",", ""))
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
    """Extract product data from a Movers & Shakers page."""
    soup = BeautifulSoup(html, "html.parser")
    products = []

    # Amazon uses several possible selectors for M&S product cards
    cards = soup.select("#gridItemRoot")
    if not cards:
        cards = soup.select("[id^='gridItemRoot']")
    if not cards:
        cards = soup.select(".a-list-item .zg-grid-general-faceout")

    for card in cards:
        product = {}

        # Product name
        name_el = card.select_one(
            "._cDEzb_p13n-sc-css-line-clamp-1_1Fn1y"
        ) or card.select_one(".p13n-sc-truncate")
        if not name_el:
            name_el = card.select_one("a[href] span div")
        product["name"] = name_el.get_text(strip=True) if name_el else None

        # Rank change percentage
        change_el = card.select_one(".zg-percent-change span") or card.select_one(
            "[class*='percent']"
        )
        raw_change = change_el.get_text(strip=True) if change_el else None
        product["percent_change"] = _parse_percent_change(raw_change)

        # Price
        price_el = card.select_one(".p13n-sc-price") or card.select_one(
            "span.a-price .a-offscreen"
        )
        product["price"] = _parse_price(
            price_el.get_text(strip=True) if price_el else None
        )

        # Rating
        rating_el = card.select_one("span.a-icon-alt")
        product["rating"] = rating_el.get_text(strip=True) if rating_el else None

        # Image
        img_el = card.select_one("img")
        product["image_url"] = img_el.get("src") if img_el else None

        # Product URL
        link_el = card.select_one("a.a-link-normal[href*='/dp/']") or card.select_one("a[href*='/dp/']")
        href = link_el.get("href", "") if link_el else ""
        product["product_url"] = f"https://www.amazon.com{href}" if href.startswith("/") else href or None

        if product["name"]:
            products.append(product)

    return products


class AmazonMoversCollector(BaseCollector):
    source_name = "amazon"

    def __init__(self):
        self._rate_limit = settings.amazon_rate_limit_secs

    async def collect(self, keywords: list[str]) -> list[RawSignalData]:
        """Scrape Amazon M&S pages for all mapped categories."""
        return await asyncio.to_thread(self._collect_sync, keywords)

    def _collect_sync(self, keywords: list[str]) -> list[RawSignalData]:
        signals: list[RawSignalData] = []
        keyword_set = {kw.lower() for kw in keywords}

        with httpx.Client(headers=HEADERS, timeout=30.0) as client:
            for i, (cat_name, slug) in enumerate(CATEGORY_SLUGS.items()):
                if i > 0:
                    time.sleep(self._rate_limit)

                url = BASE_URL.format(slug=slug)
                try:
                    resp = client.get(url, follow_redirects=True)
                except httpx.HTTPError as e:
                    logger.warning("Amazon HTTP error for %s: %s", cat_name, e)
                    continue

                if resp.status_code != 200:
                    logger.warning("Amazon returned %d for %s", resp.status_code, cat_name)
                    continue

                # CAPTCHA detection
                if "captcha" in resp.text.lower() or len(resp.text) < 1000:
                    logger.warning("Amazon CAPTCHA or empty response for %s", cat_name)
                    continue

                products = _parse_products(resp.text)
                logger.info("Amazon M&S %s: found %d products", cat_name, len(products))

                for product in products:
                    name = product["name"]
                    name_lower = name.lower()

                    # BSR momentum signal (percent change)
                    pct = product.get("percent_change")
                    if pct is not None and pct > 0:
                        signals.append(RawSignalData(
                            source=self.source_name,
                            product_name=name,
                            signal_type="bsr_momentum",
                            value=pct,
                            metadata={
                                "category": cat_name,
                                "price": product.get("price"),
                                "image_url": product.get("image_url"),
                                "rating": product.get("rating"),
                                "product_url": product.get("product_url"),
                            },
                        ))

                    # Mention signal for cross-platform counting
                    signals.append(RawSignalData(
                        source=self.source_name,
                        product_name=name,
                        signal_type="mention",
                        value=1.0,
                        metadata={
                            "category": cat_name,
                            "price": product.get("price"),
                            "image_url": product.get("image_url"),
                            "product_url": product.get("product_url"),
                        },
                    ))

        return signals
