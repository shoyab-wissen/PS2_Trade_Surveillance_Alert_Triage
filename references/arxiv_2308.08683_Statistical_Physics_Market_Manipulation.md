# Detecting Financial Market Manipulation with Statistical Physics Tools

**Title:** Detecting Financial Market Manipulation with Statistical Physics Tools
**Authors:** Haochen Li, Maria Polukarova, Carmine Ventre
**Publication Date:** August 16, 2023
**arXiv ID:** 2308.08683
**PDF:** https://arxiv.org/pdf/2308.08683
**Abstract:** https://arxiv.org/abs/2308.08683

## Abstract

The researchers developed a framework inspired by statistical physics to analyse financial
markets. They model the order book dynamics as a "motion of particles" and introduced a
**momentum metric** to evaluate market conditions.

## Key Methodology

- Model order book as a physical system where orders are "particles" with momentum
- Momentum metric captures directional pressure and velocity of order flow
- Applied to LUNA cryptocurrency flash crash as a test case
- Compared against conventional Z-score anomaly detection

## Results

Successfully identified two manipulation tactics:
- **Spoofing**: Placing fake orders to deceive other traders
- **Layering**: Creating multiple orders at different price levels

The method demonstrated **superior performance vs Z-score** in identifying market manipulation
across both LUNA and Bitcoin markets, uncovering "widespread instances of spoofing and layering."

## Relevance to Our Project

- Validates z-score as a baseline detection method (which we use)
- Shows that z-score alone is not enough — context and sequence matter
- The "momentum metric" concept informs our Momentum Ignition detector: sustained
  directional order flow pressure is itself a signal
- Subject areas: Trading and Market Microstructure (q-fin.TR), Computational Finance (q-fin.CP)
