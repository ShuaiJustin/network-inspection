from io import BytesIO

from fastapi.testclient import TestClient

from app.models.device import Base
from app.db import engine
from app.routers import excel as excel_router
from app.routers import system as system_router
from main import app


client = TestClient(app)


def setup_module():
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)


def _headers() -> dict[str, str]:
    client.post("/auth/bootstrap", json={"username": "admin", "password": "inspect123"})
    response = client.post("/auth/login", json={"username": "admin", "password": "inspect123"})
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


def test_excel_import_rejects_an_oversized_upload(monkeypatch):
    monkeypatch.setattr(excel_router, "MAX_EXCEL_IMPORT_SIZE", 8)

    response = client.post(
        "/excel/import",
        headers=_headers(),
        files={"file": ("devices.xlsx", BytesIO(b"x" * 9), "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
    )

    assert response.status_code == 413
    assert response.json()["detail"] == "Excel import file is too large"


def test_database_restore_rejects_an_oversized_upload_before_validation(monkeypatch):
    monkeypatch.setattr(system_router, "MAX_DATABASE_RESTORE_SIZE", 8)

    response = client.post(
        "/system/database-restore",
        headers=_headers(),
        data={"confirmed": "true"},
        files={"file": ("backup.db", BytesIO(b"x" * 9), "application/vnd.sqlite3")},
    )

    assert response.status_code == 413
    assert response.json()["detail"] == "Database backup file is too large"
