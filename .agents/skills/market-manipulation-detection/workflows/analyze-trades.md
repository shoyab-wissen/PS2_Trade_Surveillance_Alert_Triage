# Analyze Trades for Market Manipulation

## Objective
Scan trading data and detect all manipulation patterns with confidence scores and evidence.

## Required Reading
- `references/detection-patterns.md` - Manipulation signatures
- `references/statistical-methods.md` - Validation techniques

## Process

### Step 1: Prepare Trade Data
```python
from src.ingestion.loader import load_trade_data
from src.config import get_config

config = get_config()
trades = load_trade_data(
    source_file="data/trades_scenario.csv",
    validate=True
)
```

### Step 2: Initialize Detection Engine
```python
from src.detection.engine import ManipulationDetectionEngine

engine = ManipulationDetectionEngine(config=config)
```

### Step 3: Run Full Analysis
```python
results = engine.detect_all(
    trades=trades,
    confidence_threshold=0.75,
    include_evidence=True
)
```

### Step 4: Analyze Results by Type
```python
for detection in results:
    manipulation_type = detection['manipulation_type']
    confidence = detection['confidence']
    severity = detection['severity']
    evidence = detection['evidence']
    
    # Filter by confidence level
    if confidence >= 0.85:
        print(f"HIGH CONFIDENCE: {manipulation_type}")
```

### Step 5: Generate Report
Use `templates/detection-report.json` to format findings.

## Success Criteria

- All trades processed without errors
- Each detection includes:
  - ✅ Manipulation type (layering/wash/momentum/ramp/close)
  - ✅ Confidence score (0-1)
  - ✅ Severity level (low/medium/high/critical)
  - ✅ Statistical evidence
  - ✅ Recommended action
- Results ready for escalation workflow

## Next Steps
After analysis, route to **compliance-triage-escalation** skill for alert classification and Jira/Slack escalation.
