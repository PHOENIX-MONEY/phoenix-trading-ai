"""Phase 2 long-lived process: ingest candles into Postgres and publish
new-candle events to the Redis stream.

Run from /backend:
    python scripts/run_market_data_engine.py

Requires a reachable Postgres (docker compose 'postgres' service), Redis
(docker compose 'redis' service), and on Windows a running MT5 terminal to
actually fetch candle data. Ctrl+C to stop cleanly.
"""

import logging
import os
import signal
import sys
import threading
import time
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.market_data import MarketDataEngine  # noqa: E402
from app import config  # noqa: E402

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-7s [%(name)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("market-data-engine")


def main() -> int:
    engine = MarketDataEngine()

    logger.info(
        "Starting market data engine: symbol=%s timeframes=%s interval=%ss\n"
        "  postgres: %s\n  redis:    %s\n  stream:   %s",
        engine.symbol,
        ",".join(engine.timeframes),
        engine.poll_interval,
        config.DATABASE_URL,
        config.REDIS_URL,
        engine.stream,
    )

    stop_event = threading.Event()

    def _handle_signal(signum, _frame):
        logger.info("Signal %s received — shutting down.", signum)
        stop_event.set()

    signal.signal(signal.SIGINT, _handle_signal)
    signal.signal(signal.SIGTERM, _handle_signal)

    try:
        engine.run(stop_event=stop_event)
    finally:
        engine.stop()
        logger.info("Market data engine stopped at %s.", datetime.utcnow().isoformat())

    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        sys.exit(0)