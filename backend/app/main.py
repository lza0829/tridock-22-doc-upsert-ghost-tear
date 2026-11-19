from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import PROJECT_NAME
from app.routers import dashboard, lines, reconcile, settings
from app.seed import seed_if_empty

@asynccontextmanager
async def lifespan(_app: FastAPI):
    seed_if_empty()
    yield

app = FastAPI(title=PROJECT_NAME, lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)
app.include_router(dashboard.router, prefix="/api")
app.include_router(lines.router, prefix="/api")
app.include_router(reconcile.router, prefix="/api")
app.include_router(settings.router, prefix="/api")
