import pandas as pd
from datetime import datetime

from src.ingestion.schemas import Alert, TraderProfile, SEVERITY_RANK
from src.detection.statistics import BaselineStats, MarketVolatility
from src.detection.layering import LayeringDetector
from src.detection.wash_trading import WashTradingDetector
from src.detection.momentum_ignition import MomentumIgnitionDetector
from src.detection.price_ramping import PriceRampingDetector
from src.detection.marking_close import MarkingCloseDetector

# ── Instrument → Jurisdiction mapping ────────────────────────────────────────
# Derived from listing venue / exchange suffix. Extensible via config.
INSTRUMENT_JURISDICTION: dict[str, str] = {}

# Default jurisdiction by known US stocks (extend as needed)
_US_INSTRUMENTS = {
    "AAPL", "MSFT", "TSLA", "NVDA", "GOOGL", "AMZN", "META", "NFLX",
    "AMD", "INTC", "ORCL", "CRM", "PYPL", "SQ", "UBER", "COIN",
    "JPM", "BAC", "GS", "MS", "C", "WFC",
}


def infer_jurisdiction(instrument: str) -> str:
    """Infer regulatory jurisdiction from instrument name/suffix."""
    if instrument in INSTRUMENT_JURISDICTION:
        return INSTRUMENT_JURISDICTION[instrument]
    # Check suffixes: .L = London, .NS/.BO = India, .DE = Germany, .T = Tokyo
    if instrument.endswith(".L") or instrument.endswith(".LSE"):
        return "UK"
    if instrument.endswith(".NS") or instrument.endswith(".BO"):
        return "IN"
    if instrument.endswith(".DE") or instrument.endswith(".F"):
        return "EU"
    if instrument.endswith(".T"):
        return "JP"
    if instrument.endswith(".HK"):
        return "HK"
    # Check known US symbols
    base = instrument.split(".")[0]
    if base in _US_INSTRUMENTS:
        return "US"
    # Default: assume US for plain symbols (most common in this dataset)
    if "." not in instrument:
        return "US"
    return "UNKNOWN"


class DetectionEngine:
    """
    Orchestrates all detection algorithms over a scenario DataFrame.

    Usage:
        engine = DetectionEngine(baseline_df, related_accounts, profiles)
        alerts = engine.run(scenario_df)
    """

    def __init__(
        self,
        baseline_df: pd.DataFrame,
        related_accounts: dict[str, str],
        profiles: dict[str, TraderProfile] | None = None,
    ):
        print("  [DetectionEngine] Building baseline statistics …")
        self.stats = BaselineStats(baseline_df)
        self.related_accounts = related_accounts or {}
        self.profiles = profiles or {}
        self._market_volatility: MarketVolatility | None = None
        print("  [DetectionEngine] Ready.")

    def run(self, scenario_df: pd.DataFrame) -> list[Alert]:
        """
        Run all detectors on scenario data. Returns alerts sorted by severity
        (CRITICAL first, then HIGH, MEDIUM, LOW).
        """
        if scenario_df is None or scenario_df.empty:
            print("  [DetectionEngine] Empty scenario DataFrame — no alerts.")
            return []

        # Compute market-wide volatility for threshold normalization
        self._market_volatility = self.stats.compute_market_volatility(scenario_df)
        vf = self._market_volatility.volatility_factor
        print(f"  [DetectionEngine] Market volatility factor: {vf:.2f}"
              f" ({'elevated' if vf > 1.3 else 'normal' if vf < 1.1 else 'slightly elevated'})")

        alerts: list[Alert] = []

        detectors = [
            LayeringDetector(scenario_df, self.stats, self.profiles, self._market_volatility),
            WashTradingDetector(scenario_df, self.stats, self.related_accounts, self._market_volatility),
            MomentumIgnitionDetector(scenario_df, self.stats, self._market_volatility),
            PriceRampingDetector(scenario_df, self.stats, self._market_volatility),
            MarkingCloseDetector(scenario_df, self.stats, self.profiles, self._market_volatility),
        ]

        for detector in detectors:
            try:
                found = detector.detect()
                # Enrich alerts with jurisdiction
                for alert in found:
                    alert.jurisdiction = infer_jurisdiction(alert.instrument)
                alerts.extend(found)
                print(f"  {detector.__class__.__name__}: {len(found)} alert(s)")
            except Exception as e:
                print(f"  {detector.__class__.__name__} ERROR: {e}")

        alerts.sort(key=lambda a: SEVERITY_RANK[a.severity], reverse=True)
        return alerts
