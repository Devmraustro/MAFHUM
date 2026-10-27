"""Logging with mandatory redaction.

Document contents, credentials, session tokens, cookies and absolute storage
paths must never appear in logs. Rather than relying on every call site to
remember, we redact defensively at the formatter boundary: any value matching a
sensitive key is replaced, and long free-text payloads are truncated.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any

_REDACTED = "[REDACTED]"

# Keys whose values are always secrets or user content, wherever they appear.
_SENSITIVE_KEYS = {
    "password",
    "new_password",
    "current_password",
    "token",
    "session_token",
    "session_id",
    "cookie",
    "cookies",
    "authorization",
    "api_key",
    "secret",
    "session_secret",
    "csrf",
    "csrf_token",
    "text",
    "content",
    "body",
    "payload",
    "document_text",
    "extracted_text",
    "file_contents",
    "data",
}

# Anything that looks like an absolute path into the private storage tree.
_PATH_RE = re.compile(r"(?:/[\w.\-]+){2,}")

_SENSITIVE_KEY_RE = re.compile(
    r"(?i)(session_secret|api[_-]?key|authorization|password|token|csrf[_-]?token)\s*[=:]\s*\S+"
)
# Credential schemes put the secret in a *following* token, not after "=" or ":",
# so "Authorization: Bearer abc" needs its own rule or the value survives.
_CREDENTIAL_SCHEME_RE = re.compile(
    r"(?i)\b(bearer|basic|token|apikey|api_key)\s+[A-Za-z0-9._~+/=\-]{6,}"
)

_MAX_FREEFORM = 240


def redact_text(value: str) -> str:
    """Strip credential-looking substrings and clamp length of free-form text."""
    scrubbed = _CREDENTIAL_SCHEME_RE.sub(lambda m: f"{m.group(1)} {_REDACTED}", value)
    scrubbed = _SENSITIVE_KEY_RE.sub(lambda m: f"{m.group(1)}={_REDACTED}", scrubbed)
    scrubbed = _PATH_RE.sub(_REDACTED, scrubbed)
    if len(scrubbed) > _MAX_FREEFORM:
        scrubbed = scrubbed[:_MAX_FREEFORM] + "…[truncated]"
    return scrubbed


def redact_mapping(value: dict[str, Any]) -> dict[str, Any]:
    """Return a copy of ``value`` with sensitive keys replaced."""
    out: dict[str, Any] = {}
    for key, item in value.items():
        if key.lower() in _SENSITIVE_KEYS:
            out[key] = _REDACTED
        elif isinstance(item, dict):
            out[key] = redact_mapping(item)
        elif isinstance(item, str):
            out[key] = redact_text(item)
        else:
            out[key] = item
    return out


class RedactingFormatter(logging.Formatter):
    """Formatter that redacts the message and any ``extra`` context fields."""

    def __init__(self, *, json_output: bool = False) -> None:
        super().__init__()
        self.json_output = json_output

    def format(self, record: logging.LogRecord) -> str:
        message = redact_text(record.getMessage())
        context = redact_mapping(
            {
                key: value
                for key, value in record.__dict__.items()
                if key in {"request_id", "user_id", "document_id", "job_id", "event"}
                and value is not None
            }
        )
        if self.json_output:
            payload = {
                "level": record.levelname,
                "logger": record.name,
                "message": message,
                **context,
            }
            return json.dumps(payload, ensure_ascii=False, sort_keys=True)
        suffix = " ".join(f"{k}={v}" for k, v in context.items())
        base = f"{record.levelname} {record.name}: {message}"
        return f"{base} | {suffix}" if suffix else base


def configure_logging(level: str, *, json_output: bool = False) -> None:
    """Install a redacting handler on the root logger."""
    root = logging.getLogger()
    for handler in list(root.handlers):
        root.removeHandler(handler)
    handler = logging.StreamHandler()
    handler.setFormatter(RedactingFormatter(json_output=json_output))
    root.addHandler(handler)
    root.setLevel(level.upper())
    # Third-party loggers can be chatty and occasionally echo request bodies.
    for noisy in ("multipart", "python_multipart", "urllib3", "asyncio"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
