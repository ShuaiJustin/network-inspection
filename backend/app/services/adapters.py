from copy import deepcopy
from fnmatch import fnmatchcase

from app.services.vendor_registry import DEVICE_TYPES, PLATFORMS, resolve_platform


VERIFIED_ADAPTERS: tuple[dict, ...] = ()


def list_verified_adapters() -> list[dict]:
    return deepcopy(list(VERIFIED_ADAPTERS))


def list_adapter_capabilities() -> list[dict]:
    """Describe implemented platform coverage without claiming field verification."""

    device_type_order = [value for value, _label in DEVICE_TYPES]
    capabilities = []
    for platform in PLATFORMS.values():
        supported_types = set(platform.device_types)
        capabilities.append({
            "status": "unverified",
            "vendor": platform.vendor_id,
            "platform_id": platform.id,
            "platform_label": platform.label,
            "device_types": [
                value for value in device_type_order if value in supported_types
            ],
            "protocols": ["SNMP", "SSH"],
            "backup_command": platform.backup_command,
            "inspection_items": sorted({
                item for command in platform.commands for item in command.items
            }),
        })
    return capabilities


REQUIRED_VERIFICATION_FIELDS = frozenset({
    "vendor",
    "platform_id",
    "device_type",
    "model_range",
    "version_range",
    "inspection_items",
    "adapter_version",
    "verification_date",
})


def _matches(pattern: str, value: str) -> bool:
    return fnmatchcase(value.casefold(), pattern.casefold())


def adapter_compatibility(
    vendor: str,
    platform_id: str | None,
    device_type: str,
    model: str | None,
    software_version: str | None,
) -> dict:
    """Return evidence-backed adapter compatibility for one observed device.

    A vendor or platform match alone is not verification. Both the device model
    and software version must match a complete, shipped verification record.
    """

    try:
        platform = resolve_platform(vendor, platform_id)
    except (ValueError, KeyError):
        return {
            "status": "unverified",
            "platform_id": platform_id,
            "model": model,
            "software_version": software_version,
            "reason": "Selected platform is not available in the adapter registry",
        }
    resolved_platform_id = platform.id
    canonical_vendor = platform.vendor_id
    for record in VERIFIED_ADAPTERS:
        if record.get("status") != "verified":
            continue
        if not REQUIRED_VERIFICATION_FIELDS <= record.keys():
            continue
        if not model or not software_version:
            continue
        if (
            record["vendor"] == canonical_vendor
            and record["platform_id"] == resolved_platform_id
            and record["device_type"] == device_type
            and _matches(str(record["model_range"]), model)
            and _matches(str(record["version_range"]), software_version)
        ):
            return {
                "status": "verified",
                "platform_id": resolved_platform_id,
                "model": model,
                "software_version": software_version,
                "adapter_version": str(record["adapter_version"]),
                "inspection_items": [str(item) for item in record["inspection_items"]],
                "verification_date": str(record["verification_date"]),
            }

    return {
        "status": "unverified",
        "platform_id": resolved_platform_id,
        "model": model,
        "software_version": software_version,
        "reason": "Model or software version is not in the verified adapter catalog",
    }
