# Generate Compliance Report

## Objective
Create formal, audit-ready compliance report documenting detections, evidence, and investigation findings.

## Required Reading
- `references/compliance-standards.md` - Regulatory requirements
- `references/report-templates.md` - Structure and formatting
- `references/distribution-matrix.md` - Distribution rules

## Process

### Step 1: Gather Source Data
```python
from src.workflows.report_generator import ComplianceReportGenerator
from src.ingestion.loader import load_trade_data
import json
from datetime import datetime

# Load all source documents
with open("detection-report.json") as f:
    detections = json.load(f)
    
with open("triage-summary.json") as f:
    triage = json.load(f)

trades = load_trade_data("data/trades_scenario.csv")

generator = ComplianceReportGenerator()
```

### Step 2: Create Report Structure
```python
report_data = {
    "report_type": "compliance",
    "report_date": datetime.now().isoformat(),
    "period_start": "2026-06-01",
    "period_end": "2026-06-07",
    "executive_summary": {
        "total_alerts": len(detections['detections']),
        "critical_findings": len([d for d in detections['detections'] if d['severity'] == 'critical']),
        "traders_flagged": len(set(d['trader_id'] for d in detections['detections'])),
        "estimated_impact": sum(float(d.get('estimated_market_impact', '0').replace('$', '').replace(',', '')) 
                               for d in detections['detections'])
    },
    "findings": [],
    "evidence": [],
    "audit_trail": []
}
```

### Step 3: Document Each Finding
```python
for detection in detections['detections']:
    finding = {
        "finding_id": detection['detection_id'],
        "trader_id": detection['trader_id'],
        "manipulation_type": detection['manipulation_type'],
        "confidence": detection['confidence'],
        "severity": detection['severity'],
        "detected_date": detection['detected_time'],
        "evidence_summary": detection['evidence'],
        "market_impact": detection.get('estimated_market_impact', 'TBD'),
        "status": "under_investigation",
        "jira_ticket": triage.get('jira_key', 'pending'),
        "narrative": f"""
        On {detection['detected_time']}, our automated detection system identified 
        a potential {detection['manipulation_type']} pattern involving trader {detection['trader_id']}.
        
        The detection algorithm flagged this activity with {detection['confidence']:.0%} confidence
        based on statistical analysis of trading patterns. The evidence includes:
        
        {generate_evidence_narrative(detection['evidence'])}
        
        This finding is classified as {detection['severity'].upper()} severity and has been
        escalated for immediate investigation.
        """.strip()
    }
    report_data['findings'].append(finding)
```

### Step 4: Include Evidence Documentation
```python
for detection in detections['detections']:
    evidence = {
        "detection_id": detection['detection_id'],
        "trader_id": detection['trader_id'],
        "evidence_type": detection['manipulation_type'],
        "statistical_markers": detection['evidence'],
        "trades_involved": detection['affected_trades_count'],
        "affected_security": detection['affected_security'],
        "time_range": {
            "start": detection['detected_time'],
            "duration_seconds": 300  # Typical window
        },
        "confidence_calculation": {
            "base_confidence": detection['confidence'],
            "methodology": "See references/statistical-methods.md"
        }
    }
    report_data['evidence'].append(evidence)
```

### Step 5: Document Audit Trail
```python
# Record when report was created and by whom
report_data['audit_trail'] = {
    "created_timestamp": datetime.now().isoformat(),
    "created_by": "trading-report-generator",
    "source_documents": [
        "detection-report.json",
        "triage-summary.json",
        "trades_scenario.csv"
    ],
    "verification_status": "awaiting_compliance_review",
    "signature": None,  # Will be signed by compliance officer
    "retention_period": "7 years minimum (regulatory hold)"
}
```

### Step 6: Generate Report File
```python
# Generate in multiple formats
pdf_file = generator.to_pdf(
    data=report_data,
    output_path=f"reports/compliance/compliance-report-{datetime.now().strftime('%Y%m%d')}.pdf"
)

json_file = generator.to_json(
    data=report_data,
    output_path=f"reports/compliance/compliance-report-{datetime.now().strftime('%Y%m%d')}.json"
)

print(f"✅ Compliance Report Generated")
print(f"  PDF: {pdf_file}")
print(f"  JSON: {json_file}")
```

### Step 7: Distribute Report
```python
# Send to compliance officer for review and signature
from src.workflows.slack_client import SlackClient

slack_client = SlackClient(webhook_url=config.slack_webhook_url)
slack_client.send_message(
    channel='#compliance-reports',
    message=f"""
📋 **Compliance Report Ready for Review**

Report: {pdf_file}
Period: 2026-06-01 to 2026-06-07
Findings: {len(report_data['findings'])}
Critical: {report_data['executive_summary']['critical_findings']}

Status: ⏳ Awaiting compliance officer signature
SLA: Sign within 24 hours
    """
)
```

## Success Criteria

- ✅ Report includes all detections with full evidence
- ✅ Audit trail documents all decisions
- ✅ Prepared in multiple formats (PDF, JSON)
- ✅ Ready for regulatory review
- ✅ Signature field prepared for compliance officer

## Report Sections

1. **Title Page** - Report type, date range, classification
2. **Executive Summary** - Key findings and metrics
3. **Detailed Findings** - Each finding with evidence narrative
4. **Evidence Appendix** - Statistical details and calculations
5. **Audit Trail** - Complete record of process steps
6. **Signature Page** - Compliance officer sign-off

## Regulatory Compliance

- ✅ SOX compliance (internal controls documentation)
- ✅ SEC Rule 10b-5 (insider trading surveillance)
- ✅ FINRA Rule 5210 (market surveillance)
- ✅ Record retention (7-year hold minimum)
