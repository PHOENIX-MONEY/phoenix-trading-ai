from datetime import datetime, timezone

import pytest

from app.broker import BrokerClient, BrokerError


class FakeAccountInfo:
    login = 592079091
    currency = "USD"
    balance = 10000.0
    equity = 10050.0
    margin = 100.0
    margin_free = 9950.0


class FakeTick:
    def __init__(self, bid, ask, time):
        self.bid = bid
        self.ask = ask
        self.time = time


class FakePosition:
    def __init__(self, ticket, type_, symbol, volume, price_open, sl, profit):
        self.ticket = ticket
        self.type = type_
        self.symbol = symbol
        self.volume = volume
        self.price_open = price_open
        self.sl = sl
        self.profit = profit


class FakeOrderResult:
    def __init__(self, retcode=10009, comment="done", order=12345, deal=57, volume=0.1, price=1.1000):
        self.retcode = retcode
        self.comment = comment
        self.order = order
        self.deal = deal
        self.volume = volume
        self.price = price


class FakeMT5:
    TRADE_ACTION_DEAL = 1

    def __init__(self, order_result=None, order_send_error=None):
        self.order_result = order_result or FakeOrderResult()
        self.order_send_error = order_send_error
        self.initialized = False
        self.shutdown_called = False
        self.last_code = 0
        self.last_message = "no error"

    def initialize(self, login=None, password=None, server=None):
        if login == 0:
            self.last_code = 10004
            self.last_message = "invalid login"
            return False
        self.initialized = True
        return True

    def last_error(self):
        return (self.last_code, self.last_message)

    def shutdown(self):
        self.shutdown_called = True

    def account_info(self):
        return FakeAccountInfo()

    def symbol_info_tick(self, symbol):
        return FakeTick(bid=1.10001, ask=1.10011, time=1730000000)

    def copy_rates_from_pos(self, symbol, timeframe, start_pos, count):
        return [
            {
                "time": 1730000000,
                "open": 1.10,
                "high": 1.11,
                "low": 1.09,
                "close": 1.105,
                "tick_volume": 1200,
                "real_volume": 1200,
                "spread": 5,
            },
            {
                "time": 1730000100,
                "open": 1.105,
                "high": 1.112,
                "low": 1.095,
                "close": 1.11,
                "tick_volume": 900,
                "real_volume": 900,
                "spread": 4,
            },
        ]

    def positions_get(self):
        return [
            FakePosition(ticket=1001, type_=0, symbol="EURUSD", volume=0.1, price_open=1.10, sl=1.095, profit=12.5),
            FakePosition(ticket=1002, type_=1, symbol="EURUSD", volume=0.2, price_open=1.11, sl=1.115, profit=-8.0),
        ]

    def order_send(self, request):
        if self.order_send_error:
            return None
        return self.order_result


@pytest.fixture
def fake_mt5(monkeypatch):
    fake = FakeMT5()
    monkeypatch.setattr("app.broker.broker_client.mt5", fake)
    monkeypatch.setattr("app.broker.broker_client.MT5_AVAILABLE", True)
    # Give timeframes module fake constants so validate_timeframe works.
    monkeypatch.setattr(
        "app.broker.timeframes.TIMEFRAME_MAP",
        {name: i for i, name in enumerate(
            ("M1", "M5", "M15", "M30", "H1", "H4", "D1", "W1", "MN1"), start=1
        )},
        raising=False,
    )
    monkeypatch.setattr(
        "app.broker.timeframes.VALID_TIMEFRAMES",
        tuple("M1 M5 M15 M30 H1 H4 D1 W1 MN1".split()),
        raising=False,
    )
    return fake


@pytest.fixture
def connected_client(fake_mt5):
    client = BrokerClient(login="592079091", password="x", server="y")
    client.connect()
    return client


class TestConnect:
    def test_connect_marks_connected(self, fake_mt5):
        client = BrokerClient(login="1", password="p", server="s")
        assert client.connected is False
        assert client.connect() is True
        assert client.connected is True

    def test_connect_failure_raises_clear_error(self, fake_mt5):
        client = BrokerClient(login="0", password="p", server="s")
        with pytest.raises(BrokerError, match="MT5 initialize failed"):
            client.connect()

    def test_connect_missing_creds_raises(self, fake_mt5, monkeypatch):
        monkeypatch.setattr("app.broker.broker_client.config.MT5_LOGIN", "")
        monkeypatch.setattr("app.broker.broker_client.config.MT5_PASSWORD", "")
        client = BrokerClient()
        with pytest.raises(ValueError, match="MT5_LOGIN"):
            client.connect()

    def test_disconnect(self, fake_mt5):
        client = BrokerClient(login="1", password="p", server="s")
        client.connect()
        client.disconnect()
        assert client.connected is False
        assert fake_mt5.shutdown_called is True

    def test_methods_require_connection(self, fake_mt5):
        client = BrokerClient(login="1", password="p", server="s")
        with pytest.raises(BrokerError, match="Not connected"):
            client.get_account_info()


class TestAccountInfo:
    def test_returns_values(self, connected_client):
        info = connected_client.get_account_info()
        assert info["balance"] == 10000.0
        assert info["equity"] == 10050.0
        assert info["margin"] == 100.0
        assert info["currency"] == "USD"


class TestPrice:
    def test_returns_bid_ask(self, connected_client):
        tick = connected_client.get_symbol_price("EURUSD")
        assert tick["symbol"] == "EURUSD"
        assert tick["bid"] == pytest.approx(1.10001)
        assert tick["ask"] == pytest.approx(1.10011)
        assert isinstance(tick["time"], datetime)


class TestCandles:
    def test_returns_ohlcv_candles(self, connected_client):
        candles = connected_client.get_candles("EURUSD", "H1", 2)
        assert len(candles) == 2
        first = candles[0]
        assert first["symbol"] == "EURUSD"
        assert first["timeframe"] == "H1"
        assert first["open"] == pytest.approx(1.10)
        assert first["high"] == pytest.approx(1.11)
        assert first["low"] == pytest.approx(1.09)
        assert first["close"] == pytest.approx(1.105)
        assert isinstance(first["timestamp"], datetime)
        assert first["timestamp"].tzinfo is not None  # UTC aware

    def test_invalid_timeframe_raises(self, connected_client):
        with pytest.raises(ValueError, match="Unknown timeframe"):
            connected_client.get_candles("EURUSD", "XYZ", 2)


class TestPositions:
    def test_maps_mt5_types_to_direction(self, connected_client):
        positions = connected_client.get_open_positions()
        assert len(positions) == 2
        assert positions[0].direction == "BUY"   # type 0
        assert positions[1].direction == "SELL"  # type 1
        assert positions[0].ticket == 1001
        assert positions[0].volume == pytest.approx(0.1)
        assert positions[0].profit == pytest.approx(12.5)


class TestOrder:
    def test_success_returns_result(self, connected_client):
        result = connected_client.place_market_order("EURUSD", "BUY", 0.1, 1.095, 1.11)
        assert result["retcode"] == 10009
        assert result["order"] == 12345
        assert result["deal"] == 57

    def test_rejected_raises_clear_error(self, connected_client, fake_mt5):
        fake_mt5.order_result = FakeOrderResult(retcode=10016, comment="no money")
        with pytest.raises(BrokerError, match="rejected"):
            connected_client.place_market_order("EURUSD", "BUY", 0.1)

    def test_order_send_failure_raises(self, connected_client, fake_mt5):
        fake_mt5.order_send_error = True
        with pytest.raises(BrokerError, match="order_send"):
            connected_client.place_market_order("EURUSD", "SELL", 0.1)

    def test_invalid_direction_raises(self, connected_client):
        with pytest.raises(ValueError, match="direction"):
            connected_client.place_market_order("EURUSD", "HODL", 0.1)

    def test_nonpositive_volume_raises(self, connected_client):
        with pytest.raises(ValueError, match="volume"):
            connected_client.place_market_order("EURUSD", "BUY", 0)