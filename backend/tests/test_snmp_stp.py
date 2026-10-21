import asyncio

from app.models.device import Device
from app.services import collectors


class _Pretty:
    def __init__(self, value):
        self._value = value

    def prettyPrint(self):
        return self._value


def _async(value):
    async def run(*_args, **_kwargs):
        return value
    return run()


def _snmp_device(vendor="Huawei"):
    return Device(
        ip="172.16.51.2", vendor=vendor, device_type="switch",
        snmp_version="v2c", snmp_community="encrypted",
    )


class _FakeTransport:
    @staticmethod
    async def create(*_args, **_kwargs):
        return "transport"


async def _fake_get_cmd(*_args):
    values = [("sys_name", "core"), ("sys_description", "S5720"),
              ("uptime_ticks", "1000"), ("interface_count", "48")]
    return None, None, None, [(collectors.SYSTEM_OIDS.get(oid, oid), _Pretty(value)) for oid, value in values]


def _install(monkeypatch, walk):
    monkeypatch.setattr(collectors, "UdpTransportTarget", _FakeTransport)
    monkeypatch.setattr(collectors, "get_cmd", _fake_get_cmd)
    monkeypatch.setattr(collectors, "_walk_column", walk)
    monkeypatch.setattr(collectors, "_collect_interface_metrics", lambda *_a: _async({}))
    monkeypatch.setattr(collectors, "decrypt_secret", lambda _v: "community")


def test_stp_port_states_come_from_the_standard_bridge_mib(monkeypatch):
    """The reported bug: STP reported as 不支持 on a pure SNMP run.

    collect_snmp never walked any STP OID, so the item fell through to the
    "no verified collector" branch even though switches publish port state in
    the standard BRIDGE-MIB. dot1dStpPortState uses 2=blocking and
    4=forwarding, so blocking ports are the ones that count as discarding,
    matching what the SSH parser reports from "display stp brief".
    """
    async def walk(_engine, _auth, _transport, oid):
        if oid == collectors.STP_PORT_STATE_OID:
            return {"1": "4", "2": "2", "3": "4", "4": "2"}
        return {}

    _install(monkeypatch, walk)

    result = asyncio.run(collectors.collect_snmp(_snmp_device(), timeout=1, retries=1))

    assert result.ok is True, result.message
    assert result.metrics["stp_discarding_ports"] == 2
    assert result.metrics["stp_broken_ports"] == 0


def test_a_device_without_the_bridge_mib_reports_no_stp_reading(monkeypatch):
    """An empty walk means the MIB is absent, not that every port forwards."""
    async def walk(_engine, _auth, _transport, _oid):
        return {}

    _install(monkeypatch, walk)

    result = asyncio.run(collectors.collect_snmp(_snmp_device(), timeout=1, retries=1))

    assert result.metrics["stp_discarding_ports"] is None


def test_firewall_does_not_query_the_bridge_mib(monkeypatch):
    walked_oids = []

    async def walk(_engine, _auth, _transport, oid):
        walked_oids.append(oid)
        return {}

    _install(monkeypatch, walk)
    device = _snmp_device()
    device.device_type = "firewall"

    result = asyncio.run(collectors.collect_snmp(device, timeout=1, retries=1))

    assert result.ok is True
    assert collectors.STP_PORT_STATE_OID not in walked_oids
    assert result.metrics["stp_discarding_ports"] is None


def test_a_fully_forwarding_switch_reports_zero_blocked_ports(monkeypatch):
    """A populated MIB with no blocked port is a real reading of zero."""
    async def walk(_engine, _auth, _transport, oid):
        if oid == collectors.STP_PORT_STATE_OID:
            return {"1": "4", "2": "4"}
        return {}

    _install(monkeypatch, walk)

    result = asyncio.run(collectors.collect_snmp(_snmp_device(), timeout=1, retries=1))

    assert result.metrics["stp_discarding_ports"] == 0


def test_a_dropped_stp_walk_is_retried_before_giving_up(monkeypatch):
    """The STP walk gets the same single retry as the vendor usage walks."""
    attempts = {"stp": 0}

    async def walk(_engine, _auth, _transport, oid):
        if oid == collectors.STP_PORT_STATE_OID:
            attempts["stp"] += 1
            if attempts["stp"] == 1:
                raise RuntimeError("No SNMP response received before timeout")
            return {"1": "2"}
        return {}

    _install(monkeypatch, walk)

    result = asyncio.run(collectors.collect_snmp(_snmp_device(), timeout=1, retries=1))

    assert attempts["stp"] == 2, "a dropped STP walk must be retried once"
    assert result.metrics["stp_discarding_ports"] == 1


def test_stp_is_read_for_every_vendor_not_just_huawei(monkeypatch):
    """BRIDGE-MIB is a standard, so it is not gated on the vendor table."""
    async def walk(_engine, _auth, _transport, oid):
        if oid == collectors.STP_PORT_STATE_OID:
            return {"1": "2"}
        return {}

    _install(monkeypatch, walk)

    result = asyncio.run(collectors.collect_snmp(_snmp_device("Cisco"), timeout=1, retries=1))

    assert result.metrics["stp_discarding_ports"] == 1


def test_snmp_stp_reading_is_attributed_to_the_bridge_mib(monkeypatch):
    """The result table must name the MIB the reading came from.

    Without an explicit reference the item falls back to the generic
    "vendor STP command or MIB" text, which is wrong once the value was read
    over SNMP.
    """
    async def walk(_engine, _auth, _transport, oid):
        if oid == collectors.STP_PORT_STATE_OID:
            return {"1": "2"}
        return {}

    _install(monkeypatch, walk)

    result = asyncio.run(collectors.collect_snmp(_snmp_device(), timeout=1, retries=1))

    assert result.item_references["stp"] == "BRIDGE-MIB::dot1dStpPortState"


def test_no_stp_reference_is_claimed_when_the_mib_is_absent(monkeypatch):
    """A device without BRIDGE-MIB must not be labelled as read from it."""
    async def walk(_engine, _auth, _transport, _oid):
        return {}

    _install(monkeypatch, walk)

    result = asyncio.run(collectors.collect_snmp(_snmp_device(), timeout=1, retries=1))

    assert "stp" not in result.item_references


def test_a_broken_port_is_reported_alongside_blocking_ports(monkeypatch):
    """RFC 1493 defines broken(6) for a malfunctioning port.

    dot1dStpPortState is disabled(1), blocking(2), listening(3), learning(4),
    forwarding(5), broken(6). Only blocking was counted, so a port the bridge
    itself flagged as malfunctioning was reported as healthy: a silent miss of
    the exact fault an inspection is meant to surface.
    """
    async def walk(_engine, _auth, _transport, oid):
        if oid == collectors.STP_PORT_STATE_OID:
            # forwarding, blocking, broken, forwarding
            return {"1": "5", "2": "2", "3": "6", "4": "5"}
        return {}

    _install(monkeypatch, walk)

    result = asyncio.run(collectors.collect_snmp(_snmp_device(), timeout=1, retries=1))

    assert result.metrics["stp_discarding_ports"] == 2
    assert result.metrics["stp_broken_ports"] == 1


def test_forwarding_is_state_five_not_four(monkeypatch):
    """learning(4) is a transient state, forwarding(5) is the healthy one.

    A switch whose ports are all forwarding must report zero blocked ports.
    """
    async def walk(_engine, _auth, _transport, oid):
        if oid == collectors.STP_PORT_STATE_OID:
            return {"1": "5", "2": "5", "3": "5"}
        return {}

    _install(monkeypatch, walk)

    result = asyncio.run(collectors.collect_snmp(_snmp_device(), timeout=1, retries=1))

    assert result.metrics["stp_discarding_ports"] == 0


def test_a_disabled_port_is_not_counted_as_a_fault(monkeypatch):
    """disabled(1) is an administrative choice, not an STP block."""
    async def walk(_engine, _auth, _transport, oid):
        if oid == collectors.STP_PORT_STATE_OID:
            return {"1": "1", "2": "5"}
        return {}

    _install(monkeypatch, walk)

    result = asyncio.run(collectors.collect_snmp(_snmp_device(), timeout=1, retries=1))

    assert result.metrics["stp_discarding_ports"] == 0
