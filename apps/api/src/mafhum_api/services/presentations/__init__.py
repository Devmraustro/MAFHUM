"""Editable presentation domain.

Intentionally light: importing a submodule must not pull in SQLAlchemy,
FastAPI or the database models. The benchmark tool runs in a bare virtualenv
with only python-pptx installed, so package-level imports stay side-effect free.
"""

__all__: list[str] = []
