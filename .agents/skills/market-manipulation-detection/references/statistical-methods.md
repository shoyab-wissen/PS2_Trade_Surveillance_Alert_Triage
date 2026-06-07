# Statistical Methods for Manipulation Detection

## Confidence Scoring

All detections must include confidence (0-1) based on:

1. **Pattern Strength** (0.3 weight)
   - How closely matches known signature
   - Statistical significance of indicators
   - Example: Chi-square goodness-of-fit for distribution

2. **Evidence Robustness** (0.3 weight)
   - Multiple independent indicators confirming
   - Consistency across time periods
   - Magnitude of effect size

3. **False Positive Risk** (0.2 weight)
   - How common pattern is in legitimate trading
   - Whether context explains behavior
   - Peer group comparison

4. **Temporal Consistency** (0.2 weight)
   - Repeated patterns indicate intentionality
   - Isolated incidents less suspicious
   - Behavioral persistence

**Calculation**:
```
confidence = (pattern_strength × 0.3) + 
             (evidence_robustness × 0.3) +
             (1 - false_positive_risk) × 0.2 +
             (temporal_consistency × 0.2)
```

## Severity Classification

Map confidence to severity:

| Confidence Range | Severity | Action |
|------------------|----------|--------|
| 0.90 - 1.00 | CRITICAL | Immediate escalation |
| 0.75 - 0.90 | HIGH | Escalate to L2 review |
| 0.50 - 0.75 | MEDIUM | Flag for investigation |
| 0.30 - 0.50 | LOW | Monitor pattern |
| < 0.30 | INFO | Archive for reference |

## False Positive Reduction

**Filtering Techniques**:

1. **Context Filtering**
   - Was there news/earnings release?
   - Was there volatility spike?
   - Was there index rebalancing?
   - Check market calendar

2. **Peer Comparison**
   - Compare trader metrics to peer group
   - Identify outliers (>2σ from mean)
   - Account for trader style (HFT vs. fundamental)

3. **Pattern Combination**
   - Single indicator suspicious = medium confidence
   - Multiple independent indicators = high confidence
   - Cross-pattern confirmation = critical confidence

4. **Historical Baseline**
   - Compare trader's current behavior to own history
   - Sudden change in pattern = more suspicious
   - Consistent behavior = less suspicious

## Implementation Examples

**Order-to-Trade Ratio Test** (Layering):
```python
import scipy.stats as stats

# Null hypothesis: ratio is normal for this trader
observed_ratio = canceled_orders / executed_trades
expected_ratio = peer_group_median_ratio
std_dev = peer_group_std

z_score = (observed_ratio - expected_ratio) / std_dev
p_value = 1 - stats.norm.cdf(abs(z_score))

confidence = 1 - p_value if p_value < 0.05 else 0.1
```

**Price Impact Test** (Momentum Ignition):
```python
# Measure price movement vs. expected impact
expected_impact = order_size / (daily_volume * scale_factor)
observed_impact = abs(price_after - price_before)

impact_ratio = observed_impact / expected_impact
if impact_ratio > 1.5:  # 50% more impact than expected
    confidence += 0.3
```

**Temporal Clustering Test** (All Patterns):
```python
# Measure if events cluster in time
from scipy.stats import poisson

# Null: events randomly distributed
observed_clusters = count_events_per_minute
expected_clusters = poisson.mean(events_total / total_minutes)

# High clustering = higher confidence
clustering_score = 1 - (expected_clusters / observed_clusters)
```
