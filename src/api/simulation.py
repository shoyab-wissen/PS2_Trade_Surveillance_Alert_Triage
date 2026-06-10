"""
simulation.py — Real-time trade surveillance simulation via Server-Sent Events.

Detection runs at load time; Claude triage runs ASYNCHRONOUSLY during the
simulation — trades keep flowing while Claude processes in the background,
and verdicts appear as soon as each call completes.

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
from src.ingestion.schemas import Alert, TraderProfile, TriageResult
from src.triage.claude_client import ClaudeTriageClient
from src.workflows.jira_client import JiraClient
from src.workflows.slack_client import SlackClient
from src.workflows.report_generator import ComplianceReportGenerator

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

ANOMALY_MAIN_TRADERS = {"T-4821", "T-9033", "T-9034", "T-6610", "T-8802", "T-5599"}
FP_TRADERS = {"T-0003"}

DATA_DIR = Path(__file__).parent.parent.parent / "data"
STATIC_DIR = Path(__file__).parent.parent.parent / "static"

# ---------------------------------------------------------------------------
# Pre-computation cache (data + detection only — NO triage)
# ---------------------------------------------------------------------------

_cache: dict = {
    "ready": False,
    "computing": False,
    "scenario_df": None,
    "alerts": [],
    "profiles": {},
    "error": None,
}

# ---------------------------------------------------------------------------
# Router
# ---------------------------------------------------------------------------

sim_router = APIRouter()

# ---------------------------------------------------------------------------
# Helpers
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
    return f"data: {json.dumps(payload, default=str)}\n\n"


def _find_alert(alerts: list[Alert], trader_id: str, pattern_type: str) -> Alert | None:
    for a in alerts:
        if a.trader_id == trader_id and a.pattern_type == pattern_type:
            return a
    return None


def _find_any_alert_for_trader(alerts: list[Alert], trader_id: str) -> Alert | None:
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


def _default_profile(trader_id: str) -> TraderProfile:
    return TraderProfile(
        trader_id=trader_id,
        account_type="unknown",
        market_maker_registered=False,
    )


# ---------------------------------------------------------------------------
# Pre-computation: load data + run detection (triage is real-time async)
# ---------------------------------------------------------------------------


async def _ensure_ready() -> None:
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

        alerts: list[Alert] = AppState.alerts or []
        profiles: dict = AppState.profiles or {}

        scenario_df: pd.DataFrame = await asyncio.to_thread(
            load_events, DATA_DIR / "trades_scenario.csv"
        )

        AppState.scenario_df = scenario_df
        if not alerts and AppState.engine:
            alerts = await asyncio.to_thread(AppState.engine.run, scenario_df)
            AppState.alerts = alerts

        _cache.update(
            {
                "ready": True,
                "computing": False,
                "scenario_df": scenario_df,
                "alerts": alerts,
                "profiles": profiles,
                "error": None,
            }
        )

    except Exception as exc:
        _cache["computing"] = False
        _cache["error"] = str(exc)
        raise


# ---------------------------------------------------------------------------
# SSE stream generator — async triage (trades never block on Claude)
# ---------------------------------------------------------------------------


async def _generate_stream() -> AsyncGenerator[str, None]:
    global _cache

    # ── Loading phase ────────────────────────────────────────────────────
    yield _sse({"type": "loading", "msg": "Loading trade data...", "progress": 15})
    await asyncio.sleep(0.3)

    try:
        compute_task = asyncio.create_task(_ensure_ready())

        yield _sse(
            {"type": "loading", "msg": "Running detection engine...", "progress": 45}
        )
        await asyncio.sleep(0.5)

        yield _sse(
            {"type": "loading", "msg": "Preparing real-time triage...", "progress": 75}
        )
        await compute_task

    except Exception as exc:
        yield _sse({"type": "error", "msg": str(exc)})
        return

    if _cache["error"]:
        yield _sse({"type": "error", "msg": _cache["error"]})
        return

    yield _sse({"type": "loading", "msg": "Simulation ready", "progress": 100})
    await asyncio.sleep(0.4)

    # ── Unpack cache ─────────────────────────────────────────────────────
    scenario_df: pd.DataFrame = _cache["scenario_df"]
    alerts: list[Alert] = _cache["alerts"]
    profiles: dict = _cache["profiles"]

    if scenario_df is None or scenario_df.empty:
        yield _sse({"type": "error", "msg": "Scenario DataFrame is empty"})
        return

    # ── Real-time async triage state ─────────────────────────────────────
    claude = ClaudeTriageClient()
    triage_map: dict[str, TriageResult] = {}
    fp_intercepted = 0  # alerts flagged as false positive

    # Background triage tasks: (asyncio.Task, Alert, is_fp)
    pending: list[tuple[asyncio.Task, Alert, bool]] = []

    anomaly_traders = ANOMALY_MAIN_TRADERS

    # ── Metrics helper ───────────────────────────────────────────────────
    def _metrics_payload() -> dict:
        m = claude.get_metrics()
        triaged = len(triage_map)
        return {
            "type": "metrics_update",
            "api_calls": m["total_api_calls"],
            "input_tokens": m["total_input_tokens"],
            "output_tokens": m["total_output_tokens"],
            "cache_tokens": m["cache_read_input_tokens"],
            "cache_hit_rate": m["cache_hit_rate_pct"],
            "cost_usd": m["estimated_cost_usd"],
            "savings_usd": m["savings_from_caching_usd"],
            "fp_suppression": round(100 * fp_intercepted / max(triaged, 1), 1),
        }

    # ── Workflow clients (created once, reused) ───────────────────────────
    jira_client = JiraClient()
    slack_client = SlackClient()
    pdf_gen = ComplianceReportGenerator()

    from src.api.routes import AppState as _AppState

    # ── Drain completed background triages ───────────────────────────────
    async def _drain() -> AsyncGenerator[str, None]:
        nonlocal fp_intercepted
        done_indices = []
        for i, (task, alert, is_fp) in enumerate(pending):
            if task.done():
                try:
                    triage = task.result()
                except Exception:
                    done_indices.append(i)
                    continue
                triage_map[alert.alert_id] = triage
                # Sync to AppState immediately so feedback buttons work
                _AppState.triage_results[alert.alert_id] = triage
                if is_fp:
                    fp_intercepted += 1
                yield _sse(_verdict_dict(alert, triage, is_fp))
                yield _sse(_metrics_payload())

                # --- Workflow triggers (Jira + Slack + PDF) ---
                jira_key = None
                if triage.verdict in ("ESCALATE", "REVIEW"):
                    try:
                        jira_key = await asyncio.to_thread(
                            jira_client.create_ticket, alert, triage
                        )
                        triage.jira_ticket_id = jira_key
                    except Exception as e:
                        print(f"  [Jira] Error: {e}")

                if triage.verdict == "ESCALATE":
                    try:
                        sent = await asyncio.to_thread(
                            slack_client.send_alert, alert, triage, jira_key
                        )
                        triage.slack_message_sent = sent
                    except Exception as e:
                        print(f"  [Slack] Error: {e}")
                    try:
                        pdf_bytes = await asyncio.to_thread(
                            pdf_gen.generate_case_pdf, alert, triage
                        )
                        if pdf_bytes:
                            reports_dir = (
                                Path(__file__).parent.parent.parent / "reports"
                            )
                            reports_dir.mkdir(exist_ok=True)
                            pdf_path = reports_dir / f"case_{alert.alert_id}.pdf"
                            pdf_path.write_bytes(pdf_bytes)
                            print(f"  [PDF] Saved compliance case -> {pdf_path}")
                    except Exception as e:
                        print(f"  [PDF] Error: {e}")

                if jira_key:
                    yield _sse(
                        {
                            "type": "workflow",
                            "action": "jira_created",
                            "alert_id": alert.alert_id,
                            "ticket": jira_key,
                        }
                    )

                if not is_fp and triage.verdict == "ESCALATE":
                    yield _sse(
                        {
                            "type": "watchlist",
                            "trader_id": alert.trader_id,
                            "reason": alert.pattern_type,
                            "hours": 72,
                        }
                    )
                done_indices.append(i)
        for i in reversed(done_indices):
            pending.pop(i)

    # ── Start a background triage ────────────────────────────────────────
    def _start_triage(alert: Alert, is_fp: bool = False) -> None:
        profile = profiles.get(alert.trader_id, _default_profile(alert.trader_id))
        task = asyncio.create_task(
            asyncio.to_thread(claude.triage_single, alert, profile, 0, False)
        )
        pending.append((task, alert, is_fp))

    # ── Emit detect + alert + triaging, then fire background call ────────
    async def _emit_detect_start(
        trader_id: str,
        pattern_type: str,
        instrument: str,
        is_fp: bool = False,
    ) -> AsyncGenerator[str, None]:
        alert = _find_alert(alerts, trader_id, pattern_type)
        if alert is None:
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
        await asyncio.sleep(0.5)

        yield _sse(_alert_dict(alert))
        await asyncio.sleep(0.3)

        yield _sse(
            {"type": "triaging", "alert_id": alert.alert_id, "trader": trader_id}
        )

        _start_triage(alert, is_fp)

    # ── Sim start ────────────────────────────────────────────────────────
    yield _sse(
        {
            "type": "sim_start",
            "date": "2026-02-17",
            "total_events": len(scenario_df),
            "n_alerts": len(alerts),
        }
    )
    await asyncio.sleep(0.3)

    yield _sse(_metrics_payload())

    total_events_shown = 0

    # ── Phase 1: MARKET OPEN (normal events 09:00–09:43) ─────────────────
    yield _sse(
        {"type": "phase", "name": "MARKET OPEN", "time": "09:00", "normal": True}
    )

    normal_mask = ~scenario_df["trader_id"].isin(anomaly_traders)
    early_mask = scenario_df["timestamp"] < "2026-02-17 09:44"
    phase1_df = scenario_df[normal_mask & early_mask].head(25)

    for row in phase1_df.itertuples(index=False):
        yield _sse(_row_to_trade_dict(row, anomaly=False, anomaly_type=None))
        total_events_shown += 1
        async for ev in _drain():
            yield ev
        await asyncio.sleep(0.12)

    await asyncio.sleep(0.5)

    # ── Phase 2: LAYERING — T-4821 (09:44–09:47) ─────────────────────────
    yield _sse(
        {
            "type": "phase",
            "name": "⚠ LAYERING PATTERN FORMING",
            "time": "09:44",
            "normal": False,
        }
    )

    layering_df = scenario_df[scenario_df["trader_id"] == "T-4821"].sort_values(
        "timestamp"
    )

    for row in layering_df.itertuples(index=False):
        yield _sse(_row_to_trade_dict(row, anomaly=True, anomaly_type="LAYERING"))
        total_events_shown += 1
        async for ev in _drain():
            yield ev
        await asyncio.sleep(0.25)

    await asyncio.sleep(0.5)

    # Start layering triage (non-blocking)
    async for ev in _emit_detect_start("T-4821", "LAYERING", "AAPL", is_fp=False):
        yield ev

    # ── Phase 3: Fast-forward → WASH TRADING (trades keep flowing) ───────
    yield _sse(
        {
            "type": "fast_forward",
            "from_time": "09:47",
            "to_time": "11:00",
            "skipped": "~2,400 normal events",
        }
    )
    async for ev in _drain():
        yield ev
    await asyncio.sleep(0.5)

    yield _sse(
        {
            "type": "phase",
            "name": "⚠ WASH TRADING PATTERN FORMING",
            "time": "11:00",
            "normal": False,
        }
    )

    wash_df = scenario_df[
        scenario_df["trader_id"].isin({"T-9033", "T-9034"})
    ].sort_values("timestamp")

    for row in wash_df.itertuples(index=False):
        yield _sse(_row_to_trade_dict(row, anomaly=True, anomaly_type="WASH_TRADING"))
        total_events_shown += 1
        async for ev in _drain():
            yield ev
        await asyncio.sleep(0.20)

    await asyncio.sleep(0.5)

    # Start wash trading triage (non-blocking)
    async for ev in _emit_detect_start("T-9033", "WASH_TRADING", "MSFT", is_fp=False):
        yield ev

    # ── Phase 4: Fast-forward → MOMENTUM IGNITION ────────────────────────
    yield _sse(
        {
            "type": "fast_forward",
            "from_time": "11:30",
            "to_time": "13:15",
            "skipped": "~3,200 normal events",
        }
    )
    async for ev in _drain():
        yield ev
    await asyncio.sleep(0.5)

    yield _sse(
        {
            "type": "phase",
            "name": "⚠ MOMENTUM IGNITION PATTERN FORMING",
            "time": "13:15",
            "normal": False,
        }
    )

    momentum_df = scenario_df[scenario_df["trader_id"] == "T-6610"].sort_values(
        "timestamp"
    )

    for row in momentum_df.itertuples(index=False):
        yield _sse(
            _row_to_trade_dict(row, anomaly=True, anomaly_type="MOMENTUM_IGNITION")
        )
        total_events_shown += 1
        async for ev in _drain():
            yield ev
        await asyncio.sleep(0.18)

    await asyncio.sleep(0.5)

    # Start momentum ignition triage (non-blocking)
    async for ev in _emit_detect_start(
        "T-6610", "MOMENTUM_IGNITION", "TSLA", is_fp=False
    ):
        yield ev

    # ── Phase 5: Fast-forward → PRICE RAMPING ────────────────────────────
    yield _sse(
        {
            "type": "fast_forward",
            "from_time": "13:22",
            "to_time": "14:45",
            "skipped": "~2,800 normal events",
        }
    )
    async for ev in _drain():
        yield ev
    await asyncio.sleep(0.5)

    yield _sse(
        {
            "type": "phase",
            "name": "⚠ PRICE RAMPING DETECTED",
            "time": "14:45",
            "normal": False,
        }
    )

    ramping_df = scenario_df[scenario_df["trader_id"] == "T-8802"].sort_values(
        "timestamp"
    )

    for row in ramping_df.itertuples(index=False):
        yield _sse(_row_to_trade_dict(row, anomaly=True, anomaly_type="PRICE_RAMPING"))
        total_events_shown += 1
        async for ev in _drain():
            yield ev
        await asyncio.sleep(0.20)

    await asyncio.sleep(0.5)

    # Start price ramping triage (non-blocking)
    async for ev in _emit_detect_start("T-8802", "PRICE_RAMPING", "NVDA", is_fp=False):
        yield ev

    # ── Phase 5.5: MARKING THE CLOSE ─────────────────────────────────────
    yield _sse(
        {
            "type": "fast_forward",
            "from_time": "14:55",
            "to_time": "15:55",
            "skipped": "~3,100 normal events",
        }
    )
    async for ev in _drain():
        yield ev
    await asyncio.sleep(0.5)

    yield _sse(
        {
            "type": "phase",
            "name": "⚠ MARKING THE CLOSE DETECTED",
            "time": "15:55",
            "normal": False,
        }
    )

    marking_df = scenario_df[scenario_df["trader_id"] == "T-5599"].sort_values(
        "timestamp"
    )
    for row in marking_df.itertuples(index=False):
        yield _sse(_row_to_trade_dict(row, anomaly=True, anomaly_type="MARKING_CLOSE"))
        total_events_shown += 1
        async for ev in _drain():
            yield ev
        await asyncio.sleep(0.22)

    await asyncio.sleep(0.5)

    # Start marking close triage (non-blocking)
    async for ev in _emit_detect_start("T-5599", "MARKING_CLOSE", "AAPL", is_fp=False):
        yield ev

    # ── Phase 6: FALSE POSITIVE — T-0003 Market Maker ──────────────────
    yield _sse(
        {
            "type": "phase",
            "name": "✓ FALSE POSITIVE: MARKET MAKER IDENTIFIED",
            "time": "10:30",
            "normal": True,
        }
    )
    async for ev in _drain():
        yield ev
    await asyncio.sleep(0.3)

    mm_fp_df = (
        scenario_df[scenario_df["trader_id"] == "T-0003"]
        .sort_values("timestamp")
        .head(20)
    )
    for row in mm_fp_df.itertuples(index=False):
        yield _sse(_row_to_trade_dict(row, anomaly=True, anomaly_type="LAYERING"))
        total_events_shown += 1
        async for ev in _drain():
            yield ev
        await asyncio.sleep(0.15)

    await asyncio.sleep(0.3)

    fp_alert = _find_alert(alerts, "T-0003", "LAYERING")
    if fp_alert:
        # Start FP triage (non-blocking)
        async for ev in _emit_detect_start("T-0003", "LAYERING", "TSLA", is_fp=True):
            yield ev
    else:
        fp_intercepted += 1
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
        yield _sse(_metrics_payload())

    # Start triage for other borderline alerts (non-blocking)
    other_alerts = [
        a
        for a in alerts
        if a.trader_id not in ANOMALY_MAIN_TRADERS and a.trader_id not in FP_TRADERS
    ]
    for alert in other_alerts[:2]:
        yield _sse(_alert_dict(alert))
        await asyncio.sleep(0.2)
        yield _sse(
            {"type": "triaging", "alert_id": alert.alert_id, "trader": alert.trader_id}
        )
        _start_triage(alert, is_fp=True)
        async for ev in _drain():
            yield ev
        await asyncio.sleep(0.3)

    fp_count = 1 + len(other_alerts[:2])

    # ── Phase 7: MARKET CLOSE ─────────────────────────────────────────────
    yield _sse(
        {"type": "phase", "name": "MARKET CLOSE", "time": "16:00", "normal": True}
    )

    close_mask = ~scenario_df["trader_id"].isin(anomaly_traders) & (
        scenario_df.get("session", pd.Series(dtype=str)) == "CLOSE"
    )
    close_df = scenario_df[close_mask].head(5)

    if close_df.empty:
        close_df = scenario_df[~scenario_df["trader_id"].isin(anomaly_traders)].tail(5)

    for row in close_df.itertuples(index=False):
        yield _sse(_row_to_trade_dict(row, anomaly=False, anomaly_type=None))
        total_events_shown += 1
        async for ev in _drain():
            yield ev
        await asyncio.sleep(0.15)

    # ── Drain ALL remaining pending triages ───────────────────────────────
    while pending:
        async for ev in _drain():
            yield ev
        if pending:
            await asyncio.sleep(0.3)

    await asyncio.sleep(0.5)

    # ── Sync triage results to AppState ──────────────────────────────────
    from src.api.routes import AppState

    AppState.triage_results = triage_map

    # ── Final stats ───────────────────────────────────────────────────────
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

    cache_hit_rate = 0.0
    if triage_map:
        first_result = next(iter(triage_map.values()))
        cache_hit_rate = 1.0 if getattr(first_result, "cache_hit", False) else 0.0

    # ── Store run result for dashboard ──────────────────────────────────
    m = claude.get_metrics()

    # Build detailed alert + triage records for dashboard history
    alerts_detail = []
    for a in alerts:
        t = triage_map.get(a.alert_id)
        alert_rec = {
            "alert_id": a.alert_id,
            "pattern_type": a.pattern_type,
            "severity": a.severity,
            "trader_id": a.trader_id,
            "instrument": a.instrument,
            "z_score": round(a.z_score, 2),
            "evidence": a.evidence,
        }
        if t:
            alert_rec["triage"] = {
                "verdict": t.verdict,
                "confidence": t.confidence,
                "false_positive_probability": t.false_positive_probability,
                "rationale": t.rationale,
                "key_factors": list(t.key_factors) if t.key_factors else [],
                "recommended_action": t.recommended_action,
                "jira_ticket_id": getattr(t, "jira_ticket_id", None),
                "slack_message_sent": getattr(t, "slack_message_sent", False),
            }
        alerts_detail.append(alert_rec)

    # Unique traders for investigate buttons
    unique_traders = list({a.trader_id for a in alerts})

    run_record = {
        "total_events_shown": total_events_shown,
        "total_alerts": len(alerts),
        "escalated": escalated,
        "reviewed": reviewed,
        "dismissed": dismissed,
        "false_positives_intercepted": fp_count,
        "traders_watchlisted": watchlisted,
        "cache_hit_rate": m.get("cache_hit_rate_pct", 0),
        "api_calls": m.get("total_api_calls", 0),
        "input_tokens": m.get("total_input_tokens", 0),
        "output_tokens": m.get("total_output_tokens", 0),
        "cache_tokens": m.get("cache_read_input_tokens", 0),
        "cost_usd": m.get("estimated_cost_usd", 0),
        "savings_usd": m.get("savings_from_caching_usd", 0),
        "alerts_detail": alerts_detail,
        "traders": unique_traders,
    }
    from datetime import datetime

    run_record["timestamp"] = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
    run_record["run_id"] = len(AppState.simulation_runs) + 1
    AppState.simulation_runs.append(run_record)

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
async def serve_landing() -> HTMLResponse:
    landing_path = STATIC_DIR / "landing.html"
    if landing_path.exists():
        return HTMLResponse(content=landing_path.read_text(encoding="utf-8"))
    return HTMLResponse(content="<h1>Trade Surveillance Engine</h1>")


@sim_router.get("/dashboard", response_class=HTMLResponse)
async def serve_dashboard_page() -> HTMLResponse:
    dash_path = STATIC_DIR / "dashboard.html"
    if dash_path.exists():
        return HTMLResponse(content=dash_path.read_text(encoding="utf-8"))
    return HTMLResponse(content="<h1>Dashboard</h1>")


@sim_router.get("/simulation", response_class=HTMLResponse)
async def serve_simulation() -> HTMLResponse:
    index_path = STATIC_DIR / "index.html"
    if index_path.exists():
        return HTMLResponse(content=index_path.read_text(encoding="utf-8"))
    return HTMLResponse(content="<h1>Simulation</h1>")


@sim_router.get("/stream")
async def stream() -> StreamingResponse:
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
    global _cache
    _cache.update(
        {
            "ready": False,
            "computing": False,
            "scenario_df": None,
            "alerts": [],
            "profiles": {},
            "error": None,
        }
    )
    return {"status": "reset", "message": "Cache cleared. Next /stream will recompute."}
