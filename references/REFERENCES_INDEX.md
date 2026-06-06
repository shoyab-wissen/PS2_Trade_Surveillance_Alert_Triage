# References Index — Trade Surveillance & Alert Triage Engine

All materials saved in this folder were used for research, planning, and design of the
detection algorithms and Claude triage logic. Each file is saved with full content for verification.

---

## PDFs (Full Documents)

| File | Description | Source |
|------|-------------|--------|
| `TT_Trade_Surveillance_Guide_v1.06.pdf` | Trading Technologies' comprehensive guide to 80+ trade surveillance models — the primary reference for detection pattern taxonomy | https://library.tradingtechnologies.com/downloads/TT_Score_Guide_to_Trade_Surveillance_Models.pdf |
| `Detecting_Financial_Market_Manipulation_Statistical_Physics.pdf` | arXiv:2308.08683 — Modelling order book as particle system; detects spoofing/layering; compares vs z-score baseline | https://arxiv.org/pdf/2308.08683 |
| `Detecting_Triaging_Spoofing_Temporal_Convolutional_Networks.pdf` | arXiv:2403.13429 — TCN-based spoofing detection + triage framework (AAAI 2024) | https://arxiv.org/pdf/2403.13429 |
| `Deep_Semi_Supervised_Anomaly_Detection_Futures_Market.pdf` | arXiv:2309.00088 — Deep SAD for fraud detection in TMX futures; validates limit order book as input | https://arxiv.org/pdf/2309.00088 |

---

## Markdown Articles (Full Content Saved)

| File | Description | Source |
|------|-------------|--------|
| `NASDAQ_ITCH_Dataset_CedarDB.md` | How to download the NASDAQ ITCH free dataset; full schema for orders/executions/cancellations tables | https://cedardb.com/docs/example_datasets/nasdaq/ |
| `eflow_High_Impact_Market_Manipulation_Tactics.md` | Practical surveillance guide: spoofing, wash trading, pump & dump, front running, cross-market manipulation — all with specific red flags and metrics | https://www.eflowglobal.com/insights/blogs/high-impact-market-manipulation-tactics-red-flags-for-modern-surveillance-teams |
| `arxiv_2403.13429_Spoofing_Temporal_Convolutional_Networks.md` | Abstract + methodology summary: TCN-based spoofing detection and triage using order book sequences | https://arxiv.org/abs/2403.13429 |
| `arxiv_2309.00088_Deep_Semi_Supervised_Anomaly_Detection.md` | Abstract + methodology: Deep SAD for futures fraud; dataset has 20-field LOB data, 5k labeled anomaly windows | https://arxiv.org/abs/2309.00088 |
| `arxiv_2308.08683_Statistical_Physics_Market_Manipulation.md` | Abstract + methodology: momentum metric for order book analysis; outperforms z-score on LUNA/BTC manipulation detection | https://arxiv.org/abs/2308.08683 |

---

## URLs That Could Not Be Saved (Access Blocked)

| URL | Reason | Notes |
|-----|--------|-------|
| https://papers.ssrn.com/sol3/papers.cfm?abstract_id=4525036 | HTTP 403 — SSRN requires login | "Detecting Layering and Spoofing in Markets" by Do & Putniņš (2023) |
| https://www.bloomberg.com/professional/insights/risk/seven-common-market-abuse-scenarios-monitored-through-trade-surveillance/ | HTTP 403 — Bloomberg requires subscription | 7 common market abuse scenarios |

---

## How These References Were Used

### TT Guide → Detection Pattern Selection
Used to identify which of the 80+ TT surveillance models are implementable with
order/execution/cancellation data only (no news feed, no cross-venue). Selected:
Layering, Wash Trading, Momentum Ignition, Price Ramping as primary detectors.

### NASDAQ ITCH → Data Strategy
Used to confirm free real order book data exists with the exact fields we need
(orders with side/qty/price, executions, cancellations). Strategy: download real
NASDAQ data as baseline, inject synthetic anomalies on top.

### eflow Article → False Positive Logic
Provided the "legitimate explanations" that Claude uses to assess false positive
probability: market maker quoting, algorithmic quote adjustment, index rebalancing,
internal portfolio transfers.

### arXiv Papers → Methodology Validation
Confirmed that:
- z-score anomaly detection is standard baseline (2308.08683)
- Rule-based detection + expert triage is the established workflow (2403.13429)  
- Limit order book (orders+executions+cancellations) is the correct input (2309.00088)
- Small labeled anomaly sets injected into real data is a valid research approach (2309.00088)
