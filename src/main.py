import asyncio
from fastapi import FastAPI
from contextlib import asynccontextmanager
from pathlib import Path
import pandas as pd

from src.ingestion.loader import load_events, load_trader_profiles, load_related_accounts
from src.detection.engine import DetectionEngine
from src.workflows.watchlist import WatchlistManager
from src.api.routes import router, AppState
from src.api.simulation import sim_router
from src.database import Database

DATA_DIR = Path(__file__).parent.parent / "data"


@asynccontextmanager
async def lifespan(app: FastAPI):
    print("[startup] Loading baseline data and building detection engine...")
    baseline_path = DATA_DIR / "trades_baseline.csv"
    scenario_path = DATA_DIR / "trades_scenario.csv"

    if baseline_path.exists():
        baseline_df = load_events(baseline_path)
        print(f"[startup] Baseline: {len(baseline_df):,} events")
    else:
        print("[startup] WARNING: trades_baseline.csv not found — run data/generate_data.py first")
        baseline_df = pd.DataFrame()

    related_accounts = load_related_accounts()
    profiles = load_trader_profiles()

    # Initialize database
    db = Database()
    AppState.db = db
    AppState._lock = asyncio.Lock()
    print(f"[startup] Database initialized at {db._db_path}")

    AppState.baseline_df = baseline_df
    AppState.profiles = profiles
    AppState.related_accounts = related_accounts
    AppState.engine = DetectionEngine(baseline_df, related_accounts, profiles) if not baseline_df.empty else None
    AppState.watchlist = WatchlistManager()

    # Load persisted data from DB
    AppState.alerts = db.load_alerts()
    AppState.triage_results = db.load_triage_results()
    AppState.simulation_runs = db.load_simulation_runs()
    AppState.feedback_history = db.load_feedback()
    print(f"[startup] Loaded from DB: {len(AppState.alerts)} alerts, "
          f"{len(AppState.triage_results)} triage results, "
          f"{len(AppState.simulation_runs)} runs")

    if scenario_path.exists() and not AppState.alerts:
        scenario_df = load_events(scenario_path)
        print(f"[startup] Scenario: {len(scenario_df):,} events — auto-detecting alerts...")
        AppState.scenario_df = scenario_df
        if AppState.engine:
            AppState.alerts = AppState.engine.run(scenario_df)
            db.save_alerts(AppState.alerts)
            print(f"[startup] Pre-detected {len(AppState.alerts)} alert(s), saved to DB")

    print("[startup] Ready.")
    yield
    print("[shutdown] Cleaning up...")


app = FastAPI(
    title="Trade Surveillance & Alert Triage Engine",
    description="AI-powered trade surveillance using Claude for intelligent alert triage",
    version="2.0.0",
    lifespan=lifespan,
)

# ── Middleware: API Key auth + rate limiting ──────────────────────────────────
from fastapi import Request
from fastapi.responses import JSONResponse
import time

# Simple in-memory rate limiter
_rate_limits: dict[str, list[float]] = {}
RATE_LIMIT_WINDOW = 60  # seconds
RATE_LIMIT_MAX_REQUESTS = 30  # per window for expensive endpoints

EXPENSIVE_PATHS = {"/alerts/", "/traders/", "/demo/run", "/report/daily", "/query"}


@app.middleware("http")
async def rate_limit_middleware(request: Request, call_next):
    """Rate limit expensive endpoints (Claude API calls) per client IP."""
    path = request.url.path

    # Check if this is an expensive endpoint
    is_expensive = any(path.startswith(p) or path == p for p in EXPENSIVE_PATHS)

    if is_expensive and request.method in ("POST", "PUT"):
        client_ip = request.client.host if request.client else "unknown"
        key = f"{client_ip}:{path}"
        now = time.time()

        if key not in _rate_limits:
            _rate_limits[key] = []

        # Prune old entries
        _rate_limits[key] = [t for t in _rate_limits[key] if now - t < RATE_LIMIT_WINDOW]

        if len(_rate_limits[key]) >= RATE_LIMIT_MAX_REQUESTS:
            return JSONResponse(
                status_code=429,
                content={
                    "detail": f"Rate limit exceeded. Max {RATE_LIMIT_MAX_REQUESTS} requests per {RATE_LIMIT_WINDOW}s.",
                    "retry_after": RATE_LIMIT_WINDOW,
                },
            )

        _rate_limits[key].append(now)

    response = await call_next(request)
    return response


# ── Optional API key auth ────────────────────────────────────────────────────
from src.config import get_settings

API_KEY_HEADER = "X-API-Key"


@app.middleware("http")
async def auth_middleware(request: Request, call_next):
    """Optional API key authentication. If SURVEILLANCE_API_KEY is set in .env,
    all POST/PUT/DELETE endpoints require it. GET endpoints are public."""
    settings = get_settings()
    api_key = getattr(settings, 'surveillance_api_key', '')

    if api_key and request.method in ("POST", "PUT", "DELETE"):
        # Allow simulation page and static assets without auth
        if request.url.path in ("/stream", "/stream/reset", "/"):
            pass
        else:
            provided_key = request.headers.get(API_KEY_HEADER, "")
            if provided_key != api_key:
                return JSONResponse(
                    status_code=401,
                    content={"detail": "Invalid or missing API key. Set X-API-Key header."},
                )

    response = await call_next(request)
    return response


app.include_router(sim_router)   # GET /  → dashboard, GET /stream → SSE
app.include_router(router)        # API routes: /alerts, /metrics, /demo/run, …

# ── Serve static files ───────────────────────────────────────────────────────
from fastapi.staticfiles import StaticFiles

STATIC_DIR = Path(__file__).parent.parent / "static"
if STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


@app.get("/health")
async def health():
    db = getattr(AppState, 'db', None)
    return {
        "status": "ok",
        "alerts_loaded": len(AppState.alerts),
        "db_alerts": db.get_alert_count() if db else 0,
        "triage_results": len(AppState.triage_results),
        "simulation_runs": len(AppState.simulation_runs),
        "engine_ready": AppState.engine is not None,
    }
