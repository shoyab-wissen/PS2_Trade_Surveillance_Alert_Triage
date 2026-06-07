from fastapi import APIRouter, HTTPException, UploadFile, File, Query, Response
from pydantic import BaseModel
from typing import Optional
import io
import pandas as pd
import time
from pathlib import Path

from src.ingestion.schemas import Alert, TriageResult, FeedbackRecord, SEVERITY_RANK
from src.ingestion.loader import load_events
from src.detection.engine import DetectionEngine
from src.triage.claude_client import ClaudeTriageClient
from src.workflows.watchlist import WatchlistManager
from src.workflows.jira_client import JiraClient
from src.workflows.slack_client import SlackClient
from src.workflows.report_generator import ComplianceReportGenerator

REPORTS_DIR = Path(__file__).parent.parent.parent / "reports"
REPORTS_DIR.mkdir(exist_ok=True)


def _save_pdf(alert_id: str, pdf_bytes: bytes) -> str:
    """Save PDF to reports/ and return the file path string."""
    path = REPORTS_DIR / f"case_{alert_id}.pdf"
    path.write_bytes(pdf_bytes)
    print(f"  [PDF] Saved compliance case → {path}")
    return str(path)


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
    simulation_runs: list[dict] = []  # stored simulation run results
    feedback_history: list = []

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

        # 3rd workflow action: PDF compliance case
        pdf_gen = ComplianceReportGenerator()
        pdf_bytes = pdf_gen.generate_case_pdf(alert, result)
        pdf_path = _save_pdf(alert_id, pdf_bytes) if pdf_bytes else None
    else:
        pdf_path = None

    out = result.model_dump()
    if pdf_path:
        out["pdf_report_path"] = pdf_path
        out["pdf_download_url"] = f"/report/case/{alert_id}"
    return out


# ─── Feedback (analyst correction + few-shot learning) ─────────────────────────

class FeedbackBody(BaseModel):
    correct: bool
    analyst_note: str = ""
    corrected_verdict: Optional[str] = None


@router.post("/alerts/{alert_id}/feedback", summary="Submit analyst feedback on a triage verdict")
async def submit_feedback(alert_id: str, body: FeedbackBody):
    alert = next((a for a in AppState.alerts if a.alert_id == alert_id), None)
    if not alert:
        raise HTTPException(404, f"Alert {alert_id} not found")

    triage = AppState.triage_results.get(alert_id)
    if not triage:
        raise HTTPException(400, f"Alert {alert_id} has not been triaged yet — triage it first")

    feedback = FeedbackRecord(
        alert_id=alert_id,
        correct=body.correct,
        analyst_note=body.analyst_note,
        original_verdict=triage.verdict,
        corrected_verdict=body.corrected_verdict,
    )

    # Store in feedback history on AppState
    if not hasattr(AppState, "feedback_history"):
        AppState.feedback_history = []
    AppState.feedback_history.append(feedback)

    # Inject into Claude client's few-shot store for future calls
    claude = get_claude()
    claude.add_feedback(feedback)

    return {
        "alert_id": alert_id,
        "accepted": True,
        "original_verdict": feedback.original_verdict,
        "corrected_verdict": feedback.corrected_verdict,
        "analyst_note": feedback.analyst_note,
        "few_shot_examples_stored": len(claude.feedback_examples),
        "message": (
            "Feedback recorded. Claude will use this as a few-shot example in subsequent triage calls."
            if not body.correct else
            "Verdict confirmed correct. Stored as a positive few-shot example."
        ),
    }
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
            # 3rd workflow: PDF compliance case
            pdf_gen = ComplianceReportGenerator()
            pdf_bytes = pdf_gen.generate_case_pdf(alert, result)
            if pdf_bytes:
                pdf_path = _save_pdf(alert.alert_id, pdf_bytes)
                log_entry["pdf_report_path"] = pdf_path
                log_entry["pdf_download_url"] = f"/report/case/{alert.alert_id}"

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
    triaged_count  = len(AppState.triage_results)
    dismissed      = sum(1 for r in AppState.triage_results.values() if r.verdict == "DISMISS")
    escalated      = sum(1 for r in AppState.triage_results.values() if r.verdict == "ESCALATE")
    fp_suppression = round(100 * dismissed / max(triaged_count, 1), 1)
    return {
        "detection": {
            "total_alerts": len(AppState.alerts),
            "triaged": triaged_count,
            "escalated": escalated,
            "dismissed": dismissed,
            "fp_suppression_rate_pct": fp_suppression,
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

# ─── Cross-alert Trader Investigation ──────────────────────────────────────────────

@router.post("/traders/{trader_id}/investigate",
             summary="Claude cross-alert investigation: are multiple alerts a coordinated scheme?")
async def investigate_trader(trader_id: str):
    trader_alerts = [a for a in AppState.alerts if a.trader_id == trader_id]
    if not trader_alerts:
        raise HTTPException(404, f"No alerts found for trader {trader_id}")

    from src.ingestion.schemas import TraderProfile
    profile = AppState.profiles.get(trader_id, TraderProfile(
        trader_id=trader_id, account_type="unknown", market_maker_registered=False))
    prior = AppState.watchlist.prior_alert_count(trader_id)
    on_wl = AppState.watchlist.is_flagged(trader_id)

    claude = get_claude()
    try:
        result = claude.investigate_trader(
            trader_id, trader_alerts, AppState.profiles, prior, on_wl
        )
    except Exception as e:
        print(f"  [Investigate] Error for {trader_id}: {e}")
        result = {
            "trader_id": trader_id,
            "alerts_analysed": len(trader_alerts),
            "summary": f"Investigation encountered an error: {str(e)}",
            "rationale": f"Error during analysis: {str(e)}",
            "confidence": 0.5,
            "risk_score": 0.5,
            "coordinated_confidence": 0.5,
            "combined_risk_score": 0.5,
            "scheme_type": "REVIEW",
            "escalation_recommendation": "STANDARD",
            "cross_alert_rationale": f"Error: {str(e)}",
            "regulatory_flags": [],
            "recommended_action": "Manual review required due to analysis error.",
            "sections": {},
        }
    return result


# ─── Daily Compliance Report ──────────────────────────────────────────────────

@router.get("/report/daily",
            summary="Claude-generated daily compliance officer narrative report")
async def daily_report():
    if not AppState.triage_results:
        raise HTTPException(400, "No triage results yet — run /demo/run first")

    from datetime import datetime
    triaged_alerts  = [a for a in AppState.alerts if a.alert_id in AppState.triage_results]
    triage_list     = [AppState.triage_results[a.alert_id] for a in triaged_alerts]
    watchlist_status = AppState.watchlist.get_status()
    date_str        = datetime.utcnow().strftime("%Y-%m-%d")

    claude = get_claude()
    report = claude.generate_daily_report(triaged_alerts, triage_list, watchlist_status, date_str)
    return report


# ─── Natural Language Query ───────────────────────────────────────────────────────

class QueryBody(BaseModel):
    question: str


@router.post("/query",
             summary="Natural language alert query — Claude parses question into filters")
async def nl_query(body: QueryBody):
    if not body.question.strip():
        raise HTTPException(400, "question must not be empty")

    claude  = get_claude()
    filters = claude.natural_language_query(body.question)

    # Apply returned filters to in-memory alerts
    alerts = list(AppState.alerts)
    if filters.get("severity"):
        alerts = [a for a in alerts if a.severity == filters["severity"]]
    if filters.get("pattern_type"):
        alerts = [a for a in alerts if a.pattern_type == filters["pattern_type"]]
    if filters.get("trader_id"):
        alerts = [a for a in alerts if a.trader_id == filters["trader_id"]]
    if filters.get("instrument"):
        alerts = [a for a in alerts if a.instrument == filters["instrument"]]
    if filters.get("verdict"):
        alerts = [a for a in alerts
                  if AppState.triage_results.get(a.alert_id, None) is not None
                  and AppState.triage_results[a.alert_id].verdict == filters["verdict"]]
    if filters.get("min_confidence") is not None:
        alerts = [a for a in alerts
                  if AppState.triage_results.get(a.alert_id) is not None
                  and AppState.triage_results[a.alert_id].confidence >= filters["min_confidence"]]
    if filters.get("max_fp_probability") is not None:
        alerts = [a for a in alerts
                  if AppState.triage_results.get(a.alert_id) is not None
                  and AppState.triage_results[a.alert_id].false_positive_probability
                  <= filters["max_fp_probability"]]

    return {
        "question": body.question,
        "interpretation": filters.get("interpretation", ""),
        "filters_applied": filters,
        "result_count": len(alerts),
        "alerts": [_alert_summary(a) for a in alerts],
    }
# ─── PDF Report Download ────────────────────────────────────────────────────────────

@router.get("/report/case/{alert_id}",
            summary="Download the PDF compliance case report for an ESCALATE alert",
            response_class=Response)
async def download_case_pdf(alert_id: str):
    pdf_path = REPORTS_DIR / f"case_{alert_id}.pdf"
    if not pdf_path.exists():
        raise HTTPException(
            404,
            f"No PDF report found for {alert_id}. "
            "Triage the alert first — PDFs are generated only for ESCALATE verdicts."
        )
    return Response(
        content=pdf_path.read_bytes(),
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="case_{alert_id}.pdf"'},
    )

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


# ─── Dashboard API ────────────────────────────────────────────────────────────

@router.post("/api/dashboard/runs",
             summary="Store a simulation run result")
async def store_run(run_data: dict):
    from datetime import datetime
    run_data["timestamp"] = run_data.get("timestamp", datetime.utcnow().isoformat())
    run_data["run_id"] = len(AppState.simulation_runs) + 1
    AppState.simulation_runs.append(run_data)
    return {"status": "stored", "run_id": run_data["run_id"]}


@router.get("/api/dashboard/runs",
            summary="List all simulation runs")
async def list_runs():
    return AppState.simulation_runs


@router.get("/api/dashboard/stats",
            summary="Aggregated dashboard statistics")
async def dashboard_stats():
    runs = AppState.simulation_runs
    total_runs = len(runs)
    total_alerts = sum(r.get("total_alerts", 0) for r in runs)
    total_escalated = sum(r.get("escalated", 0) for r in runs)
    total_reviewed = sum(r.get("reviewed", 0) for r in runs)
    total_dismissed = sum(r.get("dismissed", 0) for r in runs)
    total_fp = sum(r.get("false_positives_intercepted", 0) for r in runs)
    total_watchlisted = sum(r.get("traders_watchlisted", 0) for r in runs)
    total_cost = sum(r.get("cost_usd", 0) for r in runs)
    total_api_calls = sum(r.get("api_calls", 0) for r in runs)

    return {
        "total_runs": total_runs,
        "total_alerts": total_alerts,
        "total_escalated": total_escalated,
        "total_reviewed": total_reviewed,
        "total_dismissed": total_dismissed,
        "total_fp": total_fp,
        "total_watchlisted": total_watchlisted,
        "total_cost": round(total_cost, 6),
        "total_api_calls": total_api_calls,
    }
