# Market Manipulation Detection Patterns

## Pattern: Layering

**Signature**: Large number of fake orders placed and quickly cancelled.

**Detection Characteristics**:
- Order-to-trade ratio: 5:1 to 20:1 or higher
- Order placement duration: Seconds to minutes
- Orders placed at slightly worse price (to avoid execution)
- Orders cancelled upon real trade activity
- Pattern repeats multiple times per session

**Statistical Markers**:
- High order cancellation rate (>60%)
- Order lifetime < 30 seconds
- Price-weighted order book imbalance
- Temporal clustering of cancellations

**Code Location**: `src/detection/layering.py`

## Pattern: Wash Trading

**Signature**: Simultaneous buy-sell of same security between parties with coordinated timing.

**Detection Characteristics**:
- Buyer and seller are related (same entity, shared account, family)
- Price near or at mid-quote (not profit-seeking)
- Size matches exactly or near-exactly
- Timing within seconds (often same millisecond block)
- No material change in beneficial ownership
- Repeated with multiple securities

**Statistical Markers**:
- Zero profit on matched pair
- Zero economic risk transfer
- Suspicious party relationships
- High frequency pattern (multiple pairs per day)

**Code Location**: `src/detection/wash_trading.py`

## Pattern: Momentum Ignition

**Signature**: Aggressive trades designed to trigger algorithmic trading responses.

**Detection Characteristics**:
- Large, market-moving order(s)
- Executed at market rather than limit
- Followed by reverse position within seconds
- Profits from algorithmic cascade
- Target is momentum algorithms or stop losses
- Trader acquires initial loss position

**Statistical Markers**:
- Sudden large market order
- Market impact exceeds order size
- Price moves in predicted direction
- Reverse trade captures impact benefit
- Reversal happens within 60 seconds

**Code Location**: `src/detection/momentum_ignition.py`

## Pattern: Price Ramping

**Signature**: Series of trades executed to artificially inflate or deflate security price.

**Detection Characteristics**:
- Sequential trades progressively higher (or lower)
- Each trade slightly above (below) previous
- Trades increase price step-by-step
- Often correlated with order placement
- Coordinated with other traders
- Exit at inflated price

**Statistical Markers**:
- Persistent price trend
- Trade sequence shows coordination
- Limited real market participation
- Price reversal after position exit
- Abnormal concentration in time

**Code Location**: `src/detection/price_ramping.py`

## Pattern: Marking the Close

**Signature**: Trades at market close to artificially set closing price.

**Detection Characteristics**:
- Trade activity concentrated in final minute
- Trades at prices away from session trading range
- High volume near close relative to session average
- Coordinated selling (or buying) at close
- Potential to affect fund valuations/indices
- Price reverses sharply next session

**Statistical Markers**:
- Abnormal volume in final 60 seconds
- Close price > typical closing bid-ask
- Volume concentration at specific time
- Next-day reversal
- Associated with index rebalancing dates

**Code Location**: `src/detection/marking_close.py`

## Cross-Pattern Signals

**Trader Characterization**:
- Layering + Momentum = Sophisticated manipulation
- Layering + Wash = Liquidity spoofing
- Price Ramp + Marking Close = Coordinated abuse
- Multiple patterns from same trader = Elevated risk

**Temporal Signals**:
- Patterns clustered in specific hours (market open/close)
- Patterns before news releases (information leakage)
- Coordinated patterns across multiple securities
