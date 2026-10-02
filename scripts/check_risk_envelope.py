"""Print the Stage-4 risk-envelope drift report (read-only, fail-soft)."""
from pathlib import Path
import sys

# Make direct `python scripts/check_risk_envelope.py` work from the repo root.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from forex_lab.risk_envelope import drift_report, format_report


if __name__ == "__main__":
    # A drift warning is actionable but must not block the desk or rewrite YAML.
    print(format_report(drift_report()))
