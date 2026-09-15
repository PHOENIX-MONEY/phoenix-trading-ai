# PHOENIX — PROJECT STATE
Last updated: 2026-09-15

## 1. What exists right now
- `backend` — FastAPI (Python 3.11) package layout `app/`, exposing:
  - `GET /health` (`{"status":"ok","service":"phoenix-backend"}`)
  - `GET /trading/decisions?limit=20` — most recent decision-engine outcomes (read-only audit log)
- **Phase 1 — broker connectivity** (`app/broker/`): `BrokerClient` using the official **native `MetaTrader5` Python library** (`connect()` → `mt5.initialize(...)`). Methods: `connect`/`disconnect`, `get_account_info`, `get_symbol_price`, `get_candles`, `get_open_positions`, `place_market_order` (now **wired in Phase 4** as the final execution path), plus `BrokerError`; guarded import runs on Linux. `app/broker/timeframes.py` provides `canonical_timeframe` (string-only, platform-safe) and `validate_timeframe` (MT5 constant for broker calls).
- **Phase 2 — market data** (`app/market_data/`): `MarketDataEngine` polls broker, normalizes rows (`_to_storable`), upserts to Postgres (`upsert_candles` — check-then-insert, idempotent), publishes new candles to Redis stream `phoenix:candles:new`.
- **Phase 3 — risk engine** (`app/risk/`): `RiskEngine` — deterministic, auditable, zero coupling to strategy. Owns ALL position sizing via `evaluate_trade`. Enforces three mandatory limits (no defaults), daily-loss halt, kill switch.
- **Phase 4 — strategy + decision loop** (`app/strategy/`, `app/decision/`):
  - `MovingAverageCrossover`: pure SMA golden/death-cross signal generator; fully **stateless** (replaying the same candle window always yields the same signal).
  - `DecisionEngine`: consumes new-candle events from Redis → strategy → RiskEngine → (dry-run log | real market order) via broker. Every event is written to the `decisions` table (unique `stream_id`); replayed events are safely skipped. Auto-connects the broker when a signal fires (`_ensure_broker`). Defaults to **DRY RUN** (`STRATEGY_DRY_RUN=true`).
- `app/models.py` — SQLAlchemy models: `Candle` (unique `(symbol, timeframe, timestamp)`), `Decision` (audit log, unique `stream_id`), `EngineState` (key/value state; currently stores day-start equity).
- `app/db.py` — `engine`/`SessionLocal`/`get_session` from `DATABASE_URL`.
- `app/config.py` — central env accessor. Lenient: no import-time raise on missing risk vars; strategy vars have safe defaults (`DRY_RUN=true` is the critical one).
- **Migrations**: Alembic; `0001_create_candles_table.py`, `0002_create_decisions_and_state.py`. Docker runs `alembic upgrade head` on boot (idempotent).
- **Tests — 75 pytest tests, all passing**: risk engine (33), market data (11), broker client (15), strategy (6), decision engine (10). All run against SQLite in-memory + a `StubRedis`; no real infra needed.
- **Scripts**: `test_broker_connection.py` (live-tested), `run_market_data_engine.py` (candle ingestion loop), `run_decision_engine.py` (live decision loop, dry-run by default).
- `frontend` — placeholder Vite + React + TypeScript scaffold.
- `infra` — Docker Compose: `backend` (port 8000, alembic-on-boot), `postgres` (postgres:16-alpine, port 5432, healthcheck), `redis` (redis:7-alpine, port 6379), single network `phoenix-net`. Native runs share Postgres/Redis via published ports.
- Root `.env.example`, `.gitignore`, `README.md`; local dev `.env` (real demo MT5 creds + risk + strategy vars, gitignored).

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
│   ├── requirements-mt5.txt (adds MetaTrader5==5.0.6180)
│   ├── pytest.ini           (testpaths=tests, pythonpath=.)
│   ├── alembic.ini
│   ├── migrations/
│   │   ├── env.py
│   │   ├── versions/0001_create_candles_table.py
│   │   └── versions/0002_create_decisions_and_state.py
│   ├── scripts/
│   │   ├── test_broker_connection.py
│   │   ├── run_market_data_engine.py
│   │   └── run_decision_engine.py
│   ├── tests/
│   │   ├── test_risk_engine.py         (33 tests)
│   │   ├── test_market_data.py         (11 tests)
│   │   ├── test_broker_client.py       (15 tests)
│   │   ├── test_strategy_moving_average.py (6 tests)
│   │   └── test_decision_engine.py     (10 tests)
│   └── app/
│       ├── __init__.py
│       ├── config.py        (lenient env; risk enforcement in RiskEngine; STRATEGY_* + DRY_RUN)
│       ├── main.py          (FastAPI app + /health + /trading/decisions)
│       ├── models.py        (Base, Candle, Decision, EngineState)
│       ├── db.py
│       ├── broker/
│       │   ├── __init__.py  (exports BrokerClient, BrokerError, canonical/validate_timeframe)
│       │   ├── broker_client.py
│       │   └── timeframes.py
│       ├── market_data/
│       │   ├── __init__.py
│       │   ├── engine.py
│       │   └── storage.py
│       ├── strategy/
│       │   ├── __init__.py  (exports Strategy, Signal, MovingAverageCrossover)
│       │   ├── base.py      (Strategy ABC + frozen Signal dataclass)
│       │   └── moving_average.py
│       ├── decision/
│       │   ├── __init__.py  (exports DecisionEngine)
│       │   └── engine.py    (stream consumer → strategy → risk → broker; DRY_RUN default)
│       └── risk/
│           ├── __init__.py
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
| `DATABASE_URL` | Postgres connection string (native: `localhost:5432`; container: `@postgres:5432`) | YES |
| `REDIS_URL` | Redis connection string (native: `localhost:6379`; container: `@redis:6379`) | YES |
| `PRIMARY_SYMBOL` | Default instrument (e.g. `EURUSD`) | YES |
| `MT5_LOGIN` / `MT5_PASSWORD` / `MT5_SERVER` | MT5 demo terminal creds (`FxPro-MT5 Demo`) | YES (real demo values) |
| `MT5_TIMEOUT_MS` | `initialize()` timeout (optional) | optional |
| `MARKET_DATA_TIMEFRAMES` | Comma-separated (e.g. `M15,H1,H4`) | YES |
| `MARKET_DATA_POLL_INTERVAL_SEC` | Market-data engine poll cadence (default 30) | YES |
| `MARKET_DATA_FETCH_COUNT` | Candles fetched per timeframe each poll (default 200) | YES |
| `CANDLE_STREAM` | Redis stream for new-candle events (default `phoenix:candles:new`) | YES |
| `RISK_MAX_PER_TRADE_PCT` | Max % equity risked per trade — **NO default**; raises `ValueError` if unset | YES (1.0) |
| `RISK_MAX_DAILY_LOSS_PCT` | Max % day-start equity lost before auto-halt — no default | YES (3.0) |
| `RISK_MAX_CONCURRENT_POSITIONS` | Max concurrent open positions — no default | YES (3) |
| `RISK_KILL_SWITCH_PATH` | Kill-switch flag file path; `1/true/yes/on` = halt; empty = disabled | YES (empty) |
| `STRATEGY_FAST_PERIOD` | Fast SMA period (default 10) | YES |
| `STRATEGY_SLOW_PERIOD` | Slow SMA period (default 30; must be > fast) | YES |
| `STRATEGY_STOP_LOSS_PIPS` | Signal stop-loss distance (default 50) | YES |
| `STRATEGY_TAKE_PROFIT_PIPS` | Signal take-profit distance (default 100) | YES |
| `STRATEGY_PIP_SIZE` | Pip value of symbol (0.0001 for 5-digit FX) | YES |
| `STRATEGY_LOOKBACK` | Candles loaded from Postgres for each eval (default 200) | YES |
| `STRATEGY_TIMEFRAME` | Timeframe the decision engine trades (default H1) | YES |
| `STRATEGY_DRY_RUN` | **SAFETY**: defaults to `true`. Set `false` only when ready to trade live | YES |

## 4. Manual tasks pending (before Phase 5 / going live)
1. **Decide/confirm your real risk numbers and check `.env`.** Current values: `1.0` / `3.0` / `3`. They are now live — the decision engine actually uses them.
2. **Only set `STRATEGY_DRY_RUN=false` when you are ready.** Defaults to `true`; the engine logs what it would do. The risk engine + broker integration has been proven on the FxPro demo account in dry-run mode.
3. **Run both engines together for live operation**: `run_market_data_engine.py` (candle ingestion, Ctrl+C stop) and `run_decision_engine.py` (decision loop, Ctrl+C stop) on native Windows with the MT5 terminal running. They share the same Postgres/Redis stack.
4. Decide the frontend dependency-pinning policy before real frontend work (currently loose `^` ranges).

## 5. How to run this right now
**Tests** (from `/backend`, no infra needed):
```powershell
pip install -r backend\requirements-dev.txt
..\venv\Scripts\python.exe -m pytest            # expect 75 passed
```
**Infra** (Postgres + Redis + API):
```powershell
Copy-Item .env.example .env          # once; fill in real values
docker compose -f infra/docker-compose.yml up --build -d
curl http://localhost:8000/health     # -> {"status":"ok","service":"phoenix-backend"}
curl http://localhost:8000/trading/decisions  # -> latest decision rows
```
`alembic upgrade head` runs automatically on backend boot.
**Native Windows** (live mode, MT5 terminal running + logged in):
```powershell
pip install -r backend\requirements-mt5.txt
python backend\scripts\test_broker_connection.py     # verify live connectivity
python backend\scripts\run_market_data_engine.py     # candle ingestion loop
python backend\scripts\run_decision_engine.py        # decision/trading loop (DRY RUN by default)
```

## 6. How to test what was just built
**Unit tests (authoritative):** `75 passed` — risk engine (33), market data (11), broker client (15), MA crossover (6), decision engine (10).
- **Risk (33):** construction refusal, approval, concurrent limit, daily loss, kill switch (incl. fail-closed on unreadable file), sizing rounds **down** to 0.01 lots (never up), 3 hand-checkable sizing cases.
- **Broker (15):** unavailable-guard, connect/price/account mapping, disconnect, candles, open-positions → risk model, place_market_order returns ticket and raises on non-DONE, PIN error.
- **Market data (11):** connected/disposed session, insert/publish counts, idempotent second poll, Redis failure tolerated, backoff, error-retry.
- **Strategy (6):** golden cross → BUY, death cross → SELL, no cross → None, insufficient data → None, idempotent replay (same window = same signal), rejects invalid periods.
- **Decision engine (10):** NO_SIGNAL logged, golden-cross DRY_RUN with correct volume (manual check: 0.20 lots on $10k / 0.01 on $500), rejected at concurrent limit, real order placed in `dry_run=false` mode, broker-error FAILED, SKIPPED without candles, ignores wrong symbol/timeframe, day-start equity recorded once, drains-then-watches only new, auto-connects broker.
- **Manual math for sizing (unchanged):** `volume = equity×(pct/100) ÷ (|entry − SL| × 100_000)`, floored to 0.01.

**Live integration (done 2026-09-15):**
- **Phase 1/2:** broker logged in ($500 demo), live M15/H1/H4 ingested (450 new candles), published to Redis.
- **Phase 4 — NO_SIGNAL path:** Real trigger published post-engine-start → real H1 candles loaded from Postgres → real strategy evaluated → `NO_SIGNAL` → decision row persisted → served by `GET /trading/decisions`.
- **Phase 4 — DRY_RUN approved path:** `AlwaysBuy` throwaway strategy fed real broker price/account → RiskEngine approved 0.01 lots ($5 risk on $500) → `DRY_RUN` logged → `place_market_order` never called → decision row persisted with real `entry_price`, `stop_loss`, `take_profit`, `volume` → served by API. `engine_state` row created (`daily_starting_equity:EURUSD:2026-09-15 = 500.0`).

## 7. Decisions log
- **Broker = native `MetaTrader5`, NOT MetaApi.** Live-tested against user's FxPro demo.
- **Database = local Docker Postgres, NOT Supabase.** Published ports share data between containers and native runs.
- **MetaTrader5==5.0.6180** pinned; the earlier 5.0.84 does not exist on PyPI.
- **Market-data upsert = check-then-insert + `IntegrityError` catch** — idempotent; returns only newly inserted rows (what gets published to Redis).
- **Broker rows normalized in the engine** (`_to_storable`): `volume` defaults to `tick_volume`.
- **Migrations run on container boot** (`alembic upgrade head && uvicorn`), gated by Postgres healthcheck.
- **`config.py` lenient by design** — enforcement lives only in `RiskEngine.__init__` (raises `ValueError`); broker/market-data/strategy tooling can run without risk limits in place.
- **Position sizing: `math.floor(volume*100+1e-6)/100`** avoids float rounding edge (0.2 → 0.20 not 0.19).
- **Phase 4 strategy is fully stateless** — no internal MA state; re-evaluates both the current and previous windows from the candle list. This keeps the decision idempotent and safe to replay (Phase 5 backtesting).
- **DecisionEngine uses `canonical_timeframe` (string), not `validate_timeframe` (MT5 constant)** — the stream, DB, and comparison logic work on the string; only the broker's `get_candles` needs the numeric constant.
- **DRY_RUN defaults to `true`** — the engine logs what it would do. Live orders require `STRATEGY_DRY_RUN=false` in `.env`.
- **`_ensure_broker`** called lazily when a signal fires; avoids an extra connection when strategy says NO_SIGNAL.
- **Day-start equity tracked in `engine_state`** table per (symbol, UTC date); seeded from live equity on first event of each trading day; used by the daily-loss halt.
- Earlier decisions retained: pinned exact versions; slim image; frontend scaffold; compose env override; `.env` gitignored.

## 8. Known issues / TODO
- **Phase 5 — backtesting**: replay stored candles offline (no MT5 terminal needed) to evaluate strategy performance across history; the stateless strategy design is ready for this.
- **No consumer group** on the Redis stream — single `DecisionEngine` instance only. A consumer group would let multiple engine instances share load (future work).
- Kill switch has no dashboard/API writer; only the file flag exists.
- No auth, no async infra, no settings persistence beyond the file flag and `engine_state` table.
- Frontend has no real UI, linting, or tests.
- MT5 trading requires native Windows + running, logged-in MT5 terminal — Docker mode is API/read-only.
