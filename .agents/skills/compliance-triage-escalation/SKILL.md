---
name: compliance-triage-escalation
description: Expert in classifying compliance alerts, routing to appropriate teams, and managing escalations via Jira and Slack. Use when triaging detection findings, assigning investigations, and communicating alerts to stakeholders.
---

<objective>Classify market abuse alerts, assign investigations, and escalate critical findings to stakeholders through integrated Jira and Slack workflows.</objective>

<essential_principles>

## Triage Classification System

1. **Severity-Based Routing**: High-confidence detections route to L2 review, critical findings trigger immediate escalation
2. **Action Mapping**: Each severity level has defined next steps (monitor, investigate, suspend, escalate)
3. **Stakeholder Notification**: Appropriate teams notified via Slack with actionable summaries
4. **Jira Integration**: Formal tracking for audit trail and SLA management
5. **Evidence Chain**: Every alert linked to source detection with full traceability

## Escalation Levels

- **CRITICAL**: Immediate suspension + C-level notification + regulatory notification
- **HIGH**: L2 investigation assignment + Slack alert + Jira ticket
- **MEDIUM**: Investigation queue + Low priority Slack notification
- **LOW**: Monitoring alert + Archive in watchlist
- **INFO**: Reference data only

## Workflow Integration Points

1. **Detection Input**: Receives findings from market-manipulation-detection skill
2. **Jira Management**: Create tickets, assign to investigators, track status
3. **Slack Notifications**: Alert stakeholders, provide action summaries
4. **Decision Logging**: Decisions documented for audit and ML training
5. **Feedback Loop**: Investigation results fed back to detection engine

</essential_principles>

<intake>
What triage action would you like to perform?

1. **Triage detections** - Classify alerts and route to teams
2. **Create investigation** - Open Jira ticket and assign to investigator
3. **Notify stakeholders** - Send Slack alert with findings
4. **Manage watchlist** - Add trader to monitoring watchlist
5. **Escalate critical** - Fast-track critical finding to leadership
6. **Get guidance** - Understand triage workflow

**Select option or describe your triage need.**
</intake>

<routing>

| Response | Workflow |
|----------|----------|
| 1, "triage", "classify", "route" | workflows/triage-detections.md |
| 2, "investigation", "create", "ticket" | workflows/create-investigation.md |
| 3, "notify", "alert", "slack" | workflows/notify-stakeholders.md |
| 4, "watchlist", "monitor", "add" | workflows/manage-watchlist.md |
| 5, "critical", "escalate", "urgent" | workflows/escalate-critical.md |
| 6, "guidance", "workflow", "help" | workflows/triage-guidance.md |

**After selecting, follow the workflow exactly.**

</routing>

<references_preview>
See `references/` for:
- **severity-mapping.md** - How confidence maps to severity
- **jira-integration.md** - Ticket templates and workflow states
- **slack-templates.md** - Alert message formats and @mentions
- **escalation-paths.md** - Decision trees for each finding type

</references_preview>

<quick_reference>

**Key Integration Files in Project:**
- `src/workflows/jira_client.py` - Jira ticket creation and management
- `src/workflows/slack_client.py` - Slack notification sending
- `src/workflows/watchlist.py` - Trader monitoring lists
- `src/triage/prompt_templates.py` - Claude triage reasoning

**Typical Workflow**:
Detection → Triage Classification → Jira Ticket → Slack Alert → Investigation → Resolution

**Environment Setup Required**:
```
JIRA_BASE_URL=https://wissen-hackathon.atlassian.net
JIRA_API_TOKEN=***
JIRA_USER_EMAIL=***
JIRA_PROJECT_KEY=KAN
SLACK_WEBHOOK_URL=***
SLACK_CHANNEL=#compliance-alerts
```

</quick_reference>
