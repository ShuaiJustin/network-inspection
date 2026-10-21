import pytest

from app.services.parsers import parse_ssh_metrics, parse_ssh_output


def test_huawei_firewall_decimal_cpu_usage_is_parsed():
    raw = (
        "$ display cpu-usage\n"
        "CPU Usage : 10.7%\n"
        "Management-plane CPU Usage: 12.5%\n"
        "Data-plane CPU Usage : 9.3%\n"
    )

    metrics = parse_ssh_metrics("Huawei", raw)

    assert metrics["cpu"] == 10.7


def test_h3c_memory_uses_free_ratio_rather_than_unparsed_output():
    raw = (
        "$ display memory\n"
        "             Total      Used      Free    Shared   Buffers    Cached   FreeRatio\n"
        "Mem:      3907780   1354276   2553504         0     41008    405292       65.3%\n"
    )

    metrics = parse_ssh_metrics("H3C", raw)

    assert metrics["memory"] == 35


def test_ruijie_uptime_and_memory_are_parsed():
    raw = (
        "$ show version\n"
        " System description : Ruijie Full Gigabit Switch\n"
        " System uptime      : 21d:2h:15m:33s\n"
        "\n$show memory\n"
        "System Memory Statistic:\n"
        " Total    Used     Free    Used Rate\n"
        " 2097152  838860   1258292  40%\n"
    )

    metrics = parse_ssh_metrics("Ruijie", raw)

    assert metrics["uptime"] == "21d:2h:15m:33s"
    assert metrics["memory"] == 40


@pytest.mark.parametrize("vendor", ["Cisco", "Ruijie"])
def test_ios_style_ntp_synchronization_status_is_parsed(vendor):
    metrics = parse_ssh_metrics(
        vendor,
        "$ show ntp status\nClock is synchronized, stratum 2, reference is 192.0.2.1\n",
    )

    assert metrics["ntp"] == {"synchronized": True}


def test_ios_style_unsynchronized_ntp_status_is_parsed():
    metrics = parse_ssh_metrics(
        "Cisco",
        "$ show ntp status\nClock is unsynchronized, stratum 16, no reference clock\n",
    )

    assert metrics["ntp"] == {"synchronized": False}


def test_explicitly_disabled_ntp_is_not_applicable_evidence():
    metrics = parse_ssh_metrics(
        "Cisco",
        "$ show ntp status\nNTP is not enabled.\n",
    )

    assert metrics["ntp"] == {"configured": False, "synchronized": None}


def test_cisco_stp_summary_counts_blocking_ports():
    raw = (
        "$ show spanning-tree summary\n"
        "Name                 Blocking Listening Learning Forwarding STP Active\n"
        "VLAN0001                   2         0        0         10        12\n"
        "VLAN0010                   1         0        0          8         9\n"
    )

    metrics = parse_ssh_metrics("Cisco", raw)

    assert metrics["stp_discarding_ports"] == 3


def test_huawei_interface_brief_is_parsed_as_an_interface_summary():
    raw = (
        "$ display interface brief\n"
        "Interface                   PHY   Protocol InUti OutUti inErrors outErrors\n"
        "GE1/0/1                     up    up       0%    0%     0        0\n"
        "GE1/0/2                     *down down     0%    0%     0        0\n"
        "XGE1/0/1                    down  down     0%    0%     0        0\n"
    )

    metrics = parse_ssh_metrics("Huawei", raw)

    assert metrics["interface_collection_status"] == "success"
    assert metrics["interfaces_total"] == 3
    assert metrics["interfaces_up"] == 1
    assert metrics["interfaces_down"] == 2
    assert metrics["interfaces_admin_down"] == 1


def test_cisco_interface_status_is_parsed_as_an_interface_summary():
    raw = (
        "$ show interfaces status\n"
        "Port      Name               Status       Vlan       Duplex  Speed Type\n"
        "Gi1/0/1   uplink             connected    trunk      a-full a-1000 10/100/1000BaseTX\n"
        "Gi1/0/2                      notconnect   1          auto   auto   10/100/1000BaseTX\n"
        "Gi1/0/3                      disabled     1          auto   auto   10/100/1000BaseTX\n"
    )

    metrics = parse_ssh_metrics("Cisco", raw)

    assert metrics["interface_collection_status"] == "success"
    assert metrics["interfaces_total"] == 3
    assert metrics["interfaces_up"] == 1
    assert metrics["interfaces_down"] == 2
    assert metrics["interfaces_admin_down"] == 1


def test_vendor_patterns_do_not_leak_across_vendors():
    huawei_only = "Memory Using Percentage Is: 61%\n"

    assert parse_ssh_metrics("Huawei", huawei_only)["memory"] == 61
    assert "memory" not in parse_ssh_metrics("Cisco", huawei_only)


def test_unknown_vendor_is_rejected():
    with pytest.raises(KeyError):
        parse_ssh_metrics("NotAVendor", "sysname X\n")


def test_cisco_decimal_cpu_utilisation_is_parsed():
    """Some IOS-XE builds report a fractional CPU percentage.

    CISCO_CPU only accepted whole numbers, so "12.5%" returned no reading at
    all and the CPU item was reported as unsupported on a device that does
    publish it. The Huawei pattern already allowed a decimal.
    """
    output = (
        "$ show processes cpu\n"
        "CPU utilization for five seconds: 12.5%/0%; one minute: 9%; five minutes: 8%\n"
    )

    assert parse_ssh_metrics("Cisco", output)["cpu"] == 12.5


def test_cisco_cpu_reports_utilisation_not_the_interrupt_rate():
    """The five-second field is "utilisation/interrupt"; only the first counts."""
    output = (
        "$ show processes cpu\n"
        "CPU utilization for five seconds: 3%/15%; one minute: 4%; five minutes: 5%\n"
    )

    assert parse_ssh_metrics("Cisco", output)["cpu"] == 3


def test_h3c_cpu_percentage_line_is_parsed_without_a_usage_header():
    """Comware prints the reading on its own "N% in last 5 seconds" line.

    The H3C extractors only matched a "CPU usage:" header, so a build that
    omits it reported no CPU reading and the item showed as unsupported.
    """
    output = "$ display cpu-usage\n       12% in last 5 seconds\n       10% in last 1 minute\n"

    assert parse_ssh_metrics("H3C", output)["cpu"] == 12


def test_h3c_decimal_cpu_percentage_line_is_parsed():
    output = "$ display cpu-usage\n       12.5% in last 5 seconds\n"

    assert parse_ssh_metrics("H3C", output)["cpu"] == 12.5


def test_h3c_administratively_down_interface_is_counted_as_admin_down():
    """Comware marks a shut interface as ADM, not *down or disabled.

    Verified against real device output published in the ntc-templates test
    corpus (release 60615P16), where GE0/0 and GE0/2 are "ADM  DOWN". Without
    ADM in the admin-down set, a port an operator deliberately shut is
    reported as a plain down port, i.e. as a fault.
    """
    output = (
        "$ display interface brief\n"
        "Brief information on interfaces in route mode:\n"
        "Link: ADM - administratively down; Stby - standby\n"
        "Protocol: (s) - spoofing\n"
        "Interface            Link Protocol Primary IP      Description\n"
        "GE0/0                ADM  DOWN     --              ### NOT USED ###\n"
        "GE0/1                UP   UP       5.6.7.8         ### WAN ###\n"
        "GE0/2                ADM  DOWN     --              ### NOT USED ###\n"
    )

    metrics = parse_ssh_metrics("H3C", output)

    assert metrics["interfaces_total"] == 3
    assert metrics["interfaces_up"] == 1
    assert metrics["interfaces_admin_down"] == 2


# Verbatim excerpt of real CE12808S output published in the ntc-templates test
# corpus. Every card is Normal; the only line containing the words "Card" and
# "Alarm" is the column header.
HUAWEI_DISPLAY_DEVICE = """$ display device
CE12808S's Device status:
-------------------------------------------------------------------------------
Slot  Card   Type                     Online   Power Register     Alarm     Primary
-------------------------------------------------------------------------------
3     -      CE-L24LQ-EC1             Present  On    Registered   Normal    NA
9     -      CE-MPUA-S                Present  On    Registered   Normal    Master
PWR1  -      PHD-3000WA               Present  On    Registered   Normal    NA
FAN1  -      FAN-4C-B                 Present  On    Registered   Normal    NA
"""


def test_huawei_display_device_header_is_not_counted_as_a_faulty_board():
    """The reported bug: a healthy chassis reported an abnormal board.

    BOARD_ROW matched lines starting with "Slot"/"Card", which on a real device
    is the column header, not a card. The header also contains the word
    "Alarm", so it was classified as abnormal: a chassis whose cards are all
    Normal was reported as 1 board, 1 abnormal.
    """
    boards = parse_ssh_metrics("Huawei", HUAWEI_DISPLAY_DEVICE)["boards"]

    assert boards["abnormal"] == 0, "no card in this output is faulty"
    assert boards["total"] == 2, "two cards in slots 3 and 9"


def test_huawei_display_device_counts_power_supplies_and_fans():
    """PWR/FAN rows are real hardware and must be reported separately."""
    metrics = parse_ssh_metrics("Huawei", HUAWEI_DISPLAY_DEVICE)

    assert metrics["power_supplies"]["total"] == 1
    assert metrics["power_supplies"]["abnormal"] == 0
    assert metrics["fans"]["total"] == 1
    assert metrics["fans"]["abnormal"] == 0


def test_huawei_hardware_keeps_component_identity_model_and_status():
    metrics = parse_ssh_metrics("Huawei", HUAWEI_DISPLAY_DEVICE)

    assert metrics["boards"]["components"][0] == {
        "id": "3",
        "model": "CE-L24LQ-EC1",
        "status": "normal",
        "summary": "3     -      CE-L24LQ-EC1             Present  On    Registered   Normal    NA",
    }
    assert metrics["power_supplies"]["components"][0]["id"] == "PWR1"
    assert metrics["fans"]["components"][0]["status"] == "normal"


# Verbatim excerpt of real VRP output from the ntc-templates test corpus.
# The word "Temp" appears only in the column header; the readings themselves
# are identified by their status field.
HUAWEI_DISPLAY_TEMPERATURE = """$ display temperature
SlotID2 :
 Base-Board, Unit:C, Slot2
PCB       I2C Addr Chl  Status  Minor Major Fatal Adj_speed Temp
                                               TMin Tmax (C)
---------------------------------------------------------------
LPUF41A   0   0    0    NORMAL  68    76    83    55   65   38
LPUF41A   0   1    0    NORMAL  63    71    78    50   60   36
EFGF      2   73   0    NORMAL  77    83    92    64   74   30

SlotID31 : No temp sensors.
"""


def test_huawei_temperature_counts_sensor_rows_not_the_column_header():
    """The reported bug: a chassis with many sensors reported only a few.

    TEMP_ROW matched any line containing "Temp", which on a real device is the
    column header and the "No temp sensors." notice, while the readings
    themselves never matched. The result was a sensor count unrelated to the
    hardware.
    """
    temperature = parse_ssh_metrics("Huawei", HUAWEI_DISPLAY_TEMPERATURE)["temperature"]

    assert temperature["sensors"] == 3, "three NORMAL readings in this output"
    assert temperature["alarming"] == 0


def test_huawei_temperature_flags_an_alarming_sensor():
    """A sensor above its major threshold must be reported as alarming."""
    output = HUAWEI_DISPLAY_TEMPERATURE.replace(
        "EFGF      2   73   0    NORMAL  77    83    92    64   74   30",
        "EFGF      2   73   0    MAJOR   77    83    92    64   74   88",
    )

    temperature = parse_ssh_metrics("Huawei", output)["temperature"]

    assert temperature["sensors"] == 3
    assert temperature["alarming"] == 1


def test_huawei_temperature_keeps_sensor_values_and_device_thresholds():
    temperature = parse_ssh_metrics("Huawei", HUAWEI_DISPLAY_TEMPERATURE)["temperature"]

    assert temperature["max_current"] == 38
    assert temperature["readings"][0] == {
        "name": "LPUF41A/0/0/0",
        "current": 38,
        "unit": "C",
        "status": "normal",
        "thresholds": {"minor": 68, "major": 76, "fatal": 83},
    }


def test_alarm_output_is_a_separate_check_and_does_not_fake_temperature_readings():
    raw = (
        "$ display alarm active\n"
        "1/Independent/2026-08-12 10:00:00+08:00/-/0x1/hwFanFail/Major/Start/Fan stopped.\n"
        "2/Independent/2026-08-12 10:01:00+08:00/-/0x2/hwPowerRemove/Warning/Start/Power absent.\n"
    )
    parsed = parse_ssh_output(
        "Huawei",
        raw,
        {"display alarm active": {"active_alarms", "temperature"}},
    )

    assert parsed.metrics["active_alarms"]["total"] == 2
    assert parsed.metrics["active_alarms"]["critical"] == 0
    assert parsed.metrics["active_alarms"]["warning"] == 2
    assert parsed.metrics["active_alarms"]["alarms"][0]["name"] == "hwFanFail"
    assert "temperature" not in parsed.metrics


def test_huawei_alarm_headers_without_alarm_rows_report_zero_alarms():
    raw = (
        "$ display alarm active\n"
        "A/B/C/D/E/F/G/H/I/J\n"
        "A=Sequence, B=RootKindFlag\n"
        "I=Description information for locating, active alarm details\n"
    )

    parsed = parse_ssh_output(
        "Huawei",
        raw,
        {"display alarm active": {"active_alarms"}},
    )

    assert parsed.metrics["active_alarms"] == {
        "total": 0, "critical": 0, "warning": 0, "alarms": [],
    }


def test_device_explicitly_reporting_no_active_alarms_is_normal_evidence():
    parsed = parse_ssh_output(
        "Cisco",
        "$ show facility-alarm status\nNo active alarms found.\n",
        {"show facility-alarm status": {"active_alarms"}},
    )

    assert parsed.metrics["active_alarms"] == {
        "total": 0, "critical": 0, "warning": 0, "alarms": [],
    }


def test_alarm_heading_without_rows_or_explicit_empty_message_is_not_normal_evidence():
    parsed = parse_ssh_output(
        "Ruijie",
        "$ show alarm\nAlarm Information\n-----------------\n",
        {"show alarm": {"active_alarms"}},
    )

    assert "active_alarms" not in parsed.metrics


def test_unregistered_hardware_is_not_mistaken_for_registered_normal_hardware():
    metrics = parse_ssh_metrics(
        "Huawei",
        "$ display device\nSlot 1  CE6865  Present  On  Unregistered  Unknown\n",
    )

    assert metrics["boards"]["components"][0]["status"] == "unknown"


def test_storage_keeps_each_cf_flash_filesystem_and_capacity():
    raw = (
        "$ show file systems\n"
        "      Size(b)     Free(b)      Type  Flags  Prefixes\n"
        "*   256487136    64121784     flash    rw   flash:\n"
        "    134217728    33554432     flash    ro   bootflash:\n"
    )

    storage = parse_ssh_metrics("Cisco", raw)["storage"]

    assert storage["used_percent"] == 75
    assert storage["filesystems"] == [
        {
            "name": "flash:", "media_type": "flash", "total_bytes": 256487136,
            "free_bytes": 64121784, "used_percent": 75, "access": "rw", "status": "normal",
        },
        {
            "name": "bootflash:", "media_type": "bootflash", "total_bytes": 134217728,
            "free_bytes": 33554432, "used_percent": 75, "access": "ro", "status": "read_only",
        },
    ]


def test_huawei_dir_identifies_hda_or_cf_storage_media():
    raw = (
        "$ dir\n"
        "Directory of hda1:/\n"
        "2,048,000 KB total (1,024,000 KB free)\n"
    )

    storage = parse_ssh_metrics("Huawei", raw)["storage"]

    assert storage["filesystems"][0]["name"] == "hda1:"
    assert storage["filesystems"][0]["media_type"] == "hda"
    assert storage["filesystems"][0]["used_percent"] == 50


def test_storage_from_multiple_commands_keeps_the_most_used_medium():
    parsed = parse_ssh_output(
        "Huawei",
        (
            "$ display storage\n"
            "1024000 819200 flash rw flash:\n"
            "$ dir\n"
            "Directory of cfcard:/\n"
            "1,000 MB total (10 MB free)\n"
        ),
        {"display storage": {"storage"}, "dir": {"storage"}},
    )

    assert parsed.metrics["storage"]["used_percent"] == 99
    assert {item["name"] for item in parsed.metrics["storage"]["filesystems"]} == {
        "flash:", "cfcard:",
    }
