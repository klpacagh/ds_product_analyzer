"""Diagnostic script for TikTok search scraping.

Usage:
    python scripts/test_tiktok_search.py "air fryer"

What it does:
- Runs search_tiktok_videos with DEBUG logging enabled
- Prints ALL intercepted TikTok JSON response URLs to stdout
- Takes a screenshot of the final page state → /tmp/tiktok_debug.png
- Prints the result dict (or "None — scrape failed")
"""

import asyncio
import logging
import os
import sys
import time
import urllib.parse
from pathlib import Path

# Allow running from repo root without installing the package
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

logging.basicConfig(
    level=logging.DEBUG,
    format="%(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

# Ensure bundled browser libs are on LD_LIBRARY_PATH (WSL2 compat).
local_libs = Path(__file__).resolve().parents[1] / ".local-libs" / "lib"
if local_libs.is_dir():
    existing = os.environ.get("LD_LIBRARY_PATH", "")
    if str(local_libs) not in existing:
        os.environ["LD_LIBRARY_PATH"] = (
            f"{local_libs}:{existing}" if existing else str(local_libs)
        )


async def run_diagnostic(keyword: str) -> None:
    from playwright.async_api import async_playwright
    from playwright_stealth import stealth_async

    intercepted_urls: list[str] = []
    all_videos: list[dict] = []

    async def _on_response(response):
        if "tiktok.com" not in response.url:
            return
        content_type = response.headers.get("content-type", "")
        if "json" not in content_type:
            return
        intercepted_urls.append(response.url)
        print(f"[JSON] {response.url[:160]}", flush=True)
        try:
            body = await response.json()
            for key in ("itemList", "item_list"):
                items = body.get(key)
                if items:
                    all_videos.extend(items)
                    print(f"  → found {len(items)} items under '{key}'", flush=True)
                    break
            data = body.get("data", {})
            if isinstance(data, dict):
                items = data.get("itemList", data.get("item_list", []))
                if items:
                    all_videos.extend(items)
                    print(f"  → found {len(items)} items under 'data.itemList'", flush=True)
        except Exception:
            pass

    encoded = urllib.parse.quote(keyword)
    url = f"https://www.tiktok.com/search/video?q={encoded}"
    screenshot_path = "/tmp/tiktok_debug.png"

    print(f"\nNavigating to: {url}", flush=True)
    print("=" * 60, flush=True)

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
        await stealth_async(page)
        page.on("response", _on_response)

        try:
            await page.goto(url, wait_until="networkidle", timeout=30000)
            await asyncio.sleep(3)
            for _ in range(3):
                await page.evaluate("window.scrollBy(0, 800)")
                await asyncio.sleep(2)
        except Exception as e:
            print(f"\nNavigation error: {e}", flush=True)

        await page.screenshot(path=screenshot_path, full_page=False)
        print(f"\nScreenshot saved → {screenshot_path}", flush=True)

        page_title = await page.title()
        print(f"Page title: {page_title}", flush=True)

        await browser.close()

    print("\n" + "=" * 60, flush=True)
    print(f"Total intercepted TikTok JSON URLs: {len(intercepted_urls)}", flush=True)
    print(f"Total videos captured: {len(all_videos)}", flush=True)

    if all_videos:
        print("\nFirst video sample keys:", list(all_videos[0].keys())[:10])
    else:
        print("\nNo videos captured — likely bot-detection or URL filter mismatch.")
        print("Check /tmp/tiktok_debug.png to see what the page rendered.")

    # Now run the real function
    print("\n" + "=" * 60, flush=True)
    print("Running search_tiktok_videos()...", flush=True)
    from ds_product_analyzer.collectors.tiktok import search_tiktok_videos
    result = await search_tiktok_videos(keyword, months=3)
    if result is None:
        print("\nResult: None — scrape failed", flush=True)
    else:
        import json
        print("\nResult:", flush=True)
        print(json.dumps(result, indent=2), flush=True)


def main():
    keyword = sys.argv[1] if len(sys.argv) > 1 else "air fryer"
    print(f"TikTok Search Diagnostic — keyword: {keyword!r}")
    asyncio.run(run_diagnostic(keyword))


if __name__ == "__main__":
    main()
