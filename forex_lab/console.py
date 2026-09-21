"""ASCII-safe CLI printing and best-effort UTF-8 stdio.

Windows cp1252 consoles raise UnicodeEncodeError on glyphs such as ``→``.
That must never look like a fetch failure after OHLCV has already been saved.
"""
from __future__ import annotations

import os
import sys
from typing import Any, TextIO


_ASCII_SWAPS = {
    "\u2192": "->",  # →
    "\u2190": "<-",  # ←
    "\u2014": "-",  # —
    "\u2013": "-",  # –
    "\u2265": ">=",  # ≥
    "\u2264": "<=",  # ≤
    "\u2260": "!=",  # ≠
    "\u00b1": "+/-",  # ±
    "\u00b7": "*",  # ·
    "\u00d7": "x",  # ×
    "\u2026": "...",  # …
}


def configure_stdio() -> None:
    """Best-effort UTF-8 stdio. Failures are ignored; prints still use ASCII."""
    os.environ.setdefault("PYTHONUTF8", "1")
    os.environ.setdefault("PYTHONIOENCODING", "utf-8")
    if sys.platform == "win32":
        try:
            import ctypes

            ctypes.windll.kernel32.SetConsoleOutputCP(65001)  # type: ignore[attr-defined]
        except Exception:
            pass
    for name in ("stdout", "stderr"):
        stream = getattr(sys, name, None)
        reconfigure = getattr(stream, "reconfigure", None)
        if not callable(reconfigure):
            continue
        try:
            reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass


def ascii_text(msg: Any) -> str:
    text = str(msg)
    for src, dst in _ASCII_SWAPS.items():
        text = text.replace(src, dst)
    return text.encode("ascii", "replace").decode("ascii")


def safe_print(msg: Any = "", *, file: TextIO | None = None) -> None:
    """Print ASCII text. Never raise — a console encoding error is not a data error."""
    text = ascii_text(msg)
    stream = file if file is not None else sys.stdout
    try:
        print(text, file=stream)
        return
    except Exception:
        pass
    try:
        buf = getattr(stream, "buffer", None)
        if buf is not None:
            buf.write((text + "\n").encode("utf-8", "replace"))
            buf.flush()
    except Exception:
        pass
