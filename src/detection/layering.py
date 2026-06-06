import uuid
import pandas as pd
import numpy as np
from datetime import datetime, timedelta, timezone

from src.ingestion.schemas import Alert
from src.detection.statistics import BaselineStats


class LayeringDetector:
    """
    Detects layering/spoofing: large orders on one side, rapid mass cancellation,
    then opposite-side execution at manipulated price.

    Fire when ALL:
    - cancel_ratio >= 0.70 (cancelled/placed on one side in window)
    - median_ttc_ms <= 2000 (median time-to-cancel)
    - opposite-side TRADE_EXECUTE exists in same window
    - z_score vs trader baseline >= 2.5

    Severity: cancel_ratio > 0.85 AND z > 4.0 → CRITICAL, else HIGH
    Window: 5-minute rolling
    """

    WINDOW_MINUTES = 5
    MIN_CANCEL_RATIO = 0.70
    MAX_MEDIAN_TTC_MS = 2000
    MIN_Z_SCORE = 2.5
    MIN_ORDERS = 5  # minimum orders to consider a window

    def __init__(self, events_df: pd.DataFrame, stats: BaselineStats):
        self.df = events_df.copy() if events_df is not None else pd.DataFrame()
        self.stats = stats
        self._counter = 0

    def _next_alert_id(self) -> str:
        self._counter += 1
        return f"TRD-{datetime.now().strftime('%Y%m%d')}-LAY-{self._counter:04d}"

    def detect(self) -> list[Alert]:
        alerts: list[Alert] = []

        if self.df.empty:
            return alerts

        df = self.df.copy()
        if not pd.api.types.is_datetime64_any_dtype(df["timestamp"]):
            df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True, errors="coerce")
        df = df.dropna(subset=["timestamp"])
        df = df.sort_values("timestamp")

        # Build a lookup: event_id -> timestamp for ORDER_PLACE events
        # (ORDER_CANCEL.related_order_id stores the place's event_id, not order_id)
        place_ts_map: dict[str, pd.Timestamp] = dict(
            zip(
                df[df["event_type"] == "ORDER_PLACE"]["event_id"],
                df[df["event_type"] == "ORDER_PLACE"]["timestamp"],
            )
        )

        place_cancel_df = df[df["event_type"].isin(["ORDER_PLACE", "ORDER_CANCEL"])]
        execute_df = df[df["event_type"] == "TRADE_EXECUTE"]

        seen_windows: set[tuple] = set()  # (trader_id, instrument, window_start_minute)

        window_td = timedelta(minutes=self.WINDOW_MINUTES)

        for (trader_id, instrument), group in place_cancel_df.groupby(
            ["trader_id", "instrument"]
        ):
            group = group.sort_values("timestamp")
            timestamps = group["timestamp"].tolist()
            if not timestamps:
                continue

            for i, t_start in enumerate(timestamps):
                t_end = t_start + window_td

                # Deduplicate: bucket by 5-min window
                bucket = (
                    trader_id,
                    instrument,
                    t_start.floor("5min") if hasattr(t_start, "floor") else t_start,
                )
                if bucket in seen_windows:
                    continue

                window_mask = (group["timestamp"] >= t_start) & (
                    group["timestamp"] < t_end
                )
                window = group[window_mask]

                if len(window) < self.MIN_ORDERS:
                    continue

                for side in ["BUY", "SELL"]:
                    opposite = "SELL" if side == "BUY" else "BUY"

                    side_window = window[window["side"] == side]
                    placed = side_window[side_window["event_type"] == "ORDER_PLACE"]
                    cancelled = side_window[side_window["event_type"] == "ORDER_CANCEL"]

                    n_placed = len(placed)
                    n_cancelled = len(cancelled)
                    total = n_placed + n_cancelled
                    if total < self.MIN_ORDERS or n_placed == 0:
                        continue

                    cancel_ratio = n_cancelled / n_placed if n_placed > 0 else 0.0
                    if cancel_ratio < self.MIN_CANCEL_RATIO:
                        continue

                    # Compute median TTC in ms
                    ttc_values = []
                    for _, cancel_row in cancelled.iterrows():
                        rid = cancel_row.get("related_order_id")
                        if rid and rid in place_ts_map:
                            delta_ms = (
                                cancel_row["timestamp"] - place_ts_map[rid]
                            ).total_seconds() * 1000
                            if delta_ms >= 0:
                                ttc_values.append(delta_ms)

                    if not ttc_values:
                        # Can't confirm rapid cancellation without TTC data; skip
                        continue

                    median_ttc = float(np.median(ttc_values))
                    if median_ttc > self.MAX_MEDIAN_TTC_MS:
                        continue

                    # Check opposite-side TRADE_EXECUTE in same window
                    opp_exec = execute_df[
                        (execute_df["trader_id"] == trader_id)
                        & (execute_df["instrument"] == instrument)
                        & (execute_df["side"] == opposite)
                        & (execute_df["timestamp"] >= t_start)
                        & (execute_df["timestamp"] < t_end)
                    ]
                    if opp_exec.empty:
                        continue

                    # Z-score on cancel ratio
                    z = self.stats.z_score(trader_id, "cancel_ratio_mean", cancel_ratio)
                    if z < self.MIN_Z_SCORE:
                        continue

                    # Determine severity
                    if cancel_ratio > 0.85 and z > 4.0:
                        severity = "CRITICAL"
                    else:
                        severity = "HIGH"

                    seen_windows.add(bucket)

                    # Gather event IDs
                    event_ids = (
                        list(placed["event_id"])
                        + list(cancelled["event_id"])
                        + list(opp_exec["event_id"])
                    )

                    baseline_stat, _ = self.stats.get(trader_id)

                    alert = Alert(
                        alert_id=self._next_alert_id(),
                        detected_at=datetime.now(tz=timezone.utc),
                        pattern_type="LAYERING",
                        severity=severity,
                        trader_id=trader_id,
                        instrument=instrument,
                        evidence={
                            "cancel_ratio": round(cancel_ratio, 4),
                            "median_ttc_ms": round(median_ttc, 2),
                            "n_placed": n_placed,
                            "n_cancelled": n_cancelled,
                            "spoofed_side": side,
                            "execution_side": opposite,
                            "window_start": str(t_start),
                            "window_end": str(t_end),
                            "opposite_exec_count": len(opp_exec),
                        },
                        event_ids=event_ids,
                        z_score=round(z, 4),
                        baseline_metric=round(baseline_stat.cancel_ratio_mean, 4),
                        observed_metric=round(cancel_ratio, 4),
                        baseline_description=(
                            f"Trader {trader_id} baseline cancel ratio "
                            f"{baseline_stat.cancel_ratio_mean:.2%} "
                            f"± {baseline_stat.cancel_ratio_std:.2%}"
                        ),
                    )
                    alerts.append(alert)
                    break  # one alert per window

        return alerts
