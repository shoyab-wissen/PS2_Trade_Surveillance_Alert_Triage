# Escalate Critical Findings

## Objective
Fast-track critical detections to leadership with immediate action triggers.

## Required Reading
- `references/escalation-paths.md` - Critical decision tree
- `references/jira-integration.md` - Expedited ticket workflow

## Process

### Step 1: Validate Critical Threshold
```python
detection = {
    "trader_id": "T12345",
    "manipulation_type": "layering",
    "confidence": 0.94,
    "severity": "critical"
}

# Critical = confidence >= 0.90 AND clear evidence
if detection['confidence'] < 0.90:
    raise ValueError("Confidence below critical threshold")
```

### Step 2: Create Expedited Jira Ticket
```python
from src.workflows.jira_client import JiraClient
from src.config import get_config
from datetime import datetime

config = get_config()
jira_client = JiraClient(
    base_url=config.jira_base_url,
    api_token=config.jira_api_token,
    user_email=config.jira_user_email
)

# Use CRITICAL priority and expedited workflow
critical_ticket = {
    "project": "KAN",
    "issue_type": "Critical Incident",
    "priority": "Highest",
    "summary": f"[CRITICAL] {detection['manipulation_type'].upper()} - {detection['trader_id']}",
    "description": f"""
🚨 CRITICAL MARKET MANIPULATION DETECTED 🚨

*Alleged Violation*: {detection['manipulation_type']}
*Trader*: {detection['trader_id']}
*Confidence*: {detection['confidence']:.0%}
*Detected*: {datetime.now().isoformat()}

*IMMEDIATE ACTIONS REQUIRED*:
1. ⚠️ SUSPEND TRADER TRADING PRIVILEGES (within 1 hour)
2. 📋 Pull complete trade history (last 30 days)
3. 📞 Notify regulatory affairs
4. 📞 Alert compliance officer and general counsel

*Evidence Attached*: See detection-report.json

*Next Escalation*: C-level notification + regulatory notification
    """
}

ticket = jira_client.create_issue(**critical_ticket)
print(f"CRITICAL TICKET CREATED: {ticket.key}")
```

### Step 3: Assign to Senior Reviewer
```python
jira_client.assign_issue(
    issue_key=ticket.key,
    assignee_id=config.jira_l2_assignee_account_id
)

# Mark as urgent
jira_client.add_label(issue_key=ticket.key, label="urgent")
```

### Step 4: Send Critical Slack Alert
```python
from src.workflows.slack_client import SlackClient

slack_client = SlackClient(webhook_url=config.slack_webhook_url)

critical_alert = f"""
🚨🚨🚨 **CRITICAL MARKET MANIPULATION ALERT** 🚨🚨🚨

@compliance-lead @risk-management @general-counsel

**Alleged Violation**: {detection['manipulation_type']}
**Trader ID**: {detection['trader_id']}
**Confidence**: {detection['confidence']:.0%}
**Status**: 🔴 REQUIRES IMMEDIATE ACTION

**Required Actions**:
1. SUSPEND trader trading privileges within 1 hour
2. Review evidence: <https://wissen-hackathon.atlassian.net/browse/{ticket.key}|{ticket.key}>
3. Contact regulatory affairs
4. Prepare C-level notification

**Evidence**: {detection.get('estimated_market_impact', 'TBD')} estimated impact

⏱️ SLA: Decision required within 2 hours
"""

slack_client.send_message(
    channel='#compliance-escalation',
    message=critical_alert,
    priority='urgent'
)
```

### Step 5: Trigger Secondary Escalations
```python
# Log escalation event
escalation_log = {
    "timestamp": datetime.now().isoformat(),
    "ticket_key": ticket.key,
    "trader_id": detection['trader_id'],
    "trigger": "critical_confidence",
    "actions_triggered": [
        "jira_ticket_created",
        "slack_alert_sent",
        "senior_reviewer_assigned"
    ],
    "pending_actions": [
        "trading_suspension",
        "regulatory_notification",
        "c_level_notification"
    ]
}

# Store for audit
import json
with open("critical_escalations.log", "a") as f:
    f.write(json.dumps(escalation_log) + "\n")
```

### Step 6: Set Aggressive Follow-Up
```python
jira_client.set_due_date(
    issue_key=ticket.key,
    due_date="today"  # Immediate
)

jira_client.set_reminder(
    issue_key=ticket.key,
    remind_after_minutes=60,
    reminder_text=f"CRITICAL: Status update required on {ticket.key}"
)
```

## Success Criteria

- ✅ Critical ticket created with Highest priority
- ✅ Assigned to senior investigator
- ✅ Slack alerts sent to leadership
- ✅ Trading suspension workflow initiated
- ✅ Regulatory notification queued
- ✅ 1-hour review SLA set

## Post-Escalation Steps
1. Trading suspension decision within 1 hour
2. Regulatory notification (if warranted)
3. C-level briefing (within 2 hours)
4. Communications strategy (prepared)
5. Documentation and audit trail (complete)

## Critical Definition
- Confidence ≥ 0.90 AND
- Clear, robust evidence AND
- Potential market impact ≥ $100K OR
- Potential regulatory violation with precedent
