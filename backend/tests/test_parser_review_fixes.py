from datetime import datetime, timezone

from app.services import collectors
from app.services.inspection import _item_conclusion
from app.models.device import AppSettings
from app.services.interface_readings import interface_summary
from app.services.parsers import parse_ssh_metrics, parse_ssh_output
from app.services.reports import _failure_stage_text, _inspection_value_text, _local_time
from app.services.vendor_registry import resolve_platform


def test_huawei_interface_brief_state_flags_are_not_unknown_states():
    summary = interface_summary(
        "$ display interface brief\n"
        "Interface                   PHY   Protocol  InUti OutUti   inErrors  outErrors\n"
        "GigabitEthernet0/0/1        up    up           0%     0%          0          0\n"
        "GigabitEthernet0/0/2        down(e) down       0%     0%          0          0\n"
        "GigabitEthernet0/0/3        *down down         0%     0%          0          0\n"
        "GigabitEthernet0/0/4        ^down down         0%     0%          0          0\n"
        "NULL0                       up    up(s)        0%     0%          0          0\n"
    )

    assert summary["interfaces_unknown"] == 0
    assert summary["interfaces_up"] == 2
    assert summary["interfaces_down"] == 3
    assert summary["interfaces_admin_down"] == 1


def test_huawei_switch_device_table_counts_indented_power_and_fan_rows():
    metrics = parse_ssh_metrics(
        "Huawei",
        "$ display device\n"
        "S5720-32X-EI-AC's Device status:\n"
        "Slot  Sub  Type                   Online    Power    Register     Status   Role\n"
        "-------------------------------------------------------------------------------\n"
        "0     -    S5720-32X-EI-AC        Present   PowerOn  Registered   Normal   Master\n"
        "      PWR1 POWER                  Present   PowerOn  Registered   Normal   NA\n"
        "      PWR2 POWER                  Absent    -        -            Abnormal NA\n"
        "      FAN1 FAN                    Present   PowerOn  Registered   Normal   NA\n",
    )

    assert metrics["boards"]["total"] == 1
    assert metrics["power_supplies"]["total"] == 2
    assert metrics["power_supplies"]["abnormal"] == 1
    assert metrics["fans"]["total"] == 1
    assert metrics["fans"]["components"][0]["id"] == "FAN1"


def _link_aggregation(vendor: str, command: str, output: str):
    parsed = parse_ssh_output(vendor, f"$ {command}\n{output}", {command: {"link_aggregation"}})
    return parsed.metrics.get("link_aggregation")


def test_huawei_eth_trunk_state_comes_from_the_block_body():
    value = _link_aggregation(
        "Huawei",
        "display eth-trunk",
        "Eth-Trunk1's state information is:\n"
        "WorkingMode: NORMAL           Hash arithmetic: According to SIP-XOR-DIP\n"
        "Operate status: up            Number Of Up Port In Trunk: 1\n"
        "--------------------------------------------------------------------------------\n"
        "PortName                      Status      Weight\n"
        "GigabitEthernet0/0/1          Up          1\n"
        "GigabitEthernet0/0/2          Down        1\n"
        "\n"
        "Eth-Trunk2's state information is:\n"
        "Operate status: down          Number Of Up Port In Trunk: 0\n",
    )

    assert value["groups"] == 2
    assert value["degraded"] == 2
    assert value["details"][0]["down_members"] == ["GigabitEthernet0/0/2"]
    assert _item_conclusion("link_aggregation", {"link_aggregation": value}, AppSettings()) == "warning"


def test_healthy_huawei_eth_trunk_is_normal():
    value = _link_aggregation(
        "Huawei",
        "display eth-trunk",
        "Eth-Trunk1's state information is:\n"
        "Operate status: up            Number Of Up Port In Trunk: 2\n"
        "PortName                      Status      Weight\n"
        "GigabitEthernet0/0/1          Up          1\n"
        "GigabitEthernet0/0/2          Up          1\n",
    )

    assert value["groups"] == 1
    assert value["degraded"] == 0


def test_cisco_etherchannel_summary_rows_are_recognised():
    value = _link_aggregation(
        "Cisco",
        "show etherchannel summary",
        "Number of channel-groups in use: 2\n"
        "Number of aggregators:           2\n"
        "\n"
        "Group  Port-channel  Protocol    Ports\n"
        "------+-------------+-----------+-----------------------------------------------\n"
        "1      Po1(SU)         LACP      Gi1/0/1(P)  Gi1/0/2(P)\n"
        "2      Po2(SU)         LACP      Gi1/0/3(P)  Gi1/0/4(D)\n",
    )

    assert value["groups"] == 2
    assert value["degraded"] == 1
    assert value["details"][1]["down_members"] == ["Gi1/0/4"]


def test_cisco_without_etherchannels_is_not_applicable():
    value = _link_aggregation(
        "Cisco",
        "show etherchannel summary",
        "Number of channel-groups in use: 0\nNumber of aggregators:           0\n",
    )

    assert value["groups"] == 0
    assert _item_conclusion("link_aggregation", {"link_aggregation": value}, AppSettings()) == "not_applicable"


def test_ruijie_aggregate_port_summary_rows_are_recognised():
    value = _link_aggregation(
        "Ruijie",
        "show aggregateport summary",
        "AggregatePort MaxPorts SwitchPort Mode   Load balance           Ports\n"
        "------------- -------- ---------- ------ ---------------------- -----------\n"
        "Ag1           8        Enabled    ACCESS src-dst-mac            Gi0/1 ,Gi0/2\n",
    )

    assert value["groups"] == 1


def test_h3c_unselected_local_member_degrades_the_group():
    value = _link_aggregation(
        "H3C",
        "display link-aggregation verbose",
        "Aggregate Interface: Bridge-Aggregation1\n"
        "Aggregation Mode: Dynamic\n"
        "Local:\n"
        "  Port             Status  Priority Oper-Key  Flag\n"
        "  GE1/0/1          S       32768    1         {ACDEF}\n"
        "  GE1/0/2          U       32768    1         {AC}\n"
        "Remote:\n"
        "  Actor            Partner Priority Oper-Key  SystemID               Flag\n"
        "  GE1/0/1          1       32768    1         0x8000, 0000-fc00-6505 {ACDEF}\n",
    )

    assert value["groups"] == 1
    assert value["degraded"] == 1
    assert value["details"][0]["down_members"] == ["GE1/0/2"]


def test_report_formats_items_that_used_to_be_raw_json():
    assert _inspection_value_text("link_aggregation", {"groups": 2, "degraded": 0, "details": []}) == "共 2 组，异常 0 组"
    assert _inspection_value_text("routing_neighbors", {"total": 3, "established": 2}) == "共 3 个邻居，已建立 2 个"
    assert _inspection_value_text("ntp", {"synchronized": False}) == "未同步"
    assert _inspection_value_text("ntp", {"configured": False, "synchronized": None}) == "未配置"
    assert _inspection_value_text("configuration_saved", {"saved": True}) == "已保存"


def test_report_translates_failure_stage_and_uses_local_time():
    assert _failure_stage_text("host_fingerprint_untrusted") == "SSH 主机密钥未确认"
    assert _failure_stage_text(None) is None
    stored = datetime(2026, 10, 16, 1, 2, 3)
    expected = stored.replace(tzinfo=timezone.utc).astimezone().replace(tzinfo=None)
    assert _local_time(stored) == expected


def test_later_ssh_read_timeout_keeps_output_already_collected(monkeypatch):
    platform = resolve_platform("Huawei")
    device = type("DeviceStub", (), {
        "ip": "192.0.2.10", "ssh_port": 22, "ssh_username": "admin", "ssh_password": "secret",
        "enable_password": None, "vendor": "Huawei", "platform_id": platform.id,
        "ssh_host_fingerprint": "SHA256:trusted",
    })()
    sent_commands = []

    class Connection:
        def __init__(self, **_config):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

    def send(_connection, command, _read_timeout, _max_bytes):
        sent_commands.append(command)
        if command == "display cpu-usage":
            output = "CPU Usage Stat. Cycle: 60 (Second)\nCPU Usage            : 12% Max: 30%\n"
            return output, len(output)
        raise collectors.ReadTimeout("prompt not received")

    monkeypatch.setattr(collectors, "decrypt_secret", lambda value: value)
    monkeypatch.setattr(
        collectors,
        "_verify_ssh_host_fingerprint",
        lambda *_args: collectors.ProtocolResult(True, "SSH", host_fingerprint="SHA256:trusted"),
    )
    monkeypatch.setattr(collectors, "ConnectHandler", Connection)
    monkeypatch.setattr(collectors, "_send_command_with_output_limit", send)

    result = collectors._collect_ssh_sync(device, "quick", required_checks={"cpu", "memory", "interfaces"})

    assert result.ok is True
    assert result.metrics["cpu"] == 12
    assert "memory" in result.failed_checks
    assert "timed out" in result.failed_reasons["memory"]
    assert sent_commands == ["display cpu-usage", "display memory-usage"]


def test_critical_item_is_not_hidden_by_an_unreadable_supplementary_item():
    from app.models.device import Device, InspectionTask
    from app.services import inspection

    task = InspectionTask(id=1, name="t", inspection_level="full", collection_mode="auto")
    device = Device(id=1, ip="192.0.2.9", vendor="Huawei", platform_id="huawei-vrp", device_type="switch")
    snmp = collectors.ProtocolResult(True, "SNMP", metrics={
        "hostname": "SW", "model": "S5720", "uptime": "1d", "interfaces_total": 1, "cpu": 99, "memory": 10,
        "stp_discarding_ports": 0, "stp_broken_ports": 0, "interface_collection_status": "success",
        "interfaces_up": 1, "interfaces_down": 0, "interfaces_unknown": 0, "interfaces": [],
    }, failed_checks={"memory"}, failed_reasons={"memory": "SNMP value is invalid"})

    result = inspection._success_with_check_items(task, device, "SNMP", snmp, AppSettings(), snmp, None)

    items = result.metrics["inspection_items"]
    assert items["cpu"]["conclusion"] == "critical"
    assert any(item["conclusion"] == "data_fetch_failed" for item in items.values())
    assert result.conclusion == "critical"
