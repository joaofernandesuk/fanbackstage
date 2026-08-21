import asyncio
import json
from abc import ABC, abstractmethod
from base64 import b64decode, urlsafe_b64decode, urlsafe_b64encode
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from hmac import compare_digest
from hmac import new as hmac_new
from urllib.request import Request, urlopen

from app.core.config import get_settings


class StreamingProviderError(RuntimeError):
    pass


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

    def verify_webhook(self, body: bytes, authorization: str | None) -> dict:
        """Verify LiveKit's signed JWT and raw-body SHA-256 claim before parsing."""
        if not authorization or not authorization.startswith("Bearer "):
            raise ValueError("Missing LiveKit webhook authorization")
        token = authorization.removeprefix("Bearer ")
        try:
            header, payload, signature = token.split(".")
            claims = json.loads(urlsafe_b64decode(f"{payload}{'=' * (-len(payload) % 4)}"))
        except (ValueError, json.JSONDecodeError) as exc:
            raise ValueError("Invalid LiveKit webhook authorization") from exc
        expected = (
            urlsafe_b64encode(
                hmac_new(
                    get_settings().livekit_api_secret.encode(),
                    f"{header}.{payload}".encode(),
                    sha256,
                ).digest()
            )
            .rstrip(b"=")
            .decode()
        )
        if not compare_digest(signature, expected):
            raise ValueError("Invalid LiveKit webhook signature")
        if claims.get("iss") != get_settings().livekit_api_key or int(claims.get("exp", 0)) < int(
            datetime.now(UTC).timestamp()
        ):
            raise ValueError("Expired or untrusted LiveKit webhook")
        try:
            expected_hash = b64decode(claims["sha256"])
        except (KeyError, ValueError) as exc:
            raise ValueError("LiveKit webhook body hash is missing") from exc
        if not compare_digest(expected_hash, sha256(body).digest()):
            raise ValueError("LiveKit webhook body hash does not match")
        try:
            event = json.loads(body)
        except json.JSONDecodeError as exc:
            raise ValueError("Invalid LiveKit webhook body") from exc
        if not event.get("id") or not event.get("event"):
            raise ValueError("LiveKit webhook event is incomplete")
        return event

    async def list_participant_identities(self, room_name: str) -> set[str]:
        """Read provider room membership for replay-safe repair; never trusts browsers."""
        settings = get_settings()
        base_url = settings.livekit_url.replace("ws://", "http://").replace("wss://", "https://")
        token = await self._service_token()

        def request() -> set[str]:
            payload = json.dumps({"room": room_name}).encode()
            call = Request(
                f"{base_url}/twirp/livekit.RoomService/ListParticipants",
                data=payload,
                headers={
                    "Authorization": f"Bearer {token}",
                    "Content-Type": "application/json",
                },
                method="POST",
            )
            try:
                with urlopen(call, timeout=5) as response:
                    parsed = json.loads(response.read())
            except OSError as exc:
                raise StreamingProviderError("LiveKit participant reconciliation failed") from exc
            return {
                item["identity"] for item in parsed.get("participants", []) if item.get("identity")
            }

        return await asyncio.to_thread(request)

    async def _service_token(self) -> str:
        settings = get_settings()
        now = datetime.now(UTC)
        header = self._part({"alg": "HS256", "typ": "JWT"})
        payload = self._part(
            {
                "iss": settings.livekit_api_key,
                "nbf": int(now.timestamp()),
                "exp": int(
                    (now + timedelta(seconds=settings.livekit_token_ttl_seconds)).timestamp()
                ),
                "video": {"roomList": True, "roomAdmin": True},
            }
        )
        signature = (
            urlsafe_b64encode(
                hmac_new(
                    settings.livekit_api_secret.encode(), f"{header}.{payload}".encode(), sha256
                ).digest()
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
