from io import BytesIO


def test_downloading_the_template_and_importing_it_unchanged_creates_a_device(tmp_path):
    """End-to-end: the file the button produces must import as-is.

    Header wording, alias handling and the sample row all have to agree; testing
    them separately would still allow a template that cannot be re-imported.
    """
    from tests.test_inspection_api import _bootstrap_and_login, client

    headers = _bootstrap_and_login()

    template = client.get("/excel/template", headers=headers)
    assert template.status_code == 200

    response = client.post(
        "/excel/import",
        files={"file": (
            "device-import-template.xlsx",
            BytesIO(template.content),
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )},
        headers=headers,
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["imported"] == 1, body
    assert body["errors"] == [], body

    devices = client.get("/devices", headers=headers).json()
    created = next(device for device in devices if device["ip"] == "192.168.1.1")
    assert created["vendor"] == "Huawei"
    assert created["platform_id"] == "huawei-vrp"
    assert created["device_type"] == "switch"
    assert created["name"] == "核心交换机-01"
