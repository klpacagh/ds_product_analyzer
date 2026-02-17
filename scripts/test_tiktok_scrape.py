"""TikTok Creative Center Top Products scraping feasibility test.

Tests three approaches in order:
1. Direct httpx GET — check if page has inline data or is JS-only
2. API endpoint discovery — try known TikTok Creative Center API patterns
3. Playwright browser rendering — full headless Chromium as fallback
"""

import json
import httpx

PAGE_URL = "https://ads.tiktok.com/business/creativecenter/top-products/pc/en"

BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/131.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip, deflate, br",
    "DNT": "1",
    "Connection": "keep-alive",
    "Upgrade-Insecure-Requests": "1",
}

API_HEADERS = {
    "User-Agent": BROWSER_HEADERS["User-Agent"],
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": PAGE_URL,
    "Origin": "https://ads.tiktok.com",
}

# Known/guessed API endpoints for TikTok Creative Center
API_CANDIDATES = [
    "https://ads.tiktok.com/creative_radar_api/v1/top_product/list",
    "https://ads.tiktok.com/creative_radar_api/v1/top_products/list",
    "https://ads.tiktok.com/creative_radar_api/v2/top_product/list",
    "https://ads.tiktok.com/creative_radar_api/v1/top_product/rank_list",
]

API_PARAMS = {
    "page": 1,
    "limit": 20,
    "period": 7,  # last 7 days
    "country_code": "US",
    "order_by": "popular",
}


def test_direct_html() -> dict:
    """Approach 1: Direct HTTP GET to check for inline data."""
    print("\n" + "=" * 60)
    print("Approach 1: Direct httpx GET")
    print("=" * 60)

    try:
        with httpx.Client(headers=BROWSER_HEADERS, timeout=30.0) as client:
            resp = client.get(PAGE_URL, follow_redirects=True)
    except httpx.HTTPError as e:
        print(f"  HTTP error: {e}")
        return {"approach": "direct_html", "status": "error", "error": str(e)}

    print(f"  Status: {resp.status_code}")
    print(f"  Content-Length: {len(resp.text)}")
    print(f"  Final URL: {resp.url}")

    if resp.status_code != 200:
        print(f"  Non-200 response")
        return {"approach": "direct_html", "status": resp.status_code}

    body = resp.text.lower()

    # Check if it's a JS shell (minimal HTML with script bundles)
    has_product_data = any(
        kw in body
        for kw in ["top-product", "product_name", "product-name", "popularity"]
    )
    is_js_shell = body.count("<script") > 3 and not has_product_data

    print(f"  Script tags: {body.count('<script')}")
    print(f"  Has product keywords: {has_product_data}")
    print(f"  Likely JS shell: {is_js_shell}")

    # Look for embedded JSON data (Next.js __NEXT_DATA__ or similar)
    for marker in ["__NEXT_DATA__", "__INITIAL_STATE__", "window.__data"]:
        if marker.lower() in body:
            print(f"  Found embedded data marker: {marker}")

    if has_product_data and not is_js_shell:
        print(f"  Result: Page contains product data inline!")
        return {"approach": "direct_html", "status": "success"}
    else:
        print(f"  Result: JS-rendered shell — no inline product data")
        return {"approach": "direct_html", "status": "js_shell"}


def test_api_endpoints() -> dict:
    """Approach 2: Try known API endpoint patterns."""
    print("\n" + "=" * 60)
    print("Approach 2: API endpoint discovery")
    print("=" * 60)

    with httpx.Client(headers=API_HEADERS, timeout=30.0) as client:
        for url in API_CANDIDATES:
            print(f"\n  Trying: {url}")
            try:
                # Try GET with params
                resp = client.get(url, params=API_PARAMS, follow_redirects=True)
                print(f"    GET status: {resp.status_code}, length: {len(resp.text)}")

                if resp.status_code == 200:
                    try:
                        data = resp.json()
                        print(f"    Valid JSON response")
                        print(f"    Keys: {list(data.keys()) if isinstance(data, dict) else 'list'}")

                        # Check if it contains product data
                        products = extract_api_products(data)
                        if products:
                            print(f"    Products found: {len(products)}")
                            for p in products[:2]:
                                print(f"    Sample: {p}")
                            return {
                                "approach": "api",
                                "status": "success",
                                "url": url,
                                "method": "GET",
                                "products": len(products),
                            }
                    except json.JSONDecodeError:
                        print(f"    Not JSON")

                # Also try POST
                resp = client.post(url, json=API_PARAMS, follow_redirects=True)
                print(f"    POST status: {resp.status_code}, length: {len(resp.text)}")

                if resp.status_code == 200:
                    try:
                        data = resp.json()
                        print(f"    Valid JSON response")
                        products = extract_api_products(data)
                        if products:
                            print(f"    Products found: {len(products)}")
                            for p in products[:2]:
                                print(f"    Sample: {p}")
                            return {
                                "approach": "api",
                                "status": "success",
                                "url": url,
                                "method": "POST",
                                "products": len(products),
                            }
                    except json.JSONDecodeError:
                        print(f"    Not JSON")

            except httpx.HTTPError as e:
                print(f"    Error: {e}")

    print(f"\n  No working API endpoint found")
    return {"approach": "api", "status": "no_endpoint_found"}


def extract_api_products(data: dict | list) -> list[dict]:
    """Try to extract product list from API response."""
    if isinstance(data, list):
        return data[:5]

    # Common response structures
    for path in [
        lambda d: d.get("data", {}).get("list", []),
        lambda d: d.get("data", {}).get("products", []),
        lambda d: d.get("data", {}).get("items", []),
        lambda d: d.get("data", {}).get("rank_list", []),
        lambda d: d.get("data", []) if isinstance(d.get("data"), list) else [],
        lambda d: d.get("products", []),
        lambda d: d.get("list", []),
    ]:
        try:
            result = path(data)
            if result and isinstance(result, list) and len(result) > 0:
                return result[:5]
        except (AttributeError, TypeError):
            continue
    return []


def test_playwright() -> dict:
    """Approach 3: Use Playwright to render the page."""
    print("\n" + "=" * 60)
    print("Approach 3: Playwright browser rendering")
    print("=" * 60)

    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("  playwright not installed")
        return {"approach": "playwright", "status": "not_installed"}

    try:
        with sync_playwright() as p:
            print("  Launching headless Chromium...")
            browser = p.chromium.launch(headless=True)
            context = browser.new_context(
                viewport={"width": 1920, "height": 1080},
                user_agent=BROWSER_HEADERS["User-Agent"],
            )
            page = context.new_page()

            # Capture API calls made by the page
            api_calls = []

            def handle_response(response):
                url = response.url
                if any(kw in url for kw in ["top_product", "top-product", "creative_radar"]):
                    api_calls.append(
                        {"url": url, "status": response.status}
                    )

            page.on("response", handle_response)

            print(f"  Navigating to {PAGE_URL}")
            page.goto(PAGE_URL, wait_until="networkidle", timeout=60000)

            # Wait for product content to appear
            print("  Waiting for product content...")
            try:
                page.wait_for_selector(
                    "[class*='product'], [class*='Product'], [data-testid*='product']",
                    timeout=15000,
                )
                print("  Product elements found via selector")
            except Exception:
                print("  No product selector matched, checking page content...")

            content = page.content()
            title = page.title()
            print(f"  Page title: {title}")
            print(f"  Content length: {len(content)}")
            print(f"  API calls captured: {len(api_calls)}")
            for call in api_calls:
                print(f"    {call['status']} {call['url'][:100]}")

            # Try to extract visible product text
            product_elements = page.query_selector_all(
                "[class*='product' i], [class*='Product'], "
                "[class*='rank' i], [class*='Rank']"
            )
            print(f"  Product-related elements: {len(product_elements)}")

            # Get all visible text as fallback analysis
            body_text = page.inner_text("body")
            text_lines = [
                line.strip()
                for line in body_text.split("\n")
                if line.strip() and len(line.strip()) > 3
            ]
            print(f"  Visible text lines: {len(text_lines)}")
            if text_lines:
                print(f"  First 10 lines of visible text:")
                for line in text_lines[:10]:
                    print(f"    {line[:100]}")

            browser.close()

            has_products = len(product_elements) > 0 or any(
                kw in body_text.lower()
                for kw in ["trending", "popularity", "creator", "product"]
            )

            return {
                "approach": "playwright",
                "status": "success" if has_products else "no_products",
                "product_elements": len(product_elements),
                "api_calls": api_calls,
                "text_lines": len(text_lines),
            }

    except Exception as e:
        print(f"  Playwright error: {e}")
        return {"approach": "playwright", "status": "error", "error": str(e)}


def main():
    print("TikTok Creative Center Top Products — Scraping Feasibility Test")
    print("=" * 60)

    results = []

    # Approach 1: Direct HTML
    r1 = test_direct_html()
    results.append(r1)

    # Approach 2: API endpoints
    r2 = test_api_endpoints()
    results.append(r2)

    # Approach 3: Playwright (only if previous approaches failed)
    if r2.get("status") != "success":
        r3 = test_playwright()
        results.append(r3)
    else:
        print("\n  Skipping Playwright — API endpoint worked")

    # Summary
    print(f"\n\n{'='*60}")
    print("SUMMARY")
    print(f"{'='*60}")
    for r in results:
        approach = r["approach"]
        status = r["status"]
        extra = ""
        if "products" in r:
            extra = f", products={r['products']}"
        if "url" in r:
            extra += f", endpoint={r['url']}"
        print(f"  {approach}: {status}{extra}")

    working = [r for r in results if r.get("status") == "success"]
    if working:
        best = working[0]
        print(f"\nVerdict: TikTok scraping is FEASIBLE via {best['approach']}")
        if best["approach"] == "api":
            print(f"  Best method: API endpoint ({best.get('method')} {best.get('url')})")
        elif best["approach"] == "playwright":
            print(f"  Best method: Playwright browser rendering")
    else:
        print(f"\nVerdict: TikTok scraping NOT feasible without proxies")
        print(f"  Consider: Apify TikTok scraper or proxy service")


if __name__ == "__main__":
    main()
