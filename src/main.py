from fastapi import FastAPI
from contextlib import asynccontextmanager
from pathlib import Path
import pandas as pd

from src.ingestion.loader import load_events, load_trader_profiles, load_related_accounts
from src.detection.engine import DetectionEngine
from src.workflows.watchlist import WatchlistManager
from src.api.routes import router, AppState
from src.api.simulation import sim_router

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

    AppState.baseline_df = baseline_df
    AppState.profiles = profiles
    AppState.related_accounts = related_accounts
    AppState.engine = DetectionEngine(baseline_df, related_accounts) if not baseline_df.empty else None
    AppState.watchlist = WatchlistManager()
    AppState.alerts = []
    AppState.triage_results = {}

    if scenario_path.exists():
        scenario_df = load_events(scenario_path)
        print(f"[startup] Scenario: {len(scenario_df):,} events — auto-detecting alerts...")
        if AppState.engine:
            AppState.alerts = AppState.engine.run(scenario_df)
            print(f"[startup] Pre-detected {len(AppState.alerts)} alert(s)")

    print("[startup] Ready.")
    yield
    print("[shutdown] Cleaning up...")


app = FastAPI(
    title="Trade Surveillance & Alert Triage Engine",
    description="AI-powered trade surveillance using Claude for intelligent alert triage",
    version="1.0.0",
    lifespan=lifespan,
)

app.include_router(sim_router)   # GET /  → dashboard, GET /stream → SSE
app.include_router(router)        # API routes: /alerts, /metrics, /demo/run, …


@app.get("/health")
async def health():
    return {"status": "ok", "alerts_loaded": len(AppState.alerts)}
