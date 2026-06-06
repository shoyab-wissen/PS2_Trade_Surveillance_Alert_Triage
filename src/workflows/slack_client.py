import httpx
from src.ingestion.schemas import Alert, TriageResult
from src.config import get_settings

SEVERITY_EMOJI = {"CRITICAL": "🚨", "HIGH": "🔴", "MEDIUM": "🟡", "LOW": "🟢"}
VERDICT_EMOJI = {"ESCALATE": "⚠️ ESCALATE", "REVIEW": "👁 REVIEW", "DISMISS": "✅ DISMISS"}


class SlackClient:
    """Posts alert notifications to Slack using Incoming Webhooks (Block Kit)."""

    def __init__(self):
        settings = get_settings()
        self.webhook_url = settings.slack_webhook_url
        self.channel = settings.slack_channel

    def send_alert(self, alert: Alert, triage: TriageResult, jira_key: str = None) -> bool:
        """Post alert to Slack. Returns True if sent (or simulated)."""
        if not self.webhook_url:
            print(f"  [Slack] Not configured — simulating message for {alert.alert_id}")
            self._print_simulated(alert, triage)
            return True

        blocks = self._build_blocks(alert, triage, jira_key)
        mention = "<!here> " if triage.verdict == "ESCALATE" else ""

        payload = {
            "channel": self.channel,
            "text": (f"{mention}{SEVERITY_EMOJI.get(alert.severity, '⚪')} Trade Alert: "
                     f"{alert.pattern_type} — {alert.instrument}"),
            "blocks": blocks,
        }

        try:
            with httpx.Client(timeout=10.0) as client:
                resp = client.post(self.webhook_url, json=payload)
                resp.raise_for_status()
                print(f"  [Slack] Message sent for {alert.alert_id}")
                return True
        except Exception as e:
            print(f"  [Slack] Error sending message: {e}")
            self._print_simulated(alert, triage)
            return True  # still count as sent for demo purposes

    def _build_blocks(self, alert: Alert, triage: TriageResult, jira_key: str) -> list:
        severity_emoji = SEVERITY_EMOJI.get(alert.severity, "⚪")
        verdict_text = VERDICT_EMOJI.get(triage.verdict, triage.verdict)
        settings = get_settings()

        blocks = [
            {
                "type": "header",
                "text": {
                    "type": "plain_text",
                    "text": f"{severity_emoji} {alert.pattern_type.replace('_', ' ')} — {alert.instrument}",
                }
            },
            {
                "type": "section",
                "fields": [
                    {"type": "mrkdwn", "text": f"*Alert ID:*\n`{alert.alert_id}`"},
                    {"type": "mrkdwn", "text": f"*Verdict:*\n{verdict_text}"},
                    {"type": "mrkdwn", "text": f"*Trader:*\n{alert.trader_id}"},
                    {"type": "mrkdwn", "text": f"*Confidence:*\n{triage.confidence * 100:.0f}%"},
                    {"type": "mrkdwn", "text": f"*Anomaly:*\n+{alert.z_score:.1f}σ vs baseline"},
                    {"type": "mrkdwn", "text": f"*FP Probability:*\n{triage.false_positive_probability * 100:.0f}%"},
                ],
            },
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": f"*Claude Rationale:*\n_{triage.rationale}_",
                },
            },
        ]

        if triage.key_factors:
            factors_text = "\n".join(f"• {f}" for f in triage.key_factors[:3])
            blocks.append({
                "type": "section",
                "text": {"type": "mrkdwn", "text": f"*Key Factors:*\n{factors_text}"},
            })

        if jira_key and settings.jira_base_url:
            jira_url = f"{settings.jira_base_url.rstrip('/')}/browse/{jira_key}"
            blocks.append({
                "type": "actions",
                "elements": [
                    {
                        "type": "button",
                        "text": {"type": "plain_text", "text": f"View {jira_key}"},
                        "url": jira_url,
                        "style": "primary",
                    }
                ],
            })

        blocks.append({"type": "divider"})
        return blocks

    def _print_simulated(self, alert: Alert, triage: TriageResult):
        print(f"  [Slack SIMULATED] #{self.channel}")
        print(f"    {SEVERITY_EMOJI.get(alert.severity, '⚪')} {alert.pattern_type} | {alert.instrument}")
        print(f"    Verdict: {triage.verdict} ({triage.confidence * 100:.0f}% confidence)")
        rationale_preview = triage.rationale[:120] if triage.rationale else "(no rationale)"
        print(f"    Rationale: {rationale_preview}...")
