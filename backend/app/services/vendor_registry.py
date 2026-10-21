from dataclasses import dataclass


DEVICE_TYPES = (
    ("switch", "交换机"),
    ("router", "路由器"),
    ("firewall", "防火墙"),
)
DEVICE_TYPE_ALIASES = {"交换机": "switch", "路由器": "router", "防火墙": "firewall"}


@dataclass(frozen=True)
class CommandSpec:
    command: str
    items: frozenset[str]
    level: str
    is_fallback: bool = False


@dataclass(frozen=True)
class PlatformConfig:
    id: str
    vendor_id: str
    label: str
    netmiko_type: str
    parser_profile: str
    snmp_usage_profile: str | None
    snmp_usage_oids: dict[str, str]
    device_types: frozenset[str]
    commands: tuple[CommandSpec, ...]
    backup_command: str
    not_applicable_by_device_type: dict[str, frozenset[str]]
    snmp_usage_zero_policy: str = "accept"

    def commands_for(self, inspection_level: str) -> list[str]:
        levels = {"quick", "full"} if inspection_level == "full" else {"quick"}
        return [spec.command for spec in self.commands if spec.level in levels]

    @property
    def command_items(self) -> dict[str, set[str]]:
        return {spec.command: set(spec.items) for spec in self.commands}


@dataclass(frozen=True)
class VendorConfig:
    id: str
    label: str
    aliases: frozenset[str]
    default_platform_id: str
    platform_ids: tuple[str, ...]


def _command(command: str, level: str, *items: str) -> CommandSpec:
    return CommandSpec(command, frozenset(items), level)


def _fallback_command(command: str, level: str, *items: str) -> CommandSpec:
    return CommandSpec(command, frozenset(items), level, is_fallback=True)


PLATFORMS = {
    "huawei-vrp": PlatformConfig(
        id="huawei-vrp",
        vendor_id="Huawei",
        label="VRP",
        netmiko_type="huawei",
        parser_profile="Huawei",
        snmp_usage_profile="entity-percent",
        snmp_usage_oids={
            "cpu": "1.3.6.1.4.1.2011.5.25.31.1.1.1.1.5",
            "memory": "1.3.6.1.4.1.2011.5.25.31.1.1.1.1.7",
        },
        device_types=frozenset({"switch", "router", "firewall"}),
        commands=(
            _command("display current-configuration | include sysname", "quick", "device_information"),
            _command("display version", "quick", "device_information", "uptime"),
            _command("display cpu-usage", "quick", "cpu"),
            _command("display memory-usage", "quick", "memory"),
            _fallback_command("display memory", "quick", "memory"),
            _command("display interface brief", "quick", "interfaces"),
            _command("display stp brief", "quick", "stp"),
            _command("display alarm active", "quick", "active_alarms"),
            _command("display device", "full", "boards", "power_supplies", "fans"),
            _command("display health", "full", "temperature", "storage"),
            _command("display temperature", "full", "temperature"),
            _command("display eth-trunk", "full", "link_aggregation"),
            _command("display ospf peer brief", "full", "routing_neighbors"),
            _command("display ntp status", "full", "ntp"),
            _command("display storage", "full", "storage"),
            _command("dir", "full", "storage"),
            _command("compare configuration", "full", "configuration_saved"),
            _command("display startup", "full", "configuration_saved"),
            _command("display interface", "full", "interface_errors"),
        ),
        backup_command="display current-configuration",
        not_applicable_by_device_type={"firewall": frozenset({"stp"})},
        snmp_usage_zero_policy="require-active-entity",
    ),
    "h3c-comware": PlatformConfig(
        id="h3c-comware",
        vendor_id="H3C",
        label="Comware",
        netmiko_type="hp_comware",
        parser_profile="H3C",
        snmp_usage_profile="entity-percent",
        snmp_usage_oids={
            "cpu": "1.3.6.1.4.1.25506.2.6.1.1.1.1.6",
            "memory": "1.3.6.1.4.1.25506.2.6.1.1.1.1.8",
        },
        device_types=frozenset({"switch", "router", "firewall"}),
        commands=(
            _command("display current-configuration | include sysname", "quick", "device_information"),
            _command("display version", "quick", "device_information", "uptime"),
            _command("display cpu-usage", "quick", "cpu"),
            _command("display memory", "quick", "memory"),
            _command("display interface brief", "quick", "interfaces"),
            _command("display stp brief", "quick", "stp"),
            _command("display alarm active", "quick", "active_alarms"),
            _command("display device", "full", "boards", "power_supplies", "fans"),
            _fallback_command("display power", "full", "power_supplies"),
            _fallback_command("display fan", "full", "fans"),
            _command("display environment", "full", "temperature"),
            _command("display link-aggregation verbose", "full", "link_aggregation"),
            _command("display ospf peer", "full", "routing_neighbors"),
            _command("display ntp-service status", "full", "ntp"),
            _command("dir", "full", "storage"),
            _command("display startup", "full", "configuration_saved"),
            _command("display interface", "full", "interface_errors"),
        ),
        backup_command="display current-configuration",
        not_applicable_by_device_type={"firewall": frozenset({"stp"})},
        snmp_usage_zero_policy="require-active-entity",
    ),
    "ruijie-rgos": PlatformConfig(
        id="ruijie-rgos",
        vendor_id="Ruijie",
        label="RGOS",
        netmiko_type="ruijie_os",
        parser_profile="Ruijie",
        snmp_usage_profile="entity-percent",
        snmp_usage_oids={
            "cpu": "1.3.6.1.4.1.4881.1.1.10.2.36.1.1.1",
            "memory": "1.3.6.1.4.1.4881.1.1.10.2.35.1.1.1.3",
        },
        device_types=frozenset({"switch", "router", "firewall"}),
        commands=(
            _command("show running-config | include hostname", "quick", "device_information"),
            _command("show version", "quick", "device_information", "uptime"),
            _command("show cpu", "quick", "cpu"),
            _command("show memory", "quick", "memory"),
            _command("show interfaces status", "quick", "interfaces"),
            _command("show spanning-tree", "quick", "stp"),
            _command("show alarm", "quick", "active_alarms"),
            _command("show environment", "full", "boards", "power_supplies", "fans", "temperature"),
            _command("show aggregateport summary", "full", "link_aggregation"),
            _command("show ip ospf neighbor", "full", "routing_neighbors"),
            _command("show ntp status", "full", "ntp"),
            _command("show file systems", "full", "storage"),
            _command("show boot", "full", "configuration_saved"),
            _command("show interfaces counters errors", "full", "interface_errors"),
        ),
        backup_command="show running-config",
        not_applicable_by_device_type={"firewall": frozenset({"stp"})},
    ),
    "cisco-ios": PlatformConfig(
        id="cisco-ios",
        vendor_id="Cisco",
        label="IOS / IOS XE",
        netmiko_type="cisco_ios",
        parser_profile="Cisco",
        snmp_usage_profile="cisco-pools",
        snmp_usage_oids={
            "cpu": "1.3.6.1.4.1.9.109.1.1.1.1.6",
            "memory_used": "1.3.6.1.4.1.9.48.1.1.1.5",
            "memory_free": "1.3.6.1.4.1.9.48.1.1.1.6",
            "memory_valid": "1.3.6.1.4.1.9.48.1.1.1.4",
        },
        device_types=frozenset({"switch", "router"}),
        commands=(
            _command("show running-config | include hostname", "quick", "device_information"),
            _command("show version", "quick", "device_information", "uptime"),
            _command("show processes cpu", "quick", "cpu"),
            _command("show processes memory", "quick", "memory"),
            _command("show interfaces status", "quick", "interfaces"),
            # Routers have no switchport status table.
            _fallback_command("show ip interface brief", "quick", "interfaces"),
            _command("show spanning-tree summary", "quick", "stp"),
            _command("show facility-alarm status", "quick", "active_alarms"),
            _command("show environment all", "full", "boards", "power_supplies", "fans", "temperature"),
            _command("show inventory", "full", "boards"),
            _command("show etherchannel summary", "full", "link_aggregation"),
            _command("show ip ospf neighbor", "full", "routing_neighbors"),
            _command("show ntp status", "full", "ntp"),
            _command("show file systems", "full", "storage"),
            _command("show boot", "full", "configuration_saved"),
            _command("show interfaces counters errors", "full", "interface_errors"),
        ),
        backup_command="show running-config",
        not_applicable_by_device_type={"firewall": frozenset({"stp"})},
    ),
}

VENDORS = {
    "Huawei": VendorConfig("Huawei", "华为", frozenset({"华为"}), "huawei-vrp", ("huawei-vrp",)),
    "H3C": VendorConfig("H3C", "新华三", frozenset({"新华三", "华三"}), "h3c-comware", ("h3c-comware",)),
    "Ruijie": VendorConfig("Ruijie", "锐捷", frozenset({"锐捷"}), "ruijie-rgos", ("ruijie-rgos",)),
    "Cisco": VendorConfig("Cisco", "思科", frozenset({"思科"}), "cisco-ios", ("cisco-ios",)),
}

VENDOR_ALIASES = {
    alias: vendor.id
    for vendor in VENDORS.values()
    for alias in vendor.aliases
}


def normalize_vendor(value: str) -> str:
    text = value.strip()
    canonical = VENDOR_ALIASES.get(text, text)
    if canonical not in VENDORS:
        raise ValueError(f"Unsupported vendor: {text}")
    return canonical


def normalize_device_type(value: str) -> str:
    text = value.strip()
    canonical = DEVICE_TYPE_ALIASES.get(text, text)
    if canonical not in {device_type for device_type, _label in DEVICE_TYPES}:
        raise ValueError(f"Unsupported device type: {text}")
    return canonical


def resolve_platform(vendor: str, platform_id: str | None = None) -> PlatformConfig:
    canonical_vendor = normalize_vendor(vendor)
    selected_id = platform_id or VENDORS[canonical_vendor].default_platform_id
    platform = PLATFORMS.get(selected_id)
    if not platform:
        raise ValueError(f"Unsupported platform: {selected_id}")
    if platform.vendor_id != canonical_vendor:
        raise ValueError(f"Platform {selected_id} does not belong to vendor {canonical_vendor}")
    return platform


def not_applicable_checks(vendor: str, platform_id: str | None, device_type: str) -> set[str]:
    platform = resolve_platform(vendor, platform_id)
    normalized_type = normalize_device_type(device_type)
    if normalized_type not in platform.device_types:
        raise ValueError(f"Device type {normalized_type} is not supported by platform {platform.id}")
    return set(platform.not_applicable_by_device_type.get(normalized_type, ()))


def device_catalog() -> dict:
    device_types = [{"id": value, "label": label} for value, label in DEVICE_TYPES]
    vendors = []
    for vendor in VENDORS.values():
        platforms = [PLATFORMS[platform_id] for platform_id in vendor.platform_ids]
        supported_types = [
            value for value, _label in DEVICE_TYPES
            if any(value in platform.device_types for platform in platforms)
        ]
        vendors.append({
            "id": vendor.id,
            "label": vendor.label,
            "aliases": sorted(vendor.aliases),
            "default_platform_id": vendor.default_platform_id,
            "device_types": supported_types,
            "platforms": [{
                "id": platform.id,
                "label": platform.label,
                "netmiko_type": platform.netmiko_type,
                "device_types": [value for value, _label in DEVICE_TYPES if value in platform.device_types],
            } for platform in platforms],
        })
    return {"vendors": vendors, "device_types": device_types}
