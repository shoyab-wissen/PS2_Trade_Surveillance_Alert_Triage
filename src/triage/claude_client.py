import anthropic
import json
import re
from src.ingestion.schemas import Alert, TraderProfile, TriageResult
from src.triage.prompt_templates import SYSTEM_PROMPT, build_user_prompt, build_batch_user_prompt
from src.config import get_settings


class ClaudeTriageClient:
    """
    Triages alerts using Claude with prompt caching.
    - HIGH/CRITICAL: one call per alert
    - MEDIUM/LOW: batched up to 5 per call
    Tracks token usage and cache hits.
    """

    MODEL = "claude-sonnet-4-6"
    MAX_TOKENS = 800

    def __init__(self):
        settings = get_settings()
        self.client = anthropic.Anthropic(api_key=settings.anthropic_api_key)
        self.total_input_tokens = 0
        self.total_output_tokens = 0
        self.total_cache_read_tokens = 0
        self.total_api_calls = 0
        self._alert_counter = 0

    def _cached_system(self) -> list:
        """System prompt with cache_control for prompt caching."""
        return [{"type": "text", "text": SYSTEM_PROMPT,
                 "cache_control": {"type": "ephemeral"}}]

    def _parse_triage_json(self, text: str, alert_id: str) -> TriageResult:
        """Parse Claude's JSON response into TriageResult."""
        # Strip markdown code fences if present
        text = re.sub(r"```(?:json)?", "", text).strip().rstrip("`").strip()
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            # Fallback: extract JSON object
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

    def triage_single(self, alert: Alert, profile: TraderProfile, prior_alert_count: int = 0,
                      on_watchlist: bool = False) -> TriageResult:
        """Triage one HIGH/CRITICAL alert with a dedicated Claude call."""
        user_prompt = build_user_prompt(alert, profile, prior_alert_count, on_watchlist)

        response = self.client.messages.create(
            model=self.MODEL,
            max_tokens=self.MAX_TOKENS,
            system=self._cached_system(),
            messages=[{"role": "user", "content": user_prompt}]
        )

        self.total_api_calls += 1
        usage = response.usage
        self.total_input_tokens += usage.input_tokens
        self.total_output_tokens += usage.output_tokens
        cache_read = getattr(usage, 'cache_read_input_tokens', 0) or 0
        self.total_cache_read_tokens += cache_read

        result = self._parse_triage_json(response.content[0].text, alert.alert_id)
        result.tokens_used = usage.input_tokens + usage.output_tokens
        result.cache_hit = cache_read > 0
        return result

    def triage_batch(self, alerts: list[Alert], profiles: dict[str, TraderProfile],
                     prior_counts: dict[str, int], watchlist_set: set) -> list[TriageResult]:
        """Triage multiple MEDIUM/LOW alerts in one Claude call (up to 5)."""
        if not alerts:
            return []

        user_prompt = build_batch_user_prompt(alerts, profiles, prior_counts, watchlist_set)

        response = self.client.messages.create(
            model=self.MODEL,
            max_tokens=self.MAX_TOKENS * len(alerts),
            system=self._cached_system(),
            messages=[{"role": "user", "content": user_prompt}]
        )

        self.total_api_calls += 1
        usage = response.usage
        self.total_input_tokens += usage.input_tokens
        self.total_output_tokens += usage.output_tokens
        cache_read = getattr(usage, 'cache_read_input_tokens', 0) or 0
        self.total_cache_read_tokens += cache_read

        text = response.content[0].text
        text = re.sub(r"```(?:json)?", "", text).strip().rstrip("`").strip()

        try:
            data_list = json.loads(text)
            if not isinstance(data_list, list):
                data_list = [data_list]
        except json.JSONDecodeError:
            match = re.search(r'\[.*\]', text, re.DOTALL)
            data_list = json.loads(match.group()) if match else [{}] * len(alerts)

        results = []
        tokens_per_alert = (usage.input_tokens + usage.output_tokens) // len(alerts)
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
            )
            results.append(r)
        return results

    def triage_all(self, alerts: list[Alert], profiles: dict[str, TraderProfile],
                   prior_counts: dict[str, int] = None, watchlist_set: set = None) -> list[TriageResult]:
        """Main entry: route HIGH/CRITICAL individually, batch MEDIUM/LOW."""
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

        # batch in groups of 5
        for i in range(0, len(medium_low), 5):
            batch = medium_low[i:i + 5]
            batch_results = self.triage_batch(batch, profiles, prior_counts, watchlist_set)
            results.extend(batch_results)

        return results

    def get_metrics(self) -> dict:
        return {
            "total_api_calls": self.total_api_calls,
            "total_input_tokens": self.total_input_tokens,
            "total_output_tokens": self.total_output_tokens,
            "cache_read_input_tokens": self.total_cache_read_tokens,
            "cache_hit_rate_pct": round(
                100 * self.total_cache_read_tokens / max(self.total_input_tokens, 1), 1),
        }
