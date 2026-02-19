import asyncio
import logging

from apscheduler.schedulers.asyncio import AsyncIOScheduler

from ds_product_analyzer.config import settings
from ds_product_analyzer.pipeline.runner import (
    run_amazon_collection,
    run_google_collection,
    run_reddit_collection,
    run_scoring,
    run_tiktok_collection,
)

logger = logging.getLogger(__name__)

scheduler = AsyncIOScheduler()


def _run_async(coro_func):
    """Wrapper for APScheduler to run async functions."""
    async def wrapper():
        try:
            await coro_func()
        except Exception as e:
            logger.error("Scheduled job %s failed: %s", coro_func.__name__, e)
    return wrapper


def setup_scheduler():
    """Configure and return the scheduler with all jobs."""
    scheduler.add_job(
        _run_async(run_google_collection),
        "interval",
        hours=settings.collect_interval_hours,
        id="google_trends",
        max_instances=1,
        name="Google Trends Collection",
    )
    scheduler.add_job(
        _run_async(run_reddit_collection),
        "interval",
        hours=settings.reddit_interval_hours,
        id="reddit",
        max_instances=1,
        name="Reddit Collection",
    )
    scheduler.add_job(
        _run_async(run_amazon_collection),
        "interval",
        hours=settings.amazon_interval_hours,
        id="amazon",
        max_instances=1,
        name="Amazon M&S Collection",
    )
    scheduler.add_job(
        _run_async(run_tiktok_collection),
        "interval",
        hours=settings.tiktok_interval_hours,
        id="tiktok",
        max_instances=1,
        name="TikTok Collection",
    )
    scheduler.add_job(
        _run_async(run_scoring),
        "interval",
        hours=settings.collect_interval_hours,
        id="scoring",
        max_instances=1,
        name="Trend Scoring",
    )
    return scheduler
