"""FastAPI layer — thin wiring only. If you're writing logic here, it belongs
in /core/agent instead (design doc §6). Step 2 adds clone/index endpoints."""

from fastapi import FastAPI

app = FastAPI(title="CodeGraph Agent")


@app.get("/health")
def health():
    return {"status": "ok"}