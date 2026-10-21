from openpyxl import load_workbook

from app.services.reports import create_device_template


def _template_sheet():
    return load_workbook(create_device_template()).active


def test_the_import_template_is_labelled_in_chinese():
    """The reported bug: the template ships English field names.

    Every other surface is Chinese, so a template headed "snmp_v3_priv_key"
    leaves the operator guessing which cell holds which credential.
    """
    headers = [cell.value for cell in _template_sheet()[1]]

    assert "设备名称" in headers
    assert "管理 IP" in headers
    assert "厂商" in headers
    assert "系统平台" in headers
    assert "设备类型" in headers
    for header in headers:
        assert header is not None
        # Protocol nouns stay verbatim; the surrounding label must be Chinese.
        # Protocol and CLI keywords stay verbatim: they are what the device
        # documentation and the login prompt actually print.
        prose = header
        for keyword in ("SNMPv3", "SNMP", "SSH", "Enable", "IP"):
            prose = prose.replace(keyword, "")
        assert not any(character.isascii() and character.isalpha() for character in prose), header


def test_the_sample_row_uses_the_same_wording_as_the_device_form():
    """A sample reading "switch" tells the operator to type English."""
    sample = [cell.value for cell in _template_sheet()[2]]

    assert "交换机" in sample, "device type must be shown the way the UI names it"
    assert "华为" in sample or "Huawei" in sample


def test_a_template_filled_in_by_the_operator_still_imports():
    """The Chinese headers must map back to the fields the importer expects."""
    from app.routers.excel import normalize_import_headers

    headers = [cell.value for cell in _template_sheet()[1]]
    normalized = normalize_import_headers(headers)

    assert {"ip", "vendor", "device_type"} <= set(normalized)
    assert "snmp_v3_priv_key" in normalized


def test_a_legacy_english_sheet_is_still_accepted():
    """Operators keep old exports; those must not stop importing."""
    from app.routers.excel import normalize_import_headers

    assert normalize_import_headers(["ip", "vendor", "device_type"]) == ["ip", "vendor", "device_type"]


def test_vendor_and_device_type_accept_the_chinese_wording():
    """The importer must read back exactly what the template asks for."""
    from app.schemas.device import DeviceCreate

    device = DeviceCreate.model_validate({
        "ip": "10.0.0.1", "vendor": "华为", "device_type": "交换机",
        "snmp_version": "v2c", "snmp_community": "public",
    })

    assert device.vendor == "Huawei"
    assert device.platform_id == "huawei-vrp"
    assert device.device_type == "switch"


def test_the_sample_row_imports_without_being_edited():
    """A sample the operator cannot import teaches the wrong format.

    The row filled in snmp_version but left the community empty, so importing
    the untouched template failed with "at least one complete credential set is
    required" before the operator had changed anything.
    """
    from app.schemas.device import DeviceCreate
    from app.services.device_import_columns import normalize_import_headers

    sheet = _template_sheet()
    headers = normalize_import_headers(cell.value for cell in sheet[1])
    row = [cell.value for cell in sheet[2]]
    payload = {headers[index]: value for index, value in enumerate(row) if value not in (None, "")}

    device = DeviceCreate.model_validate(payload)

    assert device.ip == "192.168.1.1"
    assert device.vendor == "Huawei"
    assert device.device_type == "switch"
