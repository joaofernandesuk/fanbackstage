import asyncio
import json
from abc import ABC, abstractmethod
from base64 import b64decode, urlsafe_b64decode, urlsafe_b64encode
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from hmac import compare_digest
from hmac import new as hmac_new
from urllib.error import HTTPError
from urllib.request import HTTPRedirectHandler, Request, build_opener

from app.core.config import get_settings


class StreamingProviderError(RuntimeError):
    pass


def _livekit_control_base_url() -> str:
    settings = get_settings()
    control_url = settings.livekit_control_url or settings.livekit_url
    return control_url.replace("ws://", "http://").replace("wss://", "https://")


class _RejectLiveKitRedirects(HTTPRedirectHandler):
    """Never forward a signed RoomService JWT to a redirect destination."""

    def redirect_request(self, *_args, **_kwargs):
        return None


def _open_livekit_request(request: Request, *, timeout: float):
    return build_opener(_RejectLiveKitRedirects()).open(request, timeout=timeout)


def _is_livekit_twirp_not_found(exc: HTTPError) -> bool:
    """Accept only LiveKit/Twirp's structured missing-resource response.

    A reverse proxy's generic 404 must never be mistaken for successful room
    deletion or empty membership because that would make provider enforcement
    fail open.
    """

    if exc.code != 404:
        return False
    content_type = (exc.headers.get("Content-Type") if exc.headers else "") or ""
    if content_type.partition(";")[0].strip().lower() != "application/json":
        return False
    try:
        body = exc.read(8193)
        if len(body) > 8192:
            return False
        payload = json.loads(body)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return False
    return (
        isinstance(payload, dict)
        and payload.get("code") == "not_found"
        and isinstance(payload.get("msg"), str)
    )


class StreamingProvider(ABC):
    """Future streaming boundary. Product authorization remains in FanBackstage services."""

    @abstractmethod
    async def health(self) -> bool: ...

    @abstractmethod
    async def create_room(self, room_name: str) -> None: ...

    @abstractmethod
    async def close_room(self, room_name: str) -> None: ...

    @abstractmethod
    async def remove_participant(self, room_name: str, identity: str) -> None: ...

    @abstractmethod
    async def participant_token(
        self,
        room_name: str,
        identity: str,
        *,
        can_publish: bool,
        can_subscribe: bool,
        authority_expires_at: datetime | None = None,
    ) -> str: ...


class LiveKitStreamingProvider(StreamingProvider):
    async def health(self) -> bool:
        return bool(get_settings().livekit_url)

    async def create_room(self, room_name: str) -> None:
        # LiveKit lazily creates a room when the first participant joins. The
        # application persists its lifecycle before issuing a token.
        del room_name

    async def close_room(self, room_name: str) -> None:
        await self._room_service_call("DeleteRoom", {"room": room_name}, missing_ok=True)

    async def remove_participant(self, room_name: str, identity: str) -> None:
        await self._room_service_call(
            "RemoveParticipant",
            {"room": room_name, "identity": identity},
            missing_ok=True,
        )

    async def participant_token(
        self,
        room_name: str,
        identity: str,
        *,
        can_publish: bool,
        can_subscribe: bool,
        authority_expires_at: datetime | None = None,
    ) -> str:
        settings = get_settings()
        now = datetime.now(UTC)
        configured_expiry = now + timedelta(seconds=settings.livekit_token_ttl_seconds)
        authority_expiry = (
            authority_expires_at
            if authority_expires_at is None or authority_expires_at.tzinfo
            else authority_expires_at.replace(tzinfo=UTC)
        )
        expires_at = (
            min(configured_expiry, authority_expiry)
            if authority_expiry is not None
            else configured_expiry
        )
        if expires_at <= now:
            raise StreamingProviderError("LiveKit authority has expired")
        header = self._part({"alg": "HS256", "typ": "JWT"})
        payload = self._part(
            {
                "iss": settings.livekit_api_key,
                "sub": identity,
                # A sleeping VM can resume with its media node briefly behind
                # the API clock. Backdate only the start boundary; ``exp``
                # remains capped by current application authority.
                "nbf": int(
                    (now - timedelta(seconds=settings.livekit_token_ttl_seconds)).timestamp()
                ),
                "exp": int(expires_at.timestamp()),
                "video": {
                    "room": room_name,
                    "roomJoin": True,
                    "canPublish": can_publish,
                    "canSubscribe": can_subscribe,
                    # Durable chat and moderation live on FanBackstage HTTP
                    # APIs; do not grant an unmoderated LiveKit data channel.
                    "canPublishData": False,
                    "canUpdateOwnMetadata": False,
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

    async def _room_service_call(
        self,
        method: str,
        payload: dict[str, str],
        *,
        missing_ok: bool,
    ) -> None:
        base_url = _livekit_control_base_url()
        room_name = payload.get("room")
        token = await self._service_token(
            room_name=room_name if method == "RemoveParticipant" else None,
            room_create=method == "DeleteRoom",
        )

        def request() -> None:
            call = Request(
                f"{base_url}/twirp/livekit.RoomService/{method}",
                data=json.dumps(payload, separators=(",", ":")).encode(),
                headers={
                    "Authorization": f"Bearer {token}",
                    "Content-Type": "application/json",
                },
                method="POST",
            )
            try:
                with _open_livekit_request(call, timeout=5) as response:
                    response.read()
            except HTTPError as exc:
                if missing_ok and _is_livekit_twirp_not_found(exc):
                    return
                raise StreamingProviderError(f"LiveKit {method} control failed") from exc
            except OSError as exc:
                raise StreamingProviderError(f"LiveKit {method} control failed") from exc

        await asyncio.to_thread(request)

    def verify_webhook(self, body: bytes, authorization: str | None) -> dict:
        """Verify LiveKit's signed JWT and raw-body SHA-256 claim before parsing."""
        # LiveKit webhook delivery places the signed JWT directly in
        # ``Authorization``. This deliberately differs from the Twirp service
        # API, which uses a ``Bearer`` token. Do not accept a browser-style
        # bearer prefix here: the provider's exact signed transport format is
        # part of the ingress contract.
        if not authorization or authorization.startswith("Bearer "):
            raise ValueError("Missing LiveKit webhook authorization")
        token = authorization
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
        base_url = _livekit_control_base_url()
        token = await self._service_token(room_name=room_name)

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
                with _open_livekit_request(call, timeout=5) as response:
                    parsed = json.loads(response.read())
            except HTTPError as exc:
                # A room that no longer exists is authoritative empty
                # membership. Treating it as a transient provider failure can
                # leave a missed disconnect billable forever.
                if _is_livekit_twirp_not_found(exc):
                    return set()
                raise StreamingProviderError("LiveKit participant reconciliation failed") from exc
            except OSError as exc:
                raise StreamingProviderError("LiveKit participant reconciliation failed") from exc
            return {
                item["identity"] for item in parsed.get("participants", []) if item.get("identity")
            }

        return await asyncio.to_thread(request)

    async def _service_token(
        self,
        *,
        room_name: str | None = None,
        room_create: bool = False,
    ) -> str:
        settings = get_settings()
        now = datetime.now(UTC)
        video_grant: dict[str, bool | str] = {}
        if room_name is not None:
            # LiveKit requires roomAdmin and the exact room name for
            # participant listing/removal; roomAdmin alone is insufficient.
            video_grant.update({"roomAdmin": True, "room": room_name})
        if room_create:
            # DeleteRoom is authorized by roomCreate in LiveKit's server API.
            video_grant["roomCreate"] = True
        if not video_grant:
            raise StreamingProviderError("LiveKit control grant is incomplete")
        header = self._part({"alg": "HS256", "typ": "JWT"})
        payload = self._part(
            {
                "iss": settings.livekit_api_key,
                "nbf": int(
                    (now - timedelta(seconds=settings.livekit_token_ttl_seconds)).timestamp()
                ),
                "exp": int(
                    (now + timedelta(seconds=settings.livekit_token_ttl_seconds)).timestamp()
                ),
                "video": video_grant,
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
