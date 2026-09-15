from fastapi import FastAPI, Query

from app.db import SessionLocal
from app.models import Decision

app = FastAPI(title="Phoenix Backend")


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "service": "phoenix-backend"}


@app.get("/trading/decisions")
def recent_decisions(
    limit: int = Query(20, ge=1, le=200),
) -> dict:
    """Most recent decision-engine outcomes, newest first (audit log)."""
    with SessionLocal() as session:
        rows = (
            session.query(Decision)
            .order_by(Decision.processed_at.desc())
            .limit(limit)
            .all()
        )
        return {"count": len(rows), "decisions": [row.as_dict() for row in rows]}