"""Pytest configuration: environment validated before the app is imported."""

import os

os.environ.setdefault("APP_ENV", "development")
os.environ.setdefault("APP_NAME", "MAFHUM")
os.environ.setdefault("LOG_LEVEL", "DEBUG")
os.environ.setdefault("API_PREFIX", "/api/v1")
os.environ.setdefault("DATABASE_URL", "sqlite:////tmp/mafhum_api_test.db")
os.environ.setdefault("AI_PROVIDER", "mock")
os.environ.setdefault("AI_MODEL", "mock")
os.environ.setdefault("MAX_UPLOAD_MB", "20")
os.environ.setdefault("MAX_DOCUMENT_PAGES", "200")
os.environ.setdefault("MAX_OUTPUT_TOKENS", "2000")
os.environ.setdefault("REQUEST_TIMEOUT_SECONDS", "30")
os.environ.setdefault("ALLOW_DEV_ORIGINS", "http://localhost:3000")

from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402


def client():
    return TestClient(app)
