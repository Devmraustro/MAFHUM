"""Abstract AI provider interface.

Implemented by: the deterministic mock provider (Phase 1) and, later, local or
approved remote adapters. No vendor-specific types leak past this boundary.
"""

import hashlib
from abc import ABC, abstractmethod

from app.schemas.ai import (
    EmbedRequest,
    EmbedResponse,
    GenerateRequest,
    GenerateResponse,
    ProviderHealth,
)


class AIProvider(ABC):
    @abstractmethod
    async def health(self) -> ProviderHealth:
        ...

    @abstractmethod
    async def generate(self, request: GenerateRequest, request_id: str) -> GenerateResponse:
        ...

    @abstractmethod
    async def embed(self, request: EmbedRequest, request_id: str) -> EmbedResponse:
        ...


def deterministic_text(seed: str, length: int = 40) -> str:
    """Stable pseudo-text derived from a seed hash (no randomness, no model)."""
    digest = hashlib.sha256(seed.encode("utf-8")).hexdigest()
    return (digest * ((length // len(digest)) + 1))[:length]
