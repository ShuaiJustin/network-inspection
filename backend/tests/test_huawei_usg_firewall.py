import asyncio
from pathlib import Path

from app.models.device import AppSettings, Device, InspectionTask
from app.services import collectors, inspection
from app.services.collectors import ProtocolResult, SSH_COMMAND_ITEMS
from app.services.parsers import parse_ssh_output


FIXTURE = Path(__file__).parent / "fixtures" / "huawei_usg6330_full.txt"


def _parsed_usg_output():
    return parse_ssh_output(
        "Huawei",
        FIXTURE.read_text(encoding="utf-8"),
        SSH_COMMAND_ITEMS["Huawei"],
    )


def test_usg6330_full_output_is_parsed_per_command_and_classified():
    parsed = _parsed_usg_output()

    assert parsed.metrics["boards"]["total"] == 2
    assert parsed.metrics["boards"]["abnormal"] == 0
    assert parsed.metrics["power_supplies"]["total"] == 1
    assert parsed.metrics["power_supplies"]["abnormal"] == 1
    assert parsed.metrics["power_supplies"]["components"][0]["alarms"][0]["name"] == "hwPowerRemove"
    assert parsed.metrics["fans"]["total"] == 1
    assert parsed.metrics["fans"]["abnormal"] == 0
    assert parsed.metrics["temperature"]["sensors"] == 3
    assert parsed.metrics["temperature"]["max_current"] == 51
    assert parsed.metrics["temperature"]["readings"][0] == {
        "name": "0/-/0",
        "current": 36,
        "unit": "C",
        "status": "normal",
        "thresholds": {"lower": 0, "upper": 63},
    }
    assert parsed.metrics["active_alarms"]["total"] == 3
    assert parsed.metrics["link_aggregation"] == {"groups": 0, "degraded": 0}
    assert parsed.metrics["routing_neighbors"] == {"total": 0, "established": 0}
    assert parsed.metrics["ntp"] == {"synchronized": False}
    assert "configuration_saved" not in parsed.metrics
    filesystem = parsed.metrics["storage"]["filesystems"][0]
    assert filesystem["media_type"] == "hda"
    assert filesystem["used_percent"] == 60
    assert filesystem["total_bytes"] == 1172 * 1024 ** 2
    assert filesystem["free_bytes"] == 464 * 1024 ** 2
    assert "access" not in filesystem
    assert "status" not in filesystem
    assert parsed.unsupported_checks == {"configuration_saved"}


def test_usg6330_full_inspection_keeps_faults_and_unverifiable_items_separate(monkeypatch):
    parsed = _parsed_usg_output()
    device = Device(
        name="USG6330",
        ip="172.16.51.1",
        vendor="Huawei",
        device_type="firewall",
        snmp_version="v3",
        ssh_username="ops",
        ssh_password="encrypted-password",
    )
    task = InspectionTask(name="USG full", inspection_level="full", collection_mode="auto")

    async def fake_snmp(*_args, **_kwargs):
        return ProtocolResult(
            True,
            "SNMP",
            metrics={
                "hostname": "USG6330",
                "model": "Huawei USG6330 VRP V500R005C20SPC500",
                "uptime": "80d 21h 55m 36s",
                "cpu": 8,
                "memory": 43,
                "interfaces_total": 16,
                "interfaces_up": 10,
                "interfaces_down": 6,
                "interface_collection_status": "success",
                "interface_error_count": 0,
            },
        )

    async def fake_ssh(*_args, **_kwargs):
        return ProtocolResult(
            True,
            "SSH",
            metrics=parsed.metrics,
            unsupported_checks=parsed.unsupported_checks,
            unsupported_reasons=parsed.unsupported_reasons,
            item_messages=parsed.item_messages,
        )

    monkeypatch.setattr(inspection, "collect_snmp", fake_snmp)
    monkeypatch.setattr(inspection, "collect_ssh", fake_ssh)

    result = asyncio.run(inspection.inspect_device(task, device, AppSettings()))
    items = result.metrics["inspection_items"]

    assert items["power_supplies"]["conclusion"] == "critical"
    assert items["fans"]["conclusion"] == "normal"
    assert items["temperature"]["conclusion"] == "normal"
    assert items["link_aggregation"]["conclusion"] == "not_applicable"
    assert items["routing_neighbors"]["conclusion"] == "not_applicable"
    # Capacity alone does not prove that the filesystem is writable and healthy.
    assert items["storage"]["conclusion"] == "unsupported"
    assert items["ntp"]["conclusion"] == "warning"
    assert items["configuration_saved"]["conclusion"] == "unsupported"
    assert result.conclusion == "critical"
    assert result.collection_status == "success"


def test_collect_ssh_propagates_per_command_capability_states(monkeypatch):
    raw = FIXTURE.read_text(encoding="utf-8")
    sections = {}
    current = None
    for line in raw.splitlines():
        if line.startswith("$ "):
            current = line[2:]
            sections[current] = []
        elif current is not None:
            sections[current].append(line)

    class FakeConnection:
        def __init__(self, **_kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def send_command(self, command, **_kwargs):
            return "\n".join(sections[command]) if command in sections else "Error: Wrong parameter found"

    device = Device(
        name="USG6330",
        ip="172.16.51.1",
        vendor="Huawei",
        device_type="firewall",
        ssh_username="ops",
        ssh_password="encrypted-password",
    )
    device.ssh_host_fingerprint = "SHA256:trusted"
    monkeypatch.setattr(collectors, "decrypt_secret", lambda value: value)
    monkeypatch.setattr(
        collectors,
        "_verify_ssh_host_fingerprint",
        lambda *_args: ProtocolResult(True, "SSH", host_fingerprint="SHA256:trusted"),
    )
    monkeypatch.setattr(collectors, "ConnectHandler", FakeConnection)

    result = collectors._collect_ssh_sync(
        device,
        "full",
        required_checks={
            "boards", "power_supplies", "fans", "temperature",
            "active_alarms", "link_aggregation", "routing_neighbors", "ntp", "storage",
            "configuration_saved",
        },
    )

    assert result.ok is True
    assert result.metrics["power_supplies"]["total"] == 1
    assert result.metrics["power_supplies"]["abnormal"] == 1
    assert result.metrics["temperature"]["max_current"] == 51
    assert result.metrics["storage"]["used_percent"] == 60
    assert result.item_references["temperature"] == "display health"
    assert result.item_references["storage"] == "display health"
    assert result.unsupported_checks == {"configuration_saved"}
    assert result.item_messages["link_aggregation"] == "No link aggregation is configured"


def test_collect_ssh_keeps_successful_metrics_when_one_command_fails(monkeypatch):
    class FakeConnection:
        def __init__(self, **_kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def send_command(self, command, **_kwargs):
            if command == "display cpu-usage":
                return "CPU utilization for five seconds: 20%"
            raise RuntimeError("command read timeout")

    device = Device(
        name="USG6330",
        ip="172.16.51.1",
        vendor="Huawei",
        device_type="firewall",
        ssh_username="ops",
        ssh_password="encrypted-password",
        ssh_host_fingerprint="SHA256:trusted",
    )
    monkeypatch.setattr(collectors, "decrypt_secret", lambda value: value)
    monkeypatch.setattr(
        collectors,
        "_verify_ssh_host_fingerprint",
        lambda *_args: ProtocolResult(True, "SSH", host_fingerprint="SHA256:trusted"),
    )
    monkeypatch.setattr(collectors, "ConnectHandler", FakeConnection)

    result = collectors._collect_ssh_sync(
        device,
        "quick",
        required_checks={"cpu", "memory"},
    )

    assert result.ok is True
    assert result.metrics["cpu"] == 20
    assert result.failed_checks == {"memory"}
    assert "memory" in result.failed_reasons


def test_enable_password_failure_is_classified_as_authentication(monkeypatch):
    class FakeConnection:
        def __init__(self, **_kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def enable(self):
            raise RuntimeError("Failed to enter enable mode: incorrect enable password")

    device = Device(
        name="USG6330",
        ip="172.16.51.1",
        vendor="Huawei",
        device_type="firewall",
        ssh_username="ops",
        ssh_password="encrypted-password",
        enable_password="encrypted-enable-password",
        ssh_host_fingerprint="SHA256:trusted",
    )
    monkeypatch.setattr(collectors, "decrypt_secret", lambda value: value)
    monkeypatch.setattr(
        collectors,
        "_verify_ssh_host_fingerprint",
        lambda *_args: ProtocolResult(True, "SSH", host_fingerprint="SHA256:trusted"),
    )
    monkeypatch.setattr(collectors, "ConnectHandler", FakeConnection)

    result = collectors._collect_ssh_sync(device, "quick")

    assert result.ok is False
    assert result.failure_stage == "authentication"
    assert result.authentication_failed is True


def test_startup_file_path_alone_does_not_prove_configuration_is_saved():
    parsed = parse_ssh_output(
        "Huawei",
        "$ display startup\nStartup saved-configuration file: flash:/vrpcfg.cfg\n",
        SSH_COMMAND_ITEMS["Huawei"],
    )

    assert "configuration_saved" not in parsed.metrics
    assert "configuration_saved" in parsed.unsupported_checks
    assert "does not prove" in parsed.unsupported_reasons["configuration_saved"]


def test_explicit_configuration_comparison_is_parsed():
    saved = parse_ssh_output(
        "Huawei",
        "$ display startup\nCurrent configuration is the same as saved configuration.\n",
        SSH_COMMAND_ITEMS["Huawei"],
    )
    unsaved = parse_ssh_output(
        "Huawei",
        "$ display startup\nCurrent configuration has unsaved changes.\n",
        SSH_COMMAND_ITEMS["Huawei"],
    )

    assert saved.metrics["configuration_saved"] == {"saved": True}
    assert unsaved.metrics["configuration_saved"] == {"saved": False}


def test_huawei_compare_configuration_output_is_parsed():
    saved = parse_ssh_output(
        "Huawei",
        "$ compare configuration\n"
        "The current configuration is the same as the next startup configuration file.\n",
        SSH_COMMAND_ITEMS["Huawei"],
    )
    unsaved = parse_ssh_output(
        "Huawei",
        "$ compare configuration\n"
        "The current configuration is not the same as the next startup configuration file.\n"
        "====== configuration line 14 ======\n",
        SSH_COMMAND_ITEMS["Huawei"],
    )

    assert saved.metrics["configuration_saved"] == {"saved": True}
    assert unsaved.metrics["configuration_saved"] == {"saved": False}
