import pandas as pd
import numpy as np
from datetime import datetime, timedelta, timezone
from itertools import combinations

from src.ingestion.schemas import Alert
from src.detection.statistics import BaselineStats, MarketVolatility


class WashTradingDetector:
    """
    Detects wash trading: matched BUY/SELL between accounts with same beneficial owner.

    Improvements over v1:
    - Volatility normalization: thresholds adjust during volatile periods
    - Impact-based severity: factors in wash volume * price to estimate economic impact
    """

    WINDOW_MINUTES = 30
    MIN_PAIRS = 3
    MIN_WASH_FRACTION = 0.20
    MAX_PRICE_DEVIATION = 0.001   # 0.1%
    MAX_QTY_DEVIATION = 0.05      # 5%
    MAX_TIME_OFFSET_SECONDS = 120

    def __init__(
        self,
        events_df: pd.DataFrame,
        stats: BaselineStats,
        related_accounts: dict[str, str],
        market_volatility: MarketVolatility | None = None,
    ):
        self.df = events_df.copy() if events_df is not None else pd.DataFrame()
        self.stats = stats
        self.related_accounts = related_accounts or {}
        self.volatility = market_volatility or MarketVolatility()
        self._counter = 0

    def _next_alert_id(self) -> str:
        self._counter += 1
        return f"TRD-{datetime.now().strftime('%Y%m%d')}-WSH-{self._counter:04d}"

    def _group_traders_by_owner(self) -> dict[str, list[str]]:
        owner_to_traders: dict[str, list[str]] = {}
        for trader_id, owner_id in self.related_accounts.items():
            owner_to_traders.setdefault(owner_id, []).append(trader_id)
        return owner_to_traders

    def _match_pairs(self, sells: pd.DataFrame, buys: pd.DataFrame) -> list[tuple]:
        matched = []
        used_buy_ids: set[str] = set()

        for _, sell in sells.iterrows():
            for _, buy in buys.iterrows():
                if buy["event_id"] in used_buy_ids:
                    continue
                time_delta = abs(
                    (sell["timestamp"] - buy["timestamp"]).total_seconds()
                )
                if time_delta > self.MAX_TIME_OFFSET_SECONDS:
                    continue
                price_mid = (sell["price"] + buy["price"]) / 2
                price_dev = (
                    abs(sell["price"] - buy["price"]) / price_mid
                    if price_mid > 0 else 0.0
                )
                if price_dev > self.MAX_PRICE_DEVIATION:
                    continue
                qty_mid = (sell["quantity"] + buy["quantity"]) / 2
                qty_dev = (
                    abs(sell["quantity"] - buy["quantity"]) / qty_mid
                    if qty_mid > 0 else 0.0
                )
                if qty_dev > self.MAX_QTY_DEVIATION:
                    continue
                matched.append((
                    sell["event_id"], buy["event_id"],
                    min(sell["quantity"], buy["quantity"]),
                    (sell["price"] + buy["price"]) / 2,  # avg matched price
                ))
                used_buy_ids.add(buy["event_id"])
                break

        return matched

    def detect(self) -> list[Alert]:
        alerts: list[Alert] = []

        if self.df.empty or not self.related_accounts:
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
        adjusted_min_wash = min(self.MIN_WASH_FRACTION * vf, 0.60)

        owner_to_traders = self._group_traders_by_owner()
        window_td = timedelta(minutes=self.WINDOW_MINUTES)
        seen_windows: set[tuple] = set()

        for owner_id, traders in owner_to_traders.items():
            if len(traders) < 2:
                continue

            trader_pairs = list(combinations(traders, 2))
            owner_execs = exec_df[exec_df["trader_id"].isin(traders)]
            if owner_execs.empty:
                continue

            instruments = owner_execs["instrument"].unique()

            for instrument in instruments:
                instr_execs = owner_execs[owner_execs["instrument"] == instrument]
                if instr_execs.empty:
                    continue

                all_timestamps = instr_execs["timestamp"].sort_values().tolist()

                for t_start in all_timestamps:
                    t_end = t_start + window_td
                    bucket = (owner_id, instrument, t_start.floor("30min") if hasattr(t_start, "floor") else t_start)
                    if bucket in seen_windows:
                        continue

                    window = instr_execs[
                        (instr_execs["timestamp"] >= t_start)
                        & (instr_execs["timestamp"] < t_end)
                    ]
                    if window.empty:
                        continue

                    total_window_volume = window["quantity"].sum()
                    if total_window_volume == 0:
                        continue

                    all_matched_pairs = []
                    wash_volume = 0.0
                    wash_notional = 0.0  # volume * price for impact estimation
                    primary_trader = traders[0]

                    for trader_a, trader_b in trader_pairs:
                        a_execs = window[window["trader_id"] == trader_a]
                        b_execs = window[window["trader_id"] == trader_b]
                        if a_execs.empty or b_execs.empty:
                            continue

                        a_sells = a_execs[a_execs["side"] == "SELL"]
                        b_buys = b_execs[b_execs["side"] == "BUY"]
                        matched_ab = self._match_pairs(a_sells, b_buys)

                        a_buys = a_execs[a_execs["side"] == "BUY"]
                        b_sells = b_execs[b_execs["side"] == "SELL"]
                        matched_ba = self._match_pairs(b_sells, a_buys)

                        for sell_id, buy_id, qty, avg_price in matched_ab + matched_ba:
                            all_matched_pairs.append((sell_id, buy_id))
                            wash_volume += qty
                            wash_notional += qty * avg_price

                    if len(all_matched_pairs) < self.MIN_PAIRS:
                        continue

                    wash_fraction = wash_volume / total_window_volume
                    if wash_fraction < adjusted_min_wash:
                        continue

                    seen_windows.add(bucket)

                    z = self.stats.z_score(primary_trader, "daily_volume_mean", total_window_volume)

                    # Impact-based severity
                    severity = self._compute_severity(wash_fraction, wash_notional, z)

                    all_event_ids = list(set(
                        [s for s, _ in all_matched_pairs] +
                        [b for _, b in all_matched_pairs]
                    ))
                    baseline_stat, _ = self.stats.get(primary_trader)

                    alert = Alert(
                        alert_id=self._next_alert_id(),
                        detected_at=datetime.now(tz=timezone.utc),
                        pattern_type="WASH_TRADING",
                        severity=severity,
                        trader_id=primary_trader,
                        instrument=instrument,
                        evidence={
                            "beneficial_owner_id": owner_id,
                            "involved_traders": traders,
                            "n_matched_pairs": len(all_matched_pairs),
                            "wash_volume": round(wash_volume, 4),
                            "wash_notional": round(wash_notional, 2),
                            "total_window_volume": round(total_window_volume, 4),
                            "wash_fraction": round(wash_fraction, 4),
                            "window_start": str(t_start),
                            "window_end": str(t_end),
                            "estimated_impact": round(wash_notional, 2),
                            "market_volatility_factor": self.volatility.volatility_factor,
                        },
                        event_ids=all_event_ids,
                        z_score=round(z, 4),
                        baseline_metric=round(baseline_stat.daily_volume_mean, 4),
                        observed_metric=round(total_window_volume, 4),
                        baseline_description=(
                            f"Trader {primary_trader} baseline daily volume "
                            f"{baseline_stat.daily_volume_mean:.2f} "
                            f"± {baseline_stat.daily_volume_std:.2f}"
                        ),
                    )
                    alerts.append(alert)

        return alerts

    def _compute_severity(self, wash_fraction: float, wash_notional: float, z: float) -> str:
        score = 0

        # Wash fraction (0-30)
        if wash_fraction > 0.70:
            score += 30
        elif wash_fraction > 0.50:
            score += 20
        elif wash_fraction > 0.30:
            score += 15
        else:
            score += 5

        # Notional impact (0-35)
        if wash_notional > 500000:
            score += 35
        elif wash_notional > 100000:
            score += 25
        elif wash_notional > 10000:
            score += 15
        else:
            score += 5

        # Z-score (0-20)
        if z > 4.0:
            score += 20
        elif z > 3.0:
            score += 15
        elif z > 2.0:
            score += 10
        else:
            score += 5

        # Number of pairs penalty captured via wash_fraction already

        if score >= 65:
            return "CRITICAL"
        elif score >= 45:
            return "HIGH"
        elif score >= 25:
            return "MEDIUM"
        else:
            return "LOW"
