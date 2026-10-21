import pytest

from app.services.vendor_registry import (
    CommandSpec,
    device_catalog,
    not_applicable_checks,
    normalize_vendor,
    resolve_platform,
)


EXPECTED_PLATFORMS = {
    "Huawei": ("huawei-vrp", "huawei"),
    "H3C": ("h3c-comware", "hp_comware"),
    "Ruijie": ("ruijie-rgos", "ruijie_os"),
    "Cisco": ("cisco-ios", "cisco_ios"),
}

EXPECTED_COMMANDS = {
    "Huawei": {
        "quick": [
            "display current-configuration | include sysname",
            "display version",
            "display cpu-usage",
            "display memory-usage",
            "display memory",
            "display interface brief",
            "display stp brief",
            "display alarm active",
        ],
        "full": [
            "display device",
            "display health",
            "display temperature",
            "display eth-trunk",
            "display ospf peer brief",
            "display ntp status",
            "display storage",
            "dir",
            "compare configuration",
            "display startup",
            "display interface",
        ],
        "backup": "display current-configuration",
    },
    "H3C": {
        "quick": [
            "display current-configuration | include sysname",
            "display version",
            "display cpu-usage",
            "display memory",
            "display interface brief",
            "display stp brief",
            "display alarm active",
        ],
        "full": [
            "display device",
            "display power",
            "display fan",
            "display environment",
            "display link-aggregation verbose",
            "display ospf peer",
            "display ntp-service status",
            "dir",
            "display startup",
            "display interface",
        ],
        "backup": "display current-configuration",
    },
    "Ruijie": {
        "quick": [
            "show running-config | include hostname",
            "show version",
            "show cpu",
            "show memory",
            "show interfaces status",
            "show spanning-tree",
            "show alarm",
        ],
        "full": [
            "show environment",
            "show aggregateport summary",
            "show ip ospf neighbor",
            "show ntp status",
            "show file systems",
            "show boot",
            "show interfaces counters errors",
        ],
        "backup": "show running-config",
    },
    "Cisco": {
        "quick": [
            "show running-config | include hostname",
            "show version",
            "show processes cpu",
            "show processes memory",
            "show interfaces status",
            "show ip interface brief",
            "show spanning-tree summary",
            "show facility-alarm status",
        ],
        "full": [
            "show environment all",
            "show inventory",
            "show etherchannel summary",
            "show ip ospf neighbor",
            "show ntp status",
            "show file systems",
            "show boot",
            "show interfaces counters errors",
        ],
        "backup": "show running-config",
    },
}


def test_registry_preserves_current_platform_and_command_plans():
    for vendor, (platform_id, netmiko_type) in EXPECTED_PLATFORMS.items():
        platform = resolve_platform(vendor)

        assert platform.id == platform_id
        assert platform.netmiko_type == netmiko_type
        assert platform.commands_for("quick") == EXPECTED_COMMANDS[vendor]["quick"]
        assert platform.commands_for("full") == (
            EXPECTED_COMMANDS[vendor]["quick"] + EXPECTED_COMMANDS[vendor]["full"]
        )
        assert platform.backup_command == EXPECTED_COMMANDS[vendor]["backup"]


def test_registry_owns_vendor_specific_snmp_usage_collectors():
    assert resolve_platform("Huawei").snmp_usage_profile == "entity-percent"
    assert resolve_platform("Huawei").snmp_usage_oids == {
        "cpu": "1.3.6.1.4.1.2011.5.25.31.1.1.1.1.5",
        "memory": "1.3.6.1.4.1.2011.5.25.31.1.1.1.1.7",
    }
    assert resolve_platform("Cisco").snmp_usage_profile == "cisco-pools"
    assert resolve_platform("Cisco").snmp_usage_oids["memory_used"] == "1.3.6.1.4.1.9.48.1.1.1.5"


def test_each_collection_command_is_a_single_command_spec():
    for vendor in EXPECTED_PLATFORMS:
        platform = resolve_platform(vendor)
        assert all(isinstance(command, CommandSpec) for command in platform.commands)
        assert len({command.command for command in platform.commands}) == len(platform.commands)
        assert all(command.items for command in platform.commands)


@pytest.mark.parametrize(
    ("alias", "canonical"),
    [("华为", "Huawei"), ("新华三", "H3C"), ("华三", "H3C"), ("锐捷", "Ruijie"), ("思科", "Cisco")],
)
def test_vendor_aliases_are_owned_by_the_registry(alias, canonical):
    assert normalize_vendor(alias) == canonical


def test_platform_must_belong_to_the_selected_vendor():
    with pytest.raises(ValueError, match="does not belong"):
        resolve_platform("Huawei", "cisco-ios")


def test_device_type_capabilities_are_resolved_by_the_registry():
    assert not_applicable_checks("Huawei", "huawei-vrp", "firewall") == {"stp"}
    assert not_applicable_checks("Huawei", "huawei-vrp", "switch") == set()


def test_catalog_exposes_current_vendors_platforms_and_device_types():
    catalog = device_catalog()

    assert [vendor["id"] for vendor in catalog["vendors"]] == list(EXPECTED_PLATFORMS)
    assert [item["id"] for item in catalog["device_types"]] == ["switch", "router", "firewall"]
    assert catalog["vendors"][0]["default_platform_id"] == "huawei-vrp"
    assert catalog["vendors"][0]["platforms"][0]["netmiko_type"] == "huawei"
