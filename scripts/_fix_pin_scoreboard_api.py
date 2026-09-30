from pathlib import Path
import json
import sys

data = {
  "as_of_dhaka": "2026-10-01 05:16 Asia/Dhaka",
  "disclaimer": "Research after-cost pins only. Promote=null everywhere. Not a live deploy rank. Active live pair remains EURUSD min_confidence=0.60.",
  "pin_ref_eurusd": "2cee7e7163134266ad8c2b39234442d8",
  "rows": [
    {"rank": 1, "pair": "USDJPY", "job_id": "ef6fe062c6f84cccaf8c9d55facddeeb", "pf": 3.037, "wr": 0.728, "n": 5694, "dd": -0.0330, "total_return": 613.61, "sma_pf": 0.851, "qa": "TRUST", "promote": None},
    {"rank": 2, "pair": "AUDUSD", "job_id": "6a5653c8d5fc4f43aa896ca8c26a6150", "pf": 2.980, "wr": 0.724, "n": 5977, "dd": -0.0318, "total_return": 4316.04, "sma_pf": 0.786, "qa": "TRUST", "promote": None},
    {"rank": 3, "pair": "NZDUSD", "job_id": "83c2715df9104a099056b96812141c42", "pf": 2.851, "wr": 0.709, "n": 5695, "dd": -0.0294, "total_return": 3047.89, "sma_pf": 0.749, "qa": "TRUST", "promote": None},
    {"rank": 4, "pair": "USDCAD", "job_id": "a9b26129d9a44f86b2f89646d4698ff3", "pf": 2.735, "wr": 0.705, "n": 6164, "dd": -0.0246, "total_return": 238.13, "sma_pf": 0.798, "qa": "TRUST", "promote": None},
    {"rank": 5, "pair": "GBPUSD", "job_id": "d6a6f7a60036404aabb1e57dd1da8725", "pf": 2.601, "wr": 0.701, "n": 6149, "dd": -0.0261, "total_return": 604.22, "sma_pf": 0.822, "qa": "TRUST", "promote": None},
    {"rank": 6, "pair": "EURUSD", "job_id": "2cee7e7163134266ad8c2b39234442d8", "pf": 1.989, "wr": 0.648, "n": 3791, "dd": -0.1000, "total_return": 11.17, "sma_pf": 0.883, "qa": "ACTIVE", "promote": None, "note": "Live Active; tip refreshed; pin stands"},
  ],
  "freezes": {
    "measured_intel": "3/3 FAIL CLOSED",
    "new_track_purge_size": "3/3 FAIL CLOSED",
    "meta_label": "1/3 FAIL (quiet_weak d5908db6) — enabled=false",
  },
  "source_note": "_PIN_SCOREBOARD_20261001.txt",
}
Path(r"C:\AI\forex-lab\data\pin_scoreboard.json").write_text(json.dumps(data, indent=2), encoding="utf-8")
print("json ok")

helper = '''
def pin_scoreboard_payload(cfg: dict | None = None) -> dict:
    """Research multi-pair pin rank table. Promote always null."""
    from forex_lab.paths import project_root

    _ = cfg  # reserved for future workspace scoping
    path = project_root() / "data" / "pin_scoreboard.json"
    if not path.exists():
        return {"as_of_dhaka": None, "disclaimer": "pin scoreboard missing", "rows": [], "freezes": {}}
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except Exception as exc:
        return {"as_of_dhaka": None, "disclaimer": f"pin scoreboard unreadable: {exc}", "rows": [], "freezes": {}}
'''

dd = Path(r"C:\AI\forex-lab\api\deskdata.py")
text = dd.read_text(encoding="utf-8")
# strip any prior helper
marker = "\ndef pin_scoreboard_payload"
idx = text.find(marker)
if idx < 0:
    idx = text.find("def pin_scoreboard_payload")
    if idx > 0 and text[idx-1] == '\n':
        idx = idx - 1
if idx >= 0:
    text = text[:idx].rstrip() + "\n"
if "import json" not in text.splitlines()[:40]:
    text = "import json\n" + text
dd.write_text(text.rstrip() + "\n\n" + helper + "\n", encoding="utf-8")
print("helper rewritten")

sys.path.insert(0, r"C:\AI\forex-lab")
from importlib import reload
import api.deskdata as deskdata
reload(deskdata)
payload = deskdata.pin_scoreboard_payload()
print("rows", len(payload.get("rows") or []), payload.get("rows")[0]["pair"])
