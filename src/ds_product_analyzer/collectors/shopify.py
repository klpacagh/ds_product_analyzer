"""Shopify D2C bestseller collector.

Fetches the public /products.json endpoint (no auth required) from a list of
configured Shopify store URLs, sorted by best-selling. Returns up to 100
non-zero-value signals per store (rank 101+ → value 0, skipped).
"""

import asyncio
import logging

import httpx

from ds_product_analyzer.config import settings

from .base import BaseCollector, RawSignalData

logger = logging.getLogger(__name__)


class ShopifyCollector(BaseCollector):
    source_name = "shopify"

    async def collect(self, keywords: list[str]) -> list[RawSignalData]:
        # keywords ignored — like Amazon M&S, we collect trending regardless of category
        signals: list[RawSignalData] = []
        async with httpx.AsyncClient(timeout=15, follow_redirects=True) as client:
            for store_url in settings.shopify_store_urls:
                try:
                    url = f"{store_url.rstrip('/')}/products.json"
                    resp = await client.get(
                        url, params={"sort_by": "best-selling", "limit": 250}
                    )
                    resp.raise_for_status()
                    products = resp.json().get("products", [])
                    for i, product in enumerate(products):
                        rank = i + 1
                        value = max(0.0, 100 - rank + 1)
                        if value <= 0:
                            break
                        price = None
                        if product.get("variants"):
                            try:
                                price = float(product["variants"][0]["price"])
                            except (KeyError, ValueError):
                                pass
                        image_url = None
                        if product.get("images"):
                            image_url = product["images"][0].get("src")
                        handle = product.get("handle", "")
                        signals.append(
                            RawSignalData(
                                source="shopify",
                                product_name=product["title"],
                                signal_type="shopify_bestseller",
                                value=value,
                                metadata={
                                    "store_url": store_url,
                                    "store_name": store_url.replace("https://", "")
                                    .replace("http://", "")
                                    .split("/")[0],
                                    "price": price,
                                    "image_url": image_url,
                                    "product_url": f"{store_url.rstrip('/')}/products/{handle}"
                                    if handle
                                    else None,
                                    "product_type": product.get("product_type", ""),
                                },
                            )
                        )
                    await asyncio.sleep(settings.shopify_rate_limit_secs)
                except Exception as e:
                    logger.warning("Shopify collection failed for %s: %s", store_url, e)
        return signals
