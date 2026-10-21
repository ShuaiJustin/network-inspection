import asyncio

import pytest
from pysnmp.proto import errind

from app.models.device import Device
from app.services import collectors


class Pretty:
    def __init__(self, value):
        self.value = value

    def prettyPrint(self):
        return self.value


def bind(oid, value):
    return oid, Pretty(value)


def install_walk(monkeypatch, responses):
    async def walk(*_args, **_kwargs):
        for response in responses:
            yield response

    monkeypatch.setattr(collectors, "walk_cmd", walk)
    monkeypatch.setattr(collectors, "bulk_walk_cmd", walk, raising=False)


def run_walk(oid="1.3.6.1.4.1.9", **kwargs):
    return asyncio.run(collectors._walk_column(object(), object(), object(), oid, **kwargs))


def test_walk_preserves_complete_relative_indexes(monkeypatch):
    install_walk(monkeypatch, [(None, None, None, [
        bind("1.3.6.1.4.1.9.10.1", "10"), bind("1.3.6.1.4.1.9.20.1", "20"),
    ])])
    assert run_walk() == {"10.1": "10", "20.1": "20"}


def test_walk_stops_at_subtree_boundary_and_ignores_exception_values(monkeypatch):
    install_walk(monkeypatch, [(None, None, None, [
        bind("1.3.6.1.4.1.9.1", "10"), bind("1.3.6.1.4.1.90.2", "99"),
    ])])
    assert run_walk() == {"1": "10"}
    install_walk(monkeypatch, [(None, None, None, [bind("1.3.6.1.4.1.9.1", "NoSuchInstance")])])
    assert run_walk() == {}


def test_walk_rejects_repeated_oids_instead_of_silently_overwriting(monkeypatch):
    install_walk(monkeypatch, [(None, None, None, [
        bind("1.3.6.1.4.1.9.1", "10"), bind("1.3.6.1.4.1.9.1", "20"),
    ])])
    with pytest.raises(RuntimeError, match="increasing"):
        run_walk()


def test_walk_row_limit_reports_incomplete_table(monkeypatch):
    install_walk(monkeypatch, [(None, None, None, [
        bind(f"1.3.6.1.4.1.9.{i}", "10") for i in range(1, 4)
    ])])
    with pytest.raises(RuntimeError, match="row limit"):
        run_walk(max_rows=2)


def test_walk_elapsed_limit_cancels_and_closes_generator(monkeypatch):
    closed = []

    async def slow_walk(*_args, **_kwargs):
        try:
            await asyncio.sleep(0.1)
            yield None, None, None, []
        finally:
            closed.append(True)

    monkeypatch.setattr(collectors, "walk_cmd", slow_walk)
    monkeypatch.setattr(collectors, "bulk_walk_cmd", slow_walk, raising=False)
    with pytest.raises(RuntimeError, match="elapsed time limit"):
        run_walk(max_seconds=0.01)
    assert closed == [True]


def test_bulk_walk_uses_small_batches_and_falls_back_only_on_too_big(monkeypatch):
    calls = []

    async def bulk(*args, **kwargs):
        calls.append(("bulk", args[4:6], kwargs))
        yield None, Pretty("tooBig"), None, []

    async def next_walk(*_args, **_kwargs):
        calls.append(("next",))
        yield None, None, None, [bind("1.3.6.1.4.1.9.1", "10")]

    monkeypatch.setattr(collectors, "bulk_walk_cmd", bulk, raising=False)
    monkeypatch.setattr(collectors, "walk_cmd", next_walk)
    assert run_walk() == {"1": "10"}
    assert [call[0] for call in calls] == ["bulk", "next"]
    assert calls[0][1] == (0, 10)
    assert calls[0][2]["lexicographicMode"] is False
    assert calls[0][2]["maxCalls"] > 0


def test_if_oper_status_retains_every_standard_state_and_admin_shutdown():
    metrics = collectors.build_interface_metrics({
        "oper_status": {str(i): str(i) for i in range(1, 8)},
        "admin_status": {"1": "1", "2": "2", "7": "1"},
    })
    assert [row["oper_status"] for row in metrics["interfaces"]] == [
        "up", "down", "testing", "unknown", "dormant", "notPresent", "lowerLayerDown",
    ]
    assert metrics["interfaces"][1]["admin_status"] == "down"
    assert metrics["interfaces"][1]["oper_status_code"] == 2
    assert metrics["interfaces_down"] == 2
    assert metrics["interfaces_admin_down"] == 1
    assert metrics["interfaces_unknown"] == 1


def test_unknown_interface_codes_remain_evidence():
    metrics = collectors.build_interface_metrics({"oper_status": {"9": "999"}})
    assert metrics["interfaces"][0]["oper_status_code"] == 999
    assert metrics["interfaces_unknown"] == 1


def test_interface_metrics_ignore_stale_counter_indexes_outside_oper_status():
    metrics = collectors.build_interface_metrics({
        "oper_status": {"1": "1"},
        "name": {"1": "GE1/0/1", "99": "deleted-port"},
        "in_errors": {"1": "0", "99": "100"},
        "out_errors": {"1": "0", "99": "0"},
        "in_discards": {"1": "0", "99": "50"},
        "out_discards": {"1": "0", "99": "0"},
    })

    assert [interface["index"] for interface in metrics["interfaces"]] == [1]
    assert metrics["interface_error_count"] == 0
    assert metrics["interface_discard_count"] == 0
    assert "outside ifOperStatus indexes" in metrics["interface_collection_warnings"]["ignored_indexes"]
    assert "99" in metrics["interface_collection_warnings"]["ignored_indexes"]


def test_interface_collector_requests_if_admin_status(monkeypatch):
    queried = []

    async def walk(_engine, _auth, _transport, oid):
        queried.append(oid)
        return {"1": "1"}

    monkeypatch.setattr(collectors, "_walk_column", walk)
    asyncio.run(collectors._collect_interface_metrics(object(), object(), object()))
    assert "1.3.6.1.2.1.2.2.1.7" in queried


def device():
    return Device(ip="192.0.2.10", vendor="Huawei", device_type="firewall",
                  snmp_version="v2c", snmp_community="encrypted")


def install_probe(monkeypatch, responses):
    engines, transport_retries, calls = [], [], []

    class Engine:
        def __init__(self):
            self.closed = False
            engines.append(self)

        def close_dispatcher(self):
            self.closed = True

    class Transport:
        @staticmethod
        async def create(*_args, **kwargs):
            transport_retries.append(kwargs["retries"])
            return object()

    async def get(*_args):
        if len(_args) == 5:  # Optional sysObjectID is separate from the required probe.
            return None, None, None, [bind("1.3.6.1.2.1.1.2.0", "NoSuchObject")]
        calls.append(True)
        response = responses[min(len(calls) - 1, len(responses) - 1)]
        if isinstance(response, Exception):
            raise response
        return response

    async def interface(*_args):
        return {"interface_collection_status": "success", "interfaces": [{"index": 1}]}

    async def usage(*_args):
        return {"cpu": None, "memory": None, "failed_reasons": {}}

    monkeypatch.setattr(collectors, "SnmpEngine", Engine)
    monkeypatch.setattr(collectors, "UdpTransportTarget", Transport)
    monkeypatch.setattr(collectors, "get_cmd", get)
    monkeypatch.setattr(collectors, "decrypt_secret", lambda _: "community")
    monkeypatch.setattr(collectors, "_collect_interface_metrics", interface)
    monkeypatch.setattr(collectors, "_collect_usage_metrics", usage)
    return engines, transport_retries, calls


SUCCESS = (None, None, None, [
    bind(collectors.SYSTEM_OIDS["sys_name"], "core"),
    bind(collectors.SYSTEM_OIDS["sys_description"], "switch"),
    bind(collectors.SYSTEM_OIDS["uptime_ticks"], "1000"),
    bind(collectors.SYSTEM_OIDS["interface_count"], "1"),
])
TIMEOUT = (errind.requestTimedOut, None, None, [])


@pytest.mark.parametrize("method", ["test_snmp_connectivity", "collect_snmp"])
def test_initial_timeout_uses_retry_budget_then_closes_engine(monkeypatch, method):
    engines, retries, calls = install_probe(monkeypatch, [TIMEOUT, SUCCESS])
    result = asyncio.run(getattr(collectors, method)(device(), timeout=1, retries=1))
    assert result.ok is True
    assert len(calls) == 2
    assert all(retry == 0 for retry in retries)
    assert engines and all(engine.closed for engine in engines)


@pytest.mark.parametrize("method", ["test_snmp_connectivity", "collect_snmp"])
@pytest.mark.parametrize("response", [
    (errind.unknownUserName, None, None, []),
    (None, Pretty("noAccess"), None, []),
    RuntimeError("invalid transport"),
])
def test_non_timeout_response_is_not_retried_and_engine_always_closes(monkeypatch, method, response):
    engines, _retries, calls = install_probe(monkeypatch, [response, TIMEOUT])
    result = asyncio.run(getattr(collectors, method)(device(), timeout=1, retries=3))
    assert result.ok is False
    assert len(calls) == 1
    assert engines and all(engine.closed for engine in engines)


@pytest.mark.parametrize("method", ["test_snmp_connectivity", "collect_snmp"])
def test_zero_retry_budget_performs_one_exchange(monkeypatch, method):
    engines, _retries, calls = install_probe(monkeypatch, [TIMEOUT, SUCCESS])
    result = asyncio.run(getattr(collectors, method)(device(), timeout=1, retries=0))
    assert result.ok is False
    assert len(calls) == 1
    assert all(engine.closed for engine in engines)


@pytest.mark.parametrize("vendor", ["Huawei", "H3C"])
def test_idle_cpu_with_same_entity_memory_evidence_is_zero(monkeypatch, vendor):
    oids = collectors.resolve_platform(vendor).snmp_usage_oids

    async def walk(_engine, _auth, _transport, oid):
        return {"10.1": "0", "20.1": "0"} if oid == oids["cpu"] else {"20.1": "40"}

    monkeypatch.setattr(collectors, "_walk_column", walk)
    metrics = asyncio.run(collectors._collect_usage_metrics(object(), object(), object(), vendor))
    assert metrics["cpu"] == 0
    assert metrics["memory"] == 40


def test_ruijie_populated_zero_percent_is_valid(monkeypatch):
    async def walk(*_args):
        return {"0": "0"}

    monkeypatch.setattr(collectors, "_walk_column", walk)
    metrics = asyncio.run(collectors._collect_usage_metrics(object(), object(), object(), "Ruijie"))
    assert metrics["cpu"] == 0
    assert metrics["memory"] == 0


@pytest.mark.parametrize("collector", ["usage", "stp"])
def test_permission_error_on_optional_walk_is_preserved_without_retry(monkeypatch, collector):
    calls = []

    async def walk(_engine, _auth, _transport, oid):
        calls.append(oid)
        if calls.count(oid) == 1:
            raise RuntimeError("noAccess")
        raise RuntimeError("No SNMP response received before timeout")

    monkeypatch.setattr(collectors, "_walk_column", walk)
    if collector == "usage":
        metrics = asyncio.run(collectors._collect_usage_metrics(object(), object(), object(), "Huawei"))
    else:
        metrics = asyncio.run(collectors._collect_stp_metrics(object(), object(), object()))
    assert all(calls.count(oid) == 1 for oid in set(calls))
    assert set(metrics["failed_reasons"].values()) == {"noAccess"}


def test_collection_maps_reordered_scalar_responses_by_oid(monkeypatch):
    install_probe(monkeypatch, [(None, None, None, list(reversed(SUCCESS[3])))])
    result = asyncio.run(collectors.collect_snmp(device(), retries=0))
    assert result.metrics["hostname"] == "core"
    assert result.metrics["model"] == "switch"
    assert result.metrics["interfaces_total"] == 1
    assert result.metrics["uptime"] == "0d 0h 0m 10s"


def test_collection_keeps_optional_sys_object_id_and_evidence(monkeypatch):
    install_probe(monkeypatch, [SUCCESS])
    requests = []

    async def get(*args):
        requests.append(len(args) - 4)
        if len(args) == 5:
            return None, None, None, [bind("1.3.6.1.2.1.1.2.0", "1.3.6.1.4.1.2011.2.23.1")]
        return SUCCESS

    monkeypatch.setattr(collectors, "get_cmd", get)
    result = asyncio.run(collectors.collect_snmp(device(), retries=0))
    assert requests == [4, 1]
    assert result.metrics["sys_object_id"] == "1.3.6.1.4.1.2011.2.23.1"
    assert "1.3.6.1.2.1.1.2.0 = 1.3.6.1.4.1.2011.2.23.1" in result.raw_output


@pytest.mark.parametrize("response", [(None, Pretty("noAccess"), None, []), TIMEOUT])
def test_optional_identity_failure_does_not_discard_successful_metrics(monkeypatch, response):
    install_probe(monkeypatch, [SUCCESS])
    requests = []

    async def get(*args):
        requests.append(len(args) - 4)
        return response if len(args) == 5 else SUCCESS

    monkeypatch.setattr(collectors, "get_cmd", get)
    result = asyncio.run(collectors.collect_snmp(device(), retries=3))
    assert requests == [4, 1]
    assert result.ok
    assert result.metrics["hostname"] == "core"
    assert "sys_object_id" not in result.metrics


def test_cpu_entity_class_can_validate_zero_cpu_without_validating_empty_slots(monkeypatch):
    oids = collectors.resolve_platform("Huawei").snmp_usage_oids

    async def walk(_engine, _auth, _transport, oid):
        if oid == "1.3.6.1.2.1.47.1.1.1.1.5":
            return {"10": "5", "20": "12"}  # Container slot vs actual processor.
        return {"10": "0", "20": "0"} if oid == oids["cpu"] else {}

    monkeypatch.setattr(collectors, "_walk_column", walk)
    metrics = asyncio.run(collectors._collect_usage_metrics(object(), object(), object(), "Huawei"))
    assert metrics["cpu"] == 0
    assert metrics["memory"] is None


def test_zero_cpu_does_not_use_other_entitys_positive_memory(monkeypatch):
    oids = collectors.resolve_platform("Huawei").snmp_usage_oids

    async def walk(_engine, _auth, _transport, oid):
        return {oids["cpu"]: {"10.1": "0"}, oids["memory"]: {"20.1": "40"}}.get(oid, {})

    monkeypatch.setattr(collectors, "_walk_column", walk)
    metrics = asyncio.run(collectors._collect_usage_metrics(object(), object(), object(), "Huawei"))
    assert metrics["cpu"] is None
    assert metrics["memory"] == 40


@pytest.mark.parametrize("method", ["test_snmp_connectivity", "collect_snmp"])
def test_timeout_retries_honor_supported_budget_without_unbounded_attempts(monkeypatch, method):
    engines, retries, calls = install_probe(monkeypatch, [TIMEOUT])
    result = asyncio.run(getattr(collectors, method)(device(), timeout=1, retries=99))
    assert not result.ok
    assert len(calls) == 6  # Settings support 0..5 retries.
    assert retries == [0]
    assert all(engine.closed for engine in engines)


def test_cisco_invalid_cpu_percent_is_not_reported_as_utilization(monkeypatch):
    async def walk(_engine, _auth, _transport, oid):
        return {"1": "400", "2": "0"} if oid == collectors.CISCO_CPU_OID else {}

    monkeypatch.setattr(collectors, "_walk_column", walk)
    metrics = asyncio.run(collectors._collect_cisco_usage(object(), object(), object()))
    assert metrics["cpu"] == 0


def test_sys_object_id_uses_numeric_value_even_when_mib_resolves_a_symbol(monkeypatch):
    install_probe(monkeypatch, [SUCCESS])

    class ObjectIdentifier(Pretty):
        def asTuple(self):
            return (1, 3, 6, 1, 4, 1, 2011, 2, 23, 1)

    async def get(*args):
        if len(args) == 5:
            return None, None, None, [("1.3.6.1.2.1.1.2.0", ObjectIdentifier("SNMPv2-SMI::enterprises.2011.2.23.1"))]
        return SUCCESS

    monkeypatch.setattr(collectors, "get_cmd", get)
    result = asyncio.run(collectors.collect_snmp(device(), retries=0))
    assert result.metrics["sys_object_id"] == "1.3.6.1.4.1.2011.2.23.1"


@pytest.mark.parametrize("method", ["test_snmp_connectivity", "collect_snmp"])
def test_cancelled_probe_closes_engine(monkeypatch, method):
    engines, _retries, _calls = install_probe(monkeypatch, [SUCCESS])

    async def get(*_args):
        await asyncio.sleep(10)

    async def cancel():
        task = asyncio.create_task(getattr(collectors, method)(device(), retries=0))
        await asyncio.sleep(0)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    monkeypatch.setattr(collectors, "get_cmd", get)
    asyncio.run(cancel())
    assert engines and all(engine.closed for engine in engines)


def test_bulk_permission_error_never_triggers_getnext_fallback(monkeypatch):
    calls = []

    async def bulk(*_args, **_kwargs):
        calls.append("bulk")
        yield None, Pretty("noAccess"), None, []

    async def next_walk(*_args, **_kwargs):
        calls.append("next")
        yield None, None, None, []

    monkeypatch.setattr(collectors, "bulk_walk_cmd", bulk)
    monkeypatch.setattr(collectors, "walk_cmd", next_walk)
    with pytest.raises(RuntimeError, match="noAccess"):
        run_walk()
    assert calls == ["bulk"]


def test_malformed_interface_index_is_an_item_failure_not_a_collection_crash(monkeypatch):
    async def walk(_engine, _auth, _transport, oid):
        return {"10.1": "1"} if oid == collectors.INTERFACE_OIDS["oper_status"] else {}

    monkeypatch.setattr(collectors, "_walk_column", walk)
    metrics = asyncio.run(collectors._collect_interface_metrics(object(), object(), object()))
    assert metrics["interface_collection_status"] == "data_fetch_failed"
    assert "ifIndex" in metrics["interface_collection_message"]


def test_unavailable_admin_status_is_reported_as_incomplete_evidence(monkeypatch):
    async def walk(_engine, _auth, _transport, oid):
        return {} if oid == collectors.INTERFACE_OIDS["admin_status"] else {"1": "1"}

    monkeypatch.setattr(collectors, "_walk_column", walk)
    metrics = asyncio.run(collectors._collect_interface_metrics(object(), object(), object()))
    assert metrics["interfaces"][0]["admin_status"] == "unknown"
    assert "admin_status" in metrics["interface_collection_warnings"]


def test_walk_retry_respects_the_remaining_whole_device_budget(monkeypatch):
    async def stalled_walk(*_args, **_kwargs):
        await asyncio.sleep(1)
        return {"1": "1"}

    async def run():
        token = collectors._ACTIVE_SNMP_DEADLINE.set(
            asyncio.get_running_loop().time() + 0.02
        )
        try:
            return await collectors._walk_with_retry(object(), object(), object(), "1.3.6.1")
        finally:
            collectors._ACTIVE_SNMP_DEADLINE.reset(token)

    monkeypatch.setattr(collectors, "_walk_column", stalled_walk)
    values, error = asyncio.run(run())

    assert values == {}
    assert "device collection exceeded elapsed time limit" in error
