# Severity Mapping: Confidence to Action

## Mapping Table

| Confidence | Severity | Action | SLA | Stakeholder | Jira Type |
|------------|----------|--------|-----|-------------|-----------|
| 0.90 - 1.00 | CRITICAL | Immediate escalation + suspension | 1 hour | C-level + Regulatory | Critical Incident |
| 0.75 - 0.89 | HIGH | L2 investigation | 24 hours | Compliance Lead | Investigation |
| 0.50 - 0.74 | MEDIUM | Investigation queue | 5 days | Compliance Team | Task |
| 0.30 - 0.49 | LOW | Monitoring watchlist | No deadline | Watch only | Monitoring |
| < 0.30 | INFO | Archive for reference | No deadline | None | Reference |

## Decision Logic by Manipulation Type

### Layering (Order Cancellation)

**High Confidence Threshold**: Z-score > 5 (order ratio >> peer group)
```
IF z_score > 6:
  CRITICAL
ELSE IF z_score > 4:
  HIGH
ELSE IF z_score > 2:
  MEDIUM
ELSE:
  LOW
```

### Wash Trading (Coordinated Buy-Sell)

**High Confidence Threshold**: Party relationship + perfect timing + no profit
```
IF party_related AND timing_ms < 100 AND profit == 0:
  CRITICAL
ELSE IF party_related AND (timing_ms < 500 OR no_profit):
  HIGH
ELSE IF suspicious_timing:
  MEDIUM
ELSE:
  LOW
```

### Momentum Ignition (Aggressive Trade + Reversal)

**High Confidence Threshold**: Large impact + rapid reversal + profit captured
```
IF impact_ratio > 2.0 AND reversal_seconds < 30:
  CRITICAL
ELSE IF impact_ratio > 1.5 AND reversal_seconds < 60:
  HIGH
ELSE IF impact_detected:
  MEDIUM
ELSE:
  LOW
```

### Price Ramping (Sequential Price Movement)

**High Confidence Threshold**: Coordinated sequential trades + price persistence
```
IF coordinated_traders > 2 AND price_persistence > 15min:
  CRITICAL
ELSE IF price_trend_significant AND high_volume:
  HIGH
ELSE IF pattern_detected:
  MEDIUM
ELSE:
  LOW
```

### Marking the Close (End-of-Day Price Setting)

**High Confidence Threshold**: Large volume + reversal next day + index impact
```
IF (close_volume > 3x_avg) AND (next_day_reversal > 1%):
  CRITICAL
ELSE IF (close_volume > 2x_avg) AND (price_away_from_range):
  HIGH
ELSE IF unusual_close_activity:
  MEDIUM
ELSE:
  LOW
```

## Contextual Modifiers

Apply to base confidence score:

**Positive Evidence** (increase severity):
- Trader has prior violations: +0.10 confidence
- Pattern repeated multiple times: +0.05 confidence
- Significant estimated impact (>$500K): +0.10 confidence
- Impacts regulated index: +0.15 confidence

**Mitigating Evidence** (decrease severity):
- Market volatility spike explains behavior: -0.20 confidence
- Legitimate news event occurred: -0.15 confidence
- Peer group exhibits same pattern: -0.10 confidence
- Trader has clean history: -0.05 confidence

## Example Workflow

```python
def map_to_severity(detection):
    base_confidence = detection['confidence']
    
    # Apply modifiers
    if detection.get('trader_history') == 'clean':
        base_confidence -= 0.05
    if detection.get('estimated_impact', 0) > 500000:
        base_confidence += 0.10
    if detection.get('pattern_repetitions', 1) > 5:
        base_confidence += 0.05
        
    # Cap at [0, 1]
    final_confidence = max(0, min(1, base_confidence))
    
    # Map to severity
    if final_confidence >= 0.90:
        return {
            'severity': 'CRITICAL',
            'action': 'immediate_escalation',
            'sla_hours': 1
        }
    elif final_confidence >= 0.75:
        return {
            'severity': 'HIGH',
            'action': 'l2_investigation',
            'sla_hours': 24
        }
    elif final_confidence >= 0.50:
        return {
            'severity': 'MEDIUM',
            'action': 'investigation_queue',
            'sla_days': 5
        }
    else:
        return {
            'severity': 'LOW',
            'action': 'monitoring_watchlist',
            'sla_days': None
        }
```
