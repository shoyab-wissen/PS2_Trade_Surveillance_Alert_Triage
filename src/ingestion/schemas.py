from pydantic import BaseModel, Field
from typing import Optional
from datetime import datetime


class TradeEvent(BaseModel):
    event_id: str
    event_type: str          # ORDER_PLACE | ORDER_CANCEL | TRADE_EXECUTE
    timestamp: datetime
    trader_id: str
    account_id: str
    instrument: str
    side: str                # BUY | SELL
    order_id: str
    quantity: float
    price: float
    order_type: str          # LIMIT | MARKET
    session: str             # PRE | REGULAR | CLOSE
    related_order_id: Optional[str] = None   # cancel → its place event
    is_aggressive: bool = False              # market orders or aggressive limits


class TraderProfile(BaseModel):
    trader_id: str
    account_type: str                        # market_maker | prop_desk | retail | fund
    market_maker_registered: bool = False
    beneficial_owner_id: Optional[str] = None


class Alert(BaseModel):
    alert_id: str
    detected_at: datetime
    pattern_type: str        # LAYERING | WASH_TRADING | MOMENTUM_IGNITION | PRICE_RAMPING | MARKING_CLOSE
    severity: str            # LOW | MEDIUM | HIGH | CRITICAL
    trader_id: str
    instrument: str
    evidence: dict
    event_ids: list[str]
    z_score: float
    baseline_metric: float
    observed_metric: float
    baseline_description: str


class TriageResult(BaseModel):
    alert_id: str
    verdict: str             # ESCALATE | REVIEW | DISMISS
    confidence: float
    false_positive_probability: float
    rationale: str
    key_factors: list[str]
    recommended_action: str
    jira_ticket_id: Optional[str] = None
    slack_message_sent: bool = False
    tokens_used: int = 0
    cache_hit: bool = False
    call_cost_usd: float = 0.0        # USD cost for this specific Claude call
    call_tokens_input: int = 0        # input tokens for this call
    call_tokens_output: int = 0       # output tokens for this call
    call_tokens_cache_read: int = 0   # cache-read tokens for this call


class FeedbackRecord(BaseModel):
    alert_id: str
    correct: bool                     # True = analyst agrees with Claude verdict
    analyst_note: str = ""
    original_verdict: str
    corrected_verdict: Optional[str] = None   # What verdict it SHOULD have been
    recorded_at: datetime = Field(default_factory=datetime.utcnow)


SEVERITY_RANK = {"LOW": 0, "MEDIUM": 1, "HIGH": 2, "CRITICAL": 3}
