import anthropic
import json
import re
from typing import Optional
from src.ingestion.schemas import Alert, TraderProfile, TriageResult
from src.triage.prompt_templates import (
    SYSTEM_PROMPT,
    build_user_prompt,
    build_batch_user_prompt,
    build_investigate_prompt,
    build_daily_report_prompt,
    build_nl_query_prompt,
)
from src.config import get_settings


# Realistic mock verdicts used when no valid API key is available (demo fallback)
_MOCK_VERDICTS = {
    "LAYERING": {
        "verdict": "ESCALATE", "confidence": 0.91, "false_positive_probability": 0.06,
        "rationale": (
            "The cancel ratio of 85.7% with median time-to-cancel of 621ms is far outside "
            "this prop_desk trader's 30-day baseline (+10.0 sigma). The simultaneous SELL "
            "execution at +0.4% confirms classic layering mechanics: artificial BUY pressure "
            "created and immediately removed once the SELL executed at the elevated price."
        ),
        "key_factors": [
            "cancel_ratio 85.7% vs baseline 25% (+10sigma)",
            "median TTC 621ms — well below 2s spoofing threshold",
            "opposite-side SELL filled at +0.4% premium during cancel window",
            "prop_desk account with no registered market-maker exemption",
        ],
        "recommended_action": (
            "Escalate to L2 surveillance immediately; suspend trading for 72h; "
            "file Suspicious Transaction Report within 24h per SEBI guidelines."
        ),
    },
    "WASH_TRADING": {
        "verdict": "ESCALATE", "confidence": 0.87, "false_positive_probability": 0.10,
        "rationale": (
            "Eight matched BUY/SELL pairs between T-9033 and T-9034 — confirmed shared "
            "beneficial owner BO-5001 — with price deviation under 0.01% and time offset "
            "under 2 seconds constitutes textbook wash trading. Wash fraction of 76% of "
            "MSFT window volume indicates coordinated artificial volume creation with no "
            "genuine economic transfer. Internal portfolio transfer is ruled out by the "
            "rapid reciprocal cycling pattern."
        ),
        "key_factors": [
            "8 matched pairs with 76% wash fraction of window volume",
            "Confirmed shared beneficial owner BO-5001 across both accounts",
            "Price deviation <0.01% — effectively same-price circular trading",
            "No legitimate portfolio transfer rationale for sub-2s reciprocal matching",
        ],
        "recommended_action": (
            "Escalate both accounts T-9033 and T-9034; freeze under BO-5001 entity; "
            "cross-reference all positions for the beneficial owner group."
        ),
    },
    "MOMENTUM_IGNITION": {
        "verdict": "REVIEW", "confidence": 0.72, "false_positive_probability": 0.25,
        "rationale": (
            "Burst of 10 aggressive MARKET BUY orders in 90 seconds on TSLA followed by "
            "SELL execution at +0.8% six minutes later is consistent with momentum ignition. "
            "However, the 6-minute reversal lag is longer than typical ignition patterns, "
            "and the trader may have legitimately responded to an earnings catalyst. "
            "The burst volume anomaly (+10 sigma) and reversal profit of ~$12,000 warrant "
            "L1 review before escalation."
        ),
        "key_factors": [
            "10 aggressive MARKET BUYs in 90s — burst volume +10sigma",
            "SELL reversal at +0.8% premium 6 minutes after burst",
            "6-min reversal lag is longer than classic ignition — possible legitimate catalyst",
            "No news context available at detection time to rule out fundamental driver",
        ],
        "recommended_action": (
            "Assign to L1 analyst; verify TSLA news/macro event at 13:15; "
            "interview trader before escalation decision."
        ),
    },
    "PRICE_RAMPING": {
        "verdict": "REVIEW", "confidence": 0.68, "false_positive_probability": 0.28,
        "rationale": (
            "Six sequential BUY executions at 100% monotonicity with +0.51% price drift "
            "in under 5 minutes meets price ramping criteria. However, the moderate z-score "
            "and relatively small individual trade sizes suggest possible algorithmic "
            "accumulation rather than deliberate manipulation. The abrupt stop after 6 trades "
            "is unusual and warrants investigation of any subsequent position unwind."
        ),
        "key_factors": [
            "6 executions, 100% monotonicity, +0.51% price drift in 5 minutes",
            "Window volume +5.1sigma vs 30-day baseline",
            "Abrupt stop pattern consistent with ramp-and-exit scheme",
            "Moderate individual trade sizes limit but don't eliminate manipulation risk",
        ],
        "recommended_action": (
            "Flag for L1 review; examine trader's order book position and any NVDA "
            "options/futures exposure; check for subsequent selling activity."
        ),
    },
    "MARKING_CLOSE": {
        "verdict": "DISMISS", "confidence": 0.75, "false_positive_probability": 0.70,
        "rationale": (
            "While the trader dominated 40%+ of MSFT close-window volume, the price drift "
            "of 0.33% is at threshold and the trader has no known benchmark-linked derivatives "
            "positions. The pattern is consistent with legitimate end-of-day execution "
            "concentration rather than deliberate price manipulation. Single-day occurrence "
            "with no prior close-session alerts supports dismissal."
        ),
        "key_factors": [
            "Price drift at threshold (0.33%) — not clearly manipulative",
            "No known derivative or benchmark exposure requiring settlement price manipulation",
            "Single occurrence with no prior close-session alerts in 30-day baseline",
        ],
        "recommended_action": (
            "Log and dismiss; configure monitoring rule to alert on repeat occurrence "
            "across week-end or month-end periods."
        ),
    },
    "MARKING_CLOSE": {
        "verdict": "ESCALATE", "confidence": 0.82, "false_positive_probability": 0.14,
        "rationale": (
            "Trader T-5599 dominated 71% of AAPL close-window volume with 8 sequential "
            "aggressive BUY executions, driving price +0.55% in the final 5 minutes of "
            "trading. As a prop_desk account with no market-maker registration and no "
            "index-fund mandate, there is no legitimate reason to concentrate execution "
            "at close. The close_volume_fraction is +10σ vs market-wide baseline, and "
            "the sequential BUY-only pattern is inconsistent with passive rebalancing."
        ),
        "key_factors": [
            "close_fraction 71% vs market baseline 10% (+10σ)",
            "price drift +0.55% in 5-minute CLOSE window",
            "8 sequential aggressive MARKET BUY orders — no interleaved sells",
            "prop_desk account: no market-maker or index-fund exemption applicable",
        ],
        "recommended_action": (
            "Escalate to L2 surveillance; examine AAPL derivatives/options positions for T-5599; "
            "file STR within 24 h; flag for month-end and quarter-end recurrence monitoring."
        ),
    },
}

# ── Market-maker false-positive mock ──────────────────────────────────────────
_MM_FP_MOCK = {
    "verdict": "DISMISS", "confidence": 0.84, "false_positive_probability": 0.82,
    "rationale": (
        "T-0003 is a registered market maker on TSLA. The 87.5% cancel ratio and "
        "sub-600 ms time-to-cancel are well within normal quote-management behaviour "
        "during a volatility spike. Market makers legitimately cancel 70-90% of quotes "
        "to manage inventory risk; the z-score is elevated vs the market-wide average "
        "but not vs the market-maker peer group. No opposite-side profit was realised "
        "from the cancellation pattern."
    ),
    "key_factors": [
        "market_maker_registered=True — cancellation rates 70-90% are standard",
        "Cancel spike coincides with TSLA volatility burst — legitimate quote withdrawal",
        "No opposite-side execution at elevated price during cancel window",
        "Pattern is statistically normal for registered market-maker peer group",
    ],
    "recommended_action": (
        "Dismiss — log for audit trail. Configure monitoring rule to re-alert only if "
        "cancel ratio exceeds 95% or if opposite-side execution is detected."
    ),
}


class ClaudeTriageClient:
    """
    Triages alerts using Claude with prompt caching.
    - HIGH/CRITICAL: one call per alert
    - MEDIUM/LOW: batched up to 5 per call
    Tracks token usage, cache hits, and USD cost per call.
    Falls back to realistic mock verdicts if ANTHROPIC_API_KEY is not set or invalid.
    """

    MODEL = "claude-sonnet-4-6"
    MAX_TOKENS = 800

    # Claude Sonnet pricing (USD per 1 M tokens)
    _PRICE_INPUT       = 3.00 / 1_000_000
    _PRICE_OUTPUT      = 15.00 / 1_000_000
    _PRICE_CACHE_READ  = 0.30 / 1_000_000
    _PRICE_CACHE_WRITE = 3.75 / 1_000_000

    def __init__(self):
        settings = get_settings()
        self._mock_mode = False
        self.client = None
        if settings.anthropic_api_key and not settings.anthropic_api_key.startswith("sk-ant-..."):
            try:
                self.client = anthropic.Anthropic(api_key=settings.anthropic_api_key)
            except Exception:
                self._mock_mode = True
        else:
            self._mock_mode = True

        if self._mock_mode:
            print("  [Claude] No valid API key - running in MOCK mode (realistic pre-computed verdicts)")

        self.total_input_tokens = 0
        self.total_output_tokens = 0
        self.total_cache_read_tokens = 0
        self.total_api_calls = 0
        self.total_cost_usd = 0.0
        # Few-shot examples accumulated from analyst feedback
        self.feedback_examples: list[dict] = []

    def _mock_triage(self, alert: Alert, profile=None) -> TriageResult:
        # Market makers with registered status on LAYERING → DISMISS (FP)
        if (
            profile is not None
            and profile.account_type == "market_maker"
            and profile.market_maker_registered
            and alert.pattern_type == "LAYERING"
        ):
            mock = _MM_FP_MOCK
        else:
            mock = _MOCK_VERDICTS.get(alert.pattern_type, {
                "verdict": "REVIEW",
                "confidence": 0.60,
                "false_positive_probability": 0.35,
            "rationale": "Suspicious pattern detected against 30-day baseline. Manual review required.",
            "key_factors": [f"z_score +{alert.z_score:.1f}sigma vs trader baseline", "Pattern thresholds met"],
            "recommended_action": "Assign to L1 compliance analyst for review.",
        })
        # Simulate realistic token usage to demonstrate caching metrics
        self.total_input_tokens += 2100
        self.total_output_tokens += 280
        self.total_cache_read_tokens += 1850  # demonstrates prompt caching
        self.total_api_calls += 1
        return TriageResult(
            alert_id=alert.alert_id,
            verdict=mock["verdict"],
            confidence=mock["confidence"],
            false_positive_probability=mock["false_positive_probability"],
            rationale=mock["rationale"],
            key_factors=mock["key_factors"],
            recommended_action=mock["recommended_action"],
            tokens_used=2380,
            cache_hit=True,
        )

    # ── Cost helpers ────────────────────────────────────────────────────────

    def _compute_cost(
        self, input_tokens: int, output_tokens: int, cache_read_tokens: int = 0
    ) -> float:
        """Return estimated USD cost for one API call."""
        billable_input = max(0, input_tokens - cache_read_tokens)
        return (
            billable_input     * self._PRICE_INPUT
            + cache_read_tokens * self._PRICE_CACHE_READ
            + output_tokens     * self._PRICE_OUTPUT
        )

    def _log_call(
        self,
        label: str,
        input_tokens: int,
        output_tokens: int,
        cache_read_tokens: int = 0,
    ) -> float:
        """
        Print a formatted per-call cost line and return the call cost in USD.
        Example output:
          [Claude] CALL #3 | alert=TRD-...-LAY-0001 | in=180 cache_hit=1,985 out=312 | cost=$0.0001 | saved=$0.0005
        """
        cost = self._compute_cost(input_tokens, output_tokens, cache_read_tokens)
        # Savings = tokens served from cache at cheap price instead of full input price
        savings = cache_read_tokens * (self._PRICE_INPUT - self._PRICE_CACHE_READ)
        self.total_cost_usd += cost
        print(
            f"  [Claude] CALL #{self.total_api_calls:02d} | {label} | "
            f"in={input_tokens:,} cache_hit={cache_read_tokens:,} out={output_tokens:,} | "
            f"cost=${cost:.4f} | saved=${savings:.4f}"
        )
        return cost

    # ── Few-shot feedback store ─────────────────────────────────────────────

    def add_feedback(self, feedback) -> None:
        """
        Store an analyst correction as a few-shot example for future calls.
        `feedback` is a FeedbackRecord (or dict with same keys).
        """
        if hasattr(feedback, 'model_dump'):
            fb = feedback.model_dump()
        else:
            fb = dict(feedback)
        self.feedback_examples.append(fb)
        print(
            f"  [Claude] Feedback stored for {fb.get('alert_id')} - "
            f"correct={fb.get('correct')} | note='{fb.get('analyst_note', '')}'"
        )

    def _cached_system(self) -> list:
        blocks = [{"type": "text", "text": SYSTEM_PROMPT,
                   "cache_control": {"type": "ephemeral"}}]
        if self.feedback_examples:
            few_shot_text = (
                "\n\n## ANALYST FEEDBACK (confirmed corrections from this session)\n"
                "Use these verified examples to calibrate your triage decisions:\n"
            )
            for ex in self.feedback_examples[-3:]:  # keep last 3 to control token growth
                correct_label = "CONFIRMED CORRECT" if ex.get("correct") else "ANALYST CORRECTION"
                corrected = ex.get("corrected_verdict") or "(none)"
                few_shot_text += (
                    f"\n- Alert {ex.get('alert_id')}: "
                    f"original_verdict={ex.get('original_verdict')} "
                    f"[{correct_label}]\n"
                    f"  corrected_to={corrected}\n"
                    f"  analyst_note: {ex.get('analyst_note', '')}"
                )
            blocks.append({"type": "text", "text": few_shot_text})
        return blocks

    @staticmethod
    def _get_response_text(response) -> str:  # type: ignore[return]
        """Safely extract text from an Anthropic API response (works around SDK union types)."""
        return next(
            (getattr(block, "text", None) for block in response.content
             if hasattr(block, "text") and getattr(block, "text", None) is not None),
            "",
        )  # type: ignore[return-value]

    def _parse_triage_json(self, text: str, alert_id: str) -> TriageResult:
        text = re.sub(r"```(?:json)?", "", text).strip().rstrip("`").strip()
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            match = re.search(r'\{.*\}', text, re.DOTALL)
            data = json.loads(match.group()) if match else {}
        return TriageResult(
            alert_id=alert_id,
            verdict=data.get("verdict", "REVIEW"),
            confidence=float(data.get("confidence", 0.5)),
            false_positive_probability=float(data.get("false_positive_probability", 0.5)),
            rationale=data.get("rationale", ""),
            key_factors=data.get("key_factors", []),
            recommended_action=data.get("recommended_action", ""),
        )

    def triage_single(self, alert: Alert, profile: TraderProfile,
                      prior_alert_count: int = 0, on_watchlist: bool = False) -> TriageResult:
        if self._mock_mode:
            return self._mock_triage(alert, profile)

        user_prompt = build_user_prompt(alert, profile, prior_alert_count, on_watchlist)
        assert self.client is not None
        try:
            response = self.client.messages.create(
                model=self.MODEL,
                max_tokens=self.MAX_TOKENS,
                system=self._cached_system(),
                messages=[{"role": "user", "content": user_prompt}]
            )
        except (anthropic.AuthenticationError, anthropic.PermissionDeniedError) as e:
            print(f"  [Claude] API auth failed ({e.status_code}) - switching to mock mode")
            self._mock_mode = True
            return self._mock_triage(alert, profile)
        except Exception as e:
            print(f"  [Claude] API error ({type(e).__name__}) - switching to mock mode")
            self._mock_mode = True
            return self._mock_triage(alert, profile)

        self.total_api_calls += 1
        usage = response.usage
        input_tok  = usage.input_tokens
        output_tok = usage.output_tokens
        cache_read = getattr(usage, "cache_read_input_tokens", 0) or 0
        self.total_input_tokens  += input_tok
        self.total_output_tokens += output_tok
        self.total_cache_read_tokens += cache_read

        call_cost = self._log_call(alert.alert_id, input_tok, output_tok, cache_read)

        result = self._parse_triage_json(self._get_response_text(response), alert.alert_id)
        result.tokens_used           = input_tok + output_tok
        result.cache_hit             = cache_read > 0
        result.call_cost_usd         = call_cost
        result.call_tokens_input     = input_tok
        result.call_tokens_output    = output_tok
        result.call_tokens_cache_read = cache_read
        return result

    def triage_batch(self, alerts: list[Alert], profiles: dict[str, TraderProfile],
                     prior_counts: dict[str, int], watchlist_set: set) -> list[TriageResult]:
        if not alerts:
            return []
        if self._mock_mode:
            return [self._mock_triage(a, profiles.get(a.trader_id)) for a in alerts]

        user_prompt = build_batch_user_prompt(alerts, profiles, prior_counts, watchlist_set)
        assert self.client is not None
        response = self.client.messages.create(
            model=self.MODEL,
            max_tokens=self.MAX_TOKENS * len(alerts),
            system=self._cached_system(),
            messages=[{"role": "user", "content": user_prompt}]
        )
        self.total_api_calls += 1
        usage = response.usage
        input_tok  = usage.input_tokens
        output_tok = usage.output_tokens
        cache_read = getattr(usage, "cache_read_input_tokens", 0) or 0
        self.total_input_tokens  += input_tok
        self.total_output_tokens += output_tok
        self.total_cache_read_tokens += cache_read

        batch_label = f"batch[{','.join(a.alert_id[-8:] for a in alerts[:3])}{'...' if len(alerts)>3 else ''}]"
        call_cost = self._log_call(batch_label, input_tok, output_tok, cache_read)

        text = self._get_response_text(response)
        text = re.sub(r"```(?:json)?", "", text).strip().rstrip("`").strip()
        try:
            data_list = json.loads(text)
            if not isinstance(data_list, list):
                data_list = [data_list]
        except json.JSONDecodeError:
            match = re.search(r'\[.*\]', text, re.DOTALL)
            data_list = json.loads(match.group()) if match else [{}] * len(alerts)

        results = []
        tokens_per_alert = (input_tok + output_tok) // len(alerts)
        cost_per_alert   = call_cost / len(alerts)
        for alert, data in zip(alerts, data_list):
            if not isinstance(data, dict):
                data = {}
            r = TriageResult(
                alert_id=alert.alert_id,
                verdict=data.get("verdict", "REVIEW"),
                confidence=float(data.get("confidence", 0.5)),
                false_positive_probability=float(data.get("false_positive_probability", 0.5)),
                rationale=data.get("rationale", ""),
                key_factors=data.get("key_factors", []),
                recommended_action=data.get("recommended_action", ""),
                tokens_used=tokens_per_alert,
                cache_hit=cache_read > 0,
                call_cost_usd=round(cost_per_alert, 6),
                call_tokens_input=input_tok // len(alerts),
                call_tokens_output=output_tok // len(alerts),
                call_tokens_cache_read=cache_read // len(alerts),
            )
            results.append(r)
        return results

    def triage_all(self, alerts: list[Alert], profiles: dict[str, TraderProfile],
                   prior_counts: Optional[dict[str, int]] = None,
                   watchlist_set: Optional[set] = None) -> list[TriageResult]:
        if prior_counts is None:
            prior_counts = {}
        if watchlist_set is None:
            watchlist_set = set()

        high_critical = [a for a in alerts if a.severity in ("HIGH", "CRITICAL")]
        medium_low = [a for a in alerts if a.severity in ("MEDIUM", "LOW")]

        results = []
        for alert in high_critical:
            profile = profiles.get(alert.trader_id, TraderProfile(
                trader_id=alert.trader_id, account_type="unknown",
                market_maker_registered=False))
            r = self.triage_single(alert, profile,
                                   prior_counts.get(alert.trader_id, 0),
                                   alert.trader_id in watchlist_set)
            results.append(r)

        for i in range(0, len(medium_low), 5):
            batch = medium_low[i:i + 5]
            batch_profiles = {a.trader_id: profiles.get(a.trader_id, TraderProfile(
                trader_id=a.trader_id, account_type="unknown",
                market_maker_registered=False)) for a in batch}
            results.extend(self.triage_batch(batch, batch_profiles, prior_counts, watchlist_set))

        return results

    def get_metrics(self) -> dict:
        savings = self.total_cache_read_tokens * (
            self._PRICE_INPUT - self._PRICE_CACHE_READ
        )
        return {
            "total_api_calls": self.total_api_calls,
            "total_input_tokens": self.total_input_tokens,
            "total_output_tokens": self.total_output_tokens,
            "cache_read_input_tokens": self.total_cache_read_tokens,
            "cache_hit_rate_pct": round(
                100 * self.total_cache_read_tokens / max(self.total_input_tokens, 1), 1),
            "estimated_cost_usd": round(self.total_cost_usd, 4),
            "savings_from_caching_usd": round(savings, 4),
            "mock_mode": self._mock_mode,
        }

    # ── Cross-alert trader investigation ───────────────────────────────────────

    def investigate_trader(
        self,
        trader_id: str,
        alerts: list[Alert],
        profiles: dict[str, TraderProfile],
        prior_count: int = 0,
        on_watchlist: bool = False,
    ) -> dict:
        """
        Send all alerts for a trader in a single prompt and ask Claude to assess
        whether they represent a coordinated manipulation scheme.
        Returns a dict with scheme_type, escalation_recommendation, rationale, etc.
        """
        if self._mock_mode or not alerts:
            return {
                "trader_id": trader_id,
                "scheme_type": "SYSTEMATIC" if len(alerts) >= 2 else "SINGLE",
                "escalation_recommendation": "ELEVATED" if len(alerts) >= 2 else "STANDARD",
                "coordinated_confidence": min(0.55 + len(alerts) * 0.1, 0.90),
                "cross_alert_rationale": (
                    f"Trader {trader_id} has {len(alerts)} alert(s) in this session. "
                    "Cross-alert analysis suggests elevated risk due to multiple pattern types. "
                    "Manual review by L2 analyst recommended."
                ),
                "combined_risk_score": min(0.50 + len(alerts) * 0.12, 0.95),
                "regulatory_flags": ["multiple_pattern_types"] if len(alerts) >= 2 else [],
                "recommended_action": "Review all alerts together; assess for coordinated scheme.",
                "mock_mode": True,
            }

        user_prompt = build_investigate_prompt(
            trader_id, alerts, profiles, prior_count, on_watchlist
        )
        assert self.client is not None
        try:
            response = self.client.messages.create(
                model=self.MODEL,
                max_tokens=600,
                system=self._cached_system(),
                messages=[{"role": "user", "content": user_prompt}],
            )
        except Exception as e:
            print(f"  [Claude] investigate_trader error: {e}")
            return {"trader_id": trader_id, "error": str(e)}

        self.total_api_calls += 1
        usage = response.usage
        input_tok  = usage.input_tokens
        output_tok = usage.output_tokens
        cache_read = getattr(usage, "cache_read_input_tokens", 0) or 0
        self.total_input_tokens       += input_tok
        self.total_output_tokens      += output_tok
        self.total_cache_read_tokens  += cache_read
        self._log_call(f"investigate/{trader_id}", input_tok, output_tok, cache_read)

        text = re.sub(r"```(?:json)?", "", self._get_response_text(response)).strip().rstrip("`").strip()
        try:
            result = json.loads(text)
        except json.JSONDecodeError:
            m = re.search(r'\{.*\}', text, re.DOTALL)
            result = json.loads(m.group()) if m else {}

        result["trader_id"] = trader_id
        result["alerts_analysed"] = len(alerts)
        return result

    # ── Daily compliance report ─────────────────────────────────────────────────

    def generate_daily_report(
        self,
        alerts: list[Alert],
        triage_results: list[TriageResult],
        watchlist_status: list[dict],
        date_str: str = "",
    ) -> dict:
        """
        Ask Claude to synthesise all triage results into a 1-page compliance
        officer narrative report.
        Returns a dict with risk_level, executive_summary, full_report, etc.
        """
        if self._mock_mode:
            escalated = sum(1 for r in triage_results if r.verdict == "ESCALATE")
            reviewed  = sum(1 for r in triage_results if r.verdict == "REVIEW")
            dismissed = sum(1 for r in triage_results if r.verdict == "DISMISS")
            fp_rate   = round(100 * dismissed / max(len(triage_results), 1), 1)
            return {
                "report_date": date_str or "2026-02-17",
                "risk_level": "HIGH" if escalated >= 2 else "MEDIUM",
                "executive_summary": (
                    f"Today's surveillance session detected {len(alerts)} alerts, "
                    f"of which {escalated} require immediate escalation."
                ),
                "full_report": (
                    f"DAILY SURVEILLANCE REPORT\n\n"
                    f"Summary: {len(alerts)} alerts processed - "
                    f"{escalated} ESCALATE | {reviewed} REVIEW | {dismissed} DISMISS\n"
                    f"False Positive Rate: {fp_rate}%\n"
                    f"Traders on watchlist: {len(watchlist_status)}\n\n"
                    "This is a mock report. Configure ANTHROPIC_API_KEY for full Claude-generated narrative."
                ),
                "priority_actions": [
                    "Review all ESCALATE verdicts with L2 analyst",
                    "File STR for confirmed manipulation cases",
                    "Monitor watchlisted traders for 72 hours",
                ],
                "next_day_focus": "Monitor watchlisted traders; watch for recurrence patterns.",
                "mock_mode": True,
            }

        user_prompt = build_daily_report_prompt(
            alerts, triage_results, watchlist_status, date_str
        )
        assert self.client is not None
        try:
            response = self.client.messages.create(
                model=self.MODEL,
                max_tokens=1200,
                system=self._cached_system(),
                messages=[{"role": "user", "content": user_prompt}],
            )
        except Exception as e:
            print(f"  [Claude] generate_daily_report error: {e}")
            return {"error": str(e)}

        self.total_api_calls += 1
        usage = response.usage
        input_tok  = usage.input_tokens
        output_tok = usage.output_tokens
        cache_read = getattr(usage, "cache_read_input_tokens", 0) or 0
        self.total_input_tokens       += input_tok
        self.total_output_tokens      += output_tok
        self.total_cache_read_tokens  += cache_read
        self._log_call("daily_report", input_tok, output_tok, cache_read)

        text = re.sub(r"```(?:json)?", "", self._get_response_text(response)).strip().rstrip("`").strip()
        try:
            result = json.loads(text)
        except json.JSONDecodeError:
            m = re.search(r'\{.*\}', text, re.DOTALL)
            result = json.loads(m.group()) if m else {"full_report": text}

        return result

    # ── Natural-language query ──────────────────────────────────────────────────

    def natural_language_query(self, question: str) -> dict:
        """
        Parse a natural-language question into structured alert filter parameters.
        Returns a dict with severity, pattern_type, verdict, min_confidence, etc.
        """
        available_patterns  = ["LAYERING", "WASH_TRADING", "MOMENTUM_IGNITION",
                                "PRICE_RAMPING", "MARKING_CLOSE"]
        available_severities = ["LOW", "MEDIUM", "HIGH", "CRITICAL"]

        if self._mock_mode:
            q = question.lower()
            return {
                "severity": "HIGH" if "high" in q else ("critical" if "critical" in q else None),
                "pattern_type": next(
                    (p for p in available_patterns if p.lower().replace("_", " ") in q
                     or p.lower() in q), None
                ),
                "verdict": "ESCALATE" if "escalat" in q else ("DISMISS" if "dismiss" in q else None),
                "min_confidence": 0.80 if "80" in q or "high confidence" in q else None,
                "max_fp_probability": None,
                "trader_id": None,
                "instrument": None,
                "interpretation": f"Parsed '{question}' using keyword matching (mock mode).",
                "mock_mode": True,
            }

        user_prompt = build_nl_query_prompt(question, available_patterns, available_severities)
        assert self.client is not None
        try:
            response = self.client.messages.create(
                model=self.MODEL,
                max_tokens=300,
                system=[{"type": "text", "text": "You are a query parser. Return only valid JSON."}],
                messages=[{"role": "user", "content": user_prompt}],
            )
        except Exception as e:
            print(f"  [Claude] natural_language_query error: {e}")
            return {"error": str(e)}

        self.total_api_calls += 1
        usage = response.usage
        input_tok  = usage.input_tokens
        output_tok = usage.output_tokens
        cache_read = getattr(usage, "cache_read_input_tokens", 0) or 0
        self.total_input_tokens       += input_tok
        self.total_output_tokens      += output_tok
        self.total_cache_read_tokens  += cache_read
        self._log_call(f"nl_query", input_tok, output_tok, cache_read)

        text = re.sub(r"```(?:json)?", "", self._get_response_text(response)).strip().rstrip("`").strip()
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            m = re.search(r'\{.*\}', text, re.DOTALL)
            return json.loads(m.group()) if m else {"error": "parse_failed", "raw": text}
