"""Phase 1 verification script: connect to the local MT5 terminal via the
native MetaTrader5 bridge and print live account + market data.

Run from /backend:
    python scripts/test_broker_connection.py
    (or) python -m scripts.test_broker_connection

Requires: a Windows machine, MetaTrader5 pip package (requirements-mt5.txt),
a running + logged-in MT5 desktop terminal, and valid MT5_* vars in .env.
"""

import os
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.broker import BrokerClient  # noqa: E402
from app import config  # noqa: E402


def main() -> int:
    broker = BrokerClient()
    print(f"Broker available: {broker.available}")
    print(f"Symbol: {broker.symbol}")

    if not broker.available:
        print(
            "\nERROR: MetaTrader5 package not installed / not on Windows.\n"
            "Install it with:  pip install -r requirements-mt5.txt\n"
            "and run natively on Windows next to a logged-in MT5 terminal."
        )
        return 1

    try:
        broker.connect()
    except Exception as exc:
        print(f"\nERROR connecting: {exc}")
        print(
            "\nCheck that:\n"
            "  1. The MT5 desktop terminal is OPEN and logged into the demo "
            "account.\n"
            "  2. MT5_LOGIN / MT5_PASSWORD / MT5_SERVER in .env are correct.\n"
            "  3. 'AutoTrading' is enabled in MT5 (Tools > Options > Expert "
            "Advisors) — needed later for paper/live trading."
        )
        return 1

    try:
        info = broker.get_account_info()
        print("\n--- Account ---")
        print(f"  Login      : {info['login']}")
        print(f"  Currency   : {info['currency']}")
        print(f"  Balance    : {info['balance']:.2f}")
        print(f"  Equity     : {info['equity']:.2f}")
        print(f"  Margin     : {info['margin']:.2f} (free: {info['margin_free']:.2f})")

        tick = broker.get_symbol_price()
        print("\n--- Market price ---")
        print(f"  {tick['symbol']}  bid={tick['bid']:.5f}  ask={tick['ask']:.5f}  "
              f"@ {tick['time'].isoformat()}")

        candles = broker.get_candles(timeframe="D1", count=5)
        print(f"\n--- Last {len(candles)} daily candles ---")
        for c in candles:
            ts = c["timestamp"]
            print(
                f"  {ts:%Y-%m-%d}  O={c['open']:.5f}  H={c['high']:.5f}  "
                f"L={c['low']:.5f}  C={c['close']:.5f}  V={c['tick_volume']:.0f}"
            )

        print("\nCONNECTION OK — broker connectivity layer works end to end.")
        return 0
    finally:
        broker.disconnect()


if __name__ == "__main__":
    sys.exit(main())