import pandas as pd
import numpy as np
from datetime import datetime, timedelta, timezone

from src.ingestion.schemas import Alert
from src.detection.statistics import BaselineStats, MarketVolatility


class PriceRampingDetector:
    """
    Detects price ramping: sequential executions at escalating prices.

    Improvements over v1:
    - Fixed SELL-side monotonicity bug (now directly counts descending pairs)
    - Volatility normalization: price drift threshold scales with market conditions
    - Impact-based severity: considers notional impact and market volume share
    """

    WINDOW_MINUTES = 10
    MIN_EXECUTIONS = 4
    MIN_MONOTONICITY = 0.80
    MIN_PRICE_DELTA_PCT = 0.003   # 0.3%
    MIN_Z_SCORE = 2.0

    def __init__(self, events_df: pd.DataFrame, stats: BaselineStats,
                 market_volatility: MarketVolatility | None = None):
        self.df = events_df.copy() if events_df is not None else pd.DataFrame()
        self.stats = stats
        self.volatility = market_volatility or MarketVolatility()
        self._counter = 0

    def _next_alert_id(self) -> str:
        self._counter += 1
        return f"TRD-{datetime.now().strftime('%Y%m%d')}-PRC-{self._counter:04d}"

    def _compute_monotonicity(self, prices: list[float], ascending: bool = True) -> float:
        """Fraction of consecutive pairs in the specified direction.
        ascending=True: count pairs where price[i+1] > price[i]
        ascending=False: count pairs where price[i+1] < price[i]
        """
        if len(prices) < 2:
            return 0.0
        if ascending:
            n_match = sum(1 for a, b in zip(prices, prices[1:]) if b > a)
        else:
            n_match = sum(1 for a, b in zip(prices, prices[1:]) if b < a)
        return n_match / (len(prices) - 1)

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

        # Volatility-adjusted thresholds
        vf = self.volatility.volatility_factor
        adjusted_price_delta = self.MIN_PRICE_DELTA_PCT * vf
        adjusted_min_z = max(self.MIN_Z_SCORE * (1.0 / vf), 1.5)

        window_td = timedelta(minutes=self.WINDOW_MINUTES)
        seen_windows: set[tuple] = set()

        for (trader_id, instrument), group in exec_df.groupby(["trader_id", "instrument"]):
            group = group.sort_values("timestamp")
            if len(group) < self.MIN_EXECUTIONS:
                continue

            # Market volume for this instrument (for impact estimation)
            instr_market = exec_df[exec_df["instrument"] == instrument]

            for side in ["BUY", "SELL"]:
                side_group = group[group["side"] == side].sort_values("timestamp")
                if len(side_group) < self.MIN_EXECUTIONS:
                    continue

                timestamps = side_group["timestamp"].tolist()

                for i, t_start in enumerate(timestamps):
                    t_end = t_start + window_td

                    bucket = (
                        trader_id, instrument, side,
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

                    # Fixed: direct ascending/descending check per side
                    if side == "BUY":
                        monotonicity = self._compute_monotonicity(prices, ascending=True)
                        price_delta_pct = (last_price - first_price) / first_price
                    else:
                        # SELL ramping: prices should go DOWN (descending)
                        monotonicity = self._compute_monotonicity(prices, ascending=False)
                        price_delta_pct = (first_price - last_price) / first_price  # positive = downward drift

                    if monotonicity < self.MIN_MONOTONICITY:
                        continue

                    if price_delta_pct < adjusted_price_delta:
                        continue

                    window_volume = window["quantity"].sum()
                    trading_minutes = 390.0
                    annualized_vol = window_volume * (trading_minutes / float(self.WINDOW_MINUTES))
                    z = self.stats.z_score(trader_id, "daily_volume_mean", annualized_vol)
                    if z < adjusted_min_z:
                        continue

                    seen_windows.add(bucket)

                    # Impact estimation
                    notional_impact = float(window_volume) * abs(last_price - first_price)

                    # Market volume share
                    market_window = instr_market[
                        (instr_market["timestamp"] >= t_start) & (instr_market["timestamp"] < t_end)
                    ]
                    market_vol = float(market_window["quantity"].sum())
                    market_vol_share = float(window_volume) / max(market_vol, 1.0)

                    severity = self._compute_severity(
                        price_delta_pct, notional_impact, z, market_vol_share
                    )

                    # Raw (signed) delta for evidence
                    raw_delta_pct = (last_price - first_price) / first_price

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
                            "notional_impact": round(notional_impact, 2),
                            "estimated_impact": round(notional_impact, 2),
                            "market_vol_share": round(market_vol_share, 4),
                            "window_start": str(t_start),
                            "window_end": str(t_end),
                            "market_volatility_factor": self.volatility.volatility_factor,
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
                    break

        return alerts

    def _compute_severity(self, price_delta_pct: float, notional_impact: float,
                          z: float, market_vol_share: float) -> str:
        score = 0

        # Price delta (0-30)
        if price_delta_pct > 0.01:  # >1%
            score += 30
        elif price_delta_pct > 0.006:
            score += 20
        elif price_delta_pct > 0.003:
            score += 10
        else:
            score += 5

        # Notional impact (0-30)
        if notional_impact > 50000:
            score += 30
        elif notional_impact > 10000:
            score += 20
        elif notional_impact > 1000:
            score += 10
        else:
            score += 5

        # Z-score (0-20)
        if z > 4.0:
            score += 20
        elif z > 3.0:
            score += 15
        else:
            score += 10

        # Market share (0-20)
        if market_vol_share > 0.30:
            score += 20
        elif market_vol_share > 0.15:
            score += 15
        elif market_vol_share > 0.05:
            score += 10
        else:
            score += 5

        if score >= 70:
            return "CRITICAL"
        elif score >= 50:
            return "HIGH"
        elif score >= 30:
            return "MEDIUM"
        else:
            return "LOW"
