import json
import logging
import secrets
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api.router import api_router, health_router
from app.audit.service import bind_request_metadata, reset_request_metadata
from app.core.config import get_settings
from app.core.logging import configure_sensitive_http_logging


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        return json.dumps(
            {
                "timestamp": self.formatTime(record),
                "level": record.levelname,
                "logger": record.name,
                "message": record.getMessage(),
                "service": "fanbackstage-api",
                "environment": get_settings().environment,
            }
        )


handler = logging.StreamHandler()
handler.setFormatter(JsonFormatter())
logging.basicConfig(level=logging.INFO, handlers=[handler])
configure_sensitive_http_logging()
logger = logging.getLogger("fanbackstage")


@asynccontextmanager
async def lifespan(app: FastAPI):
    get_settings().validate_production()
    yield


app = FastAPI(title="FanBackstage API", version="0.1.0", lifespan=lifespan)
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
        if get_settings().environment == "production":
            response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
        logger.info(
            "request_completed",
            extra={
                "correlation_id": request.state.correlation_id,
                "status_code": response.status_code,
            },
        )
        return response
    finally:
        reset_request_metadata(audit_token)


@app.exception_handler(Exception)
async def unhandled_exception(_: Request, exc: Exception):
    logger.exception("Unhandled application exception", exc_info=exc)
    return JSONResponse(status_code=500, content={"detail": "Internal server error"})
