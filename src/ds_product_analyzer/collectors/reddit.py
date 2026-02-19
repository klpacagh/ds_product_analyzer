import asyncio
import logging
import re
from datetime import datetime, timezone

import praw

from ds_product_analyzer.config import settings

from .base import BaseCollector, RawSignalData

logger = logging.getLogger(__name__)

SUBREDDITS = [
    "shutupandtakemymoney",
    "BuyItForLife",
    "gadgets",
    "DidntKnowIWantedThat",  # was AmazonTopRated (403)
    "TopOfAmazon",           # was cooltools (404)
]


class RedditCollector(BaseCollector):
    source_name = "reddit"

    def __init__(self):
        self._reddit = None

    def _get_reddit(self) -> praw.Reddit:
        if self._reddit is None:
            if not settings.reddit_client_id:
                raise RuntimeError(
                    "Reddit credentials not configured. "
                    "Set REDDIT_CLIENT_ID and REDDIT_CLIENT_SECRET in .env"
                )
            self._reddit = praw.Reddit(
                client_id=settings.reddit_client_id,
                client_secret=settings.reddit_client_secret,
                user_agent=settings.reddit_user_agent,
            )
        return self._reddit

    async def collect(self, keywords: list[str]) -> list[RawSignalData]:
        """Collect signals from Reddit. Keywords used for relevance filtering."""
        return await asyncio.to_thread(self._collect_sync, keywords)

    def _collect_sync(self, keywords: list[str]) -> list[RawSignalData]:
        signals: list[RawSignalData] = []
        keyword_set = {kw.lower() for kw in keywords}

        try:
            reddit = self._get_reddit()
        except RuntimeError as e:
            logger.warning("Reddit collector skipped: %s", e)
            return signals

        for sub_name in SUBREDDITS:
            try:
                subreddit = reddit.subreddit(sub_name)
                for post in subreddit.hot(limit=50):
                    title = post.title
                    title_lower = title.lower()

                    # Check if any keyword appears in the title
                    matched_keywords = [kw for kw in keyword_set if kw in title_lower]

                    # Extract product name from title
                    product_name = self._extract_product_name(title)
                    if not product_name:
                        continue

                    # Calculate upvote velocity (upvotes per hour since creation)
                    age_hours = max(
                        (datetime.now(timezone.utc) - datetime.fromtimestamp(
                            post.created_utc, tz=timezone.utc
                        )).total_seconds() / 3600,
                        0.1,
                    )
                    upvote_velocity = post.score / age_hours

                    signals.append(RawSignalData(
                        source=self.source_name,
                        product_name=product_name,
                        signal_type="upvote_velocity",
                        value=round(upvote_velocity, 2),
                        metadata={
                            "subreddit": sub_name,
                            "title": title[:200],
                            "score": post.score,
                            "num_comments": post.num_comments,
                            "age_hours": round(age_hours, 1),
                            "url": f"https://reddit.com{post.permalink}",
                        },
                    ))

                    # Also emit a mention signal for cross-platform counting
                    signals.append(RawSignalData(
                        source=self.source_name,
                        product_name=product_name,
                        signal_type="mention",
                        value=1.0,
                        metadata={"subreddit": sub_name},
                    ))

            except Exception as e:
                logger.warning("Failed to scrape r/%s: %s", sub_name, e)

        return signals

    def _extract_product_name(self, title: str) -> str | None:
        """Extract a product name from a Reddit post title."""
        for prefix in ["[OC]", "[Amazon]", "[Kickstarter]", "Just found", "Check out"]:
            title = title.replace(prefix, "").strip()

        # Prefer quoted product names
        quoted = re.findall(r'"([^"]+)"', title)
        if quoted:
            return quoted[0]

        # Use the cleaned title directly — these subreddits are product-centric
        clean = title.strip("!?. ")
        if not clean:
            return None
        if len(clean) > 120:
            clean = clean[:120].rsplit(" ", 1)[0]
        return clean
