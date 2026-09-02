import logging
import secrets
import time
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api.router import api_router, health_router
from app.audit.service import bind_request_metadata, reset_request_metadata
from app.core.config import get_settings
from app.core.logging import configure_sensitive_http_logging, configure_structured_logging
from app.observability.errors import capture_exception

configure_structured_logging(service="fanbackstage-api", environment=get_settings().environment)
configure_sensitive_http_logging()
logger = logging.getLogger("fanbackstage")


@asynccontextmanager
async def lifespan(app: FastAPI):
    get_settings().validate_production()
    yield


settings = get_settings()
app = FastAPI(
    title="FanBackstage API",
    version="0.1.0",
    lifespan=lifespan,
    docs_url="/docs" if settings.api_docs_enabled else None,
    redoc_url="/redoc" if settings.api_docs_enabled else None,
    openapi_url="/openapi.json" if settings.api_docs_enabled else None,
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[get_settings().web_origin],
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
    allow_headers=["Content-Type", "X-Request-ID", "Idempotency-Key"],
)
app.include_router(health_router)
app.include_router(api_router)


@app.middleware("http")
async def correlation_id(request: Request, call_next):
    started_at = time.monotonic()
    request.state.correlation_id = request.headers.get("X-Request-ID", secrets.token_hex(16))
    audit_token = bind_request_metadata(
        request.client.host if request.client else None,
        request.headers.get("user-agent"),
        request.state.correlation_id,
    )
    try:
        response = await call_next(request)
        response.headers["X-Request-ID"] = request.state.correlation_id
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
        if request.url.path.startswith("/api/v1/compliance/age-verification/callback/"):
            # OAuth callback state and codes are transient credentials. They must not
            # be cached or propagated through a referrer, including on error responses.
            response.headers["Cache-Control"] = "no-store"
            response.headers["Pragma"] = "no-cache"
            response.headers["Referrer-Policy"] = "no-referrer"
        if not request.url.path.startswith("/docs") and not request.url.path.startswith("/openapi"):
            response.headers["Content-Security-Policy"] = (
                "default-src 'none'; frame-ancestors 'none'; base-uri 'none'"
            )
        if get_settings().environment in {"staging", "production"}:
            response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
        logger.info(
            "request_completed",
            extra={
                "correlation_id": request.state.correlation_id,
                "route": request.url.path,
                "status_code": response.status_code,
                "duration_ms": round((time.monotonic() - started_at) * 1000, 2),
            },
        )
        return response
    finally:
        reset_request_metadata(audit_token)


@app.exception_handler(Exception)
async def unhandled_exception(request: Request, exc: Exception):
    correlation_id = getattr(request.state, "correlation_id", None)
    event_id = capture_exception(
        exc,
        correlation_id=correlation_id,
        route=request.url.path,
    )
    if get_settings().environment in {"development", "test"}:
        logger.exception("unhandled_application_exception", exc_info=exc)
    return JSONResponse(
        status_code=500,
        content={"detail": "Internal server error", "event_id": event_id},
        headers={"X-Request-ID": correlation_id} if correlation_id else None,
    )
