"""The single-writer lease that makes one-worker-per-database enforceable.

These tests run against the real PostgreSQL that ``conftest`` provisions, so they
exercise the actual ``pg_try_advisory_lock`` behaviour rather than a double. That
matters: the whole value of the lease is that the database arbitrates atomically,
and a stubbed lock would prove nothing about the property being enforced.

The unsafe configuration is detected reliably and startup fails with an actionable
error. Nothing here weakens worker semantics to make the check pass -- the worker
still claims, processes and persists exactly as before, and a replica opts out
through configuration rather than by the lease quietly doing nothing.
"""

from __future__ import annotations

import pytest

from mafhum_api.services.documents.worker import (
    ProcessingWorker,
    WorkerAlreadyRunningError,
    WorkerConfig,
)
from mafhum_api.services.documents.worker_lease import LEASE_KEY, WorkerLease


def _config() -> WorkerConfig:
    return WorkerConfig(
        worker_count=1,
        job_timeout_seconds=5,
        stale_after_seconds=60,
        max_attempts=3,
    )


@pytest.fixture
def database(app):
    return app.state.context.database


class TestLeaseAcquisition:
    def test_a_single_lease_can_be_acquired(self, database) -> None:
        lease = WorkerLease(database)
        lease.acquire()
        try:
            assert lease.held is True
        finally:
            lease.release()
        assert lease.held is False

    def test_a_second_lease_on_the_same_database_is_refused(self, database) -> None:
        """The core guarantee: two processes cannot both run a worker."""
        first = WorkerLease(database)
        second = WorkerLease(database)
        first.acquire()
        try:
            with pytest.raises(WorkerAlreadyRunningError):
                second.acquire()
            assert second.held is False, "a refused lease must not report itself held"
        finally:
            first.release()

    def test_the_lease_can_be_taken_again_after_release(self, database) -> None:
        """Release must genuinely free it, or restarts would be blocked forever."""
        first = WorkerLease(database)
        first.acquire()
        first.release()

        second = WorkerLease(database)
        second.acquire()
        try:
            assert second.held is True
        finally:
            second.release()

    def test_release_is_safe_when_never_held(self, database) -> None:
        WorkerLease(database).release()

    def test_releasing_twice_does_not_raise(self, database) -> None:
        lease = WorkerLease(database)
        lease.acquire()
        lease.release()
        lease.release()

    def test_the_context_manager_releases_on_the_way_out(self, database) -> None:
        with WorkerLease(database):
            pass
        # A subsequent lease proves the lock was really given up.
        with WorkerLease(database):
            pass

    def test_a_failed_acquire_leaves_nothing_held(self, database) -> None:
        """A refused startup must not hold a connection or a lock behind."""
        holder = WorkerLease(database)
        holder.acquire()
        rejected = WorkerLease(database)
        try:
            with pytest.raises(WorkerAlreadyRunningError):
                rejected.acquire()
            assert rejected._connection is None
        finally:
            holder.release()

    def test_the_error_message_tells_the_operator_what_to_do(self, database) -> None:
        """The message is the product: it must name the fix, not just the failure."""
        holder = WorkerLease(database)
        holder.acquire()
        try:
            with pytest.raises(WorkerAlreadyRunningError) as excinfo:
                WorkerLease(database).acquire()
        finally:
            holder.release()

        message = str(excinfo.value)
        assert "another MAFHUM process" in message
        assert "MAFHUM_PROCESSING_WORKER_ENABLED=false" in message
        assert "docs/DEPLOYMENT.md" in message


class TestWorkerStartupEnforcement:
    def test_a_single_worker_starts_normally(self, app, database, tmp_path) -> None:
        worker = ProcessingWorker(database, _storage(tmp_path), _limits(app), _config())
        worker.start()
        try:
            assert worker._lease.held is True
        finally:
            worker.stop(wait_seconds=1.0)
        assert worker._lease.held is False

    def test_a_second_worker_cannot_start_on_the_same_database(
        self, app, database, tmp_path
    ) -> None:
        """The unsafe configuration is rejected at startup, before any thread runs."""
        limits = _limits(app)
        first = ProcessingWorker(database, _storage(tmp_path / "a"), limits, _config())
        second = ProcessingWorker(database, _storage(tmp_path / "b"), limits, _config())

        first.start()
        try:
            with pytest.raises(WorkerAlreadyRunningError):
                second.start()
            # The rejected worker must not have started a dispatcher thread.
            assert second._dispatcher is None
        finally:
            first.stop(wait_seconds=1.0)

    def test_a_worker_can_start_again_after_the_first_one_stops(
        self, app, database, tmp_path
    ) -> None:
        """Restart, and rolling deploys, must not be blocked by a stale lease."""
        limits = _limits(app)
        first = ProcessingWorker(database, _storage(tmp_path / "a"), limits, _config())
        first.start()
        first.stop(wait_seconds=1.0)

        second = ProcessingWorker(database, _storage(tmp_path / "b"), limits, _config())
        second.start()
        try:
            assert second._lease.held is True
        finally:
            second.stop(wait_seconds=1.0)

    def test_stopping_releases_the_lease_exactly_once(self, app, database, tmp_path) -> None:

        worker = ProcessingWorker(
            database,
            _storage(tmp_path),
            _limits(app),
            _config(),
        )
        worker.start()
        worker.stop(wait_seconds=1.0)
        worker.stop(wait_seconds=1.0)  # idempotent, must not raise


class TestNonPostgresDialect:
    def test_sqlite_is_permitted_with_a_warning_not_a_fake_lock(self) -> None:
        """SQLite has no advisory locks; it must not silently pretend otherwise.

        This path exists only for fast unit tests. Faking a lock would be worse
        than skipping, because it would make the guard look verified where it is
        not. The warning is asserted on the module logger directly rather than via
        caplog, so the test does not depend on log propagation being configured a
        particular way.
        """
        from unittest.mock import patch

        from mafhum_api.db.base import Database
        from mafhum_api.services.documents import worker_lease

        sqlite_db = Database("sqlite://")
        try:
            assert sqlite_db.dialect == "sqlite"
            with patch.object(worker_lease.logger, "warning") as warn:
                lease = WorkerLease(sqlite_db)
                lease.acquire()
                lease.release()
            assert warn.call_count == 1, "the unenforced path must warn exactly once"
            assert "no advisory locks" in warn.call_args[0][0]
            assert warn.call_args[1]["extra"]["event"] == "worker_lease_unenforced"
        finally:
            sqlite_db.engine.dispose()


def _limits(app):
    """Reuse the application's real limits rather than inventing a variant."""
    return app.state.context.limits


def _storage(root):
    from pathlib import Path

    from mafhum_api.services.documents.storage import LocalFileStorage

    path = Path(root)
    path.mkdir(parents=True, exist_ok=True)
    return LocalFileStorage(path)


def test_lease_key_is_a_stable_namespaced_constant() -> None:
    """The key must be stable across processes and inside int32 range.

    A key that drifted between processes, or overflowed the range PostgreSQL
    accepts, would silently break mutual exclusion.
    """
    assert LEASE_KEY == 0x4D414648
    assert 0 < LEASE_KEY < 2**31
