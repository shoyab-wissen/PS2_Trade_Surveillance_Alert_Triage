# Profile Trader Behavior

## Objective
Analyze a specific trader's historical behavior to identify patterns and risk profile.

## Required Reading
- `references/trader-profiles.md` - Common trader profiles and tactics
- `references/statistical-methods.md` - Behavioral metrics

## Process

### Step 1: Load Trader Data
```python
from src.ingestion.loader import load_trade_data
import pandas as pd

trades = load_trade_data("data/trades_scenario.csv")
trader_id = "T12345"  # Replace with target trader

trader_trades = trades[trades['trader_id'] == trader_id]
print(f"Loaded {len(trader_trades)} trades from {trader_id}")
```

### Step 2: Calculate Behavioral Metrics
```python
from src.detection.statistics import BehavioralAnalyzer

analyzer = BehavioralAnalyzer()
metrics = analyzer.profile_trader(trader_trades)

# Key metrics
print(f"Trade count: {metrics['total_trades']}")
print(f"Win rate: {metrics['win_rate']:.2%}")
print(f"Avg order size: {metrics['avg_order_size']:.0f}")
print(f"Avg holding time: {metrics['avg_holding_seconds']:.0f}s")
```

### Step 3: Compare Against Peer Group
```python
all_trades = load_trade_data("data/trades_scenario.csv")
peer_metrics = analyzer.peer_group_stats(all_trades, trader_id)

print(f"Trading frequency: {metrics['trades_per_hour']:.1f} (peer avg: {peer_metrics['avg_trades_per_hour']:.1f})")
print(f"Order size: {metrics['avg_order_size']:.0f} (peer avg: {peer_metrics['avg_order_size']:.0f})")
```

### Step 4: Flag Suspicious Behaviors
```python
risk_flags = []
if metrics['win_rate'] > 0.75:
    risk_flags.append("Unusually high win rate")
if metrics['cancel_rate'] > 0.50:
    risk_flags.append("High order cancellation rate")
if metrics['layering_score'] > 0.70:
    risk_flags.append("Characteristic layering patterns")

print(f"Risk flags: {risk_flags}")
```

### Step 5: Generate Profile Report
Use `templates/trader-profile.json` to document findings.

## Success Criteria

- ✅ Complete behavioral profile created
- ✅ Compared against peer group
- ✅ Risk factors identified
- ✅ Historical patterns documented
- ✅ Ready for ongoing monitoring

## Monitoring Ongoing Behavior
```python
# Re-profile weekly to detect changes
updated_metrics = analyzer.profile_trader(trader_trades)
changes = analyzer.detect_behavioral_changes(metrics, updated_metrics)
```
