import logging

SENSITIVE_HTTP_LOGGERS = ("httpx", "httpcore")


def configure_sensitive_http_logging() -> None:
    """Prevent HTTP client request lines from serializing query credentials."""

    for logger_name in SENSITIVE_HTTP_LOGGERS:
        logging.getLogger(logger_name).setLevel(logging.WARNING)
