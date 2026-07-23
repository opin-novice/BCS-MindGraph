from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from pathlib import Path

from bcs.api.routes import router

app = FastAPI(title="BCS Batighor MCQ API", version="1.0.0")

app.include_router(router, prefix="/api/v1")

static_dir = Path(__file__).resolve().parent.parent.parent.parent / "static"
if static_dir.exists():
    app.mount("/", StaticFiles(directory=str(static_dir), html=True), name="static")
