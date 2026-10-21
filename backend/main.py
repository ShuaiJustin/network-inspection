import asyncio
from contextlib import asynccontextmanager, suppress
import hashlib
import hmac
import os
import secrets

from fastapi import Depends, FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.db import SessionLocal, init_db
from app.routers import adapters, auth, backup, catalog, devices, excel, logs, screenshots, settings, system, tasks
from app.routers.auth import require_session
from app.services.maintenance import database_access_operation
from app.services.log_maintenance import run_activity_log_cleanup
from app.services.raw_output_maintenance import run_raw_output_cleanup


RAW_OUTPUT_CLEANUP_INTERVAL_SECONDS = 24 * 60 * 60
HEALTH_NONCE_BYTES = 32

async def _raw_output_cleanup_loop(
    *,
    interval_seconds: float = RAW_OUTPUT_CLEANUP_INTERVAL_SECONDS,
    cleanup=run_raw_output_cleanup,
    session_factory=SessionLocal,
) -> None:
    while True:
        await asyncio.sleep(interval_seconds)
        await asyncio.to_thread(cleanup, session_factory)


async def _activity_log_cleanup_loop(
    *,
    interval_seconds: float = RAW_OUTPUT_CLEANUP_INTERVAL_SECONDS,
    cleanup=run_activity_log_cleanup,
    session_factory=SessionLocal,
) -> None:
    while True:
        await asyncio.sleep(interval_seconds)
        await asyncio.to_thread(cleanup, session_factory)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    cleanup_tasks = [
        asyncio.create_task(_raw_output_cleanup_loop()),
        asyncio.create_task(_activity_log_cleanup_loop()),
    ]
    try:
        yield
    finally:
        for cleanup_task in cleanup_tasks:
            cleanup_task.cancel()
        for cleanup_task in cleanup_tasks:
            with suppress(asyncio.CancelledError):
                await cleanup_task


app = FastAPI(title="Network Inspection Tool API", version="1.0.0", lifespan=lifespan)
app.state.runtime_token = os.environ.get("INSPECTION_RUNTIME_TOKEN") or None
init_db()

_SELF_GATED_ENDPOINTS = {
    ("POST", "/backup"),
    ("POST", "/system/database-restore"),
    ("POST", "/tasks"),
}


def configure_runtime_token(token: str | None) -> None:
    app.state.runtime_token = token or None


def _decode_health_nonce(nonce: str | None) -> bytes | None:
    if len(nonce or "") != HEALTH_NONCE_BYTES * 2:
        return None
    try:
        nonce_bytes = bytes.fromhex(nonce)
    except ValueError:
        return None
    return nonce_bytes if len(nonce_bytes) == HEALTH_NONCE_BYTES else None


@app.middleware("http")
async def require_runtime_token(request: Request, call_next):
    expected_token = getattr(request.app.state, "runtime_token", None)
    if request.method == "OPTIONS" or not expected_token:
        return await call_next(request)
    if request.url.path == "/health" and _decode_health_nonce(request.query_params.get("nonce")):
        return await call_next(request)

    provided_token = request.headers.get("X-Inspection-Run-Token", "")
    if not secrets.compare_digest(provided_token, expected_token):
        return JSONResponse(status_code=401, content={"detail": "Local runtime token is required"})

    return await call_next(request)


@app.middleware("http")
async def serialize_database_restore(request: Request, call_next):
    endpoint = (request.method, request.url.path.rstrip("/") or "/")
    if request.method == "OPTIONS" or request.url.path == "/health" or endpoint in _SELF_GATED_ENDPOINTS:
        return await call_next(request)
    async with database_access_operation():
        return await call_next(request)


app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "tauri://localhost",
        "https://tauri.localhost",
        "http://tauri.localhost",
        "http://localhost:5173",
        "http://127.0.0.1:5173",
        "http://localhost:5174",
        "http://127.0.0.1:5174",
    ],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth.router, prefix="/auth", tags=["auth"])
app.include_router(
    devices.router, prefix="/devices", tags=["devices"], dependencies=[Depends(require_session)]
)
app.include_router(
    tasks.router, prefix="/tasks", tags=["tasks"], dependencies=[Depends(require_session)]
)
app.include_router(
    excel.router, prefix="/excel", tags=["excel"], dependencies=[Depends(require_session)]
)
app.include_router(
    backup.router, prefix="/backup", tags=["backup"], dependencies=[Depends(require_session)]
)
app.include_router(
    logs.router, prefix="/logs", tags=["logs"], dependencies=[Depends(require_session)]
)
app.include_router(
    settings.router, prefix="/settings", tags=["settings"], dependencies=[Depends(require_session)]
)
app.include_router(
    system.router, prefix="/system", tags=["system"], dependencies=[Depends(require_session)]
)
app.include_router(
    screenshots.router, prefix="/screenshots", tags=["screenshots"], dependencies=[Depends(require_session)]
)
app.include_router(
    adapters.router, prefix="/adapters", tags=["adapters"], dependencies=[Depends(require_session)]
)
app.include_router(
    catalog.router, prefix="/catalog", tags=["catalog"], dependencies=[Depends(require_session)]
)


@app.get("/health")
def health(request: Request, nonce: str | None = None):
    runtime_token = getattr(request.app.state, "runtime_token", None)
    if not runtime_token or nonce is None:
        return {"status": "ok"}

    nonce_bytes = _decode_health_nonce(nonce)
    if nonce_bytes is None:
        return JSONResponse(
            status_code=400,
            content={"detail": "A 32-byte hexadecimal health nonce is required"},
        )

    proof = hmac.new(runtime_token.encode(), nonce_bytes, hashlib.sha256).hexdigest()
    return {"status": "ok", "proof": proof}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=8000)
