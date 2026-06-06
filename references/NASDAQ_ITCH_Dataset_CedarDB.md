# NASDAQ Level 3 Order Data – CedarDB Documentation

**Source:** https://cedardb.com/docs/example_datasets/nasdaq/
**Retrieved:** 2026-06-06

## Overview

NASDAQ provides free dumps of real-time orders for some trading days via their ITCH data portal.
The dataset contains real-time trading information from NASDAQ for January 30, 2020.

## Download Instructions

```bash
git clone git@github.com:cedardb/examples.git
cd examples/nasdaq
./prepare.sh
```

Direct data source: https://emi.nasdaq.com/ITCH/Nasdaq%20ITCH/

## File Sizes

- Format: NASDAQ ITCH v5.0 protocol (binary)
- Sample date: January 30, 2020
- Download size: ~5.2 GB (compressed gzip)
- Decompressed: ~13 GB
- CSV format after parsing: ~16 GB
- A Python parser is included (auto-invoked by prepare.sh) to convert binary → CSV

## Database Schema (5 tables)

### stocks
Security reference data: stockId, name, market category, financial status, IPO flag, exchange trading indicators.

### orders
Tracks all order submissions:
- `orderId` (primary key)
- `stockId`, `timestamp`, `side` (BUY/SELL)
- `quantity`, `price` (numeric 10,4 precision)
- `prevOrder` (references superseded/modified orders)

### executions
Records completed trades:
- `timestamp`, `orderId`, `stockId`
- `quantity`, `price`

### cancellations
Logs withdrawn orders:
- `timestamp`, `orderId`, `stockId`, `quantity`

### marketmakers
Market maker activity:
- `timestamp`, `stockId`, `name`, `mode`, `state`

## Scale

- 181,194,793 new orders submitted on Jan 30, 2020
- Most orders are cancelled — only a small fraction execute
- Some sell orders waited 600+ minutes before partial execution
- Largest single trade: Tesla at $647.00 × 14,549 shares (~$9.4M)

## Key Insight for Trade Surveillance

"Most orders are canceled and only a small part of all incoming orders are executed" — this
baseline cancellation behaviour is exactly what our z-score anomaly detection needs to measure
against. A trader with 85% cancel ratio vs a market baseline of 60-70% is much less suspicious
than the same trader vs their own 30-day baseline of 24%.
