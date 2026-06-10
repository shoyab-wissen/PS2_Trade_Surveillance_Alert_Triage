"""
database.py — SQLite persistence layer for the trade surveillance engine.

Tables: alerts, triage_results, feedback, simulation_runs, watchlist
All operations are async-safe via asyncio.Lock.
"""

import sqlite3
import json
import asyncio
from pathlib import Path
from datetime import datetime, timedelta
from typing import Optional
from contextlib import contextmanager

from src.ingestion.schemas import Alert, TriageResult, FeedbackRecord

DB_PATH = Path(__file__).parent.parent / "data" / "surveillance.db"


class Database:
    """SQLite persistence with thread-safe access."""

    def __init__(self, db_path: Path = DB_PATH):
        self._db_path = db_path
        self._lock = asyncio.Lock()
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    @contextmanager
    def _conn(self):
        conn = sqlite3.connect(str(self._db_path), timeout=10)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def _init_schema(self):
        with self._conn() as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS alerts (
                    alert_id TEXT PRIMARY KEY,
                    detected_at TEXT NOT NULL,
                    pattern_type TEXT NOT NULL,
                    severity TEXT NOT NULL,
                    trader_id TEXT NOT NULL,
                    instrument TEXT NOT NULL,
                    jurisdiction TEXT DEFAULT 'UNKNOWN',
                    evidence TEXT NOT NULL,
                    event_ids TEXT NOT NULL,
                    z_score REAL NOT NULL,
                    baseline_metric REAL NOT NULL,
                    observed_metric REAL NOT NULL,
                    baseline_description TEXT NOT NULL,
                    estimated_impact REAL DEFAULT 0.0,
                    market_volatility_factor REAL DEFAULT 1.0,
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP
                );

                CREATE TABLE IF NOT EXISTS triage_results (
                    alert_id TEXT PRIMARY KEY REFERENCES alerts(alert_id),
                    verdict TEXT NOT NULL,
                    confidence REAL NOT NULL,
                    false_positive_probability REAL NOT NULL,
                    rationale TEXT NOT NULL,
                    key_factors TEXT NOT NULL,
                    recommended_action TEXT NOT NULL,
                    jira_ticket_id TEXT,
                    slack_message_sent INTEGER DEFAULT 0,
                    tokens_used INTEGER DEFAULT 0,
                    cache_hit INTEGER DEFAULT 0,
                    call_cost_usd REAL DEFAULT 0.0,
                    call_tokens_input INTEGER DEFAULT 0,
                    call_tokens_output INTEGER DEFAULT 0,
                    call_tokens_cache_read INTEGER DEFAULT 0,
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP
                );

                CREATE TABLE IF NOT EXISTS feedback (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    alert_id TEXT NOT NULL REFERENCES alerts(alert_id),
                    correct INTEGER NOT NULL,
                    analyst_note TEXT DEFAULT '',
                    original_verdict TEXT NOT NULL,
                    corrected_verdict TEXT,
                    recorded_at TEXT NOT NULL,
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP
                );

                CREATE TABLE IF NOT EXISTS simulation_runs (
                    run_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp TEXT NOT NULL,
                    data TEXT NOT NULL,
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP
                );

                CREATE TABLE IF NOT EXISTS watchlist (
                    trader_id TEXT PRIMARY KEY,
                    reason TEXT NOT NULL,
                    flagged_at TEXT NOT NULL,
                    duration_hours INTEGER NOT NULL,
                    prior_alert_count INTEGER DEFAULT 0,
                    alert_id TEXT
                );

                CREATE TABLE IF NOT EXISTS watchlist_history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    trader_id TEXT NOT NULL,
                    alert_id TEXT NOT NULL,
                    recorded_at TEXT NOT NULL,
                    UNIQUE(trader_id, alert_id)
                );

                CREATE INDEX IF NOT EXISTS idx_alerts_trader ON alerts(trader_id);
                CREATE INDEX IF NOT EXISTS idx_alerts_pattern ON alerts(pattern_type);
                CREATE INDEX IF NOT EXISTS idx_alerts_severity ON alerts(severity);
                CREATE INDEX IF NOT EXISTS idx_feedback_alert ON feedback(alert_id);
                CREATE INDEX IF NOT EXISTS idx_watchlist_history_trader ON watchlist_history(trader_id);
            """)

    # ── Alerts ──────────────────────────────────────────────────────────────

    def save_alert(self, alert: Alert):
        with self._conn() as conn:
            conn.execute("""
                INSERT OR REPLACE INTO alerts
                (alert_id, detected_at, pattern_type, severity, trader_id, instrument,
                 jurisdiction, evidence, event_ids, z_score, baseline_metric,
                 observed_metric, baseline_description, estimated_impact, market_volatility_factor)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                alert.alert_id,
                alert.detected_at.isoformat(),
                alert.pattern_type,
                alert.severity,
                alert.trader_id,
                alert.instrument,
                getattr(alert, 'jurisdiction', 'UNKNOWN'),
                json.dumps(alert.evidence),
                json.dumps(alert.event_ids),
                alert.z_score,
                alert.baseline_metric,
                alert.observed_metric,
                alert.baseline_description,
                alert.evidence.get('estimated_impact', 0.0),
                alert.evidence.get('market_volatility_factor', 1.0),
            ))

    def save_alerts(self, alerts: list[Alert]):
        with self._conn() as conn:
            for alert in alerts:
                conn.execute("""
                    INSERT OR REPLACE INTO alerts
                    (alert_id, detected_at, pattern_type, severity, trader_id, instrument,
                     jurisdiction, evidence, event_ids, z_score, baseline_metric,
                     observed_metric, baseline_description, estimated_impact, market_volatility_factor)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    alert.alert_id,
                    alert.detected_at.isoformat(),
                    alert.pattern_type,
                    alert.severity,
                    alert.trader_id,
                    alert.instrument,
                    getattr(alert, 'jurisdiction', 'UNKNOWN'),
                    json.dumps(alert.evidence),
                    json.dumps(alert.event_ids),
                    alert.z_score,
                    alert.baseline_metric,
                    alert.observed_metric,
                    alert.baseline_description,
                    alert.evidence.get('estimated_impact', 0.0),
                    alert.evidence.get('market_volatility_factor', 1.0),
                ))

    def load_alerts(self) -> list[Alert]:
        with self._conn() as conn:
            rows = conn.execute("SELECT * FROM alerts ORDER BY created_at DESC").fetchall()
        alerts = []
        for r in rows:
            alerts.append(Alert(
                alert_id=r["alert_id"],
                detected_at=datetime.fromisoformat(r["detected_at"]),
                pattern_type=r["pattern_type"],
                severity=r["severity"],
                trader_id=r["trader_id"],
                instrument=r["instrument"],
                jurisdiction=r["jurisdiction"] or "UNKNOWN",
                evidence=json.loads(r["evidence"]),
                event_ids=json.loads(r["event_ids"]),
                z_score=r["z_score"],
                baseline_metric=r["baseline_metric"],
                observed_metric=r["observed_metric"],
                baseline_description=r["baseline_description"],
            ))
        return alerts

    # ── Triage Results ──────────────────────────────────────────────────────

    def save_triage(self, result: TriageResult):
        with self._conn() as conn:
            conn.execute("""
                INSERT OR REPLACE INTO triage_results
                (alert_id, verdict, confidence, false_positive_probability, rationale,
                 key_factors, recommended_action, jira_ticket_id, slack_message_sent,
                 tokens_used, cache_hit, call_cost_usd, call_tokens_input,
                 call_tokens_output, call_tokens_cache_read)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                result.alert_id,
                result.verdict,
                result.confidence,
                result.false_positive_probability,
                result.rationale,
                json.dumps(result.key_factors),
                result.recommended_action,
                result.jira_ticket_id,
                1 if result.slack_message_sent else 0,
                result.tokens_used,
                1 if result.cache_hit else 0,
                result.call_cost_usd,
                result.call_tokens_input,
                result.call_tokens_output,
                result.call_tokens_cache_read,
            ))

    def load_triage_results(self) -> dict[str, TriageResult]:
        with self._conn() as conn:
            rows = conn.execute("SELECT * FROM triage_results").fetchall()
        results = {}
        for r in rows:
            results[r["alert_id"]] = TriageResult(
                alert_id=r["alert_id"],
                verdict=r["verdict"],
                confidence=r["confidence"],
                false_positive_probability=r["false_positive_probability"],
                rationale=r["rationale"],
                key_factors=json.loads(r["key_factors"]),
                recommended_action=r["recommended_action"],
                jira_ticket_id=r["jira_ticket_id"],
                slack_message_sent=bool(r["slack_message_sent"]),
                tokens_used=r["tokens_used"],
                cache_hit=bool(r["cache_hit"]),
                call_cost_usd=r["call_cost_usd"],
                call_tokens_input=r["call_tokens_input"],
                call_tokens_output=r["call_tokens_output"],
                call_tokens_cache_read=r["call_tokens_cache_read"],
            )
        return results

    # ── Feedback ────────────────────────────────────────────────────────────

    def save_feedback(self, feedback: FeedbackRecord):
        with self._conn() as conn:
            conn.execute("""
                INSERT INTO feedback
                (alert_id, correct, analyst_note, original_verdict, corrected_verdict, recorded_at)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (
                feedback.alert_id,
                1 if feedback.correct else 0,
                feedback.analyst_note,
                feedback.original_verdict,
                feedback.corrected_verdict,
                feedback.recorded_at.isoformat(),
            ))

    def load_feedback(self) -> list[FeedbackRecord]:
        with self._conn() as conn:
            rows = conn.execute("SELECT * FROM feedback ORDER BY recorded_at DESC").fetchall()
        return [
            FeedbackRecord(
                alert_id=r["alert_id"],
                correct=bool(r["correct"]),
                analyst_note=r["analyst_note"],
                original_verdict=r["original_verdict"],
                corrected_verdict=r["corrected_verdict"],
                recorded_at=datetime.fromisoformat(r["recorded_at"]),
            )
            for r in rows
        ]

    # ── Simulation Runs ─────────────────────────────────────────────────────

    def save_simulation_run(self, run_data: dict) -> int:
        with self._conn() as conn:
            cursor = conn.execute("""
                INSERT INTO simulation_runs (timestamp, data)
                VALUES (?, ?)
            """, (
                run_data.get("timestamp", datetime.utcnow().isoformat()),
                json.dumps(run_data, default=str),
            ))
            run_id = cursor.lastrowid
        return run_id

    def load_simulation_runs(self) -> list[dict]:
        with self._conn() as conn:
            rows = conn.execute("SELECT * FROM simulation_runs ORDER BY run_id DESC").fetchall()
        runs = []
        for r in rows:
            data = json.loads(r["data"])
            data["run_id"] = r["run_id"]
            runs.append(data)
        return runs

    # ── Watchlist ───────────────────────────────────────────────────────────

    def save_watchlist_entry(self, trader_id: str, reason: str, alert_id: str,
                             duration_hours: int = 72, prior_alert_count: int = 0):
        now = datetime.utcnow().isoformat()
        with self._conn() as conn:
            conn.execute("""
                INSERT OR REPLACE INTO watchlist
                (trader_id, reason, flagged_at, duration_hours, prior_alert_count, alert_id)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (trader_id, reason, now, duration_hours, prior_alert_count, alert_id))

    def save_watchlist_alert(self, trader_id: str, alert_id: str):
        now = datetime.utcnow().isoformat()
        with self._conn() as conn:
            conn.execute("""
                INSERT OR IGNORE INTO watchlist_history
                (trader_id, alert_id, recorded_at)
                VALUES (?, ?, ?)
            """, (trader_id, alert_id, now))

    def load_watchlist(self) -> list[dict]:
        now = datetime.utcnow()
        with self._conn() as conn:
            rows = conn.execute("SELECT * FROM watchlist").fetchall()
        entries = []
        for r in rows:
            flagged_at = datetime.fromisoformat(r["flagged_at"])
            expiry = flagged_at + timedelta(hours=r["duration_hours"])
            if now <= expiry:
                entries.append({
                    "trader_id": r["trader_id"],
                    "reason": r["reason"],
                    "flagged_at": r["flagged_at"],
                    "duration_hours": r["duration_hours"],
                    "prior_alert_count": r["prior_alert_count"],
                })
        return entries

    def get_watchlist_alert_count(self, trader_id: str) -> int:
        cutoff = (datetime.utcnow() - timedelta(days=90)).isoformat()
        with self._conn() as conn:
            row = conn.execute(
                "SELECT COUNT(*) as cnt FROM watchlist_history WHERE trader_id=? AND recorded_at>=?",
                (trader_id, cutoff)
            ).fetchone()
        return row["cnt"] if row else 0

    # ── Cleanup ─────────────────────────────────────────────────────────────

    def cleanup_expired_watchlist(self):
        now = datetime.utcnow()
        with self._conn() as conn:
            rows = conn.execute("SELECT trader_id, flagged_at, duration_hours FROM watchlist").fetchall()
            for r in rows:
                flagged_at = datetime.fromisoformat(r["flagged_at"])
                expiry = flagged_at + timedelta(hours=r["duration_hours"])
                if now > expiry:
                    conn.execute("DELETE FROM watchlist WHERE trader_id=?", (r["trader_id"],))

    def get_alert_count(self) -> int:
        with self._conn() as conn:
            row = conn.execute("SELECT COUNT(*) as cnt FROM alerts").fetchone()
        return row["cnt"] if row else 0

    def clear_session(self):
        """Clear current session data (for stream reset)."""
        with self._conn() as conn:
            conn.executescript("""
                DELETE FROM triage_results;
                DELETE FROM alerts;
                DELETE FROM feedback;
                DELETE FROM watchlist;
                DELETE FROM watchlist_history;
            """)
