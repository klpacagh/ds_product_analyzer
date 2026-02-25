"""AliExpress Affiliate API hot products collector.

Uses the AliExpress Affiliate API (aliexpress.affiliate.hotproduct.query) to
fetch trending/bestselling products sorted by recent order volume.

Requires ALIEXPRESS_APP_KEY and ALIEXPRESS_APP_SECRET in .env.
Falls back to empty list when credentials are absent (same pattern as Etsy).
"""

import asyncio
import hashlib
import hmac
import logging
import time

import httpx

from ds_product_analyzer.config import settings

from .base import BaseCollector, RawSignalData

logger = logging.getLogger(__name__)


class AliExpressCollector(BaseCollector):
    source_name = "aliexpress"
    _ENDPOINT = "https://api-sg.aliexpress.com/sync"
    _CATEGORIES = ["toys", "beauty", "sports", "home", "electronics", "clothing"]

    async def collect(self, keywords: list[str]) -> list[RawSignalData]:
        if not settings.aliexpress_app_key or not settings.aliexpress_app_secret:
            logger.warning("AliExpress credentials not set, skipping")
            return []

        signals = []
        async with httpx.AsyncClient(timeout=20, follow_redirects=True) as client:
            for category in self._CATEGORIES:
                try:
                    params = self._build_params(category)
                    resp = await client.get(self._ENDPOINT, params=params)
                    resp.raise_for_status()
                    data = resp.json()
                    products = (
                        data.get("aliexpress_affiliate_hotproduct_query_response", {})
                            .get("resp_result", {})
                            .get("result", {})
                            .get("products", {})
                            .get("product", [])
                    )
                    for i, product in enumerate(products):
                        rank = i + 1
                        orders = int(product.get("lastest_volume", 0) or 0)
                        value = min(orders / 100, 100.0)  # normalise: 10k orders → 100
                        if value <= 0:
                            continue
                        try:
                            price = float(product.get("target_sale_price") or
                                          product.get("app_sale_price") or 0)
                        except (TypeError, ValueError):
                            price = None
                        signals.append(RawSignalData(
                            source="aliexpress",
                            product_name=product.get("product_title", ""),
                            signal_type="aliexpress_hot_product",
                            value=value,
                            metadata={
                                "category": category,
                                "orders": orders,
                                "rank": rank,
                                "price": price if price else None,
                                "image_url": product.get("product_main_image_url"),
                                "product_url": product.get("promotion_link") or
                                               product.get("product_detail_url"),
                            },
                        ))
                    await asyncio.sleep(settings.aliexpress_rate_limit_secs)
                except Exception as e:
                    logger.warning("AliExpress collection failed for %s: %s", category, e)
        return signals

    def _build_params(self, category: str) -> dict:
        """Build HMAC-SHA256 signed request params."""
        ts = str(int(time.time() * 1000))
        base_params = {
            "method": "aliexpress.affiliate.hotproduct.query",
            "app_key": settings.aliexpress_app_key,
            "timestamp": ts,
            "sign_method": "sha256",
            "format": "json",
            "v": "2.0",
            "category_ids": category,
            "fields": "product_title,target_sale_price,app_sale_price,product_main_image_url,promotion_link,product_detail_url,lastest_volume",
            "page_size": "50",
            "sort": "LAST_VOLUME_DESC",
        }
        # Sign: sort params alphabetically, concat key+value, HMAC-SHA256
        sorted_str = "".join(k + str(v) for k, v in sorted(base_params.items()))
        sign = hmac.new(
            settings.aliexpress_app_secret.encode(),
            sorted_str.encode(),
            hashlib.sha256,
        ).hexdigest().upper()
        return {**base_params, "sign": sign}
