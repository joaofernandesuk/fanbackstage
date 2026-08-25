from abc import ABC, abstractmethod
from email.message import EmailMessage
from urllib.parse import urlencode

import aiosmtplib

from app.core.config import get_settings


class EmailProvider(ABC):
    name = "provider"

    @abstractmethod
    async def send(
        self,
        *,
        template: str,
        recipient: str,
        payload: dict,
        secure_payload: dict,
        classification: str,
        idempotency_key: str,
    ) -> str: ...


class SmtpEmailProvider(EmailProvider):
    """Local Mailpit-compatible adapter; production adapters share this boundary."""

    name = "smtp"

    async def send(
        self,
        *,
        template: str,
        recipient: str,
        payload: dict,
        secure_payload: dict,
        classification: str,
        idempotency_key: str,
    ) -> str:
        settings = get_settings()
        path, token = secure_payload.get("path"), secure_payload.get("token")
        link = (
            f"{settings.web_origin}{path}?{urlencode({'token': token})}" if path and token else None
        )
        message = EmailMessage()
        message["From"] = settings.email_from
        message["To"] = recipient
        message["Message-ID"] = f"<{idempotency_key}@fanbackstage.local>"
        message["X-FanBackstage-Stream"] = classification
        if classification == "marketing":
            unsubscribe = secure_payload.get("unsubscribe_token")
            message["List-Unsubscribe"] = f"<{settings.web_origin}/unsubscribe?token={unsubscribe}>"
            message["List-Unsubscribe-Post"] = "List-Unsubscribe=One-Click"
        message["Subject"] = str(payload.get("subject") or "FanBackstage notification")
        body = str(payload.get("body") or "You have a FanBackstage notification.")
        if link:
            body = f"{body}\n\nOpen your secure FanBackstage link: {link}"
        message.set_content(body)
        await aiosmtplib.send(message, hostname=settings.smtp_host, port=settings.smtp_port)
        return idempotency_key


email_provider: EmailProvider = SmtpEmailProvider()
