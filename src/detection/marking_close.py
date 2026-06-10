import pandas as pd
import numpy as np
from datetime import datetime, timezone

from src.ingestion.schemas import Alert, TraderProfile
from src.detection.statistics import BaselineStats, MarketVolatility


class MarkingCloseDetector:
    """
    Detects marking the close: dominating close-window volume to push settlement price.

    Improvements over v1:
    - Fund/index exemption: accounts with type='fund' get elevated thresholds
    - Volatility normalization for price drift threshold
    - Impact-based severity: considers trader's price impact vs market drift
    """

    PRICE_DRIFT_THRESHOLD = 0.003   # 0.3%
    TRADER_FRACTION_THRESHOLD = 0.40
    FUND_FRACTION_THRESHOLD = 0.60    # Higher threshold for fund accounts (rebalancing)
    MIN_Z_SCORE = 3.0

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
        return f"TRD-{datetime.now().strftime('%Y%m%d')}-MKC-{self._counter:04d}"

    def _is_fund(self, trader_id: str) -> bool:
        profile = self.profiles.get(trader_id)
        return bool(profile and profile.account_type in ("fund", "index_fund", "etf"))

    def detect(self) -> list[Alert]:
        alerts: list[Alert] = []

        if self.df.empty:
            return alerts

        df = self.df.copy()
        if not pd.api.types.is_datetime64_any_dtype(df["timestamp"]):
            df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True, errors="coerce")
        df = df.dropna(subset=["timestamp"]).sort_values("timestamp")

        close_df = df[
            (df["event_type"] == "TRADE_EXECUTE") & (df["session"] == "CLOSE")
        ].copy()

        if close_df.empty:
            return alerts

        # Volatility-adjusted price drift threshold
        vf = self.volatility.volatility_factor
        adjusted_drift = self.PRICE_DRIFT_THRESHOLD * vf
        adjusted_min_z = max(self.MIN_Z_SCORE * (1.0 / vf), 2.0)

        close_df["date"] = close_df["timestamp"].dt.date

        seen_events: set[tuple] = set()

        for (instrument, date), day_close in close_df.groupby(["instrument", "date"]):
            if day_close.empty:
                continue

            total_close_volume = day_close["quantity"].sum()
            if total_close_volume == 0:
                continue

            sorted_day = day_close.sort_values("timestamp")
            first_price = float(sorted_day.iloc[0]["price"])
            last_price = float(sorted_day.iloc[-1]["price"])

            if first_price == 0:
                continue

            price_drift_pct = abs(last_price - first_price) / first_price
            if price_drift_pct < adjusted_drift:
                continue

            for trader_id, trader_close in day_close.groupby("trader_id"):
                bucket = (trader_id, instrument, str(date))
                if bucket in seen_events:
                    continue

                trader_close_volume = trader_close["quantity"].sum()
                trader_close_fraction = trader_close_volume / total_close_volume

                # Fund exemption: higher threshold for fund accounts
                is_fund = self._is_fund(trader_id)
                threshold = self.FUND_FRACTION_THRESHOLD if is_fund else self.TRADER_FRACTION_THRESHOLD

                if trader_close_fraction < threshold:
                    continue

                z = self.stats.z_score(
                    trader_id, "close_volume_fraction_mean", trader_close_fraction
                )
                if z < adjusted_min_z:
                    continue

                seen_events.add(bucket)

                buy_vol = trader_close[trader_close["side"] == "BUY"]["quantity"].sum()
                sell_vol = trader_close[trader_close["side"] == "SELL"]["quantity"].sum()
                dominant_side = "BUY" if buy_vol >= sell_vol else "SELL"

                # Compute trader's price impact
                trader_avg_price = float(trader_close["price"].mean())
                market_avg_price = float(day_close["price"].mean())
                price_impact = abs(trader_avg_price - market_avg_price) / max(market_avg_price, 0.01)

                # Notional impact
                notional_impact = float(trader_close_volume) * abs(last_price - first_price)

                severity = self._compute_severity(
                    trader_close_fraction, z, notional_impact, price_impact, is_fund
                )

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
                        "trader_price_impact": round(price_impact, 6),
                        "notional_impact": round(notional_impact, 2),
                        "estimated_impact": round(notional_impact, 2),
                        "fund_exemption_applied": is_fund,
                        "market_volatility_factor": self.volatility.volatility_factor,
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

    def _compute_severity(self, trader_fraction: float, z: float,
                          notional_impact: float, price_impact: float,
                          is_fund: bool) -> str:
        score = 0

        # Trader fraction (0-25)
        if trader_fraction > 0.70:
            score += 25
        elif trader_fraction > 0.55:
            score += 20
        elif trader_fraction > 0.45:
            score += 15
        else:
            score += 10

        # Z-score (0-25)
        if z > 5.0:
            score += 25
        elif z > 4.0:
            score += 20
        elif z > 3.5:
            score += 15
        else:
            score += 10

        # Notional impact (0-25)
        if notional_impact > 50000:
            score += 25
        elif notional_impact > 10000:
            score += 15
        elif notional_impact > 1000:
            score += 10
        else:
            score += 5

        # Price impact (0-15)
        if price_impact > 0.005:  # >0.5% price impact
            score += 15
        elif price_impact > 0.002:
            score += 10
        else:
            score += 5

        # Fund exemption: reduce score by 15 points
        if is_fund:
            score = max(score - 15, 0)

        if score >= 70:
            return "CRITICAL"
        elif score >= 50:
            return "HIGH"
        elif score >= 30:
            return "MEDIUM"
        else:
            return "LOW"
