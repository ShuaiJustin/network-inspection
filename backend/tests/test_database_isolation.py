import os
from pathlib import Path

from app import db
from sqlalchemy.pool import NullPool


def test_test_suite_uses_an_isolated_database_directory():
    configured = Path(os.environ["NETWORK_INSPECTION_DATA_DIR"]).resolve()

    assert db.DATA_DIR.resolve() == configured
    assert configured != (Path(__file__).resolve().parents[1] / "data").resolve()
    assert isinstance(db.engine.pool, NullPool)
