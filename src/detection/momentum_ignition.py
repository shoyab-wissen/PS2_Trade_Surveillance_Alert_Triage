import pandas as pd
import numpy as np
from datetime import datetime, timedelta, timezone

from src.ingestion.schemas import Alert
from src.detection.statistics import BaselineStats


class MomentumIgnitionDetector:
    """
    Detects momentum ignition: burst of aggressive same-side orders to move price,
    followed by profitable reversal.

    Fire when ALL:
    - >= 5 aggressive (is_aggressive=True) same-side orders in <= 3 minutes
    - Price moves >= 0.5% in burst direction within 5 min after burst
    - Trader executes opposite-side orders within 10 min of burst end
    - z_score of burst volume vs trader baseline >= 2.0

    Severity: estimated_pnl > 10000 AND z > 3.5 → HIGH, else MEDIUM
    """

    BURST_WINDOW_SECONDS = 180     # 3 min
    PRICE_CHECK_SECONDS = 300      # 5 min after burst
    MIN_BURST_ORDERS = 5
    PRICE_MOVE_THRESHOLD = 0.005   # 0.5%
    REVERSAL_WINDOW_SECONDS = 600  # 10 min
    MIN_Z_SCORE = 2.0

    def __init__(self, events_df: pd.DataFrame, stats: BaselineStats):
        self.df = events_df.copy() if events_df is not None else pd.DataFrame()
        self.stats = stats
        self._counter = 0

    def _next_alert_id(self) -> str:
        self._counter += 1
        return f"TRD-{datetime.now().strftime('%Y%m%d')}-MOM-{self._counter:04d}"

    def detect(self) -> list[Alert]:
        alerts: list[Alert] = []

        if self.df.empty:
            return alerts

        df = self.df.copy()
        if not pd.api.types.is_datetime64_any_dtype(df["timestamp"]):
            df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True, errors="coerce")
        df = df.dropna(subset=["timestamp"]).sort_values("timestamp")

        # Market-wide executions for price estimation (all traders)
        market_execs = df[df["event_type"] == "TRADE_EXECUTE"].copy()

        burst_td = timedelta(seconds=self.BURST_WINDOW_SECONDS)
        price_check_td = timedelta(seconds=self.PRICE_CHECK_SECONDS)
        reversal_td = timedelta(seconds=self.REVERSAL_WINDOW_SECONDS)

        seen_bursts: set[tuple] = set()

        for (trader_id, instrument), group in df.groupby(["trader_id", "instrument"]):
            # Only aggressive order events
            aggressive = group[
                (group["event_type"].isin(["ORDER_PLACE", "TRADE_EXECUTE"]))
                & (group["is_aggressive"] == True)
            ].sort_values("timestamp")

            if len(aggressive) < self.MIN_BURST_ORDERS:
                continue

            timestamps = aggressive["timestamp"].tolist()

            for i, t_burst_start in enumerate(timestamps):
                t_burst_end = t_burst_start + burst_td

                bucket = (
                    trader_id,
                    instrument,
                    t_burst_start.floor("3min") if hasattr(t_burst_start, "floor") else t_burst_start,
                )
                if bucket in seen_bursts:
                    continue

                burst_mask = (aggressive["timestamp"] >= t_burst_start) & (
                    aggressive["timestamp"] < t_burst_end
                )
                burst_orders = aggressive[burst_mask]

                if len(burst_orders) < self.MIN_BURST_ORDERS:
                    continue

                # All burst orders must be on same side
                sides = burst_orders["side"].unique()
                if len(sides) != 1:
                    continue
                burst_side = sides[0]
                opposite_side = "SELL" if burst_side == "BUY" else "BUY"

                burst_volume = burst_orders["quantity"].sum()

                # ── Price movement check ──────────────────────────────────────
                # Compare burst execution price to reversal execution price
                # (market-wide GBM doesn't move enough in minutes for the threshold)
                burst_execs = burst_orders[burst_orders["event_type"] == "TRADE_EXECUTE"]
                if burst_execs.empty:
                    continue
                price_before = burst_execs["price"].mean()

                # Reversal check done first to get price_after
                t_reversal_end = t_burst_end + reversal_td
                trader_reversal = df[
                    (df["trader_id"] == trader_id)
                    & (df["instrument"] == instrument)
                    & (df["event_type"] == "TRADE_EXECUTE")
                    & (df["side"] == opposite_side)
                    & (df["timestamp"] >= t_burst_end)
                    & (df["timestamp"] <= t_reversal_end)
                ]
                if trader_reversal.empty:
                    continue
                price_after = trader_reversal["price"].mean()

                if price_before == 0:
                    continue

                price_delta_pct = (price_after - price_before) / price_before

                # Check direction matches burst side
                expected_direction = 1 if burst_side == "BUY" else -1
                if (price_delta_pct * expected_direction) < self.PRICE_MOVE_THRESHOLD:
                    continue

                # reversal_qty already captured above alongside price_after
                reversal_qty = trader_reversal["quantity"].sum()

                # ── Z-score on burst volume ───────────────────────────────────
                # Scale burst volume to daily equivalent so it's comparable to
                # the daily_volume_mean baseline (burst is only 3 minutes)
                trading_minutes = 390.0
                burst_minutes = self.BURST_WINDOW_SECONDS / 60.0
                annualized_burst = burst_volume * (trading_minutes / burst_minutes)
                z = self.stats.z_score(trader_id, "daily_volume_mean", annualized_burst)
                if z < self.MIN_Z_SCORE:
                    continue

                # ── Estimated PnL ─────────────────────────────────────────────
                price_delta_abs = abs(price_after - price_before)
                estimated_pnl = reversal_qty * price_delta_abs

                severity = "HIGH" if estimated_pnl > 10000 and z > 3.5 else "MEDIUM"

                seen_bursts.add(bucket)

                event_ids = list(burst_orders["event_id"]) + list(trader_reversal["event_id"])

                baseline_stat, _ = self.stats.get(trader_id)

                alert = Alert(
                    alert_id=self._next_alert_id(),
                    detected_at=datetime.now(tz=timezone.utc),
                    pattern_type="MOMENTUM_IGNITION",
                    severity=severity,
                    trader_id=trader_id,
                    instrument=instrument,
                    evidence={
                        "burst_side": burst_side,
                        "burst_order_count": len(burst_orders),
                        "burst_volume": round(float(burst_volume), 4),
                        "price_before": round(float(price_before), 6),
                        "price_after": round(float(price_after), 6),
                        "price_delta_pct": round(float(price_delta_pct), 6),
                        "reversal_qty": round(float(reversal_qty), 4),
                        "estimated_pnl": round(float(estimated_pnl), 2),
                        "burst_start": str(t_burst_start),
                        "burst_end": str(t_burst_end),
                        "reversal_window_end": str(t_reversal_end),
                    },
                    event_ids=event_ids,
                    z_score=round(z, 4),
                    baseline_metric=round(baseline_stat.daily_volume_mean, 4),
                    observed_metric=round(float(burst_volume), 4),
                    baseline_description=(
                        f"Trader {trader_id} baseline daily volume "
                        f"{baseline_stat.daily_volume_mean:.2f} "
                        f"± {baseline_stat.daily_volume_std:.2f}"
                    ),
                )
                alerts.append(alert)

        return alerts
