#!/usr/bin/env python3
"""
Trade Surveillance & Alert Triage Engine — End-to-End Demo
Runs the full pipeline: ingest → detect → triage → escalate
"""
import sys
import os
import time
from pathlib import Path

# Add project root to path
PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import pandas as pd


def main():
    print("=" * 70)
    print("  TRADE SURVEILLANCE & ALERT TRIAGE ENGINE — LIVE DEMO")
    print("=" * 70)

    # -------------------------------------------------------------------------
    # Step 1: Load data
    # -------------------------------------------------------------------------
    print("\n[STEP 1] Loading trade data...")
    from src.ingestion.loader import load_events, load_trader_profiles, load_related_accounts

    baseline_path = PROJECT_ROOT / "data" / "trades_baseline.csv"
    scenario_path = PROJECT_ROOT / "data" / "trades_scenario.csv"

    if not baseline_path.exists() or not scenario_path.exists():
        print("  Data not found. Running generate_data.py first...")
        import subprocess
        subprocess.run(
            [sys.executable, str(PROJECT_ROOT / "data" / "generate_data.py")],
            check=True,
            cwd=str(PROJECT_ROOT),
        )

    baseline_df = load_events(baseline_path)
    scenario_df = load_events(scenario_path)
    profiles = load_trader_profiles()
    related_accounts = load_related_accounts()

    print(f"  Baseline loaded: {len(baseline_df):,} events over 30 days")
    print(f"  Scenario loaded: {len(scenario_df):,} events (1 day + anomalies)")
    print(f"  Trader profiles: {len(profiles)} traders")

    # -------------------------------------------------------------------------
    # Step 2: Detection
    # -------------------------------------------------------------------------
    print("\n[STEP 2] Running pattern detection...")
    t0 = time.time()
    from src.detection.engine import DetectionEngine

    engine = DetectionEngine(baseline_df, related_accounts)
    alerts = engine.run(scenario_df)
    elapsed = time.time() - t0

    print(f"\n  Detection completed in {elapsed:.2f}s")
    if alerts:
        print(f"  {'='*60}")
        print(f"  {'Alert ID':<20} {'Pattern':<22} {'Inst':<6} {'Trader':<8} {'Sev':<10} {'Z-Score'}")
        print(f"  {'-'*60}")
        for alert in alerts:
            print(
                f"  {alert.alert_id:<20} {alert.pattern_type:<22} {alert.instrument:<6} "
                f"{alert.trader_id:<8} {alert.severity:<10} +{alert.z_score:.1f}σ"
            )
    else:
        print("  No alerts detected. Check that anomalies were injected correctly.")
        return

    # -------------------------------------------------------------------------
    # Step 3: Claude Triage
    # -------------------------------------------------------------------------
    print(f"\n[STEP 3] AI Triage with Claude ({len(alerts)} alert(s))...")

    from src.triage.claude_client import ClaudeTriageClient
    from src.workflows.watchlist import WatchlistManager

    watchlist = WatchlistManager()
    for alert in alerts:
        watchlist.record_alert(alert.trader_id, alert.alert_id)

    client = ClaudeTriageClient()
    prior_counts = {a.trader_id: watchlist.prior_alert_count(a.trader_id) for a in alerts}
    watchlist_set = watchlist.get_flagged_set()

    triage_results = client.triage_all(alerts, profiles, prior_counts, watchlist_set)

    print(f"\n  {'='*70}")
    print(f"  {'Alert ID':<20} {'Verdict':<12} {'Confidence':<12} {'FP Prob':<10} {'Cache'}")
    print(f"  {'-'*70}")
    for result in triage_results:
        cache_str = "HIT" if result.cache_hit else "MISS"
        print(
            f"  {result.alert_id:<20} {result.verdict:<12} "
            f"{result.confidence * 100:.0f}%{'':>8} "
            f"{result.false_positive_probability * 100:.0f}%{'':>5} {cache_str}"
        )

    # Detailed rationale for each result
    print()
    for alert, result in zip(alerts, triage_results):
        print(f"\n  --- {alert.alert_id} ({alert.pattern_type}) ---")
        print(f"  Verdict: {result.verdict} | Confidence: {result.confidence * 100:.0f}%")
        print(f"  Rationale: {result.rationale}")
        if result.key_factors:
            print(f"  Key factors:")
            for f in result.key_factors:
                print(f"    - {f}")
        print(f"  Action: {result.recommended_action}")

    # -------------------------------------------------------------------------
    # Step 4: Automated Workflows (Jira + Slack + Watchlist)
    # -------------------------------------------------------------------------
    print(f"\n[STEP 4] Triggering automated workflows...")
    from src.workflows.jira_client import JiraClient
    from src.workflows.slack_client import SlackClient

    jira = JiraClient()
    slack = SlackClient()

    workflow_results: list[tuple] = []
    for alert, result in zip(alerts, triage_results):
        if result.verdict == "DISMISS":
            print(f"  {alert.alert_id}: DISMISSED — no workflow actions")
            continue

        jira_key = None
        if result.verdict in ("ESCALATE", "REVIEW"):
            print(f"  {alert.alert_id}: Creating Jira ticket ({result.verdict})...")
            jira_key = jira.create_ticket(alert, result)
            result.jira_ticket_id = jira_key

        if result.verdict == "ESCALATE":
            print(f"  {alert.alert_id}: Sending Slack alert...")
            result.slack_message_sent = slack.send_alert(alert, result, jira_key)
            watchlist.flag_trader(alert.trader_id, alert.pattern_type, alert.alert_id)

        workflow_results.append((alert, result, jira_key))

    # -------------------------------------------------------------------------
    # Step 5: Summary
    # -------------------------------------------------------------------------
    escalated = sum(1 for r in triage_results if r.verdict == "ESCALATE")
    reviewed = sum(1 for r in triage_results if r.verdict == "REVIEW")
    dismissed = sum(1 for r in triage_results if r.verdict == "DISMISS")
    metrics = client.get_metrics()

    print(f"\n{'='*70}")
    print(f"  DEMO COMPLETE — SUMMARY")
    print(f"{'='*70}")
    print(f"  Alerts detected:      {len(alerts)}")
    print(f"  ESCALATE:             {escalated}")
    print(f"  REVIEW:               {reviewed}")
    print(f"  DISMISS:              {dismissed}")
    print(f"  Traders watchlisted:  {len(watchlist.get_flagged_set())}")
    print(f"\n  Claude API Usage:")
    print(f"    Total API calls:      {metrics['total_api_calls']}")
    print(f"    Input tokens:         {metrics['total_input_tokens']:,}")
    print(f"    Output tokens:        {metrics['total_output_tokens']:,}")
    print(f"    Cache read tokens:    {metrics['cache_read_input_tokens']:,}")
    print(f"    Cache hit rate:       {metrics['cache_hit_rate_pct']}%")

    jira_keys = [r.jira_ticket_id for _, r, _ in workflow_results if r.jira_ticket_id]
    if jira_keys:
        print(f"\n  Jira tickets created: {', '.join(jira_keys)}")

    watchlist_status = watchlist.get_status()
    if watchlist_status:
        print(f"\n  Active watchlist entries:")
        for entry in watchlist_status:
            print(f"    - {entry['trader_id']}: {entry['reason']} "
                  f"(expires in {entry['hours_remaining']}h)")

    print(f"{'='*70}\n")


if __name__ == "__main__":
    main()
