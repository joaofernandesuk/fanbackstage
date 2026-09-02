import json
import logging

SENSITIVE_HTTP_LOGGERS = ("httpx", "httpcore")
SAFE_STRUCTURED_FIELDS = (
    "correlation_id",
    "route",
    "status_code",
    "duration_ms",
    "event_id",
    "error_type",
    "metrics",
    "release_sha",
)


class JsonFormatter(logging.Formatter):
    def __init__(self, *, service: str, environment: str):
        super().__init__()
        self.service = service
        self.environment = environment

    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "timestamp": self.formatTime(record),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "service": self.service,
            "environment": self.environment,
        }
        for field in SAFE_STRUCTURED_FIELDS:
            value = getattr(record, field, None)
            if value is not None:
                payload[field] = value
        return json.dumps(payload)


def configure_structured_logging(*, service: str, environment: str) -> None:
    handler = logging.StreamHandler()
    handler.setFormatter(JsonFormatter(service=service, environment=environment))
    logging.basicConfig(level=logging.INFO, handlers=[handler], force=True)


def configure_sensitive_http_logging() -> None:
    """Prevent HTTP client request lines from serializing query credentials."""

    for logger_name in SENSITIVE_HTTP_LOGGERS:
        logging.getLogger(logger_name).setLevel(logging.WARNING)
