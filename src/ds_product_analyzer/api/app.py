import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from ds_product_analyzer.scheduler.jobs import setup_scheduler

logger = logging.getLogger(__name__)

FRONTEND_DIR = Path(__file__).resolve().parent.parent / "frontend"


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    logger.info("Starting DS Product Analyzer...")
    sched = setup_scheduler()
    sched.start()
    logger.info("Scheduler started with %d jobs", len(sched.get_jobs()))
    yield
    # Shutdown
    sched.shutdown()
    logger.info("Scheduler shut down.")


def create_app() -> FastAPI:
    app = FastAPI(title="DS Product Analyzer", version="0.1.0", lifespan=lifespan)

    # Static files
    static_dir = FRONTEND_DIR / "static"
    static_dir.mkdir(parents=True, exist_ok=True)
    app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

    # Register routes
    from ds_product_analyzer.api.routes.products import router as products_router

    app.include_router(products_router)

    return app


# Templates instance shared by routes
templates = Jinja2Templates(directory=str(FRONTEND_DIR / "templates"))

app = create_app()
