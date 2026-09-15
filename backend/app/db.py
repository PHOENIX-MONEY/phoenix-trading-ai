from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app import config

# pool_pre_ping avoids stale connections after idle/restarts.
engine = create_engine(config.DATABASE_URL, pool_pre_ping=True)
SessionLocal = sessionmaker(bind=engine, autocommit=False, autoflush=False)


def get_session():
    """Context-manager style session helper for scripts/services."""
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()