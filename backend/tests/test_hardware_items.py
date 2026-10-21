import pytest

from app.services.collectors import SSH_COMMANDS, SSH_COMMAND_ITEMS, ssh_commands_for_checks
from app.services.parsers import parse_ssh_metrics

VENDORS = ("Huawei", "H3C", "Ruijie", "Cisco")
HARDWARE_ITEMS = (
    "active_alarms",
    "boards",
    "power_supplies",
    "fans",
    "temperature",
    "interface_errors",
    "link_aggregation",
    "routing_neighbors",
    "ntp",
    "storage",
    "configuration_saved",
)


@pytest.mark.parametrize("vendor", VENDORS)
def test_full_inspection_covers_every_hardware_item(vendor):
    covered = set()
    for items in SSH_COMMAND_ITEMS[vendor].values():
        covered |= items
    missing = [i for i in HARDWARE_ITEMS if i not in covered]
    assert missing == []


@pytest.mark.parametrize("vendor", VENDORS)
def test_every_mapped_command_is_actually_sent(vendor):
    sent = set(SSH_COMMANDS[vendor]["quick"]) | set(SSH_COMMANDS[vendor]["full"])
    mapped = set(SSH_COMMAND_ITEMS[vendor])
    assert mapped - sent == set()


@pytest.mark.parametrize("vendor", VENDORS)
def test_quick_ssh_plan_covers_interfaces_and_stp(vendor):
    commands = ssh_commands_for_checks(vendor, "quick", {"interfaces", "stp"})
    covered = {
        item
        for command in commands
        for item in SSH_COMMAND_ITEMS[vendor].get(command, set())
    }

    assert {"interfaces", "stp"} <= covered


def test_huawei_hardware_output_is_parsed():
    raw = (
        "$ display device\n"
        "Slot 1  CE6865-48S8CQ-EI  Normal\n"
        "Slot 2  CE6865-48S8CQ-EI  Abnormal\n"
        "Power 1  PWR-800WA-B  Normal\n"
        "Power 2  PWR-800WA-B  Absent\n"
        "FAN 1  FAN-40BA-B  Normal\n"
        "\n$ display ntp status\n"
        " clock status: synchronized\n"
        " reference clock ID: 10.0.0.1\n"
    )

    metrics = parse_ssh_metrics("Huawei", raw)

    assert metrics["boards"]["total"] == 2
    assert metrics["boards"]["abnormal"] == 1
    assert metrics["power_supplies"]["abnormal"] == 1
    assert metrics["fans"]["abnormal"] == 0
    assert metrics["ntp"]["synchronized"] is True


def test_cisco_storage_and_neighbors_are_parsed():
    raw = (
        "$ show file systems\n"
        "      Size(b)     Free(b)      Type  Flags  Prefixes\n"
        "*   256487136    64121784     flash    rw   flash:\n"
        "\n$ show ip ospf neighbor\n"
        "Neighbor ID     Pri   State           Dead Time   Address\n"
        "1.1.1.1           1   FULL/DK         00:00:35    10.0.0.1\n"
    )

    metrics = parse_ssh_metrics("Cisco", raw)

    assert metrics["storage"]["used_percent"] == 75
    assert metrics["routing_neighbors"]["total"] == 1
    assert metrics["routing_neighbors"]["established"] == 1



def test_zero_interface_errors_is_valid_data_not_missing():
    # 零错误计数是有效数据，不得被当作未采集丢弃
    raw = "$ display interface brief\nGE1/0/1  up  up  CRC errors 0\n"
    metrics = parse_ssh_metrics("Huawei", raw)
    assert "interface_error_count" in metrics
    assert metrics["interface_error_count"] == 0


def test_zero_abnormal_hardware_is_still_reported():
    raw = "$ display device\nSlot 1  CE6865  Normal\n"
    metrics = parse_ssh_metrics("Huawei", raw)
    assert metrics["boards"]["abnormal"] == 0
