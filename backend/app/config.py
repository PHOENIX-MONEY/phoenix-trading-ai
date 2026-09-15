import os

from dotenv import load_dotenv

load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://phoenix:phoenix@localhost:5432/phoenix")
SUPABASE_URL = os.getenv("SUPABASE_URL", "")
SUPABASE_SERVICE_ROLE_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY", "")

REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")

MT5_LOGIN = os.getenv("MT5_LOGIN", "")
MT5_PASSWORD = os.getenv("MT5_PASSWORD", "")
MT5_SERVER = os.getenv("MT5_SERVER", "")

PRIMARY_SYMBOL = os.getenv("PRIMARY_SYMBOL", "EURUSD")

# --- Market data engine (Phase 2) ---
# Comma-separated list of MT5 timeframes to ingest (e.g. "M15,H1,H4").
MARKET_DATA_TIMEFRAMES = os.getenv("MARKET_DATA_TIMEFRAMES", "M15,H1,H4")
# Polling interval in seconds for the market data engine.
MARKET_DATA_POLL_INTERVAL_SEC = int(os.getenv("MARKET_DATA_POLL_INTERVAL_SEC", "30"))
# Redis stream name used to publish new-candle events.
CANDLE_STREAM = os.getenv("CANDLE_STREAM", "phoenix:candles:new")
# How many candles to fetch per symbol/timeframe each poll.
MARKET_DATA_FETCH_COUNT = int(os.getenv("MARKET_DATA_FETCH_COUNT", "200"))

# --- Risk Engine ---
# Enforcement lives in RiskEngine.__init__ (raises ValueError if unset/invalid).
# These have NO defaults on purpose: risk limits must be an explicit choice.
RISK_MAX_PER_TRADE_PCT = os.getenv("RISK_MAX_PER_TRADE_PCT")
RISK_MAX_DAILY_LOSS_PCT = os.getenv("RISK_MAX_DAILY_LOSS_PCT")
RISK_MAX_CONCURRENT_POSITIONS = os.getenv("RISK_MAX_CONCURRENT_POSITIONS")
RISK_KILL_SWITCH_PATH = os.getenv("RISK_KILL_SWITCH_PATH", "")

# --- Strategy / Decision engine (Phase 4) ---
# Moving-average crossover parameters. The engine has a safe default:
# STRATEGY_DRY_RUN defaults to true unless explicitly set to false.
STRATEGY_FAST_PERIOD = int(os.getenv("STRATEGY_FAST_PERIOD", "10"))
STRATEGY_SLOW_PERIOD = int(os.getenv("STRATEGY_SLOW_PERIOD", "30"))
STRATEGY_STOP_LOSS_PIPS = float(os.getenv("STRATEGY_STOP_LOSS_PIPS", "50"))
STRATEGY_TAKE_PROFIT_PIPS = float(os.getenv("STRATEGY_TAKE_PROFIT_PIPS", "100"))
# Pip value of the traded symbol (0.0001 for 5-digit FX pairs like EURUSD).
STRATEGY_PIP_SIZE = float(os.getenv("STRATEGY_PIP_SIZE", "0.0001"))
# How many recent candles to load from Postgres for each evaluation.
STRATEGY_LOOKBACK = int(os.getenv("STRATEGY_LOOKBACK", "200"))
STRATEGY_TIMEFRAME = os.getenv("STRATEGY_TIMEFRAME", "H1")
STRATEGY_DRY_RUN = os.getenv("STRATEGY_DRY_RUN", "true").strip().lower() not in (
    "0",
    "false",
    "no",
)