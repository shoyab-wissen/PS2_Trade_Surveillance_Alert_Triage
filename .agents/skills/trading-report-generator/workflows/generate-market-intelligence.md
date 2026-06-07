# Generate Market Intelligence Report

## Objective
Analyze trading patterns and manipulation trends for strategic market surveillance insights.

## Required Reading
- `references/report-templates.md` - Intelligence report format
- `references/distribution-matrix.md` - Strategic stakeholder distribution

## Process

### Step 1: Load Historical Data
```python
from src.workflows.report_generator import MarketIntelligenceGenerator
from src.ingestion.loader import load_trade_data
from datetime import datetime, timedelta
import pandas as pd

# Load last 30 days of detection data
generator = MarketIntelligenceGenerator()
trades = load_trade_data("data/trades_scenario.csv")

# Aggregate detections by type, trader, security
detection_summary = trades.groupby(['manipulation_type', 'trader_id']).size()
```

### Step 2: Analyze Patterns and Trends
```python
# Identify emerging patterns
patterns = {
    "layering_trend": generator.calculate_trend(
        pattern_type="layering",
        days=30
    ),
    "wash_trading_trend": generator.calculate_trend(
        pattern_type="wash_trading",
        days=30
    ),
    "momentum_ignition_trend": generator.calculate_trend(
        pattern_type="momentum_ignition",
        days=30
    ),
    "most_targeted_securities": generator.get_top_securities(
        by_detection_count=5
    ),
    "most_active_traders": generator.get_top_traders(
        by_pattern_count=5
    )
}

# Trend analysis
for pattern_type, trend in patterns.items():
    if trend.get('trend_direction') == 'increasing':
        print(f"⚠️ {pattern_type} increasing (+{trend['percent_change']:.1%})")
    elif trend.get('trend_direction') == 'decreasing':
        print(f"✅ {pattern_type} decreasing (-{abs(trend['percent_change']):.1%})")
```

### Step 3: Identify Trader Clusters
```python
# Find trader groups with coordinated activity
trader_clusters = generator.find_trader_clusters(
    similarity_threshold=0.75,
    min_cluster_size=2
)

intelligence = {
    "report_type": "Market Intelligence",
    "analysis_period": "2026-05-08 to 2026-06-07",
    "key_insights": [
        f"Detected {len(trader_clusters)} potential trader networks",
        f"Layering patterns {patterns['layering_trend']['trend_direction']} by {patterns['layering_trend']['percent_change']:.1%}",
        f"Most targeted security: {patterns['most_targeted_securities'][0]}",
        f"Most active manipulator: {patterns['most_active_traders'][0]['trader_id']}"
    ],
    "trader_networks": trader_clusters,
    "vulnerability_assessment": {
        "most_manipulated_securities": patterns['most_targeted_securities'],
        "most_active_traders": patterns['most_active_traders'],
        "time_of_day_clustering": generator.analyze_temporal_patterns()
    }
}
```

### Step 4: Create Strategic Recommendations
```python
recommendations = []

if patterns['layering_trend']['trend_direction'] == 'increasing':
    recommendations.append({
        "priority": "HIGH",
        "action": "Increase monitoring of order flow in affected securities",
        "rationale": f"Layering patterns increasing {patterns['layering_trend']['percent_change']:.0%}",
        "timeline": "Implement within 1 week"
    })

for trader in patterns['most_active_traders'][:3]:
    recommendations.append({
        "priority": "MEDIUM",
        "action": f"Flag {trader['trader_id']} for enhanced surveillance",
        "rationale": f"Multiple pattern detections ({trader['pattern_count']}) in last 30 days",
        "timeline": "Ongoing monitoring"
    })

intelligence['strategic_recommendations'] = recommendations
```

### Step 5: Generate Visualization Data
```python
# Prepare charts for business intelligence tools
visualization_data = {
    "detections_by_type": {
        "layering": patterns['layering_trend']['current_count'],
        "wash_trading": patterns['wash_trading_trend']['current_count'],
        "momentum_ignition": patterns['momentum_ignition_trend']['current_count'],
        "price_ramping": generator.get_pattern_count("price_ramping"),
        "marking_close": generator.get_pattern_count("marking_close")
    },
    "trend_chart": {
        "dates": [datetime.now() - timedelta(days=i) for i in range(30)],
        "layering": generator.get_daily_counts("layering", days=30),
        "wash_trading": generator.get_daily_counts("wash_trading", days=30)
    },
    "top_traders_chart": [
        {"trader": t['trader_id'], "detections": t['pattern_count']}
        for t in patterns['most_active_traders'][:10]
    ]
}

intelligence['visualizations'] = visualization_data
```

### Step 6: Format Intelligence Report
```python
intelligence_report = {
    "classification": "Internal Use - Compliance Sensitive",
    "distribution": ["Risk Management", "Strategy Team", "Board Compliance Committee"],
    "created": datetime.now().isoformat(),
    **intelligence
}
```

### Step 7: Export and Distribute
```python
# Export to multiple formats for BI tools
excel_file = generator.to_excel(
    data=intelligence_report,
    output_path=f"reports/intelligence/market-intelligence-{datetime.now().strftime('%Y%m%d')}.xlsx"
)

json_file = generator.to_json(
    data=intelligence_report,
    output_path=f"reports/intelligence/market-intelligence-{datetime.now().strftime('%Y%m%d')}.json"
)

print(f"✅ Market Intelligence Report Generated")
print(f"  Excel: {excel_file}")
print(f"  JSON: {json_file}")
```

## Success Criteria

- ✅ Trend analysis completed for all manipulation types
- ✅ Trader networks identified
- ✅ Strategic recommendations provided
- ✅ Visualization data prepared for BI tools
- ✅ Report distributed to strategic stakeholders

## Key Insights to Include

1. **Trend Analysis**: Are manipulations increasing or decreasing?
2. **Hotspots**: Which securities most frequently targeted?
3. **Sophisticated Actors**: Which traders showing advanced tactics?
4. **Time Patterns**: Are manipulations concentrated at market open/close?
5. **Network Patterns**: Evidence of coordinated activity between traders?
6. **Emerging Techniques**: New manipulation patterns emerging?

## Distribution Audience

- **Risk Management**: Strategic surveillance priorities
- **Strategy Team**: Market structure decisions
- **Board Compliance**: Quarterly board briefing
- **Regulatory Affairs**: Regulatory notification decisions
