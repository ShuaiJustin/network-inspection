import asyncio
import importlib
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from app.db import SessionLocal, init_db
from app.models.device import AppSettings, Device, InspectionResult, InspectionTask
from app.routers import tasks as tasks_router
from app.schemas.settings import SettingsUpdate
from app.schemas.device import ResultResponse
from app.services import collectors, inspection
from app.services.collectors import ProtocolResult


@pytest.mark.parametrize("ssh_cpu, expected", [(20, "normal"), (85, "warning"), (99, "critical")])
def test_ssh_recovery_replaces_failed_snmp_metrics_and_final_conclusion(monkeypatch, ssh_cpu, expected):
    device = Device(
        id=987001, name="Recovered", ip="192.0.2.201", vendor="Huawei",
        device_type="switch", ssh_username="ops", ssh_password="encrypted",
    )
    task = InspectionTask(id=987001, name="Recovered", inspection_level="quick", collection_mode="auto")
    snmp_metrics = {
        "hostname": "Recovered", "model": "VRP", "uptime": "1d",
        "cpu": 100, "memory": 30, "stp_discarding_ports": 0,
        "interfaces_total": 99, "interfaces": [],
        "interface_collection_status": "data_fetch_failed",
        "interface_collection_message": "IF-MIB status timed out",
        "interface_collection_warnings": {"oper_status": "timed out"},
    }

    async def fake_snmp(*_args, **_kwargs):
        return ProtocolResult(
            True, "SNMP", metrics=snmp_metrics,
            failed_checks={"interfaces", "cpu"},
            failed_reasons={"interfaces": "IF-MIB status timed out", "cpu": "Invalid sample"},
        )

    async def fake_ssh(*_args, **_kwargs):
        return ProtocolResult(True, "SSH", metrics={
            "cpu": ssh_cpu, "memory": 90, "interface_collection_status": "success",
            "interfaces_total": 24, "interfaces_up": 20, "interfaces_down": 4,
            "active_alarms": {"total": 0},
        })

    monkeypatch.setattr(inspection, "collect_snmp", fake_snmp)
    monkeypatch.setattr(inspection, "collect_ssh", fake_ssh)
    result = asyncio.run(inspection.inspect_device(task, device, AppSettings()))

    assert result.conclusion == expected
    assert result.collection_status == "success"
    assert result.failure_stage is None
    assert result.message is None
    assert result.metrics["cpu"] == ssh_cpu
    assert result.metrics["memory"] == 30
    assert result.metrics["interfaces_total"] == 24
    assert result.metrics["interface_collection_status"] == "success"
    assert "interface_collection_message" not in result.metrics
    assert "interface_collection_warnings" not in result.metrics
    assert "interfaces" not in result.metrics
    assert result.metrics["inspection_items"]["interfaces"]["source"] == "SSH"
    assert snmp_metrics["interface_collection_status"] == "data_fetch_failed"


def test_ssh_interface_recovery_does_not_hide_another_failed_item(monkeypatch):
    device = Device(
        id=987002, name="Partial recovery", ip="192.0.2.202", vendor="Huawei",
        device_type="switch", ssh_username="ops", ssh_password="encrypted",
    )
    task = InspectionTask(id=987002, name="Partial", inspection_level="quick", collection_mode="auto")

    async def fake_snmp(*_args, **_kwargs):
        return ProtocolResult(True, "SNMP", metrics={
            "hostname": "Partial", "model": "VRP", "uptime": "1d", "memory": 20,
            "stp_discarding_ports": 0, "interface_collection_status": "data_fetch_failed",
        }, failed_checks={"interfaces", "cpu"})

    async def fake_ssh(*_args, **_kwargs):
        return ProtocolResult(True, "SSH", metrics={
            "interface_collection_status": "success", "interfaces_total": 24,
            "active_alarms": {"total": 0},
        }, failed_checks={"cpu"}, failed_reasons={"cpu": "CPU command timed out"})

    monkeypatch.setattr(inspection, "collect_snmp", fake_snmp)
    monkeypatch.setattr(inspection, "collect_ssh", fake_ssh)
    result = asyncio.run(inspection.inspect_device(task, device, AppSettings()))

    assert result.metrics["interface_collection_status"] == "success"
    assert result.metrics["inspection_items"]["interfaces"]["conclusion"] == "normal"
    assert result.metrics["inspection_items"]["cpu"]["conclusion"] == "data_fetch_failed"
    assert result.conclusion == "data_fetch_failed"
    assert result.collection_status == "failed"


def test_auto_collection_keeps_snmp_items_and_only_requests_missing_items_from_ssh(monkeypatch):
    device = Device(
        name="Core-SW-01",
        ip="192.0.2.10",
        vendor="Huawei",
        device_type="switch",
        snmp_version="v2c",
        snmp_community="encrypted-community",
        ssh_username="ops",
        ssh_password="encrypted-password",
    )
    task = InspectionTask(name="Item fallback", inspection_level="quick", collection_mode="auto")
    observed: dict[str, object] = {}

    async def fake_snmp(*_args, **_kwargs):
        return ProtocolResult(True, "SNMP", metrics={
            "hostname": "Core-SW-01",
            "model": "Huawei Versatile Routing Platform",
            "uptime": "3d 1h 2m 3s",
            "interfaces_total": 24,
            "interface_collection_status": "success",
            "interfaces": [],
        })

    async def fake_ssh(*_args, required_checks=None, **_kwargs):
        observed["required_checks"] = required_checks
        assert required_checks == {
            "device_information", "cpu", "memory", "stp", "active_alarms",
        }
        return ProtocolResult(True, "SSH", metrics={
            "cpu": 23,
            "memory": 41,
            "stp_discarding_ports": 2,
            "active_alarms": {"total": 0, "critical": 0, "warning": 0, "alarms": []},
        })

    monkeypatch.setattr(inspection, "collect_snmp", fake_snmp)
    monkeypatch.setattr(inspection, "collect_ssh", fake_ssh)

    result = asyncio.run(inspection.inspect_device(task, device, AppSettings()))

    assert observed["required_checks"] == {
        "device_information", "cpu", "memory", "stp", "active_alarms",
    }
    items = result.metrics["inspection_items"]
    assert items["device_information"]["source"] == "SNMP"
    assert items["interfaces"]["source"] == "SNMP"
    assert items["cpu"]["source"] == "SSH"
    assert items["cpu"]["downgrade_reason"] == "SNMP value unavailable"
    assert items["memory"]["source"] == "SSH"
    assert items["stp"]["source"] == "SSH"
    assert items["active_alarms"]["source"] == "SSH"
    assert items["active_alarms"]["value"]["total"] == 0


def test_auto_collection_preserves_supplementary_ssh_authentication_failure(monkeypatch):
    device = Device(
        name="Core-SW-Auth",
        ip="192.0.2.21",
        vendor="Huawei",
        device_type="switch",
        snmp_version="v2c",
        snmp_community="encrypted-community",
        ssh_username="ops",
        ssh_password="encrypted-password",
    )
    task = InspectionTask(name="SSH auth fallback", inspection_level="quick", collection_mode="auto")

    async def fake_snmp(*_args, **_kwargs):
        return ProtocolResult(True, "SNMP", metrics={
            "hostname": "Core-SW-Auth",
            "model": "Huawei VRP",
            "uptime": "1d",
            "interfaces_total": 24,
            "interface_collection_status": "success",
        })

    async def fake_ssh(*_args, **_kwargs):
        return ProtocolResult(
            False,
            "SSH",
            failure_stage="authentication",
            message="Authentication failed",
            authentication_failed=True,
        )

    monkeypatch.setattr(inspection, "collect_snmp", fake_snmp)
    monkeypatch.setattr(inspection, "collect_ssh", fake_ssh)

    result = asyncio.run(inspection.inspect_device(task, device, AppSettings(network_retry_count=0)))

    assert result.metrics["hostname"] == "Core-SW-Auth"
    assert result.conclusion == "authentication_failed"
    assert result.authentication_status == "failed"
    assert result.failure_stage == "authentication"
    assert result.metrics["inspection_items"]["cpu"]["conclusion"] == "authentication_failed"


def test_auto_collection_preserves_supplementary_ssh_connection_failure(monkeypatch):
    device = Device(
        name="Core-SW-Timeout",
        ip="192.0.2.22",
        vendor="Huawei",
        device_type="switch",
        snmp_version="v2c",
        snmp_community="encrypted-community",
        ssh_username="ops",
        ssh_password="encrypted-password",
    )
    task = InspectionTask(name="SSH connection fallback", inspection_level="quick", collection_mode="auto")

    async def fake_snmp(*_args, **_kwargs):
        return ProtocolResult(True, "SNMP", metrics={
            "hostname": "Core-SW-Timeout",
            "model": "Huawei VRP",
            "uptime": "1d",
            "interfaces_total": 24,
            "interface_collection_status": "success",
        })

    async def fake_ssh(*_args, **_kwargs):
        return ProtocolResult(False, "SSH", failure_stage="connection", message="Connection timed out")

    monkeypatch.setattr(inspection, "collect_snmp", fake_snmp)
    monkeypatch.setattr(inspection, "collect_ssh", fake_ssh)

    result = asyncio.run(inspection.inspect_device(task, device, AppSettings(network_retry_count=0)))

    assert result.metrics["hostname"] == "Core-SW-Timeout"
    assert result.conclusion == "connection_failed"
    assert result.connection_status == "failed"
    assert result.failure_stage == "connection"
    assert result.metrics["inspection_items"]["memory"]["conclusion"] == "connection_failed"


def test_auto_failure_priority_prefers_ssh_connection_over_snmp_data_failure(monkeypatch):
    device = Device(
        name="Dual-Failure",
        ip="192.0.2.23",
        vendor="Huawei",
        device_type="switch",
        snmp_version="v2c",
        snmp_community="encrypted-community",
        ssh_username="ops",
        ssh_password="encrypted-password",
    )
    task = InspectionTask(name="Failure priority", inspection_level="quick", collection_mode="auto")

    async def fake_snmp(*_args, **_kwargs):
        return ProtocolResult(False, "SNMP", failure_stage="parsing", message="Incomplete response")

    async def fake_ssh(*_args, **_kwargs):
        return ProtocolResult(False, "SSH", failure_stage="connection", message="Connection timed out")

    monkeypatch.setattr(inspection, "collect_snmp", fake_snmp)
    monkeypatch.setattr(inspection, "collect_ssh", fake_ssh)

    result = asyncio.run(inspection.inspect_device(task, device, AppSettings(network_retry_count=0)))

    assert result.conclusion == "connection_failed"
    assert result.failure_stage == "connection"


def test_auto_success_preserves_ssh_audit_metadata_when_protocols_are_merged(monkeypatch):
    device = Device(
        name="Merged-Audit",
        ip="192.0.2.24",
        vendor="Huawei",
        device_type="switch",
        snmp_version="v2c",
        snmp_community="encrypted-community",
        ssh_username="ops",
        ssh_password="encrypted-password",
    )
    task = InspectionTask(name="Merged audit", inspection_level="quick", collection_mode="auto")
    snmp_raw = "=== SNMP scalar ===\n1.3.6.1.2.1.1.5.0 = Merged-Audit\n"
    ssh_raw = "$ display cpu-usage\nCPU utilization: 20%"

    async def fake_snmp(*_args, **_kwargs):
        return ProtocolResult(
            True,
            "SNMP",
            metrics={
                "hostname": "Merged-Audit",
                "model": "Huawei VRP",
                "uptime": "1d",
                "interfaces_total": 24,
                "interface_collection_status": "success",
            },
            raw_output=snmp_raw,
            raw_output_bytes=len(snmp_raw.encode("utf-8")),
        )

    async def fake_ssh(*_args, **_kwargs):
        return ProtocolResult(
            True,
            "SSH",
            metrics={
                "cpu": 20,
                "memory": 30,
                "stp_discarding_ports": 0,
                "active_alarms": {"total": 0, "critical": 0, "warning": 0, "alarms": []},
            },
            raw_output=ssh_raw,
            raw_output_status="complete",
            raw_output_bytes=len(ssh_raw.encode("utf-8")),
            host_fingerprint="SHA256:trusted",
            host_key_algorithm="ssh-ed25519",
        )

    monkeypatch.setattr(inspection, "collect_snmp", fake_snmp)
    monkeypatch.setattr(inspection, "collect_ssh", fake_ssh)

    result = asyncio.run(inspection.inspect_device(task, device, AppSettings(network_retry_count=0)))

    expected_raw = f"=== SNMP ===\n{snmp_raw}\n\n=== SSH ===\n{ssh_raw}"
    assert result.raw_output == expected_raw
    assert result.raw_output_bytes == len(expected_raw.encode("utf-8"))
    assert result.raw_output_status == "complete"
    assert result.metrics["_ssh_host_fingerprint"] == "SHA256:trusted"


def test_snmp_only_inspection_preserves_raw_varbind_evidence(monkeypatch):
    device = Device(
        name="SNMP-Raw",
        ip="192.0.2.27",
        vendor="Huawei",
        device_type="switch",
        snmp_version="v2c",
        snmp_community="encrypted-community",
    )
    task = InspectionTask(name="SNMP raw", inspection_level="quick", collection_mode="snmp")
    raw_output = "=== SNMP scalar ===\n1.3.6.1.2.1.1.5.0 = SNMP-Raw\n"

    async def fake_snmp(*_args, **_kwargs):
        return ProtocolResult(
            True,
            "SNMP",
            metrics={
                "hostname": "SNMP-Raw",
                "model": "Huawei VRP",
                "uptime": "1d",
                "cpu": 20,
                "memory": 30,
                "interfaces_total": 24,
                "interface_collection_status": "success",
                "stp_discarding_ports": 0,
                "stp_broken_ports": 0,
                "active_alarms": {"total": 0, "critical": 0, "warning": 0, "alarms": []},
            },
            raw_output=raw_output,
            raw_output_status="complete",
            raw_output_bytes=len(raw_output.encode("utf-8")),
        )

    monkeypatch.setattr(inspection, "collect_snmp", fake_snmp)

    result = asyncio.run(inspection.inspect_device(task, device, AppSettings(network_retry_count=0)))

    assert result.raw_output == raw_output
    assert result.raw_output_status == "complete"
    assert result.raw_output_bytes == len(raw_output.encode("utf-8"))


def test_auto_collection_bounds_the_combined_snmp_and_ssh_raw_evidence(monkeypatch):
    device = Device(
        name="Merged-Limit",
        ip="192.0.2.26",
        vendor="Huawei",
        device_type="switch",
        snmp_version="v2c",
        snmp_community="encrypted-community",
        ssh_username="ops",
        ssh_password="encrypted-password",
    )
    task = InspectionTask(name="Merged limit", inspection_level="quick", collection_mode="auto")
    snmp_raw = "1.3.6.1.2.1.1.5.0 = " + ("S" * 80)
    ssh_raw = "$ display cpu-usage\n" + ("H" * 80)
    monkeypatch.setattr(inspection, "MAX_DEVICE_RAW_OUTPUT_BYTES", 96, raising=False)

    async def fake_snmp(*_args, **_kwargs):
        return ProtocolResult(
            True,
            "SNMP",
            metrics={
                "hostname": "Merged-Limit",
                "model": "Huawei VRP",
                "uptime": "1d",
                "interfaces_total": 24,
                "interface_collection_status": "success",
            },
            raw_output=snmp_raw,
            raw_output_bytes=len(snmp_raw.encode("utf-8")),
        )

    async def fake_ssh(*_args, **_kwargs):
        return ProtocolResult(
            True,
            "SSH",
            metrics={
                "cpu": 20,
                "memory": 30,
                "stp_discarding_ports": 0,
                "active_alarms": {"total": 0, "critical": 0, "warning": 0, "alarms": []},
            },
            raw_output=ssh_raw,
            raw_output_bytes=len(ssh_raw.encode("utf-8")),
        )

    monkeypatch.setattr(inspection, "collect_snmp", fake_snmp)
    monkeypatch.setattr(inspection, "collect_ssh", fake_ssh)

    result = asyncio.run(inspection.inspect_device(task, device, AppSettings(network_retry_count=0)))

    full_raw = f"=== SNMP ===\n{snmp_raw}\n\n=== SSH ===\n{ssh_raw}"
    expected_retained = full_raw.encode("utf-8")[:96].decode("utf-8", errors="ignore")
    assert result.raw_output == expected_retained
    assert result.raw_output_status == "truncated"
    assert result.raw_output_bytes == len(full_raw.encode("utf-8"))


def test_combined_raw_evidence_preserves_an_input_truncation_and_original_byte_count():
    snmp_raw = "1.3.6.1.2.1.1.5.0 = retained"
    ssh_raw = "$ show version\nretained"
    snmp = ProtocolResult(
        True,
        "SNMP",
        raw_output=snmp_raw,
        raw_output_status="truncated",
        raw_output_bytes=512,
    )
    ssh = ProtocolResult(
        True,
        "SSH",
        raw_output=ssh_raw,
        raw_output_status="complete",
        raw_output_bytes=len(ssh_raw.encode("utf-8")),
    )

    captured = inspection._merge_protocol_raw_output(snmp, ssh)

    visible = f"=== SNMP ===\n{snmp_raw}\n\n=== SSH ===\n{ssh_raw}"
    hidden_snmp_bytes = 512 - len(snmp_raw.encode("utf-8"))
    assert captured.content == visible
    assert captured.status == "truncated"
    assert captured.truncated is True
    assert captured.byte_count == len(visible.encode("utf-8")) + hidden_snmp_bytes


def test_auto_collection_does_not_overwrite_successful_snmp_metrics_with_ssh_side_effects(monkeypatch):
    device = Device(
        name="Merge-Source-SW",
        ip="192.0.2.25",
        vendor="Huawei",
        device_type="switch",
        snmp_version="v2c",
        snmp_community="encrypted-community",
        ssh_username="ops",
        ssh_password="encrypted-password",
    )
    task = InspectionTask(name="Source consistency", inspection_level="full", collection_mode="auto")

    async def fake_snmp(*_args, **_kwargs):
        return ProtocolResult(True, "SNMP", metrics={
            "hostname": "Merge-Source-SW",
            "model": "Huawei VRP",
            "uptime": "1d",
            "interfaces_total": 48,
            "interfaces_up": 40,
            "interfaces_down": 8,
            "interface_collection_status": "success",
        })

    async def fake_ssh(*_args, **_kwargs):
        return ProtocolResult(True, "SSH", metrics={
            # The interface command was selected to collect error counters. Its
            # summary must not replace the already successful IF-MIB result.
            "interfaces_total": 12,
            "interfaces_up": 10,
            "interfaces_down": 2,
            "interface_error_count": 0,
        })

    monkeypatch.setattr(inspection, "collect_snmp", fake_snmp)
    monkeypatch.setattr(inspection, "collect_ssh", fake_ssh)

    result = asyncio.run(inspection.inspect_device(task, device, AppSettings(network_retry_count=0)))

    assert result.metrics["interfaces_total"] == 48
    assert result.metrics["interfaces_up"] == 40
    assert result.metrics["interfaces_down"] == 8
    assert result.metrics["interface_error_count"] == 0
    assert result.metrics["inspection_items"]["interfaces"]["source"] == "SNMP"
    assert result.metrics["inspection_items"]["interface_errors"]["source"] == "SSH"


def test_snmp_only_device_failure_still_has_per_item_failure_details(monkeypatch):
    device = Device(
        name="SNMP-Failed",
        ip="192.0.2.25",
        vendor="Cisco",
        device_type="router",
        snmp_version="v2c",
        snmp_community="encrypted-community",
    )
    task = InspectionTask(name="SNMP failed details", inspection_level="quick", collection_mode="snmp")

    async def fake_snmp(*_args, **_kwargs):
        return ProtocolResult(False, "SNMP", failure_stage="connection", message="SNMP timeout")

    monkeypatch.setattr(inspection, "collect_snmp", fake_snmp)

    result = asyncio.run(inspection.inspect_device(task, device, AppSettings(network_retry_count=0)))
    items = result.metrics["inspection_items"]

    assert result.conclusion == "connection_failed"
    assert set(items) == set(inspection.QUICK_INSPECTION_ITEMS)
    assert all(item["source"] == "SNMP" for item in items.values())
    assert all(item["conclusion"] == "connection_failed" for item in items.values())
    assert all(item["downgrade_reason"] == "SNMP timeout" for item in items.values())


def test_ssh_only_authentication_failure_still_has_per_item_failure_details(monkeypatch):
    device = Device(
        name="SSH-Failed",
        ip="192.0.2.26",
        vendor="Huawei",
        device_type="switch",
        ssh_username="ops",
        ssh_password="encrypted-password",
    )
    task = InspectionTask(name="SSH failed details", inspection_level="quick", collection_mode="ssh")

    async def fake_ssh(*_args, **_kwargs):
        return ProtocolResult(
            False,
            "SSH",
            failure_stage="authentication",
            message="Authentication failed",
            authentication_failed=True,
        )

    monkeypatch.setattr(inspection, "collect_ssh", fake_ssh)

    result = asyncio.run(inspection.inspect_device(task, device, AppSettings(network_retry_count=0)))
    items = result.metrics["inspection_items"]

    assert result.conclusion == "authentication_failed"
    assert all(item["source"] == "SSH" for item in items.values())
    assert all(item["conclusion"] == "authentication_failed" for item in items.values())


def test_auto_dual_failure_keeps_both_protocol_reasons_per_item(monkeypatch):
    device = Device(
        name="Dual-Failed-Details",
        ip="192.0.2.27",
        vendor="Huawei",
        device_type="switch",
        snmp_version="v2c",
        snmp_community="encrypted-community",
        ssh_username="ops",
        ssh_password="encrypted-password",
    )
    task = InspectionTask(name="Dual failed details", inspection_level="quick", collection_mode="auto")

    async def fake_snmp(*_args, **_kwargs):
        return ProtocolResult(False, "SNMP", failure_stage="connection", message="SNMP timeout")

    async def fake_ssh(*_args, **_kwargs):
        return ProtocolResult(
            False,
            "SSH",
            failure_stage="authentication",
            message="SSH wrong password",
            authentication_failed=True,
        )

    monkeypatch.setattr(inspection, "collect_snmp", fake_snmp)
    monkeypatch.setattr(inspection, "collect_ssh", fake_ssh)

    result = asyncio.run(inspection.inspect_device(task, device, AppSettings(network_retry_count=0)))
    cpu = result.metrics["inspection_items"]["cpu"]

    assert result.conclusion == "authentication_failed"
    assert cpu["source"] == "SNMP+SSH"
    assert cpu["conclusion"] == "authentication_failed"
    assert "SNMP timeout" in cpu["downgrade_reason"]
    assert "SSH wrong password" in cpu["downgrade_reason"]


def test_auto_ssh_parse_miss_reports_the_ssh_attempt_not_only_the_snmp_failure(monkeypatch):
    device = Device(
        name="Fallback-Parse-Miss",
        ip="192.0.2.28",
        vendor="Huawei",
        device_type="switch",
        snmp_version="v2c",
        snmp_community="encrypted-community",
        ssh_username="ops",
        ssh_password="encrypted-password",
    )
    task = InspectionTask(name="Fallback parse miss", inspection_level="quick", collection_mode="auto")

    async def fake_snmp(*_args, **_kwargs):
        return ProtocolResult(False, "SNMP", failure_stage="connection", message="SNMP timeout")

    async def fake_ssh(*_args, **_kwargs):
        return ProtocolResult(True, "SSH", metrics={"cpu": 20})

    monkeypatch.setattr(inspection, "collect_snmp", fake_snmp)
    monkeypatch.setattr(inspection, "collect_ssh", fake_ssh)

    result = asyncio.run(inspection.inspect_device(task, device, AppSettings(network_retry_count=0)))
    memory = result.metrics["inspection_items"]["memory"]

    assert memory["source"] == "SNMP+SSH"
    assert memory["conclusion"] == "connection_failed"
    assert "SNMP timeout" in memory["downgrade_reason"]
    assert "SSH output" in memory["downgrade_reason"]


def test_failed_ssh_result_keeps_command_failure_and_unsupported_items_separate():
    task_items = inspection._check_items(
        "quick",
        "ssh",
        None,
        ProtocolResult(
            False,
            "SSH",
            failure_stage="collection",
            message="One or more commands failed",
            failed_checks={"cpu"},
            failed_reasons={"cpu": "SSH command failed: timeout"},
            unsupported_checks={"stp"},
            unsupported_reasons={"stp": "Device rejected the SSH command as unsupported"},
            item_references={"cpu": "display cpu-usage", "stp": "display stp brief"},
        ),
        AppSettings(),
        "Huawei",
        None,
        "switch",
    )

    assert task_items["cpu"]["conclusion"] == "data_fetch_failed"
    assert task_items["cpu"]["downgrade_reason"] == "SSH command failed: timeout"
    assert task_items["stp"]["conclusion"] == "unsupported"
    assert task_items["stp"]["source"] == "unsupported"


def test_snmp_interface_error_column_failure_is_not_reported_as_unsupported():
    task_items = inspection._check_items(
        "full",
        "snmp",
        ProtocolResult(
            True,
            "SNMP",
            metrics={
                "hostname": "restricted-switch",
                "model": "VRP",
                "uptime": "1d",
                "interfaces_total": 1,
                "interface_collection_status": "success",
                "interfaces": [{"index": 1, "oper_status": "up"}],
                "interface_collection_warnings": {
                    "in_errors": "No SNMP response received before timeout",
                },
            },
            failed_checks={"interface_errors"},
            failed_reasons={
                "interface_errors": "IF-MIB error counter columns unavailable: ifInErrors",
            },
        ),
        None,
        AppSettings(),
        "Huawei",
        None,
        "switch",
    )

    item = task_items["interface_errors"]
    assert item["source"] == "SNMP"
    assert item["conclusion"] == "data_fetch_failed"
    assert item["downgrade_reason"] == "IF-MIB error counter columns unavailable: ifInErrors"


def test_ssh_interface_error_value_replaces_restricted_snmp_column():
    snmp = ProtocolResult(
        True,
        "SNMP",
        metrics={
            "interface_collection_warnings": {
                "in_errors": "No SNMP response received before timeout",
            },
        },
    )
    ssh = ProtocolResult(True, "SSH", metrics={"interface_error_count": 7})

    item = inspection._check_items(
        "full",
        "auto",
        snmp,
        ssh,
        AppSettings(),
        "Huawei",
        None,
        "switch",
    )["interface_errors"]

    assert item["source"] == "SSH"
    assert item["conclusion"] == "normal"
    assert item["value"] == 7
    assert item["downgrade_reason"] == "SNMP value unavailable"


def test_ssh_value_replaces_a_failed_snmp_scalar():
    snmp = ProtocolResult(
        True,
        "SNMP",
        metrics={"hostname": "partial-switch", "model": "VRP"},
        failed_checks={"uptime"},
        failed_reasons={"uptime": "SNMP sysUpTime returned an invalid value"},
    )
    ssh = ProtocolResult(True, "SSH", metrics={"uptime": "10d"})

    item = inspection._check_items(
        "quick",
        "auto",
        snmp,
        ssh,
        AppSettings(),
        "Huawei",
        None,
        "switch",
    )["uptime"]

    assert item["source"] == "SSH"
    assert item["conclusion"] == "normal"
    assert item["value"] == "10d"


def test_failed_ssh_fallback_keeps_the_failed_snmp_item_reason():
    snmp = ProtocolResult(
        True,
        "SNMP",
        metrics={"hostname": "partial-switch", "model": "VRP"},
        failed_checks={"uptime"},
        failed_reasons={"uptime": "SNMP sysUpTime returned an invalid value"},
    )
    ssh = ProtocolResult(
        False,
        "SSH",
        failure_stage="collection",
        message="One or more commands failed",
        failed_checks={"uptime"},
        failed_reasons={"uptime": "SSH command failed: timeout"},
    )

    item = inspection._check_items(
        "quick",
        "auto",
        snmp,
        ssh,
        AppSettings(),
        "Huawei",
        None,
        "switch",
    )["uptime"]

    assert item["source"] == "SNMP+SSH"
    assert item["conclusion"] == "data_fetch_failed"
    assert item["downgrade_reason"] == (
        "SNMP: SNMP sysUpTime returned an invalid value; "
        "SSH: SSH command failed: timeout"
    )


def test_unavailable_ssh_fallback_keeps_the_failed_snmp_item_reason():
    snmp = ProtocolResult(
        True,
        "SNMP",
        failed_checks={"uptime"},
        failed_reasons={"uptime": "SNMP sysUpTime returned an invalid value"},
    )
    ssh = ProtocolResult(
        False,
        "SSH",
        failure_stage="authentication",
        message="Authentication failed",
        authentication_failed=True,
    )

    item = inspection._check_items(
        "quick",
        "auto",
        snmp,
        ssh,
        AppSettings(),
        "Huawei",
        None,
        "switch",
    )["uptime"]

    assert item["source"] == "SNMP+SSH"
    assert item["conclusion"] == "authentication_failed"
    assert item["downgrade_reason"] == (
        "SNMP: SNMP sysUpTime returned an invalid value; SSH: Authentication failed"
    )


def test_unsupported_ssh_fallback_keeps_the_failed_snmp_item_reason():
    snmp = ProtocolResult(
        True,
        "SNMP",
        failed_checks={"uptime"},
        failed_reasons={"uptime": "SNMP sysUpTime returned an invalid value"},
    )
    ssh = ProtocolResult(
        True,
        "SSH",
        unsupported_checks={"uptime"},
        unsupported_reasons={"uptime": "Device rejected the SSH command as unsupported"},
    )

    item = inspection._check_items(
        "quick",
        "auto",
        snmp,
        ssh,
        AppSettings(),
        "Huawei",
        None,
        "switch",
    )["uptime"]

    assert item["source"] == "SNMP+SSH"
    assert item["conclusion"] == "data_fetch_failed"
    assert item["downgrade_reason"] == (
        "SNMP: SNMP sysUpTime returned an invalid value; "
        "SSH: Device rejected the SSH command as unsupported"
    )


def test_snmp_authentication_failure_outweighs_an_ssh_item_failure():
    snmp = ProtocolResult(
        False,
        "SNMP",
        failure_stage="authentication",
        message="Wrong SNMP PDU digest",
        authentication_failed=True,
    )
    ssh = ProtocolResult(
        True,
        "SSH",
        failed_checks={"uptime"},
        failed_reasons={"uptime": "SSH command failed: timeout"},
    )

    item = inspection._check_items(
        "quick",
        "auto",
        snmp,
        ssh,
        AppSettings(),
        "Huawei",
        None,
        "switch",
    )["uptime"]

    assert item["source"] == "SNMP+SSH"
    assert item["conclusion"] == "authentication_failed"
    assert "Wrong SNMP PDU digest" in item["downgrade_reason"]
    assert "SSH command failed" in item["downgrade_reason"]


def test_snmp_connection_failure_outweighs_an_unsupported_ssh_item():
    snmp = ProtocolResult(
        False,
        "SNMP",
        failure_stage="connection",
        message="No SNMP response received before timeout",
    )
    ssh = ProtocolResult(
        True,
        "SSH",
        unsupported_checks={"uptime"},
        unsupported_reasons={"uptime": "Device rejected the SSH command as unsupported"},
    )

    item = inspection._check_items(
        "quick",
        "auto",
        snmp,
        ssh,
        AppSettings(),
        "Huawei",
        None,
        "switch",
    )["uptime"]

    assert item["source"] == "SNMP+SSH"
    assert item["conclusion"] == "connection_failed"
    assert "No SNMP response" in item["downgrade_reason"]
    assert "Device rejected" in item["downgrade_reason"]


def test_partial_snmp_device_information_is_requested_from_ssh():
    snmp = ProtocolResult(
        True,
        "SNMP",
        metrics={"hostname": "partial-switch"},
        failed_checks={"device_information"},
        failed_reasons={
            "device_information": "SNMP sysName/sysDescr returned an invalid value",
        },
    )

    missing = inspection._missing_items_for_ssh(
        "quick",
        snmp,
        "Huawei",
        None,
        "switch",
    )

    assert "device_information" in missing


def test_firewall_quick_inspection_skips_stp_and_marks_it_not_applicable(monkeypatch):
    device = Device(
        name="USG6300",
        ip="192.0.2.20",
        vendor="Huawei",
        device_type="firewall",
        snmp_version="v3",
        ssh_username="ops",
        ssh_password="encrypted-password",
    )
    task = InspectionTask(name="Firewall quick", inspection_level="quick", collection_mode="auto")
    observed: dict[str, object] = {}

    async def fake_snmp(*_args, **_kwargs):
        return ProtocolResult(True, "SNMP", metrics={
            "hostname": "USG6300",
            "model": "Huawei USG6300",
            "uptime": "72d 2h 3m 43s",
            "interfaces_total": 16,
            "interface_collection_status": "success",
            "interfaces": [],
        })

    async def fake_ssh(*_args, required_checks=None, **_kwargs):
        observed["required_checks"] = required_checks
        return ProtocolResult(True, "SSH", metrics={
            "cpu": 10.7,
            "memory": 68,
            "active_alarms": {"total": 0, "critical": 0, "warning": 0, "alarms": []},
        })

    monkeypatch.setattr(inspection, "collect_snmp", fake_snmp)
    monkeypatch.setattr(inspection, "collect_ssh", fake_ssh)

    result = asyncio.run(inspection.inspect_device(task, device, AppSettings()))

    assert observed["required_checks"] == {
        "device_information", "cpu", "memory", "active_alarms",
    }
    assert result.metrics["inspection_items"]["stp"] == {
        "source": "not_applicable",
        "conclusion": "not_applicable",
        "adapter_version": "builtin-v1",
        "reference": "vendor STP command or MIB",
        "downgrade_reason": "防火墙设备不适用 STP 巡检",
    }
    assert result.conclusion == "normal"
    assert result.collection_status == "success"


def test_full_inspection_marks_unimplemented_items_as_unsupported_instead_of_normal(monkeypatch):
    device = Device(
        name="Edge-R-01",
        ip="192.0.2.11",
        vendor="Cisco",
        device_type="router",
        snmp_version="v2c",
        snmp_community="encrypted-community",
    )
    task = InspectionTask(name="Full catalog", inspection_level="full", collection_mode="snmp")

    async def fake_snmp(*_args, **_kwargs):
        return ProtocolResult(True, "SNMP", metrics={
            "hostname": "Edge-R-01",
            "model": "Cisco IOS XE",
            "uptime": "1d 0h 0m 0s",
            "interfaces_total": 8,
            "interface_collection_status": "success",
            "interfaces": [],
        })

    monkeypatch.setattr(inspection, "collect_snmp", fake_snmp)

    result = asyncio.run(inspection.inspect_device(task, device, AppSettings()))

    items = result.metrics["inspection_items"]
    for item_id in (
        "boards", "power_supplies", "fans", "temperature", "interface_errors",
        "link_aggregation", "routing_neighbors", "ntp", "storage", "configuration_saved",
    ):
        assert items[item_id]["conclusion"] == "unsupported"
        assert items[item_id]["source"] == "unsupported"


def test_cpu_and_memory_items_use_the_frozen_critical_threshold():
    settings = AppSettings(
        cpu_warning_threshold=80,
        cpu_critical_threshold=95,
        memory_warning_threshold=80,
        memory_critical_threshold=95,
    )

    assert inspection._item_conclusion("cpu", {"cpu": 80}, settings) == "warning"
    assert inspection._item_conclusion("cpu", {"cpu": 95}, settings) == "critical"
    assert inspection._item_conclusion("memory", {"memory": 95}, settings) == "critical"


def test_error_disabled_interface_makes_interface_item_critical():
    metrics = {
        "interfaces_total": 1,
        "interfaces_up": 0,
        "interfaces_down": 1,
        "interfaces_error_disabled": 1,
        "interfaces_unknown": 0,
    }

    assert inspection._item_conclusion("interfaces", metrics, AppSettings()) == "critical"


def test_a_critical_inspection_item_makes_the_device_result_critical(monkeypatch):
    device = Device(
        name="Core-SW-Critical",
        ip="192.0.2.95",
        vendor="Huawei",
        device_type="switch",
        snmp_version="v2c",
        snmp_community="encrypted-community",
    )
    task = InspectionTask(name="Critical thresholds", inspection_level="quick", collection_mode="snmp")
    settings = AppSettings(
        cpu_warning_threshold=80,
        cpu_critical_threshold=95,
        memory_warning_threshold=80,
        memory_critical_threshold=95,
    )

    async def fake_snmp(*_args, **_kwargs):
        return ProtocolResult(True, "SNMP", metrics={
            "hostname": "Core-SW-Critical",
            "model": "Huawei VRP",
            "uptime": "1d 0h 0m 0s",
            "cpu": 97,
            "memory": 40,
            "interfaces_total": 24,
            "interface_collection_status": "success",
            "interfaces": [],
        })

    monkeypatch.setattr(inspection, "collect_snmp", fake_snmp)

    result = asyncio.run(inspection.inspect_device(task, device, settings))

    assert result.metrics["inspection_items"]["cpu"]["conclusion"] == "critical"
    assert result.conclusion == "critical"


def test_raw_output_limit_marks_utf8_content_as_truncated_without_overwriting_limit():
    limit = getattr(collectors, "limit_raw_output", None)

    assert callable(limit)
    captured = limit("abcdef", max_bytes=4)

    assert captured.content == "abcd"
    assert captured.byte_count == 6
    assert captured.truncated is True
    assert captured.status == "truncated"


def test_ssh_command_plan_only_selects_commands_for_missing_quick_items():
    command_plan = getattr(collectors, "ssh_commands_for_checks", None)

    assert callable(command_plan)
    assert command_plan("Huawei", "quick", {"cpu", "memory", "stp"}) == [
        "display cpu-usage",
        "display memory-usage",
        "display stp brief",
    ]


def test_item_values_preserve_per_entity_usage_and_interface_diagnostics():
    metrics = {
        "cpu": 99,
        "cpu_readings": [
            {"object_id": "slot 1", "scope": "board", "window": "5s", "percent": 5},
            {"object_id": "slot 2", "scope": "board", "window": "5s", "percent": 99},
        ],
        "memory": 68,
        "memory_readings": [
            {"object_id": "MPU 0", "scope": "board", "window": None, "percent": 68},
        ],
        "interfaces_total": 4,
        "interfaces_up": 1,
        "interfaces_down": 3,
        "interfaces_admin_down": 1,
        "interfaces_error_disabled": 1,
        "interfaces_unknown": 1,
        "interface_details": [{
            "name": "GE1/0/2", "physical_status": "down", "protocol_status": "down",
            "admin_down": False, "error_disabled": True,
        }],
        "interface_error_count": 123,
        "interface_discard_count": 7,
        "interface_error_details": [{
            "name": "GE1/0/2", "input_errors": 123, "output_errors": 0,
            "crc_errors": 123, "input_discards": 0, "output_discards": 0,
            "total_errors": 123,
        }],
    }

    assert inspection._item_value(metrics, "cpu") == {
        "percent": 99,
        "readings": metrics["cpu_readings"],
    }
    assert inspection._item_value(metrics, "memory") == {
        "percent": 68,
        "readings": metrics["memory_readings"],
    }
    assert inspection._item_value(metrics, "interfaces") == {
        "interfaces_total": 4,
        "interfaces_up": 1,
        "interfaces_down": 3,
        "interfaces_admin_down": 1,
        "interfaces_error_disabled": 1,
        "interfaces_unknown": 1,
        "interface_details": metrics["interface_details"],
    }
    snmp_interfaces = [{
        "name": "GigabitEthernet1/0/3",
        "oper_status": "lowerLayerDown",
        "admin_status": "up",
    }]
    assert inspection._item_value({
        "interfaces_total": 1,
        "interfaces_up": 0,
        "interfaces_down": 1,
        "interfaces": snmp_interfaces,
    }, "interfaces")["interface_details"] == snmp_interfaces
    assert inspection._item_value(metrics, "interface_errors") == {
        "total": 123,
        "discarded": 7,
        "interfaces": metrics["interface_error_details"],
    }


def test_merge_recovered_cpu_removes_stale_per_entity_readings():
    snmp = ProtocolResult(
        True,
        "SNMP",
        metrics={
            "cpu": 100,
            "cpu_readings": [{"object_id": "stale", "percent": 100}],
        },
        failed_checks={"cpu"},
    )
    ssh = ProtocolResult(
        True,
        "SSH",
        metrics={
            "cpu": 42,
            "cpu_readings": [{"object_id": "slot 1", "percent": 42}],
        },
    )

    assert inspection._merge_protocol_metrics(snmp, ssh) == {
        "cpu": 42,
        "cpu_readings": [{"object_id": "slot 1", "percent": 42}],
    }


def test_device_information_exposes_evidence_backed_adapter_compatibility(monkeypatch):
    observed = {}

    def fake_compatibility(vendor, platform_id, device_type, model, software_version):
        observed.update({
            "vendor": vendor,
            "platform_id": platform_id,
            "device_type": device_type,
            "model": model,
            "software_version": software_version,
        })
        return {
            "status": "verified",
            "adapter_version": "huawei-s5735-v1",
            "verification_date": "2026-09-12",
        }

    monkeypatch.setattr(inspection, "adapter_compatibility", fake_compatibility, raising=False)
    device = Device(
        name="Access-SW",
        ip="192.0.2.40",
        vendor="Huawei",
        platform_id="huawei-vrp",
        device_type="switch",
        snmp_version="v2c",
        snmp_community="encrypted-community",
    )
    task = InspectionTask(name="Compatibility", inspection_level="quick", collection_mode="snmp")

    async def fake_snmp(*_args, **_kwargs):
        return ProtocolResult(True, "SNMP", metrics={
            "hostname": "Access-SW",
            "model": "S5735-L24T4S-A1",
            "software_version": "V200R022C00SPC500",
            "uptime": "1d",
            "cpu": 20,
            "memory": 30,
            "interfaces_total": 24,
            "interface_collection_status": "success",
            "stp_discarding_ports": 0,
            "active_alarms": {"total": 0},
        })

    monkeypatch.setattr(inspection, "collect_snmp", fake_snmp)
    result = asyncio.run(inspection.inspect_device(task, device, AppSettings()))

    assert observed == {
        "vendor": "Huawei",
        "platform_id": "huawei-vrp",
        "device_type": "switch",
        "model": "S5735-L24T4S-A1",
        "software_version": "V200R022C00SPC500",
    }
    compatibility = result.metrics["adapter_compatibility"]
    assert compatibility["status"] == "verified"
    assert result.metrics["inspection_items"]["device_information"]["value"][
        "adapter_compatibility"
    ] == compatibility


def test_auto_collection_enriches_generic_snmp_identity_with_ssh_model_and_version(monkeypatch):
    observed = {}
    device = Device(
        name="Access-SW",
        ip="192.0.2.41",
        vendor="Huawei",
        platform_id="huawei-vrp",
        device_type="switch",
        snmp_version="v2c",
        snmp_community="encrypted-community",
        ssh_username="ops",
        ssh_password="encrypted-password",
    )
    task = InspectionTask(name="Identity enrichment", inspection_level="quick", collection_mode="auto")

    async def fake_snmp(*_args, **_kwargs):
        return ProtocolResult(True, "SNMP", metrics={
            "hostname": "Access-SW",
            "model": "Huawei Versatile Routing Platform Software",
            "uptime": "1d",
            "cpu": 20,
            "memory": 30,
            "interfaces_total": 24,
            "interface_collection_status": "success",
            "stp_discarding_ports": 0,
            "active_alarms": {"total": 0},
        })

    async def fake_ssh(*_args, required_checks=None, **_kwargs):
        observed["required_checks"] = required_checks
        return ProtocolResult(True, "SSH", metrics={
            "model": "S5735-L24T4S-A1",
            "software_version": "V200R022C00SPC500",
        })

    monkeypatch.setattr(inspection, "collect_snmp", fake_snmp)
    monkeypatch.setattr(inspection, "collect_ssh", fake_ssh)
    result = asyncio.run(inspection.inspect_device(task, device, AppSettings()))

    assert observed["required_checks"] == {"device_information"}
    assert result.metrics["model"] == "S5735-L24T4S-A1"
    assert result.metrics["software_version"] == "V200R022C00SPC500"
    identity = result.metrics["inspection_items"]["device_information"]
    assert identity["source"] == "SNMP+SSH"
    assert identity["value"]["model"] == "S5735-L24T4S-A1"
    assert identity["value"]["software_version"] == "V200R022C00SPC500"


def test_raw_output_storage_truncates_at_the_remaining_task_quota(tmp_path):
    try:
        raw_outputs = importlib.import_module("app.services.raw_outputs")
        store_raw_output = getattr(raw_outputs, "store_raw_output", None)
    except ModuleNotFoundError:
        store_raw_output = None

    assert callable(store_raw_output)
    stored = store_raw_output(
        tmp_path,
        task_id=8,
        device_ip="192.0.2.18",
        content="abcdef",
        existing_task_bytes=2,
        max_task_bytes=5,
    )

    assert stored.status == "truncated"
    assert stored.received_bytes == 6
    assert stored.stored_bytes == 3
    assert Path(stored.file_path).read_text(encoding="utf-8") == "abc"
    assert not list(tmp_path.rglob("*.tmp"))


def test_raw_output_storage_keeps_utf8_valid_when_quota_splits_a_character(tmp_path):
    raw_outputs = importlib.import_module("app.services.raw_outputs")

    stored = raw_outputs.store_raw_output(
        tmp_path,
        task_id=9,
        device_ip="192.0.2.19",
        content="A中B",
        existing_task_bytes=0,
        max_task_bytes=3,
    )

    assert stored.status == "truncated"
    assert stored.received_bytes == 5
    assert stored.stored_bytes == 1
    assert Path(stored.file_path).read_text(encoding="utf-8") == "A"


def test_persisting_result_raw_output_redacts_and_replaces_database_content(tmp_path, monkeypatch):
    raw_outputs = importlib.import_module("app.services.raw_outputs")
    persist_result = getattr(raw_outputs, "persist_inspection_result_raw_output", None)

    assert callable(persist_result)
    monkeypatch.setattr(raw_outputs, "RAW_OUTPUT_DIR", tmp_path)
    init_db()
    db = SessionLocal()
    try:
        task = InspectionTask(name="Raw output", total_devices=1, selected_device_ids="")
        result = InspectionResult(
            task=task,
            device_id=None,
            device_ip="192.0.2.19",
            device_name="Raw-SW-01",
            vendor="Huawei",
            collection_source="SSH",
            connection_status="success",
            authentication_status="success",
            collection_status="success",
            parsing_status="success",
            conclusion="normal",
            failure_stage=None,
            message=None,
            metrics={},
            raw_output="snmp-agent community secret-community read",
        )
        db.add(result)
        db.flush()

        persist_result(db, result)
        db.commit()
        db.refresh(result)

        assert result.raw_output is None
        assert result.raw_output_status == "complete"
        assert result.raw_output_bytes > 0
        assert result.raw_output_ref
        stored = Path(result.raw_output_ref).read_text(encoding="utf-8")
        assert "secret-community" not in stored
        assert "[REDACTED]" in stored
    finally:
        db.close()


def test_raw_output_reader_returns_only_the_result_file_from_the_application_directory(tmp_path, monkeypatch):
    raw_outputs = importlib.import_module("app.services.raw_outputs")
    read_raw_output = getattr(tasks_router, "read_result_raw_output", None)

    assert callable(read_raw_output)
    monkeypatch.setattr(raw_outputs, "RAW_OUTPUT_DIR", tmp_path)
    init_db()
    db = SessionLocal()
    try:
        task = InspectionTask(name="Read raw output", total_devices=1, selected_device_ids="")
        result = InspectionResult(
            task=task,
            device_id=None,
            device_ip="192.0.2.20",
            device_name="Read-SW-01",
            vendor="Cisco",
            collection_source="SSH",
            connection_status="success",
            authentication_status="success",
            collection_status="success",
            parsing_status="success",
            conclusion="normal",
            failure_stage=None,
            message=None,
            metrics={},
            raw_output="show version\nCisco IOS XE",
        )
        db.add(result)
        db.flush()
        raw_outputs.persist_inspection_result_raw_output(db, result)
        db.commit()

        body = read_raw_output(task.id, result.id, db)

        assert body["status"] == "complete"
        assert body["byte_count"] == result.raw_output_bytes
        assert body["content"] == "show version\nCisco IOS XE"
    finally:
        db.close()


def test_task_execution_persists_raw_output_in_its_actual_task_directory(tmp_path, monkeypatch):
    raw_outputs = importlib.import_module("app.services.raw_outputs")
    monkeypatch.setattr(raw_outputs, "RAW_OUTPUT_DIR", tmp_path)
    init_db()
    db = SessionLocal()
    try:
        existing_ips = {ip for (ip,) in db.query(Device.ip).all()}
        device_ip = next(
            f"198.51.100.{octet}"
            for octet in range(1, 255)
            if f"198.51.100.{octet}" not in existing_ips
        )
        device = Device(
            name="Task-Raw-SW-01",
            ip=device_ip,
            vendor="Huawei",
            device_type="switch",
        )
        db.add(device)
        db.flush()
        snapshot = {
            "id": device.id,
            "name": device.name,
            "ip": device.ip,
            "vendor": device.vendor,
            "device_type": device.device_type,
            "group_name": None,
        }
        task = InspectionTask(
            name="Persist task raw output",
            inspection_level="quick",
            collection_mode="ssh",
            status="queued",
            total_devices=1,
            selected_device_ids=str(device.id),
            selected_device_snapshot=[snapshot],
        )
        db.add(task)
        db.commit()
        task_id = task.id
    finally:
        db.close()

    async def fake_inspect(current_task, current_device, _settings):
        return InspectionResult(
            task=current_task,
            device_id=current_device.id,
            device_ip=current_device.ip,
            device_name=current_device.name,
            vendor=current_device.vendor,
            collection_source="SSH",
            connection_status="success",
            authentication_status="success",
            collection_status="success",
            parsing_status="success",
            conclusion="normal",
            failure_stage=None,
            message=None,
            metrics={},
            raw_output="display version\nVersion test output",
        )

    monkeypatch.setattr(inspection, "inspect_device", fake_inspect)
    asyncio.run(inspection.execute_task(task_id, SessionLocal))

    db = SessionLocal()
    try:
        result = db.query(InspectionResult).filter_by(task_id=task_id).one()
        assert result.raw_output is None
        assert Path(result.raw_output_ref).parent.name == f"task_{task_id}"
        assert Path(result.raw_output_ref).read_text(encoding="utf-8") == "display version\nVersion test output"
    finally:
        db.close()


def test_raw_output_cleanup_expires_content_without_deleting_its_result(tmp_path, monkeypatch):
    raw_outputs = importlib.import_module("app.services.raw_outputs")
    cleanup = getattr(raw_outputs, "cleanup_expired_raw_outputs", None)

    assert callable(cleanup)
    monkeypatch.setattr(raw_outputs, "RAW_OUTPUT_DIR", tmp_path)
    init_db()
    db = SessionLocal()
    try:
        task = InspectionTask(name="Expire raw output", total_devices=1, selected_device_ids="")
        result = InspectionResult(
            task=task,
            device_id=None,
            device_ip="192.0.2.22",
            device_name="Expired-Raw-SW-01",
            vendor="H3C",
            collection_source="SSH",
            connection_status="success",
            authentication_status="success",
            collection_status="success",
            parsing_status="success",
            conclusion="normal",
            failure_stage=None,
            message=None,
            metrics={"hostname": "Expired-Raw-SW-01"},
            raw_output="display version\nold output",
            created_at=datetime.utcnow() - timedelta(days=31),
        )
        db.add(result)
        db.flush()
        raw_outputs.persist_inspection_result_raw_output(db, result)
        raw_path = Path(result.raw_output_ref)
        db.commit()

        assert cleanup(db, retention_days=30, now=datetime.utcnow()) == 1
        db.commit()
        db.refresh(result)

        assert result.raw_output_ref is None
        assert result.raw_output_status == "expired"
        assert result.metrics == {"hostname": "Expired-Raw-SW-01"}
        assert not raw_path.exists()
    finally:
        db.close()


def test_raw_output_retention_and_storage_threshold_are_real_settings():
    assert "raw_output_retention_days" in SettingsUpdate.model_fields
    assert "storage_warning_threshold" in SettingsUpdate.model_fields

    settings = SettingsUpdate(raw_output_retention_days=45, storage_warning_threshold=85)

    assert settings.raw_output_retention_days == 45
    assert settings.storage_warning_threshold == 85


def test_result_schema_exposes_raw_output_state_without_a_file_path():
    assert "raw_output_status" in ResultResponse.model_fields
    assert "raw_output_bytes" in ResultResponse.model_fields
    assert "raw_output_ref" not in ResultResponse.model_fields
