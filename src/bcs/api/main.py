import os
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from pathlib import Path
from typing import Optional

from bcs.api.routes import router
from bcs.api.rate_limiter import check_rate_limit
from bcs.api.auth import verify_access_token

app = FastAPI(title="BCS Batighor MCQ API", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def rate_limit_middleware(request: Request, call_next):
    forwarded = request.headers.get("X-Forwarded-For")
    ip = forwarded.split(",")[0].strip() if forwarded else (request.client.host if request.client else "unknown")
    path = request.url.path

    # Extract user_id from Bearer token if present (for per-user rate limits)
    user_id: Optional[str] = None
    auth_header = request.headers.get("Authorization", "")
    if auth_header.startswith("Bearer "):
        try:
            payload = verify_access_token(auth_header[7:])
            user_id = payload.get("sub")
        except Exception:
            pass

    if path.startswith("/api/v1/generate"):
        zone = "generate"
    elif path.startswith("/api/v1/feedback"):
        zone = "feedback"
    elif path.startswith("/api/v1/"):
        zone = "default"
    else:
        return await call_next(request)

    allowed, retry_after = check_rate_limit(ip, zone, user_id=user_id)
    if not allowed:
        return JSONResponse(
            status_code=429,
            content={"detail": f"অনুরোধের সীমা অতিক্রান্ত। {retry_after} সেকেন্ড পর আবার চেষ্টা করুন।"},
            headers={"Retry-After": str(retry_after)},
        )

    return await call_next(request)


app.include_router(router, prefix="/api/v1")

static_dir = Path(__file__).resolve().parent.parent.parent.parent / "static"
if static_dir.exists():
    app.mount("/", StaticFiles(directory=str(static_dir), html=True), name="static")
