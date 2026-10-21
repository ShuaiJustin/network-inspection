"""Credentials that Windows DPAPI cannot decrypt (restored on another Windows
user or corrupted) must surface as an authentication failure, not crash.
The requirements doc explicitly anticipates this after a cross-user restore.
"""

import asyncio

import pytest

from app.db import SessionLocal
from app.models.device import Device
from app.services.collectors import collect_snmp, collect_ssh


UNDECRYPTABLE = "bm90LW15LWJsb2I="  # valid base64, not a DPAPI blob for this user


@pytest.fixture
def foreign_device():
    session = SessionLocal()
    ip = "10.211.0.1"
    session.query(Device).filter(Device.ip == ip).delete(synchronize_session=False)
    session.commit()
    device = Device(
        name="FOREIGN", ip=ip, vendor="Huawei", device_type="firewall",
        ssh_port=22, snmp_port=161, snmp_version="v2c",
        snmp_community=UNDECRYPTABLE,
        ssh_username="ops", ssh_password=UNDECRYPTABLE,
        is_active=True,
    )
    session.add(device)
    session.commit()
    yield device
    session.query(Device).filter(Device.ip == ip).delete(synchronize_session=False)
    session.commit()
    session.close()


def test_snmp_reports_a_credential_error_instead_of_crashing(foreign_device):
    result = asyncio.run(collect_snmp(foreign_device, timeout=1, retries=0))
    assert result.ok is False
    assert result.failure_stage == "authentication"
    assert "credential" in (result.message or "").lower()


def test_ssh_reports_a_credential_error_instead_of_crashing(foreign_device):
    result = asyncio.run(
        collect_ssh(foreign_device, "quick", connection_timeout=1, command_timeout=1)
    )
    assert result.ok is False
    assert result.failure_stage == "authentication"
    assert "credential" in (result.message or "").lower()


def test_the_error_never_leaks_the_stored_blob(foreign_device):
    result = asyncio.run(collect_snmp(foreign_device, timeout=1, retries=0))
    assert UNDECRYPTABLE not in (result.message or "")
