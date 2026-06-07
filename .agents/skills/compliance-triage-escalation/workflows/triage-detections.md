# Triage Market Manipulation Detections

## Objective
Classify detection findings by severity and route to appropriate investigation teams.

## Required Reading
- `references/severity-mapping.md` - Confidence to severity mapping
- `references/escalation-paths.md` - Decision tree for each manipulation type

## Process

### Step 1: Load Detection Results
```python
from src.triage.claude_client import TriageClient
import json

# Load detection report from market-manipulation-detection skill
with open("detection-report.json") as f:
    detection_report = json.load(f)

triage_client = TriageClient()
```

### Step 2: Classify Each Detection
```python
classifications = []

for detection in detection_report['detections']:
    classification = triage_client.classify_detection(
        manipulation_type=detection['manipulation_type'],
        confidence=detection['confidence'],
        trader_history=detection.get('trader_history'),
        market_context=detection.get('market_context')
    )
    classifications.append(classification)
```

### Step 3: Apply Severity Thresholds
```python
from src.workflows.watchlist import WatchlistManager

for classification in classifications:
    if classification['confidence'] >= 0.90:
        classification['severity'] = 'critical'
        classification['action'] = 'immediate_escalation'
    elif classification['confidence'] >= 0.75:
        classification['severity'] = 'high'
        classification['action'] = 'l2_review'
    elif classification['confidence'] >= 0.50:
        classification['severity'] = 'medium'
        classification['action'] = 'investigation_queue'
    else:
        classification['severity'] = 'low'
        classification['action'] = 'monitoring'
```

### Step 4: Route by Action Type
```python
critical_alerts = [c for c in classifications if c['severity'] == 'critical']
high_alerts = [c for c in classifications if c['severity'] == 'high']
medium_alerts = [c for c in classifications if c['severity'] == 'medium']

print(f"CRITICAL: {len(critical_alerts)} - Escalate immediately")
print(f"HIGH: {len(high_alerts)} - Open investigations")
print(f"MEDIUM: {len(medium_alerts)} - Queue for review")
```

### Step 5: Generate Triage Report
Use `templates/triage-summary.json` to document classifications.

## Success Criteria

- ✅ All detections classified by severity
- ✅ Each alert has clear recommended action
- ✅ Ready for stakeholder notification
- ✅ Traceability from detection to classification
- ✅ Ready for investigation workflow

## Next Steps
Route output to:
- **Critical**: workflows/escalate-critical.md
- **High**: workflows/create-investigation.md  
- **Medium**: workflows/manage-watchlist.md
