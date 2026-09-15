import threading
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.market_data.engine import MarketDataEngine
from app.market_data.storage import upsert_candles
from app.models import Base, Candle


class StubRedis:
    def __init__(self):
        self.streams = {}

    def xadd(self, stream, fields):
        self.streams.setdefault(stream, []).append(dict(fields))
        return "fake-id"


class FakeBroker:
    def __init__(self, candles_by_timeframe=None, error=False):
        self.candles_by_timeframe = candles_by_timeframe or {}
        self._connected = False
        self.error = error
        self.get_calls = 0

    @property
    def connected(self):
        return self._connected

    def connect(self):
        self._connected = True

    def disconnect(self):
        self._connected = False

    def get_candles(self, symbol, timeframe, count):
        self.get_calls += 1
        if self.error:
            raise RuntimeError("simulated broker outage")
        return self.candles_by_timeframe.get(timeframe, [])


def make_candle(
    symbol="EURUSD",
    timeframe="H1",
    timestamp=None,
    open_=1.1,
    high=1.11,
    low=1.09,
    close=1.105,
    volume=100,
):
    return {
        "symbol": symbol,
        "timeframe": timeframe,
        "timestamp": timestamp
        or datetime(2026, 9, 15, 8, 0, tzinfo=timezone.utc),
        "open": open_,
        "high": high,
        "low": low,
        "close": close,
        "volume": volume,
    }


@pytest.fixture
def sqlite_session_factory():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, autocommit=False, autoflush=False)


@pytest.fixture
def broker():
    return FakeBroker()


class TestUpsert:
    def test_inserts_new_candles(self, sqlite_session_factory):
        with sqlite_session_factory() as session:
            inserted = upsert_candles(
                session,
                [
                    make_candle(timeframe="M15"),
                    make_candle(timeframe="H1"),
                ],
            )
            assert len(inserted) == 2
            assert session.query(Candle).count() == 2

    def test_skips_already_stored_candles(self, sqlite_session_factory):
        with sqlite_session_factory() as session:
            upsert_candles(session, [make_candle()])
        with sqlite_session_factory() as session:
            inserted_again = upsert_candles(session, [make_candle()])
            assert inserted_again == []
            assert session.query(Candle).count() == 1

    def test_updating_same_key_never_duplicates(self, sqlite_session_factory):
        with sqlite_session_factory() as session:
            first = upsert_candles(session, [make_candle(close=1.105)])
            second = upsert_candles(session, [make_candle(close=1.200)])
            assert len(first) == 1
            assert second == []  # same (symbol, tf, ts) already exists
            assert session.query(Candle).count() == 1

    def test_mixed_batch_inserts_only_new(self, sqlite_session_factory):
        ts_new = datetime(2026, 9, 15, 9, 0, tzinfo=timezone.utc)
        with sqlite_session_factory() as session:
            upsert_candles(session, [make_candle(timestamp=ts_new)])
        with sqlite_session_factory() as session:
            inserted = upsert_candles(
                session,
                [
                    make_candle(timestamp=ts_new),  # already stored
                    make_candle(  # brand new
                        timestamp=datetime(2026, 9, 15, 10, 0, tzinfo=timezone.utc)
                    ),
                ],
            )
            assert len(inserted) == 1
            assert session.query(Candle).count() == 2


class TestEnginePoll:
    def test_polls_and_publishes_each_new_candle(self, sqlite_session_factory):
        redis_client = StubRedis()
        c1 = make_candle(timestamp=datetime(2026, 9, 15, 8, 0, tzinfo=timezone.utc))
        c2 = make_candle(timestamp=datetime(2026, 9, 15, 9, 0, tzinfo=timezone.utc))
        engine = MarketDataEngine(
            broker=FakeBroker(candles_by_timeframe={"H1": [c1, c2]}),
            session_factory=sqlite_session_factory,
            redis_client=redis_client,
            symbol="EURUSD",
            timeframes="H1",
            poll_interval=1,
            fetch_count=50,
        )

        result = engine.poll_once()
        assert result.fetched == 2
        assert result.inserted == 2
        assert result.published == 2
        entries = redis_client.streams["phoenix:candles:new"]
        assert len(entries) == 2
        assert entries[0]["symbol"] == "EURUSD"
        assert entries[0]["timeframe"] == "H1"

    def test_second_poll_publishes_nothing_new(self, sqlite_session_factory):
        engine = MarketDataEngine(
            broker=FakeBroker(),
            session_factory=sqlite_session_factory,
            redis_client=StubRedis(),
            symbol="EURUSD",
            timeframes="H1",
            poll_interval=1,
        )
        candle = make_candle()
        engine.broker.candles_by_timeframe = {"H1": [candle]}

        first = engine.poll_once()
        second = engine.poll_once()
        assert first.inserted == 1
        assert second.inserted == 0
        assert second.published == 0

    def test_multiple_timeframes(self, sqlite_session_factory):
        from app.market_data.engine import PollResult

        redis_client = StubRedis()
        c_m15 = make_candle(timeframe="M15")
        c_h1 = make_candle(timeframe="H1")
        engine = MarketDataEngine(
            broker=FakeBroker(
                candles_by_timeframe={"M15": [c_m15], "H1": [c_h1]}
            ),
            session_factory=sqlite_session_factory,
            redis_client=redis_client,
            symbol="EURUSD",
            timeframes="M15,H1",
        )
        result = engine.poll_once()
        assert isinstance(result, PollResult)
        assert result.fetched == 2
        assert result.inserted == 2
        assert result.published == 2
        assert sorted(e["timeframe"] for e in redis_client.streams["phoenix:candles:new"]) == ["H1", "M15"]

    def test_redis_failure_does_not_kill_poll(self, sqlite_session_factory):
        class BadRedis:
            def xadd(self, stream, fields):
                raise RuntimeError("redis down")

        engine = MarketDataEngine(
            broker=FakeBroker(candles_by_timeframe={"H1": [make_candle()]}),
            session_factory=sqlite_session_factory,
            redis_client=BadRedis(),
            symbol="EURUSD",
            timeframes="H1",
        )
        # Even though publish fails, insertion succeeds and no exception escapes.
        result = engine.poll_once()
        assert result.inserted == 1
        assert result.published == 0

    def test_empty_timeframes_rejected(self, sqlite_session_factory):
        with pytest.raises(ValueError, match="At least one timeframe"):
            MarketDataEngine(
                broker=FakeBroker(),
                session_factory=sqlite_session_factory,
                redis_client=StubRedis(),
                timeframes="",
            )


class TestEngineRun:
    def test_run_propagates_error_when_requested(self, sqlite_session_factory):
        engine = MarketDataEngine(
            broker=FakeBroker(error=True),
            session_factory=sqlite_session_factory,
            redis_client=StubRedis(),
            symbol="EURUSD",
            timeframes="H1",
            poll_interval=1,
        )
        with pytest.raises(RuntimeError, match="simulated broker outage"):
            engine.run(raise_on_error=True, stop_event=threading.Event())

    def test_run_retries_and_does_not_exit_on_error(self, sqlite_session_factory):
        engine = MarketDataEngine(
            broker=FakeBroker(error=True),
            session_factory=sqlite_session_factory,
            redis_client=StubRedis(),
            symbol="EURUSD",
            timeframes="H1",
            poll_interval=0.1,
        )
        stop_event = threading.Event()

        def _stop_after(t):
            import time as _t

            _t.sleep(t)
            stop_event.set()

        t = threading.Thread(target=_stop_after, args=(0.5,), daemon=True)
        t.start()
        engine.run(stop_event=stop_event)
        # It must have kept retrying rather than exiting at first failure.
        assert engine.broker.get_calls >= 2