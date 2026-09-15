from abc import ABC, abstractmethod
from dataclasses import dataclass, field


@dataclass(frozen=True)
class Signal:
    symbol: str
    direction: str  # "BUY" or "SELL"
    stop_loss_pips: float
    take_profit_pips: float
    detail: dict = field(default_factory=dict, compare=False)


class Strategy(ABC):
    """Pure, deterministic signal generator.

    evaluate() must be stateless/idempotent: replaying the same candle window
    must always yield the same Signal. This keeps the decision engine safe to
    restart and lets Phase 5 replay history deterministically.
    """

    @abstractmethod
    def evaluate(self, symbol: str, candles: list[dict]) -> Signal | None:
        """Return a Signal, or None when there is nothing to act on.

        `candles` are stored rows (oldest first) with keys: symbol, timeframe,
        timestamp, open, high, low, close, volume.
        """