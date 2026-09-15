from datetime import datetime, timedelta, timezone

import pytest

from app.strategy import MovingAverageCrossover


def candle(close: float, i: int = 0) -> dict:
    return {
        "symbol": "EURUSD",
        "timeframe": "H1",
        "timestamp": datetime(2026, 9, 1, tzinfo=timezone.utc) + timedelta(hours=i),
        "open": close,
        "high": close,
        "low": close,
        "close": close,
        "volume": 100,
    }


def flat_then(jump: float, n: int = 30) -> list[dict]:
    """n flat candles (SMA10 == SMA30), then one jump candle."""
    candles = [candle(1.0, i=i) for i in range(n)]
    candles.append(candle(jump, i=n))
    return candles


class TestMovingAverageCrossover:
    def test_requires_slow_period_plus_one_candles(self):
        strategy = MovingAverageCrossover(fast_period=10, slow_period=30)
        assert strategy.evaluate("EURUSD", [candle(1.0, i=i) for i in range(20)]) is None

    def test_golden_cross_buys(self):
        strategy = MovingAverageCrossover(fast_period=10, slow_period=30)
        signal = strategy.evaluate("EURUSD", flat_then(1.20))
        assert signal is not None
        assert signal.symbol == "EURUSD"
        assert signal.direction == "BUY"
        assert signal.stop_loss_pips == 50
        assert signal.take_profit_pips == 100
        assert signal.detail["fast"] > signal.detail["slow"]

    def test_death_cross_sells(self):
        strategy = MovingAverageCrossover(fast_period=10, slow_period=30)
        signal = strategy.evaluate("EURUSD", flat_then(0.80))
        assert signal is not None
        assert signal.direction == "SELL"
        assert signal.detail["fast"] < signal.detail["slow"]

    def test_no_cross_returns_none(self):
        strategy = MovingAverageCrossover(fast_period=10, slow_period=30)
        closes = [candle(1.0 + i * 0.001, i=i) for i in range(40)]
        assert strategy.evaluate("EURUSD", closes) is None

    def test_replaying_same_window_is_idempotent(self):
        strategy = MovingAverageCrossover(fast_period=10, slow_period=30)
        data = flat_then(1.20)
        first = strategy.evaluate("EURUSD", data)
        second = strategy.evaluate("EURUSD", data)
        assert first is not None and second is not None
        assert first == second  # equal on symbol/direction/pips (detail excluded)

    def test_rejects_invalid_periods(self):
        with pytest.raises(ValueError, match="smaller than slow_period"):
            MovingAverageCrossover(fast_period=30, slow_period=30)
        with pytest.raises(ValueError, match="fast_period"):
            MovingAverageCrossover(fast_period=0, slow_period=30)