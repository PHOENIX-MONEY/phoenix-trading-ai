# PHOENIX — PROJECT STATE
Last updated: 2026-09-15

## 1. What exists right now
- `backend` — FastAPI (Python 3.11) package layout `app/`, exposing `GET /health` (`{"status":"ok","service":"phoenix-backend"}`). No auth, no trading API routes yet.
  - `app/broker/` — **Phase 1 (built, live-tested)**: `BrokerClient` using the official **native `MetaTrader5` Python library** (`connect()` → `mt5.initialize(login, password, server)`; mounts `MT5_TIMEOUT_MS`). Methods: `connect`/`disconnect`, `get_account_info`, `get_symbol_price`, `get_candles`, `get_open_positions`, `place_market_order` (returns ticket; **not yet wired into any trading flow**), plus graceful `BrokerError`; import is guarded so the Linux Docker image runs without it (`available: False`). `app/broker/timeframes.py` maps `M1…MN1` ↔ MT5 constants and validates.
  - `app/market_data/` — **Phase 2 (built, live-tested)**: `MarketDataEngine` (`run` loop w/ `stop_event`, exponential backoff to `MAX_BACKOFF_SEC=300`; `poll_once`) fetches candles per configured timeframes, normalizes broker rows (`_to_storable`, `volume` from `tick_volume`), upserts via `app/market_data/storage.py` (`upsert_candles` — check-then-insert, `IntegrityError`-safe, returns the inserted rows), and publishes each new candle to the Redis stream `phoenix:candles:new` (fields: `symbol`, `timeframe`, `timestamp`).
  - `app/models.py` + `app/db.py` — SQLAlchemy `Base`, `Candle` (unique `(symbol, timeframe, timestamp)`), `engine`/`SessionLocal`/`get_session` from `DATABASE_URL`.
  - `app/risk/` — **Risk Engine (Phase 3, built)**: `RiskEngine` (deterministic, auditable, zero dependency on strategy/AI code), plain dataclasses (`ProposedTrade`, `OpenPosition`, `AccountInfo`, `RiskDecision`). Enforces limits, owns ALL position sizing, daily-loss halt, file-flag kill switch.
  - `app/config.py` — central env accessor. **Does NOT hard-require risk vars at import anymore** (lenient): enforcement lives only in `RiskEngine.__init__`, which raises a clear `ValueError` if any of `RISK_MAX_PER_TRADE_PCT`, `RISK_MAX_DAILY_LOSS_PCT`, `RISK_MAX_CONCURRENT_POSITIONS` is unset/negative/etc. Needed so broker/market-data tooling can run without risk limits in place.
- **Storage/migrations**: Alembic configured (`alembic.ini`, `migrations/`); single migration `0001_create_candles_table.py`; the Docker backend runs `alembic upgrade head` before starting uvicorn (idempotent on re-runs).
- `backend/tests/` — **59 pytest tests, all passing** (`test_risk_engine.py` 33, `test_market_data.py` 11, `test_broker_client.py` 15; FakeMT5 monkeypatch + SQLite in-memory + StubRedis).
- `backend/scripts/test_broker_connection.py` — **live-tested 2026-09-15**: logged into the FxPro-MT5 Demo terminal (login 592079091), printed account balance/equity ($500), lived EURUSD bid/ask, last 5 D1 candles. `backend/scripts/run_market_data_engine.py` — long-lived Click/Python loop for ingesting live candles (Ctrl+C stop).
- `frontend` — placeholder-only Vite + React + TypeScript scaffold.
- `infra` — Docker Compose: `backend` (built from `/backend`, port 8000), `postgres` (postgres:16-alpine, port 5432, healthcheck `pg_isready`, named volume `phoenix-postgres-data`), `redis` (redis:7-alpine, port 6379, named volume `phoenix-redis-data`), single network `phoenix-net`. Backend env overrides `DATABASE_URL`/`REDIS_URL` to point at the compose services.
- Root `.env.example`, `.gitignore`, `README.md`; local dev `.env` (real demo MT5 creds + risk limits, gitignored).

## 2. Repo structure
```
phoenix-trading-ai/
├── .env                     (gitignored — local values incl. demo MT5 creds)
├── .env.example             (committed template)
├── .gitignore
├── README.md
├── LICENSE
├── backend/
│   ├── Dockerfile           (python:3.11-slim, alembic upgrade head && uvicorn, EXPOSE 8000)
│   ├── .dockerignore        (excludes tests/, pytest.ini, .env, caches)
│   ├── requirements.txt     (fastapi, uvicorn, python-dotenv, SQLAlchemy, alembic, psycopg2-binary, redis — pinned)
│   ├── requirements-dev.txt (adds pytest==8.3.4)
│   ├── requirements-mt5.txt (adds MetaTrader5==5.0.6180 — NOTE: 5.0.84 does not exist on PyPI)
│   ├── pytest.ini           (testpaths=tests, pythonpath=.)
│   ├── alembic.ini
│   ├── migrations/
│   │   ├── env.py
│   │   └── versions/0001_create_candles_table.py
│   ├── scripts/
│   │   ├── test_broker_connection.py
│   │   └── run_market_data_engine.py
│   ├── tests/
│   │   ├── test_risk_engine.py   (33 tests)
│   │   ├── test_market_data.py   (11 tests)
│   │   └── test_broker_client.py (15 tests)
│   └── app/
│       ├── __init__.py
│       ├── config.py        (lenient env accessor; risk enforcement lives in RiskEngine)
│       ├── main.py          (FastAPI app + GET /health)
│       ├── models.py        (Base, Candle)
│       ├── db.py            (engine, SessionLocal, get_session)
│       ├── broker/
│       │   ├── __init__.py  (exports BrokerClient, BrokerError, timeframes)
│       │   ├── broker_client.py
│       │   └── timeframes.py
│       ├── market_data/
│       │   ├── __init__.py  (exports MarketDataEngine, PollResult)
│       │   ├── engine.py
│       │   └── storage.py
│       └── risk/
│           ├── __init__.py  (exports RiskEngine + models)
│           ├── models.py    (ProposedTrade, OpenPosition, AccountInfo, RiskDecision)
│           └── risk_engine.py
├── frontend/                (placeholder — Vite + React + TS)
├── infra/
│   └── docker-compose.yml   (services: backend, postgres, redis; network phoenix-net)
└── docs/
    └── PROJECT_STATE.md     (this file)
```

## 3. Environment variables required
All are read by the backend (compose injects via `env_file: ../.env`, overriding `DATABASE_URL`/`REDIS_URL` for containers). `.env` is gitignored.

| Variable | Purpose | Set? |
|----------|---------|------|
| `DATABASE_URL` | Postgres connection string. Native runs use `localhost:5432` (compose publishes it); the backend container uses `@postgres:5432`. | YES (local dev) |
| `REDIS_URL` | Redis connection string. Native runs use `localhost:6379` (compose publishes it); container uses `@redis:6379`. | YES (local dev) |
| `PRIMARY_SYMBOL` | Default instrument (e.g. `EURUSD`) | YES |
| `MT5_LOGIN` / `MT5_PASSWORD` / `MT5_SERVER` | MT5 demo terminal creds (`FxPro-MT5 Demo`) | YES (real demo values) |
| `MT5_TIMEOUT_MS` | `initialize()` timeout (default if unset) | optional |
| `MARKET_DATA_TIMEFRAMES` | Comma-separated (e.g. `M15,H1,H4`) | YES |
| `MARKET_DATA_POLL_INTERVAL_SEC` | Engine poll cadence (default 30) | YES |
| `MARKET_DATA_FETCH_COUNT` | Candles fetched per timeframe per poll (default 200) | YES |
| `CANDLE_STREAM` | Redis stream key for new candles (default `phoenix:candles:new`) | YES |
| `RISK_MAX_PER_TRADE_PCT` | Max % equity risked per trade — NO default; `RiskEngine.__init__` raises `ValueError` if unset/invalid | YES (1.0) |
| `RISK_MAX_DAILY_LOSS_PCT` | Max % of day's starting equity lost before auto-halt — no default | YES (3.0) |
| `RISK_MAX_CONCURRENT_POSITIONS` | Max concurrent open positions — no default | YES (3) |
| `RISK_KILL_SWITCH_PATH` | Kill-switch flag file path; `1/true/yes/on` halt all trading; empty = disabled | YES (empty) |

## 4. Manual tasks pending (before Phase 4)
1. **Decide/confirm your real risk numbers and check `.env`.** Current local values: `1.0` / `3.0` / `3` (conservative starting points). These will be enforced once Phase 4 wires trading.
2. **Use a demo account for automated trading.** Current login is a demo (`FxPro-MT5 Demo`, $500). Test with it; only after Phase 4 is proven, consider real values — never ship real creds in `.env`.
3. **Commit the Phase 1/2 work.** The repo still has only the initial commit; `git status` shows everything untracked. Commit before building on top.
4. Decide the frontend dependency-pinning policy before real frontend work (currently loose `^` ranges).

## 5. How to run this right now
Tests (run from `/backend`; `.env` not needed — config is lenient and tests fake broker/DB):
```powershell
pip install -r backend\requirements-dev.txt
..\venv\Scripts\python.exe -m pytest            # expect 59 passed
```
Infra (Postgres + Redis + API):
```powershell
Copy-Item .env.example .env          # once
docker compose -f infra/docker-compose.yml up --build     # or ... up -d for detached
```
`alembic upgrade head` runs automatically on backend boot. Verify: `curl http://localhost:8000/health` → `{"status":"ok","service":"phoenix-backend"}`.
Native Windows (trading mode, MT5 terminal running + logged in):
```powershell
pip install -r backend\requirements-mt5.txt
python backend\scripts\test_broker_connection.py         # connectivity + account + candles
python backend\scripts\run_market_data_engine.py         # live ingestion loop (Ctrl+C to stop)
```

## 6. How to test what was just built
**Unit tests (authoritative):** `59 passed` — risk engine (33), market data engine/storage (11), broker client (15). Risk coverage unchanged from before (see git history): construction enforcement, approval/rejection, concurrent limit, daily loss, kill switch (incl. fail-closed on unreadable file), position sizing rounds **down** to 0.01 lots (never up), 3 hand-checkable sizing cases.
**Broker client (15 tests):** unavailable-guard across all methods, connect/symbol-price/account-info mapping, disconnect, candles identical-rows, open-positions → risk `OpenPosition` mapping, market-order placement returns ticket and raises `BrokerError(retcode)` on non-DONE, PIN request surfaces `BrokerError`.
**Market data (11 tests):** poll keeps client connected & disposes session, empty-frames fetch, insert-then-publish counts, second poll inserts nothing (idempotency), Redis publish failure tolerated, backoff not applied on success, brokers that fail mid-frame are handled.
**Manual math for sizing (unchanged):** `volume = equity×(pct/100) ÷ (|entry − SL| × 100_000)`, floored to 0.01. (a) $10k, 1%, 50-pip SL → **0.20**; (b) same, 100-pip SL → **0.10**; (c) $20k, 0.5%, 25-pip SL → **0.40**.
**Live integration (done 2026-09-15):** `test_broker_connection.py` printed real balance ($500, login 592079091), live EURUSD bid/ask, 5 D1 candles, `CONNECTION OK`. Then a real engine poll fetched 150 candles (50 × M15/H1/H4), inserted 150 into Docker Postgres, published 150 events to `phoenix:candles:new` in Docker Redis; a second poll inserted/published **0** (dedupe proven live). Verified via `psql` (`SELECT count(*) FROM candles` → 150) and `redis-cli XRANGE`.

## 7. Decisions log
- **Broker = native `MetaTrader5` Python lib, NOT MetaApi cloud.** Reconfirmed during Phase 1 build; no retries/cloud tokens anywhere. `test_broker_connection.py` succeeded against the user's own FxPro demo terminal.
- **Database = local Docker Postgres, NOT Supabase.** User decision (2026-09-15). `DATABASE_URL` points at the compose `postgres` service; native runs reuse it via published `localhost:5432`. Supabase vars removed from the project. Supabase env keys no longer exist in `.env.example`.
- **Redis port published (6379→6379)** in compose so native Windows engine runs share the same stream as the containerized backend. Postgres was already published (5432).
- **`MetaTrader5==5.0.6180`** pinned in `requirements-mt5.txt`. The earlier `5.0.84` pin **does not exist on PyPI**; 5.0.6180 is the newest available and installed cleanly on Python 3.12.
- **Market-data writes use check-then-insert + `IntegrityError` catch** (unique `(symbol, timeframe, timestamp)`) — idempotent across polls and concurrent writers; upsert returns only the rows actually inserted, which is exactly what gets published to Redis.
- **Broker rows normalized in the engine** (`_to_storable`): storage/publish contract is `open/high/low/close/volume`; `volume` defaults to `tick_volume` so MT5 and other feed shapes both work.
- **Migrations run inside the container on boot** (`alembic upgrade head && uvicorn`), gated by the Postgres healthcheck — schema is always current for the API image; native runs use the same `migrations/`.
- **`config.py` relaxed: no import-time raise on missing risk vars.** Enforcement lives solely in `RiskEngine.__init__` (raises `ValueError`). Rationale: broker/market-data tooling must run without a trading config; the system trading path still can't start without limits. Risk limits still have NO defaults anywhere.
- **Position sizing formula, kill-switch file semantics, daily-loss equity-threshold rules, `math.floor(volume*100+1e-6)/100` rounding** — unchanged from Phase 3 (documented above).
- Earlier decisions retained: pinned exact versions; slim Python 3.11 image; hand-written frontend scaffold; `env_file: ../.env` with compose override of DB/Redis URLs; local `.env` gitignored.

## 8. Known issues / TODO
- **Repo has only the initial commit; Phase 1/2 work is untracked** — commit it (manual task 3).
- Risk engine is built and tested but **not yet wired** into any execution flow; `place_market_order` exists but nothing calls it. That is Phase 4 (strategy → decision loop consuming the `phoenix:candles:new` stream).
- `MarketDataEngine` published data is not yet consumed by anything; consumer arrives in Phase 4.
- Live test left 150 real candles (M15/H1/H4, 2026-09-15) and 150 Redis events in the store — harmless seed data for the future backtester.
- Kill switch currently has no dashboard/API writer; only the file flag exists.
- No auth, no trading API routes, no async infra, no settings persistence.
- Frontend has no real UI, linting, or tests.
- MT5 trading requires the native Windows run + a running, logged-in MT5 desktop terminal — Docker mode is ingest/API only.