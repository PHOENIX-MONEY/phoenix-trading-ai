# Phoenix

Phoenix is an autonomous trading system built incrementally from one codebase: a FastAPI (Python 3.11) backend in `/backend`, a React + TypeScript frontend (placeholder), and everything orchestrated with Docker Compose from `/infra`. The broker bridge uses the native MetaTrader 5 Python library connected directly to a locally running MT5 desktop terminal on Windows (no MetaApi cloud subscription).

Live pipeline: **MT5 terminal → BrokerClient → MarketDataEngine → Postgres (`candles`) + Redis event stream → DecisionEngine → strategy (MA crossover) → RiskEngine → (dry-run log | real market order)**, with every outcome audited in the `decisions` table. Phases built: 0 (repo/infra), 1 (MT5 connectivity), 2 (market data ingestion + storage), 3 (risk engine), 4 (strategy + decision loop) — 75 unit tests passing.

## How to run

### Docker (Postgres + Redis + API)

Starts `postgres:16` (named volume `phoenix-postgres-data`), `redis:7` (named volume `phoenix-redis-data`), and the backend. On boot the backend runs `alembic upgrade head` automatically:

```powershell
Copy-Item .env.example .env          # once; then fill in your values
docker compose -f infra/docker-compose.yml up --build
```

Backend health: `GET http://localhost:8000/health` → `{"status":"ok","service":"phoenix-backend"}`.
Postgres is published on `localhost:5432` (user/pass `phoenix`/`phoenix`, db `phoenix` by default) and Redis on `localhost:6379`, so native Windows processes can use the same store.

### Native Windows (trading / MT5 bridge)

To talk to the local MT5 terminal, run with real Python on Windows (the MetaTrader5 package is Windows-only):

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r backend\requirements-mt5.txt
```

Make sure the MT5 desktop terminal is installed, logged in, and kept running. Verify connectivity end to end:

```powershell
python backend\scripts\test_broker_connection.py
```

Ingest live candles (writes to Postgres + publishes to Redis stream):

```powershell
python scripts\run_market_data_engine.py
```

Run the decision engine — the strategy+trading loop (Ctrl+C to stop):

```powershell
python scripts\run_decision_engine.py
```

`STRATEGY_DRY_RUN` defaults to `true` (safe). In dry-run the engine logs every trade it **would** make without sending real orders. Set `STRATEGY_DRY_RUN=false` in `.env` only when ready to trade live.

### Tests (from `/backend`)

```powershell
pip install -r backend\requirements-dev.txt
..\venv\Scripts\python.exe -m pytest
```

### Frontend (placeholder)

From `/frontend`: `npm install` then `npm run dev`.

## Layout

- `backend/app/broker/` — native MT5 `BrokerClient` (accounts, prices, candles, positions, market orders) + timeframe map.
- `backend/app/market_data/` — `MarketDataEngine` poll loop + Postgres upsert storage + Redis stream publish.
- `backend/app/strategy/` — `Strategy` ABC + `MovingAverageCrossover` (SMA golden/death cross).
- `backend/app/decision/` — `DecisionEngine` — the full loop: Redis stream → strategy → RiskEngine → broker; every outcome persisted.
- `backend/app/risk/` — `RiskEngine`: mandatory risk limits, position sizing, daily-loss halt, kill switch.
- `backend/app/models.py` — SQLAlchemy models: `Candle`, `Decision` (audit log), `EngineState` (day-start equity tracker).
- `backend/scripts/` — `test_broker_connection.py`, `run_market_data_engine.py`, `run_decision_engine.py`.
- `backend/migrations/` — Alembic migrations (0001: `candles`, 0002: `decisions` + `engine_state`).
- `infra/docker-compose.yml` — backend + postgres + redis on one network `phoenix-net`.