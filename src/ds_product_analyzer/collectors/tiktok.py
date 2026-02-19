"""TikTok trending products collector.

Two-stage strategy:

1. **Creative Center API** — tries the ``/creative_radar_api/v1/product/list``
   endpoint directly.  This requires auth headers computed client-side, so it
   almost always returns ``code 50004`` (no data) without a logged-in session.

2. **Hashtag scraping (Playwright)** — loads TikTok hashtag pages for
   product-related tags (#tiktokmademebuyit, #amazonfinds, …) and intercepts
   the ``challenge/item_list`` API responses that contain video metadata.
   Product names are extracted from video descriptions and engagement stats
   serve as popularity signals.

Returns empty list gracefully if all approaches fail.
"""

import asyncio
import logging
import os
import re
from pathlib import Path

import httpx

from .base import BaseCollector, RawSignalData

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Creative Center direct API (rarely works without auth)
# ---------------------------------------------------------------------------
_CREATIVE_CENTER_PAGE = (
    "https://ads.tiktok.com/business/creativecenter/top-products/pc/en"
)

_API_ENDPOINTS = [
    {
        "method": "GET",
        "url": "https://ads.tiktok.com/creative_radar_api/v1/product/list",
        "params": {
            "page": 1, "limit": 50, "country_code": "US",
            "period_type": "last", "last": 7,
            "order_by": "post", "order_type": "desc",
        },
    },
    {
        "method": "GET",
        "url": "https://ads.tiktok.com/creative_radar_api/v1/product/list",
        "params": {
            "page": 1, "limit": 50, "country_code": "GB",
            "period_type": "last", "last": 7,
            "order_by": "post", "order_type": "desc",
        },
    },
]

_ENVELOPE_PATHS = [
    ("data", "list"),
    ("data", "products"),
    ("data", "items"),
    ("data", "product_list"),
    ("data", "rank_list"),
    ("products",),
    ("items",),
    ("list",),
]

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/131.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": _CREATIVE_CENTER_PAGE,
    "Origin": "https://ads.tiktok.com",
}

# ---------------------------------------------------------------------------
# Hashtag scraping config
# ---------------------------------------------------------------------------
_PRODUCT_HASHTAGS = [
    "tiktokmademebuyit",
    "amazonfinds",
    "tiktokshop",
    "viralproducts",
]

_HASHTAG_RE = re.compile(r"#\S+")
_MENTION_RE = re.compile(r"@\S+")
_EMOJI_RE = re.compile(
    r"[\U0001f600-\U0001f64f\U0001f300-\U0001f5ff\U0001f680-\U0001f6ff"
    r"\U0001f1e0-\U0001f1ff\U00002702-\U000027b0\U0001f900-\U0001f9ff"
    r"\U0001fa00-\U0001fa6f\U0001fa70-\U0001faff\U00002600-\U000026ff"
    r"\U0000fe0f\U0000200d]+",
)
# Filler phrases that don't describe a product
_FILLER_RE = re.compile(
    r"(?i)^(reply to|link in bio|shop at the link|everything is linked|"
    r"follow for more|check out|comment below|POV:|you guys)",
)


def _extract_product_name(desc: str) -> str | None:
    """Best-effort extraction of a product name from a TikTok video description.

    Strips hashtags, @-mentions, emojis, and common filler phrases, then
    returns the cleaned text (capped at 120 chars) or None if nothing useful
    remains.
    """
    text = _HASHTAG_RE.sub("", desc)
    text = _MENTION_RE.sub("", text)
    text = _EMOJI_RE.sub("", text)
    # Collapse whitespace
    text = " ".join(text.split()).strip()

    # Drop leading filler
    text = _FILLER_RE.sub("", text).strip()
    # Strip leading/trailing punctuation
    text = text.strip(".,;:!?…*•|/\\-–— \"'")

    if len(text) < 5:
        return None
    return text[:120]


def _extract_items(data: dict) -> list[dict]:
    """Try all known envelope shapes to find the product list."""
    for path in _ENVELOPE_PATHS:
        node = data
        for key in path:
            if isinstance(node, dict) and key in node:
                node = node[key]
            else:
                node = None
                break
        if isinstance(node, list) and len(node) > 0:
            return node

    raw = data.get("data")
    if isinstance(raw, list) and len(raw) > 0:
        return raw

    return []


def _parse_item(item: dict) -> dict | None:
    """Extract product info from a single Creative Center API item."""
    product_info = item.get("product_info") or item.get("product") or {}

    name = (
        item.get("title")
        or item.get("product_name")
        or item.get("name")
        or product_info.get("title")
        or product_info.get("name")
        or product_info.get("product_name")
    )
    if not name:
        return None

    popularity = (
        item.get("order_count")
        or item.get("sales_volume")
        or item.get("popularity_score")
        or item.get("click_count")
        or item.get("sold_count")
        or item.get("sales_count")
        or item.get("view_count")
        or (item.get("statistics", {}) or {}).get("sold_count")
        or (item.get("statistics", {}) or {}).get("view_count")
        or product_info.get("order_count")
        or product_info.get("sales_volume")
        or 0
    )
    try:
        popularity = float(popularity)
    except (TypeError, ValueError):
        popularity = 0.0

    image_url = (
        item.get("product_image")
        or item.get("cover_url")
        or item.get("cover")
        or item.get("image_url")
        or item.get("thumbnail")
        or product_info.get("product_image")
        or product_info.get("cover_url")
    )
    if isinstance(image_url, list):
        image_url = image_url[0] if image_url else None
    if isinstance(image_url, dict):
        image_url = image_url.get("url") or image_url.get("url_list", [None])[0]

    price = item.get("price") or item.get("min_price") or product_info.get("price")
    if isinstance(price, dict):
        price = price.get("price") or price.get("min_price")
    try:
        price_val = float(str(price).replace(",", "")) if price else None
        if price_val is not None and price_val > 100:
            price_val = price_val / 100
        price = price_val
    except (TypeError, ValueError):
        price = None

    url = (
        item.get("detail_url")
        or item.get("product_detail_url")
        or item.get("url")
        or item.get("product_url")
        or item.get("link")
        or product_info.get("detail_url")
    )

    return {
        "name": name,
        "popularity": popularity,
        "image_url": image_url,
        "price": price,
        "url": url,
    }


# ---------------------------------------------------------------------------
# Collector
# ---------------------------------------------------------------------------
class TikTokCollector(BaseCollector):
    source_name = "tiktok"

    async def collect(self, keywords: list[str]) -> list[RawSignalData]:
        """Fetch trending products from TikTok.

        Tries the Creative Center API first, then falls back to scraping
        product-related hashtag pages via Playwright.
        """
        signals = await self._try_api()
        if signals:
            return signals

        signals = await self._try_hashtag_scrape()
        if signals:
            return signals

        logger.warning("TikTok collector: all approaches failed, returning empty")
        return []

    # ------------------------------------------------------------------
    # Strategy 1: Creative Center API (needs auth — usually fails)
    # ------------------------------------------------------------------
    async def _try_api(self) -> list[RawSignalData]:
        """Try Creative Center API endpoints."""
        async with httpx.AsyncClient(headers=HEADERS, timeout=15.0) as client:
            for endpoint in _API_ENDPOINTS:
                try:
                    resp = await client.get(
                        endpoint["url"], params=endpoint.get("params"),
                    )
                    if resp.status_code != 200:
                        logger.debug(
                            "TikTok API %s returned status %d",
                            endpoint["url"], resp.status_code,
                        )
                        continue

                    data = resp.json()
                    items = _extract_items(data)
                    if not items:
                        logger.debug(
                            "TikTok API returned 200 but no items (code=%s)",
                            data.get("code"),
                        )
                        continue

                    signals = []
                    for item in items:
                        parsed = _parse_item(item)
                        if not parsed:
                            continue
                        signals.append(RawSignalData(
                            source=self.source_name,
                            product_name=parsed["name"],
                            signal_type="tiktok_popularity",
                            value=parsed["popularity"],
                            metadata={
                                "image_url": parsed.get("image_url"),
                                "price": parsed.get("price"),
                                "product_url": parsed.get("url"),
                            },
                        ))
                        signals.append(RawSignalData(
                            source=self.source_name,
                            product_name=parsed["name"],
                            signal_type="mention",
                            value=1.0,
                        ))

                    if signals:
                        logger.info(
                            "TikTok API success: %d signals from %s",
                            len(signals), endpoint["url"],
                        )
                        return signals

                except Exception as e:
                    logger.debug("TikTok API endpoint failed: %s", e)
                    continue

        return []

    # ------------------------------------------------------------------
    # Strategy 2: Hashtag page scraping via Playwright
    # ------------------------------------------------------------------
    async def _try_hashtag_scrape(self) -> list[RawSignalData]:
        """Load TikTok hashtag pages and intercept video-list API responses."""
        from playwright.async_api import async_playwright

        # Ensure bundled browser libs are on LD_LIBRARY_PATH (WSL2 compat).
        local_libs = Path(__file__).resolve().parents[3] / ".local-libs" / "lib"
        if local_libs.is_dir():
            existing = os.environ.get("LD_LIBRARY_PATH", "")
            if str(local_libs) not in existing:
                os.environ["LD_LIBRARY_PATH"] = (
                    f"{local_libs}:{existing}" if existing else str(local_libs)
                )

        all_videos: list[dict] = []

        async def _on_response(response):
            if "challenge/item_list" not in response.url:
                return
            try:
                body = await response.json()
                items = body.get("itemList", [])
                if items:
                    all_videos.extend(items)
                    logger.debug(
                        "TikTok intercepted %d videos from %s",
                        len(items), response.url[:100],
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
                page.on("response", _on_response)

                for tag in _PRODUCT_HASHTAGS:
                    url = f"https://www.tiktok.com/tag/{tag}"
                    try:
                        await page.goto(url, wait_until="networkidle", timeout=25000)
                        # Wait for video list API call
                        for _ in range(5):
                            await asyncio.sleep(1)
                    except Exception as e:
                        logger.debug("TikTok hashtag page %s failed: %s", tag, e)

                await browser.close()

        except Exception as e:
            logger.warning("TikTok Playwright hashtag scrape failed: %s", e)
            return []

        if not all_videos:
            logger.warning("TikTok hashtag scrape: no videos captured")
            return []

        # Deduplicate by video ID
        seen_ids: set[str] = set()
        unique_videos: list[dict] = []
        for v in all_videos:
            vid = v.get("id", "")
            if vid and vid in seen_ids:
                continue
            seen_ids.add(vid)
            unique_videos.append(v)

        signals: list[RawSignalData] = []
        for video in unique_videos:
            desc = video.get("desc", "")
            if not desc:
                continue

            product_name = _extract_product_name(desc)
            if not product_name:
                continue

            stats = video.get("stats", {})
            plays = stats.get("playCount", 0)
            likes = stats.get("diggCount", 0)
            shares = stats.get("shareCount", 0)
            comments = stats.get("commentCount", 0)

            # Engagement-weighted popularity score
            popularity = float(plays + likes * 10 + shares * 20 + comments * 5)

            # Video cover as image
            video_data = video.get("video", {})
            cover_url = video_data.get("cover") or video_data.get("originCover")

            video_id = video.get("id", "")
            author = video.get("author", {}).get("uniqueId", "")
            video_url = f"https://www.tiktok.com/@{author}/video/{video_id}" if author and video_id else None

            signals.append(RawSignalData(
                source=self.source_name,
                product_name=product_name,
                signal_type="tiktok_popularity",
                value=popularity,
                metadata={
                    "image_url": cover_url,
                    "product_url": video_url,
                    "play_count": plays,
                    "like_count": likes,
                    "share_count": shares,
                    "comment_count": comments,
                },
            ))
            signals.append(RawSignalData(
                source=self.source_name,
                product_name=product_name,
                signal_type="mention",
                value=1.0,
            ))

        logger.info(
            "TikTok hashtag scrape: %d signals from %d unique videos",
            len(signals), len(unique_videos),
        )
        return signals
