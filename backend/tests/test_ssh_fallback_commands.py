import asyncio

from app.models.device import Device
from app.services import collectors
from app.services.collectors import ProtocolResult


class _FakeConnection:
    responses: dict[str, str]
    calls: list[str]

    def __init__(self, **_kwargs):
        self.calls = type(self).calls

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def send_command(self, command, **_kwargs):
        self.calls.append(command)
        return type(self).responses[command]


def _device(vendor="Huawei", platform_id="huawei-vrp"):
    return Device(
        name="Fallback-SW",
        ip="192.0.2.51",
        vendor=vendor,
        platform_id=platform_id,
        device_type="switch",
        ssh_username="ops",
        ssh_password="secret",
    )


def _patch_connection(monkeypatch, responses):
    _FakeConnection.responses = responses
    _FakeConnection.calls = []
    monkeypatch.setattr(collectors, "ConnectHandler", _FakeConnection)
    monkeypatch.setattr(collectors, "decrypt_secret", lambda value: value)
    monkeypatch.setattr(
        collectors,
        "_verify_ssh_host_fingerprint",
        lambda *_args, **_kwargs: ProtocolResult(True, "SSH"),
    )
    return _FakeConnection.calls


def test_huawei_memory_fallback_is_skipped_when_primary_command_has_data(monkeypatch):
    calls = _patch_connection(monkeypatch, {
        "display memory-usage": "Memory Using Percentage Is: 42%",
        "display memory": "Memory Using Percentage: 99%",
    })

    result = collectors._collect_ssh_sync(_device(), "quick", required_checks={"memory"})

    assert result.ok is True
    assert result.metrics["memory"] == 42
    assert calls == ["display memory-usage"]
    assert result.item_references["memory"] == "display memory-usage"


def test_huawei_memory_fallback_runs_when_primary_command_is_unsupported(monkeypatch):
    calls = _patch_connection(monkeypatch, {
        "display memory-usage": "Error: Unrecognized command found at '^' position.",
        "display memory": "Memory Using Percentage: 68%",
    })

    result = collectors._collect_ssh_sync(_device(), "quick", required_checks={"memory"})

    assert result.ok is True
    assert result.metrics["memory"] == 68
    assert calls == ["display memory-usage", "display memory"]
    assert result.item_references["memory"] == "display memory"


def test_backup_collector_rejects_paged_configuration_before_returning_success(monkeypatch):
    _patch_connection(monkeypatch, {
        "display current-configuration": (
            "#\nsysname Fallback-SW\n#\ninterface GigabitEthernet0/0/1\n"
            " description uplink\n---- More ----\n#\nreturn\n"
        ),
    })

    result = asyncio.run(collectors.backup_configuration(_device(), 1, 1))

    assert result.ok is False
    assert result.failure_stage == "validation"
    assert "paging marker" in result.message
