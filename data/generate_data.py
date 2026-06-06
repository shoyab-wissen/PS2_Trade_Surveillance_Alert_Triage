"""
generate_data.py — Synthetic trade data generator for trade surveillance hackathon.

Generates:
  data/trades_baseline.csv   — 30 days of clean, realistic trading data
  data/trades_scenario.csv   — 1 demo day (2026-02-17) with 4 injected anomalies
  data/trader_profiles.csv   — 50 trader profiles
  data/related_accounts.csv  — trader → beneficial_owner (non-null rows only)
"""

import numpy as np
import pandas as pd
from datetime import datetime, timedelta, date
from pathlib import Path
import warnings
warnings.filterwarnings("ignore")

# ---------------------------------------------------------------------------
# Reproducibility
# ---------------------------------------------------------------------------
RNG = np.random.default_rng(42)
np.random.seed(42)

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
DATA_DIR = Path(__file__).parent
DATA_DIR.mkdir(parents=True, exist_ok=True)

# ---------------------------------------------------------------------------
# Instruments & reference prices
# ---------------------------------------------------------------------------
TICKERS = ["AAPL", "TSLA", "MSFT", "NVDA", "AMZN"]
HARDCODED_PRICES = {
    "AAPL": 175.0,
    "TSLA": 250.0,
    "MSFT": 400.0,
    "NVDA": 800.0,
    "AMZN": 185.0,
}

def fetch_yfinance_prices() -> dict[str, float]:
    """Try to fetch last closing price from yfinance; fall back gracefully."""
    try:
        import yfinance as yf
        prices = {}
        for ticker in TICKERS:
            data = yf.download(ticker, period="5d", interval="1d", progress=False, auto_adjust=True)
            if data is not None and not data.empty and "Close" in data.columns:
                prices[ticker] = float(data["Close"].iloc[-1])
        if prices:
            print(f"[yfinance] fetched prices: {prices}")
            return prices
    except Exception as exc:
        print(f"[yfinance] failed ({exc}), using hardcoded prices.")
    return HARDCODED_PRICES.copy()


def simulate_gbm_path(s0: float, n_steps: int, dt: float = 1 / (252 * 390),
                      mu: float = 0.0, sigma: float = 0.18) -> np.ndarray:
    """Geometric Brownian Motion price path of length n_steps+1."""
    shocks = RNG.standard_normal(n_steps)
    log_returns = (mu - 0.5 * sigma ** 2) * dt + sigma * np.sqrt(dt) * shocks
    path = np.empty(n_steps + 1)
    path[0] = s0
    for i in range(n_steps):
        path[i + 1] = path[i] * np.exp(log_returns[i])
    return path

# ---------------------------------------------------------------------------
# Trader profile definitions
# ---------------------------------------------------------------------------
ANOMALY_TRADERS = {"T-4821", "T-6610", "T-8802", "T-5599"}
WASH_TRADERS = {"T-9033", "T-9034"}

def build_trader_profiles() -> pd.DataFrame:
    rows = []

    # Market makers T-0001 to T-0010
    for i in range(1, 11):
        tid = f"T-{i:04d}"
        rows.append({
            "trader_id": tid,
            "account_type": "market_maker",
            "market_maker_registered": True,
            "beneficial_owner_id": None,
            "cancel_rate": RNG.uniform(0.65, 0.80),
            "order_size_min": 500,
            "order_size_max": 5000,
        })

    # Prop desk T-0011 to T-0030
    for i in range(11, 31):
        tid = f"T-{i:04d}"
        rows.append({
            "trader_id": tid,
            "account_type": "prop_desk",
            "market_maker_registered": False,
            "beneficial_owner_id": None,
            "cancel_rate": RNG.uniform(0.20, 0.35),
            "order_size_min": 1000,
            "order_size_max": 50000,
        })

    # Retail T-0031 to T-0050
    for i in range(31, 51):
        tid = f"T-{i:04d}"
        rows.append({
            "trader_id": tid,
            "account_type": "retail",
            "market_maker_registered": False,
            "beneficial_owner_id": None,
            "cancel_rate": RNG.uniform(0.05, 0.15),
            "order_size_min": 100,
            "order_size_max": 2000,
        })

    # Wash-trade pair — both share beneficial owner
    for tid in ["T-9033", "T-9034"]:
        rows.append({
            "trader_id": tid,
            "account_type": "prop_desk",
            "market_maker_registered": False,
            "beneficial_owner_id": "BO-5001",
            "cancel_rate": 0.25,
            "order_size_min": 1000,
            "order_size_max": 50000,
        })

    # Anomaly traders
    for tid in ["T-4821", "T-6610", "T-8802"]:
        rows.append({
            "trader_id": tid,
            "account_type": "prop_desk",
            "market_maker_registered": False,
            "beneficial_owner_id": None,
            "cancel_rate": 0.25,
            "order_size_min": 1000,
            "order_size_max": 50000,
        })

    # Marking-close anomaly trader
    rows.append({
        "trader_id": "T-5599",
        "account_type": "prop_desk",
        "market_maker_registered": False,
        "beneficial_owner_id": None,
        "cancel_rate": 0.15,
        "order_size_min": 5000,
        "order_size_max": 20000,
    })

    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------
# Session helper
# ---------------------------------------------------------------------------
def assign_session(ts: datetime) -> str:
    t = ts.time()
    pre_start = datetime.strptime("09:00", "%H:%M").time()
    reg_start = datetime.strptime("09:30", "%H:%M").time()
    close_start = datetime.strptime("15:55", "%H:%M").time()
    close_end = datetime.strptime("16:00", "%H:%M").time()
    if pre_start <= t < reg_start:
        return "PRE"
    elif reg_start <= t < close_start:
        return "REGULAR"
    elif close_start <= t <= close_end:
        return "CLOSE"
    return "REGULAR"


# ---------------------------------------------------------------------------
# U-shaped intraday volume weight helper
# ---------------------------------------------------------------------------
def intraday_weight(minute_of_day: int) -> float:
    """Returns a weight in [0,1] reflecting U-shaped volume. Minute 0 = 09:30."""
    total_minutes = 390  # 09:30 to 16:00
    x = minute_of_day / total_minutes  # [0, 1]
    # Cosine-based U shape: high at 0 and 1, low at 0.5
    w = 0.3 + 0.7 * (1 - np.sin(np.pi * x)) ** 2
    return float(w)


# ---------------------------------------------------------------------------
# ID generators (counters wrapped in a mutable object)
# ---------------------------------------------------------------------------
class Counter:
    def __init__(self, start: int = 0):
        self.value = start

    def next(self) -> int:
        self.value += 1
        return self.value


EVT_COUNTER = Counter()
ORD_COUNTER: dict[str, Counter] = {}  # per trader


def new_event_id(day: date) -> str:
    n = EVT_COUNTER.next()
    return f"EVT-{day.strftime('%Y%m%d')}-{n:07d}"


def new_order_id(trader_id: str) -> str:
    if trader_id not in ORD_COUNTER:
        ORD_COUNTER[trader_id] = Counter()
    n = ORD_COUNTER[trader_id].next()
    return f"ORD-{trader_id}-{n:06d}"


def account_id_for(trader_id: str) -> str:
    if trader_id == "T-9033":
        return "A-9033"
    if trader_id == "T-9034":
        return "A-9034"
    return trader_id


# ---------------------------------------------------------------------------
# Core event-builder
# ---------------------------------------------------------------------------
def make_place_event(day: date, trader_id: str, instrument: str,
                     side: str, qty: float, price: float,
                     order_type: str, ts: datetime,
                     is_aggressive: bool = False) -> dict:
    oid = new_order_id(trader_id)
    return {
        "event_id": new_event_id(day),
        "event_type": "ORDER_PLACE",
        "timestamp": ts.isoformat(),
        "trader_id": trader_id,
        "account_id": account_id_for(trader_id),
        "instrument": instrument,
        "side": side,
        "order_id": oid,
        "quantity": qty,
        "price": price,
        "order_type": order_type,
        "session": assign_session(ts),
        "related_order_id": None,
        "is_aggressive": is_aggressive,
    }


def make_cancel_event(day: date, place_event: dict, ts: datetime) -> dict:
    return {
        "event_id": new_event_id(day),
        "event_type": "ORDER_CANCEL",
        "timestamp": ts.isoformat(),
        "trader_id": place_event["trader_id"],
        "account_id": place_event["account_id"],
        "instrument": place_event["instrument"],
        "side": place_event["side"],
        "order_id": place_event["order_id"],
        "quantity": place_event["quantity"],
        "price": place_event["price"],
        "order_type": place_event["order_type"],
        "session": assign_session(ts),
        "related_order_id": place_event["event_id"],
        "is_aggressive": place_event["is_aggressive"],
    }


def make_execute_event(day: date, place_event: dict, ts: datetime,
                       exec_price: float | None = None) -> dict:
    price = exec_price if exec_price is not None else place_event["price"]
    return {
        "event_id": new_event_id(day),
        "event_type": "TRADE_EXECUTE",
        "timestamp": ts.isoformat(),
        "trader_id": place_event["trader_id"],
        "account_id": place_event["account_id"],
        "instrument": place_event["instrument"],
        "side": place_event["side"],
        "order_id": place_event["order_id"],
        "quantity": place_event["quantity"],
        "price": price,
        "order_type": place_event["order_type"],
        "session": assign_session(ts),
        "related_order_id": place_event["event_id"],
        "is_aggressive": place_event["is_aggressive"],
    }


# ---------------------------------------------------------------------------
# Baseline day generator
# ---------------------------------------------------------------------------
NORMAL_TRADER_IDS: list[str] = []  # populated after profile build


def generate_baseline_day(trade_date: date, profiles_df: pd.DataFrame,
                           ref_prices: dict[str, float]) -> list[dict]:
    """Generate one day of normal baseline trading events."""
    events: list[dict] = []
    normal_traders = profiles_df[
        ~profiles_df["trader_id"].isin(ANOMALY_TRADERS | WASH_TRADERS)
    ]

    # Select a random subset of traders that are active today (80% chance per trader)
    active_mask = RNG.random(len(normal_traders)) < 0.80
    active_traders = normal_traders[active_mask]

    # Each stock has its own GBM path for the day
    open_time = datetime(trade_date.year, trade_date.month, trade_date.day, 9, 30)
    total_minutes = 390  # 09:30 to 16:00

    stock_paths: dict[str, np.ndarray] = {}
    for ticker in TICKERS:
        stock_paths[ticker] = simulate_gbm_path(ref_prices[ticker], total_minutes)

    # Iterate minute by minute
    for minute_idx in range(total_minutes):
        ts_minute = open_time + timedelta(minutes=minute_idx)
        weight = intraday_weight(minute_idx)

        for _, trow in active_traders.iterrows():
            tid = trow["trader_id"]
            cancel_rate = trow["cancel_rate"]
            qty_min = int(trow["order_size_min"])
            qty_max = int(trow["order_size_max"])

            # Decide how many orders this minute (0-3 scaled by weight)
            max_orders = 3
            n_orders = int(RNG.poisson(weight * 1.2))
            n_orders = min(n_orders, max_orders)
            if n_orders == 0:
                continue

            # Pick a random instrument for this trader this minute
            instrument = TICKERS[int(RNG.integers(0, len(TICKERS)))]
            price = float(stock_paths[instrument][minute_idx])

            for _ in range(n_orders):
                side = "BUY" if RNG.random() < 0.5 else "SELL"
                qty = float(int(RNG.integers(qty_min, qty_max + 1) / 100) * 100)
                qty = max(qty, 100.0)

                # Order type
                is_market = RNG.random() < 0.10
                order_type = "MARKET" if is_market else "LIMIT"

                # Price: MARKET orders use last price; LIMIT has spread
                if is_market:
                    order_price = price
                    is_aggressive = True
                else:
                    spread_pct = RNG.uniform(-0.002, 0.002)
                    order_price = round(price * (1 + spread_pct), 2)
                    is_aggressive = abs(spread_pct) < 0.0005

                # Seconds offset within the minute
                sec_offset = float(RNG.uniform(0, 59))
                place_ts = ts_minute + timedelta(seconds=sec_offset)

                place_evt = make_place_event(
                    trade_date, tid, instrument, side, qty,
                    order_price, order_type, place_ts, is_aggressive
                )
                events.append(place_evt)

                # Cancel?
                if RNG.random() < cancel_rate:
                    cancel_delay = float(RNG.uniform(0.5, 45))
                    cancel_ts = place_ts + timedelta(seconds=cancel_delay)
                    events.append(make_cancel_event(trade_date, place_evt, cancel_ts))
                else:
                    # Execute?
                    if RNG.random() < 0.60:
                        exec_delay = float(RNG.uniform(1, 120))
                        exec_ts = place_ts + timedelta(seconds=exec_delay)
                        # Slight exec price slippage
                        slippage = RNG.uniform(-0.001, 0.001)
                        exec_price = round(order_price * (1 + slippage), 2)
                        events.append(make_execute_event(
                            trade_date, place_evt, exec_ts, exec_price
                        ))

    return events


# ---------------------------------------------------------------------------
# Anomaly injectors
# ---------------------------------------------------------------------------

def inject_layering(trade_date: date, ref_prices: dict[str, float]) -> list[dict]:
    """Anomaly 1 — Layering by T-4821 in AAPL, 09:44-09:47."""
    tid = "T-4821"
    instrument = "AAPL"
    base_price = ref_prices[instrument]
    events: list[dict] = []

    base_ts = datetime(trade_date.year, trade_date.month, trade_date.day, 9, 44, 0)

    # 14 BUY LIMIT orders slightly below market (layering bids)
    place_events = []
    for i in range(14):
        offset_sec = float(RNG.uniform(0, 180))  # spread over ~3 minutes
        ts = base_ts + timedelta(seconds=offset_sec)
        qty = float(int(RNG.integers(40000, 60001) / 1000) * 1000)
        # Prices stacked below market: 0.1%-0.5% below
        discount = RNG.uniform(0.001, 0.005)
        price = round(base_price * (1 - discount), 2)
        pe = make_place_event(trade_date, tid, instrument, "BUY", qty, price, "LIMIT", ts)
        place_events.append(pe)
        events.append(pe)

    # 12 of 14 get cancelled quickly (400-800ms)
    for pe in place_events[:12]:
        place_ts = datetime.fromisoformat(pe["timestamp"])
        delay_ms = float(RNG.uniform(400, 800))
        cancel_ts = place_ts + timedelta(milliseconds=delay_ms)
        events.append(make_cancel_event(trade_date, pe, cancel_ts))

    # 2 SELL TRADE_EXECUTEs at price 0.4% above the buy levels, just after cancels
    cancel_done_ts = base_ts + timedelta(seconds=185)
    for i in range(2):
        sell_ts = cancel_done_ts + timedelta(seconds=float(i * 3 + RNG.uniform(0, 2)))
        sell_price = round(base_price * 1.004, 2)
        qty_sell = float(int(RNG.integers(5000, 15000) / 1000) * 1000)
        sell_place = make_place_event(
            trade_date, tid, instrument, "SELL", qty_sell, sell_price, "LIMIT", sell_ts
        )
        exec_ts = sell_ts + timedelta(seconds=float(RNG.uniform(1, 5)))
        exec_evt = make_execute_event(trade_date, sell_place, exec_ts, sell_price)
        events.append(sell_place)
        events.append(exec_evt)

    return events


def inject_wash_trading(trade_date: date, ref_prices: dict[str, float]) -> list[dict]:
    """Anomaly 2 — Wash trading between T-9033 and T-9034 in MSFT, 11:00-11:30."""
    t_sell = "T-9033"
    t_buy = "T-9034"
    instrument = "MSFT"
    base_price = ref_prices[instrument]
    events: list[dict] = []

    base_ts = datetime(trade_date.year, trade_date.month, trade_date.day, 11, 0, 0)

    qty = 10000.0
    for i in range(8):
        offset_sec = float(i * RNG.uniform(150, 225))  # spread over 30 minutes
        pair_ts = base_ts + timedelta(seconds=offset_sec)

        # Price fluctuates slightly each pair
        price_center = round(base_price * RNG.uniform(0.998, 1.002), 2)

        # T-9033 SELL
        sell_price = price_center
        sell_ts = pair_ts
        sell_pe = make_place_event(
            trade_date, t_sell, instrument, "SELL", qty, sell_price, "LIMIT", sell_ts
        )
        # TRADE_EXECUTE for SELL
        sell_exec_ts = sell_ts + timedelta(seconds=float(RNG.uniform(0.1, 1.0)))
        sell_exec = make_execute_event(trade_date, sell_pe, sell_exec_ts, sell_price)

        # T-9034 BUY at same price ±0.01%
        buy_price = round(sell_price * RNG.uniform(0.9999, 1.0001), 2)
        buy_ts = pair_ts + timedelta(seconds=float(RNG.uniform(0.3, 1.8)))
        buy_pe = make_place_event(
            trade_date, t_buy, instrument, "BUY", qty, buy_price, "LIMIT", buy_ts
        )
        # TRADE_EXECUTE for BUY
        buy_exec_ts = buy_ts + timedelta(seconds=float(RNG.uniform(0.1, 1.0)))
        buy_exec = make_execute_event(trade_date, buy_pe, buy_exec_ts, buy_price)

        events.extend([sell_pe, sell_exec, buy_pe, buy_exec])

    return events


def inject_momentum_ignition(trade_date: date, ref_prices: dict[str, float]) -> list[dict]:
    """Anomaly 3 — Momentum ignition by T-6610 in TSLA, 13:15-13:20 then 13:27-13:28."""
    tid = "T-6610"
    instrument = "TSLA"
    base_price = ref_prices[instrument]
    events: list[dict] = []

    buy_base_ts = datetime(trade_date.year, trade_date.month, trade_date.day, 13, 15, 0)

    # 10 aggressive MARKET BUY orders in 90 seconds
    buy_prices = []
    buy_place_events = []
    for i in range(10):
        offset_sec = float(RNG.uniform(0, 90))
        ts = buy_base_ts + timedelta(seconds=offset_sec)
        qty = float(int(RNG.integers(500, 2001) / 100) * 100)
        # Market orders => is_aggressive=True
        buy_pe = make_place_event(
            trade_date, tid, instrument, "BUY", qty, base_price, "MARKET", ts,
            is_aggressive=True
        )
        exec_ts = ts + timedelta(milliseconds=float(RNG.uniform(50, 300)))
        exec_evt = make_execute_event(trade_date, buy_pe, exec_ts, base_price)
        buy_prices.append(base_price)
        buy_place_events.append(buy_pe)
        events.append(buy_pe)
        events.append(exec_evt)

    # After 7-minute gap (13:22+), 3 SELL TRADE_EXECUTEs at price 0.8% higher
    sell_base_ts = buy_base_ts + timedelta(minutes=7)
    avg_buy_price = float(np.mean(buy_prices))
    sell_price = round(avg_buy_price * 1.008, 2)

    for i in range(3):
        sell_ts = sell_base_ts + timedelta(seconds=float(i * RNG.uniform(10, 25)))
        qty_sell = float(int(RNG.integers(500, 2001) / 100) * 100)
        sell_pe = make_place_event(
            trade_date, tid, instrument, "SELL", qty_sell, sell_price, "LIMIT", sell_ts
        )
        exec_ts = sell_ts + timedelta(seconds=float(RNG.uniform(1, 5)))
        exec_evt = make_execute_event(trade_date, sell_pe, exec_ts, sell_price)
        events.append(sell_pe)
        events.append(exec_evt)

    return events


def inject_price_ramping(trade_date: date, ref_prices: dict[str, float]) -> list[dict]:
    """Anomaly 4 — Price ramping by T-8802 in NVDA, 14:45-14:55."""
    tid = "T-8802"
    instrument = "NVDA"
    base_price = ref_prices[instrument]
    events: list[dict] = []

    ramp_base_ts = datetime(trade_date.year, trade_date.month, trade_date.day, 14, 45, 0)
    total_seconds = 600  # 10 minutes

    # 6 BUY TRADE_EXECUTEs, each 0.1% higher than the previous
    step_offsets = sorted(RNG.uniform(0, total_seconds, 6).tolist())
    current_price = base_price

    for i, offset_sec in enumerate(step_offsets):
        ts = ramp_base_ts + timedelta(seconds=offset_sec)
        qty = float(int(RNG.integers(1000, 5001) / 100) * 100)
        current_price = round(current_price * 1.001, 2)  # 0.1% ramp per step
        buy_pe = make_place_event(
            trade_date, tid, instrument, "BUY", qty, current_price, "LIMIT", ts
        )
        exec_ts = ts + timedelta(seconds=float(RNG.uniform(0.5, 3)))
        exec_evt = make_execute_event(trade_date, buy_pe, exec_ts, current_price)
        events.append(buy_pe)
        events.append(exec_evt)

    return events


def inject_marking_close(trade_date: date, ref_prices: dict[str, float]) -> list[dict]:
    """Anomaly 5 — Marking the Close by T-5599 in AAPL, 15:55-16:00.

    T-5599 dominates >60% of AAPL close-window volume with 8 aggressive BUY
    executions, pushing price +0.55% in the final 5 minutes.  This gives a
    z-score of 10σ vs market-wide close_volume_fraction baseline (~10%).
    MarkingCloseDetector fires; Claude triages as ESCALATE (prop_desk, no
    market-maker registration, no index-fund exemption).
    """
    tid = "T-5599"
    instrument = "AAPL"
    base_price = ref_prices[instrument]
    events: list[dict] = []

    close_start = datetime(
        trade_date.year, trade_date.month, trade_date.day, 15, 55, 0
    )
    n_orders = 8
    step_offsets = sorted(RNG.uniform(0, 270, n_orders).tolist())  # 270 s = 4m30s
    current_price = base_price

    for offset_sec in step_offsets:
        ts = close_start + timedelta(seconds=offset_sec)
        qty = float(int(RNG.integers(6000, 16001) / 1000) * 1000)
        current_price = round(current_price * (1 + RNG.uniform(0.0006, 0.0012)), 2)
        buy_pe = make_place_event(
            trade_date, tid, instrument, "BUY", qty, current_price, "MARKET", ts,
            is_aggressive=True,
        )
        exec_ts = ts + timedelta(milliseconds=float(RNG.uniform(50, 300)))
        exec_evt = make_execute_event(trade_date, buy_pe, exec_ts, current_price)
        events.append(buy_pe)
        events.append(exec_evt)

    return events


def inject_market_maker_fp(trade_date: date, ref_prices: dict[str, float]) -> list[dict]:
    """FP Scenario — Market maker T-0003 spikes cancel rate during a TSLA volatility
    burst (10:28-10:30).  Cancel ratio of 87.5% with 300-600 ms TTC crosses the
    layering thresholds but T-0003 is market_maker_registered=True.  Claude should
    DISMISS this as legitimate quote management during a volatility event.
    """
    tid = "T-0003"
    instrument = "TSLA"
    base_price = ref_prices[instrument]
    events: list[dict] = []

    base_ts = datetime(trade_date.year, trade_date.month, trade_date.day, 10, 28, 0)
    place_events: list[dict] = []

    # 16 limit orders spread across both sides (quote management pattern)
    for i in range(16):
        offset_sec = float(RNG.uniform(0, 120))
        ts = base_ts + timedelta(seconds=offset_sec)
        side = "BUY" if i < 8 else "SELL"
        qty = float(int(RNG.integers(500, 2001) / 100) * 100)
        spread = RNG.uniform(-0.003, 0.003)
        price = round(base_price * (1 + spread), 2)
        pe = make_place_event(
            trade_date, tid, instrument, side, qty, price, "LIMIT", ts
        )
        place_events.append(pe)
        events.append(pe)

    # Cancel 14 of 16 very quickly (300-600 ms) — market-maker quote withdrawal
    for pe in place_events[:14]:
        place_ts = datetime.fromisoformat(pe["timestamp"])
        delay_ms = float(RNG.uniform(300, 600))
        cancel_ts = place_ts + timedelta(milliseconds=delay_ms)
        events.append(make_cancel_event(trade_date, pe, cancel_ts))

    # Execute remaining 2 (legit fills)
    for pe in place_events[14:]:
        place_ts = datetime.fromisoformat(pe["timestamp"])
        exec_ts = place_ts + timedelta(seconds=float(RNG.uniform(1, 4)))
        events.append(make_execute_event(trade_date, pe, exec_ts))

    return events


# ---------------------------------------------------------------------------
# Main generation logic
# ---------------------------------------------------------------------------

def get_trading_days(start: date, end: date) -> list[date]:
    """Return weekdays between start and end inclusive."""
    days = []
    current = start
    while current <= end:
        if current.weekday() < 5:  # Monday=0, Friday=4
            days.append(current)
        current += timedelta(days=1)
    return days


def main():
    print("=" * 60)
    print("Trade Surveillance — Synthetic Data Generator")
    print("=" * 60)

    # Build trader profiles
    profiles_df = build_trader_profiles()
    print(f"Built {len(profiles_df)} trader profiles.")

    # Fetch/set reference prices
    ref_prices = fetch_yfinance_prices()
    for ticker in TICKERS:
        if ticker not in ref_prices:
            ref_prices[ticker] = HARDCODED_PRICES[ticker]
    print(f"Reference prices: {ref_prices}")

    # -----------------------------------------------------------------------
    # Baseline: 2026-01-02 to 2026-02-14 (30 trading days)
    # -----------------------------------------------------------------------
    baseline_start = date(2026, 1, 2)
    baseline_end = date(2026, 2, 14)
    trading_days = get_trading_days(baseline_start, baseline_end)[:30]
    print(f"\nGenerating baseline data for {len(trading_days)} trading days...")

    all_baseline_events: list[dict] = []
    for idx, trade_date in enumerate(trading_days, 1):
        print(f"  Generated day {idx}/{len(trading_days)}: {trade_date} ...", end="\r")
        day_events = generate_baseline_day(trade_date, profiles_df, ref_prices)
        all_baseline_events.extend(day_events)

    print(f"\n  Total baseline events: {len(all_baseline_events):,}")

    # -----------------------------------------------------------------------
    # Scenario day: 2026-02-17
    # -----------------------------------------------------------------------
    scenario_date = date(2026, 2, 17)
    print(f"\nGenerating scenario day: {scenario_date}")

    # Normal trading for scenario day (copy normal traders)
    normal_day_events = generate_baseline_day(scenario_date, profiles_df, ref_prices)

    # Inject anomalies
    print("  Injecting Anomaly 1 — Layering (T-4821, AAPL, 09:44-09:47)...")
    a1_events = inject_layering(scenario_date, ref_prices)

    print("  Injecting Anomaly 2 — Wash Trading (T-9033+T-9034, MSFT, 11:00-11:30)...")
    a2_events = inject_wash_trading(scenario_date, ref_prices)

    print("  Injecting Anomaly 3 — Momentum Ignition (T-6610, TSLA, 13:15-13:20)...")
    a3_events = inject_momentum_ignition(scenario_date, ref_prices)

    print("  Injecting Anomaly 4 — Price Ramping (T-8802, NVDA, 14:45-14:55)...")
    a4_events = inject_price_ramping(scenario_date, ref_prices)

    print("  Injecting Anomaly 5 — Marking the Close (T-5599, AAPL, 15:55-16:00)...")
    a5_events = inject_marking_close(scenario_date, ref_prices)

    print("  Injecting FP Scenario  — Market Maker False Positive (T-0003, TSLA, 10:28-10:30)...")
    fp_events = inject_market_maker_fp(scenario_date, ref_prices)

    all_scenario_events = (
        normal_day_events + a1_events + a2_events + a3_events
        + a4_events + a5_events + fp_events
    )

    # -----------------------------------------------------------------------
    # Build DataFrames and write CSVs
    # -----------------------------------------------------------------------
    COLUMNS = [
        "event_id", "event_type", "timestamp", "trader_id", "account_id",
        "instrument", "side", "order_id", "quantity", "price", "order_type",
        "session", "related_order_id", "is_aggressive",
    ]

    def events_to_df(events: list[dict]) -> pd.DataFrame:
        df = pd.DataFrame(events, columns=COLUMNS)
        # Sort by timestamp
        df["_ts_sort"] = pd.to_datetime(df["timestamp"], format="ISO8601")
        df = df.sort_values("_ts_sort").drop(columns=["_ts_sort"])
        df = df.reset_index(drop=True)
        return df

    baseline_df = events_to_df(all_baseline_events)
    scenario_df = events_to_df(all_scenario_events)

    # Trader profiles CSV
    profiles_out = profiles_df[[
        "trader_id", "account_type", "market_maker_registered", "beneficial_owner_id"
    ]].copy()

    # Related accounts CSV
    related_out = profiles_df[profiles_df["beneficial_owner_id"].notna()][
        ["trader_id", "beneficial_owner_id"]
    ].copy()

    # Write files
    baseline_path = DATA_DIR / "trades_baseline.csv"
    scenario_path = DATA_DIR / "trades_scenario.csv"
    profiles_path = DATA_DIR / "trader_profiles.csv"
    related_path = DATA_DIR / "related_accounts.csv"

    baseline_df.to_csv(baseline_path, index=False)
    scenario_df.to_csv(scenario_path, index=False)
    profiles_out.to_csv(profiles_path, index=False)
    related_out.to_csv(related_path, index=False)

    # -----------------------------------------------------------------------
    # Summary
    # -----------------------------------------------------------------------
    print("\n" + "=" * 60)
    print("GENERATION COMPLETE")
    print("=" * 60)
    print(f"  trades_baseline.csv  : {len(baseline_df):>10,} events ({len(trading_days)} days)")
    print(f"  trades_scenario.csv  : {len(scenario_df):>10,} events (1 day + 5 anomalies + 1 FP)")
    print(f"  trader_profiles.csv  : {len(profiles_out):>10,} traders")
    print(f"  related_accounts.csv : {len(related_out):>10,} rows")
    print()
    print("Anomaly event counts in scenario day:")
    print(f"  Layering (T-4821, AAPL)              : {len(a1_events):>5} events")
    print(f"  Wash Trading (T-9033+T-9034, MSFT)   : {len(a2_events):>5} events")
    print(f"  Momentum Ignition (T-6610, TSLA)     : {len(a3_events):>5} events")
    print(f"  Price Ramping (T-8802, NVDA)         : {len(a4_events):>5} events")
    print(f"  Marking the Close (T-5599, AAPL)     : {len(a5_events):>5} events")
    print(f"  Market Maker FP (T-0003, TSLA)       : {len(fp_events):>5} events")
    print(f"  Normal trading events (scenario day) : {len(normal_day_events):>5} events")
    print()
    print(f"Output directory: {DATA_DIR.resolve()}")


if __name__ == "__main__":
    main()
