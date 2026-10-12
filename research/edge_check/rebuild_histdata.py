"""Stage a clean HistData (bid, M1) rebuild into data/history_rebuild/. Never writes data/history.
HistData ASCII stamps are New York local time (EST/EDT); verified vs clean Dukascopy EURUSD 2018 (Jan +5h, Jul +4h, |dC| 0.1 pip). Converted to UTC here.
Usage: python research/edge_check/rebuild_histdata.py PAIR [PAIR...] [--from 2015]"""
import sys, re, time, pathlib, datetime as dt
import httpx, pandas as pd
from forex_lab.history import _zip_or_text, parse_histdata_text, resample_bars, _UA
RAW = pathlib.Path("data/history_rebuild/raw"); RAW.mkdir(parents=True, exist_ok=True)
BASE = "https://www.histdata.com/download-free-forex-historical-data/?/ascii/1-minute-bar-quotes/{p}/{y}"
EST_TO_UTC = pd.Timedelta(hours=5)

def fetch(c, pair, y, m=None):
    f = RAW / f"{pair}_{y}{'' if m is None else f'{m:02d}'}.zip"
    if f.exists() and f.stat().st_size > 1000: return f.read_bytes()
    page = BASE.format(p=pair.lower(), y=y) + ("" if m is None else f"/{m}")
    for a in range(3):
        try:
            r = c.get(page); t = re.search(r'id=["\']tk["\'][^>]*value=["\']([^"\']+)', r.text)
            if not t: return None
            p = c.post("https://www.histdata.com/get.php", headers={"Referer": page}, data={
                "tk": t.group(1), "date": str(y), "datemonth": f"{y}" if m is None else f"{y}{m:02d}",
                "platform": "ASCII", "timeframe": "M1", "fxpair": pair.upper()})
            if p.status_code == 200 and p.content[:2] == b"PK":
                f.write_bytes(p.content); return p.content
            return None
        except Exception as e:
            print("  retry", pair, y, m, e); time.sleep(3 * (a + 1))
    return None

def stage(pair, y0=2015):
    now = dt.datetime.utcnow(); parts = []
    with httpx.Client(timeout=60, follow_redirects=True, headers={"User-Agent": _UA}) as c:
        for y in range(y0, now.year + 1):
            units = [None] if y < now.year else list(range(1, now.month + 1))
            for m in units:
                b = fetch(c, pair, y, m)
                if b: parts.append(parse_histdata_text(_zip_or_text(b)))
                print(f"  {pair} {y}{'' if m is None else '-%02d' % m}: {'ok' if b else 'none'}", flush=True)
    m1 = pd.concat(parts); m1 = m1[~m1.index.duplicated(keep="last")].sort_index()
    m1.index = m1.index.tz_localize("America/New_York", ambiguous="NaT", nonexistent="NaT").tz_convert("UTC").tz_localize(None)
    m1 = m1[m1.index.notna()]
    h = resample_bars(m1, "1h"); h.index.name = "Datetime"
    out = pathlib.Path(f"data/history_rebuild/{pair}_1h.csv"); h.to_csv(out)
    print(f"{pair}: {len(h)} bars {h.index.min()} .. {h.index.max()} -> {out}", flush=True)

if __name__ == "__main__":
    args = sys.argv[1:]; y0 = 2015
    if "--from" in args: i = args.index("--from"); y0 = int(args[i + 1]); del args[i:i + 2]
    for p in args: stage(p.upper(), y0)
