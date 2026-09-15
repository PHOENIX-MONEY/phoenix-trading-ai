"""Phase 4 long-lived process: the decision loop.

Redis stream (new candles) -> strategy -> RiskEngine -> broker.

Run from /backend:
    python scripts/run_decision_engine.py

Requires the docker compose stack (Postgres for candles + decisions, Redis for
the stream) and, on Windows, a running MT5 terminal to fetch live prices and
place orders.

SAFETY: STRATEGY_DRY_RUN defaults to true. In dry-run the engine logs every
trade it WOULD make without sending orders. Set STRATEGY_DRY_RUN=false in .env
only when you are ready to trade live. Ctrl+C to stop cleanly.
"""

import logging
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import config  # noqa: E402
from app.broker import BrokerClient  # noqa: E402
from app.decision import DecisionEngine  # noqa: E402
from app.risk import RiskEngine  # noqa: E402
from app.strategy import MovingAverageCrossover  # noqa: E402

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-7s [%(name)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("decision-engine")


def main() -> int:
    broker = BrokerClient(symbol=config.PRIMARY_SYMBOL)
    strategy = MovingAverageCrossover(
        fast_period=config.STRATEGY_FAST_PERIOD,
        slow_period=config.STRATEGY_SLOW_PERIOD,
        stop_loss_pips=config.STRATEGY_STOP_LOSS_PIPS,
        take_profit_pips=config.STRATEGY_TAKE_PROFIT_PIPS,
    )
    risk_engine = RiskEngine()
    engine = DecisionEngine(
        strategy=strategy,
        broker=broker,
        risk_engine=risk_engine,
        symbol=config.PRIMARY_SYMBOL,
        timeframe=config.STRATEGY_TIMEFRAME,
        dry_run=config.STRATEGY_DRY_RUN,
    )

    logger.info(
        "Strategy: %s (fast=%s slow=%s SL=%spips TP=%spips) | risk: %s%%/trade, "
        "%s%%/day, %s positions | mode: %s",
        strategy.__class__.__name__,
        strategy.fast_period,
        strategy.slow_period,
        strategy.stop_loss_pips,
        strategy.take_profit_pips,
        risk_engine.max_risk_per_trade_pct,
        risk_engine.max_daily_loss_pct,
        risk_engine.max_concurrent_positions,
        "DRY RUN" if config.STRATEGY_DRY_RUN else "LIVE",
    )

    try:
        engine.run()
    finally:
        logger.info(
            "Decision engine stopped at %s.", __import__("datetime").datetime.utcnow().isoformat()
        )

    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        sys.exit(0)