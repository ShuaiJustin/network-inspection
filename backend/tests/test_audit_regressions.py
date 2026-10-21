import asyncio
from datetime import datetime, timedelta, timezone

import pytest

from app.models.device import Device, InspectionTask
from app.routers import auth as auth_router
from app.routers.devices import _response as device_response
from app.routers.tasks import _task_response
from app.services import collectors
from app.services.device_import_columns import normalize_import_headers
from app.services.time import utc_for_api


def test_api_datetime_marks_sqlite_naive_values_as_utc():
    value = utc_for_api(datetime(2026, 8, 20, 5, 30, 0))

    assert value.tzinfo == timezone.utc
    assert value.isoformat().endswith("+00:00")


def test_task_response_uses_timezone_aware_api_timestamps():
    task = InspectionTask(
        id=1,
        name="timezone",
        status="completed",
        inspection_level="quick",
        collection_mode="snmp",
        created_at=datetime(2026, 8, 20, 5, 30, 0),
    )

    response = _task_response(task)

    assert response["created_at"].utcoffset() == timedelta(0)


def test_duplicate_excel_headers_are_rejected_after_translation():
    with pytest.raises(ValueError, match="Duplicate Excel columns: ip"):
        normalize_import_headers(["管理 IP", "ip", "厂商", "设备类型"])


def test_legacy_platform_keeps_device_visible_for_repair():
    device = Device(
        id=1,
        ip="192.0.2.20",
        vendor="Huawei",
        platform_id="retired-platform",
        device_type="switch",
        ssh_port=22,
        snmp_port=161,
        is_active=True,
    )

    assert device_response(device)["platform_id"] == "retired-platform"


def test_invalid_platform_is_a_structured_collection_failure():
    device = Device(
        ip="192.0.2.21",
        vendor="Huawei",
        platform_id="retired-platform",
        device_type="switch",
        snmp_version="v2c",
        snmp_community="not-read",
    )

    result = asyncio.run(collectors.test_snmp_connectivity(device, timeout=1, retries=0))

    assert result.ok is False
    assert result.failure_stage == "configuration"
    assert "platform configuration" in (result.message or "")


def test_session_store_evicts_oldest_tokens_at_the_limit():
    auth_router.clear_sessions()
    try:
        start = datetime(2026, 8, 20, 5, 30, 0)
        for index in range(auth_router.MAX_SESSION_TOKENS + 3):
            auth_router._create_session(start + timedelta(seconds=index))

        assert len(auth_router.SESSION_TOKENS) == auth_router.MAX_SESSION_TOKENS
    finally:
        auth_router.clear_sessions()
