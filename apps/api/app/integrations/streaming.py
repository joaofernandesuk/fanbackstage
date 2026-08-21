import json
from abc import ABC, abstractmethod
from base64 import urlsafe_b64encode
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from hmac import new as hmac_new

from app.core.config import get_settings


class StreamingProvider(ABC):
    """Future streaming boundary. Product authorization remains in FanBackstage services."""

    @abstractmethod
    async def health(self) -> bool: ...

    @abstractmethod
    async def create_room(self, room_name: str) -> None: ...

    @abstractmethod
    async def close_room(self, room_name: str) -> None: ...

    @abstractmethod
    async def participant_token(
        self, room_name: str, identity: str, *, can_publish: bool, can_subscribe: bool
    ) -> str: ...


class LiveKitStreamingProvider(StreamingProvider):
    async def health(self) -> bool:
        return bool(get_settings().livekit_url)

    async def create_room(self, room_name: str) -> None:
        # LiveKit lazily creates a room when the first participant joins. The
        # application persists its lifecycle before issuing a token.
        del room_name

    async def close_room(self, room_name: str) -> None:
        # Room closure is reconciled by signed provider events in production.
        # The domain first revokes new tokens by transitioning its own state.
        del room_name

    async def participant_token(
        self, room_name: str, identity: str, *, can_publish: bool, can_subscribe: bool
    ) -> str:
        settings = get_settings()
        now = datetime.now(UTC)
        header = self._part({"alg": "HS256", "typ": "JWT"})
        payload = self._part(
            {
                "iss": settings.livekit_api_key,
                "sub": identity,
                "nbf": int(now.timestamp()),
                "exp": int(
                    (now + timedelta(seconds=settings.livekit_token_ttl_seconds)).timestamp()
                ),
                "video": {
                    "room": room_name,
                    "roomJoin": True,
                    "canPublish": can_publish,
                    "canSubscribe": can_subscribe,
                },
            }
        )
        signed = f"{header}.{payload}".encode()
        signature = (
            urlsafe_b64encode(
                hmac_new(settings.livekit_api_secret.encode(), signed, sha256).digest()
            )
            .rstrip(b"=")
            .decode()
        )
        return f"{header}.{payload}.{signature}"

    @staticmethod
    def _part(value: dict) -> str:
        return (
            urlsafe_b64encode(json.dumps(value, separators=(",", ":")).encode())
            .rstrip(b"=")
            .decode()
        )
