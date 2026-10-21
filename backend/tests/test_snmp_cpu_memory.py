import asyncio

from app.models.device import Device
from app.services import collectors


def test_huawei_cpu_and_memory_come_from_snmp(monkeypatch):
    """The reported bug: CPU and memory reported as 不支持 on a pure SNMP run.

    collect_snmp hard-coded cpu/memory to None, so both items fell through to
    the "no verified collector" branch even though Huawei publishes them in
    HUAWEI-ENTITY-EXTENT-MIB. Measured on the live devices: only the main
    control entity (67108873) reports a non-zero value, the other slots are 0,
    so the reading is the maximum across the column.
    """
    device = Device(
        ip="172.16.51.2", vendor="Huawei", device_type="switch",
        snmp_version="v2c", snmp_community="encrypted",
    )
    walks = {
        collectors.HUAWEI_CPU_OID: {"67108867": "0", "67108869": "0", "67108873": "15"},
        collectors.HUAWEI_MEMORY_OID: {"67108867": "0", "67108869": "0", "67108873": "9"},
    }

    class FakeTransport:
        @staticmethod
        async def create(*_args, **_kwargs):
            return "transport"

    async def fake_get_cmd(*_args):
        values = [("sys_name", "core"), ("sys_description", "S5720"),
                  ("uptime_ticks", "1000"), ("interface_count", "48")]
        return None, None, None, [(collectors.SYSTEM_OIDS.get(oid, oid), _Pretty(value)) for oid, value in values]

    async def fake_walk_column(_engine, _auth, _transport, oid):
        return walks.get(oid, {})

    monkeypatch.setattr(collectors, "UdpTransportTarget", FakeTransport)
    monkeypatch.setattr(collectors, "get_cmd", fake_get_cmd)
    monkeypatch.setattr(collectors, "_walk_column", fake_walk_column)
    monkeypatch.setattr(collectors, "_collect_interface_metrics", lambda *_a: _async({}))
    monkeypatch.setattr(collectors, "decrypt_secret", lambda _v: "community")

    result = asyncio.run(collectors.collect_snmp(device, timeout=1, retries=1))

    assert result.ok is True, result.message
    assert result.metrics["cpu"] == 15
    assert result.metrics["memory"] == 9


def test_a_vendor_without_a_verified_cpu_mib_reports_no_reading(monkeypatch):
    """Only vendors with a verified MIB may claim an SNMP CPU reading."""
    device = Device(
        ip="10.99.80.10", vendor="Cisco", device_type="switch",
        snmp_version="v2c", snmp_community="encrypted",
    )

    class FakeTransport:
        @staticmethod
        async def create(*_args, **_kwargs):
            return "transport"

    async def fake_get_cmd(*_args):
        values = [("sys_name", "edge"), ("sys_description", "IOS"),
                  ("uptime_ticks", "1000"), ("interface_count", "24")]
        return None, None, None, [(collectors.SYSTEM_OIDS.get(oid, oid), _Pretty(value)) for oid, value in values]

    async def fail_walk(*_args):
        raise AssertionError("must not walk a vendor MIB that was never verified")

    monkeypatch.setattr(collectors, "UdpTransportTarget", FakeTransport)
    monkeypatch.setattr(collectors, "get_cmd", fake_get_cmd)
    monkeypatch.setattr(collectors, "_walk_column", fail_walk)
    monkeypatch.setattr(collectors, "_collect_interface_metrics", lambda *_a: _async({}))
    monkeypatch.setattr(collectors, "decrypt_secret", lambda _v: "community")

    result = asyncio.run(collectors.collect_snmp(device, timeout=1, retries=1))

    assert result.ok is True
    assert result.metrics["cpu"] is None
    assert result.metrics["memory"] is None


def test_an_all_zero_walk_is_not_read_as_a_real_measurement(monkeypatch):
    """Slots that do not host a CPU report 0; that is absence, not idleness."""
    device = Device(
        ip="172.16.51.2", vendor="Huawei", device_type="switch",
        snmp_version="v2c", snmp_community="encrypted",
    )

    class FakeTransport:
        @staticmethod
        async def create(*_args, **_kwargs):
            return "transport"

    async def fake_get_cmd(*_args):
        values = [("sys_name", "core"), ("sys_description", "S5720"),
                  ("uptime_ticks", "1000"), ("interface_count", "48")]
        return None, None, None, [(collectors.SYSTEM_OIDS.get(oid, oid), _Pretty(value)) for oid, value in values]

    async def zero_walk(_engine, _auth, _transport, _oid):
        return {"1": "0", "2": "0"}

    monkeypatch.setattr(collectors, "UdpTransportTarget", FakeTransport)
    monkeypatch.setattr(collectors, "get_cmd", fake_get_cmd)
    monkeypatch.setattr(collectors, "_walk_column", zero_walk)
    monkeypatch.setattr(collectors, "_collect_interface_metrics", lambda *_a: _async({}))
    monkeypatch.setattr(collectors, "decrypt_secret", lambda _v: "community")

    result = asyncio.run(collectors.collect_snmp(device, timeout=1, retries=1))

    assert result.metrics["cpu"] is None, "an all-zero walk means the MIB is unpopulated"


def _async(value):
    async def run(*_args, **_kwargs):
        return value
    return run()


class _Pretty:
    def __init__(self, value):
        self._value = value

    def prettyPrint(self):
        return self._value


def test_a_timed_out_vendor_walk_is_retried_before_giving_up(monkeypatch):
    """The reported bug: CPU shown as 不支持 on a device that does report it.

    The vendor column is the ninth consecutive walk of a full collection, and a
    live Huawei S5720 intermittently drops it under that load while answering
    the very next walk. Ten back-to-back CPU walks in isolation all succeeded,
    so the timeout reflects momentary pressure rather than a missing MIB and one
    retry must be attempted before the item is written off as unsupported.
    """
    device = Device(
        ip="172.16.51.2", vendor="Huawei", device_type="switch",
        snmp_version="v2c", snmp_community="encrypted",
    )
    attempts = {"cpu": 0}

    class FakeTransport:
        @staticmethod
        async def create(*_args, **_kwargs):
            return "transport"

    async def fake_get_cmd(*_args):
        values = [("sys_name", "core"), ("sys_description", "S5720"),
                  ("uptime_ticks", "1000"), ("interface_count", "48")]
        return None, None, None, [(collectors.SYSTEM_OIDS.get(oid, oid), _Pretty(value)) for oid, value in values]

    async def flaky_walk(_engine, _auth, _transport, oid):
        if oid == collectors.HUAWEI_CPU_OID:
            attempts["cpu"] += 1
            if attempts["cpu"] == 1:
                raise RuntimeError("No SNMP response received before timeout")
            return {"67108873": "19"}
        return {"67108873": "9"}

    monkeypatch.setattr(collectors, "UdpTransportTarget", FakeTransport)
    monkeypatch.setattr(collectors, "get_cmd", fake_get_cmd)
    monkeypatch.setattr(collectors, "_walk_column", flaky_walk)
    monkeypatch.setattr(collectors, "_collect_interface_metrics", lambda *_a: _async({}))
    monkeypatch.setattr(collectors, "decrypt_secret", lambda _v: "community")

    result = asyncio.run(collectors.collect_snmp(device, timeout=1, retries=1))

    assert attempts["cpu"] == 2, "a dropped vendor walk must be retried once"
    assert result.metrics["cpu"] == 19


def test_a_vendor_mib_that_never_answers_is_not_retried_forever(monkeypatch):
    """A device without the MIB must not pay the retry cost on every item."""
    device = Device(
        ip="172.16.51.2", vendor="Huawei", device_type="switch",
        snmp_version="v2c", snmp_community="encrypted",
    )
    attempts = {"count": 0}

    class FakeTransport:
        @staticmethod
        async def create(*_args, **_kwargs):
            return "transport"

    async def fake_get_cmd(*_args):
        values = [("sys_name", "core"), ("sys_description", "S5720"),
                  ("uptime_ticks", "1000"), ("interface_count", "48")]
        return None, None, None, [(collectors.SYSTEM_OIDS.get(oid, oid), _Pretty(value)) for oid, value in values]

    async def always_timeout(_engine, _auth, _transport, _oid):
        attempts["count"] += 1
        raise RuntimeError("No SNMP response received before timeout")

    monkeypatch.setattr(collectors, "UdpTransportTarget", FakeTransport)
    monkeypatch.setattr(collectors, "get_cmd", fake_get_cmd)
    monkeypatch.setattr(collectors, "_walk_column", always_timeout)
    monkeypatch.setattr(collectors, "_collect_interface_metrics", lambda *_a: _async({}))
    monkeypatch.setattr(collectors, "decrypt_secret", lambda _v: "community")

    result = asyncio.run(collectors.collect_snmp(device, timeout=1, retries=1))

    # cpu, memory and stp, two attempts each: one initial walk plus a single retry.
    assert attempts["count"] == 6
    assert result.metrics["cpu"] is None
    assert result.metrics["memory"] is None


def test_cisco_cpu_and_memory_come_from_the_official_cisco_mibs(monkeypatch):
    """Cisco publishes CPU and memory in its own documented MIBs.

    OIDs resolved from the ASN.1 sources in Cisco's own cisco-mibs
    repository:
      cpmCPUTotal5secRev  1.3.6.1.4.1.9.109.1.1.1.1.6  Gauge32 (0..100) percent
      ciscoMemoryPoolUsed 1.3.6.1.4.1.9.48.1.1.1.5     Gauge32 bytes
      ciscoMemoryPoolFree 1.3.6.1.4.1.9.48.1.1.1.6     Gauge32 bytes
    Memory is published as byte counters, so the percentage is used/(used+free).
    """
    device = Device(
        ip="10.99.80.10", vendor="Cisco", device_type="switch",
        snmp_version="v2c", snmp_community="encrypted",
    )
    walks = {
        collectors.CISCO_CPU_OID: {"1": "23"},
        collectors.CISCO_MEMORY_USED_OID: {"1": "250000"},
        collectors.CISCO_MEMORY_FREE_OID: {"1": "750000"},
    }

    class FakeTransport:
        @staticmethod
        async def create(*_args, **_kwargs):
            return "transport"

    async def fake_get_cmd(*_args):
        values = [("sys_name", "cat"), ("sys_description", "IOS"),
                  ("uptime_ticks", "1000"), ("interface_count", "24")]
        return None, None, None, [(collectors.SYSTEM_OIDS.get(oid, oid), _Pretty(value)) for oid, value in values]

    async def fake_walk(_engine, _auth, _transport, oid):
        return walks.get(oid, {})

    monkeypatch.setattr(collectors, "UdpTransportTarget", FakeTransport)
    monkeypatch.setattr(collectors, "get_cmd", fake_get_cmd)
    monkeypatch.setattr(collectors, "_walk_column", fake_walk)
    monkeypatch.setattr(collectors, "_collect_interface_metrics", lambda *_a: _async({}))
    monkeypatch.setattr(collectors, "decrypt_secret", lambda _v: "community")

    result = asyncio.run(collectors.collect_snmp(device, timeout=1, retries=1))

    assert collectors.CISCO_CPU_OID == "1.3.6.1.4.1.9.109.1.1.1.1.6"
    assert result.metrics["cpu"] == 23
    assert result.metrics["memory"] == 25


def test_a_cisco_without_the_process_mib_reports_no_reading(monkeypatch):
    """An IOS build without CISCO-PROCESS-MIB must not fabricate a value."""
    device = Device(
        ip="10.99.80.11", vendor="Cisco", device_type="switch",
        snmp_version="v2c", snmp_community="encrypted",
    )

    class FakeTransport:
        @staticmethod
        async def create(*_args, **_kwargs):
            return "transport"

    async def fake_get_cmd(*_args):
        values = [("sys_name", "cat"), ("sys_description", "IOS"),
                  ("uptime_ticks", "1000"), ("interface_count", "24")]
        return None, None, None, [(collectors.SYSTEM_OIDS.get(oid, oid), _Pretty(value)) for oid, value in values]

    async def empty_walk(_engine, _auth, _transport, _oid):
        return {}

    monkeypatch.setattr(collectors, "UdpTransportTarget", FakeTransport)
    monkeypatch.setattr(collectors, "get_cmd", fake_get_cmd)
    monkeypatch.setattr(collectors, "_walk_column", empty_walk)
    monkeypatch.setattr(collectors, "_collect_interface_metrics", lambda *_a: _async({}))
    monkeypatch.setattr(collectors, "decrypt_secret", lambda _v: "community")

    result = asyncio.run(collectors.collect_snmp(device, timeout=1, retries=1))

    assert result.metrics["cpu"] is None
    assert result.metrics["memory"] is None


def test_cisco_memory_sums_every_pool(monkeypatch):
    """A device reports several pools; the percentage covers all of them."""
    device = Device(
        ip="10.99.80.12", vendor="Cisco", device_type="switch",
        snmp_version="v2c", snmp_community="encrypted",
    )
    walks = {
        collectors.CISCO_CPU_OID: {"1": "10"},
        collectors.CISCO_MEMORY_USED_OID: {"1": "300", "2": "100"},
        collectors.CISCO_MEMORY_FREE_OID: {"1": "500", "2": "100"},
    }

    class FakeTransport:
        @staticmethod
        async def create(*_args, **_kwargs):
            return "transport"

    async def fake_get_cmd(*_args):
        values = [("sys_name", "cat"), ("sys_description", "IOS"),
                  ("uptime_ticks", "1000"), ("interface_count", "24")]
        return None, None, None, [(collectors.SYSTEM_OIDS.get(oid, oid), _Pretty(value)) for oid, value in values]

    async def fake_walk(_engine, _auth, _transport, oid):
        return walks.get(oid, {})

    monkeypatch.setattr(collectors, "UdpTransportTarget", FakeTransport)
    monkeypatch.setattr(collectors, "get_cmd", fake_get_cmd)
    monkeypatch.setattr(collectors, "_walk_column", fake_walk)
    monkeypatch.setattr(collectors, "_collect_interface_metrics", lambda *_a: _async({}))
    monkeypatch.setattr(collectors, "decrypt_secret", lambda _v: "community")

    result = asyncio.run(collectors.collect_snmp(device, timeout=1, retries=1))

    # (300+100) / (300+100+500+100) = 40%
    assert result.metrics["memory"] == 40


def test_cisco_memory_pairs_pool_indexes_and_ignores_invalid_pools(monkeypatch):
    result = _usage_case(monkeypatch, "Cisco", {
        collectors.CISCO_CPU_OID: {"1": "10"},
        collectors.CISCO_MEMORY_USED_OID: {"1": "900", "2": "10", "3": "9999"},
        collectors.CISCO_MEMORY_FREE_OID: {"1": "100", "2": "90", "4": "1"},
        collectors.CISCO_MEMORY_VALID_OID: {"1": "2", "2": "1", "3": "1", "4": "1"},
    })

    assert result.metrics["memory"] == 10


def _usage_case(monkeypatch, vendor, walks):
    device = Device(
        ip="10.99.80.20", vendor=vendor, device_type="switch",
        snmp_version="v2c", snmp_community="encrypted",
    )

    class FakeTransport:
        @staticmethod
        async def create(*_args, **_kwargs):
            return "transport"

    async def fake_get_cmd(*_args):
        values = [("sys_name", "sw"), ("sys_description", "d"),
                  ("uptime_ticks", "1000"), ("interface_count", "24")]
        return None, None, None, [(collectors.SYSTEM_OIDS.get(oid, oid), _Pretty(value)) for oid, value in values]

    async def fake_walk(_engine, _auth, _transport, oid):
        return walks.get(oid, {})

    monkeypatch.setattr(collectors, "UdpTransportTarget", FakeTransport)
    monkeypatch.setattr(collectors, "get_cmd", fake_get_cmd)
    monkeypatch.setattr(collectors, "_walk_column", fake_walk)
    monkeypatch.setattr(collectors, "_collect_interface_metrics", lambda *_a: _async({}))
    monkeypatch.setattr(collectors, "decrypt_secret", lambda _v: "community")
    return asyncio.run(collectors.collect_snmp(device, timeout=1, retries=1))


def test_h3c_cpu_and_memory_come_from_the_official_hh3c_mib(monkeypatch):
    """H3C publishes both as a 0..100 percentage in HH3C-ENTITY-EXT-MIB.

    OIDs resolved from the ASN.1 source: hh3c is enterprise 25506,
    hh3cCommon is {hh3c 2}, and the entity state entry carries
    hh3cEntityExtCpuUsage(6) and hh3cEntityExtMemUsage(8), both
    Integer32 (0..100). Like the Huawei entity column, idle slots answer 0,
    so the reading is the highest value in the column.
    """
    result = _usage_case(monkeypatch, "H3C", {
        collectors.H3C_CPU_OID: {"1": "0", "2": "17"},
        collectors.H3C_MEMORY_OID: {"1": "0", "2": "44"},
    })

    assert result.metrics["cpu"] == 17
    assert result.metrics["memory"] == 44


def test_ruijie_cpu_and_memory_come_from_the_official_ruijie_mibs(monkeypatch):
    """Ruijie publishes both as the Percent textual convention.

    ruijie is enterprise 4881; ruijieMgmt resolves through
    products(1).switch(1).switchMib(10).ruijieMgmt(2). CPU comes from
    RUIJIE-PROCESS-MIB and the pool utilisation from RUIJIE-MEMORY-MIB, so
    neither needs a byte conversion.
    """
    result = _usage_case(monkeypatch, "Ruijie", {
        collectors.RUIJIE_CPU_OID: {"0": "12"},
        collectors.RUIJIE_MEMORY_OID: {"1": "58"},
    })

    assert result.metrics["cpu"] == 12
    assert result.metrics["memory"] == 58


def test_a_vendor_device_without_the_private_mib_reports_no_reading(monkeypatch):
    """An empty walk must not be turned into a zero-percent reading."""
    result = _usage_case(monkeypatch, "H3C", {})

    assert result.metrics["cpu"] is None
    assert result.metrics["memory"] is None
