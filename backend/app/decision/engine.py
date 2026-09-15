import logging
import time
from datetime import datetime, timezone

import redis as redis_mod

from app import config
from app.broker import BrokerClient, BrokerError, canonical_timeframe
from app.db import SessionLocal
from app.models import Candle, Decision, EngineState
from app.risk import AccountInfo, ProposedTrade, RiskEngine
from app.strategy import Signal, Strategy

logger = logging.getLogger(__name__)


class DecisionEngine:
    """The Phase 4 trading loop.

    Pipeline per candle event: Redis stream -> strategy -> RiskEngine ->
    broker.

    - Consumes new-candle events published by the Phase 2 MarketDataEngine.
    - Reloads the recent candle window from Postgres and asks the strategy
      for a Signal (or nothing).
    - If there is a signal, converts the pip-based stop-loss/take-profit into
      absolute prices off the live market, then asks the RiskEngine to gate
      and size the trade.
    - If approved and NOT in dry-run (the safe default), places the real
      market order through the broker; otherwise logs what it would do.

    Every event is written to the `decisions` table (unique stream_id), so the
    loop is restart-safe: a replayed event simply collides on the unique key
    and is ignored.
    """

    def __init__(
        self,
        strategy: Strategy,
        broker: BrokerClient | None = None,
        risk_engine: RiskEngine | None = None,
        symbol: str | None = None,
        timeframe: str | None = None,
        redis_client=None,
        session_factory=None,
        stream: str | None = None,
        pip_size: float | None = None,
        lookback: int | None = None,
        dry_run: bool | None = None,
        poll_interval: float = 5.0,
    ) -> None:
        self.strategy = strategy
        self.broker = broker or BrokerClient(symbol=symbol)
        self.risk_engine = risk_engine or RiskEngine()
        self.symbol = symbol or config.PRIMARY_SYMBOL
        self.timeframe = canonical_timeframe(timeframe or config.STRATEGY_TIMEFRAME)
        self.redis_client = redis_client or redis_mod.Redis.from_url(
            config.REDIS_URL, decode_responses=True
        )
        self.session_factory = session_factory or SessionLocal
        self.stream = stream or config.CANDLE_STREAM
        self.pip_size = pip_size if pip_size is not None else config.STRATEGY_PIP_SIZE
        self.lookback = lookback or config.STRATEGY_LOOKBACK
        self.dry_run = dry_run if dry_run is not None else config.STRATEGY_DRY_RUN
        self.poll_interval = poll_interval
        # None => first read watches only for brand-new events (XREAD "$").
        self._last_id = None

    # --- Redis consumption -------------------------------------------------

    def _ensure_broker(self) -> None:
        if not self.broker.connected:
            logger.info("Connecting to broker (%s)...", self.symbol)
            self.broker.connect()

    def _read_new(self, count: int = 20, block_ms: int = 250) -> list:
        """Return (stream_id, fields) tuples for events newer than last seen."""
        try:
            if self._last_id is None:
                result = self.redis_client.xread(
                    {self.stream: "$"}, count=count, block=block_ms
                )
            else:
                result = self.redis_client.xread(
                    {self.stream: self._last_id}, count=count, block=block_ms
                )
        except Exception as exc:
            logger.error("Redis read failed on %s: %s", self.stream, exc)
            return []
        if not result:
            return []
        return result[0][1]

    def process_stream(self, block_ms: int = 250) -> list[dict]:
        """Read and act on any new candle events; return a summary per event."""
        results = []
        for entry_id, fields in self._read_new(block_ms=block_ms):
            result = self._process(entry_id, fields)
            if result is not None:
                results.append(result)
            # Advance only after the entry is fully handled so a crash mid-batch
            # re-delivers at most one (deduped) event on restart.
            self._last_id = entry_id
        return results

    # --- Persistence helpers -----------------------------------------------

    def _recent_candles(self, session) -> list[dict]:
        rows = (
            session.query(Candle)
            .filter(Candle.symbol == self.symbol, Candle.timeframe == self.timeframe)
            .order_by(Candle.timestamp.desc())
            .limit(self.lookback)
            .all()
        )
        return [row.as_dict() for row in reversed(rows)]

    def _starting_equity(self, session, live_equity: float) -> float:
        """Day-start equity, remembered per (symbol, UTC date) in engine_state."""
        today = datetime.now(timezone.utc).date().isoformat()
        key = f"daily_starting_equity:{self.symbol}:{today}"
        row = session.query(EngineState).filter_by(key=key).first()
        if row is not None:
            return float(row.value)
        session.add(
            EngineState(
                key=key,
                value=str(live_equity),
                updated_at=datetime.now(timezone.utc),
            )
        )
        logger.info(
            "Recorded day-start equity %s for %s (%s).", live_equity, self.symbol, today
        )
        return float(live_equity)

    def _record(
        self,
        session,
        entry_id: str,
        candle_time,
        signal: Signal | None,
        decision: str,
        reason: str,
        trade: ProposedTrade | None = None,
        volume: float | None = None,
        ticket=None,
    ) -> None:
        detail = signal.detail if signal else {}
        session.add(
            Decision(
                stream_id=entry_id,
                processed_at=datetime.now(timezone.utc),
                candle_time=candle_time,
                symbol=self.symbol,
                timeframe=self.timeframe,
                signal=signal.direction if signal else "NONE",
                decision=decision,
                reason=reason,
                fast_ma=detail.get("fast"),
                slow_ma=detail.get("slow"),
                volume=volume,
                entry_price=trade.entry_price if trade else None,
                stop_loss=trade.stop_loss if trade else None,
                take_profit=trade.take_profit if trade else None,
                ticket=ticket,
            )
        )

    # --- Signal -> trade ---------------------------------------------------

    @staticmethod
    def _build_trade(signal: Signal, market: dict, pip_size: float) -> ProposedTrade:
        """Convert a pip-based signal into a ProposedTrade with absolute SL/TP."""
        if signal.direction == "BUY":
            entry = float(market["ask"])
            stop_loss = entry - signal.stop_loss_pips * pip_size
            take_profit = entry + signal.take_profit_pips * pip_size
        else:  # SELL
            entry = float(market["bid"])
            stop_loss = entry + signal.stop_loss_pips * pip_size
            take_profit = entry - signal.take_profit_pips * pip_size
        return ProposedTrade(
            symbol=signal.symbol,
            direction=signal.direction,
            entry_price=entry,
            stop_loss=stop_loss,
            take_profit=take_profit,
        )

    def _process(self, entry_id: str, fields: dict) -> dict | None:
        """Handle a single event; returns a summary dict (None if skipped)."""
        if fields.get("symbol") != self.symbol or fields.get("timeframe") != self.timeframe:
            return None
        candle_time = fields.get("timestamp")

        with self.session_factory() as session:
            summary = self._evaluate(session, entry_id, candle_time, fields)
            session.commit()
            return summary

    def _evaluate(self, session, entry_id: str, candle_time, fields: dict) -> dict:
        summary = {
            "stream_id": entry_id,
            "symbol": self.symbol,
            "timeframe": self.timeframe,
        }

        candles = self._recent_candles(session)
        if not candles:
            self._record(
                session, entry_id, candle_time, None, "SKIPPED",
                "No candles in store yet for this symbol/timeframe.",
            )
            summary["decision"] = "SKIPPED"
            summary["reason"] = "no candles in store yet"
            return summary

        signal = self.strategy.evaluate(self.symbol, candles)
        if signal is None:
            self._record(
                session, entry_id, candle_time, None, "NO_SIGNAL",
                "No crossover signal on the latest candle.",
            )
            summary["decision"] = "NO_SIGNAL"
            return summary

        summary["signal"] = signal.direction
        try:
            self._ensure_broker()
            market = self.broker.get_symbol_price(self.symbol)
            account_raw = self.broker.get_account_info()
            open_positions = self.broker.get_open_positions()
        except BrokerError as exc:
            reason = f"Broker unavailable while acting on signal: {exc}"
            self._record(session, entry_id, candle_time, signal, "FAILED", reason)
            summary["decision"] = "FAILED"
            summary["reason"] = reason
            return summary

        trade = self._build_trade(signal, market, self.pip_size)
        account = AccountInfo(
            balance=float(account_raw["balance"]),
            equity=float(account_raw["equity"]),
            daily_starting_equity=self._starting_equity(
                session, float(account_raw["equity"])
            ),
        )

        decision = self.risk_engine.evaluate_trade(trade, account, open_positions)
        summary["reason"] = decision.reason
        if not decision.approved:
            self._record(
                session, entry_id, candle_time, signal, "REJECTED",
                decision.reason, trade, decision.adjusted_volume,
            )
            summary["decision"] = "REJECTED"
            return summary

        summary["volume"] = decision.adjusted_volume
        if self.dry_run:
            self._record(
                session, entry_id, candle_time, signal, "DRY_RUN",
                decision.reason, trade, decision.adjusted_volume,
            )
            summary["decision"] = "DRY_RUN"
            return summary

        try:
            placed = self.broker.place_market_order(
                trade.symbol,
                trade.direction,
                decision.adjusted_volume,
                stop_loss=trade.stop_loss,
                take_profit=trade.take_profit,
            )
        except BrokerError as exc:
            reason = f"Order placement rejected: {exc}"
            self._record(
                session, entry_id, candle_time, signal, "FAILED",
                reason, trade, decision.adjusted_volume,
            )
            summary["decision"] = "FAILED"
            summary["reason"] = reason
            return summary

        self._record(
            session, entry_id, candle_time, signal, "PLACED",
            decision.reason, trade, decision.adjusted_volume,
            placed.get("order"),
        )
        summary["decision"] = "PLACED"
        summary["ticket"] = placed.get("order")
        return summary

    # --- Run loop ----------------------------------------------------------

    def run(self) -> None:
        import signal as _signal

        stop = {"stop": False}

        def _handle_signal(signum, _frame):
            logger.info("Signal %s received — stopping decision engine.", signum)
            stop["stop"] = True

        _signal.signal(_signal.SIGINT, _handle_signal)
        _signal.signal(_signal.SIGTERM, _handle_signal)

        logger.info(
            "Decision engine starting: symbol=%s timeframe=%s mode=%s strategy=%s stream=%s",
            self.symbol,
            self.timeframe,
            "DRY RUN (no orders)" if self.dry_run else "LIVE TRADING",
            self.strategy.__class__.__name__,
            self.stream,
        )

        while not stop["stop"]:
            try:
                for result in self.process_stream(block_ms=0):
                    if result["decision"] in ("DRY_RUN", "PLACED", "REJECTED", "FAILED"):
                        logger.info(
                            "signal=%s decision=%s volume=%s reason=%s",
                            result.get("signal"),
                            result["decision"],
                            result.get("volume"),
                            result.get("reason"),
                        )
            except Exception as exc:
                logger.error("Decision engine poll errored: %s", exc, exc_info=True)
            time.sleep(self.poll_interval)

        logger.info("Decision engine stopped.")