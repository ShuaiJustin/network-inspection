"""Firewalls are a first-class device type alongside switches and routers."""

import pytest
from pydantic import ValidationError

from app.schemas.device import DeviceCreate


def _payload(**overrides):
    base = {
        "ip": "10.9.9.9",
        "vendor": "Huawei",
        "device_type": "firewall",
        "snmp_version": "v2c",
        "snmp_community": "public",
    }
    base.update(overrides)
    return base


def test_a_firewall_can_be_created():
    device = DeviceCreate.model_validate(_payload())
    assert device.device_type == "firewall"


@pytest.mark.parametrize("device_type", ["switch", "router", "firewall"])
def test_all_supported_device_types_are_accepted(device_type):
    assert DeviceCreate.model_validate(_payload(device_type=device_type)).device_type == device_type


def test_an_unknown_device_type_is_still_rejected():
    with pytest.raises(ValidationError):
        DeviceCreate.model_validate(_payload(device_type="loadbalancer"))


@pytest.mark.parametrize("vendor", ["Huawei", "H3C", "Ruijie"])
def test_every_vendor_can_have_a_firewall(vendor):
    device = DeviceCreate.model_validate(_payload(vendor=vendor))
    assert device.device_type == "firewall"


def test_cisco_ios_is_not_mislabeled_as_a_firewall_platform():
    with pytest.raises(ValidationError, match="not supported by platform cisco-ios"):
        DeviceCreate.model_validate(_payload(vendor="Cisco"))


def test_default_platform_is_selected_for_a_new_device():
    device = DeviceCreate.model_validate(_payload(vendor="Huawei"))
    assert device.platform_id == "huawei-vrp"


def test_a_platform_from_another_vendor_is_rejected():
    with pytest.raises(ValidationError, match="does not belong"):
        DeviceCreate.model_validate(_payload(vendor="Huawei", platform_id="cisco-ios"))
