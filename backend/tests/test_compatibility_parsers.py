"""Reduced documented formats and synthetic edge cases; no user-device captures.

The compatibility audit in output/compatibility-audit-2026-09-12/reproduce.py
records the vendor documentation URLs behind the reduced format examples.
"""
import pytest

from app.services.parsers import parse_ssh_metrics, parse_ssh_output


def scoped(vendor, command, output, item):
    return parse_ssh_output(vendor, f"$ {command}\n{output}", {command: {item}}).metrics


def test_h3c_cpu_retains_slots_and_compares_the_same_window():
    metrics = scoped("H3C", "display cpu-usage", (
        "Slot 1 CPU 0 CPU usage:\n 5% in last 5 seconds\n 100% in last 1 minute\n"
        "Slot 2 CPU 0 CPU usage:\n 99% in last 5 seconds\n 4% in last 1 minute\n"
    ), "cpu")
    assert metrics["cpu"] == 99
    assert metrics["cpu_readings"] == [
        {"object_id": "slot:1/cpu:0", "scope": "overall", "window": "5s", "percent": 5},
        {"object_id": "slot:1/cpu:0", "scope": "overall", "window": "1m", "percent": 100},
        {"object_id": "slot:2/cpu:0", "scope": "overall", "window": "5s", "percent": 99},
        {"object_id": "slot:2/cpu:0", "scope": "overall", "window": "1m", "percent": 4},
    ]


def test_h3c_cpu_table_retains_cpu_identity_and_windows():
    metrics = scoped("H3C", "display cpu-usage", (
        "Slot CPU Last 5 sec Last 1 min Last 5 min\n"
        "1 0 17% 29% 28%\n2 0 45% 88% 99%\n"
    ), "cpu")
    assert metrics["cpu"] == 45
    assert metrics["cpu_readings"][0] == {
        "object_id": "slot:1/cpu:0", "scope": "overall", "window": "5s", "percent": 17,
    }
    assert len(metrics["cpu_readings"]) == 6


def test_huawei_overall_cpu_is_not_replaced_by_overlapping_planes():
    metrics = parse_ssh_metrics("Huawei", (
        "CPU Usage : 10.7%\nManagement-plane CPU Usage: 12.5%\nData-plane CPU Usage : 9.3%\n"
    ))
    assert metrics["cpu"] == 10.7
    assert [reading["scope"] for reading in metrics["cpu_readings"]] == [
        "overall", "management-plane", "data-plane",
    ]


def test_huawei_cpu_usage_ignores_the_maximum_on_the_same_line():
    metrics = scoped(
        "Huawei", "display cpu-usage", "CPU Usage : 7% Max: 100%\n", "cpu"
    )

    assert metrics["cpu"] == 7
    assert metrics["cpu_readings"][0]["percent"] == 7


@pytest.mark.parametrize(("vendor", "output", "expected"), [
    ("H3C", "Used Rate: 32%", 32),
    ("Ruijie", "System Memory Statistic:\nUsedRate : 89%", 89),
    ("Huawei", "Memory Using Percentage: 8%", 8),
])
def test_named_memory_formats(vendor, output, expected):
    metrics = parse_ssh_metrics(vendor, output)
    assert metrics["memory"] == expected
    assert metrics["memory_readings"][0]["percent"] == expected


def test_h3c_memory_keeps_multiple_slots():
    metrics = scoped("H3C", "display memory", (
        "Slot 1:\nMem: 1000 320 680 0 0 0 68%\n"
        "Slot 2:\nMem: 1000 990 10 0 0 0 1%\n"
    ), "memory")
    assert metrics["memory"] == 99
    assert [item["object_id"] for item in metrics["memory_readings"]] == ["slot:1", "slot:2"]


@pytest.mark.parametrize("value", ["101", "-1", "1..5", "NaN", "2.3.4"])
@pytest.mark.parametrize(("vendor", "label", "metric"), [
    ("Huawei", "CPU Usage", "cpu"),
    ("H3C", "CPU usage", "cpu"),
    ("Ruijie", "CPU utilization", "cpu"),
    ("Huawei", "Memory Using Percentage Is", "memory"),
    ("H3C", "Used Rate", "memory"),
    ("Ruijie", "UsedRate", "memory"),
])
def test_invalid_resource_percentages_are_missing(vendor, label, metric, value):
    metrics = parse_ssh_metrics(vendor, f"{label}: {value}%\n")
    assert metric not in metrics
    assert f"{metric}_readings" not in metrics


def test_invalid_memory_ratio_is_missing():
    assert "memory" not in parse_ssh_metrics("H3C", "Mem: 1000 1200 0\n")


def test_h3c_counter_before_label_keeps_interface_details_without_crc_duplication():
    metrics = scoped("H3C", "display interface", (
        "GigabitEthernet1/0/1\nCurrent state: UP\n"
        "Input: 123 input errors, 0 runts, 0 giants, 0 throttles\n"
        "123 CRC, 0 frame, 0 overruns, 0 aborts\n"
        "Input: 7 discards\n"
        "Output: 4 output errors, 0 underruns, 0 buffer failures\n"
        "Output: 8 discards\n"
        "GigabitEthernet1/0/2\nCurrent state: UP\n"
        "Input: 0 input errors, 0 runts\n0 CRC, 0 frame\n"
        "Output: 0 output errors, 0 underruns\n"
    ), "interface_errors")
    assert metrics["interface_error_count"] == 127
    assert metrics["interface_discard_count"] == 15
    assert metrics["interface_error_details"][0] == {
        "name": "GigabitEthernet1/0/1", "input_errors": 123, "output_errors": 4,
        "crc_errors": 123, "input_discards": 7, "output_discards": 8, "total_errors": 127,
    }
    assert metrics["interface_error_details"][1]["total_errors"] == 0


def test_cisco_counter_table_separates_discards_and_does_not_double_count_components():
    metrics = scoped("Cisco", "show interfaces counters errors", (
        "Port Align-Err FCS-Err Xmit-Err Rcv-Err OutDiscards\n"
        "Gi1/0/1 0 3 0 3 100\n"
    ), "interface_errors")

    assert metrics["interface_error_count"] == 3
    assert metrics["interface_discard_count"] == 100
    assert metrics["interface_error_details"] == [{
        "name": "Gi1/0/1",
        "input_errors": 3,
        "output_errors": 0,
        "output_discards": 100,
        "total_errors": 3,
    }]


def test_cisco_multiple_counter_tables_switch_headers_and_merge_each_port_once():
    metrics = scoped("Cisco", "show interfaces counters errors", (
        "Port Align-Err FCS-Err Xmit-Err Rcv-Err UnderSize OutDiscards\n"
        "Gi1/0/1 0 1 0 1 0 5\n"
        "Port Single-Col Multi-Col Late-Col Excess-Col Carri-Sen Runts Giants\n"
        "Gi1/0/1 10 20 30 40 50 60 70\n"
    ), "interface_errors")

    assert metrics["interface_error_count"] == 1
    assert metrics["interface_discard_count"] == 5
    assert metrics["interface_error_details"] == [{
        "name": "Gi1/0/1",
        "input_errors": 1,
        "output_errors": 0,
        "output_discards": 5,
        "total_errors": 1,
    }]


def test_cisco_status_table_uses_the_final_structural_status_and_keeps_non_up_rows():
    metrics = scoped("Cisco", "show interfaces status", (
        "Port Name Status Vlan Duplex Speed Type\n"
        "Gi1/0/1 down 10 auto connected 20 auto auto copper\n"
        "Gi1/0/2 uplink suspended 30 auto auto copper\n"
        "Gi1/0/3 uplink mystery 40 auto auto copper\n"
    ), "interfaces")

    assert metrics["interfaces_total"] == 3
    assert metrics["interfaces_up"] == 1
    assert metrics["interfaces_down"] == 1
    assert metrics["interfaces_unknown"] == 1
    assert [item["physical_status"] for item in metrics["interface_details"]] == [
        "up", "down", "unknown",
    ]


@pytest.mark.parametrize(("vendor", "command"), [
    ("Cisco", "show processes cpu"),
    ("Ruijie", "show cpu"),
])
def test_cisco_style_cpu_line_preserves_every_sampling_window(vendor, command):
    metrics = scoped(vendor, command, (
        "CPU utilization for five seconds: 10%/1%; one minute: 50%; five minutes: 80%\n"
    ), "cpu")

    assert metrics["cpu"] == 10
    assert metrics["cpu_readings"] == [
        {"object_id": "system", "scope": "overall", "window": "5s", "percent": 10},
        {"object_id": "system", "scope": "overall", "window": "1m", "percent": 50},
        {"object_id": "system", "scope": "overall", "window": "5m", "percent": 80},
    ]


def test_interface_counters_after_labels_and_crc_only_are_supported():
    metrics = scoped("Huawei", "display interface", (
        "GigabitEthernet0/0/1 current state : UP\n"
        "Input errors: 7, CRC: 5, Output errors: 2\n"
        "GigabitEthernet0/0/2 current state : UP\nCRC errors 3\n"
    ), "interface_errors")
    assert metrics["interface_error_count"] == 12
    assert metrics["interface_error_details"][1]["crc_errors"] == 3


@pytest.mark.parametrize("output", ["", "Input errors: unavailable\n", "Error: Unknown command\n"])
def test_missing_interface_counters_do_not_create_zero(output):
    metrics = scoped("H3C", "display interface", output, "interface_errors")
    assert "interface_error_count" not in metrics
    assert "interface_error_details" not in metrics


@pytest.mark.parametrize("vendor", ["Huawei", "H3C", "Ruijie", "Cisco"])
def test_valid_forwarding_stp_table_is_zero(vendor):
    metrics = scoped(vendor, "show spanning-tree", (
        "MSTID Port Role STP State Protection\n"
        "0 GE0/0/1 ROOT FORWARDING NONE\n0 GE0/0/2 DESI FORWARDING NONE\n"
    ), "stp")
    assert metrics["stp_discarding_ports"] == 0


@pytest.mark.parametrize("output", ["", "MSTID Port Role STP State Protection\n", "Error: Unknown command\n"])
def test_empty_or_rejected_stp_output_remains_missing(output):
    assert "stp_discarding_ports" not in scoped("Huawei", "display stp brief", output, "stp")


def test_status_column_wins_over_description_and_err_disabled_is_separate():
    metrics = scoped("Cisco", "show interfaces status", (
        "Port Name Status Vlan Duplex Speed Type\n"
        "Gi1/0/1 down connected 1 a-full a-1000 10/100/1000BaseTX\n"
        "Gi1/0/2 uplink err-disabled 1 auto auto 10/100/1000BaseTX\n"
        "Gi1/0/3 disabled 1 auto auto 10/100/1000BaseTX\n"
    ), "interfaces")
    assert metrics["interfaces_up"] == 1
    assert metrics["interfaces_admin_down"] == 1
    assert metrics["interfaces_error_disabled"] == 1
    assert metrics["interface_details"][0]["physical_status"] == "up"
    assert metrics["interface_details"][1]["error_disabled"] is True


def test_physical_and_protocol_state_are_distinct():
    metrics = scoped("H3C", "display interface brief", (
        "Interface Link Protocol Primary IP Description\nGE0/1 UP DOWN -- uplink\n"
    ), "interfaces")
    assert metrics["interfaces_up"] == 1
    assert metrics["interface_details"] == [{
        "name": "GE0/1", "physical_status": "up", "protocol_status": "down",
        "admin_down": False, "error_disabled": False,
    }]


def test_h3c_numeric_temperature_limits_are_preserved_and_evaluated():
    temperature = scoped("H3C", "display environment", (
        "Board Temperature Lower limit Upper limit\n0 33 10 45\n1 51 10 45\n"
    ), "temperature")["temperature"]
    assert temperature["sensors"] == 2
    assert temperature["alarming"] == 1
    assert temperature["readings"][0] == {
        "name": "0", "current": 33, "unit": "C", "status": "normal",
        "thresholds": {"lower": 10, "upper": 45},
    }


@pytest.mark.parametrize("output", [
    "Board Temperature Lower limit Upper limit\n0 33 45 10\n",
    "Board Temperature Lower limit Upper limit\n0 -- 10 45\n",
])
def test_invalid_numeric_temperature_rows_remain_missing(output):
    assert "temperature" not in scoped("H3C", "display environment", output, "temperature")


@pytest.mark.parametrize(("vendor", "output", "model", "version"), [
    ("Huawei", "Huawei Versatile Routing Platform Software\n"
     "VRP (R) software, Version 8.180 (CE6865 V200R005C10SPC800)\n"
     "HUAWEI CE6865-48S8CQ-EI uptime is 10 days, 2 hours\n",
     "CE6865-48S8CQ-EI", "V200R005C10SPC800"),
    ("H3C", "H3C Comware Software, Version 7.1.070, Release 3507P26\n"
     "H3C S5560X-30C-EI uptime is 10 weeks, 2 days\n",
     "S5560X-30C-EI", "7.1.070, Release 3507P26"),
    ("Ruijie", "System description : Ruijie Gigabit Switch (S5750C-28GT4XS-H)\n"
     "System software version : RGOS 11.4(1)B70P1\n",
     "S5750C-28GT4XS-H", "RGOS 11.4(1)B70P1"),
])
def test_version_output_provides_model_and_software_version(vendor, output, model, version):
    command = "show version" if vendor == "Ruijie" else "display version"
    for metrics in [parse_ssh_metrics(vendor, output), scoped(vendor, command, output, "device_information")]:
        assert metrics["model"] == model
        assert metrics["software_version"] == version


def test_h3c_version_is_read_when_the_platform_heading_is_on_its_own_line():
    output = (
        "H3C Comware Platform Software\n"
        "Comware Software, Version 7.1.070, Release 3507P26\n"
        "H3C S5560X-30C-EI uptime is 10 weeks, 2 days\n"
    )

    metrics = scoped("H3C", "display version", output, "device_information")

    assert metrics["model"] == "S5560X-30C-EI"
    assert metrics["software_version"] == "7.1.070, Release 3507P26"


def test_scoped_resource_details_do_not_leak_from_unrelated_commands():
    metrics = scoped("H3C", "display current-configuration", "CPU usage: 99%\n", "device_information")
    assert "cpu" not in metrics
    assert "cpu_readings" not in metrics


@pytest.mark.parametrize("output", ["CPU Usage: 99%\n", "Used Rate: 32%\n", "Mem: 3907780 1354276 2553504\n"])
def test_resource_text_does_not_fabricate_storage_metrics(output):
    assert "storage" not in parse_ssh_metrics("H3C", output)


@pytest.mark.parametrize("value", ["101", "-1", "1..5"])
def test_invalid_storage_percentage_remains_missing(value):
    assert "storage" not in scoped("H3C", "display storage", f"Storage usage: {value}%", "storage")


def test_h3c_physical_memory_and_container_are_separate_scopes():
    # Reduced shape from H3C's hardware resources reference, synthetic values.
    metrics = scoped("H3C", "display memory", (
        "Memory statistics are measured in KB:\nSlot 0:\nMem: 1000 200 800\n"
        "Container memory statistics are measured in KB:\nSlot 0:\nMem: 1000 990 10\n"
    ), "memory")
    assert metrics["memory"] == 20
    assert [reading["scope"] for reading in metrics["memory_readings"]] == ["overall", "container"]


def test_h3c_dedicated_power_table_keeps_status_and_identity():
    # Format: H3C_CR-14016 hardware resource command reference (2023-09).
    power = scoped("H3C", "display power", (
        "PowerID State Mode Current(A) Voltage(V) Power(W)\n"
        "1 Normal AC 5.00 12.00 60\n2 Fault AC -- -- --\n"
    ), "power_supplies")["power_supplies"]
    assert power["total"] == 2
    assert power["abnormal"] == 1
    assert [(row["id"], row["status"]) for row in power["components"]] == [("1", "normal"), ("2", "abnormal")]


def test_h3c_dedicated_fan_blocks_keep_identity_and_state():
    fans = scoped("H3C", "display fan", (
        "Fan Frame 1 State: Normal\nAirflow Direction: Port-to-power\n"
        "Fan Frame 2\nState: FanDirectionFault\nAirflow Direction: Power-to-port\n"
    ), "fans")["fans"]
    assert fans["total"] == 2
    assert fans["abnormal"] == 1
    assert [row["id"] for row in fans["components"]] == ["1", "2"]


def test_unavailable_h3c_fan_state_is_not_normal():
    fans = scoped("H3C", "display fan", "Fan Frame 1\nState: Unknown\n", "fans")["fans"]
    assert fans["components"][0]["status"] == "unknown"


def test_discard_only_interface_does_not_invent_error_totals():
    metrics = scoped("H3C", "display interface", "GigabitEthernet1/0/1\nInput: 7 discards\n", "interface_errors")
    assert "interface_error_count" not in metrics
    assert metrics["interface_discard_count"] == 7
    assert "total_errors" not in metrics["interface_error_details"][0]


def test_cisco_short_stp_roles_and_states_are_recognized():
    metrics = scoped("Cisco", "show spanning-tree", (
        "Interface Role Sts Cost Prio.Nbr Type\n"
        "Gi1/0/1 Desg FWD 4 128.1 P2p\nGi1/0/2 Altn BLK 4 128.2 P2p\n"
    ), "stp")
    assert metrics["stp_discarding_ports"] == 1


def test_temperature_table_stops_at_the_next_section():
    temperature = scoped("H3C", "display environment", (
        "Board Temperature Lower limit Upper limit\n0 33 10 45\n"
        "Other diagnostic table\n1 99 10 100\n"
    ), "temperature")["temperature"]
    assert temperature["sensors"] == 1
