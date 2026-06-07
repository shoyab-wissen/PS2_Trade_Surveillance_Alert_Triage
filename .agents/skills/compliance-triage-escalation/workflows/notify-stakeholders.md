# Notify Stakeholders via Slack

## Objective
Send alert notifications to appropriate Slack channels with actionable information.

## Required Reading
- `references/slack-templates.md` - Message formatting and channel routing
- `references/escalation-paths.md` - Who gets notified for each severity

## Process

### Step 1: Initialize Slack Client
```python
from src.workflows.slack_client import SlackClient
from src.config import get_config

config = get_config()
slack_client = SlackClient(webhook_url=config.slack_webhook_url)
```

### Step 2: Build Alert Message
```python
from datetime import datetime

detection = {
    "detection_id": "DET-001",
    "trader_id": "T12345",
    "manipulation_type": "layering",
    "confidence": 0.94,
    "severity": "critical",
    "jira_key": "KAN-123"
}

message = slack_client.format_alert(
    detection_type=detection['manipulation_type'],
    trader_id=detection['trader_id'],
    confidence=detection['confidence'],
    severity=detection['severity'],
    jira_key=detection['jira_key'],
    timestamp=datetime.now()
)
```

### Step 3: Route to Appropriate Channel
```python
# Route based on severity
channel_routing = {
    'critical': '#compliance-escalation',
    'high': '#compliance-alerts',
    'medium': '#compliance-queue',
    'low': None  # Don't notify for low severity
}

channel = channel_routing.get(detection['severity'])

if channel:
    slack_client.send_message(
        channel=channel,
        message=message,
        thread_reply=False
    )
```

### Step 4: Notify Specific Teams (Critical Only)
```python
if detection['severity'] == 'critical':
    # Alert compliance lead and management
    mentions = ['@compliance-lead', '@risk-management']
    critical_message = f"{', '.join(mentions)} {message}"
    
    slack_client.send_message(
        channel='#compliance-escalation',
        message=critical_message,
        priority='urgent'
    )
```

### Step 5: Log Notification
```python
notification_log = {
    "detection_id": detection['detection_id'],
    "timestamp": datetime.now().isoformat(),
    "channel": channel,
    "severity": detection['severity'],
    "message_id": None  # Return value from slack_client
}

# Log for audit trail
import json
with open("notifications.log", "a") as f:
    f.write(json.dumps(notification_log) + "\n")
```

## Success Criteria

- ✅ Alert sent to appropriate channel(s)
- ✅ Message includes actionable next steps
- ✅ Jira ticket linked (if created)
- ✅ Evidence summary included
- ✅ Notification logged for audit

## Message Templates

**Critical Alert** (with @mentions):
```
:alert: CRITICAL MANIPULATION DETECTED

**Type**: Layering
**Trader**: T12345
**Confidence**: 94%
**Evidence**: 18.5x order-to-trade ratio (peer median: 2.1x)

**Action**: Jira ticket KAN-123 created, assigned to L2
**Next**: Review evidence → Interview trader → Suspension decision

:link: [View in Jira](https://wissen-hackathon.atlassian.net/browse/KAN-123)
```

**High Alert**:
```
:warning: High-confidence manipulation finding

Type: Wash Trading
Trader: T67890
Confidence: 87%

Jira: KAN-124
Status: Awaiting investigator assignment
```

**Medium Alert**:
```
:mag: Medium-confidence finding added to investigation queue

Type: Momentum Ignition
Trader: T99999
Confidence: 68%

SLA: Review within 5 days
```
