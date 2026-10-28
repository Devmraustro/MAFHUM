"""Portable database-URL rewriting for the backup/restore drill.

The bug being pinned here is platform-specific: pgserver returns a Unix-socket
URL with a query string on POSIX and a plain TCP URL on Windows. A literal
`.replace("/mafhum_test?", ...)` worked on the former and silently no-oped on
the latter. These tests cover BOTH shapes without needing a Windows host.
"""

from __future__ import annotations

import pytest
from sqlalchemy.engine import make_url

from tests.conftest import TEST_DB_NAME, database_url_with_name

POSIX_SOCKET = "postgresql+psycopg://mafhum@/mafhum_test?host=/tmp/mafhum-pg-xyz/socket"
WINDOWS_TCP = "postgresql+psycopg://mafhum:staging-only@127.0.0.1:5432/mafhum_test"
IPV6_TCP = "postgresql+psycopg://mafhum:pw@[::1]:5432/mafhum_test?sslmode=disable"


@pytest.mark.parametrize("original", [POSIX_SOCKET, WINDOWS_TCP, IPV6_TCP])
def test_database_component_is_replaced_on_every_platform(original: str) -> None:
    rewritten = database_url_with_name(original, "mafhum_restore_test")
    assert make_url(rewritten).database == "mafhum_restore_test"
    assert make_url(original).database == TEST_DB_NAME
    assert "/mafhum_test?" not in rewritten and "/mafhum_test" not in rewritten


def test_posix_socket_query_string_is_preserved_verbatim() -> None:
    rewritten = database_url_with_name(POSIX_SOCKET, "postgres")
    assert rewritten.endswith("?host=/tmp/mafhum-pg-xyz/socket")
    assert "%2F" not in rewritten, "percent-encoding the socket path breaks libpq"
    assert make_url(rewritten).database == "postgres"


def test_windows_style_url_needs_no_query_string() -> None:
    rewritten = database_url_with_name(WINDOWS_TCP, "postgres")
    assert "?" not in rewritten
    assert rewritten.endswith("/postgres")
    assert make_url(rewritten).database == "postgres"


def test_percent_encoded_credentials_are_untouched() -> None:
    original = "postgresql+psycopg://user:p%40ss%2Fword@localhost:5432/mafhum_test"
    rewritten = database_url_with_name(original, "scratch")
    assert "p%40ss%2Fword" in rewritten
    assert make_url(rewritten).database == "scratch"


@pytest.mark.parametrize("bad", ["", "db;DROP", "../escape", "a-b", "1abc", "db name"])
def test_unsafe_database_names_are_rejected(bad: str) -> None:
    with pytest.raises(ValueError, match="unsafe database name"):
        database_url_with_name(WINDOWS_TCP, bad)


def test_url_without_a_database_component_is_rejected() -> None:
    with pytest.raises(ValueError):
        database_url_with_name("postgresql+psycopg://user@localhost:5432", "scratch")
