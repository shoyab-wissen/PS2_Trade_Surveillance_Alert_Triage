import uuid
import pandas as pd
import numpy as np
from datetime import datetime, timedelta, timezone

from src.ingestion.schemas import Alert, TraderProfile
from src.detection.statistics import BaselineStats, MarketVolatility


class LayeringDetector:
    """
    Detects layering/spoofing: large orders on one side, rapid mass cancellation,
    then opposite-side execution at manipulated price.

    Improvements over v1:
    - Market maker exemption: registered MMs get elevated thresholds (90% vs 70%)
    - Volatility normalization: thresholds adjust based on market-wide conditions
    - Impact-based severity: factors in estimated profit and market volume share
    - Jurisdiction tagging: inferred from instrument
    """

    WINDOW_MINUTES = 5
    MIN_CANCEL_RATIO = 0.70
    MM_CANCEL_RATIO = 0.90        # Higher threshold for registered market makers
    MAX_MEDIAN_TTC_MS = 2000
    MIN_Z_SCORE = 2.5
    MIN_ORDERS = 5

    def __init__(self, events_df: pd.DataFrame, stats: BaselineStats,
                 profiles: dict[str, TraderProfile] | None = None,
                 market_volatility: MarketVolatility | None = None):
        self.df = events_df.copy() if events_df is not None else pd.DataFrame()
        self.stats = stats
        self.profiles = profiles or {}
        self.volatility = market_volatility or MarketVolatility()
        self._counter = 0

    def _next_alert_id(self) -> str:
        self._counter += 1
        return f"TRD-{datetime.now().strftime('%Y%m%d')}-LAY-{self._counter:04d}"

    def _is_market_maker(self, trader_id: str) -> bool:
        profile = self.profiles.get(trader_id)
        return bool(profile and profile.market_maker_registered)

    def detect(self) -> list[Alert]:
        alerts: list[Alert] = []

        if self.df.empty:
            return alerts

        df = self.df.copy()
        if not pd.api.types.is_datetime64_any_dtype(df["timestamp"]):
            df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True, errors="coerce")
        df = df.dropna(subset=["timestamp"])
        df = df.sort_values("timestamp")

        # Volatility-adjusted thresholds
        vf = self.volatility.volatility_factor
        adjusted_min_cr = min(self.MIN_CANCEL_RATIO * vf, 0.95)  # cap at 95%
        adjusted_min_z = max(self.MIN_Z_SCORE * (1.0 / vf), 1.5)  # lower z when volatile

        # Build a lookup: event_id -> timestamp for ORDER_PLACE events
        place_ts_map: dict[str, pd.Timestamp] = dict(
            zip(
                df[df["event_type"] == "ORDER_PLACE"]["event_id"],
                df[df["event_type"] == "ORDER_PLACE"]["timestamp"],
            )
        )

        place_cancel_df = df[df["event_type"].isin(["ORDER_PLACE", "ORDER_CANCEL"])]
        execute_df = df[df["event_type"] == "TRADE_EXECUTE"]

        seen_windows: set[tuple] = set()
        window_td = timedelta(minutes=self.WINDOW_MINUTES)

        for (trader_id, instrument), group in place_cancel_df.groupby(
            ["trader_id", "instrument"]
        ):
            group = group.sort_values("timestamp")
            timestamps = group["timestamp"].tolist()
            if not timestamps:
                continue

            # Market maker exemption: use higher threshold
            is_mm = self._is_market_maker(trader_id)
            threshold_cr = self.MM_CANCEL_RATIO if is_mm else adjusted_min_cr

            for i, t_start in enumerate(timestamps):
                t_end = t_start + window_td

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
                    if cancel_ratio < threshold_cr:
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

                    # Z-score on cancel ratio (volatility-adjusted threshold)
                    z = self.stats.z_score(trader_id, "cancel_ratio_mean", cancel_ratio)
                    if z < adjusted_min_z:
                        continue

                    # ── Estimated impact (profit from opposite-side execution) ──
                    # Mean execution price vs. mean placed price on the spoofed side
                    placed_prices = placed["price"]
                    opp_prices = opp_exec["price"]
                    opp_volume = float(opp_exec["quantity"].sum())
                    avg_placed_price = float(placed_prices.mean()) if not placed_prices.empty else 0.0
                    avg_opp_price = float(opp_prices.mean()) if not opp_prices.empty else 0.0
                    estimated_impact = abs(avg_opp_price - avg_placed_price) * opp_volume

                    # Market volume share
                    total_market_vol = float(execute_df[
                        (execute_df["instrument"] == instrument)
                        & (execute_df["timestamp"] >= t_start)
                        & (execute_df["timestamp"] < t_end)
                    ]["quantity"].sum())
                    market_vol_share = opp_volume / max(total_market_vol, 1.0)

                    # ── Impact-based severity ──
                    # Consider: z-score, cancel_ratio, estimated impact, market volume share
                    severity = self._compute_severity(
                        cancel_ratio, z, estimated_impact, market_vol_share, is_mm
                    )

                    seen_windows.add(bucket)

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
                            "estimated_impact": round(estimated_impact, 2),
                            "market_vol_share": round(market_vol_share, 4),
                            "market_volatility_factor": self.volatility.volatility_factor,
                            "market_maker_exemption": is_mm,
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

    def _compute_severity(self, cancel_ratio: float, z: float,
                          estimated_impact: float, market_vol_share: float,
                          is_mm: bool) -> str:
        """
        Impact-based severity scoring.
        Factors: z-score, cancel ratio, estimated PnL impact, market volume share.
        Market makers are downgraded one level.
        """
        score = 0

        # Z-score contribution (0-30 points)
        if z > 5.0:
            score += 30
        elif z > 4.0:
            score += 25
        elif z > 3.0:
            score += 15
        else:
            score += 5

        # Cancel ratio contribution (0-25 points)
        if cancel_ratio > 0.90:
            score += 25
        elif cancel_ratio > 0.85:
            score += 20
        elif cancel_ratio > 0.80:
            score += 15
        else:
            score += 10

        # Estimated impact contribution (0-30 points)
        if estimated_impact > 50000:
            score += 30
        elif estimated_impact > 10000:
            score += 20
        elif estimated_impact > 1000:
            score += 10
        else:
            score += 5

        # Market volume share contribution (0-15 points)
        if market_vol_share > 0.30:
            score += 15
        elif market_vol_share > 0.15:
            score += 10
        elif market_vol_share > 0.05:
            score += 5

        # Market maker penalty: reduce score by 20 points
        if is_mm:
            score = max(score - 20, 0)

        # Map score to severity
        if score >= 70:
            return "CRITICAL"
        elif score >= 50:
            return "HIGH"
        elif score >= 30:
            return "MEDIUM"
        else:
            return "LOW"
