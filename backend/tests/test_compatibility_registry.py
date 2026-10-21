from app.services import adapters
from app.services.vendor_registry import PLATFORMS, resolve_platform


def test_adapter_capabilities_are_derived_from_the_platform_registry():
    capabilities = adapters.list_adapter_capabilities()

    assert [item["platform_id"] for item in capabilities] == list(PLATFORMS)
    assert all(item["status"] == "unverified" for item in capabilities)
    assert all(item["protocols"] == ["SNMP", "SSH"] for item in capabilities)
    assert all(item["device_types"] for item in capabilities)
    assert all(item["inspection_items"] for item in capabilities)
    huawei = next(item for item in capabilities if item["platform_id"] == "huawei-vrp")
    assert huawei["vendor"] == "Huawei"
    assert huawei["platform_label"] == "VRP"
    assert huawei["backup_command"] == "display current-configuration"
    assert "cpu" in huawei["inspection_items"]


def test_platforms_declare_safe_fallback_commands_and_zero_policies():
    huawei = resolve_platform("Huawei")
    h3c = resolve_platform("H3C")
    ruijie = resolve_platform("Ruijie")

    huawei_memory = next(spec for spec in huawei.commands if spec.command == "display memory")
    h3c_power = next(spec for spec in h3c.commands if spec.command == "display power")
    h3c_fan = next(spec for spec in h3c.commands if spec.command == "display fan")

    assert huawei_memory.is_fallback is True
    assert huawei_memory.items == {"memory"}
    assert h3c_power.is_fallback is True
    assert h3c_power.items == {"power_supplies"}
    assert h3c_fan.is_fallback is True
    assert h3c_fan.items == {"fans"}
    assert huawei.snmp_usage_zero_policy == "require-active-entity"
    assert h3c.snmp_usage_zero_policy == "require-active-entity"
    assert ruijie.snmp_usage_zero_policy == "accept"


def test_adapter_compatibility_is_unverified_without_identity_or_evidence(monkeypatch):
    monkeypatch.setattr(adapters, "VERIFIED_ADAPTERS", ())

    result = adapters.adapter_compatibility(
        "Huawei", "huawei-vrp", "switch", "S5735-L24T4X-A1", "V200R022C00"
    )

    assert result == {
        "status": "unverified",
        "platform_id": "huawei-vrp",
        "model": "S5735-L24T4X-A1",
        "software_version": "V200R022C00",
        "reason": "Model or software version is not in the verified adapter catalog",
    }


def test_adapter_compatibility_matches_only_a_complete_verified_record(monkeypatch):
    monkeypatch.setattr(adapters, "VERIFIED_ADAPTERS", ({
        "status": "verified",
        "vendor": "Huawei",
        "platform_id": "huawei-vrp",
        "device_type": "switch",
        "model_range": "S5735-*",
        "version_range": "V200R022C00*",
        "inspection_items": ["device_information", "cpu"],
        "adapter_version": "huawei-campus-v1",
        "verification_date": "2026-09-12",
    },))

    verified = adapters.adapter_compatibility(
        "Huawei", "huawei-vrp", "switch", "S5735-L24T4X-A1", "V200R022C00SPC500"
    )
    wrong_version = adapters.adapter_compatibility(
        "Huawei", "huawei-vrp", "switch", "S5735-L24T4X-A1", "V200R021C00"
    )

    assert verified["status"] == "verified"
    assert verified["adapter_version"] == "huawei-campus-v1"
    assert verified["inspection_items"] == ["device_information", "cpu"]
    assert wrong_version["status"] == "unverified"


def test_adapter_compatibility_does_not_abort_results_for_a_legacy_platform():
    result = adapters.adapter_compatibility(
        "Huawei", "retired-vrp", "switch", "S5720", "V200R019"
    )

    assert result == {
        "status": "unverified",
        "platform_id": "retired-vrp",
        "model": "S5720",
        "software_version": "V200R019",
        "reason": "Selected platform is not available in the adapter registry",
    }
