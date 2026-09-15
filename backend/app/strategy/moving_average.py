from app.strategy.base import Signal, Strategy


class MovingAverageCrossover(Strategy):
    """Simple moving-average crossover.

    BUY  when the fast SMA crosses ABOVE the slow SMA (golden cross).
    SELL when the fast SMA crosses BELOW the slow SMA (death cross).

    Stateless by construction: the cross is derived from the candle window
    alone (fast/slow now vs fast/slow one bar ago), so replaying the same
    window always produces the same signal.
    """

    def __init__(
        self,
        fast_period: int = 10,
        slow_period: int = 30,
        stop_loss_pips: float = 50.0,
        take_profit_pips: float = 100.0,
    ) -> None:
        if not 0 < fast_period < slow_period:
            raise ValueError(
                "fast_period must be positive and smaller than slow_period "
                f"(got fast={fast_period}, slow={slow_period})."
            )
        self.fast_period = fast_period
        self.slow_period = slow_period
        self.stop_loss_pips = stop_loss_pips
        self.take_profit_pips = take_profit_pips

    @staticmethod
    def _sma(closes: list[float], period: int) -> float:
        window = closes[-period:]
        return sum(window) / period

    def evaluate(self, symbol: str, candles: list[dict]) -> Signal | None:
        if len(candles) < self.slow_period + 1:
            return None

        closes = [float(c["close"]) for c in candles]
        previous = closes[:-1]

        fast = self._sma(closes, self.fast_period)
        slow = self._sma(closes, self.slow_period)
        prev_fast = self._sma(previous, self.fast_period)
        prev_slow = self._sma(previous, self.slow_period)

        crossed_up = prev_fast <= prev_slow and fast > slow
        crossed_down = prev_fast >= prev_slow and fast < slow
        if not (crossed_up or crossed_down):
            return None

        direction = "BUY" if crossed_up else "SELL"
        return Signal(
            symbol=symbol,
            direction=direction,
            stop_loss_pips=self.stop_loss_pips,
            take_profit_pips=self.take_profit_pips,
            detail={
                "fast": fast,
                "slow": slow,
                "prev_fast": prev_fast,
                "prev_slow": prev_slow,
            },
        )