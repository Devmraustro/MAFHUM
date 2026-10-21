"""Provider factory. Configuration selects the provider; no vendor code here."""

from app.config import settings
from app.providers.base import AIProvider
from app.providers.mock import MockProvider


def get_ai_provider() -> AIProvider:
    if settings.ai_provider == "mock":
        return MockProvider()
    raise ValueError(
        f"AI_PROVIDER={settings.ai_provider!r} is not implemented. "
        "Phase 1 ships only the deterministic mock provider; add vendors through "
        "app.providers.base.AIProvider after license and security review."
    )
