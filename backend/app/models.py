from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Column,
    DateTime,
    Float,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import declarative_base

Base = declarative_base()


class Candle(Base):
    """One OHLCV candle row, deduplicated by (symbol, timeframe, timestamp)."""

    __tablename__ = "candles"

    id = Column(Integer, primary_key=True)
    symbol = Column(String(32), nullable=False, index=True)
    timeframe = Column(String(8), nullable=False)
    timestamp = Column(DateTime(timezone=True), nullable=False)
    open = Column(Float, nullable=False)
    high = Column(Float, nullable=False)
    low = Column(Float, nullable=False)
    close = Column(Float, nullable=False)
    volume = Column(Float, nullable=False)

    __table_args__ = (
        UniqueConstraint(
            "symbol", "timeframe", "timestamp",
            name="uq_candles_symbol_timeframe_timestamp",
        ),
    )

    def as_dict(self) -> dict:
        return {
            "symbol": self.symbol,
            "timeframe": self.timeframe,
            "timestamp": self.timestamp,
            "open": self.open,
            "high": self.high,
            "low": self.low,
            "close": self.close,
            "volume": self.volume,
        }


class Decision(Base):
    """One auditable decision-engine outcome per processed candle event.

    One row per Redis stream entry (:unique stream_id). If the engine is ever
    restarted and replays an event, the row is simply skipped on unique
    collision — no duplicate signal can be traded on.
    """

    __tablename__ = "decisions"

    id = Column(Integer, primary_key=True)
    stream_id = Column(String(64), nullable=False, unique=True)
    processed_at = Column(DateTime(timezone=True), nullable=False)
    candle_time = Column(String(64), nullable=True)
    symbol = Column(String(32), nullable=False)
    timeframe = Column(String(8), nullable=False)
    signal = Column(String(8), nullable=False)  # BUY / SELL / NONE
    decision = Column(String(16), nullable=False)  # NO_SIGNAL / REJECTED / DRY_RUN / PLACED / FAILED / SKIPPED
    reason = Column(Text, nullable=False, default="")
    fast_ma = Column(Float, nullable=True)
    slow_ma = Column(Float, nullable=True)
    volume = Column(Float, nullable=True)
    entry_price = Column(Float, nullable=True)
    stop_loss = Column(Float, nullable=True)
    take_profit = Column(Float, nullable=True)
    ticket = Column(BigInteger, nullable=True)

    def as_dict(self) -> dict:
        return {
            "id": self.id,
            "stream_id": self.stream_id,
            "processed_at": self.processed_at,
            "candle_time": self.candle_time,
            "symbol": self.symbol,
            "timeframe": self.timeframe,
            "signal": self.signal,
            "decision": self.decision,
            "reason": self.reason,
            "fast_ma": self.fast_ma,
            "slow_ma": self.slow_ma,
            "volume": self.volume,
            "entry_price": self.entry_price,
            "stop_loss": self.stop_loss,
            "take_profit": self.take_profit,
            "ticket": self.ticket,
        }


class EngineState(Base):
    """Small key/value state store for the decision engine (one row per key).

    Currently used to remember each trading day's starting equity, so the
    daily-loss halt compares a day against its true starting equity rather
    than the current balance.
    """

    __tablename__ = "engine_state"

    key = Column(String(128), primary_key=True)
    value = Column(Text, nullable=False)
    updated_at = Column(DateTime(timezone=True), nullable=False)