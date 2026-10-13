"""Stock Monitor API: app setup.

Creates the FastAPI app, adds CORS and Google sign-in middleware, includes the
endpoint routers, and serves the built React frontend. The endpoints live in
backend/routers/ and the logic they call in the other backend modules.
"""

from __future__ import annotations

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from backend.auth import require_google_auth
from backend.config import BASE_DIR
from backend.routers import (
    fundamentals,
    performance,
    portfolio,
    prices,
    system,
    tickers,
    transactions,
)


app = FastAPI(title="Stock Monitor API", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5173",
        "http://127.0.0.1:5173",
        "http://localhost:5174",
        "http://127.0.0.1:5174",
        "http://localhost:3000",
        "http://127.0.0.1:3000",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Registered after CORS, exactly as the decorator was before, so the
# middleware order is unchanged.
app.middleware("http")(require_google_auth)

# Route order matters where two URL patterns can match the same request.
# Within each router the original order is kept (for example
# POST /api/transactions/import stays ahead of POST /api/transactions/{ticker}).
for module in (system, tickers, fundamentals, performance, prices, transactions, portfolio):
    app.include_router(module.router)

# The frontend catch-all must be registered after every API route.
static_dir = BASE_DIR / "backend" / "static"
assets_dir = static_dir / "assets"
index_file = static_dir / "index.html"

if static_dir.exists():
    if assets_dir.exists():
        app.mount("/assets", StaticFiles(directory=str(assets_dir)), name="assets")

    @app.get("/{rest_of_path:path}")
    async def serve_spa(rest_of_path: str):
        if index_file.exists():
            return FileResponse(
                str(index_file),
                headers={
                    "Cache-Control": "no-store, no-cache, must-revalidate",
                    "Pragma": "no-cache",
                    "Expires": "0",
                },
            )
        raise HTTPException(status_code=404, detail="Frontend not built")
