"""
simulation.py — Real-time trade surveillance simulation via Server-Sent Events.

Routes:
  GET /          → serve static/index.html
  GET /stream    → SSE stream replaying the scenario day with detection+triage
  GET /stream/reset → clear pre-computation cache for a fresh run
"""

import asyncio
import json
from pathlib import Path
from typing import AsyncGenerator

import pandas as pd
from fastapi import APIRouter
from fastapi.responses import HTMLResponse, StreamingResponse

from src.ingestion.loader import load_events
from src.triage.claude_client import ClaudeTriageClient
from src.ingestion.schemas import Alert, TriageResult

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

ANOMALY_MAIN_TRADERS = {"T-4821", "T-9033", "T-9034", "T-6610", "T-8802", "T-5599"}
FP_TRADERS = {"T-0003"}  # Market-maker false positive scenario

DATA_DIR = Path(__file__).parent.parent.parent / "data"
STATIC_DIR = Path(__file__).parent.parent.parent / "static"

# ---------------------------------------------------------------------------
# Pre-computation cache (module-level, shared across requests)
# ---------------------------------------------------------------------------

_cache: dict = {
    "ready": False,
    "computing": False,
    "scenario_df": None,
    "alerts": [],        # list[Alert]
    "triage_map": {},    # alert_id -> TriageResult
    "profiles": {},      # trader_id -> TraderProfile
    "error": None,
}

# ---------------------------------------------------------------------------
# Router
# ---------------------------------------------------------------------------

sim_router = APIRouter()

# ---------------------------------------------------------------------------
# Helper: format a DataFrame row as a trade SSE dict
# ---------------------------------------------------------------------------

def _row_to_trade_dict(row, anomaly: bool, anomaly_type: str | None) -> dict:
    ts = getattr(row, "timestamp", None)
    if ts is not None and hasattr(ts, "strftime"):
        ts_str = ts.strftime("%H:%M:%S")
    else:
        ts_str = str(ts)

    return {
        "type": "trade",
        "event_id": str(getattr(row, "event_id", "")),
        "ts": ts_str,
        "trader_id": str(getattr(row, "trader_id", "")),
        "event_type": str(getattr(row, "event_type", "")),
        "side": str(getattr(row, "side", "")),
        "instrument": str(getattr(row, "instrument", "")),
        "quantity": int(getattr(row, "quantity", 0)),
        "price": round(float(getattr(row, "price", 0.0)), 2),
        "order_type": str(getattr(row, "order_type", "")),
        "anomaly": anomaly,
        "anomaly_type": anomaly_type,
    }


def _sse(payload: dict) -> str:
    """Encode a dict as an SSE data line."""
    return f"data: {json.dumps(payload, default=str)}\n\n"


def _find_alert(alerts: list[Alert], trader_id: str, pattern_type: str) -> Alert | None:
    """Find the first alert matching trader and pattern."""
    for a in alerts:
        if a.trader_id == trader_id and a.pattern_type == pattern_type:
            return a
    return None


def _find_any_alert_for_trader(alerts: list[Alert], trader_id: str) -> Alert | None:
    """Find the first alert for a trader."""
    for a in alerts:
        if a.trader_id == trader_id:
            return a
    return None


def _alert_dict(alert: Alert) -> dict:
    return {
        "type": "alert",
        "alert_id": alert.alert_id,
        "pattern_type": alert.pattern_type,
        "severity": alert.severity,
        "trader_id": alert.trader_id,
        "instrument": alert.instrument,
        "z_score": round(alert.z_score, 2),
        "evidence": alert.evidence,
    }


def _verdict_dict(alert: Alert, triage: TriageResult, is_fp: bool) -> dict:
    return {
        "type": "verdict",
        "alert_id": alert.alert_id,
        "trader_id": alert.trader_id,
        "verdict": triage.verdict,
        "confidence": triage.confidence,
        "false_positive_probability": triage.false_positive_probability,
        "rationale": triage.rationale,
        "key_factors": list(triage.key_factors),
        "recommended_action": triage.recommended_action,
        "is_fp": is_fp,
    }


# ---------------------------------------------------------------------------
# Pre-computation: load data, run detection, run triage
# ---------------------------------------------------------------------------

async def _ensure_ready() -> None:
    """
    Populate _cache with scenario data, alerts, and triage results.
    Reuses pre-computed data from AppState (lifespan) to avoid redundant
    baseline loading and detection — only runs triage.
    """
    global _cache

    if _cache["ready"]:
        return

    if _cache["computing"]:
        while _cache["computing"] and not _cache["ready"] and _cache["error"] is None:
            await asyncio.sleep(0.2)
        return

    _cache["computing"] = True
    _cache["error"] = None

    try:
        from src.api.routes import AppState

        # Reuse pre-computed alerts and profiles from lifespan startup
        alerts: list[Alert] = AppState.alerts or []
        profiles: dict = AppState.profiles or {}

        # Load scenario DataFrame (needed for trade replay)
        scenario_df: pd.DataFrame = await asyncio.to_thread(
            load_events, DATA_DIR / "trades_scenario.csv"
        )

        # If AppState has no alerts, fall back to running detection
        if not alerts and AppState.engine:
            alerts = await asyncio.to_thread(AppState.engine.run, scenario_df)

        # Run triage (fast in mock mode, ~1-2s with API)
        def _run_triage():
            client = ClaudeTriageClient()
            return client.triage_all(alerts, profiles, {}, set())

        triage_results: list[TriageResult] = await asyncio.to_thread(_run_triage)
        triage_map: dict[str, TriageResult] = {r.alert_id: r for r in triage_results}

        _cache.update({
            "ready": True,
            "computing": False,
            "scenario_df": scenario_df,
            "alerts": alerts,
            "triage_map": triage_map,
            "profiles": profiles,
            "error": None,
        })

        AppState.alerts = alerts
        AppState.triage_results = triage_map

    except Exception as exc:
        _cache["computing"] = False
        _cache["error"] = str(exc)
        raise


# ---------------------------------------------------------------------------
# SSE stream generator
# ---------------------------------------------------------------------------

async def _generate_stream() -> AsyncGenerator[str, None]:
    """
    Async generator that yields SSE-formatted strings.
    Replays the scenario day in phases, emitting trade events, alerts,
    and triage verdicts with realistic pacing.
    """
    global _cache

    # ── Loading phase ────────────────────────────────────────────────────────
    yield _sse({"type": "loading", "msg": "Loading trade data...", "progress": 15})
    await asyncio.sleep(0.3)

    try:
        # Kick off pre-computation (or wait if already running)
        compute_task = asyncio.create_task(_ensure_ready())

        yield _sse({"type": "loading", "msg": "Running detection engine...", "progress": 45})

        await asyncio.sleep(0.5)

        yield _sse({"type": "loading", "msg": "Triaging alerts with Claude AI...", "progress": 75})

        # Wait for computation to finish
        await compute_task

    except Exception as exc:
        yield _sse({"type": "error", "msg": str(exc)})
        return

    if _cache["error"]:
        yield _sse({"type": "error", "msg": _cache["error"]})
        return

    yield _sse({"type": "loading", "msg": "Simulation ready", "progress": 100})
    await asyncio.sleep(0.4)

    # ── Unpack cache ─────────────────────────────────────────────────────────
    scenario_df: pd.DataFrame = _cache["scenario_df"]
    alerts: list[Alert] = _cache["alerts"]
    triage_map: dict[str, TriageResult] = _cache["triage_map"]

    if scenario_df is None or scenario_df.empty:
        yield _sse({"type": "error", "msg": "Scenario DataFrame is empty"})
        return

    # Identify anomaly traders in the scenario
    anomaly_traders = ANOMALY_MAIN_TRADERS

    # Sim start metadata
    yield _sse(
        {
            "type": "sim_start",
            "date": "2026-02-17",
            "total_events": len(scenario_df),
            "n_alerts": len(alerts),
        }
    )
    await asyncio.sleep(0.3)

    total_events_shown = 0

    # ────────────────────────────────────────────────────────────────────────
    # Helper: emit detect → alert → triage → verdict cycle
    # ────────────────────────────────────────────────────────────────────────
    async def _emit_detection_cycle(
        trader_id: str,
        pattern_type: str,
        instrument: str,
        is_fp: bool = False,
    ):
        alert = _find_alert(alerts, trader_id, pattern_type)
        if alert is None:
            # Fall back: any alert for that trader
            alert = _find_any_alert_for_trader(alerts, trader_id)
        if alert is None:
            return

        yield _sse(
            {
                "type": "detecting",
                "pattern": pattern_type,
                "trader": trader_id,
                "instrument": instrument,
            }
        )
        await asyncio.sleep(1.5)

        yield _sse(_alert_dict(alert))
        await asyncio.sleep(0.8)

        yield _sse({"type": "triaging", "alert_id": alert.alert_id, "trader": trader_id})
        await asyncio.sleep(2.0)

        triage = triage_map.get(alert.alert_id)
        if triage is not None:
            yield _sse(_verdict_dict(alert, triage, is_fp))
            if not is_fp and triage.verdict == "ESCALATE":
                yield _sse(
                    {
                        "type": "watchlist",
                        "trader_id": trader_id,
                        "reason": pattern_type,
                        "hours": 72,
                    }
                )
        await asyncio.sleep(1.5)

    # ── Phase 1: MARKET OPEN (normal events 09:00–09:43) ─────────────────────
    yield _sse({"type": "phase", "name": "MARKET OPEN", "time": "09:00", "normal": True})

    normal_mask = ~scenario_df["trader_id"].isin(anomaly_traders)
    early_mask = scenario_df["timestamp"] < "2026-02-17 09:44"
    phase1_df = scenario_df[normal_mask & early_mask].head(25)

    for row in phase1_df.itertuples(index=False):
        yield _sse(_row_to_trade_dict(row, anomaly=False, anomaly_type=None))
        total_events_shown += 1
        await asyncio.sleep(0.12)

    await asyncio.sleep(0.5)

    # ── Phase 2: LAYERING — T-4821 (09:44–09:47) ─────────────────────────────
    yield _sse(
        {
            "type": "phase",
            "name": "⚠ LAYERING PATTERN FORMING",
            "time": "09:44",
            "normal": False,
        }
    )

    layering_df = (
        scenario_df[scenario_df["trader_id"] == "T-4821"]
        .sort_values("timestamp")
    )

    for row in layering_df.itertuples(index=False):
        yield _sse(_row_to_trade_dict(row, anomaly=True, anomaly_type="LAYERING"))
        total_events_shown += 1
        await asyncio.sleep(0.25)

    await asyncio.sleep(1.0)

    async for event in _emit_detection_cycle("T-4821", "LAYERING", "AAPL", is_fp=False):
        yield event

    # ── Phase 3: Fast-forward → WASH TRADING — T-9033 + T-9034 (11:00–11:30) ─
    yield _sse(
        {
            "type": "fast_forward",
            "from_time": "09:47",
            "to_time": "11:00",
            "skipped": "~2,400 normal events",
        }
    )
    await asyncio.sleep(1.0)

    yield _sse(
        {
            "type": "phase",
            "name": "⚠ WASH TRADING PATTERN FORMING",
            "time": "11:00",
            "normal": False,
        }
    )

    wash_df = (
        scenario_df[scenario_df["trader_id"].isin({"T-9033", "T-9034"})]
        .sort_values("timestamp")
    )

    for row in wash_df.itertuples(index=False):
        yield _sse(_row_to_trade_dict(row, anomaly=True, anomaly_type="WASH_TRADING"))
        total_events_shown += 1
        await asyncio.sleep(0.20)

    await asyncio.sleep(1.0)

    # Emit detection for both wash traders (primary: T-9033)
    async for event in _emit_detection_cycle("T-9033", "WASH_TRADING", "MSFT", is_fp=False):
        yield event

    await asyncio.sleep(1.5)

    # ── Phase 4: Fast-forward → MOMENTUM IGNITION — T-6610 (13:15–13:22) ─────
    yield _sse(
        {
            "type": "fast_forward",
            "from_time": "11:30",
            "to_time": "13:15",
            "skipped": "~3,200 normal events",
        }
    )
    await asyncio.sleep(0.8)

    yield _sse(
        {
            "type": "phase",
            "name": "⚠ MOMENTUM IGNITION PATTERN FORMING",
            "time": "13:15",
            "normal": False,
        }
    )

    momentum_df = (
        scenario_df[scenario_df["trader_id"] == "T-6610"]
        .sort_values("timestamp")
    )

    for row in momentum_df.itertuples(index=False):
        yield _sse(_row_to_trade_dict(row, anomaly=True, anomaly_type="MOMENTUM_IGNITION"))
        total_events_shown += 1
        await asyncio.sleep(0.18)

    await asyncio.sleep(1.0)

    async for event in _emit_detection_cycle("T-6610", "MOMENTUM_IGNITION", "TSLA", is_fp=False):
        yield event

    await asyncio.sleep(1.5)

    # ── Phase 5: Fast-forward → PRICE RAMPING — T-8802 (14:45–14:55) ─────────
    yield _sse(
        {
            "type": "fast_forward",
            "from_time": "13:22",
            "to_time": "14:45",
            "skipped": "~2,800 normal events",
        }
    )
    await asyncio.sleep(0.8)

    yield _sse(
        {
            "type": "phase",
            "name": "⚠ PRICE RAMPING DETECTED",
            "time": "14:45",
            "normal": False,
        }
    )

    ramping_df = (
        scenario_df[scenario_df["trader_id"] == "T-8802"]
        .sort_values("timestamp")
    )

    for row in ramping_df.itertuples(index=False):
        yield _sse(_row_to_trade_dict(row, anomaly=True, anomaly_type="PRICE_RAMPING"))
        total_events_shown += 1
        await asyncio.sleep(0.20)

    await asyncio.sleep(1.0)

    async for event in _emit_detection_cycle("T-8802", "PRICE_RAMPING", "NVDA", is_fp=False):
        yield event

    await asyncio.sleep(1.5)

    # ── Phase 5.5: MARKING THE CLOSE — T-5599 (15:55–16:00) ─────────────────
    yield _sse(
        {
            "type": "fast_forward",
            "from_time": "14:55",
            "to_time": "15:55",
            "skipped": "~3,100 normal events",
        }
    )
    await asyncio.sleep(0.8)

    yield _sse(
        {
            "type": "phase",
            "name": "⚠ MARKING THE CLOSE DETECTED",
            "time": "15:55",
            "normal": False,
        }
    )

    marking_df = (
        scenario_df[scenario_df["trader_id"] == "T-5599"]
        .sort_values("timestamp")
    )
    for row in marking_df.itertuples(index=False):
        yield _sse(_row_to_trade_dict(row, anomaly=True, anomaly_type="MARKING_CLOSE"))
        total_events_shown += 1
        await asyncio.sleep(0.22)

    await asyncio.sleep(1.0)

    async for event in _emit_detection_cycle("T-5599", "MARKING_CLOSE", "AAPL", is_fp=False):
        yield event

    await asyncio.sleep(1.5)

    # ── Phase 6: FALSE POSITIVE — T-0003 Market Maker ──────────────────────
    yield _sse(
        {
            "type": "phase",
            "name": "✓ FALSE POSITIVE: MARKET MAKER IDENTIFIED",
            "time": "10:30",
            "normal": True,
        }
    )
    await asyncio.sleep(0.5)

    # T-0003 is a registered market maker whose high cancel rate triggered layering detector
    mm_fp_df = (
        scenario_df[scenario_df["trader_id"] == "T-0003"]
        .sort_values("timestamp")
        .head(20)
    )
    for row in mm_fp_df.itertuples(index=False):
        yield _sse(_row_to_trade_dict(row, anomaly=True, anomaly_type="LAYERING"))
        total_events_shown += 1
        await asyncio.sleep(0.15)

    await asyncio.sleep(0.6)

    fp_alert = _find_alert(alerts, "T-0003", "LAYERING")
    if fp_alert:
        yield _sse(_alert_dict(fp_alert))
        await asyncio.sleep(0.5)
        yield _sse({"type": "triaging", "alert_id": fp_alert.alert_id, "trader": "T-0003"})
        await asyncio.sleep(2.0)
        triage = triage_map.get(fp_alert.alert_id)
        if triage:
            yield _sse(_verdict_dict(fp_alert, triage, is_fp=True))
    else:
        # Synthetic dismiss if detector didn't fire
        yield _sse(
            {
                "type": "verdict",
                "alert_id": "SYNTHETIC-MM-FP",
                "verdict": "DISMISS",
                "confidence": 0.84,
                "false_positive_probability": 0.82,
                "rationale": (
                    "T-0003 is a registered market maker. The 87.5% cancel ratio reflects "
                    "legitimate quote withdrawal during a TSLA volatility spike. "
                    "No opposite-side profit was realised. Pattern is consistent with "
                    "normal market-maker inventory risk management."
                ),
                "key_factors": [
                    "market_maker_registered=True — cancel rates 70-90% are standard",
                    "Cancel spike coincides with TSLA volatility burst",
                    "No opposite-side execution at elevated price",
                ],
                "recommended_action": "Dismiss. Log for audit trail. No further action required.",
                "is_fp": True,
            }
        )

    await asyncio.sleep(1.5)

    # Also emit any other borderline alerts from non-anomaly traders
    other_alerts = [a for a in alerts
                    if a.trader_id not in ANOMALY_MAIN_TRADERS
                    and a.trader_id not in FP_TRADERS]
    for alert in other_alerts[:2]:  # limit to 2 extra to keep demo tight
        triage = triage_map.get(alert.alert_id)
        yield _sse(_alert_dict(alert))
        await asyncio.sleep(0.4)
        yield _sse({"type": "triaging", "alert_id": alert.alert_id, "trader": alert.trader_id})
        await asyncio.sleep(1.0)
        if triage:
            yield _sse(_verdict_dict(alert, triage, is_fp=True))
        await asyncio.sleep(0.8)

    fp_count = 1 + len([a for a in alerts if a.trader_id not in ANOMALY_MAIN_TRADERS
                        and a.trader_id not in FP_TRADERS])

    # ── Phase 7: MARKET CLOSE ─────────────────────────────────────────────────
    yield _sse({"type": "phase", "name": "MARKET CLOSE", "time": "16:00", "normal": True})

    close_mask = (
        ~scenario_df["trader_id"].isin(anomaly_traders)
        & (scenario_df.get("session", pd.Series(dtype=str)) == "CLOSE")
    )
    close_df = scenario_df[close_mask].head(5)

    # Fallback: if no CLOSE session column, sample last 5 normal events
    if close_df.empty:
        close_df = scenario_df[~scenario_df["trader_id"].isin(anomaly_traders)].tail(5)

    for row in close_df.itertuples(index=False):
        yield _sse(_row_to_trade_dict(row, anomaly=False, anomaly_type=None))
        total_events_shown += 1
        await asyncio.sleep(0.15)

    await asyncio.sleep(0.5)

    # ── Final stats ───────────────────────────────────────────────────────────
    escalated = sum(1 for r in triage_map.values() if r.verdict == "ESCALATE")
    reviewed = sum(1 for r in triage_map.values() if r.verdict == "REVIEW")
    dismissed = sum(1 for r in triage_map.values() if r.verdict == "DISMISS")

    watchlisted = sum(
        1
        for a in alerts
        if a.trader_id in ANOMALY_MAIN_TRADERS
        and triage_map.get(a.alert_id) is not None
        and triage_map[a.alert_id].verdict == "ESCALATE"
    )

    # Cache hit rate from first triage result
    cache_hit_rate = 0.0
    if triage_map:
        first_result = next(iter(triage_map.values()))
        cache_hit_rate = 1.0 if getattr(first_result, "cache_hit", False) else 0.0

    yield _sse(
        {
            "type": "complete",
            "stats": {
                "total_events_shown": total_events_shown,
                "total_alerts": len(alerts),
                "escalated": escalated,
                "reviewed": reviewed,
                "dismissed": dismissed,
                "false_positives_intercepted": fp_count,
                "traders_watchlisted": watchlisted,
                "cache_hit_rate": cache_hit_rate,
            },
        }
    )


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@sim_router.get("/", response_class=HTMLResponse)
async def serve_dashboard() -> HTMLResponse:
    """Serve the HTML dashboard from static/index.html."""
    index_path = STATIC_DIR / "index.html"
    if index_path.exists():
        content = index_path.read_text(encoding="utf-8")
    else:
        content = (
            "<html><body>"
            "<h1>Trade Surveillance Dashboard</h1>"
            "<p>static/index.html not found. Place your dashboard HTML there.</p>"
            "</body></html>"
        )
    return HTMLResponse(content=content)


@sim_router.get("/stream")
async def stream() -> StreamingResponse:
    """SSE endpoint: streams the simulation in real time."""
    return StreamingResponse(
        _generate_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Access-Control-Allow-Origin": "*",
        },
    )


@sim_router.get("/stream/reset")
async def reset_stream() -> dict:
    """Reset the pre-computation cache so the next /stream triggers a fresh run."""
    global _cache
    _cache.update(
        {
            "ready": False,
            "computing": False,
            "scenario_df": None,
            "alerts": [],
            "triage_map": {},
            "profiles": {},
            "error": None,
        }
    )
    return {"status": "reset", "message": "Cache cleared. Next /stream will recompute."}
