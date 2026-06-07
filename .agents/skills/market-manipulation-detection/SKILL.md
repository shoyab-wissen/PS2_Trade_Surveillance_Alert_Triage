---
name: market-manipulation-detection
description: Expert in analyzing trading data and detecting market manipulation patterns including layering, wash trading, momentum ignition, price ramping, and marking-close tactics. Use when analyzing trades for suspicious activity, building detection rules, or investigating potential market abuse.
---

<objective>Identify and classify market manipulation patterns in trading data using statistical and behavioral analysis techniques.</objective>

<essential_principles>

## Detection Methodology

1. **Pattern Recognition**: Analyze order flows and trade sequences against known manipulation signatures
2. **Statistical Validation**: Use statistical tests to confirm suspicious patterns with quantifiable evidence
3. **Multi-Factor Analysis**: Cross-reference multiple indicators (volume, timing, price, order size) to reduce false positives
4. **Behavioral Profiling**: Compare trader behavior against historical baselines and peer groups
5. **Temporal Sensitivity**: Account for market conditions, time-of-day effects, and volatility regimes

## Manipulation Types Covered

- **Layering**: Creating multiple fake orders to simulate demand/supply
- **Wash Trading**: Simultaneous buy-sell of same security between related parties
- **Momentum Ignition**: Aggressive trades to trigger algorithmic responses
- **Price Ramping**: Sequence of trades to artificially inflate/deflate prices
- **Marking the Close**: Trades at market close to artificially set closing price

## Detection Workflow

1. **Data Ingestion**: Load and validate trade data
2. **Feature Engineering**: Calculate manipulation-specific indicators
3. **Pattern Detection**: Apply detection algorithms for each manipulation type
4. **Confidence Scoring**: Assign severity and confidence levels
5. **Result Aggregation**: Compile findings with evidence and recommendations

</essential_principles>

<intake>
What detection analysis would you like to perform?

1. **Analyze trades** - Scan trade data for all manipulation types
2. **Build detection rule** - Create custom detection logic for specific patterns
3. **Profile trader** - Analyze specific trader's historical behavior
4. **Validate findings** - Test detection accuracy against known cases
5. **Get guidance** - Understand detection methodology

**Select option or describe your analysis need.**
</intake>

<routing>

| Response | Workflow |
|----------|----------|
| 1, "analyze", "scan", "detect" | workflows/analyze-trades.md |
| 2, "build", "rule", "custom" | workflows/build-detection-rule.md |
| 3, "profile", "trader", "behavior" | workflows/profile-trader.md |
| 4, "validate", "test", "accuracy" | workflows/validate-detections.md |
| 5, "guidance", "methodology", "help" | workflows/detection-guidance.md |

**After selecting, follow the workflow exactly.**

</routing>

<references_preview>
See `references/` for:
- **detection-patterns.md** - Detailed signatures for each manipulation type
- **statistical-methods.md** - Validation techniques and threshold guidance
- **trader-profiles.md** - Common manipulation strategies by profile
- **false-positive-reduction.md** - Filtering and confidence scoring

</references_preview>

<quick_reference>

**Key Detection Files in Project:**
- `src/detection/engine.py` - Detection orchestration
- `src/detection/{layering,wash_trading,momentum_ignition,price_ramping,marking_close}.py` - Type-specific logic
- `src/detection/statistics.py` - Statistical validation
- `src/ingestion/loader.py` - Data loading and validation

**Typical Output:**
```json
{
  "trader_id": "T12345",
  "manipulation_type": "layering",
  "confidence": 0.92,
  "severity": "high",
  "evidence": [...],
  "recommended_action": "escalate"
}
```

</quick_reference>
