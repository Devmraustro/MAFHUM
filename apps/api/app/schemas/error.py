"""Safe error envelope — never leaks stack traces or internals to the client."""


from pydantic import BaseModel, Field


class ErrorField(BaseModel):
    field: str
    message: str
    code: str | None = None


class SafeErrorEnvelope(BaseModel):
    code: str = Field(..., description="Machine-readable error code")
    message: str = Field(..., description="Human-readable safe message")
    request_id: str
    field_errors: list[ErrorField] | None = None

    model_config = {
        "json_schema_extra": {
            "examples": [
                {
                    "code": "validation_error",
                    "message": "Invalid request payload.",
                    "request_id": "c1a2b3d4-e5f6-11ed-9a01-0242ac120002",
                    "field_errors": [
                        {"field": "prompt", "message": "Field required", "code": "missing"}
                    ],
                }
            ]
        }
    }
