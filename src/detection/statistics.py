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


@dataclass
class MarketVolatility:
    """Market-wide volatility context for a time window.
    Used to normalize detector thresholds during volatile periods."""
    avg_cancel_ratio: float = 0.30       # market-wide average cancel ratio
    avg_price_volatility: float = 0.005  # average |price_change|/price per window
    avg_volume_per_window: float = 10000.0
    total_participants: int = 100
    # Scaling factor: 1.0 = normal, >1.0 = elevated volatility
    volatility_factor: float = 1.0


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

    def compute_market_volatility(self, scenario_df: pd.DataFrame, window_minutes: int = 5) -> MarketVolatility:
        """Compute market-wide volatility from scenario data for threshold normalization."""
        if scenario_df is None or scenario_df.empty:
            return MarketVolatility()

        df = scenario_df.copy()
        if not pd.api.types.is_datetime64_any_dtype(df["timestamp"]):
            df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True, errors="coerce")

        # Market-wide cancel ratio
        total_places = len(df[df["event_type"] == "ORDER_PLACE"])
        total_cancels = len(df[df["event_type"] == "ORDER_CANCEL"])
        total_ops = total_places + total_cancels
        mkt_cancel_ratio = total_cancels / total_ops if total_ops > 0 else 0.3

        # Price volatility: average absolute price change per instrument
        exec_df = df[df["event_type"] == "TRADE_EXECUTE"].copy()
        price_vols = []
        if not exec_df.empty:
            for instr, grp in exec_df.groupby("instrument"):
                prices = grp.sort_values("timestamp")["price"]
                if len(prices) > 1:
                    pct_changes = prices.pct_change().dropna().abs()
                    price_vols.append(float(pct_changes.mean()))
        avg_price_vol = float(np.mean(price_vols)) if price_vols else 0.005

        # Total participants
        total_participants = df["trader_id"].nunique()

        # Average volume per window
        avg_vol = float(exec_df["quantity"].sum() / max(total_participants, 1)) if not exec_df.empty else 10000.0

        # Volatility factor: compare current cancel ratio and price vol to baseline
        baseline_cr = self._market_stats.cancel_ratio_mean if self._market_stats else 0.3
        cr_ratio = mkt_cancel_ratio / max(baseline_cr, 0.01)
        vol_ratio = avg_price_vol / 0.005  # normalized to typical 0.5% price volatility

        # Factor: geometric mean of cancel ratio surge and price volatility surge
        # Clamped to [0.5, 3.0] — never suppress more than 50%, never relax more than 3x
        raw_factor = (cr_ratio * vol_ratio) ** 0.5
        volatility_factor = float(np.clip(raw_factor, 0.5, 3.0))

        return MarketVolatility(
            avg_cancel_ratio=round(mkt_cancel_ratio, 4),
            avg_price_volatility=round(avg_price_vol, 6),
            avg_volume_per_window=round(avg_vol, 2),
            total_participants=total_participants,
            volatility_factor=round(volatility_factor, 3),
        )

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


# ── 7-Day Trader Summary ──────────────────────────────────────────────────────

@dataclass
class Trader7DaySummary:
    trader_id: str
    is_new_trader: bool
    days_covered: int
    date_range: str

    # Daily averages
    avg_daily_orders: float = 0.0
    avg_daily_cancels: float = 0.0
    avg_daily_executions: float = 0.0
    avg_daily_volume: float = 0.0
    avg_cancel_ratio: float = 0.0
    avg_order_to_trade_ratio: float = 0.0
    avg_median_ttc_ms: float = 0.0
    close_volume_fraction: float = 0.0

    # Consistency across days (low std = habitual)
    cancel_ratio_daily_std: float = 0.0
    daily_volume_daily_std: float = 0.0

    # Breadth
    instruments_traded: list = field(default_factory=list)
    session_distribution: dict = field(default_factory=dict)
    side_bias: float = 0.5  # BUY fraction, 0.5 = balanced

    # Z-scores vs full baseline
    cancel_ratio_zscore: float = 0.0
    daily_volume_zscore: float = 0.0
    order_to_trade_zscore: float = 0.0
    close_volume_zscore: float = 0.0


def compute_trader_7day_summary(
    trader_id: str,
    baseline_df: pd.DataFrame | None,
    scenario_df: pd.DataFrame | None,
    baseline_stats: BaselineStats,
) -> Trader7DaySummary:
    """
    Compute a 7-day activity summary for a trader from available trade data.
    Falls back to market-wide averages for new traders.
    """
    # Combine all available data
    frames = []
    if baseline_df is not None and not baseline_df.empty:
        frames.append(baseline_df)
    if scenario_df is not None and not scenario_df.empty:
        frames.append(scenario_df)

    if not frames:
        return _market_average_summary(trader_id, baseline_stats)

    combined = pd.concat(frames, ignore_index=True)

    # Filter to this trader
    trader_df = combined[combined["trader_id"] == trader_id].copy()

    if trader_df.empty:
        return _market_average_summary(trader_id, baseline_stats)

    # Ensure timestamp is datetime
    if not pd.api.types.is_datetime64_any_dtype(trader_df["timestamp"]):
        trader_df["timestamp"] = pd.to_datetime(
            trader_df["timestamp"], utc=True, errors="coerce"
        )

    # Last 7 calendar days
    ref_date = trader_df["timestamp"].max()
    cutoff = ref_date - pd.Timedelta(days=7)
    recent = trader_df[trader_df["timestamp"] >= cutoff].copy()

    if recent.empty:
        return _market_average_summary(trader_id, baseline_stats)

    recent["date"] = recent["timestamp"].dt.date

    unique_dates = sorted(recent["date"].unique())
    days_covered = len(unique_dates)
    date_range = f"{unique_dates[0]} to {unique_dates[-1]}"

    # ── Per-day metrics ──────────────────────────────────────────────────────

    places = recent[recent["event_type"] == "ORDER_PLACE"]
    cancels = recent[recent["event_type"] == "ORDER_CANCEL"]
    execs = recent[recent["event_type"] == "TRADE_EXECUTE"]

    daily_orders = places.groupby("date").size()
    daily_cancels = cancels.groupby("date").size()
    daily_execs = execs.groupby("date").size()

    # Cancel ratio per day
    daily_cr = []
    for d in unique_dates:
        p = daily_orders.get(d, 0)
        c = daily_cancels.get(d, 0)
        total = p + c
        daily_cr.append(c / total if total > 0 else 0.0)
    daily_cr_series = pd.Series(daily_cr)

    # Daily volume (executed quantity)
    daily_vol = execs.groupby("date")["quantity"].sum() if not execs.empty else pd.Series(dtype=float)

    # Order-to-trade ratio per day
    daily_otr = []
    for d in unique_dates:
        orders = daily_orders.get(d, 0) + daily_cancels.get(d, 0)
        trades = daily_execs.get(d, 0)
        daily_otr.append(orders / trades if trades > 0 else orders)
    daily_otr_series = pd.Series(daily_otr)

    # Median TTC (time-to-cancel)
    avg_ttc = 0.0
    if not places.empty and not cancels.empty:
        place_ts = places[["order_id", "timestamp"]].rename(
            columns={"timestamp": "place_ts"}
        )
        cancel_ts = cancels[["related_order_id", "timestamp"]].rename(
            columns={"timestamp": "cancel_ts", "related_order_id": "order_id"}
        )
        merged = place_ts.merge(cancel_ts, on="order_id", how="inner")
        if not merged.empty:
            ttc_ms = (merged["cancel_ts"] - merged["place_ts"]).dt.total_seconds() * 1000
            ttc_ms = ttc_ms[ttc_ms > 0]
            avg_ttc = float(ttc_ms.median()) if not ttc_ms.empty else 0.0

    # Close volume fraction
    close_vol = 0.0
    if "session" in recent.columns and not execs.empty:
        close_execs = execs[execs["session"] == "CLOSE"]
        total_exec_vol = execs["quantity"].sum()
        close_exec_vol = close_execs["quantity"].sum() if not close_execs.empty else 0.0
        close_vol = close_exec_vol / total_exec_vol if total_exec_vol > 0 else 0.0

    # Session distribution
    session_dist = {}
    if "session" in recent.columns:
        sess_counts = recent["session"].value_counts(normalize=True)
        session_dist = {str(k): round(float(v), 3) for k, v in sess_counts.items()}

    # Side bias
    side_counts = recent[recent["event_type"].isin(["ORDER_PLACE", "TRADE_EXECUTE"])]
    buy_count = len(side_counts[side_counts["side"] == "BUY"]) if "side" in side_counts.columns else 0
    total_sides = len(side_counts) if not side_counts.empty else 1
    side_bias = buy_count / total_sides if total_sides > 0 else 0.5

    # Instruments
    instr_counts = recent["instrument"].value_counts()
    total_instr = instr_counts.sum()
    instruments = [
        f"{instr} ({round(100 * cnt / total_instr)}%)"
        for instr, cnt in instr_counts.head(5).items()
    ]

    # ── Aggregate ────────────────────────────────────────────────────────────
    avg_cancel_ratio = float(daily_cr_series.mean())
    avg_daily_vol = float(daily_vol.mean()) if not daily_vol.empty else 0.0

    # Z-scores vs full baseline
    cr_z = baseline_stats.z_score(trader_id, "cancel_ratio", avg_cancel_ratio)
    vol_z = baseline_stats.z_score(trader_id, "daily_volume", avg_daily_vol)
    otr_z = baseline_stats.z_score(
        trader_id, "order_to_trade_ratio", float(daily_otr_series.mean())
    )
    cv_z = baseline_stats.z_score(trader_id, "close_volume_fraction", close_vol)

    return Trader7DaySummary(
        trader_id=trader_id,
        is_new_trader=False,
        days_covered=days_covered,
        date_range=date_range,
        avg_daily_orders=round(float(daily_orders.mean()), 1) if not daily_orders.empty else 0.0,
        avg_daily_cancels=round(float(daily_cancels.mean()), 1) if not daily_cancels.empty else 0.0,
        avg_daily_executions=round(float(daily_execs.mean()), 1) if not daily_execs.empty else 0.0,
        avg_daily_volume=round(avg_daily_vol, 1),
        avg_cancel_ratio=round(avg_cancel_ratio, 4),
        avg_order_to_trade_ratio=round(float(daily_otr_series.mean()), 2),
        avg_median_ttc_ms=round(avg_ttc, 1),
        close_volume_fraction=round(close_vol, 4),
        cancel_ratio_daily_std=round(float(daily_cr_series.std()), 4) if len(daily_cr_series) > 1 else 0.0,
        daily_volume_daily_std=round(float(daily_vol.std()), 1) if len(daily_vol) > 1 else 0.0,
        instruments_traded=instruments,
        session_distribution=session_dist,
        side_bias=round(side_bias, 3),
        cancel_ratio_zscore=round(cr_z, 2),
        daily_volume_zscore=round(vol_z, 2),
        order_to_trade_zscore=round(otr_z, 2),
        close_volume_zscore=round(cv_z, 2),
    )


def _market_average_summary(
    trader_id: str, baseline_stats: BaselineStats
) -> Trader7DaySummary:
    """Return a summary built from market-wide averages for a new trader."""
    mkt, _ = baseline_stats.get("__nonexistent__")  # forces fallback to market stats
    return Trader7DaySummary(
        trader_id=trader_id,
        is_new_trader=True,
        days_covered=0,
        date_range="N/A (new trader)",
        avg_daily_orders=0.0,
        avg_daily_cancels=0.0,
        avg_daily_executions=0.0,
        avg_daily_volume=round(mkt.daily_volume_mean, 1),
        avg_cancel_ratio=round(mkt.cancel_ratio_mean, 4),
        avg_order_to_trade_ratio=round(mkt.order_to_trade_ratio_mean, 2),
        avg_median_ttc_ms=round(mkt.median_ttc_ms_mean, 1),
        close_volume_fraction=round(mkt.close_volume_fraction_mean, 4),
        cancel_ratio_daily_std=round(mkt.cancel_ratio_std, 4),
        daily_volume_daily_std=round(mkt.daily_volume_std, 1),
        instruments_traded=["(market average)"],
        session_distribution={"REGULAR": 0.85, "PRE": 0.05, "CLOSE": 0.10},
        side_bias=0.5,
        cancel_ratio_zscore=0.0,
        daily_volume_zscore=0.0,
        order_to_trade_zscore=0.0,
        close_volume_zscore=0.0,
    )
