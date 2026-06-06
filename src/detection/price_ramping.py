import pandas as pd
import numpy as np
from datetime import datetime, timedelta, timezone

from src.ingestion.schemas import Alert
from src.detection.statistics import BaselineStats


class PriceRampingDetector:
    """
    Detects price ramping: sequential executions at escalating prices in rapid succession.

    Fire when ALL:
    - >= 4 TRADE_EXECUTE on same side in <= 10 minutes
    - Monotonicity ratio >= 0.80 (>=80% consecutive pairs go higher)
    - price_delta_pct >= 0.3% from first to last
    - z_score of window volume vs baseline >= 2.0

    Severity: price_delta_pct > 0.6% AND z > 3.0 → HIGH, else MEDIUM
    """

    WINDOW_MINUTES = 10
    MIN_EXECUTIONS = 4
    MIN_MONOTONICITY = 0.80
    MIN_PRICE_DELTA_PCT = 0.003   # 0.3%
    MIN_Z_SCORE = 2.0

    def __init__(self, events_df: pd.DataFrame, stats: BaselineStats):
        self.df = events_df.copy() if events_df is not None else pd.DataFrame()
        self.stats = stats
        self._counter = 0

    def _next_alert_id(self) -> str:
        self._counter += 1
        return f"TRD-{datetime.now().strftime('%Y%m%d')}-PRC-{self._counter:04d}"

    def _compute_monotonicity(self, prices: list[float]) -> float:
        """Fraction of consecutive pairs where price[i] > price[i-1]."""
        if len(prices) < 2:
            return 0.0
        n_increasing = sum(1 for a, b in zip(prices, prices[1:]) if b > a)
        return n_increasing / (len(prices) - 1)

    def detect(self) -> list[Alert]:
        alerts: list[Alert] = []

        if self.df.empty:
            return alerts

        df = self.df.copy()
        if not pd.api.types.is_datetime64_any_dtype(df["timestamp"]):
            df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True, errors="coerce")
        df = df.dropna(subset=["timestamp"]).sort_values("timestamp")

        exec_df = df[df["event_type"] == "TRADE_EXECUTE"].copy()
        if exec_df.empty:
            return alerts

        window_td = timedelta(minutes=self.WINDOW_MINUTES)
        seen_windows: set[tuple] = set()

        for (trader_id, instrument), group in exec_df.groupby(
            ["trader_id", "instrument"]
        ):
            group = group.sort_values("timestamp")
            if len(group) < self.MIN_EXECUTIONS:
                continue

            for side in ["BUY", "SELL"]:
                side_group = group[group["side"] == side].sort_values("timestamp")
                if len(side_group) < self.MIN_EXECUTIONS:
                    continue

                timestamps = side_group["timestamp"].tolist()

                for i, t_start in enumerate(timestamps):
                    t_end = t_start + window_td

                    bucket = (
                        trader_id,
                        instrument,
                        side,
                        t_start.floor("10min") if hasattr(t_start, "floor") else t_start,
                    )
                    if bucket in seen_windows:
                        continue

                    window_mask = (side_group["timestamp"] >= t_start) & (
                        side_group["timestamp"] < t_end
                    )
                    window = side_group[window_mask]

                    if len(window) < self.MIN_EXECUTIONS:
                        continue

                    prices = window["price"].tolist()
                    first_price = prices[0]
                    last_price = prices[-1]

                    if first_price == 0:
                        continue

                    price_delta_pct = (last_price - first_price) / first_price

                    # For BUY ramp: prices should go up; for SELL ramp: prices go down
                    # Monotonicity is direction-agnostic for detection; we check delta sign separately
                    monotonicity = self._compute_monotonicity(prices)

                    # For SELL side, ramping means falling prices — invert
                    if side == "SELL":
                        inverted_prices = [-p for p in prices]
                        monotonicity = self._compute_monotonicity(inverted_prices)
                        price_delta_pct = -price_delta_pct  # want this positive for a down-ramp

                    if monotonicity < self.MIN_MONOTONICITY:
                        continue

                    if price_delta_pct < self.MIN_PRICE_DELTA_PCT:
                        continue

                    window_volume = window["quantity"].sum()
                    z = self.stats.z_score(trader_id, "daily_volume_mean", window_volume)
                    if z < self.MIN_Z_SCORE:
                        continue

                    seen_windows.add(bucket)

                    # Raw (signed) delta for evidence
                    raw_delta_pct = (last_price - first_price) / first_price

                    severity = "HIGH" if price_delta_pct > 0.006 and z > 3.0 else "MEDIUM"

                    baseline_stat, _ = self.stats.get(trader_id)

                    alert = Alert(
                        alert_id=self._next_alert_id(),
                        detected_at=datetime.now(tz=timezone.utc),
                        pattern_type="PRICE_RAMPING",
                        severity=severity,
                        trader_id=trader_id,
                        instrument=instrument,
                        evidence={
                            "side": side,
                            "n_executions": len(window),
                            "first_price": round(first_price, 6),
                            "last_price": round(last_price, 6),
                            "price_delta_pct": round(raw_delta_pct, 6),
                            "monotonicity_ratio": round(monotonicity, 4),
                            "window_volume": round(float(window_volume), 4),
                            "window_start": str(t_start),
                            "window_end": str(t_end),
                        },
                        event_ids=list(window["event_id"]),
                        z_score=round(z, 4),
                        baseline_metric=round(baseline_stat.daily_volume_mean, 4),
                        observed_metric=round(float(window_volume), 4),
                        baseline_description=(
                            f"Trader {trader_id} baseline daily volume "
                            f"{baseline_stat.daily_volume_mean:.2f} "
                            f"± {baseline_stat.daily_volume_std:.2f}"
                        ),
                    )
                    alerts.append(alert)
                    break  # one alert per side per window

        return alerts
