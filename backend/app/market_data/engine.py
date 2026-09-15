import logging
import time
from dataclasses import dataclass, field
from threading import Event

import redis

from app import config
from app.broker import BrokerClient
from app.db import SessionLocal
from app.market_data.storage import upsert_candles

logger = logging.getLogger(__name__)

MAX_BACKOFF_SEC = 300


@dataclass
class PollResult:
    symbol: str
    timeframes: list[str]
    fetched: int = 0
    inserted: int = 0
    published: int = 0
    errors: list[str] = field(default_factory=list)


class MarketDataEngine:
    """Pull candles from the broker at a fixed interval, persist new rows to
    Postgres, and publish each newly stored candle to a Redis stream.

    Never exits on a transient broker/network error — it logs and retries with
    exponential backoff (up to MAX_BACKOFF_SEC).
    """

    def __init__(
        self,
        broker=None,
        session_factory=None,
        redis_client=None,
        symbol: str | None = None,
        timeframes: str | None = None,
        poll_interval: int | None = None,
        fetch_count: int | None = None,
        stream: str | None = None,
    ) -> None:
        self.broker = broker or BrokerClient(symbol=symbol)
        self.session_factory = session_factory or SessionLocal
        self.redis_client = redis_client or redis.Redis.from_url(
            config.REDIS_URL, decode_responses=True
        )
        self.symbol = symbol or config.PRIMARY_SYMBOL
        self.timeframes = [
            tf.strip().upper()
            for tf in (
                timeframes if timeframes is not None else config.MARKET_DATA_TIMEFRAMES
            ).split(",")
            if tf.strip()
        ]
        if not self.timeframes:
            raise ValueError("At least one timeframe must be configured.")
        self.poll_interval = poll_interval or config.MARKET_DATA_POLL_INTERVAL_SEC
        self.fetch_count = fetch_count or config.MARKET_DATA_FETCH_COUNT
        self.stream = stream or config.CANDLE_STREAM

    def _ensure_connected(self) -> None:
        if not self.broker.connected:
            logger.info("Connecting to broker (%s)...", self.symbol)
            self.broker.connect()

    @staticmethod
    def _to_storable(row: dict) -> dict:
        """Convert a broker candle dict into a storage row dict."""
        return {
            "symbol": row["symbol"],
            "timeframe": row["timeframe"],
            "timestamp": row["timestamp"],
            "open": row["open"],
            "high": row["high"],
            "low": row["low"],
            "close": row["close"],
            "volume": row.get("volume", row.get("tick_volume", 0)),
        }

    def poll_once(self) -> PollResult:
        result = PollResult(symbol=self.symbol, timeframes=list(self.timeframes))
        with self.session_factory() as session:
            for timeframe in self.timeframes:
                rows = self.broker.get_candles(
                    self.symbol, timeframe, self.fetch_count
                )
                result.fetched += len(rows)
                if not rows:
                    continue
                inserted = upsert_candles(
                    session, [self._to_storable(r) for r in rows]
                )
                result.inserted += len(inserted)
                for row in inserted:
                    if self._publish(row, timeframe):
                        result.published += 1
        return result

    def _publish(self, candle: dict, timeframe: str) -> bool:
        try:
            self.redis_client.xadd(
                self.stream,
                {
                    "symbol": candle["symbol"],
                    "timeframe": timeframe,
                    "timestamp": candle["timestamp"].isoformat(),
                },
            )
            return True
        except Exception as exc:  # Redis down should not kill the DB write flow
            logger.error("Redis xadd to %s failed: %s", self.stream, exc)
            return False

    def run(
        self,
        stop_event: Event | None = None,
        raise_on_error: bool = False,
    ) -> None:
        stop_event = stop_event or Event()
        backoff = self.poll_interval
        while not stop_event.is_set():
            try:
                self._ensure_connected()
                result = self.poll_once()
                logger.info(
                    "Poll complete: fetched=%s inserted=%s published=%s "
                    "(symbol=%s timeframes=%s)",
                    result.fetched,
                    result.inserted,
                    result.published,
                    result.symbol,
                    ",".join(result.timeframes),
                )
                backoff = self.poll_interval
            except Exception as exc:
                logger.error(
                    "Market data poll errored: %s. Retrying in %ss.",
                    exc,
                    backoff,
                )
                if raise_on_error:
                    raise
                stop_event.wait(timeout=backoff)
                backoff = min(backoff * 2, MAX_BACKOFF_SEC)
                continue
            stop_event.wait(timeout=self.poll_interval)

    def stop(self) -> None:
        try:
            self.broker.disconnect()
        except Exception as exc:
            logger.warning("Error disconnecting broker: %s", exc)