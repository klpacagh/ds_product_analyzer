# DS Product Analyzer

An automated dropshipping product research tool that monitors multiple retail and social platforms, scores products by trending potential, and surfaces high-opportunity candidates on a live dashboard.

---

## Table of Contents

- [How It Works](#how-it-works)
- [Data Sources](#data-sources)
- [Pipeline](#pipeline)
- [Scoring System](#scoring-system)
- [Dashboard](#dashboard)
- [Configuration](#configuration)
- [Setup & Running](#setup--running)
- [Database Schema](#database-schema)

---

## How It Works

The system runs a continuous collection-and-scoring loop:

```
Collectors → LLM Extraction → Deduplication → Raw Signal Store → Scorer → Dashboard
```

1. **Collectors** scrape 7 platforms on independent schedules and emit typed `RawSignalData` objects.
2. **LLM Extraction** (Claude Haiku) cleans product names from noisy sources like Reddit titles and TikTok captions, and filters out non-product posts.
3. **Deduplication** fuzzy-matches incoming product names against the existing product catalogue so "Apple AirPods Pro" and "airpods pro (2nd gen)" merge into one record.
4. **Raw signals** are stored in SQLite with their source, type, numeric value, and JSON metadata.
5. **The scorer** reads the last 31 days of signals per product and computes a 0–100 composite trend score.
6. **The dashboard** presents ranked products with per-component score breakdown, sparkline history, and links to source pages.

---

## Data Sources

| Source | Collector class | Schedule | Strategy | Signal types |
|---|---|---|---|---|
| Google Trends | `GoogleTrendsCollector` | Every 24 h | pytrends API — interest over time + related queries | `search_velocity`, `breakout`, `rising` |
| Reddit | `RedditCollector` | Every 1 h | PRAW — top 50 hot posts from 5 product subreddits | `upvote_velocity`, `mention` |
| Amazon | `AmazonMoversCollector` | Every 6 h | httpx + BeautifulSoup — Movers & Shakers pages (2 pages × 12 categories) | `bsr_momentum`, `mention` |
| TikTok | `TikTokCollector` | Every 6 h | Creative Center API (stage 1), Playwright hashtag scrape (stage 2 fallback) | `tiktok_popularity`, `mention` |
| Etsy | `EtsyCollector` | Every 6 h | Playwright — most-popular category pages (8 categories) | `etsy_trending`, `mention` |
| Walmart | `WalmartCollector` | Every 6 h | httpx search API (stage 1), Playwright response interception (stage 2 fallback) | `walmart_bestseller`, `mention` |
| Target | `TargetCollector` | Every 6 h | Playwright — RedSky API (`redsky.target.com`) interception (6 categories) | `target_trending`, `mention` |

### Google Trends
Queries the pytrends library in batches of 5 keywords over a 90-day US window. Computes **search velocity** as `(recent 4-week average − older average) / older average × 100`. Also scrapes related rising/breakout queries, which can surface brand-new trending terms.

### Reddit
Monitors five product-focused subreddits: `r/shutupandtakemymoney`, `r/BuyItForLife`, `r/gadgets`, `r/GiftIdeas`, `r/amazonfinds`. **Upvote velocity** is calculated as `post.score / age_in_hours`, giving a rate-normalised measure of community interest.

### Amazon Movers & Shakers
Scrapes Amazon's M&S category pages (products that moved up the most in Best Seller Rank over 24 hours). The `bsr_momentum` signal value is the percentage rank change. Covers 12 categories: electronics, home & garden, beauty, sports & outdoors, pet supplies, toys & games, office products, health & personal care, clothing, tools, automotive, and video games.

### TikTok
Attempts the Creative Center product API first (usually blocked without a session cookie). Falls back to navigating product hashtag pages (`#tiktokmademebuyit`, `#amazonfinds`, `#tiktokshop`, `#viralproducts`) via Playwright and intercepting the `challenge/item_list` JSON responses. **Popularity** is an engagement-weighted composite: `plays + likes×10 + shares×20 + comments×5`.

### Etsy
Uses Playwright (plain httpx returns 403 from bot detection) to navigate the most-popular listing pages for 8 categories. The `etsy_trending` signal value is the listing's favorites count when present, otherwise 1.0.

### Walmart
Attempts Walmart's internal search API first, parsing the JSON envelope for product items. Falls back to Playwright navigation + response interception for `best_seller`-sorted category pages. The `walmart_bestseller` signal value is a rank score: `max(0, 100 − rank + 1)`.

### Target
Uses Playwright to navigate bestselling category pages and intercepts responses from the RedSky API (`redsky.target.com`). Parses `data.search.products[]` from the JSON payload. Signal value is the same rank score formula as Walmart.

---

## Pipeline

### LLM Extraction (`llm_extract.py`)

All sources except Google Trends pass through Claude Haiku before being stored. Signals are batched (default 40 per call) and Haiku is asked to:

- Extract a concise, purchasable product name (2–6 words) from the raw text.
- Strip marketing language, dimensions, and color variants from Amazon titles.
- Identify the specific product in Reddit/TikTok posts.
- Mark the item as **not relevant** if it doesn't reference a specific purchasable product.

Signals marked not relevant (e.g. discussion posts, memes) are dropped before storage. If no `ANTHROPIC_API_KEY` is set, this stage is skipped and all signals pass through unchanged.

### Deduplication (`dedup.py`)

For each incoming signal the dedup layer:

1. **Normalises** the product name — lowercases, strips special chars, removes articles and platform stopwords (`amazon`, `walmart`, `target`, `etsy`, etc.).
2. **Exact-matches** the normalised name against the `products.canonical_name` column.
3. **Exact-matches** against the `product_aliases` table (variant names seen before).
4. **Fuzzy-matches** against all known products using `thefuzz` token sort + token set ratio. Threshold is 80/100.
5. If a fuzzy match is found, the incoming name is stored as an alias. Otherwise a new product record is created.

This means "Apple AirPods Pro 2nd Generation" and "AirPods Pro (2nd Gen)" will merge into the same product and their signals will be aggregated together.

### Scheduling

Each collector has its own APScheduler `interval` job. The scoring job runs every 4 hours. All jobs are `max_instances=1` to prevent overlap.

| Job | Default interval |
|---|---|
| Google Trends | 24 h |
| Reddit | 1 h |
| Amazon | 6 h |
| TikTok | 6 h |
| Etsy | 6 h |
| Walmart | 6 h |
| Target | 6 h |
| Scoring | 4 h |
| Amazon price enrichment | 12 h |

---

## Scoring System

Each product is scored on a 0–100 scale using 9 weighted components. Scores are computed from all signals collected in the **last 31 days**.

### Component weights

| Component | Weight | Column | Description |
|---|---|---|---|
| Search Acceleration | 25% | `search_accel` | Google Trends velocity + breakout/rising bonuses |
| Social Velocity | 18% | `social_velocity` | TikTok engagement + Reddit upvotes + creator diversity |
| Amazon Momentum | 12% | `amazon_accel` | Peak BSR % change, normalised to 0–100 |
| Price Fit | 10% | `price_fit` | How well the price sits in the $20–$60 dropship sweet spot |
| Sentiment | 10% | `sentiment` | DistilBERT sentiment on titles/descriptions |
| Trend Shape | 8% | `trend_shape` | Historical score trajectory — rewards steady inclines, penalises fads |
| Platform Count | 7% | `platform_count` | Number of distinct platforms where the product has appeared |
| Purchase Intent | 5% | `purchase_intent` | Regex match rate for buying phrases in Reddit/Amazon/TikTok text |
| Recency | 5% | `recency` | Signals collected in the last 24 h, normalised |

### Component details

**Search Acceleration (`search_accel`)**
```
base      = min(velocity / 50, 100)          # velocity = % change recent vs older
breakout  = +30 if any breakout signal
rising    = +5 per rising signal, capped at +20
result    = min(base + breakout + rising, 100)
```

**Social Velocity (`social_velocity`)**
```
tiktok_norm  = min(peak_popularity / 100, 100)
reddit_norm  = min(peak_upvote_velocity, 100)
diversity    = +5 per distinct TikTok creator, capped at +15
result       = min(0.6×tiktok + 0.4×reddit + diversity, 100)
```

**Price Fit (`price_fit`)**
Uses the midpoint of the observed price range:

| Price range | Score |
|---|---|
| $20 – $60 | 100 |
| $10 – $20 | 50 → 100 (linear) |
| $60 – $80 | 100 → 50 (linear) |
| $0 – $10 | 0 → 50 (linear) |
| $80 – $150 | 50 → 0 (linear) |
| > $150 | 0 |
| No price data | 50 (neutral) |

**Trend Shape (`trend_shape`)**
Reads the last 10 historical scores and analyses the delta series:

| Pattern | Score |
|---|---|
| Spike (Δ > 15) then drop (Δ < −10) | 15 — fad warning |
| Avg delta < −2 | 0–30 — declining |
| Steady incline (avg Δ > 0, no single jump > 20) | 70–100 |
| Flat or mixed | 50 |
| Fewer than 3 data points | 50 (neutral) |

**Platform Count (`platform_count`)**
```
score = min(distinct_sources / 4 × 100, 100)
```
A product appearing on 4 or more platforms scores 100. With 7 active collectors every cross-platform confirmation raises this score.

**Purchase Intent (`purchase_intent`)**
Scans Reddit titles, Amazon metadata, and TikTok descriptions for phrases like:
`"where can I buy"`, `"link?"`, `"just bought"`, `"take my money"`, `"added to cart"`, `"want this so bad"`, etc.
Score = percentage of text samples that contain at least one match.

### Confirmed vs Social Signals

Products are split into two groups on the dashboard:

- **Confirmed Products** — appeared on at least one retail platform (Amazon, Etsy, Walmart, or Target). These have real purchase history and a verified price signal.
- **Social Signals** — only seen on social/search sources (Reddit, TikTok, Google Trends). May be early-stage or viral content that hasn't yet appeared in retail.

---

## Dashboard

The web UI is served by FastAPI + Jinja2 at `http://localhost:8000`.

### Main table columns

| Column | Source |
|---|---|
| Score | Composite weighted score (0–100) |
| Trend | Sparkline of the last 7 score readings |
| Srch | `search_accel` — Google Trends component |
| Social | `social_velocity` — TikTok + Reddit component |
| Amzn | `amazon_accel` — Amazon BSR momentum |
| P.Fit | `price_fit` — Price range fit score |
| Sent | `sentiment` — NLP sentiment (0=negative, 100=positive) |
| Intent | `purchase_intent` — Buying-phrase match rate |
| Src | `platform_count` — Number of distinct source platforms |
| Seen | Date first observed |

### Product detail page

Drilling into a product shows:
- Full score breakdown with all component values
- Price history chart
- All raw signals grouped by source, with links to original URLs
- First/last seen dates per platform

---

## Configuration

All settings are in `config.py` and can be overridden via environment variables or a `.env` file.

```env
# Database
DATABASE_URL=sqlite+aiosqlite:///./data.db

# Reddit API credentials (required for Reddit collection)
REDDIT_CLIENT_ID=your_client_id
REDDIT_CLIENT_SECRET=your_client_secret
REDDIT_USER_AGENT=ds_product_analyzer/0.1

# Anthropic API key (optional — enables LLM name extraction)
ANTHROPIC_API_KEY=your_key_here
LLM_EXTRACTION_MODEL=claude-haiku-4-20250414
LLM_EXTRACTION_BATCH_SIZE=40

# Collection intervals (hours)
GOOGLE_TRENDS_INTERVAL_HOURS=24
REDDIT_INTERVAL_HOURS=1
AMAZON_INTERVAL_HOURS=6
TIKTOK_INTERVAL_HOURS=6
ETSY_INTERVAL_HOURS=6
WALMART_INTERVAL_HOURS=6
TARGET_INTERVAL_HOURS=6

# Rate limits between requests (seconds)
GOOGLE_TRENDS_RATE_LIMIT_SECS=5.0
AMAZON_RATE_LIMIT_SECS=3.0
ETSY_RATE_LIMIT_SECS=2.0
WALMART_RATE_LIMIT_SECS=3.0
TARGET_RATE_LIMIT_SECS=3.0

# Amazon pages per category (M&S has exactly 2 pages)
AMAZON_PAGES_PER_CATEGORY=2

# Sentiment model
SENTIMENT_MODEL=distilbert-base-uncased-finetuned-sst-2-english
```

---

## Setup & Running

### Prerequisites

- Python 3.11+
- Playwright Chromium (for TikTok, Etsy, Walmart, Target)
- Reddit API credentials (free at [reddit.com/prefs/apps](https://www.reddit.com/prefs/apps))
- Anthropic API key (optional but recommended)

### Installation

```bash
# Install dependencies
uv sync   # or: pip install -e .

# Install Playwright browser
playwright install chromium

# Copy and fill in environment variables
cp .env.example .env
```

### Database initialisation

```bash
alembic upgrade head
```

### Running

```bash
# Start the server (includes scheduler)
uvicorn ds_product_analyzer.api.app:app --reload

# Or via the project entrypoint
python -m ds_product_analyzer
```

The dashboard is available at `http://localhost:8000`.

### Manual collection

Click **Collect Now** in the dashboard to trigger a full pipeline run immediately, or call the API directly:

```bash
curl -X POST http://localhost:8000/api/collect/trigger
```

### Checking signal counts

```sql
SELECT source, signal_type, COUNT(*) as n
FROM raw_signals
GROUP BY source, signal_type
ORDER BY source, n DESC;
```

---

## Database Schema

| Table | Purpose |
|---|---|
| `categories` | Seed keyword lists per product category |
| `products` | Canonical product records with price range |
| `product_aliases` | Variant names that map to a canonical product |
| `raw_signals` | Every collected data point (source, type, value, metadata JSON) |
| `trend_scores` | Scored snapshot per product per scoring run |
| `price_history` | Historical price points per product per source |

Signals accumulate indefinitely; the scorer always operates on the last 31-day window. No migrations are needed when adding new sources — `source` and `signal_type` are free-text VARCHAR columns.
