from fastapi import APIRouter, HTTPException, UploadFile, File, Query
from pydantic import BaseModel
from typing import Optional
import io
import pandas as pd
import time

from src.ingestion.schemas import Alert, TriageResult, SEVERITY_RANK
from src.ingestion.loader import load_events
from src.detection.engine import DetectionEngine
from src.triage.claude_client import ClaudeTriageClient
from src.workflows.watchlist import WatchlistManager
from src.workflows.jira_client import JiraClient
from src.workflows.slack_client import SlackClient

router = APIRouter()

# Global app state (populated by lifespan in main.py)
class AppState:
    baseline_df = None
    profiles: dict = {}
    related_accounts: dict = {}
    engine: Optional[DetectionEngine] = None
    watchlist: Optional[WatchlistManager] = None
    alerts: list[Alert] = []
    triage_results: dict[str, TriageResult] = {}

_claude_client: Optional[ClaudeTriageClient] = None

def get_claude():
    global _claude_client
    if _claude_client is None:
        _claude_client = ClaudeTriageClient()
    return _claude_client


# ─── Ingest ────────────────────────────────────────────────────────────────────

@router.post("/ingest", summary="Upload a scenario CSV and run detection")
async def ingest(file: UploadFile = File(...)):
    if AppState.engine is None:
        raise HTTPException(503, "Detection engine not initialised — baseline data missing")

    content = await file.read()
    df = load_events(io.StringIO(content.decode("utf-8")))
    alerts = AppState.engine.run(df)

    existing_ids = {a.alert_id for a in AppState.alerts}
    new_alerts = [a for a in alerts if a.alert_id not in existing_ids]
    AppState.alerts.extend(new_alerts)

    for a in new_alerts:
        AppState.watchlist.record_alert(a.trader_id, a.alert_id)

    return {
        "ingested_events": len(df),
        "new_alerts": len(new_alerts),
        "total_alerts": len(AppState.alerts),
        "alerts": [_alert_summary(a) for a in new_alerts],
    }


# ─── Alerts ────────────────────────────────────────────────────────────────────

@router.get("/alerts", summary="List detected alerts")
async def list_alerts(
    severity: Optional[str] = Query(None, description="Filter by severity: LOW|MEDIUM|HIGH|CRITICAL"),
    pattern: Optional[str] = Query(None, description="Filter by pattern type"),
    triaged: Optional[bool] = Query(None, description="Filter by whether alert has been triaged"),
):
    alerts = AppState.alerts
    if severity:
        alerts = [a for a in alerts if a.severity == severity.upper()]
    if pattern:
        alerts = [a for a in alerts if a.pattern_type == pattern.upper()]
    if triaged is True:
        alerts = [a for a in alerts if a.alert_id in AppState.triage_results]
    if triaged is False:
        alerts = [a for a in alerts if a.alert_id not in AppState.triage_results]

    return {
        "total": len(alerts),
        "alerts": [_alert_summary(a) for a in alerts],
    }


@router.get("/alerts/{alert_id}", summary="Get full alert details with triage result")
async def get_alert(alert_id: str):
    alert = next((a for a in AppState.alerts if a.alert_id == alert_id), None)
    if not alert:
        raise HTTPException(404, f"Alert {alert_id} not found")

    triage = AppState.triage_results.get(alert_id)
    return {
        "alert": alert.model_dump(),
        "triage": triage.model_dump() if triage else None,
        "trader_on_watchlist": AppState.watchlist.is_flagged(alert.trader_id),
    }


@router.post("/alerts/{alert_id}/triage", summary="Triage a specific alert with Claude")
async def triage_alert(alert_id: str):
    alert = next((a for a in AppState.alerts if a.alert_id == alert_id), None)
    if not alert:
        raise HTTPException(404, f"Alert {alert_id} not found")

    from src.ingestion.schemas import TraderProfile
    profile = AppState.profiles.get(alert.trader_id, TraderProfile(
        trader_id=alert.trader_id, account_type="unknown", market_maker_registered=False))

    prior = AppState.watchlist.prior_alert_count(alert.trader_id)
    on_wl = AppState.watchlist.is_flagged(alert.trader_id)

    claude = get_claude()
    result = claude.triage_single(alert, profile, prior, on_wl)
    AppState.triage_results[alert_id] = result
    AppState.watchlist.record_alert(alert.trader_id, alert_id)

    # Trigger workflows
    jira_key = None
    if result.verdict in ("ESCALATE", "REVIEW"):
        jira = JiraClient()
        jira_key = jira.create_ticket(alert, result)
        result.jira_ticket_id = jira_key

    if result.verdict == "ESCALATE":
        slack = SlackClient()
        result.slack_message_sent = slack.send_alert(alert, result, jira_key)
        AppState.watchlist.flag_trader(alert.trader_id, alert.pattern_type, alert_id)

    return result.model_dump()


# ─── Watchlist ─────────────────────────────────────────────────────────────────

@router.get("/watchlist", summary="Current enhanced-monitoring watchlist")
async def get_watchlist():
    return {
        "flagged_traders": AppState.watchlist.get_status(),
        "count": len(AppState.watchlist.get_flagged_set()),
    }


# ─── Demo ──────────────────────────────────────────────────────────────────────

@router.post("/demo/run", summary="Run full end-to-end demo pipeline")
async def run_demo():
    if not AppState.alerts:
        raise HTTPException(400, "No alerts loaded — upload scenario CSV via /ingest first")

    t0 = time.time()
    claude = get_claude()

    prior_counts = {a.trader_id: AppState.watchlist.prior_alert_count(a.trader_id)
                    for a in AppState.alerts}
    watchlist_set = AppState.watchlist.get_flagged_set()

    triage_results = claude.triage_all(
        AppState.alerts, AppState.profiles, prior_counts, watchlist_set)

    jira = JiraClient()
    slack = SlackClient()
    workflow_log = []

    for alert, result in zip(AppState.alerts, triage_results):
        AppState.triage_results[alert.alert_id] = result
        AppState.watchlist.record_alert(alert.trader_id, alert.alert_id)

        log_entry = {
            "alert_id": alert.alert_id,
            "pattern": alert.pattern_type,
            "severity": alert.severity,
            "verdict": result.verdict,
            "confidence": result.confidence,
            "jira_ticket": None,
            "slack_sent": False,
        }

        if result.verdict in ("ESCALATE", "REVIEW"):
            jira_key = jira.create_ticket(alert, result)
            result.jira_ticket_id = jira_key
            log_entry["jira_ticket"] = jira_key

        if result.verdict == "ESCALATE":
            result.slack_message_sent = slack.send_alert(alert, result, result.jira_ticket_id)
            log_entry["slack_sent"] = result.slack_message_sent
            AppState.watchlist.flag_trader(alert.trader_id, alert.pattern_type, alert.alert_id)

        workflow_log.append(log_entry)

    elapsed = time.time() - t0
    metrics = claude.get_metrics()

    return {
        "elapsed_seconds": round(elapsed, 2),
        "alerts_processed": len(AppState.alerts),
        "verdicts": {
            "ESCALATE": sum(1 for r in triage_results if r.verdict == "ESCALATE"),
            "REVIEW": sum(1 for r in triage_results if r.verdict == "REVIEW"),
            "DISMISS": sum(1 for r in triage_results if r.verdict == "DISMISS"),
        },
        "workflow_log": workflow_log,
        "claude_metrics": metrics,
        "watchlist": AppState.watchlist.get_status(),
    }


# ─── Metrics ───────────────────────────────────────────────────────────────────

@router.get("/metrics", summary="Claude API usage and cache metrics")
async def get_metrics():
    claude = get_claude()
    m = claude.get_metrics()
    return {
        "detection": {
            "total_alerts": len(AppState.alerts),
            "triaged": len(AppState.triage_results),
            "by_severity": {
                sev: sum(1 for a in AppState.alerts if a.severity == sev)
                for sev in ("CRITICAL", "HIGH", "MEDIUM", "LOW")
            },
            "by_pattern": {
                pt: sum(1 for a in AppState.alerts if a.pattern_type == pt)
                for pt in ("LAYERING", "WASH_TRADING", "MOMENTUM_IGNITION",
                           "PRICE_RAMPING", "MARKING_CLOSE")
            },
        },
        "claude_api": m,
        "watchlist": {"active_traders": len(AppState.watchlist.get_flagged_set())},
    }


# ─── Helpers ───────────────────────────────────────────────────────────────────

def _alert_summary(alert: Alert) -> dict:
    triage = AppState.triage_results.get(alert.alert_id) if AppState.triage_results else None
    return {
        "alert_id": alert.alert_id,
        "pattern_type": alert.pattern_type,
        "severity": alert.severity,
        "trader_id": alert.trader_id,
        "instrument": alert.instrument,
        "z_score": round(alert.z_score, 2),
        "detected_at": alert.detected_at.isoformat(),
        "triaged": triage is not None,
        "verdict": triage.verdict if triage else None,
    }
