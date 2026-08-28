import pytest

from app.core.config import Settings
from app.integrations import email


@pytest.mark.parametrize(
    ("use_tls", "start_tls"),
    [
        (True, False),
        (False, True),
    ],
)
@pytest.mark.asyncio
async def test_smtp_provider_passes_authentication_and_tls_mode(
    monkeypatch,
    use_tls,
    start_tls,
):
    settings = Settings(
        web_origin="https://fanbackstage.example",
        smtp_host="smtp.example.com",
        smtp_port=465 if use_tls else 587,
        smtp_username="fanbackstage-smtp-user",
        smtp_password="smtp-password-with-at-least-32-characters",
        smtp_use_tls=use_tls,
        smtp_start_tls=start_tls,
    )
    captured: dict = {}

    async def send(message, **kwargs):
        captured["message"] = message
        captured.update(kwargs)

    monkeypatch.setattr(email, "get_settings", lambda: settings)
    monkeypatch.setattr(email.aiosmtplib, "send", send)

    provider_message_id = await email.SmtpEmailProvider().send(
        template="security_notice",
        recipient="fan@example.com",
        payload={"subject": "Security notice", "body": "Review your account."},
        secure_payload={},
        classification="transactional",
        idempotency_key="notification-intent-1",
    )

    assert provider_message_id == "notification-intent-1"
    assert captured["hostname"] == "smtp.example.com"
    assert captured["port"] == (465 if use_tls else 587)
    assert captured["username"] == "fanbackstage-smtp-user"
    assert captured["password"] == "smtp-password-with-at-least-32-characters"
    assert captured["use_tls"] is use_tls
    assert captured["start_tls"] is start_tls
