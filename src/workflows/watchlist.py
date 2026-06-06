from datetime import datetime, timedelta
from dataclasses import dataclass, field


@dataclass
class WatchlistEntry:
    trader_id: str
    reason: str
    flagged_at: datetime
    duration_hours: int
    prior_alert_count: int = 0


class WatchlistManager:
    """In-memory trader watchlist. Auto-flags on ESCALATE verdict."""

    def __init__(self):
        self._entries: dict[str, WatchlistEntry] = {}
        self._alert_history: dict[str, list[tuple[str, datetime]]] = {}  # trader_id -> [(alert_id, timestamp)]

    def flag_trader(self, trader_id: str, reason: str, alert_id: str, duration_hours: int = 72):
        """Flag a trader for enhanced monitoring."""
        now = datetime.utcnow()
        prior_count = self.prior_alert_count(trader_id)
        entry = WatchlistEntry(
            trader_id=trader_id,
            reason=reason,
            flagged_at=now,
            duration_hours=duration_hours,
            prior_alert_count=prior_count,
        )
        self._entries[trader_id] = entry
        # Also record this alert if not already recorded
        self.record_alert(trader_id, alert_id)
        print(f"  [Watchlist] Flagged trader {trader_id} for {duration_hours}h — reason: {reason}")

    def is_flagged(self, trader_id: str) -> bool:
        """Check if trader is currently on watchlist (not expired)."""
        entry = self._entries.get(trader_id)
        if entry is None:
            return False
        expiry = entry.flagged_at + timedelta(hours=entry.duration_hours)
        if datetime.utcnow() > expiry:
            # Entry has expired — remove it silently
            del self._entries[trader_id]
            return False
        return True

    def record_alert(self, trader_id: str, alert_id: str):
        """Record that an alert was generated for this trader."""
        now = datetime.utcnow()
        if trader_id not in self._alert_history:
            self._alert_history[trader_id] = []
        # Avoid duplicates
        existing_ids = {aid for aid, _ in self._alert_history[trader_id]}
        if alert_id not in existing_ids:
            self._alert_history[trader_id].append((alert_id, now))

    def prior_alert_count(self, trader_id: str) -> int:
        """Return number of prior alerts for this trader in last 90 days."""
        history = self._alert_history.get(trader_id, [])
        cutoff = datetime.utcnow() - timedelta(days=90)
        return sum(1 for _, ts in history if ts >= cutoff)

    def get_flagged_set(self) -> set[str]:
        """Return set of currently flagged trader_ids (pruning expired entries)."""
        # Force expiry check by calling is_flagged for all entries
        # Collect keys first to avoid mutation during iteration
        all_trader_ids = list(self._entries.keys())
        return {tid for tid in all_trader_ids if self.is_flagged(tid)}

    def get_status(self) -> list[dict]:
        """Return list of current watchlist entries for API."""
        now = datetime.utcnow()
        result = []
        for trader_id in list(self._entries.keys()):
            if not self.is_flagged(trader_id):
                continue
            entry = self._entries[trader_id]
            expiry = entry.flagged_at + timedelta(hours=entry.duration_hours)
            hours_remaining = max(0.0, (expiry - now).total_seconds() / 3600)
            result.append({
                "trader_id": entry.trader_id,
                "reason": entry.reason,
                "flagged_at": entry.flagged_at.isoformat(),
                "expires_at": expiry.isoformat(),
                "hours_remaining": round(hours_remaining, 1),
                "prior_alert_count": entry.prior_alert_count,
            })
        return result
