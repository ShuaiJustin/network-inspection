from app.services.reports import (
    INSPECTION_ITEM_TEXT,
    _inspection_reference_text,
    _inspection_reason_text,
    _inspection_source_text,
    _inspection_value_text,
)


def test_every_inspection_item_has_a_chinese_report_label():
    expected = {
        "device_information", "uptime", "cpu", "memory", "interfaces", "stp",
        "active_alarms", "boards", "power_supplies", "fans", "temperature",
        "interface_errors", "link_aggregation", "routing_neighbors", "ntp",
        "storage", "configuration_saved",
    }
    assert set(INSPECTION_ITEM_TEXT) == expected
    assert INSPECTION_ITEM_TEXT["cpu"] == "CPU 利用率"


def test_report_translates_generic_sources_references_and_reasons():
    assert _inspection_source_text("unsupported") == "不支持"
    assert _inspection_source_text("not_applicable") == "不适用"
    assert _inspection_source_text("SNMP+SSH") == "SNMP+SSH"
    assert _inspection_reference_text("vendor CPU metric") == "厂商 CPU 指标"
    assert _inspection_reference_text("IF-MIB::ifNumber/ifXTable") == "IF-MIB::ifNumber/ifXTable"
    assert _inspection_reason_text("SSH output did not contain a supported value") == "SSH 回显中没有可解析的数值"
    assert _inspection_reason_text(
        "Startup configuration file presence does not prove the current configuration is saved"
    ) == "仅检测到启动配置文件，无法确认当前配置是否已保存"
    assert _inspection_reason_text("SSH command failed: RuntimeError: timeout") == "SSH 命令执行失败：RuntimeError: timeout"
    assert _inspection_reason_text(
        "SNMP device collection exceeded elapsed time limit (60s)"
    ) == "SNMP 单设备采集超过总时间上限（60s）"
    assert _inspection_reason_text(
        "SNMP table exceeded row limit (4096)"
    ) == "SNMP 表遍历超过行数上限（4096）"


def test_report_values_include_alarm_and_hardware_details():
    alarm_text = _inspection_value_text("active_alarms", {
        "total": 1,
        "critical": 1,
        "warning": 0,
        "alarms": [{
            "name": "hwPowerRemove",
            "severity": "critical",
            "description": "Power supply removed",
        }],
    })
    assert "hwPowerRemove" in alarm_text
    assert "Power supply removed" in alarm_text

    fan_text = _inspection_value_text("fans", {
        "total": 1,
        "abnormal": 1,
        "components": [{
            "id": "FAN1",
            "model": "FAN-028A",
            "status": "abnormal",
            "alarms": [{"description": "Fan stopped"}],
        }],
    })
    assert "FAN-028A" in fan_text
    assert "Fan stopped" in fan_text


def test_report_values_include_temperature_thresholds_and_storage_capacity():
    temperature_text = _inspection_value_text("temperature", {
        "sensors": 1,
        "alarming": 0,
        "max_current": 38,
        "readings": [{
            "name": "LPU/0",
            "current": 38,
            "status": "normal",
            "thresholds": {"minor": 68, "major": 76, "fatal": 83},
        }],
    })
    assert "68" in temperature_text
    assert "76" in temperature_text
    assert "83" in temperature_text

    storage_text = _inspection_value_text("storage", {
        "used_percent": 75,
        "filesystems": [{
            "name": "flash:",
            "total_bytes": 1073741824,
            "free_bytes": 268435456,
            "used_percent": 75,
            "access": "rw",
            "status": "normal",
        }],
    })
    assert "1 GB" in storage_text
    assert "256 MB" in storage_text


def test_report_omits_unverified_storage_state():
    storage_text = _inspection_value_text("storage", {
        "used_percent": 60,
        "filesystems": [{
            "name": "hda1:",
            "total_bytes": 1172 * 1024 ** 2,
            "free_bytes": 464 * 1024 ** 2,
            "used_percent": 60,
        }],
    })

    assert "已用 60%" in storage_text
    assert "总量" in storage_text
    assert "空闲 464 MB" in storage_text
    assert "unknown" not in storage_text
    assert "未知" not in storage_text


def test_report_includes_compatibility_and_per_entity_diagnostics():
    device_text = _inspection_value_text("device_information", {
        "hostname": "Access-SW",
        "model": "S5735-L24T4S-A1",
        "software_version": "V200R022C00SPC500",
        "adapter_compatibility": {"status": "unverified"},
    })
    assert "型号/版本未验证" in device_text

    cpu_text = _inspection_value_text("cpu", {
        "percent": 99,
        "readings": [
            {"object_id": "slot 1", "percent": 5},
            {"object_id": "slot 2", "percent": 6},
            {"object_id": "slot 3", "percent": 7},
            {"object_id": "system", "scope": "management-plane", "percent": 99},
        ],
    })
    assert "整机/管理平面 99%" in cpu_text

    interfaces_text = _inspection_value_text("interfaces", {
        "interfaces_total": 4,
        "interfaces_up": 1,
        "interfaces_down": 3,
        "interfaces_admin_down": 1,
        "interfaces_error_disabled": 1,
        "interfaces_unknown": 1,
        "interface_details": [{
            "name": "GE1/0/2", "physical_status": "down",
            "admin_down": False, "error_disabled": True,
        }],
    })
    assert "管理关闭 1" in interfaces_text
    assert "错误禁用 1" in interfaces_text
    assert "未知 1" in interfaces_text
    assert "GE1/0/2 错误禁用" in interfaces_text

    errors_text = _inspection_value_text("interface_errors", {
        "total": 123,
        "discarded": 7,
        "interfaces": [
            {"name": "GE1/0/1", "total_errors": 0},
            {"name": "GE1/0/2", "total_errors": 0},
            {"name": "GE1/0/3", "total_errors": 0},
            {"name": "GE1/0/4", "total_errors": 123, "crc_errors": 123},
        ],
    })
    assert "丢弃 7" in errors_text
    assert "GE1/0/4 总错误 123 CRC 123" in errors_text


def test_report_resource_details_lead_with_summary_sampling_window():
    cpu_text = _inspection_value_text("cpu", {
        "percent": 99,
        "readings": [
            {"object_id": "slot:1", "scope": "overall", "window": "5s", "percent": 5},
            {"object_id": "slot:1", "scope": "overall", "window": "1m", "percent": 100},
            {"object_id": "slot:2", "scope": "overall", "window": "5s", "percent": 99},
        ],
    })

    assert cpu_text.startswith("99%；slot:2 99%（5 秒）")


def test_report_prioritizes_and_displays_unqualified_interface_discards():
    text = _inspection_value_text("interface_errors", {
        "total": 0,
        "discarded": 100,
        "interfaces": [
            {"name": "A", "total_errors": 0, "discards": 100},
            {"name": "B", "total_errors": 1},
            {"name": "C", "total_errors": 1},
            {"name": "D", "total_errors": 1},
        ],
    })

    assert text.startswith("总错误 0，丢弃 100；A 总错误 0 丢弃 100")
