import pandas as pd
import numpy as np
from datetime import datetime, timedelta, timezone
from itertools import combinations

from src.ingestion.schemas import Alert
from src.detection.statistics import BaselineStats


class WashTradingDetector:
    """
    Detects wash trading: matched BUY/SELL between accounts with same beneficial owner.

    Fire when ALL:
    - >= 3 matched BUY/SELL pairs (price within 0.1%, qty within 5%, time offset < 120s)
    - wash_fraction = wash_volume / total_window_volume >= 0.20
    - Shared beneficial_owner_id confirmed

    Severity: wash_fraction > 0.50 → HIGH, else MEDIUM
    Window: 30 minutes
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
    ):
        # related_accounts: trader_id -> beneficial_owner_id
        self.df = events_df.copy() if events_df is not None else pd.DataFrame()
        self.stats = stats
        self.related_accounts = related_accounts or {}
        self._counter = 0

    def _next_alert_id(self) -> str:
        self._counter += 1
        return f"TRD-{datetime.now().strftime('%Y%m%d')}-WSH-{self._counter:04d}"

    def _group_traders_by_owner(self) -> dict[str, list[str]]:
        """Returns beneficial_owner_id -> [trader_id, ...] mapping."""
        owner_to_traders: dict[str, list[str]] = {}
        for trader_id, owner_id in self.related_accounts.items():
            owner_to_traders.setdefault(owner_id, []).append(trader_id)
        return owner_to_traders

    def _match_pairs(
        self,
        sells: pd.DataFrame,
        buys: pd.DataFrame,
    ) -> list[tuple]:
        """
        Find matching (sell_row, buy_row) pairs where:
        - price within MAX_PRICE_DEVIATION
        - qty within MAX_QTY_DEVIATION
        - |time_delta| < MAX_TIME_OFFSET_SECONDS
        Returns list of (sell_event_id, buy_event_id, matched_qty).
        """
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
                    if price_mid > 0
                    else 0.0
                )
                if price_dev > self.MAX_PRICE_DEVIATION:
                    continue
                qty_mid = (sell["quantity"] + buy["quantity"]) / 2
                qty_dev = (
                    abs(sell["quantity"] - buy["quantity"]) / qty_mid
                    if qty_mid > 0
                    else 0.0
                )
                if qty_dev > self.MAX_QTY_DEVIATION:
                    continue
                matched.append(
                    (
                        sell["event_id"],
                        buy["event_id"],
                        min(sell["quantity"], buy["quantity"]),
                    )
                )
                used_buy_ids.add(buy["event_id"])
                break  # each sell matched once

        return matched

    def detect(self) -> list[Alert]:
        alerts: list[Alert] = []

        if self.df.empty:
            return alerts
        if not self.related_accounts:
            return alerts

        df = self.df.copy()
        if not pd.api.types.is_datetime64_any_dtype(df["timestamp"]):
            df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True, errors="coerce")
        df = df.dropna(subset=["timestamp"]).sort_values("timestamp")

        exec_df = df[df["event_type"] == "TRADE_EXECUTE"].copy()
        if exec_df.empty:
            return alerts

        owner_to_traders = self._group_traders_by_owner()
        window_td = timedelta(minutes=self.WINDOW_MINUTES)
        seen_windows: set[tuple] = set()

        for owner_id, traders in owner_to_traders.items():
            if len(traders) < 2:
                continue

            trader_pairs = list(combinations(traders, 2))

            # Get all executions for these traders
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
                    all_sell_event_ids: list[str] = []
                    all_buy_event_ids: list[str] = []
                    wash_volume = 0.0

                    # Report the first trader in the pair as the "primary" suspect
                    primary_trader = traders[0]

                    for trader_a, trader_b in trader_pairs:
                        a_execs = window[window["trader_id"] == trader_a]
                        b_execs = window[window["trader_id"] == trader_b]

                        if a_execs.empty or b_execs.empty:
                            continue

                        # A sells, B buys
                        a_sells = a_execs[a_execs["side"] == "SELL"]
                        b_buys = b_execs[b_execs["side"] == "BUY"]
                        matched_ab = self._match_pairs(a_sells, b_buys)

                        # A buys, B sells
                        a_buys = a_execs[a_execs["side"] == "BUY"]
                        b_sells = b_execs[b_execs["side"] == "SELL"]
                        matched_ba = self._match_pairs(b_sells, a_buys)

                        for sell_id, buy_id, qty in matched_ab + matched_ba:
                            all_matched_pairs.append((sell_id, buy_id))
                            all_sell_event_ids.append(sell_id)
                            all_buy_event_ids.append(buy_id)
                            wash_volume += qty

                    if len(all_matched_pairs) < self.MIN_PAIRS:
                        continue

                    wash_fraction = wash_volume / total_window_volume
                    if wash_fraction < self.MIN_WASH_FRACTION:
                        continue

                    seen_windows.add(bucket)

                    # Z-score on volume
                    z = self.stats.z_score(
                        primary_trader, "daily_volume_mean", total_window_volume
                    )

                    severity = "HIGH" if wash_fraction > 0.50 else "MEDIUM"

                    all_event_ids = list(
                        set(all_sell_event_ids + all_buy_event_ids)
                    )

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
                            "total_window_volume": round(total_window_volume, 4),
                            "wash_fraction": round(wash_fraction, 4),
                            "window_start": str(t_start),
                            "window_end": str(t_end),
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
