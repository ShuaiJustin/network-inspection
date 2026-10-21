import asyncio

from app.models.device import Device
from app.services import collectors


class _PrettyValue:
    def __init__(self, value):
        self.value = value

    def prettyPrint(self):
        return self.value


class _FakeTransport:
    @staticmethod
    async def create(*_args, **_kwargs):
        return "transport"


def _device():
    return Device(
        ip="192.0.2.40",
        vendor="Huawei",
        device_type="switch",
        snmp_version="v2c",
        snmp_community="encrypted-community-token",
    )


def _scalar_var_binds(description="S5720"):
    values = {
        "sys_name": "core-switch",
        "sys_description": description,
        "uptime_ticks": "1234500",
        "interface_count": "48",
    }
    return [
        (oid, _PrettyValue(values[name]))
        for name, oid in collectors.SYSTEM_OIDS.items()
    ]


def _install_probe(monkeypatch, description="S5720"):
    async def fake_get_cmd(*_args):
        return None, None, None, _scalar_var_binds(description)

    monkeypatch.setattr(collectors, "UdpTransportTarget", _FakeTransport)
    monkeypatch.setattr(collectors, "get_cmd", fake_get_cmd)
    monkeypatch.setattr(collectors, "decrypt_secret", lambda _value: "decrypted-community-secret")


def _no_usage(*_args):
    async def result():
        return {"cpu": None, "memory": None, "failed_reasons": {}}

    return result()


def _no_stp(*_args):
    async def result():
        return {
            "stp_discarding_ports": None,
            "stp_broken_ports": None,
            "failed_reasons": {},
        }

    return result()


def test_collect_snmp_preserves_scalar_and_walk_varbind_evidence_without_credentials(monkeypatch):
    _install_probe(monkeypatch)

    async def fake_walk_cmd(*_args, **_kwargs):
        yield None, None, None, [
            ("1.3.6.1.2.1.2.2.1.8.7", _PrettyValue("1")),
        ]

    monkeypatch.setattr(collectors, "bulk_walk_cmd", fake_walk_cmd)

    result = asyncio.run(collectors.collect_snmp(_device(), timeout=1, retries=0))

    assert result.ok is True
    assert "=== SNMP scalar ===" in result.raw_output
    assert "1.3.6.1.2.1.1.5.0 = core-switch" in result.raw_output
    assert "=== SNMP walk 1.3.6.1.2.1.2.2.1.8 ===" in result.raw_output
    assert "1.3.6.1.2.1.2.2.1.8.7 = 1" in result.raw_output
    assert "encrypted-community-token" not in result.raw_output
    assert "decrypted-community-secret" not in result.raw_output
    assert result.raw_output_status == "complete"
    assert result.raw_output_bytes == len(result.raw_output.encode("utf-8"))


def test_snmp_raw_evidence_is_bounded_and_reports_the_full_generated_byte_count(monkeypatch):
    description = "X" * 200
    _install_probe(monkeypatch, description)
    monkeypatch.setattr(collectors, "MAX_DEVICE_RAW_OUTPUT_BYTES", 96)
    monkeypatch.setattr(collectors, "MAX_SINGLE_RAW_OUTPUT_BYTES", 1024)

    async def interface_metrics(*_args):
        return {
            "interface_collection_status": "success",
            "interfaces": [],
            "interfaces_up": 0,
            "interfaces_down": 0,
        }

    monkeypatch.setattr(collectors, "_collect_interface_metrics", interface_metrics)
    monkeypatch.setattr(collectors, "_collect_usage_metrics", _no_usage)
    monkeypatch.setattr(collectors, "_collect_stp_metrics", _no_stp)

    result = asyncio.run(collectors.collect_snmp(_device(), timeout=1, retries=0))

    expected = "=== SNMP scalar ===\n" + "".join(
        f"{oid} = {value.prettyPrint()}\n"
        for oid, value in _scalar_var_binds(description)
    )
    retained = expected.encode("utf-8")[:96].decode("utf-8", errors="ignore")
    assert result.raw_output == retained
    assert result.raw_output_status == "truncated"
    assert result.raw_output_bytes == len(expected.encode("utf-8"))


def test_each_snmp_walk_obeys_the_existing_single_output_limit(monkeypatch):
    _install_probe(monkeypatch)
    monkeypatch.setattr(collectors, "MAX_SINGLE_RAW_OUTPUT_BYTES", 256)
    monkeypatch.setattr(collectors, "MAX_DEVICE_RAW_OUTPUT_BYTES", 4096)
    long_value = "walk-value-" + ("Y" * 400)

    async def fake_walk_cmd(*_args, **_kwargs):
        yield None, None, None, [
            ("1.3.6.1.2.1.2.2.1.8.7", _PrettyValue(long_value)),
        ]

    async def interface_metrics(engine, auth, transport):
        await collectors._walk_column(
            engine,
            auth,
            transport,
            collectors.INTERFACE_OIDS["oper_status"],
        )
        return {
            "interface_collection_status": "success",
            "interfaces": [],
            "interfaces_up": 0,
            "interfaces_down": 0,
        }

    monkeypatch.setattr(collectors, "bulk_walk_cmd", fake_walk_cmd)
    monkeypatch.setattr(collectors, "_collect_interface_metrics", interface_metrics)
    monkeypatch.setattr(collectors, "_collect_usage_metrics", _no_usage)
    monkeypatch.setattr(collectors, "_collect_stp_metrics", _no_stp)

    result = asyncio.run(collectors.collect_snmp(_device(), timeout=1, retries=0))

    assert result.raw_output_status == "truncated"
    assert result.raw_output_bytes > len(result.raw_output.encode("utf-8"))
    assert long_value not in result.raw_output
