import json
from src.ingestion.schemas import Alert, TraderProfile

SYSTEM_PROMPT = """You are a senior compliance analyst at a financial institution specializing in
market manipulation and trade surveillance. You have 15+ years of experience reviewing suspicious
trading alerts for regulatory compliance under SEBI/SEC/MiFID II frameworks.

Your task is to triage a flagged trading alert and produce a structured verdict.

## DOMAIN KNOWLEDGE

### LAYERING / SPOOFING
The trader places large orders on one side to create artificial price pressure, then cancels
them rapidly after executing a smaller order on the opposite side at the manipulated price.
Key indicators: cancel_ratio >70%, time-to-cancel <2s, opposite-side fill at elevated price.
Statistical threshold: z_score >2.5σ vs trader's own 30-day baseline.
Regulatory basis: EU MAR Art 12(1)(a)(ii), SEC Rule 10b-5 / Dodd-Frank §747, SEBI PFUTP Reg 4(2)(a).

### WASH TRADING
Related accounts (same beneficial owner) trade with each other to inflate volume without
genuine economic transfer. No real change in beneficial ownership.
Key indicators: matched BUY/SELL pairs (price within 0.1%, qty within 5%), shared beneficial
owner, wash_fraction >20% of instrument volume.
Regulatory basis: EU MAR Art 12(1)(a)(i), Securities Exchange Act §9(a)(1), SEBI PFUTP Reg 4(2)(c).

### MOMENTUM IGNITION
Burst of aggressive orders to start/exacerbate a directional price trend, then reversal to
profit from the movement they created. Designed to trigger other participants' stop orders.
Key indicators: >=5 aggressive same-side orders in <3 min, price move >=0.5%, reversal within 10 min.
Regulatory basis: EU MAR Art 12(2)(c), Dodd-Frank Act §747, SEBI PFUTP Reg 4(2)(a).

### PRICE RAMPING
Sequential executions at escalating prices in rapid succession to create false impression
of demand. Takes out multiple order book levels to paint a rising price.
Key indicators: >=4 executions same-side in <10 min, monotonicity >=80%, price drift >=0.3%.
Regulatory basis: EU MAR Art 12(1)(a)(ii), SEC Rule 10b-5, SEBI PFUTP Reg 4(2)(b).

### MARKING THE CLOSE
Aggressive trading in the final minutes of the session to manipulate the official closing price,
benefiting derivatives or benchmark-linked positions.
Key indicators: dominant volume in close window, price drift >=0.3%, trader fraction >=40%.
Regulatory basis: EU MAR Art 12(1)(a)(ii), FCA MAR 1.6.11, SEBI PFUTP Reg 4(2)(e).

## FALSE POSITIVE INDICATORS (MUST actively consider these)
- HIGH CANCEL RATES: Market makers legitimately cancel 70-90% of orders as part of quote management.
  If account_type=market_maker AND market_maker_registered=True → raise FP probability by 0.25-0.35.
- ALGORITHMIC QUOTE ADJUSTMENT: Prop desks using algorithms may have elevated cancel rates during
  volatility spikes. Check if the cancel spike correlates with market-wide volatility.
- INDEX REBALANCING: Funds buying heavily at close may be rebalancing benchmark positions, not
  manipulating. account_type=fund + close session → raise FP probability.
- INTERNAL TRANSFERS: Wash-trade-like patterns between related accounts may be legitimate portfolio
  transfers. Look for prior authorisation indicators.
- PINGING: Small rapid orders may be legitimate order-book probing, not manipulation.

## OUTPUT FORMAT
Respond ONLY with valid JSON matching this exact structure:
{
  "verdict": "ESCALATE" | "REVIEW" | "DISMISS",
  "confidence": <float 0.0-1.0>,
  "false_positive_probability": <float 0.0-1.0>,
  "rationale": "<2-4 sentences of plain English reasoning>",
  "key_factors": ["<factor 1>", "<factor 2>", "<factor 3>"],
  "recommended_action": "<specific next step for compliance team>"
}

ESCALATE: genuine manipulation, high confidence, needs immediate regulatory escalation.
REVIEW: suspicious but legitimate explanation possible, needs human review.
DISMISS: false positive, legitimate trading behaviour.

When the verdict is ESCALATE or REVIEW, cite the applicable regulation in your rationale
(e.g., "prohibited under MAR Article 12(1)(a)(ii)" or "per SEC Rule 10b-5").
"""


def _get_jurisdiction_context(jurisdiction: str) -> str:
    """Return jurisdiction-specific regulatory guidance for the triage prompt."""
    contexts = {
        "EU": (
            "Jurisdiction: European Union (MAR/MiFID II)\n"
            "  - Primary regulation: EU Market Abuse Regulation (MAR) No 596/2014\n"
            "  - Market manipulation: Article 12(1)(a) — fictitious devices, price positioning\n"
            "  - Obligation to report: Article 16 — suspicious transaction reports (STORs) to NCA\n"
            "  - Penalties: Up to EUR 5M (individuals) / EUR 15M or 15% turnover (firms)\n"
            "  - Reporting deadline: STORs must be filed 'without delay' upon reasonable suspicion"
        ),
        "US": (
            "Jurisdiction: United States (SEC/CFTC)\n"
            "  - Primary regulation: Securities Exchange Act §9(a), SEC Rule 10b-5\n"
            "  - Dodd-Frank Act §747 (anti-manipulation for swaps/commodities)\n"
            "  - Obligation to report: SAR filing via FinCEN within 30 days\n"
            "  - Penalties: Up to $5M and/or 20 years imprisonment per violation\n"
            "  - Additional: FINRA Rule 5210 (manipulation), Rule 6140 (anti-spoofing)"
        ),
        "UK": (
            "Jurisdiction: United Kingdom (FCA)\n"
            "  - Primary regulation: UK MAR (retained EU law), Financial Services Act 2012 §89-91\n"
            "  - FCA Handbook MAR 1.6 — manipulating transactions\n"
            "  - Obligation to report: STORs to FCA within 'without delay'\n"
            "  - Penalties: Unlimited fines, up to 7 years imprisonment\n"
            "  - Additional: FCA Decision Procedure and Penalties Manual (DEPP)"
        ),
        "IN": (
            "Jurisdiction: India (SEBI)\n"
            "  - Primary regulation: SEBI (Prohibition of Fraudulent & Unfair Trade Practices) Regulations 2003\n"
            "  - Reg 4(2)(a): market manipulation, Reg 4(2)(c): wash sales, Reg 4(2)(e): marking close\n"
            "  - Obligation to report: STR to FIU-IND under PMLA 2002\n"
            "  - Penalties: Up to INR 25 Cr or 3x profit, disgorgement + interest\n"
            "  - Additional: SEBI Circular SEBI/HO/ISD/ISD-SEC-4/P/CIR/2023/155"
        ),
        "JP": (
            "Jurisdiction: Japan (JFSA/SESC)\n"
            "  - Primary regulation: Financial Instruments and Exchange Act (FIEA) Art. 157-159\n"
            "  - Market manipulation: Art. 159 — prohibited manipulative acts\n"
            "  - Obligation to report: To Securities and Exchange Surveillance Commission (SESC)\n"
            "  - Penalties: Up to JPY 10M and/or 10 years imprisonment"
        ),
    }
    return contexts.get(jurisdiction, (
        "Jurisdiction: UNKNOWN — apply general anti-manipulation principles.\n"
        "  Consider both MAR (EU), SEC Rule 10b-5 (US), and SEBI PFUTP (India) standards.\n"
        "  Flag for jurisdiction determination before filing any regulatory report."
    ))


def build_user_prompt(alert: Alert, profile: TraderProfile, prior_alert_count: int,
                      on_watchlist: bool) -> str:
    """Build the dynamic per-alert user prompt."""
    evidence_str = json.dumps(alert.evidence, indent=2)
    # Jurisdiction-specific regulatory context
    jurisdiction = getattr(alert, 'jurisdiction', 'UNKNOWN')
    jurisdiction_context = _get_jurisdiction_context(jurisdiction)

    return f"""ALERT TO TRIAGE:

Alert ID: {alert.alert_id}
Pattern Type: {alert.pattern_type}
Trader: {alert.trader_id}
Instrument: {alert.instrument}
Jurisdiction: {jurisdiction}
Detection Time: {alert.detected_at.isoformat()}
Severity: {alert.severity}

STATISTICAL ANOMALY:
- Observed metric: {alert.observed_metric:.4f}
- Baseline (30-day): {alert.baseline_metric:.4f}
- Anomaly: +{alert.z_score:.1f}σ vs trader's own history
- Baseline description: {alert.baseline_description}

PATTERN-SPECIFIC EVIDENCE:
{evidence_str}

TRADER CONTEXT:
- Account type: {profile.account_type}
- Market maker registered: {profile.market_maker_registered}
- On watchlist: {on_watchlist}
- Prior alerts (last 90 days): {prior_alert_count}
- Beneficial owner group: {"flagged" if profile.beneficial_owner_id else "none"}

REGULATORY CONTEXT:
{jurisdiction_context}

Triage this alert. The higher the z_score and the less the account_type explains the behaviour,
the lower the false_positive_probability should be. Cite the applicable jurisdiction-specific
regulation in your rationale."""


def build_batch_user_prompt(alerts: list[Alert], profiles: dict, prior_counts: dict,
                            watchlist_set: set) -> str:
    """Build a single prompt for batching multiple LOW/MEDIUM alerts."""
    parts = []
    for i, alert in enumerate(alerts, 1):
        profile = profiles.get(alert.trader_id)
        prior = prior_counts.get(alert.trader_id, 0)
        on_wl = alert.trader_id in watchlist_set
        parts.append(f"=== ALERT {i} ===\n" + build_user_prompt(alert, profile, prior, on_wl))

    batch_prompt = "\n\n".join(parts)
    batch_prompt += f"\n\nProvide a JSON ARRAY with exactly {len(alerts)} triage objects in the same order as the alerts above."
    return batch_prompt


# ─── 7-Day Trader History Formatter ────────────────────────────────────────────

def format_7day_history(summary) -> str:
    """
    Format a Trader7DaySummary into a concise text block for the investigation prompt.
    """
    if summary.is_new_trader:
        source = "Market-wide averages (NEW TRADER — no prior trading history)"
        note = (
            "\nNOTE: This trader has NO prior trading history in our system. "
            "Market-wide averages are shown for reference. Any deviation from "
            "market norms should be evaluated carefully, but also consider that "
            "the trader lacks an established personal baseline."
        )
    else:
        source = "Trader's own trading history"
        # Determine consistency note
        if summary.cancel_ratio_daily_std < 0.05:
            consistency = "VERY CONSISTENT (habitual)"
            note = (
                f"\nNOTE: This trader's cancel ratio is highly consistent "
                f"(daily std={summary.cancel_ratio_daily_std:.4f}). "
                f"If the alerted cancel ratio is within this 7-day range, "
                f"it likely represents normal behavior for this trader rather than an anomaly."
            )
        elif summary.cancel_ratio_daily_std < 0.15:
            consistency = "moderately consistent"
            note = ""
        else:
            consistency = "variable (inconsistent)"
            note = (
                f"\nNOTE: This trader's behavior is variable across the 7-day window "
                f"(cancel ratio std={summary.cancel_ratio_daily_std:.4f}). "
                f"Sudden spikes are harder to distinguish from normal variance."
            )

    # Format instruments
    instr_str = ", ".join(summary.instruments_traded) if summary.instruments_traded else "None"

    # Format session distribution
    sess_parts = []
    for s in ["PRE", "REGULAR", "CLOSE"]:
        pct = summary.session_distribution.get(s, 0.0)
        sess_parts.append(f"{s} {round(pct * 100)}%")
    sess_str = " | ".join(sess_parts)

    def z_fmt(z: float) -> str:
        return f"+{z:.1f}σ" if z >= 0 else f"{z:.1f}σ"

    return f"""TRADER ACTIVITY HISTORY (LAST 7 DAYS)
Source: {source}
Period: {summary.date_range} ({summary.days_covered} active trading days)

Daily Averages:
  Orders placed:      {summary.avg_daily_orders:.0f}/day
  Orders cancelled:   {summary.avg_daily_cancels:.0f}/day  (cancel ratio: {summary.avg_cancel_ratio * 100:.1f}%, z={z_fmt(summary.cancel_ratio_zscore)} vs baseline)
  Trades executed:    {summary.avg_daily_executions:.0f}/day
  Volume executed:    {summary.avg_daily_volume:,.0f} units/day (z={z_fmt(summary.daily_volume_zscore)} vs baseline)
  Order-to-trade:     {summary.avg_order_to_trade_ratio:.1f} (z={z_fmt(summary.order_to_trade_zscore)} vs baseline)
  Median TTC:         {summary.avg_median_ttc_ms:,.0f}ms
  Close vol fraction: {summary.close_volume_fraction * 100:.1f}% (z={z_fmt(summary.close_volume_zscore)} vs baseline)

Instruments: {instr_str}
Sessions: {sess_str}
Side Bias: {summary.side_bias * 100:.0f}% BUY / {(1 - summary.side_bias) * 100:.0f}% SELL

Behavior Consistency (std across {summary.days_covered} days):
  Cancel ratio: std={summary.cancel_ratio_daily_std:.4f}{f' ({consistency})' if not summary.is_new_trader else ''}
  Daily volume: std={summary.daily_volume_daily_std:,.0f}
{note}"""


# ─── Cross-alert trader investigation ──────────────────────────────────────────

def build_investigate_prompt(
    trader_id: str,
    alerts: list,
    profiles: dict,
    prior_count: int,
    on_watchlist: bool,
    trader_history: str = "",
) -> str:
    """
    Build a prompt asking Claude to assess whether multiple alerts for the same
    trader represent a coordinated manipulation scheme or independent events.
    """
    import json

    profile = profiles.get(trader_id)
    acct_type = profile.account_type if profile else "unknown"
    mm_reg = profile.market_maker_registered if profile else False

    alerts_block = ""
    for i, alert in enumerate(alerts, 1):
        alerts_block += (
            f"\nAlert {i}: {alert.alert_id}\n"
            f"  Pattern:  {alert.pattern_type}\n"
            f"  Instrument: {alert.instrument}\n"
            f"  Severity: {alert.severity}\n"
            f"  Z-Score:  +{alert.z_score:.1f}σ\n"
            f"  Detected: {alert.detected_at.strftime('%Y-%m-%d %H:%M:%S')}\n"
            f"  Evidence: {json.dumps(alert.evidence, indent=4)}\n"
        )

    # Include trader history block if available
    history_section = ""
    if trader_history:
        history_section = f"\n{trader_history}\n"

    return f"""CROSS-ALERT TRADER INVESTIGATION

Trader: {trader_id}
Account Type: {acct_type}
Market Maker Registered: {mm_reg}
On Watchlist: {on_watchlist}
Prior alerts (last 90 days): {prior_count}
Total alerts this session: {len(alerts)}
{history_section}
ALERTS FOR THIS TRADER:
{alerts_block}

TASK: Provide a MULTI-SECTION deep-dive investigation of this trader. Assess whether these {len(alerts)} alert(s) represent:
1. A COORDINATED MANIPULATION SCHEME (multiple patterns working together)
2. INDEPENDENT UNRELATED EVENTS (coincidental and unconnected)
3. SYSTEMATIC BEHAVIOUR (same pattern repeated, habitual manipulation)

Consider the trader's recent 7-day activity history when assessing anomaly significance.
If the alerted behavior is consistent with the trader's recent pattern (low daily variance in key metrics),
note this as a mitigating factor — it may represent habitual behavior rather than sudden manipulation.
If the behavior represents a sudden departure from the 7-day trend, note this as an aggravating factor.

Respond with JSON containing these assessment sections:
{{
  "scheme_type": "COORDINATED" | "INDEPENDENT" | "SYSTEMATIC" | "SINGLE",
  "escalation_recommendation": "IMMEDIATE" | "ELEVATED" | "STANDARD" | "DISMISS",
  "coordinated_confidence": <float 0.0-1.0>,
  "combined_risk_score": <float 0.0-1.0>,

  "sections": {{
    "cross_alert_analysis": {{
      "title": "Cross-Alert Correlation Analysis",
      "finding": "<2-3 sentences: how the alerts relate to each other, timing correlations, instrument overlaps>",
      "risk_level": "HIGH" | "MEDIUM" | "LOW"
    }},
    "behavioral_assessment": {{
      "title": "Behavioral Pattern Assessment",
      "finding": "<2-3 sentences: what the trading behavior reveals about intent — compare with 7-day history, note consistency or deviation>",
      "risk_level": "HIGH" | "MEDIUM" | "LOW"
    }},
    "market_impact": {{
      "title": "Market Impact Analysis",
      "finding": "<2-3 sentences: observed or potential impact on price discovery, liquidity, other market participants>",
      "risk_level": "HIGH" | "MEDIUM" | "LOW"
    }},
    "regulatory_assessment": {{
      "title": "Regulatory Compliance Assessment",
      "finding": "<2-3 sentences: which regulations may be violated (MAR Art.12, Dodd-Frank, MiFID II), reporting obligations>",
      "flags": ["<specific regulatory flag>"],
      "risk_level": "HIGH" | "MEDIUM" | "LOW"
    }},
    "historical_context": {{
      "title": "Historical Context & 7-Day Trend",
      "finding": "<2-3 sentences: how current behavior compares to the 7-day trading history, whether this is escalating/consistent/new behavior, significance of z-scores>",
      "risk_level": "HIGH" | "MEDIUM" | "LOW"
    }}
  }},

  "risk_factors": [
    {{"factor": "<factor name>", "score": <int 0-100>, "detail": "<short explanation>"}}
  ],

  "cross_alert_rationale": "<3-5 sentence executive summary incorporating 7-day behavioral context>",
  "regulatory_flags": ["<flag1>", "<flag2>"],
  "recommended_action": "<specific next step for L2 surveillance desk>"
}}"""


# ─── Daily compliance report ────────────────────────────────────────────────────

def build_daily_report_prompt(
    alerts: list,
    triage_results: list,
    watchlist_status: list,
    date_str: str = "",
) -> str:
    """
    Build a prompt asking Claude to synthesize all triage results into a
    professional compliance officer narrative report.
    """
    import json

    if not date_str:
        from datetime import datetime
        date_str = datetime.utcnow().strftime("%Y-%m-%d")

    escalated = [r for r in triage_results if r.verdict == "ESCALATE"]
    reviewed   = [r for r in triage_results if r.verdict == "REVIEW"]
    dismissed  = [r for r in triage_results if r.verdict == "DISMISS"]
    fp_rate    = round(100 * len(dismissed) / max(len(triage_results), 1), 1)

    # Build alert + verdict summary lines
    summary_lines = []
    alert_map = {a.alert_id: a for a in alerts}
    for r in triage_results:
        a = alert_map.get(r.alert_id)
        if a:
            summary_lines.append(
                f"  [{r.verdict:8s}] {a.alert_id} | {a.pattern_type:22s} | "
                f"{a.instrument} | {a.trader_id} | {r.confidence*100:.0f}% confidence"
            )

    watchlist_lines = [
        f"  {e['trader_id']} — {e['reason']} — {e['hours_remaining']}h remaining"
        for e in watchlist_status
    ] or ["  (none)"]

    return f"""DAILY SURVEILLANCE REPORT — {date_str}

SUMMARY STATISTICS:
  Total alerts processed: {len(alerts)}
  ESCALATE: {len(escalated)}  |  REVIEW: {len(reviewed)}  |  DISMISS: {len(dismissed)}
  False positive suppression rate: {fp_rate}%
  Traders on enhanced monitoring: {len(watchlist_status)}

ALERT VERDICTS:
{chr(10).join(summary_lines) if summary_lines else "  (none)"}

ACTIVE WATCHLIST:
{chr(10).join(watchlist_lines)}

TASK: Write a concise, professional Daily Surveillance Report suitable for the Head of
Compliance. The report should:
1. Open with a one-sentence executive summary of the day's risk level
2. Describe each ESCALATE verdict in plain English with the key evidence
3. Note any REVIEW items that need analyst follow-up
4. Comment on false positive rate and what it means for detection calibration
5. List traders on enhanced monitoring and why
6. Close with recommended priority actions for the next trading day

Write in a formal, factual tone. Use specific alert IDs, trader IDs, and instrument names.
The report should be 300-500 words and ready to send directly to compliance leadership.

Respond with JSON:
{{
  "report_date": "{date_str}",
  "risk_level": "CRITICAL" | "HIGH" | "MEDIUM" | "LOW",
  "executive_summary": "<one sentence>",
  "full_report": "<the complete 300-500 word compliance officer narrative>",
  "priority_actions": ["<action 1>", "<action 2>", "<action 3>"],
  "next_day_focus": "<what the surveillance team should watch tomorrow>"
}}"""


# ─── Natural language query ─────────────────────────────────────────────────────

def build_nl_query_prompt(question: str, available_patterns: list[str],
                           available_severities: list[str]) -> str:
    """
    Build a prompt asking Claude to parse a natural language question into
    structured filter parameters for the alerts database.
    """
    return f"""You are a compliance analyst assistant. Parse the following natural language
question into structured query filters for the trade surveillance alert database.

Available pattern types: {available_patterns}
Available severity levels: {available_severities}
Available verdicts: ["ESCALATE", "REVIEW", "DISMISS"]

Question: "{question}"

Respond ONLY with valid JSON:
{{
  "severity": <string or null>,
  "pattern_type": <string or null>,
  "verdict": <string or null>,
  "min_confidence": <float 0.0-1.0 or null>,
  "max_fp_probability": <float 0.0-1.0 or null>,
  "trader_id": <string or null>,
  "instrument": <string or null>,
  "interpretation": "<one sentence explaining how you interpreted the question>"
}}

Use null for any filter not mentioned in the question.
Match pattern_type and severity to the closest valid value from the available lists."""
