from fastapi import FastAPI

app = FastAPI(title="Phoenix Backend")


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "service": "phoenix-backend"}