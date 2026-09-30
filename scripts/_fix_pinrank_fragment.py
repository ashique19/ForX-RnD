from pathlib import Path
p = Path(r"C:\AI\forex-lab\desk\src\App.tsx")
t = p.read_text(encoding="utf-8")
old = """        {mode === \"learnings\" ? (
          <PinRankPanel />
          <LearningsPanel />
        ) : mode === \"calendar\" ? ("""
new = """        {mode === \"learnings\" ? (
          <>
            <PinRankPanel />
            <LearningsPanel />
          </>
        ) : mode === \"calendar\" ? ("""
if old not in t:
    raise SystemExit("block missing")
p.write_text(t.replace(old, new, 1), encoding="utf-8")
print("fixed")
