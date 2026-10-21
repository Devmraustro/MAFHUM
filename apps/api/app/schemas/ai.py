"""AI gateway request/response schemas (provider-neutral)."""


from typing import Any

from pydantic import BaseModel, Field


class GenerateRequest(BaseModel):
    prompt: str = Field(..., min_length=1, max_length=4000)
    max_tokens: int = Field(default=512, ge=1, le=8192)
    temperature: float = Field(default=0.7, ge=0.0, le=2.0)


class GenerateResponse(BaseModel):
    text: str
    provider: str
    model: str
    draft: bool = Field(default=True, description="Always true: AI output is a draft requiring review")
    usage: dict[str, Any] | None = None
    request_id: str


class EmbedRequest(BaseModel):
    texts: list[str] = Field(..., min_length=1, max_length=64)


class EmbedResponse(BaseModel):
    embeddings: list[list[float]]
    model: str
    request_id: str


class ProviderHealth(BaseModel):
    provider: str
    model: str | None = None
    status: str = Field(..., description="ok | degraded | unavailable")
    deterministic: bool
    credentials_required: bool
