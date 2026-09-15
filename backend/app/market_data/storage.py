"""Persistence for candle data.

Upsert strategy: check the unique (symbol, timeframe, timestamp) key first,
insert only what is missing. The insert is additionally wrapped against
IntegrityError so even a concurrent duplicate can never crash the engine.
"""

from sqlalchemy.exc import IntegrityError

from app.models import Candle


def _row_to_payload(row: dict) -> dict:
    """Normalize a broker candle dict into a Candle model payload."""
    return {
        "symbol": row["symbol"],
        "timeframe": row["timeframe"],
        "timestamp": row["timestamp"],
        "open": row["open"],
        "high": row["high"],
        "low": row["low"],
        "close": row["close"],
        "volume": row["volume"],
    }


def _existing_keys(session, rows: list[dict]) -> set[tuple]:
    """Return the set of (symbol, timeframe, timestamp) already in the DB."""
    keys = {(r["symbol"], r["timeframe"], r["timestamp"]) for r in rows}
    if not keys:
        return set()

    found = set()
    for symbol, timeframe, timestamp in keys:
        exists = (
            session.query(Candle.id)
            .filter(
                Candle.symbol == symbol,
                Candle.timeframe == timeframe,
                Candle.timestamp == timestamp,
            )
            .first()
        )
        if exists is not None:
            found.add((symbol, timeframe, timestamp))
    return found


def upsert_candles(session, rows: list[dict]) -> list[dict]:
    """Insert only candles that are not already stored.

    Returns the list of newly inserted candle payloads (already committed).
    """
    rows = [_row_to_payload(r) for r in rows]
    existing = _existing_keys(session, rows)

    new_rows = [
        r for r in rows
        if (r["symbol"], r["timeframe"], r["timestamp"]) not in existing
    ]

    inserted: list[dict] = []
    for row in new_rows:
        candle = Candle(**row)
        session.add(candle)
        try:
            session.commit()
        except IntegrityError:
            # Lost a race with a concurrent writer inserting the same candle.
            session.rollback()
            continue
        inserted.append(row)

    return inserted