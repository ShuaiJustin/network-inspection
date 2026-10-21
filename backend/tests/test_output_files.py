from concurrent.futures import ThreadPoolExecutor
import inspect
from threading import Barrier

from app.routers import backup, excel, screenshots
from app.services import output_files


def test_unique_output_path_reservation_is_atomic(tmp_path):
    reserve = getattr(output_files, "reserved_unique_file_path", None)
    assert callable(reserve)
    workers = 8
    barrier = Barrier(workers)

    def claim(_index):
        barrier.wait(timeout=5)
        with reserve(tmp_path, "same-name.xlsx") as path:
            path.write_text(str(path), encoding="utf-8")
            return path

    with ThreadPoolExecutor(max_workers=workers) as executor:
        paths = list(executor.map(claim, range(workers)))

    assert len(set(paths)) == workers
    assert all(path.is_file() for path in paths)


def test_output_writers_use_atomic_path_reservation():
    assert "reserved_unique_file_path" in inspect.getsource(backup._create_backups)
    assert "reserved_unique_file_path" in inspect.getsource(excel.export_report)
    assert "reserved_unique_file_path" in inspect.getsource(screenshots.save_result_screenshot)


def test_writable_directory_probe_cleanup_error_does_not_escape(monkeypatch):
    class FailingProbe:
        def touch(self, **_kwargs):
            raise OSError("probe creation failed")

        def unlink(self, **_kwargs):
            raise OSError("probe cleanup failed")

    class FakeDirectory:
        def is_dir(self):
            return True

        def __truediv__(self, _name):
            return FailingProbe()

    monkeypatch.setattr(output_files, "Path", lambda _value: FakeDirectory())

    assert output_files.is_writable_directory("unused") is False
