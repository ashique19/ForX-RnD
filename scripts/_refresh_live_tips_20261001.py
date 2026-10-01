from forex_lab.config_loader import load_config
from forex_lab.data import fetch_ohlcv
cfg=load_config()
pairs=["EURUSD","USDJPY","GBPUSD","AUDUSD","NZDUSD","USDCAD","EURJPY","EURGBP","USDCHF","GBPJPY"]
for p in pairs:
    try:
        df,src=fetch_ohlcv(p, cfg, interval="1h", period="60d")
        print(p, src, len(df), df.index.min(), "->", df.index.max(), flush=True)
    except Exception as e:
        print(p, "ERR", e, flush=True)
