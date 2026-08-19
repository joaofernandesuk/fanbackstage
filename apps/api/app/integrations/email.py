from abc import ABC, abstractmethod
from email.message import EmailMessage
from urllib.parse import urlencode

import aiosmtplib

from app.core.config import get_settings


class EmailProvider(ABC):
    @abstractmethod
    async def send_security_link(self, recipient: str, path: str, token: str) -> None: ...


class SmtpEmailProvider(EmailProvider):
    async def send_security_link(self, recipient: str, path: str, token: str) -> None:
        settings = get_settings()
        link = f"{settings.web_origin}{path}?{urlencode({'token': token})}"
        message = EmailMessage()
        message["From"] = settings.email_from
        message["To"] = recipient
        message["Subject"] = "FanBackstage account security link"
        message.set_content(f"Open this secure FanBackstage link: {link}")
        await aiosmtplib.send(message, hostname=settings.smtp_host, port=settings.smtp_port)


email_provider: EmailProvider = SmtpEmailProvider()
