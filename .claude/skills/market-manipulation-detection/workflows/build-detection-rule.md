# Build Custom Detection Rule

## Objective
Create custom detection logic for specific manipulation patterns or trader profiles.

## Required Reading
- `references/detection-patterns.md` - Pattern signatures
- `references/statistical-methods.md` - Validation approaches

## Process

### Step 1: Define Rule Parameters
```python
# Specify what you're looking for
rule = {
    "name": "High-Volume Layering",
    "pattern_type": "layering",
    "parameters": {
        "order_to_trade_ratio": (8, 20),  # Fake orders per real trade
        "order_duration_seconds": (5, 30),  # How long fake orders sit
        "volume_threshold": 10000,  # Minimum order volume
        "time_window": 60  # Analysis window in seconds
    },
    "confidence_threshold": 0.80
}
```

### Step 2: Map to Detection Module
```python
from src.detection.layering import LayeringDetector

detector = LayeringDetector()
detector.custom_config = rule['parameters']
```

### Step 3: Test on Sample Data
```python
from src.ingestion.loader import load_trade_data

test_trades = load_trade_data("data/trades_scenario.csv")
detections = detector.detect(test_trades)

# Validate accuracy
print(f"Detected {len(detections)} cases")
for d in detections:
    print(f"  Confidence: {d['confidence']:.2%}")
```

### Step 4: Fine-Tune Parameters
Adjust thresholds based on test results:
```python
# If too many false positives, increase thresholds
# If missing real cases, decrease thresholds
rule['parameters']['order_to_trade_ratio'] = (10, 25)
```

### Step 5: Save Rule
```python
import json
with open("detection_rules/custom_layering.json", "w") as f:
    json.dump(rule, f, indent=2)
```

## Success Criteria

- ✅ Rule clearly specifies pattern signature
- ✅ Parameters validated on test data
- ✅ False positive rate acceptable (<10%)
- ✅ Rule captures intended behavior
- ✅ Saved for production use

## Integration with Detection Engine
```python
engine.add_custom_rule("custom_layering.json")
results = engine.detect_all(trades)
```
