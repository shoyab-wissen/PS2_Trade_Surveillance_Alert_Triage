import pandas as pd
import numpy as np
from datetime import datetime, timezone

from src.ingestion.schemas import Alert
from src.detection.statistics import BaselineStats


class MarkingCloseDetector:
    """
    Detects marking the close: dominating close-window volume to push settlement price.

    Fire when ALL:
    - session == CLOSE
    - price_drift_pct >= 0.3% across close window
    - trader_close_fraction >= 0.40 (trader's share of total close-window volume)
    - z_score of close volume vs trader's baseline close fraction >= 3.0
    """

    PRICE_DRIFT_THRESHOLD = 0.003   # 0.3%
    TRADER_FRACTION_THRESHOLD = 0.40
    MIN_Z_SCORE = 3.0

    def __init__(self, events_df: pd.DataFrame, stats: BaselineStats):
        self.df = events_df.copy() if events_df is not None else pd.DataFrame()
        self.stats = stats
        self._counter = 0

    def _next_alert_id(self) -> str:
        self._counter += 1
        return f"TRD-{datetime.now().strftime('%Y%m%d')}-MKC-{self._counter:04d}"

    def detect(self) -> list[Alert]:
        alerts: list[Alert] = []

        if self.df.empty:
            return alerts

        df = self.df.copy()
        if not pd.api.types.is_datetime64_any_dtype(df["timestamp"]):
            df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True, errors="coerce")
        df = df.dropna(subset=["timestamp"]).sort_values("timestamp")

        # Only CLOSE session TRADE_EXECUTE events
        close_df = df[
            (df["event_type"] == "TRADE_EXECUTE") & (df["session"] == "CLOSE")
        ].copy()

        if close_df.empty:
            return alerts

        # Add date column for grouping
        close_df["date"] = close_df["timestamp"].dt.date

        seen_events: set[tuple] = set()

        for (instrument, date), day_close in close_df.groupby(["instrument", "date"]):
            if day_close.empty:
                continue

            # Market-wide close volume for this instrument-day
            total_close_volume = day_close["quantity"].sum()
            if total_close_volume == 0:
                continue

            # Price drift across the close window (market-wide)
            sorted_day = day_close.sort_values("timestamp")
            first_price = float(sorted_day.iloc[0]["price"])
            last_price = float(sorted_day.iloc[-1]["price"])

            if first_price == 0:
                continue

            price_drift_pct = abs(last_price - first_price) / first_price
            if price_drift_pct < self.PRICE_DRIFT_THRESHOLD:
                continue

            # Per-trader analysis within this close window
            for trader_id, trader_close in day_close.groupby("trader_id"):
                bucket = (trader_id, instrument, str(date))
                if bucket in seen_events:
                    continue

                trader_close_volume = trader_close["quantity"].sum()
                trader_close_fraction = trader_close_volume / total_close_volume

                if trader_close_fraction < self.TRADER_FRACTION_THRESHOLD:
                    continue

                # Z-score: compare trader's close fraction to their baseline close fraction
                z = self.stats.z_score(
                    trader_id, "close_volume_fraction_mean", trader_close_fraction
                )
                if z < self.MIN_Z_SCORE:
                    continue

                seen_events.add(bucket)

                # Determine dominant side
                buy_vol = trader_close[trader_close["side"] == "BUY"]["quantity"].sum()
                sell_vol = trader_close[trader_close["side"] == "SELL"]["quantity"].sum()
                dominant_side = "BUY" if buy_vol >= sell_vol else "SELL"

                # Severity based on fraction and z
                if trader_close_fraction > 0.60 and z > 4.5:
                    severity = "HIGH"
                else:
                    severity = "MEDIUM"

                price_direction = "UP" if last_price > first_price else "DOWN"

                baseline_stat, _ = self.stats.get(trader_id)

                alert = Alert(
                    alert_id=self._next_alert_id(),
                    detected_at=datetime.now(tz=timezone.utc),
                    pattern_type="MARKING_CLOSE",
                    severity=severity,
                    trader_id=trader_id,
                    instrument=instrument,
                    evidence={
                        "date": str(date),
                        "first_close_price": round(first_price, 6),
                        "last_close_price": round(last_price, 6),
                        "price_drift_pct": round(price_drift_pct, 6),
                        "price_direction": price_direction,
                        "trader_close_volume": round(float(trader_close_volume), 4),
                        "total_close_volume": round(float(total_close_volume), 4),
                        "trader_close_fraction": round(float(trader_close_fraction), 4),
                        "dominant_side": dominant_side,
                        "n_trader_close_trades": len(trader_close),
                    },
                    event_ids=list(trader_close["event_id"]),
                    z_score=round(z, 4),
                    baseline_metric=round(baseline_stat.close_volume_fraction_mean, 4),
                    observed_metric=round(float(trader_close_fraction), 4),
                    baseline_description=(
                        f"Trader {trader_id} baseline close volume fraction "
                        f"{baseline_stat.close_volume_fraction_mean:.2%} "
                        f"± {baseline_stat.close_volume_fraction_std:.2%}"
                    ),
                )
                alerts.append(alert)

        return alerts
