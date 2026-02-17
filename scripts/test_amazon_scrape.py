"""Amazon Movers & Shakers scraping feasibility test.

Tests whether we can scrape product trend data from Amazon's Movers & Shakers
pages using httpx + BeautifulSoup without proxies.
"""

import time
import httpx
from bs4 import BeautifulSoup

CATEGORIES = {
    "electronics": "https://www.amazon.com/gp/movers-and-shakers/electronics/",
    "home-kitchen": "https://www.amazon.com/gp/movers-and-shakers/home-garden/",
}

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


def parse_products(html: str) -> list[dict]:
    """Extract product data from a Movers & Shakers page."""
    soup = BeautifulSoup(html, "html.parser")
    products = []

    # Amazon uses several possible selectors for M&S product cards
    # Try the grid item approach first
    cards = soup.select("#gridItemRoot")
    if not cards:
        cards = soup.select("[id^='gridItemRoot']")
    if not cards:
        # Fallback: look for the ranked list items
        cards = soup.select(".a-list-item .zg-grid-general-faceout")

    for card in cards:
        product = {}

        # Product name
        name_el = card.select_one("._cDEzb_p13n-sc-css-line-clamp-1_1Fn1y") or card.select_one(
            ".p13n-sc-truncate"
        )
        if not name_el:
            name_el = card.select_one("a[href] span div")
        product["name"] = name_el.get_text(strip=True) if name_el else None

        # Rank change percentage
        change_el = card.select_one(".zg-percent-change span") or card.select_one(
            "[class*='percent']"
        )
        product["percent_change"] = change_el.get_text(strip=True) if change_el else None

        # Price
        price_el = card.select_one(".p13n-sc-price") or card.select_one("span.a-price .a-offscreen")
        product["price"] = price_el.get_text(strip=True) if price_el else None

        # Rating / review count
        rating_el = card.select_one("span.a-icon-alt")
        product["rating"] = rating_el.get_text(strip=True) if rating_el else None

        review_el = card.select_one("span.a-size-small")
        product["review_count"] = review_el.get_text(strip=True) if review_el else None

        # Image
        img_el = card.select_one("img")
        product["image_url"] = img_el.get("src") if img_el else None

        products.append(product)

    return products


def test_category(name: str, url: str, client: httpx.Client) -> dict:
    """Test scraping a single M&S category. Returns result summary."""
    print(f"\n{'='*60}")
    print(f"Testing: {name}")
    print(f"URL: {url}")
    print(f"{'='*60}")

    try:
        resp = client.get(url, follow_redirects=True)
    except httpx.HTTPError as e:
        print(f"  HTTP error: {e}")
        return {"category": name, "status": "error", "error": str(e)}

    print(f"  Status: {resp.status_code}")
    print(f"  Content-Length: {len(resp.text)}")

    if resp.status_code != 200:
        print(f"  BLOCKED or error response")
        # Check for CAPTCHA indicators
        if "captcha" in resp.text.lower() or "robot" in resp.text.lower():
            print(f"  Detected CAPTCHA/bot check")
        return {"category": name, "status": resp.status_code}

    # Check for soft blocks (redirects to CAPTCHA, empty body, etc.)
    if len(resp.text) < 1000:
        print(f"  Suspiciously small response body")
        return {"category": name, "status": "suspicious_response"}

    if "captcha" in resp.text.lower():
        print(f"  CAPTCHA detected in response body")
        return {"category": name, "status": "captcha"}

    products = parse_products(resp.text)
    print(f"  Products found: {len(products)}")

    # Show sample products
    named_products = [p for p in products if p.get("name")]
    for p in named_products[:3]:
        print(f"\n  Sample product:")
        for k, v in p.items():
            if v:
                print(f"    {k}: {v}")

    return {
        "category": name,
        "status": resp.status_code,
        "products_total": len(products),
        "products_with_name": len(named_products),
    }


def main():
    print("Amazon Movers & Shakers — Scraping Feasibility Test")
    print("=" * 60)

    results = []

    with httpx.Client(headers=HEADERS, timeout=30.0) as client:
        for i, (name, url) in enumerate(CATEGORIES.items()):
            if i > 0:
                delay = 3.0
                print(f"\n  Waiting {delay}s between requests...")
                time.sleep(delay)
            result = test_category(name, url, client)
            results.append(result)

    # Summary
    print(f"\n\n{'='*60}")
    print("SUMMARY")
    print(f"{'='*60}")
    success = 0
    for r in results:
        status = r.get("status")
        products = r.get("products_with_name", 0)
        verdict = "PASS" if status == 200 and products > 0 else "FAIL"
        if verdict == "PASS":
            success += 1
        print(f"  {r['category']}: {verdict} (status={status}, products={products})")

    print(f"\nOverall: {success}/{len(results)} categories scraped successfully")
    if success == len(results):
        print("Verdict: Amazon scraping is FEASIBLE without proxies")
    elif success > 0:
        print("Verdict: PARTIAL success — may need retry logic or rotating headers")
    else:
        print("Verdict: Amazon scraping BLOCKED — need proxies or alternative approach")


if __name__ == "__main__":
    main()
