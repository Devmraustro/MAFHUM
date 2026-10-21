"""Health check schemas."""


from pydantic import BaseModel, Field


class CheckItem(BaseModel):
    status: str = Field(..., description="ok | degraded | unavailable")
    detail: str | None = None


class HealthResponse(BaseModel):
    status: str = Field(..., description="ok | degraded")
    version: str
    checks: dict[str, CheckItem]
    request_id: str

    model_config = {
        "json_schema_extra": {
            "examples": [
                {
                    "status": "ok",
                    "version": "0.1.0",
                    "checks": {
                        "database": {"status": "ok"},
                        "ai_provider": {"status": "ok", "detail": "mock provider"},
                    },
                    "request_id": "c1a2b3d4-e5f6-11ed-9a01-0242ac120002",
                }
            ]
        }
    }
