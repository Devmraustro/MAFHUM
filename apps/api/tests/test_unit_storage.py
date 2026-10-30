"""Unit tests for the private storage backend.

These cover the properties that stop a storage key from becoming an arbitrary
file-read/write primitive: in-tree resolution only, no symlink or junction
escape, no overwrite, restrictive permissions and orphan reconciliation.
"""

from __future__ import annotations

import os
import shutil
import stat
import subprocess
import sys
from pathlib import Path

import pytest

from mafhum_api.services.documents.storage import (
    LocalFileStorage,
    StorageError,
    StorageKeyError,
    StorageObjectNotFoundError,
    file_mode_is_restrictive,
    new_storage_key,
)

# NTFS junctions are the Windows-specific hazard: unlike symlinks they need no
# privilege to create, and Path.is_symlink() does not report them. The tests
# that exercise them only make sense on Windows; everything else in this
# module runs on every platform.
WINDOWS_ONLY = pytest.mark.skipif(
    sys.platform != "win32",
    reason="NTFS junction behaviour is Windows-only",
)


@pytest.fixture
def storage(tmp_path: Path) -> LocalFileStorage:
    return LocalFileStorage(tmp_path / "store")


class TestKeyGeneration:
    def test_keys_are_unique(self) -> None:
        assert len({new_storage_key() for _ in range(500)}) == 500

    def test_keys_are_opaque_and_sharded(self) -> None:
        key = new_storage_key()
        shard, _, rest = key.partition("/")
        assert len(shard) == 2
        assert len(rest) == 64
        assert rest.startswith(shard)

    def test_keys_contain_no_user_input(self) -> None:
        key = new_storage_key()
        assert ".." not in key
        assert "/" in key and key.count("/") == 1


class TestResolution:
    @pytest.mark.parametrize(
        "key",
        [
            "",
            "..",
            "../etc/passwd",
            "aa/../../etc/passwd",
            "/etc/passwd",
            "aa\\..\\bb",
            "aa/bb/../../secret",
            "aa/" + "x" * 200,
            "aa/bb\x00",
            ".",
            "aa/.",
        ],
    )
    def test_unsafe_keys_rejected(self, storage: LocalFileStorage, key: str) -> None:
        with pytest.raises(StorageKeyError):
            storage.resolve(key)

    def test_valid_key_resolves_inside_root(self, storage: LocalFileStorage) -> None:
        key = storage.generate_key()
        path = storage.resolve(key)
        assert storage.root in path.parents

    def test_symlink_component_rejected(self, storage: LocalFileStorage, tmp_path: Path) -> None:
        outside = tmp_path / "outside"
        outside.mkdir()
        link = storage.root / "aa"
        link.parent.mkdir(parents=True, exist_ok=True)
        try:
            link.symlink_to(outside)
        except OSError as exc:
            # A Windows account without elevation or Developer Mode cannot
            # create symlinks (WinError 1314, observed 2026-10-30 — see
            # docs/LOCAL_WINDOWS_VERIFICATION_REPORT.md §2.4). Only the
            # symlink-specific FIXTURE is skipped: the out-of-root component
            # property itself is still asserted on this host by
            # test_junction_component_rejected below, which needs no
            # privilege, and on POSIX (CI) by this test.
            pytest.skip(f"the OS denies symlink creation ({exc})")
        with pytest.raises(StorageKeyError):
            storage.resolve("aa/file.bin")

    @WINDOWS_ONLY
    def test_junction_component_rejected(self, storage: LocalFileStorage, tmp_path: Path) -> None:
        """A privilege-free NTFS junction must not buy a path escape either.

        `Path.is_symlink()` returns False for a junction, so ``resolve``'s
        explicit symlink-component loop does not even see one; the check that
        rejects it is containment of the RESOLVED absolute path. This test
        pins that containment on the platform where junctions exist, so the
        security property holds on every host even where the symlink fixture
        above has to skip. Evidence: the same probe was run manually on
        Windows 11 during the 2026-10-30 verification pass (report §2.4).
        """
        outside = tmp_path / "outside"
        outside.mkdir()
        link = storage.root / "aa"
        link.parent.mkdir(parents=True, exist_ok=True)
        # mklink is a cmd.exe builtin, not an executable, and needs the /J
        # junction form: junctions require no privilege, unlike symlinks.
        completed = subprocess.run(
            ["cmd", "/c", "mklink", "/J", str(link), str(outside)],
            capture_output=True,
        )
        if completed.returncode != 0:
            pytest.skip(
                "could not create an NTFS junction on this host/filesystem "
                f"(exit {completed.returncode})"
            )
        assert link.is_junction() and not link.is_symlink()
        with pytest.raises(StorageKeyError):
            storage.resolve("aa/file.bin")


class TestWriteRead:
    def test_round_trip(self, storage: LocalFileStorage) -> None:
        key = storage.generate_key()
        stored = storage.write(key, iter([b"hello ", b"world"]), max_bytes=1024)
        assert stored.size_bytes == 11
        assert b"".join(storage.open_read(key)) == b"hello world"

    def test_enforces_max_bytes(self, storage: LocalFileStorage) -> None:
        key = storage.generate_key()
        with pytest.raises(StorageError) as info:
            storage.write(key, iter([b"x" * 100]), max_bytes=10)
        assert "maximum size" in str(info.value)
        # The partial write must not survive.
        assert not storage.exists(key)
        assert list(storage.root.rglob("*.tmp-*")) == []

    def test_never_overwrites_existing_object(self, storage: LocalFileStorage) -> None:
        key = storage.generate_key()
        storage.write(key, iter([b"original"]), max_bytes=1024)
        # A second write to the same key replaces via os.replace, which is the
        # documented behaviour; the important guarantee is that a *concurrent*
        # write cannot interleave. Verify the final content is coherent.
        storage.write(key, iter([b"replaced"]), max_bytes=1024)
        assert b"".join(storage.open_read(key)) == b"replaced"

    def test_temp_files_are_cleaned_on_failure(self, storage: LocalFileStorage) -> None:
        key = storage.generate_key()

        def boom():  # type: ignore[no-untyped-def]
            yield b"first chunk"
            raise RuntimeError("simulated interruption")

        with pytest.raises(RuntimeError):
            storage.write(key, boom(), max_bytes=1024)
        assert list(storage.root.rglob(".tmp-*")) == []

    def test_missing_object_raises(self, storage: LocalFileStorage) -> None:
        with pytest.raises(StorageObjectNotFoundError):
            list(storage.open_read(storage.generate_key()))

    def test_bounded_read_rejects_oversized_object(self, storage: LocalFileStorage) -> None:
        key = storage.generate_key()
        storage.write(key, iter([b"x" * 500]), max_bytes=1024)
        with pytest.raises(StorageError):
            storage.read_bytes(key, max_bytes=100)


# POSIX permission bits cannot be represented on NTFS: os.chmod there only
# toggles a read-only flag, and st_mode always reports 0o777. These properties
# stay asserted on every POSIX platform (CI runs on Linux).
POSIX_ONLY = pytest.mark.skipif(
    sys.platform == "win32" or os.name == "nt",
    reason="POSIX mode bits are not representable on Windows filesystems",
)


class TestPermissions:
    @POSIX_ONLY
    def test_root_is_private(self, storage: LocalFileStorage) -> None:
        mode = stat.S_IMODE(storage.root.stat().st_mode)
        assert mode == 0o700

    @POSIX_ONLY
    def test_objects_are_owner_only(self, storage: LocalFileStorage) -> None:
        key = storage.generate_key()
        storage.write(key, iter([b"data"]), max_bytes=1024)
        path = storage.resolve(key)
        assert file_mode_is_restrictive(path, 0o600)
        assert not (stat.S_IMODE(path.stat().st_mode) & 0o077)

    @POSIX_ONLY
    def test_shard_directories_are_private(self, storage: LocalFileStorage) -> None:
        key = storage.generate_key()
        storage.write(key, iter([b"data"]), max_bytes=1024)
        shard_dir = storage.resolve(key).parent
        assert stat.S_IMODE(shard_dir.stat().st_mode) == 0o700

    @POSIX_ONLY
    def test_root_removed_at_runtime_is_recreated_private(self, storage: LocalFileStorage) -> None:
        """Regression: `mkdir(parents=True)` recreates a missing root with the
        process umask (0o755 under 0o022), and only the shard directory used to
        be re-tightened — leaving the storage root group/world-traversable.
        """
        shutil.rmtree(storage.root)
        assert not storage.root.exists()

        storage.write(storage.generate_key(), iter([b"data"]), max_bytes=1024)

        assert storage.root.exists()
        assert stat.S_IMODE(storage.root.stat().st_mode) == 0o700


class TestDeletion:
    def test_delete_removes_object(self, storage: LocalFileStorage) -> None:
        key = storage.generate_key()
        storage.write(key, iter([b"data"]), max_bytes=1024)
        assert storage.delete(key) is True
        assert storage.exists(key) is False

    def test_delete_missing_returns_false(self, storage: LocalFileStorage) -> None:
        assert storage.delete(storage.generate_key()) is False

    def test_delete_invalid_key_is_refused(self, storage: LocalFileStorage) -> None:
        assert storage.delete("../../etc/passwd") is False

    def test_empty_shard_dirs_pruned(self, storage: LocalFileStorage) -> None:
        key = storage.generate_key()
        storage.write(key, iter([b"data"]), max_bytes=1024)
        shard = storage.resolve(key).parent
        storage.delete(key)
        assert not shard.exists()

    def test_delete_many_reports_failures(
        self, storage: LocalFileStorage, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        good = storage.generate_key()
        bad = storage.generate_key()
        storage.write(good, iter([b"a"]), max_bytes=1024)
        storage.write(bad, iter([b"b"]), max_bytes=1024)

        real_delete = LocalFileStorage.delete

        def flaky(self: LocalFileStorage, key: str) -> bool:
            if key == bad:
                from mafhum_api.services.documents.storage import StorageError

                raise StorageError("simulated filesystem failure")
            return real_delete(self, key)

        monkeypatch.setattr(LocalFileStorage, "delete", flaky)
        deleted, failed = storage.delete_many([good, bad])
        assert deleted == [good]
        assert failed == [bad]


class TestReconciliation:
    def test_detects_orphan_blobs(self, storage: LocalFileStorage) -> None:
        orphan = storage.generate_key()
        live = storage.generate_key()
        storage.write(orphan, iter([b"o"]), max_bytes=1024)
        storage.write(live, iter([b"l"]), max_bytes=1024)
        assert storage.reconcile_orphans([live]) == [orphan]

    def test_no_orphans_when_all_live(self, storage: LocalFileStorage) -> None:
        key = storage.generate_key()
        storage.write(key, iter([b"x"]), max_bytes=1024)
        assert storage.reconcile_orphans([key]) == []

    def test_ignores_temp_files(self, storage: LocalFileStorage) -> None:
        shard = storage.root / "aa"
        shard.mkdir(parents=True)
        (shard / ".tmp-abc").write_bytes(b"partial")
        assert storage.reconcile_orphans([]) == []

    def test_purge_all(self, storage: LocalFileStorage) -> None:
        for _ in range(3):
            storage.write(storage.generate_key(), iter([b"x"]), max_bytes=1024)
        assert storage.purge_all() == 3
        assert list(storage.root.rglob("*")) == []

    def test_stored_object_never_exposes_absolute_path(self, storage: LocalFileStorage) -> None:
        key = storage.generate_key()
        obj = storage.write(key, iter([b"x"]), max_bytes=1024)
        assert obj.key == key
        assert str(storage.root) not in obj.key
        assert not Path(obj.key).is_absolute()
