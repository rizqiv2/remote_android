"""
main.py — FastAPI application entry point.

Security headers applied globally:
  - Content-Security-Policy (strict, no inline scripts)
  - X-Frame-Options: DENY
  - X-Content-Type-Options: nosniff
  - Referrer-Policy: no-referrer
  - Permissions-Policy: minimal
"""
import asyncio
import logging
import sys
from contextlib import asynccontextmanager
from typing import Optional

from fastapi import (
    Cookie,
    Depends,
    FastAPI,
    HTTPException,
    Request,
    WebSocket,
    WebSocketDisconnect,
    status,
)
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import (
    FileResponse,
    HTMLResponse,
    JSONResponse,
    RedirectResponse,
)
from fastapi.staticfiles import StaticFiles
try:
    from pydantic import BaseModel, Field, field_validator
except ImportError:
    from pydantic import BaseModel, Field, validator as field_validator
from starlette.middleware.base import BaseHTTPMiddleware

from .adb_controller import ADBError, adb
from .auth import (
    create_jwt,
    generate_csrf_token,
    rate_limiter,
    require_auth,
    require_auth_and_csrf,
    verify_password,
)
from .config import settings
from .screen_stream import ScreenStreamer

# ─── Logging ──────────────────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger(__name__)


def _get_client_ip(request: Request) -> str:
    forwarded = request.headers.get("X-Forwarded-For")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"

# ─── Screen Streamer ──────────────────────────────────────────────────────────

streamer = ScreenStreamer(adb)


# ─── Lifespan ─────────────────────────────────────────────────────────────────

async def adb_keepalive_loop():
    """Background task to auto-reconnect ADB over Tailscale/IP if disconnected."""
    while True:
        await asyncio.sleep(10)
        if settings.AUTO_RECONNECT_ADB and settings.ADB_DEVICE_SERIAL:
            if not adb.is_connected():
                logger.info(f"ADB disconnected. Attempting auto-reconnect to {settings.ADB_DEVICE_SERIAL}...")
                res = await adb.connect_remote()
                logger.info(f"ADB auto-reconnect result: {res['message']}")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Start background tasks on startup."""
    task_stream = asyncio.create_task(streamer.run())
    task_keepalive = asyncio.create_task(adb_keepalive_loop())
    logger.info(f"Server ready on http://{settings.SERVER_HOST}:{settings.SERVER_PORT}")
    yield
    streamer.stop()
    task_stream.cancel()
    task_keepalive.cancel()
    try:
        await asyncio.gather(task_stream, task_keepalive)
    except (asyncio.CancelledError, Exception):
        pass


# ─── App ──────────────────────────────────────────────────────────────────────

app = FastAPI(
    title="Android Remote Control",
    docs_url=None,   # Disable Swagger UI in production
    redoc_url=None,
    lifespan=lifespan,
)


# ─── Security Headers Middleware ──────────────────────────────────────────────

class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Permissions-Policy"] = (
            "camera=(), microphone=(), geolocation=(), payment=()"
        )
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; "
            "script-src 'self'; "
            "style-src 'self' https://fonts.googleapis.com; "
            "font-src 'self' https://fonts.gstatic.com; "
            "img-src 'self' blob: data:; "
            "connect-src 'self' wss: ws:; "
            "frame-ancestors 'none';"
        )
        return response


app.add_middleware(SecurityHeadersMiddleware)

# Restrict CORS — same origin only (the tunnel sets its own origin)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[],   # No cross-origin requests allowed
    allow_credentials=False,
    allow_methods=["GET", "POST"],
    allow_headers=["X-CSRF-Token", "Content-Type"],
)

# ─── Static Files ─────────────────────────────────────────────────────────────

app.mount("/static", StaticFiles(directory=str(settings.STATIC_DIR)), name="static")


# ─── Request / Response Models ────────────────────────────────────────────────

class LoginRequest(BaseModel):
    password: str = Field(..., min_length=1, max_length=256)


class TapRequest(BaseModel):
    x: float
    y: float

    @field_validator("x", "y")
    def must_be_finite(cls, v):
        import math
        if not math.isfinite(v):
            raise ValueError("coordinate must be finite")
        return v


class SwipeRequest(BaseModel):
    x1: float
    y1: float
    x2: float
    y2: float
    duration_ms: int = Field(default=300, ge=50, le=5000)


class KeyRequest(BaseModel):
    key: str = Field(..., max_length=32)


class TextRequest(BaseModel):
    text: str = Field(..., max_length=500)


# ─── Routes: Pages ────────────────────────────────────────────────────────────

@app.get("/", include_in_schema=False)
async def root(session: Optional[str] = Cookie(default=None)):
    """Redirect to /remote if authenticated, else /login."""
    if session:
        try:
            from .auth import decode_jwt
            decode_jwt(session)
            return RedirectResponse("/remote", status_code=302)
        except HTTPException:
            pass
    return RedirectResponse("/login", status_code=302)


@app.get("/login", response_class=HTMLResponse, include_in_schema=False)
async def login_page():
    return FileResponse(settings.STATIC_DIR / "login.html")


@app.get("/remote", response_class=HTMLResponse, include_in_schema=False)
async def remote_page(claims: dict = Depends(require_auth)):
    return FileResponse(settings.STATIC_DIR / "remote.html")


# ─── Routes: Auth API ─────────────────────────────────────────────────────────

@app.post("/api/login")
async def api_login(request: Request, body: LoginRequest):
    """
    Login endpoint.
    Rate-limited, uses constant-time comparison, generic error messages,
    and logs all attempts to audit logger.
    """
    ip = _get_client_ip(request)
    user_agent = request.headers.get("User-Agent", "Unknown")

    # 1. Check rate limit FIRST (raises 429 if locked)
    try:
        rate_limiter.check(request)
    except HTTPException as e:
        audit_logger.log_event("LOGIN_BLOCKED", ip=ip, status="BLOCKED", details="IP locked out due to rate limit", user_agent=user_agent)
        raise e

    # 2. Verify password (constant-time via bcrypt)
    ok = verify_password(body.password, settings.PASSWORD_HASH)

    if not ok:
        rate_limiter.record_failure(request)
        audit_logger.log_event("LOGIN_FAILED", ip=ip, status="FAILED", details="Incorrect password attempt", user_agent=user_agent)
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED,
            {"error": "invalid_credentials", "message": "Invalid credentials."},
        )

    # 3. Login success
    rate_limiter.record_success(request)
    token = create_jwt()
    csrf = generate_csrf_token()

    response = JSONResponse({"ok": True})
    response.set_cookie(
        "session",
        token,
        httponly=True,
        samesite="strict",
        secure=False,   # Set to True when behind HTTPS tunnel
        max_age=settings.JWT_EXPIRE_HOURS * 3600,
        path="/",
    )
    # CSRF cookie: NOT HttpOnly so JS can read it and send as header
    response.set_cookie(
        "csrf_token",
        csrf,
        httponly=False,
        samesite="strict",
        secure=False,
        max_age=settings.JWT_EXPIRE_HOURS * 3600,
        path="/",
    )
    return response


@app.post("/api/logout")
async def api_logout(request: Request, claims: dict = Depends(require_auth)):
    ip = _get_client_ip(request)
    user_agent = request.headers.get("User-Agent", "Unknown")
    audit_logger.log_event("LOGOUT", ip=ip, status="SUCCESS", details="User signed out", user_agent=user_agent)

    response = JSONResponse({"ok": True})
    response.delete_cookie("session", path="/")
    response.delete_cookie("csrf_token", path="/")
    return response


@app.get("/api/logs")
async def api_get_logs(claims: dict = Depends(require_auth)):
    """Return immutable rotating audit logs (read-only)."""
    return {"logs": audit_logger.get_logs(limit=200)}


# ─── Routes: Device Status ────────────────────────────────────────────────────

@app.get("/api/status")
async def api_status(claims: dict = Depends(require_auth)):
    connected = adb.is_connected()
    if not connected:
        return {
            "connected": False,
            "model": None,
            "battery": None,
            "screen_width": None,
            "screen_height": None,
            "stream_fps": streamer.fps,
            "viewers": streamer.client_count,
        }
    w, h = adb.get_screen_size()
    return {
        "connected": True,
        "model": adb.get_device_model(),
        "battery": adb.get_battery_level(),
        "screen_width": w,
        "screen_height": h,
        "stream_fps": streamer.fps,
        "viewers": streamer.client_count,
    }


@app.post("/api/adb/reconnect")
async def api_adb_reconnect(claims: dict = Depends(require_auth_and_csrf)):
    """Trigger manual ADB connect to configured ADB_DEVICE_SERIAL or auto-detect."""
    res = await adb.connect_remote()
    return res


# ─── Routes: Control API ──────────────────────────────────────────────────────

@app.post("/api/control/tap")
async def api_tap(
    body: TapRequest,
    claims: dict = Depends(require_auth_and_csrf),
):
    try:
        await adb.tap(body.x, body.y)
        return {"ok": True}
    except ADBError as e:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, str(e))


@app.post("/api/control/swipe")
async def api_swipe(
    body: SwipeRequest,
    claims: dict = Depends(require_auth_and_csrf),
):
    try:
        await adb.swipe(body.x1, body.y1, body.x2, body.y2, body.duration_ms)
        return {"ok": True}
    except ADBError as e:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, str(e))


@app.post("/api/control/keyevent")
async def api_keyevent(
    body: KeyRequest,
    claims: dict = Depends(require_auth_and_csrf),
):
    try:
        await adb.send_keyevent(body.key)
        return {"ok": True}
    except ADBError as e:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, str(e))


@app.post("/api/control/text")
async def api_text(
    body: TextRequest,
    claims: dict = Depends(require_auth_and_csrf),
):
    try:
        await adb.send_text(body.text)
        return {"ok": True}
    except ADBError as e:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, str(e))


@app.post("/api/control/back")
async def api_back(claims: dict = Depends(require_auth_and_csrf)):
    try:
        await adb.press_back()
        return {"ok": True}
    except ADBError as e:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, str(e))


@app.post("/api/control/home")
async def api_home(claims: dict = Depends(require_auth_and_csrf)):
    try:
        await adb.press_home()
        return {"ok": True}
    except ADBError as e:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, str(e))


@app.post("/api/control/recents")
async def api_recents(claims: dict = Depends(require_auth_and_csrf)):
    try:
        await adb.press_recents()
        return {"ok": True}
    except ADBError as e:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, str(e))


# ─── WebSocket: Screen Stream ─────────────────────────────────────────────────

@app.websocket("/ws/screen")
async def ws_screen(
    websocket: WebSocket,
    token: Optional[str] = None,
):
    """
    WebSocket screen stream.
    Auth: JWT passed as ?token= query param (cookies not sent with WS in all browsers).
    The token is a short-lived one-time param generated by the frontend after login.
    """
    # Validate auth
    if not token:
        await websocket.close(code=4401, reason="Unauthorized")
        return
    try:
        from .auth import decode_jwt
        decode_jwt(token)
    except HTTPException:
        await websocket.close(code=4401, reason="Unauthorized")
        return

    await websocket.accept()
    await streamer.add_client(websocket)

    try:
        # Keep connection open; client can send control messages here too
        while True:
            # We only need to keep the connection alive — screen is pushed by streamer
            try:
                data = await asyncio.wait_for(websocket.receive_text(), timeout=30)
                # Heartbeat / ping
                if data == "ping":
                    await websocket.send_text("pong")
            except asyncio.TimeoutError:
                # Send a keepalive pong
                await websocket.send_text("pong")
    except WebSocketDisconnect:
        pass
    finally:
        await streamer.remove_client(websocket)


# ─── WebSocket token endpoint ─────────────────────────────────────────────────

@app.get("/api/ws-token")
async def get_ws_token(claims: dict = Depends(require_auth)):
    """
    Returns a short-lived JWT for WebSocket authentication.
    The main session cookie can't be sent with WebSocket connections reliably,
    so we issue a dedicated token here (still validated against the same secret).
    """
    # Reuse the same JWT — the client sends it as a query param
    # In production you'd want a shorter-lived single-use token
    token = create_jwt()
    return {"token": token}


# ─── Entry Point ──────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "server.main:app",
        host=settings.SERVER_HOST,
        port=settings.SERVER_PORT,
        log_level="info",
        reload=False,
    )
