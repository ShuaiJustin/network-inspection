"""Column labels shared by the device import template and the importer.

The template shipped the raw field names, so an operator opening it saw
"snmp_v3_priv_key" instead of a description of the credential. The importer
maps a header back to its field, so the labels and that mapping have to stay in
one place or a translated template would stop importing.
"""

# Ordered as the template presents them.
TEMPLATE_COLUMNS = [
    ("name", "设备名称"),
    ("ip", "管理 IP"),
    ("vendor", "厂商"),
    ("platform_id", "系统平台"),
    ("device_type", "设备类型"),
    ("group_name", "分组"),
    ("note", "备注"),
    ("ssh_port", "SSH 端口"),
    ("ssh_username", "SSH 用户名"),
    ("ssh_password", "SSH 密码"),
    ("enable_password", "Enable 密码"),
    ("snmp_version", "SNMP 版本"),
    ("snmp_port", "SNMP 端口"),
    ("snmp_community", "SNMP 团体名"),
    ("snmp_v3_username", "SNMPv3 用户名"),
    ("snmp_v3_security_level", "SNMPv3 安全级别"),
    ("snmp_v3_auth_protocol", "SNMPv3 认证协议"),
    ("snmp_v3_auth_key", "SNMPv3 认证密钥"),
    ("snmp_v3_priv_protocol", "SNMPv3 加密协议"),
    ("snmp_v3_priv_key", "SNMPv3 加密密钥"),
]

TEMPLATE_HEADERS = [label for _field, label in TEMPLATE_COLUMNS]

# Both spellings are accepted: sheets exported by earlier versions still carry
# the English field names.
HEADER_TO_FIELD = {label: field for field, label in TEMPLATE_COLUMNS}
HEADER_TO_FIELD.update({field: field for field, _label in TEMPLATE_COLUMNS})


def normalize_import_headers(headers) -> list[str]:
    """Map sheet headers onto field names, leaving unknown columns untouched."""
    normalized = []
    seen: set[str] = set()
    duplicates: list[str] = []
    for header in headers:
        text = str(header or "").strip()
        field = HEADER_TO_FIELD.get(text, text)
        normalized.append(field)
        if field and field in seen and field not in duplicates:
            duplicates.append(field)
        if field:
            seen.add(field)
    if duplicates:
        raise ValueError(f"Duplicate Excel columns: {', '.join(duplicates)}")
    return normalized
