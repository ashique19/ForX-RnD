from pathlib import Path
ROOT = Path(r"C:\AI\forex-lab")
replay = ROOT / "forex_lab" / "replay.py"
text = replay.read_text(encoding="utf-8")
old_imp = (
    "    from forex_lab.trend_regime import apply_replay_trend_regime\n\n"
    "    apply_replay_session_gate(cfg)\n"
    "    apply_replay_news_blackout(cfg, pair=pair_u, index=frame.index)\n"
    "    apply_replay_vol_regime(cfg)\n"
    "    apply_replay_weekday_gate(cfg)\n"
    "    apply_replay_trend_regime(cfg)"
)
new_imp = (
    "    from forex_lab.trend_regime import apply_replay_trend_regime\n"
    "    from forex_lab.meta_label import apply_replay_meta_label\n\n"
    "    apply_replay_session_gate(cfg)\n"
    "    apply_replay_news_blackout(cfg, pair=pair_u, index=frame.index)\n"
    "    apply_replay_vol_regime(cfg)\n"
    "    apply_replay_weekday_gate(cfg)\n"
    "    apply_replay_trend_regime(cfg)\n"
    "    apply_replay_meta_label(cfg)"
)
assert "apply_replay_meta_label" not in text
assert old_imp in text
text = text.replace(old_imp, new_imp, 1)
print("apply ok")
old_pred = (
    "        from forex_lab.trend_regime import apply_trend_regime_to_pred\n"
    "        pred = apply_news_blackout_to_pred(pred, cfg, pair=pair)\n"
    "        pred = apply_vol_regime_to_pred(pred, cfg)\n"
    "        pred = apply_weekday_gate_to_pred(pred, cfg)\n"
    "        pred = apply_trend_regime_to_pred(pred, cfg)"
)
new_pred = (
    "        from forex_lab.trend_regime import apply_trend_regime_to_pred\n"
    "        from forex_lab.meta_label import apply_meta_label_to_pred\n"
    "        pred = apply_news_blackout_to_pred(pred, cfg, pair=pair)\n"
    "        pred = apply_vol_regime_to_pred(pred, cfg)\n"
    "        pred = apply_weekday_gate_to_pred(pred, cfg)\n"
    "        pred = apply_trend_regime_to_pred(pred, cfg)\n"
    "        pred = apply_meta_label_to_pred(pred, cfg)"
)
assert old_pred in text
text = text.replace(old_pred, new_pred, 1)
print("pred ok")
lines = text.splitlines(True)
out = []
done = False
for line in lines:
    out.append(line)
    if (not done) and "trend_regime_report_line(cfg)" in line:
        out.append('        f"- {__import__(\'forex_lab.meta_label\', fromlist=[\'meta_label_report_line\']).meta_label_report_line(cfg)}",\n')
        done = True
assert done
replay.write_text("".join(out), encoding="utf-8")
print("replay written", replay.stat().st_size)
yaml_path = ROOT / "config" / "default.yaml"
yt = yaml_path.read_text(encoding="utf-8")
assert "meta_label:" not in yt
ylines = yt.splitlines(True)
yout = []
ins = False
for line in ylines:
    yout.append(line)
    if (not ins) and "{max: 1.01, scale: 1.10}" in line:
        yout.append("  # Meta-label secondary filter (NEW family). enabled=false = bit-identical.\n")
        yout.append("  # HOLD when atr_pctile<=atr_max AND confidence<conf_max. Does NOT raise min_confidence.\n")
        yout.append("  meta_label:\n")
        yout.append("    enabled: false\n")
        yout.append("    mode: rule\n")
        yout.append("    rule: quiet_weak\n")
        yout.append("    atr_max: 0.15\n")
        yout.append("    conf_max: 0.65\n")
        ins = True
assert ins
yaml_path.write_text("".join(yout), encoding="utf-8")
print("yaml ok")
print("DONE")
