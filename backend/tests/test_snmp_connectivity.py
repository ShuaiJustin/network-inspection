import asyncio

import pytest

from app.models.device import Device
from app.services import collectors


class _NumericOid:
    def __init__(self, oid):
        self.oid = tuple(int(component) for component in oid.split("."))

    def asTuple(self):
        return self.oid


class _IdentityLikeOid:
    def __init__(self, numeric_oid, symbolic_oid):
        self.numeric_oid = numeric_oid
        self.symbolic_oid = symbolic_oid

    def getOid(self):
        return _NumericOid(self.numeric_oid)

    def __str__(self):
        return self.symbolic_oid


def test_snmp_connectivity_checks_system_and_interface_mib_access(monkeypatch):
    """A successful probe must verify the IF-MIB required by inspection."""
    device = Device(
        ip="10.99.80.10",
        vendor="Huawei",
        device_type="firewall",
        snmp_version="v2c",
        snmp_community="encrypted-community",
    )
    observed: dict[str, int] = {}

    class FakeTransport:
        @staticmethod
        async def create(*_args, **_kwargs):
            return object()

    async def fake_get_cmd(*args):
        observed["oid_count"] = len(args[4:])
        return None, None, None, [
            (collectors.SYSTEM_OIDS["sys_name"], _PrettyValue("core")),
            (collectors.SYSTEM_OIDS["interface_count"], _PrettyValue("48")),
        ]

    monkeypatch.setattr(collectors, "UdpTransportTarget", FakeTransport)
    monkeypatch.setattr(collectors, "get_cmd", fake_get_cmd)
    monkeypatch.setattr(collectors, "decrypt_secret", lambda _value: "readonly-community")

    result = asyncio.run(collectors.test_snmp_connectivity(device, timeout=1, retries=0))

    assert result.ok is True
    assert observed["oid_count"] == 2


def test_snmp_connectivity_normalizes_identity_like_oids_to_numeric_form(monkeypatch):
    device = Device(
        ip="10.99.80.18",
        vendor="Huawei",
        device_type="switch",
        snmp_version="v2c",
        snmp_community="encrypted-community",
    )

    class FakeTransport:
        @staticmethod
        async def create(*_args, **_kwargs):
            return object()

    async def fake_get_cmd(*_args):
        return None, None, None, [
            (
                _IdentityLikeOid(
                    collectors.SYSTEM_OIDS["sys_name"],
                    "SNMPv2-MIB::sysName.0",
                ),
                _PrettyValue("core"),
            ),
            (
                _IdentityLikeOid(
                    collectors.SYSTEM_OIDS["interface_count"],
                    "IF-MIB::ifNumber.0",
                ),
                _PrettyValue("48"),
            ),
        ]

    monkeypatch.setattr(collectors, "UdpTransportTarget", FakeTransport)
    monkeypatch.setattr(collectors, "get_cmd", fake_get_cmd)
    monkeypatch.setattr(collectors, "decrypt_secret", lambda _value: "readonly-community")

    result = asyncio.run(collectors.test_snmp_connectivity(device, timeout=1, retries=0))

    assert result.ok is True


def test_snmp_connectivity_accepts_at_least_one_usable_value_for_each_probe_oid(monkeypatch):
    device = Device(
        ip="10.99.80.17",
        vendor="Huawei",
        device_type="switch",
        snmp_version="v2c",
        snmp_community="encrypted-community",
    )

    class FakeTransport:
        @staticmethod
        async def create(*_args, **_kwargs):
            return object()

    async def fake_get_cmd(*_args):
        return None, None, None, [
            (collectors.SYSTEM_OIDS["sys_name"], _PrettyValue("core")),
            (
                collectors.SYSTEM_OIDS["sys_name"],
                _PrettyValue("No Such Object available on this agent"),
            ),
            (collectors.SYSTEM_OIDS["interface_count"], _PrettyValue("48")),
            (
                collectors.SYSTEM_OIDS["interface_count"],
                _PrettyValue("No Such Instance currently exists at this OID"),
            ),
        ]

    monkeypatch.setattr(collectors, "UdpTransportTarget", FakeTransport)
    monkeypatch.setattr(collectors, "get_cmd", fake_get_cmd)
    monkeypatch.setattr(collectors, "decrypt_secret", lambda _value: "readonly-community")

    result = asyncio.run(collectors.test_snmp_connectivity(device, timeout=1, retries=0))

    assert result.ok is True


@pytest.mark.parametrize(
    "var_binds",
    [
        [],
        [(collectors.SYSTEM_OIDS["sys_name"], "core")],
        [(collectors.SYSTEM_OIDS["interface_count"], "48")],
        [
            (collectors.SYSTEM_OIDS["sys_name"], "No Such Object available on this agent"),
            (collectors.SYSTEM_OIDS["interface_count"], "48"),
        ],
        [
            (collectors.SYSTEM_OIDS["sys_name"], "core"),
            (collectors.SYSTEM_OIDS["interface_count"], "No Such Instance currently exists at this OID"),
        ],
    ],
    ids=["empty", "missing-ifnumber", "missing-sysname", "no-such-sysname", "no-such-ifnumber"],
)
def test_snmp_connectivity_rejects_an_incomplete_or_unusable_probe(monkeypatch, var_binds):
    device = Device(
        ip="10.99.80.19",
        vendor="Huawei",
        device_type="switch",
        snmp_version="v2c",
        snmp_community="encrypted-community",
    )

    class FakeTransport:
        @staticmethod
        async def create(*_args, **_kwargs):
            return object()

    async def fake_get_cmd(*_args):
        return None, None, None, [
            (collectors.SYSTEM_OIDS.get(oid, oid), _PrettyValue(value)) for oid, value in var_binds
        ]

    monkeypatch.setattr(collectors, "UdpTransportTarget", FakeTransport)
    monkeypatch.setattr(collectors, "get_cmd", fake_get_cmd)
    monkeypatch.setattr(collectors, "decrypt_secret", lambda _value: "readonly-community")

    result = asyncio.run(collectors.test_snmp_connectivity(device, timeout=1, retries=0))

    assert result.ok is False
    assert result.failure_stage == "collection"
    assert "sysName" in (result.message or "")
    assert "ifNumber" in (result.message or "")


def test_pysnmp_authentication_errors_are_not_reported_as_timeouts():
    """SNMPv3 credential problems must be told apart from an unreachable device.

    pysnmp surfaces USM failures through its own errind objects whose wording
    shares no keyword with the previous substring list, so every credential
    error used to reach the operator as "No SNMP response received before
    timeout" and sent them looking for a network fault instead of the missing
    user or the wrong password.
    """
    from pysnmp.proto import errind

    credential_failures = [
        errind.unknownUserName,
        errind.wrongDigest,
        errind.decryptionError,
        errind.unknownSecurityName,
        errind.unsupportedSecurityLevel,
        errind.notInTimeWindow,
        errind.authenticationFailure,
        errind.unknownCommunityName,
    ]
    for indication in credential_failures:
        assert collectors._snmp_error_is_authentication(str(indication)) is True, indication

    assert collectors._snmp_error_is_authentication(str(errind.requestTimedOut)) is False


def test_unknown_snmp_v3_user_is_reported_as_authentication_failure(monkeypatch):
    """The reported bug: a switch without the configured SNMPv3 user.

    The device answers immediately with an unknownUserName report, so the
    result must name the credential problem rather than claim a timeout.
    """
    device = Device(
        ip="172.16.51.2",
        vendor="Huawei",
        device_type="switch",
        snmp_version="v3",
        snmp_v3_username="admin",
        snmp_v3_security_level="authPriv",
        snmp_v3_auth_protocol="SHA",
        snmp_v3_priv_protocol="AES",
        snmp_v3_auth_key="encrypted-auth",
        snmp_v3_priv_key="encrypted-priv",
    )

    class FakeTransport:
        @staticmethod
        async def create(*_args, **_kwargs):
            return object()

    from pysnmp.proto import errind

    async def fake_get_cmd(*_args):
        return errind.unknownUserName, None, None, []

    monkeypatch.setattr(collectors, "UdpTransportTarget", FakeTransport)
    monkeypatch.setattr(collectors, "get_cmd", fake_get_cmd)
    monkeypatch.setattr(collectors, "decrypt_secret", lambda _value: "secret")

    result = asyncio.run(collectors.test_snmp_connectivity(device, timeout=1, retries=0))

    assert result.ok is False
    assert result.failure_stage == "authentication"
    assert result.authentication_failed is True
def test_a_genuine_timeout_is_still_reported_as_a_connection_failure(monkeypatch):
    """An unreachable device must keep reporting a timeout, not a credential error."""
    from pysnmp.proto import errind

    device = Device(
        ip="10.99.80.11",
        vendor="Huawei",
        device_type="switch",
        snmp_version="v2c",
        snmp_community="encrypted-community",
    )
    retry_budgets: list[int] = []

    class FakeTransport:
        @staticmethod
        async def create(*_args, **kwargs):
            retry_budgets.append(kwargs.get("retries"))
            return object()

    async def fake_get_cmd(*_args):
        return errind.requestTimedOut, None, None, []

    monkeypatch.setattr(collectors, "UdpTransportTarget", FakeTransport)
    monkeypatch.setattr(collectors, "get_cmd", fake_get_cmd)
    monkeypatch.setattr(collectors, "decrypt_secret", lambda _value: "community")

    result = asyncio.run(collectors.test_snmp_connectivity(device, timeout=1, retries=1))

    assert result.ok is False
    assert result.failure_stage == "connection"
    assert result.authentication_failed is False


def test_credential_probe_does_not_retransmit_over_the_device_reply(monkeypatch):
    """The reported bug: a switch reporting a missing SNMPv3 user as a timeout.

    Huawei devices answer the very first exchange with a USM report and then
    rate limit the source, staying silent for roughly a minute. pysnmp keeps
    only the outcome of its last retransmission, so that single report was
    overwritten by "No SNMP response received before timeout" and pointed the
    operator at the network instead of the missing SNMPv3 user.

    Measured on a live Huawei S5720: the first probe returned "Unknown USM
    user" in 0.3s, every probe for the next 60s timed out, and unauthenticated
    traffic recovered to 30/30 only after the rate limit expired. Retrying is
    therefore useless for credential errors and destroys the only reply the
    device sends, so the probe must ask pysnmp not to retransmit.
    """
    from pysnmp.proto import errind

    device = Device(
        ip="172.16.51.2",
        vendor="Huawei",
        device_type="switch",
        snmp_version="v3",
        snmp_v3_username="admin",
        snmp_v3_security_level="authPriv",
        snmp_v3_auth_protocol="SHA",
        snmp_v3_priv_protocol="AES",
        snmp_v3_auth_key="encrypted-auth",
        snmp_v3_priv_key="encrypted-priv",
    )
    retry_budgets: list[int] = []

    class FakeTransport:
        @staticmethod
        async def create(*_args, **kwargs):
            retry_budgets.append(kwargs.get("retries"))
            return object()

    async def fake_get_cmd(*_args):
        # The USM report only survives when pysnmp does not retransmit.
        if retry_budgets[-1]:
            return errind.requestTimedOut, None, None, []
        return errind.unknownUserName, None, None, []

    monkeypatch.setattr(collectors, "UdpTransportTarget", FakeTransport)
    monkeypatch.setattr(collectors, "get_cmd", fake_get_cmd)
    monkeypatch.setattr(collectors, "decrypt_secret", lambda _value: "secret")

    result = asyncio.run(collectors.test_snmp_connectivity(device, timeout=1, retries=1))

    assert retry_budgets == [0], "the probe must not retransmit over the device reply"
    assert result.failure_stage == "authentication"
    assert result.authentication_failed is True
    assert "Unknown USM user" in (result.message or "")


def test_inspection_collection_keeps_the_credential_reason(monkeypatch):
    """Device rows must name the credential error, like the probe does."""
    from pysnmp.proto import errind

    device = Device(
        ip="172.16.51.2",
        vendor="Huawei",
        device_type="switch",
        snmp_version="v3",
        snmp_v3_username="admin",
        snmp_v3_security_level="authPriv",
        snmp_v3_auth_protocol="SHA",
        snmp_v3_priv_protocol="AES",
        snmp_v3_auth_key="encrypted-auth",
        snmp_v3_priv_key="encrypted-priv",
    )
    retry_budgets: list[int] = []

    class FakeTransport:
        @staticmethod
        async def create(*_args, **kwargs):
            retry_budgets.append(kwargs.get("retries"))
            return object()

    async def fake_get_cmd(*_args):
        if retry_budgets[-1]:
            return errind.requestTimedOut, None, None, []
        return errind.wrongDigest, None, None, []

    monkeypatch.setattr(collectors, "UdpTransportTarget", FakeTransport)
    monkeypatch.setattr(collectors, "get_cmd", fake_get_cmd)
    monkeypatch.setattr(collectors, "decrypt_secret", lambda _value: "secret")

    result = asyncio.run(collectors.collect_snmp(device, timeout=1, retries=1))

    assert retry_budgets == [0]
    assert result.failure_stage == "authentication"
    assert result.authentication_failed is True
    assert "Wrong SNMP PDU digest" in (result.message or "")


def test_successful_collection_reaches_the_interface_walk(monkeypatch):
    """Regression: the credential re-probe must not break a healthy collection.

    Scoping the transport inside the probe helper left the interface walk
    without one, so every device that answered correctly failed late with
    "NameError: name 'transport' is not defined". The credential tests never
    caught it because they all return before the walk.
    """
    device = Device(
        ip="172.16.51.2",
        vendor="Huawei",
        device_type="switch",
        snmp_version="v2c",
        snmp_community="encrypted-community",
    )
    walked: dict[str, object] = {}

    class FakeTransport:
        @staticmethod
        async def create(*_args, **_kwargs):
            return "udp-transport"

    async def fake_get_cmd(*args):
        var_binds = [(collectors.SYSTEM_OIDS.get(oid, oid), _PrettyValue(value)) for oid, value in (
            ("sys_name", "core-switch"),
            ("sys_description", "S5720-32X-EI-AC"),
            ("uptime_ticks", "123456789"),
            ("interface_count", "48"),
        )]
        return None, None, None, var_binds

    async def fake_interface_metrics(_engine, _auth, transport):
        walked["transport"] = transport
        return {"interfaces_up": 40, "interfaces_down": 8}

    monkeypatch.setattr(collectors, "UdpTransportTarget", FakeTransport)
    monkeypatch.setattr(collectors, "get_cmd", fake_get_cmd)
    monkeypatch.setattr(collectors, "_collect_interface_metrics", fake_interface_metrics)
    monkeypatch.setattr(collectors, "decrypt_secret", lambda _value: "community")

    result = asyncio.run(collectors.collect_snmp(device, timeout=1, retries=1))

    assert result.ok is True, result.message
    assert walked["transport"] == "udp-transport", "the walk needs the probe transport"
    assert result.metrics["hostname"] == "core-switch"
    assert result.metrics["interfaces_total"] == 48


def test_successful_collection_applies_retries_once_above_transport(monkeypatch):
    device = Device(
        ip="172.16.51.3",
        vendor="Huawei",
        device_type="switch",
        snmp_version="v2c",
        snmp_community="encrypted-community",
    )
    retry_budgets: list[int] = []
    walked: dict[str, object] = {}

    class FakeTransport:
        @staticmethod
        async def create(*_args, **kwargs):
            retry_budgets.append(kwargs["retries"])
            return f"udp-transport-{kwargs['retries']}"

    async def fake_get_cmd(*_args):
        return None, None, None, [
            (collectors.SYSTEM_OIDS.get(oid, oid), _PrettyValue(value))
            for oid, value in (
                ("sys_name", "core-switch"),
                ("sys_description", "S5720-32X-EI-AC"),
                ("uptime_ticks", "123456789"),
                ("interface_count", "48"),
            )
        ]

    async def fake_interface_metrics(_engine, _auth, transport):
        walked["transport"] = transport
        walked["retry_budget"] = collectors._ACTIVE_SNMP_RETRY_BUDGET.get()
        return {"interfaces_up": 40, "interfaces_down": 8}

    async def fake_usage_metrics(_engine, _auth, _transport, _vendor, platform_id=None):
        walked["platform_id"] = platform_id
        return {"cpu": None, "memory": None}

    monkeypatch.setattr(collectors, "UdpTransportTarget", FakeTransport)
    monkeypatch.setattr(collectors, "get_cmd", fake_get_cmd)
    monkeypatch.setattr(collectors, "_collect_interface_metrics", fake_interface_metrics)
    monkeypatch.setattr(collectors, "_collect_usage_metrics", fake_usage_metrics)
    monkeypatch.setattr(collectors, "decrypt_secret", lambda _value: "community")

    result = asyncio.run(collectors.collect_snmp(device, timeout=1, retries=3))

    assert result.ok is True
    assert retry_budgets == [0]
    assert walked["transport"] == "udp-transport-0"
    assert walked["retry_budget"] == 3
    assert walked["platform_id"] == "huawei-vrp"


def test_invalid_ifnumber_keeps_system_information_and_uses_interface_walk(monkeypatch):
    """A restricted scalar must not discard system values the device returned."""
    device = Device(
        ip="10.99.80.12",
        vendor="Huawei",
        device_type="switch",
        snmp_version="v2c",
        snmp_community="encrypted-community",
    )

    class FakeTransport:
        @staticmethod
        async def create(*_args, **_kwargs):
            return "udp-transport"

    async def fake_get_cmd(*_args):
        return None, None, None, [
            (collectors.SYSTEM_OIDS.get(oid, oid), _PrettyValue(value))
            for oid, value in (
                ("sys_name", "restricted-switch"),
                ("sys_description", "VRP restricted view"),
                ("uptime_ticks", "1234500"),
                ("interface_count", "No Such Instance currently exists at this OID"),
            )
        ]

    async def fake_interface_metrics(*_args):
        return {
            "interface_collection_status": "success",
            "interfaces": [{"index": 1}, {"index": 2}],
            "interfaces_up": 1,
            "interfaces_down": 1,
        }

    async def no_usage(*_args):
        return {"cpu": None, "memory": None}

    async def no_stp(*_args):
        return {"stp_discarding_ports": None}

    monkeypatch.setattr(collectors, "UdpTransportTarget", FakeTransport)
    monkeypatch.setattr(collectors, "get_cmd", fake_get_cmd)
    monkeypatch.setattr(collectors, "_collect_interface_metrics", fake_interface_metrics)
    monkeypatch.setattr(collectors, "_collect_usage_metrics", no_usage)
    monkeypatch.setattr(collectors, "_collect_stp_metrics", no_stp)
    monkeypatch.setattr(collectors, "decrypt_secret", lambda _value: "community")

    result = asyncio.run(collectors.collect_snmp(device, timeout=1, retries=1))

    assert result.ok is True
    assert result.metrics["hostname"] == "restricted-switch"
    assert result.metrics["model"] == "VRP restricted view"
    assert result.metrics["interfaces_total"] == 2
    assert result.metrics["interface_collection_status"] == "success"


def test_optional_if_mib_column_failure_keeps_interface_overview(monkeypatch):
    calls = []

    async def partially_restricted_walk(_engine, _auth, _transport, oid):
        calls.append(oid)
        if oid == collectors.INTERFACE_OIDS["in_errors"]:
            raise RuntimeError("No SNMP response received before timeout")
        if oid == collectors.INTERFACE_OIDS["name"]:
            return {"1": "GigabitEthernet0/0/1"}
        if oid == collectors.INTERFACE_OIDS["oper_status"]:
            return {"1": "1"}
        return {}

    monkeypatch.setattr(collectors, "_walk_column", partially_restricted_walk)

    metrics = asyncio.run(collectors._collect_interface_metrics(object(), object(), object()))

    assert metrics["interface_collection_status"] == "success"
    assert metrics["interfaces_up"] == 1
    assert metrics["interfaces_down"] == 0
    assert "interface_error_count" not in metrics
    assert metrics["interface_collection_warnings"]["in_errors"] == (
        "No SNMP response received before timeout"
    )


def test_empty_if_mib_error_columns_do_not_fabricate_zero_errors(monkeypatch):
    async def empty_error_columns(_engine, _auth, _transport, oid):
        if oid == collectors.INTERFACE_OIDS["name"]:
            return {"1": "GigabitEthernet0/0/1"}
        if oid == collectors.INTERFACE_OIDS["oper_status"]:
            return {"1": "1"}
        return {}

    monkeypatch.setattr(collectors, "_walk_column", empty_error_columns)

    metrics = asyncio.run(collectors._collect_interface_metrics(object(), object(), object()))

    assert metrics["interface_collection_status"] == "success"
    assert "interface_error_count" not in metrics
    assert set(metrics["interface_collection_warnings"]) >= {
        "in_errors", "out_errors", "in_discards", "out_discards",
    }


def test_vendor_usage_and_stp_timeouts_are_reported_as_collection_failures(monkeypatch):
    async def timeout(*_args):
        raise RuntimeError("No SNMP response received before timeout")

    monkeypatch.setattr(collectors, "_walk_column", timeout)

    usage = asyncio.run(
        collectors._collect_usage_metrics(object(), object(), object(), "Huawei", "huawei-vrp")
    )
    stp = asyncio.run(collectors._collect_stp_metrics(object(), object(), object()))

    assert usage["cpu"] is None
    assert usage["memory"] is None
    assert usage["failed_reasons"] == {
        "cpu": "No SNMP response received before timeout",
        "memory": "No SNMP response received before timeout",
    }
    assert stp["stp_discarding_ports"] is None
    assert stp["failed_reasons"] == {"stp": "No SNMP response received before timeout"}


def test_snmp_result_preserves_usage_and_stp_collection_failures(monkeypatch):
    device = Device(
        ip="10.99.80.14",
        vendor="Huawei",
        device_type="switch",
        snmp_version="v2c",
        snmp_community="encrypted-community",
    )

    class FakeTransport:
        @staticmethod
        async def create(*_args, **_kwargs):
            return "udp-transport"

    async def fake_get_cmd(*_args):
        return None, None, None, [
            (collectors.SYSTEM_OIDS.get(oid, oid), _PrettyValue(value))
            for oid, value in (
                ("sys_name", "partial-switch"),
                ("sys_description", "Huawei VRP"),
                ("uptime_ticks", "1234500"),
                ("interface_count", "1"),
            )
        ]

    async def interface_metrics(*_args):
        return {
            "interface_collection_status": "success",
            "interfaces": [{"index": 1}],
            "interfaces_up": 1,
            "interfaces_down": 0,
            "interface_error_count": 0,
        }

    async def failed_usage(*_args):
        return {
            "cpu": None,
            "memory": None,
            "failed_reasons": {"cpu": "CPU timeout", "memory": "Memory timeout"},
        }

    async def failed_stp(*_args):
        return {
            "stp_discarding_ports": None,
            "failed_reasons": {"stp": "STP timeout"},
        }

    monkeypatch.setattr(collectors, "UdpTransportTarget", FakeTransport)
    monkeypatch.setattr(collectors, "get_cmd", fake_get_cmd)
    monkeypatch.setattr(collectors, "_collect_interface_metrics", interface_metrics)
    monkeypatch.setattr(collectors, "_collect_usage_metrics", failed_usage)
    monkeypatch.setattr(collectors, "_collect_stp_metrics", failed_stp)
    monkeypatch.setattr(collectors, "decrypt_secret", lambda _value: "community")

    result = asyncio.run(collectors.collect_snmp(device, timeout=1, retries=1))

    assert result.ok is True
    assert result.failed_checks == {"cpu", "memory", "stp"}
    assert result.failed_reasons == {
        "cpu": "CPU timeout",
        "memory": "Memory timeout",
        "stp": "STP timeout",
    }


@pytest.mark.parametrize(("warning_key", "item_id", "mib_label"), [
    ("in_discards", "interface_errors", "ifInDiscards"),
    ("admin_status", "interfaces", "ifAdminStatus"),
])
def test_snmp_result_marks_incomplete_interface_columns_as_failed(
    monkeypatch, warning_key, item_id, mib_label,
):
    device = Device(
        ip="10.99.80.20",
        vendor="Huawei",
        device_type="switch",
        snmp_version="v2c",
        snmp_community="encrypted-community",
    )

    class FakeTransport:
        @staticmethod
        async def create(*_args, **_kwargs):
            return "udp-transport"

    async def fake_get_cmd(*_args):
        return None, None, None, [
            (collectors.SYSTEM_OIDS.get(oid, oid), _PrettyValue(value))
            for oid, value in (
                ("sys_name", "partial-switch"),
                ("sys_description", "Huawei VRP"),
                ("uptime_ticks", "1234500"),
                ("interface_count", "1"),
            )
        ]

    async def interface_metrics(*_args):
        return {
            "interface_collection_status": "success",
            "interfaces": [{"index": 1}],
            "interfaces_up": 1,
            "interfaces_down": 0,
            "interface_error_count": 0,
            "interface_collection_warnings": {warning_key: "timeout"},
        }

    async def no_usage(*_args):
        return {"cpu": None, "memory": None, "failed_reasons": {}}

    async def no_stp(*_args):
        return {"stp_discarding_ports": 0, "stp_broken_ports": 0, "failed_reasons": {}}

    monkeypatch.setattr(collectors, "UdpTransportTarget", FakeTransport)
    monkeypatch.setattr(collectors, "get_cmd", fake_get_cmd)
    monkeypatch.setattr(collectors, "_collect_interface_metrics", interface_metrics)
    monkeypatch.setattr(collectors, "_collect_usage_metrics", no_usage)
    monkeypatch.setattr(collectors, "_collect_stp_metrics", no_stp)
    monkeypatch.setattr(collectors, "decrypt_secret", lambda _value: "community")

    result = asyncio.run(collectors.collect_snmp(device, timeout=1, retries=0))

    assert item_id in result.failed_checks
    assert mib_label in result.failed_reasons[item_id]


def test_invalid_uptime_scalar_keeps_other_snmp_values(monkeypatch):
    device = Device(
        ip="10.99.80.13",
        vendor="Huawei",
        device_type="switch",
        snmp_version="v2c",
        snmp_community="encrypted-community",
    )

    class FakeTransport:
        @staticmethod
        async def create(*_args, **_kwargs):
            return "udp-transport"

    async def fake_get_cmd(*_args):
        return None, None, None, [
            (collectors.SYSTEM_OIDS.get(oid, oid), _PrettyValue(value))
            for oid, value in (
                ("sys_name", "partial-switch"),
                ("sys_description", "Huawei VRP"),
                ("uptime_ticks", "No Such Instance currently exists at this OID"),
                ("interface_count", "1"),
            )
        ]

    async def fake_interface_metrics(*_args):
        return {
            "interface_collection_status": "success",
            "interfaces": [{"index": 1}],
            "interfaces_up": 1,
            "interfaces_down": 0,
        }

    async def no_usage(*_args):
        return {"cpu": None, "memory": None}

    async def no_stp(*_args):
        return {"stp_discarding_ports": None}

    monkeypatch.setattr(collectors, "UdpTransportTarget", FakeTransport)
    monkeypatch.setattr(collectors, "get_cmd", fake_get_cmd)
    monkeypatch.setattr(collectors, "_collect_interface_metrics", fake_interface_metrics)
    monkeypatch.setattr(collectors, "_collect_usage_metrics", no_usage)
    monkeypatch.setattr(collectors, "_collect_stp_metrics", no_stp)
    monkeypatch.setattr(collectors, "decrypt_secret", lambda _value: "community")

    result = asyncio.run(collectors.collect_snmp(device, timeout=1, retries=1))

    assert result.ok is True
    assert result.metrics["hostname"] == "partial-switch"
    assert result.metrics["model"] == "Huawei VRP"
    assert "uptime" not in result.metrics
    assert result.failed_checks == {"uptime"}
    assert result.failed_reasons["uptime"] == "SNMP sysUpTime returned an invalid value"


class _PrettyValue:
    def __init__(self, value):
        self._value = value

    def prettyPrint(self):
        return self._value
