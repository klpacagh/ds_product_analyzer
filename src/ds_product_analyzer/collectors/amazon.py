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

# Map our categories to Amazon M&S URL slugs
CATEGORY_SLUGS = {
    "electronics":  "electronics",
    "home-kitchen": "home-garden",
    "beauty":       "beauty",
    "sports":       "sports-outdoors",
    "pets":         "pet-supplies",
    "toys":         "toys-and-games",
    "office":       "office-products",
    "health":       "health-personal-care",
    "clothing":     "clothing-accessories-jewels-watches",
    "tools":        "tools",
    "automotive":   "automotive-parts-accessories",
    "video-games":  "videogames",
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


def _extract_asin(url: str) -> str | None:
    """Extract ASIN from an Amazon product URL."""
    if not url:
        return None
    m = re.search(r"/dp/([A-Z0-9]{10})", url)
    return m.group(1) if m else None


def _parse_bsr_rank(text: str) -> int | None:
    """Parse a BSR badge like '#1,204' into an integer."""
    if not text:
        return None
    cleaned = re.sub(r"[^\d]", "", text)
    try:
        return int(cleaned) if cleaned else None
    except ValueError:
        return None


def _parse_review_count(text: str) -> int | None:
    """Parse a review count string like '4,821' or '4.8K' into an integer."""
    if not text:
        return None
    text = text.strip()
    # Handle "4.8K" style
    k_match = re.match(r"([\d.]+)[Kk]", text)
    if k_match:
        try:
            return int(float(k_match.group(1)) * 1000)
        except ValueError:
            return None
    cleaned = re.sub(r"[^\d]", "", text)
    try:
        return int(cleaned) if cleaned else None
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
        change_el = (
            card.select_one(".zg-grid-pct-change")
            or card.select_one(".zg-percent-change span")
            or card.select_one("[class*='percent']")
        )
        raw_change = change_el.get_text(strip=True) if change_el else None
        product["percent_change"] = _parse_percent_change(raw_change)

        # Price — cascade from exact legacy class to attribute-contains fallbacks
        CARD_PRICE_SELECTORS = [
            "span.a-price .a-offscreen",         # most reliable modern hidden price
            ".p13n-sc-price",
            "[class*='p13n-sc-price']",
            "[class*='sc-price']",
            ".a-color-price",
        ]
        price_el = None
        for sel in CARD_PRICE_SELECTORS:
            price_el = card.select_one(sel)
            if price_el:
                break
        product["price"] = _parse_price(price_el.get_text(strip=True) if price_el else None)

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

        # ASIN — always extractable from the product URL
        product["asin"] = _extract_asin(product.get("product_url"))

        # BSR rank — try multiple badge selectors
        bsr_el = (
            card.select_one(".zg-bdg-text")
            or card.select_one(".p13n-sc-badge-label")
            or card.select_one("[class*='zg-badge'] span")
            or card.select_one("[class*='zg-bdg'] span")
        )
        product["bsr_rank"] = _parse_bsr_rank(bsr_el.get_text(strip=True) if bsr_el else None)

        # Review count — the count lives in the ratings link inside .a-icon-row
        review_count = None
        rev_el = (
            card.select_one(".a-icon-row a[href*='reviews'] .a-size-small")
            or card.select_one(".a-icon-row .a-size-small")
            or card.select_one("a[href*='customerReviews'] span")
        )
        if rev_el:
            txt = rev_el.get_text(strip=True)
            if re.search(r"\d{2,}", txt):
                review_count = _parse_review_count(txt)
        product["review_count"] = review_count

        if product["name"]:
            products.append(product)

    return products


_CAPTCHA_MARKERS = (
    "/errors/validateCaptcha",
    "Type the characters you see in this image",
    "Enter the characters you see below",
    "Sorry, we just need to make sure you're not a robot",
)


def fetch_product_details(url: str) -> dict:
    """Fetch price and BSR from an Amazon product detail page.

    Returns a dict with keys: price (float|None), bsr_rank (int|None), bsr_category (str|None).
    """
    # Strip M&S referral params — clean URL reduces bot-detection risk
    asin = _extract_asin(url)
    if asin:
        url = f"https://www.amazon.com/dp/{asin}"

    PRICE_SELECTORS = [
        "#corePriceDisplay_desktop_feature_div .a-price .a-offscreen",
        "#apex_offerDisplay_desktop .a-price .a-offscreen",      # modern layout
        "#priceblock_ourprice",
        "#priceblock_dealprice",
        ".a-price .a-offscreen",                                  # broadened fallback
    ]
    result: dict = {"price": None, "bsr_rank": None, "bsr_category": None}

    with httpx.Client(headers=HEADERS, timeout=20.0) as client:
        resp = client.get(url, follow_redirects=True)
    is_captcha = any(marker in resp.text for marker in _CAPTCHA_MARKERS)
    if resp.status_code != 200 or is_captcha:
        logger.warning("fetch_product_details: CAPTCHA or non-200 (%d) for %s", resp.status_code, url)
        return result

    soup = BeautifulSoup(resp.text, "html.parser")

    # Price
    for sel in PRICE_SELECTORS:
        el = soup.select_one(sel)
        if el:
            result["price"] = _parse_price(el.get_text(strip=True))
            break

    # Fallback: split price display (.a-price-whole + .a-price-fraction)
    if result["price"] is None:
        whole = soup.select_one(".a-price-whole")
        frac  = soup.select_one(".a-price-fraction")
        if whole:
            raw = whole.get_text(strip=True).rstrip(".") + "." + (frac.get_text(strip=True) if frac else "00")
            result["price"] = _parse_price(raw)

    # Fallback: JSON-LD structured data
    if result["price"] is None:
        import json
        for script in soup.select('script[type="application/ld+json"]'):
            try:
                data = json.loads(script.string or "")
                offers = data.get("offers") or {}
                if isinstance(offers, list):
                    offers = offers[0] if offers else {}
                price_str = offers.get("price") or offers.get("lowPrice")
                if price_str:
                    result["price"] = _parse_price(str(price_str))
                    break
            except (json.JSONDecodeError, AttributeError):
                continue

    # BSR — Layout A: #detailBulletsWrapper_feature_div
    bsr_text: str | None = None
    bullets_div = soup.select_one("#detailBulletsWrapper_feature_div")
    if bullets_div:
        for bold in bullets_div.select("span.a-text-bold"):
            if "Best Sellers Rank" in bold.get_text():
                parent = bold.parent
                if parent:
                    full_text = parent.get_text(" ", strip=True)
                    if re.search(r"#[\d,]+", full_text):
                        bsr_text = full_text
                        break

    # BSR — Layout B: productDetails table
    if not bsr_text:
        for th in soup.select("th"):
            if "Best Sellers Rank" in th.get_text():
                td = th.find_next_sibling("td")
                if td:
                    bsr_text = td.get_text(" ", strip=True)
                    break

    if bsr_text:
        # Extract first rank number e.g. "#1,234" → 1234
        rank_match = re.search(r"#([\d,]+)", bsr_text)
        if rank_match:
            try:
                result["bsr_rank"] = int(rank_match.group(1).replace(",", ""))
            except ValueError:
                pass
        # Extract category name — stop before parenthetical links like "(See Top 100...)"
        cat_match = re.search(r"#[\d,]+\s+in\s+([^(#\n]+?)(?:\s*[\(#\n]|$)", bsr_text)
        if cat_match:
            result["bsr_category"] = cat_match.group(1).strip()

    if result["bsr_rank"]:
        logger.debug("BSR found for %s: #%d in %s", url, result["bsr_rank"], result["bsr_category"])
    else:
        logger.debug("BSR not found for %s", url)

    return result


def fetch_product_price(url: str) -> float | None:
    """Fetch current price from an Amazon product detail page (thin wrapper)."""
    return fetch_product_details(url)["price"]


class AmazonMoversCollector(BaseCollector):
    source_name = "amazon"

    def __init__(self):
        self._rate_limit = settings.amazon_rate_limit_secs
        self._pages_per_category: int = settings.amazon_pages_per_category

    async def collect(self, keywords: list[str]) -> list[RawSignalData]:
        """Scrape Amazon M&S pages for all mapped categories."""
        return await asyncio.to_thread(self._collect_sync, keywords)

    def _collect_sync(self, keywords: list[str]) -> list[RawSignalData]:
        signals: list[RawSignalData] = []
        keyword_set = {kw.lower() for kw in keywords}

        with httpx.Client(headers=HEADERS, timeout=30.0) as client:
            for i, (cat_name, slug) in enumerate(CATEGORY_SLUGS.items()):
                base_url = BASE_URL.format(slug=slug)
                for page in range(1, self._pages_per_category + 1):
                    if page > 1 or i > 0:
                        time.sleep(self._rate_limit)

                    page_url = f"{base_url}?pg={page}"
                    try:
                        resp = client.get(page_url, follow_redirects=True)
                    except httpx.HTTPError as e:
                        logger.warning("Amazon HTTP error for %s page %d: %s", cat_name, page, e)
                        break

                    if resp.status_code != 200:
                        logger.warning("Amazon returned %d for %s page %d", resp.status_code, cat_name, page)
                        break

                    # CAPTCHA detection
                    is_captcha_page = any(m in resp.text for m in (
                        "/errors/validateCaptcha",
                        "Type the characters you see in this image",
                        "Enter the characters you see below",
                    ))
                    if is_captcha_page or len(resp.text) < 1000:
                        logger.warning("Amazon CAPTCHA or empty response for %s page %d", cat_name, page)
                        break

                    products = _parse_products(resp.text)
                    logger.info("Amazon M&S %s page %d: found %d products", cat_name, page, len(products))

                    if not products:
                        break

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
                                    "asin": product.get("asin"),
                                    "bsr_rank": product.get("bsr_rank"),
                                    "review_count": product.get("review_count"),
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
                                "asin": product.get("asin"),
                                "bsr_rank": product.get("bsr_rank"),
                                "review_count": product.get("review_count"),
                            },
                        ))

        return signals
