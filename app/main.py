"""familink — FastAPI entrypoint. Starts the read-only MikroTik discovery
loop on startup (see app/sync.py) and mounts the admin panel + JSON API.
"""
from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles

from app import sync
from app.auth import BasicAuthMiddleware, warn_if_auth_disabled
from app.routers import devices, enforcement, groups, health

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")


@asynccontextmanager
async def lifespan(app: FastAPI):
    warn_if_auth_disabled()
    task = asyncio.create_task(sync.discovery_loop())
    yield
    task.cancel()


app = FastAPI(title="familink", lifespan=lifespan)
app.add_middleware(BasicAuthMiddleware)
app.mount("/static", StaticFiles(directory="app/static"), name="static")
app.include_router(devices.router)
app.include_router(groups.router)
app.include_router(enforcement.router)
app.include_router(health.router)


@app.get("/")
def root():
    return RedirectResponse("/devices")
