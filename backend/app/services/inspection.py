import asyncio
from contextvars import ContextVar
from dataclasses import replace
from datetime import datetime
from typing import Callable

from sqlalchemy.orm import Session

from app.models.device import ActivityLog, AppSettings, Device, InspectionResult, InspectionTask
from app.services.adapters import adapter_compatibility
from app.services.collectors import (
    MAX_DEVICE_RAW_OUTPUT_BYTES,
    ProtocolResult,
    RawOutputCapture,
    collect_snmp,
    collect_ssh,
    limit_raw_output,
)
from app.services.device_operations import device_operation_lock
from app.services.raw_outputs import persist_inspection_result_raw_output
from app.services.security import redact_configuration_text, redact_runtime_text
from app.services.settings import get_settings, restore_task_settings
from app.services.task_runtime import discard_task_devices, get_task_devices
from app.services.vendor_registry import not_applicable_checks


INSPECTION_ITEM_DEFINITIONS = {
    "device_information": {
        "metric_keys": ("hostname", "model"),
        "reference": "SNMPv2-MIB::sysName/sysDescr",
    },
    "uptime": {
        "metric_keys": ("uptime",),
        "reference": "SNMPv2-MIB::sysUpTime",
    },
    "cpu": {
        "metric_keys": ("cpu",),
        "reference": "vendor CPU metric",
    },
    "memory": {
        "metric_keys": ("memory",),
        "reference": "vendor memory metric",
    },
    "interfaces": {
        "metric_keys": ("interfaces_total",),
        "reference": "IF-MIB::ifNumber/ifXTable",
    },
    "stp": {
        "metric_keys": ("stp_discarding_ports", "stp_broken_ports"),
        "reference": "vendor STP command or MIB",
    },
    "active_alarms": {"metric_keys": ("active_alarms",), "reference": "vendor active alarm command or MIB"},
    "boards": {"metric_keys": ("boards",), "reference": "ENTITY-MIB or vendor command"},
    "power_supplies": {"metric_keys": ("power_supplies",), "reference": "ENTITY-MIB or vendor command"},
    "fans": {"metric_keys": ("fans",), "reference": "ENTITY-MIB or vendor command"},
    "temperature": {"metric_keys": ("temperature",), "reference": "ENTITY-SENSOR-MIB or vendor command"},
    "interface_errors": {
        "metric_keys": ("interface_error_count", "interface_discard_count"),
        "reference": "IF-MIB::ifInErrors/ifOutErrors",
    },
    "link_aggregation": {"metric_keys": ("link_aggregation",), "reference": "vendor command or MIB"},
    "routing_neighbors": {"metric_keys": ("routing_neighbors",), "reference": "vendor command or MIB"},
    "ntp": {"metric_keys": ("ntp",), "reference": "vendor command or MIB"},
    "storage": {"metric_keys": ("storage",), "reference": "vendor command or MIB"},
    "configuration_saved": {"metric_keys": ("configuration_saved",), "reference": "vendor command or MIB"},
}

ITEM_DETAIL_KEYS = {
    "cpu": {"cpu_readings"},
    "memory": {"memory_readings"},
    "interfaces": {
        "interface_collection_status",
        "interface_collection_message",
        "interface_collection_warnings",
        "interfaces",
        "interfaces_up",
        "interfaces_down",
        "interfaces_admin_down",
        "interfaces_error_disabled",
        "interfaces_unknown",
        "interface_details",
    },
    "interface_errors": {"interface_error_details", "interface_discard_count"},
    "stp": {"stp_enabled"},
}

QUICK_INSPECTION_ITEMS = (
    "device_information", "uptime", "cpu", "memory", "interfaces", "stp", "active_alarms",
)
FULL_INSPECTION_ITEMS = QUICK_INSPECTION_ITEMS + (
    "boards", "power_supplies", "fans", "temperature", "interface_errors",
    "link_aggregation", "routing_neighbors", "ntp", "storage", "configuration_saved",
)
SUCCESS_CONCLUSIONS = {"normal", "warning", "critical"}
FAILED_CONCLUSIONS = {"connection_failed", "authentication_failed", "data_fetch_failed"}
_ACTIVE_TASK_DEVICE_CONTEXT: ContextVar[
    tuple[Callable[[], Session], dict] | None
] = ContextVar("active_task_device_context", default=None)


def inspection_item_ids(inspection_level: str) -> tuple[str, ...]:
    return FULL_INSPECTION_ITEMS if inspection_level == "full" else QUICK_INSPECTION_ITEMS


def _not_applicable_items(vendor: str, platform_id: str | None, device_type: str) -> set[str]:
    try:
        return not_applicable_checks(vendor, platform_id, device_type)
    except (ValueError, KeyError):
        # A legacy device with a removed platform must still produce a result
        # instead of aborting the whole task while building item details.
        return set()


def _has_item_data(metrics: dict, item_id: str) -> bool:
    if item_id == "interfaces":
        return metrics.get("interface_collection_status") == "success" and "interfaces_total" in metrics
    if item_id == "device_information":
        # A device identity is still valid when a platform exposes only one
        # of sysName/sysDescr or the vendor parser recognizes only one field.
        # Requiring both made a successful SNMP retry look unsupported.
        return any(metrics.get(key) is not None for key in ("hostname", "model"))
    return any(metrics.get(key) is not None for key in INSPECTION_ITEM_DEFINITIONS[item_id]["metric_keys"])


def _missing_items_for_ssh(
    inspection_level: str,
    snmp_result: ProtocolResult | None,
    vendor: str,
    platform_id: str | None,
    device_type: str,
) -> set[str]:
    item_ids = set(inspection_item_ids(inspection_level)) - _not_applicable_items(
        vendor, platform_id, device_type
    )
    if not snmp_result or not snmp_result.ok:
        return item_ids
    missing = {
        item_id
        for item_id in item_ids
        if item_id in snmp_result.failed_checks
        or not _has_item_data(snmp_result.metrics, item_id)
    }
    # sysDescr is useful as a generic model description, but it normally does
    # not provide the exact model/release pair needed for compatibility proof.
    if (
        "device_information" in item_ids
        and not snmp_result.metrics.get("software_version")
    ):
        missing.add("device_information")
    return missing


def _item_conclusion(item_id: str, metrics: dict, settings: AppSettings) -> str:
    value = metrics.get(item_id)
    cpu_threshold = settings.cpu_warning_threshold or 80
    cpu_critical_threshold = settings.cpu_critical_threshold or 95
    memory_threshold = settings.memory_warning_threshold or 80
    memory_critical_threshold = settings.memory_critical_threshold or 95
    if item_id == "cpu" and isinstance(value, (int, float)):
        return "critical" if value >= cpu_critical_threshold else "warning" if value >= cpu_threshold else "normal"
    if item_id == "memory" and isinstance(value, (int, float)):
        return "critical" if value >= memory_critical_threshold else "warning" if value >= memory_threshold else "normal"

    if item_id == "interfaces":
        if (metrics.get("interfaces_error_disabled") or 0) > 0:
            return "critical"
        if (metrics.get("interfaces_unknown") or 0) > 0:
            return "unsupported"
        return "normal"

    if item_id == "stp":
        if metrics.get("stp_enabled") is False:
            return "not_applicable"
        broken = metrics.get("stp_broken_ports")
        if isinstance(broken, (int, float)) and broken > 0:
            return "critical"
        return "normal"

    if item_id in {"boards", "power_supplies", "fans"} and isinstance(value, dict):
        if not value.get("total", 0):
            return "unsupported"
        if value.get("abnormal", 0) > 0:
            return "critical"
        components = value.get("components", [])
        if components and any(component.get("status") == "unknown" for component in components):
            return "unsupported"
        return "normal"

    if item_id == "temperature" and isinstance(value, dict):
        if not value.get("sensors", 0):
            return "unsupported"
        return "warning" if value.get("alarming", 0) > 0 else "normal"

    if item_id == "active_alarms" and isinstance(value, dict):
        if value.get("critical", 0) > 0:
            return "critical"
        if value.get("warning", 0) > 0:
            return "warning"
        return "warning" if value.get("total", 0) > 0 else "normal"

    if item_id == "link_aggregation" and isinstance(value, dict):
        if not value.get("groups", 0):
            return "not_applicable"
        return "warning" if value.get("degraded", 0) > 0 else "normal"

    if item_id == "routing_neighbors" and isinstance(value, dict):
        total = value.get("total", 0)
        if not total:
            return "not_applicable"
        return "warning" if value.get("established", 0) < total else "normal"

    if item_id == "ntp" and isinstance(value, dict):
        if value.get("configured") is False:
            return "not_applicable"
        return "normal" if value.get("synchronized") else "warning"

    if item_id == "storage" and isinstance(value, dict):
        filesystems = value.get("filesystems", [])
        if any(
            isinstance(filesystem, dict)
            and (filesystem.get("access") == "ro" or filesystem.get("status") in {"read_only", "fault"})
            for filesystem in filesystems
        ):
            return "critical"
        used = value.get("used_percent")
        if not isinstance(used, (int, float)):
            return "unsupported"
        warn = settings.storage_warning_threshold or 80
        crit = settings.storage_critical_threshold or 95
        if used >= crit:
            return "critical"
        if used >= warn:
            return "warning"
        if filesystems and any(
            isinstance(filesystem, dict)
            and filesystem.get("access") in {None, "unknown"}
            and filesystem.get("status") in {None, "unknown"}
            for filesystem in filesystems
        ):
            return "unsupported"
        return "normal"

    if item_id == "configuration_saved" and isinstance(value, dict):
        return "normal" if value.get("saved") else "warning"

    return "normal"


def _item_value(metrics: dict, item_id: str):
    if item_id == "device_information":
        value = {
            key: metrics.get(key)
            for key in ("hostname", "model", "software_version", "adapter_compatibility")
            if metrics.get(key) is not None
        }
        return value or None
    if item_id in {"cpu", "memory"}:
        percent = metrics.get(item_id)
        readings = metrics.get(f"{item_id}_readings")
        if readings:
            return {"percent": percent, "readings": readings}
        return percent
    if item_id == "interfaces":
        value = {
            key: metrics.get(key)
            for key in (
                "interfaces_total", "interfaces_up", "interfaces_down",
                "interfaces_admin_down", "interfaces_error_disabled",
                "interfaces_unknown", "interface_details",
            )
            if metrics.get(key) is not None
        }
        details = metrics.get("interface_details") or metrics.get("interfaces")
        if details:
            value["interface_details"] = details
        return value
    if item_id == "interface_errors":
        details = metrics.get("interface_error_details")
        discarded = metrics.get("interface_discard_count")
        if details or discarded is not None:
            value = {
                "total": metrics.get("interface_error_count"),
                "discarded": discarded,
            }
            if details:
                value["interfaces"] = details
            return value
    if item_id == "stp":
        value = {
            key: metrics.get(key)
            for key in ("stp_discarding_ports", "stp_broken_ports", "stp_enabled")
            if metrics.get(key) is not None
        }
        return value or None
    metric_keys = INSPECTION_ITEM_DEFINITIONS[item_id]["metric_keys"]
    return next((metrics.get(key) for key in metric_keys if metrics.get(key) is not None), None)


def _protocol_failure_conclusion(protocol: ProtocolResult) -> str:
    if protocol.authentication_failed or protocol.failure_stage == "authentication":
        return "authentication_failed"
    if protocol.failure_stage == "connection":
        return "connection_failed"
    return "data_fetch_failed"


def _higher_priority_failure(*protocols: ProtocolResult) -> str:
    priority = {"authentication_failed": 3, "connection_failed": 2, "data_fetch_failed": 1}
    return max(
        (_protocol_failure_conclusion(protocol) for protocol in protocols),
        key=priority.__getitem__,
    )


def _snmp_item_failure_reason(protocol: ProtocolResult | None, item_id: str) -> str | None:
    if not protocol:
        return None
    if item_id in protocol.failed_checks:
        return protocol.failed_reasons.get(item_id, "SNMP value is invalid")
    if not protocol.ok:
        return protocol.message or "SNMP collection failed"
    return None


def _item_failure_conclusion(snmp_result: ProtocolResult | None) -> str:
    if snmp_result and not snmp_result.ok:
        return _protocol_failure_conclusion(snmp_result)
    return "data_fetch_failed"


def _check_items(
    inspection_level: str,
    mode: str,
    snmp_result: ProtocolResult | None,
    ssh_result: ProtocolResult | None,
    settings: AppSettings,
    vendor: str,
    platform_id: str | None,
    device_type: str,
) -> dict[str, dict]:
    items: dict[str, dict] = {}
    for item_id in inspection_item_ids(inspection_level):
        definition = INSPECTION_ITEM_DEFINITIONS[item_id]
        if item_id in _not_applicable_items(vendor, platform_id, device_type):
            items[item_id] = {
                "source": "not_applicable",
                "conclusion": "not_applicable",
                "adapter_version": "builtin-v1",
                "reference": definition["reference"],
                "downgrade_reason": "防火墙设备不适用 STP 巡检",
            }
            continue
        if (
            snmp_result
            and snmp_result.ok
            and item_id not in snmp_result.failed_checks
            and _has_item_data(snmp_result.metrics, item_id)
        ):
            items[item_id] = {
                "source": "SNMP",
                "conclusion": _item_conclusion(item_id, snmp_result.metrics, settings),
                "adapter_version": "standard-snmp-v1",
                "reference": snmp_result.item_references.get(item_id, definition["reference"]),
                "downgrade_reason": None,
                "value": _item_value(snmp_result.metrics, item_id),
            }
            continue
        if ssh_result and ssh_result.ok and _has_item_data(ssh_result.metrics, item_id):
            item_message = ssh_result.item_messages.get(item_id)
            items[item_id] = {
                "source": "SSH",
                "conclusion": _item_conclusion(item_id, ssh_result.metrics, settings),
                "adapter_version": "builtin-ssh-v1",
                "reference": ssh_result.item_references.get(item_id, definition["reference"]),
                "downgrade_reason": item_message or ("SNMP value unavailable" if snmp_result and snmp_result.ok else None),
                "value": _item_value(ssh_result.metrics, item_id),
            }
            continue
        if ssh_result and item_id in ssh_result.failed_checks:
            ssh_reason = ssh_result.failed_reasons.get(
                item_id,
                "SSH command failed",
            )
            snmp_reason = _snmp_item_failure_reason(snmp_result, item_id)
            reason = ssh_reason
            if snmp_reason:
                reason = f"SNMP: {snmp_reason}; SSH: {ssh_reason}"
            items[item_id] = {
                "source": "SNMP+SSH" if snmp_reason else "SSH",
                "conclusion": _item_failure_conclusion(snmp_result),
                "adapter_version": "builtin-ssh-v1",
                "reference": ssh_result.item_references.get(item_id, definition["reference"]),
                "downgrade_reason": reason,
            }
            continue
        if ssh_result and item_id in ssh_result.unsupported_checks:
            ssh_reason = ssh_result.unsupported_reasons.get(
                item_id,
                "No verified SSH command is available for this item",
            )
            snmp_reason = _snmp_item_failure_reason(snmp_result, item_id)
            items[item_id] = {
                "source": "SNMP+SSH" if snmp_reason else "unsupported",
                "conclusion": _item_failure_conclusion(snmp_result) if snmp_reason else "unsupported",
                "adapter_version": "builtin-ssh-v1",
                "reference": ssh_result.item_references.get(item_id, definition["reference"]),
                "downgrade_reason": (
                    f"SNMP: {snmp_reason}; SSH: {ssh_reason}" if snmp_reason else ssh_reason
                ),
            }
            continue
        if ssh_result and not ssh_result.ok:
            snmp_reason = _snmp_item_failure_reason(snmp_result, item_id)
            reason = ssh_result.message
            conclusion = _protocol_failure_conclusion(ssh_result)
            if snmp_result and not snmp_result.ok:
                conclusion = _higher_priority_failure(snmp_result, ssh_result)
            if snmp_reason:
                reason = f"SNMP: {snmp_reason}; SSH: {ssh_result.message}"
            items[item_id] = {
                "source": "SNMP+SSH" if snmp_reason else "SSH",
                "conclusion": conclusion,
                "adapter_version": "builtin-v1",
                "reference": definition["reference"],
                "downgrade_reason": reason,
            }
            continue
        if snmp_result and item_id in snmp_result.failed_checks:
            reason = snmp_result.failed_reasons.get(item_id, "SNMP value is invalid")
            if ssh_result and ssh_result.ok:
                reason = f"SNMP: {reason}; SSH: SSH output did not contain a supported value"
            items[item_id] = {
                "source": "SNMP+SSH" if ssh_result else "SNMP",
                "conclusion": "data_fetch_failed",
                "adapter_version": "standard-snmp-v1",
                "reference": snmp_result.item_references.get(item_id, definition["reference"]),
                "downgrade_reason": reason,
            }
            continue
        if ssh_result and ssh_result.ok:
            snmp_reason = _snmp_item_failure_reason(snmp_result, item_id)
            ssh_reason = "SSH output did not contain a supported value"
            items[item_id] = {
                "source": "SNMP+SSH" if snmp_reason else "SSH",
                "conclusion": _item_failure_conclusion(snmp_result),
                "adapter_version": "builtin-ssh-v1",
                "reference": ssh_result.item_references.get(item_id, definition["reference"]),
                "downgrade_reason": (
                    f"SNMP: {snmp_reason}; SSH: {ssh_reason}" if snmp_reason else ssh_reason
                ),
            }
            continue
        if snmp_result and not snmp_result.ok:
            items[item_id] = {
                "source": "SNMP",
                "conclusion": _protocol_failure_conclusion(snmp_result),
                "adapter_version": "standard-snmp-v1",
                "reference": definition["reference"],
                "downgrade_reason": snmp_result.message,
            }
            continue
        items[item_id] = {
            "source": "unsupported",
            "conclusion": "unsupported",
            "adapter_version": "builtin-v1",
            "reference": definition["reference"],
            "downgrade_reason": "No verified collector is available for this item",
        }
    return items


def _merge_protocol_metrics(snmp_result: ProtocolResult | None, ssh_result: ProtocolResult | None) -> dict:
    metrics = dict(snmp_result.metrics) if snmp_result else {}
    if snmp_result and ssh_result and ssh_result.ok:
        for item_id, definition in INSPECTION_ITEM_DEFINITIONS.items():
            if (
                item_id not in snmp_result.failed_checks
                and _has_item_data(snmp_result.metrics, item_id)
            ) or not _has_item_data(ssh_result.metrics, item_id):
                continue
            # Replace a recovered item as a group, including its stale status.
            keys = set(definition["metric_keys"])
            keys.update(ITEM_DETAIL_KEYS.get(item_id, set()))
            for key in keys:
                metrics.pop(key, None)
    for key, value in (ssh_result.metrics.items() if ssh_result else ()):
        if value is not None and metrics.get(key) is None:
            metrics[key] = value
    if ssh_result and ssh_result.ok:
        for key in ("model", "software_version"):
            if ssh_result.metrics.get(key) is not None:
                metrics[key] = ssh_result.metrics[key]
    return metrics


def _merge_protocol_raw_output(*protocols: ProtocolResult) -> RawOutputCapture:
    sections: list[str] = []
    unretained_bytes = 0
    input_truncated = False
    for protocol in protocols:
        if not protocol.raw_output:
            continue
        raw_bytes = len(protocol.raw_output.encode("utf-8"))
        declared_bytes = max(protocol.raw_output_bytes, raw_bytes)
        unretained_bytes += declared_bytes - raw_bytes
        input_truncated = input_truncated or protocol.raw_output_status == "truncated"
        sections.append(f"=== {protocol.source} ===\n{protocol.raw_output}")
    captured = limit_raw_output(
        "\n\n".join(sections),
        MAX_DEVICE_RAW_OUTPUT_BYTES,
    )
    truncated = input_truncated or captured.truncated
    return RawOutputCapture(
        captured.content,
        captured.byte_count + unretained_bytes,
        truncated,
        "truncated" if truncated else "complete",
    )


def _attach_check_items(
    result: InspectionResult,
    task: InspectionTask,
    mode: str,
    snmp_result: ProtocolResult | None,
    ssh_result: ProtocolResult | None,
    settings: AppSettings,
    vendor: str,
    platform_id: str | None,
    device_type: str,
) -> InspectionResult:
    items = _check_items(
        task.inspection_level,
        mode,
        snmp_result,
        ssh_result,
        settings,
        vendor,
        platform_id,
        device_type,
    )
    compatibility = adapter_compatibility(
        vendor,
        platform_id,
        device_type,
        result.metrics.get("model"),
        result.metrics.get("software_version"),
    )
    device_information = items.get("device_information")
    if device_information is not None and isinstance(device_information.get("value"), dict):
        device_information["value"] = _item_value(result.metrics, "device_information")
        device_information["value"] = {
            **device_information["value"],
            "adapter_compatibility": compatibility,
        }
        if (
            device_information.get("source") == "SNMP"
            and snmp_result
            and snmp_result.ok
            and ssh_result
            and ssh_result.ok
            and any(ssh_result.metrics.get(key) is not None for key in ("model", "software_version"))
        ):
            device_information["source"] = "SNMP+SSH"
            if ssh_result.item_references.get("device_information"):
                device_information["reference"] = ssh_result.item_references["device_information"]
        if compatibility["status"] == "verified":
            device_information["adapter_version"] = compatibility["adapter_version"]
    result.metrics = {
        **result.metrics,
        "adapter_compatibility": compatibility,
        "inspection_items": items,
    }
    item_conclusions = {item["conclusion"] for item in items.values()}
    if result.conclusion in {"normal", "warning", "critical"}:
        if "critical" in item_conclusions:
            # A confirmed fault (absent power supply, CPU over the critical
            # threshold) must not be hidden behind a supplementary check that
            # could not be read; the failed items stay visible in the list.
            result.conclusion = "critical"
        elif "authentication_failed" in item_conclusions:
            result.conclusion = "authentication_failed"
            result.authentication_status = "failed"
            result.collection_status = "failed"
            result.failure_stage = ssh_result.failure_stage if ssh_result else "authentication"
        elif "connection_failed" in item_conclusions:
            result.conclusion = "connection_failed"
            result.connection_status = "failed"
            result.collection_status = "failed"
            result.failure_stage = ssh_result.failure_stage if ssh_result else "connection"
        elif "data_fetch_failed" in item_conclusions:
            result.conclusion = "data_fetch_failed"
            result.collection_status = "failed"
            result.failure_stage = result.failure_stage or "collection"
        elif "critical" in item_conclusions:
            result.conclusion = "critical"
        elif "warning" in item_conclusions:
            result.conclusion = "warning"
        elif item_conclusions <= {"unsupported", "not_applicable"}:
            result.conclusion = "unsupported" if "unsupported" in item_conclusions else "not_applicable"
    if result.conclusion in {"authentication_failed", "connection_failed", "data_fetch_failed"} and not result.message:
        result.message = next(
            (
                str(item["downgrade_reason"])
                for item in items.values()
                if item["conclusion"] == result.conclusion and item.get("downgrade_reason")
            ),
            None,
        )
    if result.message:
        result.message = redact_runtime_text(result.message)
    for item in items.values():
        if item.get("downgrade_reason"):
            item["downgrade_reason"] = redact_runtime_text(str(item["downgrade_reason"]))
    sources = {
        source
        for item in items.values()
        for source in str(item["source"]).split("+")
        if source in {"SNMP", "SSH"}
    }
    if sources:
        result.collection_source = "+".join(source for source in ("SNMP", "SSH") if source in sources)
    return result


def _success_with_check_items(
    task: InspectionTask,
    device: Device,
    source: str,
    protocol: ProtocolResult,
    settings: AppSettings,
    snmp_result: ProtocolResult | None,
    ssh_result: ProtocolResult | None,
    message: str | None = None,
) -> InspectionResult:
    result = _success_result(
        task,
        device,
        source,
        protocol,
        message,
        cpu_warning_threshold=settings.cpu_warning_threshold or 80,
        memory_warning_threshold=settings.memory_warning_threshold or 80,
    )
    return _attach_check_items(
        result,
        task,
        task.collection_mode,
        snmp_result,
        ssh_result,
        settings,
        device.vendor,
        getattr(device, "platform_id", None),
        device.device_type,
    )


def _failure_with_check_items(
    task: InspectionTask,
    device: Device,
    source: str,
    protocol: ProtocolResult,
    settings: AppSettings,
    snmp_result: ProtocolResult | None,
    ssh_result: ProtocolResult | None,
) -> InspectionResult:
    result = _failure_result(task, device, source, protocol)
    return _attach_check_items(
        result,
        task,
        task.collection_mode,
        snmp_result,
        ssh_result,
        settings,
        device.vendor,
        getattr(device, "platform_id", None),
        device.device_type,
    )


async def _retry_connection_failure(collect, retry_count: int | None) -> ProtocolResult:
    retry_count = 2 if retry_count is None else max(0, retry_count)
    result = await collect()
    for _ in range(retry_count):
        if result.ok or result.authentication_failed or result.failure_stage != "connection":
            break
        result = await collect()
    return result


def _effective_snmp_retry_count(settings: AppSettings) -> int:
    """Use one bounded timeout-retry layer while honoring both retry settings."""
    protocol_retries = max(0, settings.snmp_retries or 0)
    network_retries = 2 if settings.network_retry_count is None else max(0, settings.network_retry_count)
    return max(protocol_retries, network_retries)


def _apply_device_snapshot(result: InspectionResult, snapshot: dict) -> InspectionResult:
    result.device_ip = snapshot.get("ip", result.device_ip)
    result.device_name = snapshot.get("name", result.device_name)
    result.vendor = snapshot.get("vendor", result.vendor)
    return result


def _deleted_device_result(task: InspectionTask, snapshot: dict) -> InspectionResult:
    return InspectionResult(
        task_id=task.id,
        device_id=None,
        device_ip=snapshot.get("ip", "-"),
        device_name=snapshot.get("name"),
        vendor=snapshot.get("vendor"),
        collection_source="none",
        connection_status="not_checked",
        authentication_status="not_checked",
        collection_status="failed",
        parsing_status="not_checked",
        conclusion="data_fetch_failed",
        failure_stage="device_deleted",
        message="Device was deleted before task execution",
        metrics={},
        raw_output=None,
    )


def _cancelled_device_result(
    task: InspectionTask,
    snapshot: dict,
    device_exists: bool = False,
) -> InspectionResult:
    return InspectionResult(
        task_id=task.id,
        device_id=snapshot.get("id") if device_exists else None,
        device_ip=snapshot.get("ip", "-"),
        device_name=snapshot.get("name"),
        vendor=snapshot.get("vendor"),
        collection_source="none",
        connection_status="not_checked",
        authentication_status="not_checked",
        collection_status="cancelled",
        parsing_status="not_checked",
        conclusion="cancelled",
        failure_stage="cancelled",
        message="Task cancelled before collection started",
        metrics={},
        raw_output=None,
    )

def _crashed_device_result(snapshot: dict, error: Exception) -> InspectionResult:
    """An unexpected collector error must be reported, never silently dropped."""
    return InspectionResult(
        device_id=snapshot.get("id"),
        device_ip=snapshot.get("ip", "-"),
        device_name=snapshot.get("name"),
        vendor=snapshot.get("vendor"),
        collection_source="none",
        connection_status="not_checked",
        authentication_status="not_checked",
        collection_status="failed",
        parsing_status="not_checked",
        conclusion="data_fetch_failed",
        failure_stage="collection_error",
        message=redact_runtime_text(
            f"Unexpected collection error: {type(error).__name__}: {error}"
        ),
        metrics={},
        raw_output=None,
    )


def _task_failed_device_result(snapshot: dict, message: str, device_exists: bool) -> InspectionResult:
    return InspectionResult(
        device_id=snapshot.get("id") if device_exists else None,
        device_ip=snapshot.get("ip", "-"),
        device_name=snapshot.get("name"),
        vendor=snapshot.get("vendor"),
        collection_source="none",
        connection_status="not_checked",
        authentication_status="not_checked",
        collection_status="failed",
        parsing_status="not_checked",
        conclusion="data_fetch_failed",
        failure_stage="task_execution",
        message=message,
        metrics={},
        raw_output=None,
    )


def _failure_result(task: InspectionTask, device: Device, source: str, protocol: ProtocolResult) -> InspectionResult:
    failure_conclusion = _protocol_failure_conclusion(protocol)
    auth_status = "failed" if failure_conclusion == "authentication_failed" else "not_checked"
    connection_status = "failed" if protocol.failure_stage == "connection" else "not_checked"
    return InspectionResult(
        task_id=task.id,
        device_id=device.id,
        device_ip=device.ip,
        device_name=device.name,
        vendor=device.vendor,
        collection_source=source,
        connection_status=connection_status,
        authentication_status=auth_status,
        collection_status="failed",
        parsing_status="not_checked",
        conclusion=failure_conclusion,
        failure_stage=protocol.failure_stage,
        message=redact_runtime_text(protocol.message) if protocol.message else None,
        metrics={"_ssh_host_fingerprint": protocol.host_fingerprint} if protocol.host_fingerprint else {},
        raw_output=redact_configuration_text(protocol.raw_output) if protocol.raw_output else None,
        raw_output_status=protocol.raw_output_status if protocol.raw_output else "not_available",
        raw_output_bytes=protocol.raw_output_bytes,
    )


def _success_result(
    task: InspectionTask,
    device: Device,
    source: str,
    protocol: ProtocolResult,
    message: str | None = None,
    cpu_warning_threshold: int = 80,
    memory_warning_threshold: int = 80,
) -> InspectionResult:
    conclusion = "normal"
    collection_status = "success"
    failure_stage = None
    result_message = redact_runtime_text(message) if message else None
    if protocol.metrics.get("interface_collection_status") == "data_fetch_failed":
        conclusion = "data_fetch_failed"
        collection_status = "failed"
        failure_stage = "collection"
        interface_message = protocol.metrics.get("interface_collection_message")
        if interface_message and result_message:
            result_message = f"{interface_message}; {result_message}"
        elif interface_message:
            result_message = str(interface_message)
        elif not result_message:
            result_message = "SNMP interface metrics could not be collected"
    cpu = protocol.metrics.get("cpu")
    memory = protocol.metrics.get("memory")
    if conclusion == "normal" and (
        (isinstance(cpu, (int, float)) and cpu >= cpu_warning_threshold)
        or (isinstance(memory, (int, float)) and memory >= memory_warning_threshold)
    ):
        conclusion = "warning"
    return InspectionResult(
        task_id=task.id,
        device_id=device.id,
        device_ip=device.ip,
        device_name=device.name or protocol.metrics.get("hostname"),
        vendor=device.vendor,
        collection_source=source,
        connection_status="success",
        authentication_status="success",
        collection_status=collection_status,
        parsing_status="success",
        conclusion=conclusion,
        failure_stage=failure_stage,
        message=result_message,
        metrics={
            **protocol.metrics,
            **({"_ssh_host_fingerprint": protocol.host_fingerprint} if protocol.host_fingerprint else {}),
        },
        raw_output=redact_configuration_text(protocol.raw_output) if protocol.raw_output else None,
        raw_output_status=protocol.raw_output_status if protocol.raw_output else "not_available",
        raw_output_bytes=protocol.raw_output_bytes,
    )


async def inspect_device(
    task: InspectionTask,
    device: Device,
    settings: AppSettings | None = None,
) -> InspectionResult:
    async with device_operation_lock(device.id):
        task_device_context = _ACTIVE_TASK_DEVICE_CONTEXT.get()
        if task_device_context is not None:
            session_factory, snapshot = task_device_context
            presence_db = session_factory()
            try:
                device_exists = presence_db.get(Device, device.id) is not None
            finally:
                presence_db.close()
            if not device_exists:
                return _deleted_device_result(task, snapshot)
        return await _inspect_device(task, device, settings)


async def _inspect_device(
    task: InspectionTask,
    device: Device,
    settings: AppSettings | None = None,
) -> InspectionResult:
    settings = settings or AppSettings()
    mode = task.collection_mode
    snmp_result: ProtocolResult | None = None
    ssh_result: ProtocolResult | None = None

    if mode in {"auto", "snmp"}:
        # collect_snmp owns the bounded timeout-retry budget. Merge the generic
        # network setting into that budget instead of wrapping the whole device
        # collection, which would multiply GET/walk attempts and elapsed time.
        snmp_result = await collect_snmp(
            device,
            timeout=settings.snmp_timeout_seconds,
            retries=_effective_snmp_retry_count(settings),
        )
        if snmp_result.ok and mode == "snmp":
            return _success_with_check_items(
                task,
                device,
                "SNMP",
                snmp_result,
                settings,
                snmp_result,
                None,
            )
        if not snmp_result.ok and mode == "snmp":
            return _failure_with_check_items(
                task, device, "SNMP", snmp_result, settings, snmp_result, None
            )

    if mode in {"auto", "ssh"}:
        required_checks = (
            _missing_items_for_ssh(
                task.inspection_level,
                snmp_result,
                device.vendor,
                getattr(device, "platform_id", None),
                device.device_type,
            )
            if mode == "auto"
            else _missing_items_for_ssh(
                task.inspection_level,
                None,
                device.vendor,
                getattr(device, "platform_id", None),
                device.device_type,
            )
        )
        if mode == "auto" and snmp_result and not required_checks:
            return _success_with_check_items(
                task,
                device,
                "SNMP",
                snmp_result,
                settings,
                snmp_result,
                None,
            )
        if mode == "auto" and snmp_result and not (device.ssh_username and device.ssh_password):
            if not snmp_result.ok:
                snmp_result.message = f"SNMP failed, SSH not configured: {snmp_result.message}"
                return _failure_with_check_items(
                    task, device, "SNMP", snmp_result, settings, snmp_result, None
                )
            return _success_with_check_items(
                task,
                device,
                "SNMP",
                snmp_result,
                settings,
                snmp_result,
                None,
                "SSH supplementary collection skipped: SSH credentials not configured",
            )
        ssh_result = await _retry_connection_failure(
            lambda: collect_ssh(
                device,
                task.inspection_level,
                connection_timeout=settings.connection_timeout_seconds,
                command_timeout=settings.command_timeout_seconds,
                required_checks=required_checks,
            ),
            settings.network_retry_count,
        )
        if ssh_result.ok:
            if snmp_result and snmp_result.ok:
                merged_metrics = _merge_protocol_metrics(snmp_result, ssh_result)
                raw_capture = _merge_protocol_raw_output(snmp_result, ssh_result)
                combined = ProtocolResult(
                    True,
                    "SNMP+SSH",
                    metrics=merged_metrics,
                    raw_output=raw_capture.content,
                    host_fingerprint=ssh_result.host_fingerprint,
                    host_key_algorithm=ssh_result.host_key_algorithm,
                    raw_output_status=raw_capture.status,
                    raw_output_bytes=raw_capture.byte_count,
                )
                return _success_with_check_items(
                    task,
                    device,
                    "SNMP+SSH",
                    combined,
                    settings,
                    snmp_result,
                    ssh_result,
                )
            downgrade_message = None
            if snmp_result and not snmp_result.ok:
                downgrade_message = f"SNMP fallback: {snmp_result.message}"
            return _success_with_check_items(
                task,
                device,
                "SSH",
                ssh_result,
                settings,
                snmp_result,
                ssh_result,
                downgrade_message,
            )
        if mode == "ssh":
            return _failure_with_check_items(
                task, device, "SSH", ssh_result, settings, None, ssh_result
            )
        if snmp_result and snmp_result.ok:
            return _success_with_check_items(
                task,
                device,
                "SNMP",
                snmp_result,
                settings,
                snmp_result,
                ssh_result,
                f"SSH supplementary collection unavailable: {ssh_result.message}",
            )

    if snmp_result and not snmp_result.ok and ssh_result and not ssh_result.ok:
        message = f"SNMP: {snmp_result.message}; SSH: {ssh_result.message}"
        failures = (snmp_result, ssh_result)
        priority = {"authentication_failed": 3, "connection_failed": 2, "data_fetch_failed": 1}
        final = max(failures, key=lambda item: priority[_protocol_failure_conclusion(item)])
        return _failure_with_check_items(
            task,
            device,
            "SNMP+SSH",
            replace(final, message=message),
            settings,
            snmp_result,
            ssh_result,
        )
    if snmp_result and not snmp_result.ok:
        if not (device.ssh_username and device.ssh_password):
            snmp_result.message = f"SNMP failed, SSH not configured: {snmp_result.message}"
        return _failure_with_check_items(
            task, device, "SNMP", snmp_result, settings, snmp_result, None
        )
    if ssh_result and not ssh_result.ok:
        return _failure_with_check_items(
            task, device, "SSH", ssh_result, settings, None, ssh_result
        )
    return _failure_result(
        task,
        device,
        "none",
        ProtocolResult(False, "none", failure_stage="credentials", message="No usable collection protocol configured"),
    )


def _start_task_if_queued(db: Session, task: InspectionTask) -> InspectionTask:
    started_at = datetime.utcnow()
    transitioned = (
        db.query(InspectionTask)
        .filter(InspectionTask.id == task.id, InspectionTask.status == "queued")
        .update(
            {InspectionTask.status: "running", InspectionTask.started_at: started_at},
            synchronize_session=False,
        )
    )
    if transitioned:
        db.add(ActivityLog(
            event="task_started",
            task_id=task.id,
            message=f"Task {task.name} started",
        ))
        db.commit()
    else:
        # End the read transaction so a concurrently committed cancellation is
        # visible before any devices are scheduled.
        db.rollback()
    db.expire_all()
    return db.get(InspectionTask, task.id)


def _set_task_terminal_status(
    db: Session,
    task_id: int,
    terminal_status: str,
) -> InspectionTask:
    finished_at = datetime.utcnow()
    transitioned = (
        db.query(InspectionTask)
        .filter(InspectionTask.id == task_id, InspectionTask.status != "cancelled")
        .update(
            {
                InspectionTask.status: terminal_status,
                InspectionTask.finished_at: finished_at,
            },
            synchronize_session=False,
        )
    )
    if not transitioned:
        db.query(InspectionTask).filter(
            InspectionTask.id == task_id,
            InspectionTask.status == "cancelled",
        ).update(
            {InspectionTask.finished_at: finished_at},
            synchronize_session=False,
        )
    db.expire_all()
    return db.get(InspectionTask, task_id)


async def execute_task(task_id: int, session_factory, max_concurrency: int | None = None) -> None:
    db: Session = session_factory()
    try:
        task = db.get(InspectionTask, task_id)
        if not task:
            return
        task = _start_task_if_queued(db, task)
        if not task:
            return
        settings = restore_task_settings(task.settings_snapshot, get_settings(db))

        device_ids = [int(value) for value in task.selected_device_ids.split(",") if value]
        current_devices = db.query(Device).filter(Device.id.in_(device_ids)).all()
        current_device_ids = {device.id for device in current_devices}
        current_by_id = {device.id: device for device in current_devices}
        runtime_devices = get_task_devices(task_id)
        devices_by_id = runtime_devices if runtime_devices is not None else {device.id: device for device in current_devices}
        snapshots = task.selected_device_snapshot or [
            {
                "id": device_id,
                "name": current_by_id[device_id].name if device_id in current_by_id else None,
                "ip": current_by_id[device_id].ip if device_id in current_by_id else "-",
                "vendor": current_by_id[device_id].vendor if device_id in current_by_id else None,
                "platform_id": current_by_id[device_id].platform_id if device_id in current_by_id else None,
                "device_type": current_by_id[device_id].device_type if device_id in current_by_id else None,
                "group_name": current_by_id[device_id].group_name if device_id in current_by_id else None,
            }
            for device_id in device_ids
        ]

        concurrency = max(1, min(max_concurrency or settings.max_concurrency, 50))
        semaphore = asyncio.Semaphore(concurrency)

        async def _one(snapshot: dict):
            async with semaphore:
                device_id = snapshot.get("id")
                device = devices_by_id.get(device_id)
                if device_id not in current_device_ids or not device:
                    return _deleted_device_result(task, snapshot)
                context_token = _ACTIVE_TASK_DEVICE_CONTEXT.set((session_factory, snapshot))
                try:
                    result = await inspect_device(task, device, settings)
                finally:
                    _ACTIVE_TASK_DEVICE_CONTEXT.reset(context_token)
                return _apply_device_snapshot(result, snapshot)

        def _persist_result(result: InspectionResult) -> None:
            current_task = db.get(InspectionTask, task_id)
            result.task_id = task_id
            result.metrics.pop("_ssh_host_fingerprint", None)
            db.add(result)
            persist_inspection_result_raw_output(db, result)
            stored_raw_output = result.raw_output_ref
            current_task.completed_devices += 1
            if result.conclusion in SUCCESS_CONCLUSIONS:
                current_task.success_devices += 1
            elif result.conclusion in FAILED_CONCLUSIONS:
                current_task.failed_devices += 1
            try:
                db.commit()
            except Exception:
                db.rollback()
                # The file is written before the row can be committed. Remove it
                # on rollback so a failed transaction cannot create an orphan.
                from app.services.raw_outputs import remove_raw_output_file

                remove_raw_output_file(stored_raw_output)
                raise

        async def _save_result(result: InspectionResult) -> None:
            device_id = result.device_id
            if device_id is None:
                _persist_result(result)
                return

            async with device_operation_lock(device_id):
                presence_db: Session = session_factory()
                try:
                    device_exists = (
                        presence_db.query(Device.id).filter(Device.id == device_id).first()
                        is not None
                    )
                finally:
                    presence_db.close()
                if not device_exists:
                    result.device_id = None
                _persist_result(result)

        active: dict[asyncio.Task, dict] = {}
        next_snapshot = 0
        while active or next_snapshot < len(snapshots):
            task = db.get(InspectionTask, task_id)
            db.refresh(task)
            if task.status != "cancelled":
                while len(active) < concurrency and next_snapshot < len(snapshots):
                    snapshot = snapshots[next_snapshot]
                    active[asyncio.create_task(_one(snapshot))] = snapshot
                    next_snapshot += 1

            if not active:
                break

            completed, _ = await asyncio.wait(active, return_when=asyncio.FIRST_COMPLETED)
            for future in completed:
                snapshot = active.pop(future)
                try:
                    result = future.result()
                except Exception as exc:  # one device must not abort the task
                    result = _crashed_device_result(snapshot, exc)
                    db.add(result)
                    db.add(ActivityLog(
                        level="ERROR",
                        event="device_collection_error",
                        task_id=task_id,
                        device_ip=snapshot.get("ip"),
                        message=result.message,
                    ))
                await _save_result(result)

        task = db.get(InspectionTask, task_id)
        db.refresh(task)
        if task.status == "cancelled":
            for snapshot in snapshots[next_snapshot:]:
                await _save_result(_cancelled_device_result(
                    task,
                    snapshot,
                    snapshot.get("id") in current_device_ids,
                ))
        task = db.get(InspectionTask, task_id)
        db.refresh(task)
        terminal_status = "completed" if task.failed_devices == 0 else "partial_failed"
        task = _set_task_terminal_status(db, task_id, terminal_status)
        db.add(ActivityLog(event="task_finished", task_id=task.id, message=f"Task finished: {task.status}"))
        db.commit()
    except Exception as exc:
        # Device failures are isolated above. A failure in task setup or result
        # persistence still needs a durable terminal state so the UI does not
        # poll a permanently "running" task until the next application start.
        db.rollback()
        failure_message = redact_runtime_text(
            f"Task execution failed: {type(exc).__name__}: {exc}"
        )
        failure_db: Session = session_factory()
        try:
            failed_task = failure_db.get(InspectionTask, task_id)
            if failed_task:
                selected_ids = [
                    int(value) for value in failed_task.selected_device_ids.split(",") if value
                ]
                current_devices = failure_db.query(Device).filter(Device.id.in_(selected_ids)).all()
                current_by_id = {device.id: device for device in current_devices}
                snapshots = failed_task.selected_device_snapshot or [
                    {
                        "id": device_id,
                        "name": current_by_id[device_id].name if device_id in current_by_id else None,
                        "ip": current_by_id[device_id].ip if device_id in current_by_id else "-",
                        "vendor": current_by_id[device_id].vendor if device_id in current_by_id else None,
                    }
                    for device_id in selected_ids
                ]
                existing_results = failure_db.query(InspectionResult).filter_by(task_id=task_id).all()
                completed_ids = {
                    result.device_id for result in existing_results if result.device_id is not None
                }
                completed_ips = {result.device_ip for result in existing_results}
                for snapshot in snapshots:
                    if snapshot.get("id") in completed_ids or snapshot.get("ip") in completed_ips:
                        continue
                    result = (
                        _cancelled_device_result(
                            failed_task,
                            snapshot,
                            snapshot.get("id") in current_by_id,
                        )
                        if failed_task.status == "cancelled"
                        else _task_failed_device_result(
                            snapshot,
                            failure_message,
                            snapshot.get("id") in current_by_id,
                        )
                    )
                    result.task_id = task_id
                    failure_db.add(result)
                    existing_results.append(result)
                conclusions = [result.conclusion for result in existing_results]
                failure_db.query(InspectionTask).filter(
                    InspectionTask.id == task_id,
                    InspectionTask.status != "cancelled",
                ).update(
                    {InspectionTask.status: "failed"},
                    synchronize_session=False,
                )
                failed_task.completed_devices = len(conclusions)
                failed_task.success_devices = sum(
                    conclusion in SUCCESS_CONCLUSIONS for conclusion in conclusions
                )
                failed_task.failed_devices = sum(
                    conclusion in FAILED_CONCLUSIONS for conclusion in conclusions
                )
                failed_task.finished_at = datetime.utcnow()
                failure_db.add(ActivityLog(
                    level="ERROR",
                    event="task_execution_error",
                    task_id=task_id,
                    message=failure_message,
                ))
                failure_db.commit()
        except Exception:
            failure_db.rollback()
        finally:
            failure_db.close()
    finally:
        discard_task_devices(task_id)
        db.close()
