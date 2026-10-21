"""Deterministic mock AI provider for development and tests.

No credentials, no network, no model downloads. Output is always a labelled
draft, never presented as verified academic content.
"""

from app.providers.base import AIProvider
from app.schemas.ai import (
    EmbedRequest,
    EmbedResponse,
    GenerateRequest,
    GenerateResponse,
    ProviderHealth,
)


class MockProvider(AIProvider):
    provider = "mock"
    model = "mock"
    deterministic = True
    credentials_required = False

    async def health(self) -> ProviderHealth:
        return ProviderHealth(
            provider=self.provider,
            model=self.model,
            status="ok",
            deterministic=self.deterministic,
            credentials_required=self.credentials_required,
        )

    async def generate(self, request: GenerateRequest, request_id: str) -> GenerateResponse:
        preview = request.prompt[:80].replace("\n", " ")
        body = (
            "[مُسودة آلية — مزوّد تجريبي (mock deterministi)]\n"
            f"الطلب المستلم: \"{preview}{'…' if len(request.prompt) > 80 else ''}\"\n"
            "\nملاحظة: هذا مخرج تجريبي محدد الناتج، من تطوير MAFHUM للاختبار فقط. "
            "ليس مصدرًا أكاديميًا، ولا مرجعًا، ولا يحل محل مراجعة الطالب.\n"
            f"\n[en] Deterministic mock draft for prompt ({len(request.prompt)} chars). "
            "Not a source, not a citation, not a replacement for student review.\n"
        )
        return GenerateResponse(
            text=body,
            provider=self.provider,
            model=self.model,
            draft=True,
            usage={"prompt_chars": len(request.prompt), "max_tokens": request.max_tokens},
            request_id=request_id,
        )

    async def embed(self, request: EmbedRequest, request_id: str) -> EmbedResponse:
        embeddings = [
            [float(b) / 255.0 for b in __import__("hashlib").sha256(t.encode("utf-8")).digest()[:16]]
            for t in request.texts
        ]
        return EmbedResponse(embeddings=embeddings, model=self.model, request_id=request_id)
