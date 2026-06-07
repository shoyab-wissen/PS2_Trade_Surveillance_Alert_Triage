# Generate Executive Summary

## Objective
Create concise, actionable summary for leadership highlighting key findings and recommendations.

## Required Reading
- `references/report-templates.md` - Executive summary format
- `references/distribution-matrix.md` - Who gets executive summary

## Process

### Step 1: Initialize Report Generator
```python
from src.workflows.report_generator import ExecutiveSummaryGenerator
from datetime import datetime
import json

with open("detection-report.json") as f:
    detections = json.load(f)

generator = ExecutiveSummaryGenerator()
```

### Step 2: Build Summary Metrics
```python
critical_findings = [d for d in detections['detections'] if d['severity'] == 'critical']
high_findings = [d for d in detections['detections'] if d['severity'] == 'high']

summary = {
    "reporting_period": "2026-06-01 to 2026-06-07",
    "total_alerts": len(detections['detections']),
    "by_severity": {
        "critical": len(critical_findings),
        "high": len(high_findings),
        "medium": len([d for d in detections['detections'] if d['severity'] == 'medium'])
    },
    "traders_involved": len(set(d['trader_id'] for d in detections['detections'])),
    "total_estimated_impact": sum(
        float(d.get('estimated_market_impact', '0').replace('$', '').replace(',', ''))
        for d in detections['detections']
    ),
    "most_common_manipulation_type": max(
        [(mt, len([d for d in detections['detections'] if d['manipulation_type'] == mt]))
         for mt in set(d['manipulation_type'] for d in detections['detections'])],
        key=lambda x: x[1]
    )[0]
}
```

### Step 3: Create Key Findings
```python
key_findings = []

for finding in critical_findings[:3]:  # Top 3 critical
    key_findings.append({
        "finding": f"{finding['manipulation_type'].title()} Pattern",
        "trader": finding['trader_id'],
        "impact": finding.get('estimated_market_impact', 'TBD'),
        "confidence": f"{finding['confidence']:.0%}",
        "status": "Under Investigation"
    })

for finding in high_findings[:2]:  # Top 2 high
    key_findings.append({
        "finding": f"{finding['manipulation_type'].title()} Pattern",
        "trader": finding['trader_id'],
        "impact": finding.get('estimated_market_impact', 'TBD'),
        "confidence": f"{finding['confidence']:.0%}",
        "status": "Investigation Queued"
    })
```

### Step 4: Generate Narrative
```python
narrative = f"""
MARKET COMPLIANCE EXECUTIVE SUMMARY
{datetime.now().strftime('%B %d, %Y')}

KEY METRICS:
• {summary['total_alerts']} compliance alerts identified
• {summary['by_severity']['critical']} CRITICAL findings requiring immediate action
• {summary['by_severity']['high']} HIGH priority investigations initiated
• ${summary['total_estimated_impact']:,.0f} estimated market impact
• {summary['traders_involved']} traders flagged across {len(set(d['affected_security'] for d in detections['detections']))} securities

CRITICAL FINDINGS REQUIRING IMMEDIATE ACTION:
{chr(10).join([f"  • [{f['trader']}] {f['finding']} ({f['confidence']} confidence)" for f in key_findings[:3]])}

RECOMMENDED ACTIONS:
1. Review critical findings (attached) and approve trader suspension decisions
2. Notify regulatory affairs of critical detections within 24 hours
3. Assign L2 investigators to high-priority cases
4. Monitor watchlist traders for behavioral changes

NEXT STEPS:
• Critical cases: Suspension decision required within 1 hour
• Investigations: Status updates due within 24 hours
• Regular review: Weekly compliance briefing scheduled
"""
```

### Step 5: Format for Distribution
```python
# Create readable executive summary
executive_summary = {
    "report_type": "Executive Summary",
    "date": datetime.now().isoformat(),
    "distribution": ["CFO", "Chief Compliance Officer", "General Counsel"],
    "confidentiality": "Internal Use Only",
    "narrative": narrative,
    "key_findings_table": key_findings,
    "recommended_actions": [
        "Approve suspension of traders T12345, T54321",
        "Assign investigations to compliance team",
        "Prepare regulatory notification",
        "Schedule C-level briefing"
    ],
    "metrics_dashboard": summary,
    "next_review": "Daily briefing - 9:00 AM EST"
}

print("Executive Summary:")
print(narrative)
```

### Step 6: Export to PDF
```python
pdf_path = f"reports/executive/executive-summary-{datetime.now().strftime('%Y%m%d-%H%M')}.pdf"
generator.to_pdf(executive_summary, output_path=pdf_path)

print(f"✅ Executive Summary Generated: {pdf_path}")
```

### Step 7: Send to Leadership
```python
from src.workflows.slack_client import SlackClient

slack_client = SlackClient(webhook_url=config.slack_webhook_url)
slack_client.send_message(
    channel='#executive-briefing',
    message=f"""
📊 **DAILY COMPLIANCE EXECUTIVE SUMMARY**

Period: {summary['reporting_period']}

🔴 **CRITICAL**: {summary['by_severity']['critical']} findings
🟠 **HIGH**: {summary['by_severity']['high']} findings
🟡 **MEDIUM**: {summary['by_severity']['medium']} findings

Impact: ${summary['total_estimated_impact']:,.0f}

⚠️ **ACTION REQUIRED**: Review attached findings and approve suspension decisions

[Full Report Available: {pdf_path}]
    """
)
```

## Success Criteria

- ✅ One-page narrative summary prepared
- ✅ Key findings highlighted with traders and impact
- ✅ Recommendations clearly stated
- ✅ Ready for leadership review
- ✅ Formatted for PDF distribution

## Content for Executive Summary

**What Executives Need**:
- How many serious problems? (count by severity)
- What traders? (IDs, firms if tracked)
- How much money at risk? (estimated impact)
- What do we do next? (clear recommended actions)
- When? (timeline and SLAs)

**What to Avoid**:
- Technical jargon (explain in plain English)
- Excessive detail (reserve for detailed report)
- Equivocation (be decisive about recommendations)
