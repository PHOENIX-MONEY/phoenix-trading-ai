from dataclasses import dataclass, field


@dataclass
class ProposedTrade:
    symbol: str
    direction: str
    entry_price: float
    stop_loss: float
    take_profit: float
    confidence: float = 1.0


@dataclass
class OpenPosition:
    ticket: int
    symbol: str
    direction: str
    volume: float
    open_price: float
    stop_loss: float
    profit: float


@dataclass
class AccountInfo:
    balance: float
    equity: float
    daily_starting_equity: float


@dataclass
class RiskDecision:
    approved: bool
    reason: str
    adjusted_volume: float = 0.0
    trade: ProposedTrade | None = None