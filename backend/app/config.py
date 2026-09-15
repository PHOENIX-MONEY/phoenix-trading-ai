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