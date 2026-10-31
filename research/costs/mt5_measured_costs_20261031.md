# MT5 measured cost table (research only), asof 2026-10-31

Round trip, pips. mt5 = spread + 2 x adverse slippage; mt5_measured = max(mt5_p90, duka_p90).

| Pair | spread n | spread p50 | spread p90 | slip n | slip mean | slip p90 | mt5 p50 | mt5 p90 | duka p50 | duka p90 | mt5_measured | binding |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|
| AUDUSD | 1766 | 1.0 | 1.1 | 60 (pair) | 0.01 | 0.0 | 1.02 | 1.1 | 1.8 | 2.6 | 2.6 | duka_p90 |
| EURGBP | 1766 | 1.1 | 1.3 | 43 (pair) | 0.014 | 0.08 | 1.1279 | 1.46 | 1.4 | 2.8 | 2.8 | duka_p90 |
| EURJPY | 1766 | 1.7 | 2.1 | 47 (pair) | 0.0468 | 0.14 | 1.7936 | 2.38 | 1.8 | 4.0 | 4.0 | duka_p90 |
| EURUSD | 1766 | 0.9 | 1.0 | 48 (pair) | 0.0104 | 0.0 | 0.9208 | 1.0 | 0.8 | 1.4 | 1.4 | duka_p90 |
| GBPJPY | 1766 | 1.9 | 2.4 | 43 (pair) | 0.0884 | 0.2 | 2.0767 | 2.8 | 3.4 | 7.6 | 7.6 | duka_p90 |
| GBPUSD | 1766 | 1.3 | 1.5 | 56 (pair) | 0.0125 | -0.0 | 1.325 | 1.5 | 1.4 | 2.8 | 2.8 | duka_p90 |
| NZDUSD | 1766 | 1.5 | 1.6 | 54 (pair) | 0.013 | -0.0 | 1.5259 | 1.6 | 2.0 | 3.2 | 3.2 | duka_p90 |
| USDCAD | 1766 | 1.5 | 1.6 | 55 (pair) | 0.0255 | 0.1 | 1.5509 | 1.8 | 2.4 | 3.6 | 3.6 | duka_p90 |
| USDCHF | 1766 | 1.4 | 1.6 | 57 (pair) | 0.0105 | 0.0 | 1.4211 | 1.6 | 1.6 | 3.0 | 3.0 | duka_p90 |
| USDJPY | 1766 | 1.2 | 1.4 | 60 (pair) | 0.0367 | 0.1 | 1.2733 | 1.6 | 1.0 | 2.4 | 2.4 | duka_p90 |

Pooled slippage: {"n": 523, "adverse_mean": 0.0254302103250478, "adverse_p90": 0.1, "signed_mean": 0.008604206500956025, "signed_median": 0.0}
