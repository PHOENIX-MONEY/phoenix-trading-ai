from datetime import datetime, timezone

from app.broker.timeframes import TIMEFRAME_MAP, validate_timeframe
from app.risk.models import OpenPosition

try:
    import MetaTrader5 as mt5

    MT5_AVAILABLE = True
except ImportError:
    mt5 = None
    MT5_AVAILABLE = False

from app import config

# MT5 direction <-> internal "BUY"/"SELL" mapping.
DIRECTION_TO_MT5 = {"BUY": mt5.ORDER_TYPE_BUY if MT5_AVAILABLE else 0, "SELL": mt5.ORDER_TYPE_SELL if MT5_AVAILABLE else 1}
POSITION_TYPE_TO_DIRECTION = {0: "BUY", 1: "SELL"}  # mt5.POSITION_TYPE_BUY / SELL

TRADE_RETCODE_DONE = 10009


class BrokerError(RuntimeError):
    """Raised for any broker-level failure with a human-readable message."""


class BrokerClient:
    """Native MetaTrader 5 bridge.

    Windows-only: requires the `MetaTrader5` package (requirements-mt5.txt)
    plus a running, logged-in MT5 desktop terminal. On Linux the import is
    guarded (available == False) and every operation raises a clear
    BrokerError so the rest of the app degrades gracefully.
    """

    def __init__(
        self,
        login: str | None = None,
        password: str | None = None,
        server: str | None = None,
        symbol: str | None = None,
    ) -> None:
        self.login = login or config.MT5_LOGIN
        self.password = password or config.MT5_PASSWORD
        self.server = server or config.MT5_SERVER
        self.symbol = symbol or config.PRIMARY_SYMBOL
        self._connected = False

    @property
    def available(self) -> bool:
        return MT5_AVAILABLE

    @property
    def connected(self) -> bool:
        return self._connected

    def _ensure_ready(self) -> None:
        if not MT5_AVAILABLE:
            raise BrokerError(
                "MetaTrader5 is only available on Windows with a local MT5 "
                "terminal. Run the backend natively "
                "(pip install -r requirements-mt5.txt) to connect."
            )
        if not self._connected:
            raise BrokerError(
                "Not connected. Call connect() (MT5 terminal must be running "
                "and logged in) before using broker methods."
            )

    def connect(self) -> bool:
        if not MT5_AVAILABLE:
            raise BrokerError(
                "MetaTrader5 is only available on Windows with a local MT5 "
                "terminal. Run the backend natively "
                "(pip install -r requirements-mt5.txt) to connect."
            )
        if not (self.login and self.password and self.server):
            raise ValueError(
                "MT5_LOGIN, MT5_PASSWORD and MT5_SERVER must be set in .env"
            )

        initialized = mt5.initialize(
            login=int(self.login),
            password=self.password,
            server=self.server,
        )
        if not initialized:
            code, message = mt5.last_error()
            raise BrokerError(f"MT5 initialize failed (code {code}): {message}")

        self._connected = True
        return True

    def disconnect(self) -> None:
        if self._connected:
            try:
                mt5.shutdown()
            finally:
                self._connected = False

    def get_account_info(self) -> dict:
        """Return balance/equity/margin snapshot as a plain dict."""
        self._ensure_ready()
        info = mt5.account_info()
        if info is None:
            code, message = mt5.last_error()
            raise BrokerError(f"account_info() failed (code {code}): {message}")
        return {
            "login": info.login,
            "currency": info.currency,
            "balance": float(info.balance),
            "equity": float(info.equity),
            "margin": float(info.margin),
            "margin_free": float(info.margin_free),
        }

    def get_symbol_price(self, symbol: str | None = None) -> dict:
        """Return current bid/ask for a symbol (default PRIMARY_SYMBOL)."""
        self._ensure_ready()
        symbol = symbol or self.symbol
        tick = mt5.symbol_info_tick(symbol)
        if tick is None:
            code, message = mt5.last_error()
            raise BrokerError(f"symbol_info_tick({symbol}) failed (code {code}): {message}")
        return {
            "symbol": symbol,
            "bid": float(tick.bid),
            "ask": float(tick.ask),
            "time": datetime.fromtimestamp(tick.time, tz=timezone.utc),
        }

    def get_candles(
        self,
        symbol: str | None = None,
        timeframe: str = "H1",
        count: int = 200,
    ) -> list[dict]:
        """Return up to `count` OHLCV candles (newest last) as plain dicts.

        `timeframe` is an MT5 string like "M15", "H1", "H4", "D1", "W1".
        """
        self._ensure_ready()
        symbol = symbol or self.symbol
        tf = validate_timeframe(timeframe)
        rates = mt5.copy_rates_from_pos(symbol, tf, 0, count)
        if rates is None:
            code, message = mt5.last_error()
            raise BrokerError(
                f"copy_rates_from_pos({symbol}, {timeframe}) failed "
                f"(code {code}): {message}"
            )

        return [
            {
                "symbol": symbol,
                "timeframe": timeframe.upper(),
                "timestamp": datetime.fromtimestamp(float(rate["time"]), tz=timezone.utc),
                "open": float(rate["open"]),
                "high": float(rate["high"]),
                "low": float(rate["low"]),
                "close": float(rate["close"]),
                "tick_volume": float(rate["tick_volume"]),
                "real_volume": float(rate["real_volume"]),
                "spread": int(rate["spread"]),
            }
            for rate in rates
        ]

    def get_open_positions(self) -> list[OpenPosition]:
        """Return current open positions as risk-engine-compatible objects."""
        self._ensure_ready()
        rows = mt5.positions_get()
        if rows is None:
            code, message = mt5.last_error()
            raise BrokerError(f"positions_get() failed (code {code}): {message}")

        positions = []
        for row in rows:
            direction = POSITION_TYPE_TO_DIRECTION.get(row.type, "UNKNOWN")
            positions.append(
                OpenPosition(
                    ticket=int(row.ticket),
                    symbol=row.symbol,
                    direction=direction,
                    volume=float(row.volume),
                    open_price=float(row.price_open),
                    stop_loss=float(row.sl),
                    profit=float(row.profit),
                )
            )
        return positions

    def place_market_order(
        self,
        symbol: str,
        direction: str,
        volume: float,
        stop_loss: float | None = None,
        take_profit: float | None = None,
        deviation: int = 20,
    ) -> dict:
        """Place a market order.

        NOTE (Phase 1): implemented but intentionally NOT called anywhere yet.
        It will be wired into the decision loop in Phase 4, and only ever via
        the RiskEngine.
        """
        self._ensure_ready()
        direction = direction.upper()
        if direction not in DIRECTION_TO_MT5:
            raise ValueError(f"direction must be BUY or SELL, got {direction!r}")
        if volume <= 0:
            raise ValueError("volume must be positive")

        request = {
            "action": mt5.TRADE_ACTION_DEAL,
            "symbol": symbol,
            "volume": float(volume),
            "type": DIRECTION_TO_MT5[direction],
            "deviation": deviation,
            "magic": 0,
        }
        if stop_loss is not None:
            request["sl"] = float(stop_loss)
        if take_profit is not None:
            request["tp"] = float(take_profit)

        result = mt5.order_send(request)
        if result is None:
            code, message = mt5.last_error()
            raise BrokerError(f"order_send() failed (code {code}): {message}")

        if result.retcode != TRADE_RETCODE_DONE:
            raise BrokerError(
                f"Order rejected (retcode {result.retcode}): {result.comment}"
            )

        return {
            "retcode": result.retcode,
            "comment": result.comment,
            "order": result.order,
            "deal": result.deal,
            "volume": float(result.volume),
            "price": float(result.price),
        }