# Create Investigation Ticket and Assign

## Objective
Open formal Jira investigation ticket and assign to appropriate investigator.

## Required Reading
- `references/jira-integration.md` - Jira API usage and ticket states
- `references/escalation-paths.md` - Assignment rules by manipulation type

## Process

### Step 1: Initialize Jira Client
```python
from src.workflows.jira_client import JiraClient
from src.config import get_config

config = get_config()
jira_client = JiraClient(
    base_url=config.jira_base_url,
    api_token=config.jira_api_token,
    user_email=config.jira_user_email
)
```

### Step 2: Create Investigation Ticket
```python
from datetime import datetime, timedelta

detection = {
    "trader_id": "T12345",
    "manipulation_type": "layering",
    "confidence": 0.94,
    "severity": "high"
}

ticket_data = {
    "project": config.jira_project_key,  # "KAN"
    "issue_type": "Investigation",
    "summary": f"[{detection['manipulation_type'].upper()}] {detection['trader_id']} - Confidence {detection['confidence']:.0%}",
    "description": f"""
*Alleged Manipulation*: {detection['manipulation_type']}
*Trader ID*: {detection['trader_id']}
*Confidence*: {detection['confidence']:.0%}
*Detection Time*: {datetime.now().isoformat()}

*Detection Evidence*:
- See attached detection report for full evidence

*Next Steps*:
1. Pull full trader trade history
2. Validate detection patterns
3. Interview trader if evidence confirmed
4. Make disposition recommendation
    """,
    "priority": "High",
    "due_date": (datetime.now() + timedelta(days=5)).date().isoformat(),
    "custom_field_trader_id": detection['trader_id'],
    "custom_field_confidence": f"{detection['confidence']:.0%}",
    "labels": [f"manipulation-{detection['manipulation_type']}", "compliance"]
}

ticket = jira_client.create_issue(**ticket_data)
print(f"Created ticket: {ticket.key}")
```

### Step 3: Assign to Investigator
```python
# Determine assignment based on manipulation type and complexity
if detection['confidence'] >= 0.90:
    # Critical: assign to L2 senior investigator
    assignee_id = config.jira_l2_assignee_account_id
else:
    # High: assign to queue (will be picked up in order)
    assignee_id = None

jira_client.assign_issue(
    issue_key=ticket.key,
    assignee_id=assignee_id
)
```

### Step 4: Link to Detection Report
```python
jira_client.attach_file(
    issue_key=ticket.key,
    file_path="detection-report.json",
    comment="Initial detection report from analysis engine"
)
```

### Step 5: Transition to "In Progress"
```python
jira_client.transition_issue(
    issue_key=ticket.key,
    transition_name="Start Investigation"
)
```

## Success Criteria

- ✅ Jira ticket created with full context
- ✅ Appropriate investigator assigned
- ✅ Detection evidence attached
- ✅ Due date set based on severity
- ✅ Ticket ready for investigator action

## Ticket States
- **Created** → Ready for assignment
- **In Progress** → Investigator working
- **Under Review** → Awaiting L2 approval
- **Resolved** → Investigation complete with disposition
- **Closed** → Case documented and archived

## SLA by Severity
- Critical: Review within 2 hours
- High: Review within 24 hours
- Medium: Review within 5 days
