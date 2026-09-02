import json
import logging
import os
import subprocess
from pathlib import Path

import pytest

from app.core.config import Settings
from app.core.logging import JsonFormatter
from app.observability import operations
from app.observability.errors import SafeLoggingErrorTracker
from app.staging import bootstrap_admin, dataset


def test_structured_logging_retains_safe_request_metadata_only():
    record = logging.LogRecord(
        "fanbackstage.test",
        logging.INFO,
        __file__,
        1,
        "request_completed",
        (),
        None,
    )
    record.correlation_id = "request-123"
    record.route = "/api/v1/account"
    record.status_code = 204
    record.duration_ms = 12.5
    record.raw_payload = {"password": "must-not-appear"}
    payload = json.loads(
        JsonFormatter(service="fanbackstage-api", environment="staging").format(record)
    )
    assert payload["correlation_id"] == "request-123"
    assert payload["route"] == "/api/v1/account"
    assert payload["status_code"] == 204
    assert payload["duration_ms"] == 12.5
    assert "raw_payload" not in payload
    assert "must-not-appear" not in json.dumps(payload)


def test_error_tracking_fallback_never_logs_exception_message(caplog):
    caplog.set_level(logging.ERROR, logger="fanbackstage.error_tracking")
    SafeLoggingErrorTracker().capture_exception(
        RuntimeError("private-token-should-not-appear"),
        event_id="safe-event-id",
        correlation_id="request-123",
        route="/api/v1/example",
    )
    assert caplog.records[-1].message == "unhandled_exception"
    assert caplog.records[-1].error_type == "RuntimeError"
    assert "private-token-should-not-appear" not in caplog.text


@pytest.mark.asyncio
async def test_operational_readiness_requires_fresh_worker_and_beat_heartbeats(monkeypatch):
    monkeypatch.setattr(operations, "_timestamp", lambda: 1_000)

    class RedisStub:
        def __init__(self, values):
            self.values = values

        async def mget(self, *_keys):
            return self.values

    assert await operations.operational_heartbeat_ready(RedisStub([b"950", b"960"]))
    assert not await operations.operational_heartbeat_ready(RedisStub([b"800", b"960"]))
    assert not await operations.operational_heartbeat_ready(RedisStub([None, b"960"]))


@pytest.mark.asyncio
async def test_admin_bootstrap_refuses_non_staging_before_database_access(monkeypatch):
    monkeypatch.setattr(
        bootstrap_admin,
        "get_settings",
        lambda: Settings(environment="production"),
    )
    with pytest.raises(RuntimeError, match="refused"):
        await bootstrap_admin.bootstrap_admin(
            "operator@example.com",
            bootstrap_admin.CONFIRMATION,
        )


def test_staging_dataset_refuses_production_and_requires_explicit_enable(monkeypatch):
    monkeypatch.setattr(dataset, "get_settings", lambda: Settings(environment="production"))
    with pytest.raises(RuntimeError, match="limited"):
        dataset._assert_enabled()
    monkeypatch.setattr(
        dataset,
        "get_settings",
        lambda: Settings(environment="staging", staging_dataset_enabled=False),
    )
    with pytest.raises(RuntimeError, match="explicitly enabled"):
        dataset._assert_enabled()


def test_staging_dataset_personas_cover_payment_and_creator_kyc_outcomes():
    assert set(dataset.FAN_PERSONAS) == {
        "fan-payment-success@staging-test.invalid",
        "fan-payment-decline@staging-test.invalid",
        "fan-ppv-refund@staging-test.invalid",
    }
    assert dataset.CREATOR_PERSONAS == {
        "creator-kyc-not-started@staging-test.invalid": None,
        "creator-kyc-pending@staging-test.invalid": dataset.VerificationStatus.pending,
        "creator-kyc-verified@staging-test.invalid": dataset.VerificationStatus.verified,
        "creator-kyc-failed@staging-test.invalid": dataset.VerificationStatus.failed,
        "creator-kyc-review-required@staging-test.invalid": dataset.VerificationStatus.needs_review,
    }
    assert set(dataset.DATASET_EMAILS) == set(dataset.FAN_PERSONAS) | set(dataset.CREATOR_PERSONAS)


def test_restore_drill_requires_marker_in_database_name():
    repository_root = Path(__file__).resolve().parents[3]
    result = subprocess.run(
        [
            repository_root / "scripts/staging-restore-drill.sh",
            "/nonexistent/backup.dump",
            "postgresql://operator:restore-marker@db.example.invalid/production",
            "RESTORE-INTO-ISOLATED-STAGING-VALIDATION",
        ],
        check=False,
        capture_output=True,
        text=True,
        env={**os.environ, "FANBACKSTAGE_ENVIRONMENT": "staging"},
    )
    assert result.returncode == 2
    assert "Target database name" in result.stderr
