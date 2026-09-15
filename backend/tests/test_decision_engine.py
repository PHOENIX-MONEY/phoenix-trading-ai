from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.broker import BrokerError
from app.decision import DecisionEngine
from app.models import Base, Candle, Decision, EngineState
from app.risk import OpenPosition, RiskEngine
from app.strategy import MovingAverageCrossover


class StubRedis:
    """Fake redis client: returns queued (id, fields) entries to xread."""

    def __init__(self):
        self.queue: list[tuple[str, dict]] = []

    def xread(self, streams, count=20, block=250):
        if not self.queue:
            return []
        batch = self.queue[:count] if count else self.queue
        self.queue = self.queue[len(batch) :]
        return [(list(streams)[0], batch)]


class FakeBroker:
    def __init__(
        self,
        bid=1.1500,
        ask=1.1502,
        equity=10000.0,
        balance=10000.0,
        positions=None,
        fail_on_price=False,
    ):
        self.bid = bid
        self.ask = ask
        self.equity = equity
        self.balance = balance
        self.positions = positions or []
        self.fail_on_price = fail_on_price
        self.order_calls = []
        self.connected = True

    def connect(self):
        self.connected = True

    def disconnect(self):
        self.connected = False

    def get_symbol_price(self, symbol):
        if self.fail_on_price:
            raise BrokerError("MT5 terminal disconnected")
        return {
            "symbol": symbol,
            "bid": self.bid,
            "ask": self.ask,
            "time": datetime.now(timezone.utc),
        }

    def get_account_info(self):
        return {
            "login": 1,
            "currency": "USD",
            "balance": self.balance,
            "equity": self.equity,
            "margin": 0.0,
            "margin_free": self.equity,
        }

    def get_open_positions(self):
        return list(self.positions)

    def place_market_order(
        self, symbol, direction, volume, stop_loss=None, take_profit=None, deviation=20
    ):
        self.order_calls.append((symbol, direction, volume, stop_loss, take_profit))
        return {
            "order": 9999,
            "deal": 1000,
            "retcode": 10009,
            "volume": volume,
            "price": self.ask if direction == "BUY" else self.bid,
        }


@pytest.fixture
def session_factory():
    engine = create_engine(
        "sqlite://",
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, autocommit=False, autoflush=False)


def seed_candles(session_factory, closes, timeframe="H1", symbol="EURUSD"):
    start = datetime(2026, 9, 1, 0, 0, tzinfo=timezone.utc)
    with session_factory() as session:
        for i, close in enumerate(closes):
            session.add(
                Candle(
                    symbol=symbol,
                    timeframe=timeframe,
                    timestamp=start + timedelta(hours=i),
                    open=close,
                    high=close,
                    low=close,
                    close=close,
                    volume=100,
                )
            )
        session.commit()


def golden_cross_closes():
    flat = [1.0] * 30
    return flat + [1.20]


def entry(seq: int, symbol="EURUSD", timeframe="H1") -> tuple[str, dict]:
    return (
        f"{seq}-0",
        {
            "symbol": symbol,
            "timeframe": timeframe,
            "timestamp": f"2026-09-15T12:{seq % 60:02d}:00+00:00",
        },
    )


def make_engine(session_factory, broker=None, dry_run=True, **kwargs):
    strategy = MovingAverageCrossover(fast_period=10, slow_period=30)
    risk = RiskEngine(
        max_risk_per_trade_pct=1.0,
        max_daily_loss_pct=3.0,
        max_concurrent_positions=3,
    )
    return DecisionEngine(
        strategy=strategy,
        broker=broker or FakeBroker(),
        risk_engine=risk,
        redis_client=StubRedis(),
        session_factory=session_factory,
        symbol="EURUSD",
        timeframe="H1",
        pip_size=0.0001,
        lookback=100,
        dry_run=dry_run,
        **kwargs,
    )


class TestDecisionEngine:
    def test_no_signal_is_logged(self, session_factory):
        seed_candles(session_factory, [1.0 + i * 0.001 for i in range(40)])
        engine = make_engine(session_factory)
        engine.redis_client.queue.append(entry(1))

        results = engine.process_stream()
        assert len(results) == 1
        assert results[0]["decision"] == "NO_SIGNAL"
        with session_factory() as session:
            row = session.query(Decision).one()
            assert row.signal == "NONE"
            assert row.decision == "NO_SIGNAL"

    def test_golden_cross_dry_run(self, session_factory):
        seed_candles(session_factory, golden_cross_closes())
        engine = make_engine(session_factory)
        engine.redis_client.queue.append(entry(1))

        results = engine.process_stream()
        result = results[0]
        assert result["signal"] == "BUY"
        assert result["decision"] == "DRY_RUN"
        # $10,000 equity, 1% = $100 risk; 50-pip SL at 0.0001 => 0.005 distance,
        # $500/lot => 0.20 lots. Dry run must NOT place any order.
        assert result["volume"] == pytest.approx(0.2)
        assert engine.broker.order_calls == []
        with session_factory() as session:
            row = session.query(Decision).one()
            assert row.decision == "DRY_RUN"
            assert row.volume == pytest.approx(0.2)
            assert row.fast_ma > row.slow_ma

    def test_rejected_at_concurrent_position_limit(self, session_factory):
        seed_candles(session_factory, golden_cross_closes())
        positions = [
            OpenPosition(
                ticket=i, symbol="GBPUSD", direction="BUY", volume=0.1,
                open_price=1.2, stop_loss=0.0, profit=0.0,
            )
            for i in range(3)
        ]
        engine = make_engine(session_factory, broker=FakeBroker(positions=positions))
        engine.redis_client.queue.append(entry(1))

        results = engine.process_stream()
        assert results[0]["decision"] == "REJECTED"
        assert "concurrent" in results[0]["reason"].lower()
        with session_factory() as session:
            assert session.query(Decision).one().decision == "REJECTED"

    def test_places_real_order_when_dry_run_disabled(self, session_factory):
        seed_candles(session_factory, golden_cross_closes())
        broker = FakeBroker()
        engine = make_engine(session_factory, broker=broker, dry_run=False)
        engine.redis_client.queue.append(entry(1))

        results = engine.process_stream()
        result = results[0]
        assert result["decision"] == "PLACED"
        assert result["ticket"] == 9999
        assert broker.order_calls == [
            ("EURUSD", "BUY", pytest.approx(0.2), pytest.approx(1.1502 - 0.005), pytest.approx(1.1502 + 0.01)),
        ]

    def test_broker_error_records_failed(self, session_factory):
        seed_candles(session_factory, golden_cross_closes())
        engine = make_engine(session_factory, broker=FakeBroker(fail_on_price=True))
        engine.redis_client.queue.append(entry(1))

        results = engine.process_stream()
        assert results[0]["decision"] == "FAILED"
        assert "MT5 terminal disconnected" in results[0]["reason"]
        with session_factory() as session:
            assert session.query(Decision).one().decision == "FAILED"

    def test_skipped_without_candles_in_store(self, session_factory):
        engine = make_engine(session_factory)
        engine.redis_client.queue.append(entry(1))
        results = engine.process_stream()
        assert results[0]["decision"] == "SKIPPED"
        with session_factory() as session:
            assert session.query(Decision).one().decision == "SKIPPED"

    def test_ignores_other_symbols_and_timeframes(self, session_factory):
        seed_candles(session_factory, golden_cross_closes())
        engine = make_engine(session_factory)
        engine.redis_client.queue.append(entry(1, symbol="GBPUSD"))
        engine.redis_client.queue.append(entry(2, timeframe="M15"))

        assert engine.process_stream() == []
        with session_factory() as session:
            assert session.query(Decision).count() == 0

    def test_day_start_equity_recorded_once(self, session_factory):
        seed_candles(session_factory, golden_cross_closes())
        broker = FakeBroker()
        engine = make_engine(session_factory, broker=broker)

        engine.redis_client.queue.append(entry(1))
        engine.process_stream()
        with session_factory() as session:
            states = session.query(EngineState).all()
            assert len(states) == 1
            assert float(states[0].value) == pytest.approx(10000.0)

        # Same day, equity changed: engine keeps the original starting equity.
        broker.equity = 9900.0
        engine.redis_client.queue.append(entry(2))
        engine.process_stream()
        with session_factory() as session:
            assert session.query(EngineState).count() == 1

    def test_auto_connects_broker_before_acting_on_signal(self, session_factory):
        seed_candles(session_factory, golden_cross_closes())
        broker = FakeBroker()
        broker.connected = False
        engine = make_engine(session_factory, broker=broker)
        engine.redis_client.queue.append(entry(1))

        results = engine.process_stream()
        assert results[0]["decision"] == "DRY_RUN"
        assert broker.connected is True

    def test_drains_queue_then_watches_only_new(self, session_factory):
        seed_candles(session_factory, golden_cross_closes())
        engine = make_engine(session_factory)
        engine.redis_client.queue.append(entry(1))

        assert len(engine.process_stream()) == 1
        assert engine.process_stream() == []  # nothing new

        engine.redis_client.queue.append(entry(2))
        results = engine.process_stream()
        assert len(results) == 1
        assert results[0]["stream_id"] == "2-0"