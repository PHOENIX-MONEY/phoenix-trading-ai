"""MT5 timeframe name <-> MetaTrader5 constant mapping.

Kept in its own module so the mapping is importable without the (Windows-only)
MetaTrader5 package, letting the rest of the app validate timeframe strings on
any platform.
"""

try:
    import MetaTrader5 as mt5

    _MT5 = mt5
except ImportError:
    _MT5 = None

# Map of human-readable timeframe -> mt5 constant (None when MT5 unavailable).
TIMEFRAME_MAP: dict[str, int] = {
    "M1": getattr(_MT5, "TIMEFRAME_M1", None) if _MT5 else None,
    "M5": getattr(_MT5, "TIMEFRAME_M5", None) if _MT5 else None,
    "M15": getattr(_MT5, "TIMEFRAME_M15", None) if _MT5 else None,
    "M30": getattr(_MT5, "TIMEFRAME_M30", None) if _MT5 else None,
    "H1": getattr(_MT5, "TIMEFRAME_H1", None) if _MT5 else None,
    "H4": getattr(_MT5, "TIMEFRAME_H4", None) if _MT5 else None,
    "D1": getattr(_MT5, "TIMEFRAME_D1", None) if _MT5 else None,
    "W1": getattr(_MT5, "TIMEFRAME_W1", None) if _MT5 else None,
    "MN1": getattr(_MT5, "TIMEFRAME_MN1", None) if _MT5 else None,
}

VALID_TIMEFRAMES: tuple[str, ...] = tuple(TIMEFRAME_MAP.keys())


def canonical_timeframe(timeframe: str) -> str:
    """Validate a timeframe name and return its canonical uppercase string.

    Works on any platform (does not need the MT5 package): useful when the
    string itself is what matters (storage, stream events, decision engine).
    """
    name = str(timeframe).strip().upper()
    if name not in TIMEFRAME_MAP:
        raise ValueError(
            f"Unknown timeframe {timeframe!r}. Valid values: {', '.join(VALID_TIMEFRAMES)}"
        )
    return name


def validate_timeframe(timeframe: str) -> int:
    """Validate an MT5 timeframe name and return its constant.

    Raises ValueError for unknown names (works on any platform); raises
    RuntimeError if the MT5 package is unavailable (won't happen in normal
    broker flow because connect() already checks availability).
    """
    name = str(timeframe).strip().upper()
    if name not in TIMEFRAME_MAP:
        raise ValueError(
            f"Unknown timeframe {timeframe!r}. Valid values: {', '.join(VALID_TIMEFRAMES)}"
        )
    value = TIMEFRAME_MAP[name]
    if value is None:
        raise RuntimeError(
            "MetaTrader5 package not available — cannot resolve timeframe constants."
        )
    return value