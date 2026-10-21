from app.models.device import AppSettings
from app.services.inspection import _item_conclusion


SETTINGS = AppSettings()


def test_faulty_board_is_critical():
    m = {"boards": {"total": 2, "abnormal": 1}}
    assert _item_conclusion("boards", m, SETTINGS) == "critical"


def test_healthy_board_is_normal():
    m = {"boards": {"total": 2, "abnormal": 0}}
    assert _item_conclusion("boards", m, SETTINGS) == "normal"


def test_hardware_with_an_unknown_component_is_not_reported_normal():
    m = {
        "fans": {
            "total": 1,
            "abnormal": 0,
            "components": [{"id": "FAN1", "status": "unknown"}],
        }
    }
    assert _item_conclusion("fans", m, SETTINGS) == "unsupported"


def test_known_hardware_fault_takes_priority_over_an_unknown_component():
    m = {
        "fans": {
            "total": 2,
            "abnormal": 1,
            "components": [
                {"id": "FAN1", "status": "abnormal"},
                {"id": "FAN2", "status": "unknown"},
            ],
        }
    }
    assert _item_conclusion("fans", m, SETTINGS) == "critical"


def test_failed_power_supply_is_critical():
    m = {"power_supplies": {"total": 2, "abnormal": 1}}
    assert _item_conclusion("power_supplies", m, SETTINGS) == "critical"


def test_failed_fan_is_critical():
    m = {"fans": {"total": 4, "abnormal": 2}}
    assert _item_conclusion("fans", m, SETTINGS) == "critical"


def test_temperature_alarm_is_warning_without_fixed_threshold():
    m = {"temperature": {"sensors": 3, "alarming": 1}}
    assert _item_conclusion("temperature", m, SETTINGS) == "warning"


def test_temperature_without_alarm_is_normal():
    m = {"temperature": {"sensors": 3, "alarming": 0}}
    assert _item_conclusion("temperature", m, SETTINGS) == "normal"


def test_temperature_requires_real_sensor_evidence_before_it_is_normal():
    assert _item_conclusion(
        "temperature",
        {"temperature": {"sensors": 0, "alarming": 0, "readings": []}},
        SETTINGS,
    ) == "unsupported"


def test_active_alarm_severity_controls_the_conclusion():
    assert _item_conclusion(
        "active_alarms", {"active_alarms": {"total": 0, "critical": 0, "warning": 0}}, SETTINGS
    ) == "normal"
    assert _item_conclusion(
        "active_alarms", {"active_alarms": {"total": 2, "critical": 0, "warning": 2}}, SETTINGS
    ) == "warning"
    assert _item_conclusion(
        "active_alarms", {"active_alarms": {"total": 1, "critical": 1, "warning": 0}}, SETTINGS
    ) == "critical"


def test_active_alarm_with_unknown_severity_is_not_reported_normal():
    assert _item_conclusion(
        "active_alarms",
        {
            "active_alarms": {
                "total": 1,
                "critical": 0,
                "warning": 0,
                "alarms": [{"name": "device-alarm", "severity": "unknown"}],
            }
        },
        SETTINGS,
    ) == "warning"


def test_degraded_aggregation_is_warning():
    m = {"link_aggregation": {"groups": 2, "degraded": 1}}
    assert _item_conclusion("link_aggregation", m, SETTINGS) == "warning"


def test_neighbor_not_established_is_warning():
    m = {"routing_neighbors": {"total": 2, "established": 1}}
    assert _item_conclusion("routing_neighbors", m, SETTINGS) == "warning"


def test_no_neighbor_protocol_is_not_applicable():
    m = {"routing_neighbors": {"total": 0, "established": 0}}
    assert _item_conclusion("routing_neighbors", m, SETTINGS) == "not_applicable"


def test_ntp_unsynchronized_is_warning():
    assert _item_conclusion("ntp", {"ntp": {"synchronized": False}}, SETTINGS) == "warning"
    assert _item_conclusion("ntp", {"ntp": {"synchronized": True}}, SETTINGS) == "normal"


def test_ntp_not_configured_is_not_applicable():
    assert _item_conclusion(
        "ntp",
        {"ntp": {"configured": False, "synchronized": None}},
        SETTINGS,
    ) == "not_applicable"


def test_storage_uses_configured_thresholds():
    assert _item_conclusion("storage", {"storage": {"used_percent": 70}}, SETTINGS) == "normal"
    assert _item_conclusion("storage", {"storage": {"used_percent": 85}}, SETTINGS) == "warning"
    assert _item_conclusion("storage", {"storage": {"used_percent": 97}}, SETTINGS) == "critical"


def test_read_only_cf_or_flash_storage_is_critical():
    metrics = {
        "storage": {
            "used_percent": 40,
            "filesystems": [{"name": "cfcard:", "access": "ro", "status": "read_only"}],
        }
    }
    assert _item_conclusion("storage", metrics, SETTINGS) == "critical"


def test_capacity_only_storage_does_not_claim_unknown_health_is_normal():
    metrics = {
        "storage": {
            "used_percent": 60,
            "filesystems": [
                {
                    "name": "hda1:",
                    "media_type": "hda",
                    "used_percent": 60,
                    "access": "unknown",
                    "status": "unknown",
                }
            ],
        }
    }

    assert _item_conclusion("storage", metrics, SETTINGS) == "unsupported"


def test_capacity_threshold_still_takes_priority_when_storage_health_is_unknown():
    metrics = {
        "storage": {
            "used_percent": 85,
            "filesystems": [{"name": "hda1:", "access": "unknown", "status": "unknown"}],
        }
    }

    assert _item_conclusion("storage", metrics, SETTINGS) == "warning"


def test_unsaved_configuration_is_warning():
    assert _item_conclusion("configuration_saved", {"configuration_saved": {"saved": False}}, SETTINGS) == "warning"
    assert _item_conclusion("configuration_saved", {"configuration_saved": {"saved": True}}, SETTINGS) == "normal"


def test_interface_errors_single_sample_is_normal():
    # 文档: 单次采样只展示累计值，不判警告
    assert _item_conclusion("interface_errors", {"interface_error_count": 42}, SETTINGS) == "normal"


def test_stp_broken_port_is_critical_but_a_normal_blocking_port_is_not():
    assert _item_conclusion(
        "stp",
        {"stp_discarding_ports": 2, "stp_broken_ports": 0},
        SETTINGS,
    ) == "normal"
    assert _item_conclusion(
        "stp",
        {"stp_discarding_ports": 1, "stp_broken_ports": 1},
        SETTINGS,
    ) == "critical"
