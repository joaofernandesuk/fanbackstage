from abc import ABC, abstractmethod


class StreamingProvider(ABC):
    """Future streaming boundary. Product authorization remains in FanBackstage services."""

    @abstractmethod
    async def health(self) -> bool: ...


class LiveKitStreamingProvider(StreamingProvider):
    async def health(self) -> bool:
        # Connectivity only; Phase 0 intentionally mints no room tokens.
        return False
