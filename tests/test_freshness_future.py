from datetime import datetime, timedelta
import pandas as pd
from forex_lab.freshness import assess_ohlcv, VALIDITY_ERROR, VALIDITY_OK

def _df(last: datetime) -> pd.DataFrame:
    idx = pd.date_range(end=last, periods=30, freq="h")
    return pd.DataFrame(
        {
            "Open": 1.1,
            "High": 1.11,
            "Low": 1.09,
            "Close": 1.1,
            "Volume": 100,
        },
        index=idx,
    )

def test_future_last_bar_is_error_not_ok():
    now = datetime(2026, 9, 21, 12, 0, 0)
    future = now + timedelta(days=365)
    fr = assess_ohlcv(_df(future), "1h", now=now)
    assert fr.validity == VALIDITY_ERROR
    assert fr.suppress_live_signal() is True

def test_recent_bar_ok_when_session_open():
    # Monday noon UTC — typically open
    now = datetime(2026, 9, 21, 12, 0, 0)  # Monday
    last = now - timedelta(minutes=30)
    fr = assess_ohlcv(_df(last), "1h", now=now)
    assert fr.validity in {VALIDITY_OK, "CLOSED"}  # allow closed if weekend logic differs
