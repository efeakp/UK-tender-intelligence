"""
Nordic Energy — Tender Intelligence API
FastAPI application entry point.
"""
import logging
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from app.config import settings
from app.routers import tenders, sources, refresh, export, summarise, digest, market, shortlist
from app.models.tender import HealthResponse
from app.dependencies import cache

# ── Logging ───────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=getattr(logging, settings.log_level.upper(), logging.INFO),
    format="%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)


# ── Lifespan ──────────────────────────────────────────────────────────────────
@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Start scheduler and trigger initial cache warm-up on startup.

    The warm-up runs as a background task so the API is immediately
    available to accept requests. The dashboard will show a 503 or empty
    cache until the warm-up completes (~60-90s), then auto-refreshes.
    """
    import asyncio
    from app.services.scheduler import start_scheduler, stop_scheduler
    from app.routers.refresh import _do_refresh

    logger.info("Nordic Energy Tender API starting up (env=%s)", settings.app_env)
    start_scheduler()

    async def _background_warmup():
        logger.info("Cache warm-up starting in background…")
        try:
            result = await _do_refresh()
            if result.success:
                logger.info("Cache warm-up complete: %d tenders", result.tenders_fetched)
            else:
                logger.warning("Cache warm-up failed: %s", result.message)
        except Exception as exc:
            logger.error("Cache warm-up raised an exception: %s", exc, exc_info=True)

    # Fire warm-up as a background task — API accepts requests immediately
    asyncio.create_task(_background_warmup())

    yield
    stop_scheduler()
    logger.info("Nordic Energy Tender API shut down")


# ── App ───────────────────────────────────────────────────────────────────────
app = FastAPI(
    title="Nordic Energy — Tender Intelligence API",
    description=(
        "Proxies Find a Tender, Contracts Finder, Sell2Wales and Public Contracts "
        "Scotland, scores results against Nordic Energy's four service areas, "
        "and serves filtered/paginated tender data to the dashboard."
    ),
    version="2.0.0",
    lifespan=lifespan,
    docs_url="/docs",
    redoc_url="/redoc",
)


# ── CORS ──────────────────────────────────────────────────────────────────────
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins_list,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE"],
    allow_headers=["*"],
)


# ── Routers ───────────────────────────────────────────────────────────────────
app.include_router(tenders.router)
app.include_router(sources.router)
app.include_router(refresh.router)
app.include_router(export.router)
app.include_router(summarise.router)
app.include_router(digest.router)
app.include_router(market.router)
app.include_router(shortlist.router)


# ── Health check ──────────────────────────────────────────────────────────────
@app.get("/health", response_model=HealthResponse, tags=["Health"])
async def health():
    return HealthResponse(
        status="ok",
        version="2.0.0",
        environment=settings.app_env,
        cache_populated=cache.is_populated(),
    )