import pandas as pd
from datetime import datetime

from src.ingestion.schemas import Alert, SEVERITY_RANK
from src.detection.statistics import BaselineStats
from src.detection.layering import LayeringDetector
from src.detection.wash_trading import WashTradingDetector
from src.detection.momentum_ignition import MomentumIgnitionDetector
from src.detection.price_ramping import PriceRampingDetector
from src.detection.marking_close import MarkingCloseDetector


class DetectionEngine:
    """
    Orchestrates all detection algorithms over a scenario DataFrame.

    Usage:
        engine = DetectionEngine(baseline_df, related_accounts)
        alerts = engine.run(scenario_df)
    """

    def __init__(
        self,
        baseline_df: pd.DataFrame,
        related_accounts: dict[str, str],
    ):
        """
        Parameters
        ----------
        baseline_df : pd.DataFrame
            Historical trade events used to compute per-trader baseline statistics.
            Expected columns match the TradeEvent schema (event_id, event_type,
            timestamp, trader_id, instrument, side, quantity, price, session,
            related_order_id, is_aggressive, etc.).
        related_accounts : dict[str, str]
            Mapping of trader_id -> beneficial_owner_id, used to detect wash trading
            between accounts controlled by the same beneficial owner.
        """
        print("  [DetectionEngine] Building baseline statistics …")
        self.stats = BaselineStats(baseline_df)
        self.related_accounts = related_accounts or {}
        print("  [DetectionEngine] Ready.")

    def run(self, scenario_df: pd.DataFrame) -> list[Alert]:
        """
        Run all detectors on scenario data. Returns alerts sorted by severity
        (CRITICAL first, then HIGH, MEDIUM, LOW).

        Parameters
        ----------
        scenario_df : pd.DataFrame
            Trade events for the scenario under investigation. Same schema as
            baseline_df.

        Returns
        -------
        list[Alert]
            Deduplicated alerts sorted by descending severity.
        """
        if scenario_df is None or scenario_df.empty:
            print("  [DetectionEngine] Empty scenario DataFrame — no alerts.")
            return []

        alerts: list[Alert] = []

        detectors = [
            LayeringDetector(scenario_df, self.stats),
            WashTradingDetector(scenario_df, self.stats, self.related_accounts),
            MomentumIgnitionDetector(scenario_df, self.stats),
            PriceRampingDetector(scenario_df, self.stats),
            MarkingCloseDetector(scenario_df, self.stats),
        ]

        for detector in detectors:
            try:
                found = detector.detect()
                alerts.extend(found)
                print(f"  {detector.__class__.__name__}: {len(found)} alert(s)")
            except Exception as e:
                print(f"  {detector.__class__.__name__} ERROR: {e}")

        alerts.sort(key=lambda a: SEVERITY_RANK[a.severity], reverse=True)
        return alerts
