import pandas as pd
import numpy as np
from datetime import datetime, timedelta, timezone

from src.ingestion.schemas import Alert
from src.detection.statistics import BaselineStats, MarketVolatility


class MomentumIgnitionDetector:
    """
    Detects momentum ignition: burst of aggressive same-side orders to move price,
    followed by profitable reversal.

    Improvements over v1:
    - Volatility normalization: price move threshold scales with market volatility
    - Impact-based severity: uses estimated PnL and market participation rate
    - Tracks max price swing (not just mean) for more accurate PnL estimation
    """

    BURST_WINDOW_SECONDS = 180     # 3 min
    PRICE_CHECK_SECONDS = 300      # 5 min after burst
    MIN_BURST_ORDERS = 5
    PRICE_MOVE_THRESHOLD = 0.005   # 0.5%
    REVERSAL_WINDOW_SECONDS = 600  # 10 min
    MIN_Z_SCORE = 2.0

    def __init__(self, events_df: pd.DataFrame, stats: BaselineStats,
                 market_volatility: MarketVolatility | None = None):
        self.df = events_df.copy() if events_df is not None else pd.DataFrame()
        self.stats = stats
        self.volatility = market_volatility or MarketVolatility()
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

        market_execs = df[df["event_type"] == "TRADE_EXECUTE"].copy()

        # Volatility-adjusted price threshold
        vf = self.volatility.volatility_factor
        adjusted_price_threshold = self.PRICE_MOVE_THRESHOLD * vf
        adjusted_min_z = max(self.MIN_Z_SCORE * (1.0 / vf), 1.5)

        burst_td = timedelta(seconds=self.BURST_WINDOW_SECONDS)
        reversal_td = timedelta(seconds=self.REVERSAL_WINDOW_SECONDS)

        seen_bursts: set[tuple] = set()

        for (trader_id, instrument), group in df.groupby(["trader_id", "instrument"]):
            aggressive = group[
                (group["event_type"].isin(["ORDER_PLACE", "TRADE_EXECUTE"]))
                & (group["is_aggressive"] == True)
            ].sort_values("timestamp")

            if len(aggressive) < self.MIN_BURST_ORDERS:
                continue

            timestamps = aggressive["timestamp"].tolist()

            # Compute market-wide order count for this instrument for context
            instr_market = market_execs[market_execs["instrument"] == instrument]

            for i, t_burst_start in enumerate(timestamps):
                t_burst_end = t_burst_start + burst_td

                bucket = (
                    trader_id, instrument,
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

                sides = burst_orders["side"].unique()
                if len(sides) != 1:
                    continue
                burst_side = sides[0]
                opposite_side = "SELL" if burst_side == "BUY" else "BUY"

                burst_volume = burst_orders["quantity"].sum()

                burst_execs = burst_orders[burst_orders["event_type"] == "TRADE_EXECUTE"]
                if burst_execs.empty:
                    continue
                price_before = burst_execs["price"].mean()

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
                # Track max price swing for better PnL estimation
                if burst_side == "BUY":
                    price_peak = float(trader_reversal["price"].max())
                else:
                    price_peak = float(trader_reversal["price"].min())

                if price_before == 0:
                    continue

                price_delta_pct = (price_after - price_before) / price_before
                expected_direction = 1 if burst_side == "BUY" else -1
                if (price_delta_pct * expected_direction) < adjusted_price_threshold:
                    continue

                reversal_qty = trader_reversal["quantity"].sum()

                # Market context: how many other traders also traded aggressively
                market_burst = instr_market[
                    (instr_market["timestamp"] >= t_burst_start)
                    & (instr_market["timestamp"] < t_burst_end)
                ]
                market_burst_participants = market_burst["trader_id"].nunique()
                trader_vol_share = float(burst_volume / max(market_burst["quantity"].sum(), 1.0))

                # Z-score on burst volume (compare to burst-scale baseline, not annualized)
                trading_minutes = 390.0
                burst_minutes = self.BURST_WINDOW_SECONDS / 60.0
                annualized_burst = burst_volume * (trading_minutes / burst_minutes)
                z = self.stats.z_score(trader_id, "daily_volume_mean", annualized_burst)
                if z < adjusted_min_z:
                    continue

                # Estimated PnL (use peak price for more accurate estimate)
                price_delta_abs = abs(price_peak - price_before)
                estimated_pnl = reversal_qty * price_delta_abs

                severity = self._compute_severity(
                    estimated_pnl, z, trader_vol_share, market_burst_participants
                )

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
                        "price_peak": round(float(price_peak), 6),
                        "price_delta_pct": round(float(price_delta_pct), 6),
                        "reversal_qty": round(float(reversal_qty), 4),
                        "estimated_pnl": round(float(estimated_pnl), 2),
                        "estimated_impact": round(float(estimated_pnl), 2),
                        "market_burst_participants": market_burst_participants,
                        "trader_vol_share": round(trader_vol_share, 4),
                        "burst_start": str(t_burst_start),
                        "burst_end": str(t_burst_end),
                        "reversal_window_end": str(t_reversal_end),
                        "market_volatility_factor": self.volatility.volatility_factor,
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

    def _compute_severity(self, estimated_pnl: float, z: float,
                          trader_vol_share: float, market_participants: int) -> str:
        score = 0

        # Estimated PnL (0-35)
        if estimated_pnl > 50000:
            score += 35
        elif estimated_pnl > 10000:
            score += 25
        elif estimated_pnl > 1000:
            score += 15
        else:
            score += 5

        # Z-score (0-25)
        if z > 4.5:
            score += 25
        elif z > 3.5:
            score += 20
        elif z > 2.5:
            score += 15
        else:
            score += 10

        # Market dominance (0-25)
        if trader_vol_share > 0.40:
            score += 25
        elif trader_vol_share > 0.20:
            score += 15
        elif trader_vol_share > 0.10:
            score += 10
        else:
            score += 5

        # Context: if many participants also bought, less likely the trader caused it
        if market_participants > 10:
            score -= 10  # mitigating: lots of participants
        elif market_participants > 5:
            score -= 5

        score = max(score, 0)

        if score >= 65:
            return "CRITICAL"
        elif score >= 45:
            return "HIGH"
        elif score >= 25:
            return "MEDIUM"
        else:
            return "LOW"
