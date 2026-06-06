# Detecting and Triaging Spoofing using Temporal Convolutional Networks

**Title:** Detecting and Triaging Spoofing using Temporal Convolutional Networks
**Authors:** Kaushalya Kularatnam and Tania Stathaki
**Publication Date:** March 20, 2024
**arXiv ID:** 2403.13429
**Venue:** AAAI 2024 Workshop on AI in Finance for Social Impact
**PDF:** https://arxiv.org/pdf/2403.13429
**Abstract:** https://arxiv.org/abs/2403.13429

## Abstract

The authors present a three-stage framework for identifying market manipulation in algorithmic
trading environments:

1. **Initial Labeling:** A labelling algorithm creates training data for a weakly supervised
   model to identify "potentially suspicious sequences of order book states"

2. **Expert Review:** Expert assessment examines flagged order book states, or alternatively
   a more complex algorithm processes them if experts are unavailable

3. **Similarity Ranking:** New order book representations are compared against expert-labeled
   representations to rank weak learner results

## Methodology

The framework leverages **temporal convolutional networks (TCN)** to learn order book
representations. TCNs are used because they capture temporal dependencies in order book
state sequences more efficiently than RNNs for this type of sequential pattern detection.

The approach aims to adapt flexibly to various market manipulation detection scenarios while
managing challenges posed by large datasets and evolving trading strategies.

## Relevance to Our Project

- Confirms that spoofing detection from order book data is a well-studied, solvable problem
- The TCN/ML approach is complementary to our rule-based approach — they detect the same
  patterns but via different methods
- The "triage" step (expert or algorithm review of flagged sequences) directly parallels our
  Claude AI triage step
- Subject areas: Trading and Market Microstructure, Machine Learning, Computational Finance
