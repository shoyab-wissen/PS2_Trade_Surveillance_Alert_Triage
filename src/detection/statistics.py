import pandas as pd
import numpy as np
from dataclasses import dataclass, field


@dataclass
class TraderStats:
    cancel_ratio_mean: float
    cancel_ratio_std: float
    median_ttc_ms_mean: float     # time-to-cancel in ms
    median_ttc_ms_std: float
    daily_volume_mean: float
    daily_volume_std: float
    order_to_trade_ratio_mean: float
    order_to_trade_ratio_std: float
    close_volume_fraction_mean: float  # fraction of daily volume in CLOSE session
    close_volume_fraction_std: float


class BaselineStats:
    def __init__(self, baseline_df: pd.DataFrame):
        self._stats: dict[str, TraderStats] = {}
        self._market_stats: TraderStats | None = None

        if baseline_df is None or baseline_df.empty:
            self._market_stats = TraderStats(
                cancel_ratio_mean=0.3,
                cancel_ratio_std=0.15,
                median_ttc_ms_mean=5000.0,
                median_ttc_ms_std=3000.0,
                daily_volume_mean=10000.0,
                daily_volume_std=5000.0,
                order_to_trade_ratio_mean=3.0,
                order_to_trade_ratio_std=2.0,
                close_volume_fraction_mean=0.1,
                close_volume_fraction_std=0.05,
            )
            return

        df = baseline_df.copy()

        # Ensure timestamp is datetime
        if "timestamp" in df.columns and not pd.api.types.is_datetime64_any_dtype(df["timestamp"]):
            df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True, errors="coerce")

        df["date"] = df["timestamp"].dt.date

        # ── Cancel ratio ──────────────────────────────────────────────────────
        places = (
            df[df["event_type"] == "ORDER_PLACE"]
            .groupby(["trader_id", "instrument", "date"])["event_id"]
            .count()
            .rename("places")
        )
        cancels = (
            df[df["event_type"] == "ORDER_CANCEL"]
            .groupby(["trader_id", "instrument", "date"])["event_id"]
            .count()
            .rename("cancels")
        )
        pc = pd.concat([places, cancels], axis=1).fillna(0)
        pc["cancel_ratio"] = pc["cancels"] / (pc["places"] + pc["cancels"] + 1e-9)

        cancel_stats = (
            pc.reset_index()
            .groupby("trader_id")["cancel_ratio"]
            .agg(["mean", "std"])
            .rename(columns={"mean": "cancel_ratio_mean", "std": "cancel_ratio_std"})
        )

        # ── Median time-to-cancel (ms) ────────────────────────────────────────
        place_ts = (
            df[df["event_type"] == "ORDER_PLACE"][["order_id", "timestamp", "trader_id"]]
            .rename(columns={"order_id": "related_order_id", "timestamp": "place_ts"})
        )
        cancel_rows = df[df["event_type"] == "ORDER_CANCEL"].dropna(subset=["related_order_id"])
        ttc_df = cancel_rows.merge(place_ts, on="related_order_id", how="inner", suffixes=("", "_place"))
        ttc_df["ttc_ms"] = (
            ttc_df["timestamp"] - ttc_df["place_ts"]
        ).dt.total_seconds() * 1000
        ttc_df = ttc_df[ttc_df["ttc_ms"] >= 0]

        ttc_stats = (
            ttc_df.groupby("trader_id")["ttc_ms"]
            .agg(lambda x: x.median())
            .rename("median_ttc_ms")
        )
        ttc_stats_agg = (
            ttc_df.groupby("trader_id")["ttc_ms"]
            .agg(["median", "std"])
            .rename(columns={"median": "median_ttc_ms_mean", "std": "median_ttc_ms_std"})
        )

        # ── Daily volume ──────────────────────────────────────────────────────
        exec_df = df[df["event_type"] == "TRADE_EXECUTE"]
        daily_vol = (
            exec_df.groupby(["trader_id", "instrument", "date"])["quantity"]
            .sum()
            .rename("volume")
        )
        vol_stats = (
            daily_vol.reset_index()
            .groupby("trader_id")["volume"]
            .agg(["mean", "std"])
            .rename(columns={"mean": "daily_volume_mean", "std": "daily_volume_std"})
        )

        # ── Order-to-trade ratio ──────────────────────────────────────────────
        orders_count = (
            df[df["event_type"].isin(["ORDER_PLACE", "ORDER_CANCEL"])]
            .groupby(["trader_id", "date"])["event_id"]
            .count()
            .rename("orders")
        )
        trades_count = (
            exec_df.groupby(["trader_id", "date"])["event_id"]
            .count()
            .rename("trades")
        )
        ot = pd.concat([orders_count, trades_count], axis=1).fillna(0)
        ot["otr"] = ot["orders"] / (ot["trades"] + 1e-9)

        otr_stats = (
            ot.reset_index()
            .groupby("trader_id")["otr"]
            .agg(["mean", "std"])
            .rename(columns={"mean": "order_to_trade_ratio_mean", "std": "order_to_trade_ratio_std"})
        )

        # ── Close volume fraction ─────────────────────────────────────────────
        close_vol = (
            exec_df[exec_df["session"] == "CLOSE"]
            .groupby(["trader_id", "date"])["quantity"]
            .sum()
            .rename("close_vol")
        )
        total_daily = (
            exec_df.groupby(["trader_id", "date"])["quantity"]
            .sum()
            .rename("total_vol")
        )
        cvf = pd.concat([close_vol, total_daily], axis=1).fillna(0)
        cvf["close_fraction"] = cvf["close_vol"] / (cvf["total_vol"] + 1e-9)

        cvf_stats = (
            cvf.reset_index()
            .groupby("trader_id")["close_fraction"]
            .agg(["mean", "std"])
            .rename(columns={"mean": "close_volume_fraction_mean", "std": "close_volume_fraction_std"})
        )

        # ── Merge all stats per trader ────────────────────────────────────────
        all_traders = set(df["trader_id"].unique())
        combined = (
            cancel_stats
            .join(ttc_stats_agg, how="outer")
            .join(vol_stats, how="outer")
            .join(otr_stats, how="outer")
            .join(cvf_stats, how="outer")
            .fillna(0)
        )

        for trader_id in all_traders:
            row = combined.loc[trader_id] if trader_id in combined.index else None
            if row is None:
                continue
            self._stats[trader_id] = TraderStats(
                cancel_ratio_mean=float(row.get("cancel_ratio_mean", 0.3)),
                cancel_ratio_std=float(row.get("cancel_ratio_std", 0.15)),
                median_ttc_ms_mean=float(row.get("median_ttc_ms_mean", 5000.0)),
                median_ttc_ms_std=float(row.get("median_ttc_ms_std", 3000.0)),
                daily_volume_mean=float(row.get("daily_volume_mean", 10000.0)),
                daily_volume_std=float(row.get("daily_volume_std", 5000.0)),
                order_to_trade_ratio_mean=float(row.get("order_to_trade_ratio_mean", 3.0)),
                order_to_trade_ratio_std=float(row.get("order_to_trade_ratio_std", 2.0)),
                close_volume_fraction_mean=float(row.get("close_volume_fraction_mean", 0.1)),
                close_volume_fraction_std=float(row.get("close_volume_fraction_std", 0.05)),
            )

        # ── Market-wide fallback ──────────────────────────────────────────────
        if len(combined) > 0:
            m = combined.mean()
            s = combined.std().fillna(0)
            self._market_stats = TraderStats(
                cancel_ratio_mean=float(m.get("cancel_ratio_mean", 0.3)),
                cancel_ratio_std=float(s.get("cancel_ratio_std", 0.15)),
                median_ttc_ms_mean=float(m.get("median_ttc_ms_mean", 5000.0)),
                median_ttc_ms_std=float(s.get("median_ttc_ms_std", 3000.0)),
                daily_volume_mean=float(m.get("daily_volume_mean", 10000.0)),
                daily_volume_std=float(s.get("daily_volume_std", 5000.0)),
                order_to_trade_ratio_mean=float(m.get("order_to_trade_ratio_mean", 3.0)),
                order_to_trade_ratio_std=float(s.get("order_to_trade_ratio_std", 2.0)),
                close_volume_fraction_mean=float(m.get("close_volume_fraction_mean", 0.1)),
                close_volume_fraction_std=float(s.get("close_volume_fraction_std", 0.05)),
            )
        else:
            self._market_stats = TraderStats(
                cancel_ratio_mean=0.3,
                cancel_ratio_std=0.15,
                median_ttc_ms_mean=5000.0,
                median_ttc_ms_std=3000.0,
                daily_volume_mean=10000.0,
                daily_volume_std=5000.0,
                order_to_trade_ratio_mean=3.0,
                order_to_trade_ratio_std=2.0,
                close_volume_fraction_mean=0.1,
                close_volume_fraction_std=0.05,
            )

    def get(self, trader_id: str) -> tuple[TraderStats, bool]:
        """Returns (stats, is_fallback) — is_fallback=True if using market-wide stats."""
        if trader_id in self._stats:
            return self._stats[trader_id], False
        return self._market_stats, True

    def z_score(self, trader_id: str, metric: str, observed: float) -> float:
        """
        Returns (observed - mean) / std, clamped to [-10, 10].
        metric is one of the TraderStats field names (e.g. 'cancel_ratio_mean').
        Strips trailing '_mean' to find the std field automatically, or accepts
        field name directly.
        """
        stats, _ = self.get(trader_id)

        # Normalise metric name: accept 'cancel_ratio_mean' or 'cancel_ratio'
        if metric.endswith("_mean"):
            mean_field = metric
            std_field = metric[: -len("_mean")] + "_std"
        else:
            mean_field = metric + "_mean"
            std_field = metric + "_std"

        mean_val = getattr(stats, mean_field, 0.0)
        std_val = getattr(stats, std_field, 0.0)

        if std_val == 0.0 or np.isnan(std_val):
            return 0.0

        z = (observed - mean_val) / std_val
        return float(np.clip(z, -10.0, 10.0))
