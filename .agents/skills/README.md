# Trading Compliance Agent Skills - Overview

This directory contains three specialized agent skills for your market manipulation detection system. Each skill provides expertise in a specific domain and workflows.

## The Three Skills

### 1. **market-manipulation-detection**
**Purpose**: Analyze trading data and detect manipulation patterns

**Expertise Area**: Pattern recognition, statistical validation, trader profiling

**Use When**:
- Scanning trade data for suspicious activity
- Analyzing specific traders for behavioral patterns
- Building custom detection rules
- Validating detection methodology

**Key Workflows**:
- `analyze-trades.md` - Run full detection analysis on trade data
- `build-detection-rule.md` - Create custom detection rules
- `profile-trader.md` - Analyze trader's historical behavior

**Key References**:
- `detection-patterns.md` - Detailed signatures for each manipulation type
- `statistical-methods.md` - Confidence scoring and false-positive reduction

**Output Templates**:
- `detection-report.json` - Structured findings with evidence

---

### 2. **compliance-triage-escalation**
**Purpose**: Classify alerts and escalate findings through Jira/Slack

**Expertise Area**: Alert classification, investigation management, stakeholder notification

**Use When**:
- Triaging detection findings by severity
- Creating investigation tickets in Jira
- Notifying teams via Slack
- Managing watchlists and escalations

**Key Workflows**:
- `triage-detections.md` - Classify alerts and route to teams
- `create-investigation.md` - Open Jira ticket and assign investigator
- `notify-stakeholders.md` - Send Slack alerts to appropriate channels
- `escalate-critical.md` - Fast-track critical findings to leadership

**Key References**:
- `severity-mapping.md` - How confidence maps to action levels
- `jira-integration.md` - Jira workflow and integration patterns

**Output Templates**:
- `triage-summary.json` - Classification and routing decisions

---

### 3. **trading-report-generator**
**Purpose**: Generate compliance reports and market intelligence

**Expertise Area**: Report generation, regulatory documentation, trend analysis

**Use When**:
- Creating formal compliance reports for audit
- Generating executive summaries for leadership
- Analyzing market trends and manipulation patterns
- Preparing regulatory documentation

**Key Workflows**:
- `generate-compliance-report.md` - Formal audit-ready documentation
- `generate-executive-summary.md` - Leadership briefing summary
- `generate-market-intelligence.md` - Trend analysis and strategic insights

**Key References**:
- `compliance-standards.md` - Regulatory requirements and record retention

**Output Templates**:
- `market-intelligence-report.json` - Strategic insights and recommendations

---

## Workflow Integration Pattern

```
┌─────────────────────────────────────┐
│  Raw Trade Data                     │
└────────────┬────────────────────────┘
             │
             ▼
┌─────────────────────────────────────┐
│  market-manipulation-detection      │
│  Analyze for suspicious patterns    │
└────────────┬────────────────────────┘
             │
             ▼ Detection Report
┌─────────────────────────────────────┐
│  compliance-triage-escalation       │
│  Classify findings & escalate       │
└────────────┬────────────────────────┘
             │
      ┌──────┴────────┬────────────┐
      │               │            │
      ▼               ▼            ▼
   Jira Ticket   Slack Alert   Watchlist
      │               │            │
      └───────────────┼────────────┘
                      ▼
┌─────────────────────────────────────┐
│  trading-report-generator           │
│  Create compliance reports & intel  │
└─────────────────────────────────────┘
```

## Quick Start Examples

### Example 1: Detect and Report
```
1. Run: market-manipulation-detection > analyze-trades
2. Output: detection-report.json
3. Input to: compliance-triage-escalation > triage-detections
4. Output: triage-summary.json
5. Input to: trading-report-generator > generate-compliance-report
6. Final: compliance-report-20260607.pdf
```

### Example 2: Leadership Briefing
```
1. Get latest detections from database
2. Run: compliance-triage-escalation > triage-detections
3. Run: trading-report-generator > generate-executive-summary
4. Send summary to leadership via Slack
```

### Example 3: Trader Risk Profile
```
1. Run: market-manipulation-detection > profile-trader
2. Run: compliance-triage-escalation > manage-watchlist
3. Run: trading-report-generator > generate-market-intelligence
4. Share intelligence report with risk team
```

## Environment Requirements

Set these in your `.env` file:

```dotenv
# For detection analysis
ANTHROPIC_API_KEY=sk-ant-...

# For Jira integration (triage)
JIRA_BASE_URL=https://wissen-hackathon.atlassian.net
JIRA_API_TOKEN=ATATT3x...
JIRA_USER_EMAIL=your@email.com
JIRA_PROJECT_KEY=KAN
JIRA_L2_ASSIGNEE_ACCOUNT_ID=712020:...

# For Slack notifications
SLACK_WEBHOOK_URL=https://hooks.slack.com/...
SLACK_CHANNEL=#compliance-alerts
```

## File Organization

```
.agents/skills/
├── market-manipulation-detection/
│   ├── SKILL.md
│   ├── workflows/
│   │   ├── analyze-trades.md
│   │   ├── build-detection-rule.md
│   │   └── profile-trader.md
│   ├── references/
│   │   ├── detection-patterns.md
│   │   └── statistical-methods.md
│   └── templates/
│       └── detection-report.json
├── compliance-triage-escalation/
│   ├── SKILL.md
│   ├── workflows/
│   │   ├── triage-detections.md
│   │   ├── create-investigation.md
│   │   └── escalate-critical.md
│   ├── references/
│   │   └── severity-mapping.md
│   └── templates/
│       └── triage-summary.json
└── trading-report-generator/
    ├── SKILL.md
    ├── workflows/
    │   ├── generate-compliance-report.md
    │   ├── generate-executive-summary.md
    │   └── generate-market-intelligence.md
    ├── references/
    │   └── compliance-standards.md
    └── templates/
        └── market-intelligence-report.json
```

## How to Use These Skills

1. **Invoke a skill** in Claude by name when you need its expertise:
   - "Use the market-manipulation-detection skill to analyze trades"
   - "I need compliance-triage-escalation to triage these findings"
   - "Generate market intelligence with trading-report-generator"

2. **Skills route automatically** - Each skill has intake questions that route you to the right workflow

3. **Follow workflows step-by-step** - Workflows include code examples and clear next steps

4. **Reference domain knowledge** - When you hit a technical question, workflows point you to reference files

5. **Use templates** - Output templates show the expected structure for reports and findings

## Key Principles

✅ **Modular** - Each skill is independent but workflows connect them
✅ **Python-Native** - Code examples use your project structure (`src/` modules)
✅ **Claude-Friendly** - Workflows are designed for Claude analysis and decision-making
✅ **Audit-Ready** - All steps produce documented, traceable outputs
✅ **Regulatory-Compliant** - Adheres to SEC/FINRA requirements

## Next Steps

1. Test each skill with sample data
2. Customize detection rules for your trading venue
3. Integrate Jira and Slack connections
4. Set up report distribution and retention policies
5. Train your team on using the skills

---

**Skills created**: June 7, 2026
**Designed for**: Python/Claude trading compliance system
**Technology**: Market manipulation detection + compliance automation
