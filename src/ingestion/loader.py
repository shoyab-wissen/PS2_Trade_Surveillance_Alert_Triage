"""
loader.py — Load trade surveillance CSVs into DataFrames and Pydantic models.
"""

import pandas as pd
import numpy as np
from pathlib import Path
from datetime import datetime
from typing import Optional

from src.ingestion.schemas import TradeEvent, TraderProfile

# ---------------------------------------------------------------------------
# Path resolution — works regardless of where the script is invoked from
# ---------------------------------------------------------------------------
DATA_DIR = Path(__file__).parent.parent.parent / "data"


# ---------------------------------------------------------------------------
# Public loaders
# ---------------------------------------------------------------------------

def load_events(csv_path: Path) -> pd.DataFrame:
    """
    Load a trade-events CSV, parse timestamps and booleans, and return a
    clean DataFrame with all TradeEvent-compatible columns.

    Parameters
    ----------
    csv_path : Path
        Absolute or relative path to the CSV file
        (e.g. DATA_DIR / "trades_baseline.csv").

    Returns
    -------
    pd.DataFrame
        Columns: event_id, event_type, timestamp (datetime64[ns, UTC-naive]),
        trader_id, account_id, instrument, side, order_id, quantity (float64),
        price (float64), order_type, session, related_order_id (str | None),
        is_aggressive (bool).
    """
    df = pd.read_csv(
        csv_path,
        dtype={
            "event_id": str,
            "event_type": str,
            "timestamp": str,
            "trader_id": str,
            "account_id": str,
            "instrument": str,
            "side": str,
            "order_id": str,
            "quantity": float,
            "price": float,
            "order_type": str,
            "session": str,
            "related_order_id": str,   # will contain "nan" strings — handled below
            "is_aggressive": str,       # "True"/"False" strings — handled below
        },
        low_memory=False,
    )

    # Parse timestamps
    df["timestamp"] = pd.to_datetime(df["timestamp"], format="ISO8601")

    # Normalise is_aggressive: accept True/False strings, 1/0, or booleans
    def _parse_bool(val) -> bool:
        if isinstance(val, bool):
            return val
        if isinstance(val, (int, float)):
            return bool(val)
        if isinstance(val, str):
            return val.strip().lower() in ("true", "1", "yes")
        return False

    df["is_aggressive"] = df["is_aggressive"].apply(_parse_bool)

    # Normalise related_order_id: "nan", empty string, or actual NaN → None/NaN
    def _clean_optional_str(val) -> Optional[str]:
        if val is None:
            return None
        if isinstance(val, float) and np.isnan(val):
            return None
        s = str(val).strip()
        if s.lower() in ("nan", "none", ""):
            return None
        return s

    df["related_order_id"] = df["related_order_id"].apply(_clean_optional_str)

    # Ensure numeric columns are float (in case CSV had int-looking values)
    df["quantity"] = df["quantity"].astype(float)
    df["price"] = df["price"].astype(float)

    return df.reset_index(drop=True)


def load_trader_profiles() -> dict[str, TraderProfile]:
    """
    Load data/trader_profiles.csv and return a dict keyed by trader_id.

    Returns
    -------
    dict[str, TraderProfile]
        Maps trader_id → TraderProfile pydantic model.
    """
    path = DATA_DIR / "trader_profiles.csv"
    df = pd.read_csv(path, dtype=str)

    profiles: dict[str, TraderProfile] = {}
    for _, row in df.iterrows():
        trader_id = str(row["trader_id"]).strip()

        # Parse market_maker_registered
        mmr_raw = str(row.get("market_maker_registered", "False")).strip().lower()
        market_maker_registered = mmr_raw in ("true", "1", "yes")

        # Parse beneficial_owner_id
        bo_raw = row.get("beneficial_owner_id", None)
        if bo_raw is None or (isinstance(bo_raw, float) and np.isnan(bo_raw)):
            beneficial_owner_id = None
        else:
            s = str(bo_raw).strip()
            beneficial_owner_id = None if s.lower() in ("nan", "none", "") else s

        profiles[trader_id] = TraderProfile(
            trader_id=trader_id,
            account_type=str(row["account_type"]).strip(),
            market_maker_registered=market_maker_registered,
            beneficial_owner_id=beneficial_owner_id,
        )

    return profiles


def load_related_accounts() -> dict[str, str]:
    """
    Load data/related_accounts.csv and return a mapping of trader_id →
    beneficial_owner_id (only rows where beneficial_owner_id is non-null).

    Returns
    -------
    dict[str, str]
        Maps trader_id → beneficial_owner_id.
    """
    path = DATA_DIR / "related_accounts.csv"
    df = pd.read_csv(path, dtype=str)

    result: dict[str, str] = {}
    for _, row in df.iterrows():
        tid = str(row["trader_id"]).strip()
        bo = str(row.get("beneficial_owner_id", "")).strip()
        if bo and bo.lower() not in ("nan", "none", ""):
            result[tid] = bo

    return result


def events_to_models(df: pd.DataFrame) -> list[TradeEvent]:
    """
    Convert a DataFrame (as returned by load_events) into a list of
    TradeEvent pydantic models.

    NaN / None values in related_order_id are mapped to None.

    Parameters
    ----------
    df : pd.DataFrame
        DataFrame with TradeEvent-compatible columns.

    Returns
    -------
    list[TradeEvent]
    """
    models: list[TradeEvent] = []
    for row in df.itertuples(index=False):
        # Resolve optional fields
        related = getattr(row, "related_order_id", None)
        if related is not None and isinstance(related, float) and np.isnan(related):
            related = None
        elif related is not None and str(related).strip().lower() in ("nan", "none", ""):
            related = None

        # Timestamp: may be pandas Timestamp or datetime string
        ts = row.timestamp
        if isinstance(ts, str):
            ts = datetime.fromisoformat(ts)
        elif hasattr(ts, "to_pydatetime"):
            ts = ts.to_pydatetime()

        models.append(
            TradeEvent(
                event_id=str(row.event_id),
                event_type=str(row.event_type),
                timestamp=ts,
                trader_id=str(row.trader_id),
                account_id=str(row.account_id),
                instrument=str(row.instrument),
                side=str(row.side),
                order_id=str(row.order_id),
                quantity=float(row.quantity),
                price=float(row.price),
                order_type=str(row.order_type),
                session=str(row.session),
                related_order_id=related,
                is_aggressive=bool(row.is_aggressive),
            )
        )

    return models


# ---------------------------------------------------------------------------
# Convenience: load both standard datasets at once
# ---------------------------------------------------------------------------

def load_baseline() -> pd.DataFrame:
    """Shorthand: load data/trades_baseline.csv."""
    return load_events(DATA_DIR / "trades_baseline.csv")


def load_scenario() -> pd.DataFrame:
    """Shorthand: load data/trades_scenario.csv."""
    return load_events(DATA_DIR / "trades_scenario.csv")
