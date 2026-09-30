import numpy as np
from forex_lab.replay import size_scale_for_confidence

def test_size_scale_disabled_is_one():
    assert size_scale_for_confidence(0.9, {"replay": {"size_by_conf": {"enabled": False}}}) == 1.0
    assert size_scale_for_confidence(0.9, {}) == 1.0

def test_size_scale_bands():
    cfg = {"replay": {"size_by_conf": {"enabled": True, "bands": [
        {"max": 0.70, "scale": 0.5}, {"max": 0.85, "scale": 1.0}, {"max": 1.01, "scale": 1.25}
    ]}}}
    assert size_scale_for_confidence(0.62, cfg) == 0.5
    assert size_scale_for_confidence(0.70, cfg) == 1.0  # max exclusive via <
    assert size_scale_for_confidence(0.84, cfg) == 1.0
    assert size_scale_for_confidence(0.85, cfg) == 1.25
    assert size_scale_for_confidence(0.99, cfg) == 1.25
