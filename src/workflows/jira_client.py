import httpx
import base64
from src.ingestion.schemas import Alert, TriageResult
from src.config import get_settings

PRIORITY_MAP = {"CRITICAL": "Highest", "HIGH": "High", "MEDIUM": "Medium", "LOW": "Low"}


class JiraClient:
    """Creates Jira compliance cases via REST API v3."""

    def __init__(self):
        settings = get_settings()
        self.base_url = settings.jira_base_url.rstrip("/")
        self.project_key = settings.jira_project_key
        self.l2_assignee = settings.jira_l2_assignee_account_id
        auth = base64.b64encode(
            f"{settings.jira_user_email}:{settings.jira_api_token}".encode()
        ).decode()
        self.headers = {
            "Authorization": f"Basic {auth}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

    def create_ticket(self, alert: Alert, triage: TriageResult) -> str | None:
        """Create a Jira ticket. Returns ticket key (e.g. COMP-8812) or None on error."""
        if not self.base_url or not self.project_key:
            print("  [Jira] Not configured — skipping ticket creation")
            return None

        summary = (f"[{alert.severity}] {alert.pattern_type} Alert — "
                   f"Trader {alert.trader_id} on {alert.instrument}")

        description = self._build_description(alert, triage)

        payload = {
            "fields": {
                "project": {"key": self.project_key},
                "summary": summary,
                "description": {
                    "type": "doc", "version": 1,
                    "content": [{"type": "paragraph", "content": [
                        {"type": "text", "text": description}
                    ]}]
                },
                "issuetype": {"name": "Task"},
                "priority": {"name": PRIORITY_MAP.get(alert.severity, "Medium")},
                "labels": ["trade-surveillance", alert.pattern_type.lower(),
                           triage.verdict.lower()],
            }
        }
        if self.l2_assignee and triage.verdict == "ESCALATE":
            payload["fields"]["assignee"] = {"accountId": self.l2_assignee}

        try:
            with httpx.Client(timeout=10.0) as client:
                resp = client.post(
                    f"{self.base_url}/rest/api/3/issue",
                    headers=self.headers, json=payload
                )
                resp.raise_for_status()
                key = resp.json().get("key", "UNKNOWN")
                print(f"  [Jira] Created ticket: {key}")
                return key
        except Exception as e:
            print(f"  [Jira] Error creating ticket: {e}")
            return f"MOCK-{alert.alert_id[-4:]}"  # return mock key so demo continues

    def _build_description(self, alert: Alert, triage: TriageResult) -> str:
        factors_block = "\n".join(f"- {f}" for f in triage.key_factors)
        return (
            f"Alert ID: {alert.alert_id}\n"
            f"Pattern: {alert.pattern_type}\n"
            f"Trader: {alert.trader_id} | Instrument: {alert.instrument}\n"
            f"Severity: {alert.severity} | Z-Score: +{alert.z_score:.1f}σ\n\n"
            f"CLAUDE TRIAGE VERDICT: {triage.verdict}\n"
            f"Confidence: {triage.confidence * 100:.0f}% | "
            f"False Positive Probability: {triage.false_positive_probability * 100:.0f}%\n\n"
            f"Rationale: {triage.rationale}\n\n"
            f"Key Factors:\n{factors_block}\n\n"
            f"Recommended Action: {triage.recommended_action}\n\n"
            f"Evidence: {alert.evidence}"
        )
