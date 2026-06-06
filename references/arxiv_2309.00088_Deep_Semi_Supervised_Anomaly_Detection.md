# Deep Semi-Supervised Anomaly Detection for Finding Fraud in the Futures Market

**Title:** Deep Semi-Supervised Anomaly Detection for Finding Fraud in the Futures Market
**Author:** Timothy DeLise
**Publication Date:** August 31, 2023
**arXiv ID:** 2309.00088
**PDF:** https://arxiv.org/pdf/2309.00088
**Abstract:** https://arxiv.org/abs/2309.00088

## Abstract

The research addresses automated fraud detection in electronic financial exchanges using machine
learning. The paper evaluates **"Deep SAD"**, a semi-supervised anomaly detection technique,
as an improvement over purely unsupervised approaches.

## Key Methodology

- **Data Source:** Proprietary limit order book data from TMX exchange in Montréal
- **Dataset Size:** 5.2 million data points, each with 20 numerical fields representing the
  top 10 levels of the order book (price and size of orders at each level)
- **Labeled Data:** A small set of verified fraud instances used to guide the model
- **Approach:** Semi-supervised learning — combines limited labeled examples with
  unsupervised anomaly detection
- **Primary Finding:** "Incorporating a small amount of labeled data into an unsupervised
  anomaly detection framework can greatly improve its accuracy"

## Dataset Structure (Relevant for Our Project)

Each data point contains:
- Best bid to 5th bid: price and order volume (10 fields)
- Best ask to 5th ask: price and order volume (10 fields)
- Trading volume
- Bid-ask spread
- ~5,000 labeled anomaly windows (market manipulation or liquidity anomalies)
- Anomaly labels based on regulatory reports

## Relevance to Our Project

- Confirms limit order book data is the right input for fraud detection
- The "small labeled set" approach validates our strategy: inject known anomalies into real
  NASDAQ data, then detect them
- The 5,000 anomaly window count gives us a sense of the base rate of manipulation in
  real exchange data
- Subject areas: Machine Learning (cs.LG), Risk Management (q-fin.RM)
