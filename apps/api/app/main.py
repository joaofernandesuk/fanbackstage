import json
import logging
import secrets
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api.router import api_router, health_router
from app.core.config import get_settings


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
    allow_methods=["GET", "POST", "PATCH", "DELETE"],
    allow_headers=["Content-Type", "X-Request-ID"],
)
app.include_router(health_router)
app.include_router(api_router)


@app.middleware("http")
async def correlation_id(request: Request, call_next):
    request.state.correlation_id = request.headers.get("X-Request-ID", secrets.token_hex(16))
    response = await call_next(request)
    response.headers["X-Request-ID"] = request.state.correlation_id
    response.headers["X-Content-Type-Options"] = "nosniff"
    logger.info(
        "request_completed",
        extra={"correlation_id": request.state.correlation_id, "status_code": response.status_code},
    )
    return response


@app.exception_handler(Exception)
async def unhandled_exception(_: Request, exc: Exception):
    logger.exception("Unhandled application exception", exc_info=exc)
    return JSONResponse(status_code=500, content={"detail": "Internal server error"})
