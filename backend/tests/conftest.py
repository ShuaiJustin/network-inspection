import gc
import os
from pathlib import Path
import shutil
import sys
import tempfile


_TEST_DATA_DIR = Path(tempfile.mkdtemp(prefix="network-inspection-tests-"))
os.environ["NETWORK_INSPECTION_DATA_DIR"] = str(_TEST_DATA_DIR)
os.environ["NETWORK_INSPECTION_DISABLE_DB_POOL"] = "1"


def pytest_sessionfinish(session, exitstatus):
    for module in tuple(sys.modules.values()):
        client = getattr(module, "client", None)
        if client is not None and client.__class__.__name__ == "TestClient":
            client.close()

    from sqlalchemy.orm import close_all_sessions

    from app.db import engine

    close_all_sessions()
    engine.dispose()
    gc.collect()
    shutil.rmtree(_TEST_DATA_DIR)
