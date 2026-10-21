from app.models.device import AppSettings
from app.services.inspection import _item_conclusion, _item_value
from app.services.interface_readings import interface_summary
from app.services.parsers import parse_ssh_metrics, parse_ssh_output
from app.services.vendor_registry import resolve_platform


def _parse(vendor: str, command: str, output: str):
    return parse_ssh_output(vendor, f"$ {command}\n{output}", resolve_platform(vendor).command_items)


def test_disabled_stp_is_not_applicable_instead_of_unreadable():
    for vendor, command, output in (
        ("Huawei", "display stp brief", "Info: The STP is not enabled.\n"),
        ("H3C", "display stp brief", " Protocol Status   : Disabled\n"),
        ("Cisco", "show spanning-tree summary", "No spanning tree instance exists.\n"),
        ("Ruijie", "show spanning-tree", "StpVersion : MSTP\nSysStpStatus : DISABLED\n"),
    ):
        parsed = _parse(vendor, command, output)
        assert parsed.metrics["stp_enabled"] is False, vendor
        assert parsed.item_messages["stp"] == "STP is not enabled"
        assert _item_conclusion("stp", parsed.metrics, AppSettings()) == "not_applicable"
        assert _item_value(parsed.metrics, "stp")["stp_enabled"] is False


def test_stp_port_table_still_wins_over_disabled_wording():
    parsed = _parse(
        "Huawei",
        "display stp brief",
        " MSTID  Port                        Role  STP State     Protection\n"
        "   0    GigabitEthernet0/0/1        DESI  FORWARDING      NONE\n",
    )
    assert parsed.metrics["stp_discarding_ports"] == 0
    assert "stp_enabled" not in parsed.metrics


def test_command_not_supported_error_marks_item_unsupported():
    parsed = _parse("Huawei", "display temperature", "Error: The command is not supported in this view.\n")
    assert "temperature" in parsed.unsupported_checks


def test_h3c_board_roles_count_as_working_cards():
    boards = parse_ssh_metrics(
        "H3C",
        "$ display device\n"
        "Slot Type             State    Subslot  Soft Ver             Patch Ver\n"
        "1    S5130-28S-EI     Master   0        S5130-28S-EI-3208P10 None\n"
        "2    S5130-28S-EI     Standby  0        S5130-28S-EI-3208P10 None\n",
    )["boards"]
    assert boards["total"] == 2
    assert {component["status"] for component in boards["components"]} == {"normal"}


def test_h3c_comware7_fan_blocks_are_parsed_per_irf_member():
    fans = parse_ssh_metrics(
        "H3C",
        "$ display fan\n"
        " Slot 1:\n"
        " Fan 1:\n"
        " State    : Normal\n"
        " Airflow Direction: Port-to-power\n"
        " Slot 2:\n"
        " Fan 1:\n"
        " State    : Absent\n",
    )["fans"]
    assert fans["total"] == 2
    assert fans["abnormal"] == 1
    assert [component["id"] for component in fans["components"]] == ["1/1", "2/1"]


def test_cisco_router_interfaces_come_from_ip_interface_brief():
    summary = interface_summary(
        "$ show interfaces status\n"
        "% Invalid input detected at '^' marker.\n"
        "$ show ip interface brief\n"
        "Interface              IP-Address      OK? Method Status                Protocol\n"
        "GigabitEthernet0/0     10.0.0.1        YES NVRAM  up                    up\n"
        "GigabitEthernet0/1     unassigned      YES NVRAM  administratively down down\n"
        "GigabitEthernet0/2     unassigned      YES unset  down                  down\n"
    )
    assert summary["interfaces_total"] == 3
    assert summary["interfaces_up"] == 1
    assert summary["interfaces_down"] == 2
    assert summary["interfaces_admin_down"] == 1


def test_switch_status_table_is_not_double_counted_with_ip_brief():
    summary = interface_summary(
        "$ show interfaces status\n"
        "Port      Name               Status       Vlan       Duplex  Speed Type\n"
        "Gi1/0/1                      connected    1          a-full a-1000 10/100/1000BaseTX\n"
        "$ show ip interface brief\n"
        "Interface              IP-Address      OK? Method Status                Protocol\n"
        "GigabitEthernet1/0/1   unassigned      YES unset  up                    up\n"
    )
    assert summary["interfaces_total"] == 1
    assert summary["interface_details"][0]["name"] == "Gi1/0/1"


def test_sys_description_yields_model_and_version():
    from app.services.parsers import parse_snmp_identity

    assert parse_snmp_identity(
        "Huawei",
        "S5720-32X-EI-AC\r\nHuawei Versatile Routing Platform Software\r\n"
        "VRP (R) software, Version 5.170 (S5720 V200R011C10SPC600)\r\nCopyright (C) 2000-2018 HUAWEI TECH Co., Ltd",
    ) == {"model": "S5720-32X-EI-AC", "software_version": "V200R011C10SPC600"}
    assert parse_snmp_identity(
        "Huawei", "Huawei Versatile Routing Platform Software\r\nVRP (R) software, Version 8.180 (CE6881 V300R020C10)",
    ) == {"model": "CE6881", "software_version": "V300R020C10"}
    assert parse_snmp_identity(
        "H3C",
        "H3C Comware Platform Software, Software Version 7.1.064, Release 3208P10\r\nH3C S5130-28S-EI\r\n"
        "Copyright (c) 2004-2021 New H3C Technologies Co., Ltd. All rights reserved.",
    ) == {"model": "S5130-28S-EI", "software_version": "7.1.064, Release 3208P10"}
    assert parse_snmp_identity(
        "Ruijie", "Ruijie Full Layer 3 Switch(S5750-28GT4XS-H) By Ruijie Networks RGOS 11.4(1)B12P1",
    ) == {"model": "S5750-28GT4XS-H", "software_version": "11.4(1)B12P1"}
    assert parse_snmp_identity(
        "Cisco",
        "Cisco IOS Software, C2960X Software (C2960X-UNIVERSALK9-M), Version 15.2(7)E2, RELEASE SOFTWARE (fc3)",
    ) == {"software_version": "15.2(7)E2"}
    assert parse_snmp_identity("Huawei", "Huawei VRP") == {}


# Formats below are copied from vendor documentation and public device output.
def test_h3c_comware5_link_aggregation_heading_and_unselected_port():
    from app.services.parsers import parse_ssh_output as parse

    output = (
        "$ display link-aggregation verbose\n"
        "Aggregation ID: 1,  AggregationType: Static,   Loadsharing Type: NonS\n"
        "Local:\n"
        "Port                     Status  Priority  Key    Flag\n"
        "GigabitEthernet1/0/4            S       32768     2      {ACDEFG}\n"
        "GigabitEthernet1/0/5            U       32768     2      {ACG}\n"
        "Remote:\n"
        "Actor                    Partner Priority Key   SystemID              Flag\n"
        "GigabitEthernet1/0/4  0         32768    0     0x8000,0000-0000-0000 {DEF}\n"
    )
    value = parse("H3C", output, {"display link-aggregation verbose": {"link_aggregation"}}).metrics["link_aggregation"]
    assert value["groups"] == 1
    assert value["details"][0]["name"] == "Aggregation 1"
    assert value["details"][0]["down_members"] == ["GigabitEthernet1/0/5"]


def test_h3c_comware7_remote_actor_rows_are_not_members():
    from app.services.parsers import parse_ssh_output as parse

    output = (
        "$ display link-aggregation verbose\n"
        "Aggregate Interface: Bridge-Aggregation10\n"
        "Local:\n"
        "  Port                Status   Priority Index    Oper-Key               Flag\n"
        "  XGE1/0/1            S        32768    61       2                      {ACDEF}\n"
        "  XGE1/0/2            S        32768    62       2                      {ACDEF}\n"
        "Remote:\n"
        "  Actor               Priority Index    Oper-Key SystemID               Flag\n"
        "  XGE1/0/1(R)         32768    111      2        0x8000, 000f-e267-57ad {ACDEF}\n"
    )
    value = parse("H3C", output, {"display link-aggregation verbose": {"link_aggregation"}}).metrics["link_aggregation"]
    assert value == {"groups": 1, "degraded": 0, "details": value["details"]}


def test_cisco_suspended_member_degrades_port_channel():
    from app.services.parsers import parse_ssh_output as parse

    output = (
        "$ show etherchannel summary\n"
        "Group  Port-channel  Protocol    Ports\n"
        "------+-------------+-----------+-----------------------------------------------\n"
        "1      Po1(SU)         LACP        Gi1/0/47(s)     Gi1/0/48(P)\n"
    )
    value = parse("Cisco", output, {"show etherchannel summary": {"link_aggregation"}}).metrics["link_aggregation"]
    assert value["degraded"] == 1
    assert value["details"][0]["down_members"] == ["Gi1/0/47"]


def test_comware5_sys_description_without_comma_before_release():
    from app.services.parsers import parse_snmp_identity

    assert parse_snmp_identity(
        "H3C", "HP Comware Platform Software, Software Version 5.20.99 Release 2215 HP A5120-48G EI Switch",
    )["software_version"] == "5.20.99 Release 2215"


def test_stp_not_configured_wording_is_recognised():
    parsed = _parse("H3C", "display stp brief", " STP is not configured.\n")
    assert parsed.metrics["stp_enabled"] is False


def test_huawei_official_eth_trunk_down_sample():
    # Verbatim from Huawei's "Eth-Trunk fails to forward packets" troubleshooting page.
    value = _parse(
        "Huawei",
        "display eth-trunk",
        "Eth-Trunk1's state information is:\n"
        "WorkingMode: NORMAL         Hash arithmetic: According to flow\n"
        "Least Active-linknumber: 3  Max Bandwidth-affected-linknumber: 16\n"
        "Operate status: down        Number Of Up Ports In Trunk: 0\n"
        "--------------------------------------------------------------------------------\n"
        "PortName                Status      Weight\n"
        "GigabitEthernet0/1/1    Down        1\n"
        "GigabitEthernet0/1/2    Down        1\n"
        "GigabitEthernet0/1/3    Down        1\n",
    ).metrics["link_aggregation"]
    assert value["groups"] == 1
    assert value["degraded"] == 1
    assert value["details"][0]["status"] == "down"
    assert len(value["details"][0]["down_members"]) == 3


def test_huawei_interface_brief_legend_lines_are_not_interfaces():
    # Legend as printed by VRP before the interface table.
    summary = interface_summary(
        "$ display interface brief\n"
        "PHY: Physical\n"
        "*down: administratively down\n"
        "(l): loopback\n"
        "(s): spoofing\n"
        "(b): BFD down\n"
        "(e): ETHOAM down\n"
        "(dl): DLDP down\n"
        "(d): Dampening Suppressed\n"
        "InUti/OutUti: input utility/output utility\n"
        "Interface                   PHY   Protocol InUti OutUti   inErrors  outErrors\n"
        "GigabitEthernet0/0/1        down  down        0%     0%          0          0\n"
        "GigabitEthernet0/0/2        up(dl) down       0%     0%          0          0\n"
    )
    assert summary["interfaces_total"] == 2
    assert summary["interfaces_unknown"] == 0
    assert summary["interfaces_up"] == 1


def test_ruijie_spanning_tree_role_and_state_abbreviations():
    parsed = _parse(
        "Ruijie",
        "show spanning-tree",
        "StpVersion : MSTP\nSysStpStatus : ENABLED\n"
        "Interface        Role Sts Cost       Prio OperEdge Type\n"
        "Gi0/1            Root FWD 20000      128  False    P2p\n"
        "Gi0/2            Altn BLK 20000      128  False    P2p\n",
    )
    assert parsed.metrics["stp_discarding_ports"] == 1
    assert "stp_enabled" not in parsed.metrics
