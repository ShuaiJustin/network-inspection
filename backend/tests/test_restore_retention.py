from pathlib import Path

from app.routers import system


def _snapshots(root, count):
    paths = []
    for number in range(count):
        path = root / f"pre_restore_202608{number + 1:02d}_120000_000000.db"
        path.write_bytes(b"snapshot")
        paths.append(path)
    return paths


def test_pre_restore_retention_keeps_ten_snapshots_and_unrelated_files(tmp_path, monkeypatch):
    monkeypatch.setattr(system, "DATABASE_BACKUP_DIR", tmp_path)
    snapshots = _snapshots(tmp_path, 13)
    unrelated = tmp_path / "migration_20260801.db"
    unrelated.write_bytes(b"migration snapshot")
    other = tmp_path / "pre_restore_notes.db"
    other.write_bytes(b"not a managed snapshot")

    system._prune_pre_restore_backups(snapshots[-1])

    assert all(path.exists() for path in snapshots[-10:])
    assert all(not path.exists() for path in snapshots[:-10])
    assert unrelated.exists()
    assert other.exists()


def test_pre_restore_retention_always_keeps_the_current_snapshot(tmp_path, monkeypatch):
    monkeypatch.setattr(system, "DATABASE_BACKUP_DIR", tmp_path)
    snapshots = _snapshots(tmp_path, 13)

    system._prune_pre_restore_backups(snapshots[0])

    assert snapshots[0].exists()
    assert all(path.exists() for path in snapshots[-9:])
    assert len(list(tmp_path.glob("pre_restore_*.db"))) == 10


def test_pre_restore_retention_failure_does_not_abort_success(tmp_path, monkeypatch, caplog):
    monkeypatch.setattr(system, "DATABASE_BACKUP_DIR", tmp_path)
    snapshots = _snapshots(tmp_path, 11)
    real_unlink = Path.unlink

    def locked_file(path, *args, **kwargs):
        if path == snapshots[0]:
            raise PermissionError("snapshot is open")
        return real_unlink(path, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", locked_file)
    system._prune_pre_restore_backups(snapshots[-1])

    assert all(path.exists() for path in snapshots)
    assert "snapshot" in caplog.text.lower()
