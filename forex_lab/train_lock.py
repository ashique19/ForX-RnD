"""Production-model training lock (desk safety).

No desk path (POST /refresh, POST /pipeline, POST /model/train, signal
generation, Streamlit Lab, schedulers) may fit or overwrite production
``models/<PAIR>_*.joblib`` implicitly. A missing model is reported as
``need_train``. The only training path is the explicit retrain gate
(``scripts/weekly_retrain.py``), which by default saves a challenger report
only; writing the champion / production joblib needs ``--promote``.
"""
from __future__ import annotations

NEED_TRAIN_RC = 3
STATUS_NEED_TRAIN = "need_train"
TRAIN_LOCKED_MSG = (
    "production training is locked outside the retrain gate: run "
    "scripts/weekly_retrain.py (challenger report only) and promote explicitly "
    "with --promote"
)


class ProductionTrainLocked(RuntimeError):
    """Raised/reported when a desk path tries to train production models."""


__all__ = ["NEED_TRAIN_RC", "STATUS_NEED_TRAIN", "TRAIN_LOCKED_MSG", "ProductionTrainLocked"]
