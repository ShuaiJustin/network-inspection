"""CVE-2026-44405: paramiko (through 4.0.0) still offers the SHA-1 based
``ssh-rsa`` host-key algorithm. netmiko pins paramiko <5.0, so the fix is to
stop negotiating the weak algorithm instead of upgrading.
"""

import asyncio
from pathlib import Path

import pytest
import paramiko

from app.models.device import Device
from app.services import collectors
from app.services.collectors import ProtocolResult
from app.services.collectors import SECURE_HOST_KEY_ALGORITHMS


def test_sha1_host_key_algorithm_is_not_offered():
    assert "ssh-rsa" not in SECURE_HOST_KEY_ALGORITHMS


def test_modern_host_key_algorithms_are_still_offered():
    # dropping the weak algorithm must not leave us unable to talk to devices
    for algorithm in ("ssh-ed25519", "rsa-sha2-512", "rsa-sha2-256"):
        assert algorithm in SECURE_HOST_KEY_ALGORITHMS


def test_the_allowlist_is_a_subset_of_what_paramiko_supports():
    assert set(SECURE_HOST_KEY_ALGORITHMS).issubset(set(paramiko.Transport._preferred_keys))


def test_every_netmiko_session_disables_the_sha1_host_key_algorithm(monkeypatch):
    device = Device(
        ip="192.0.2.200",
        vendor="Huawei",
        device_type="switch",
        ssh_port=22,
        ssh_username="ops",
        ssh_password="encrypted-password",
        ssh_host_fingerprint="SHA256:trusted",
    )
    connection_configs = []

    class FakeConnection:
        def __init__(self, **config):
            connection_configs.append(config)

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def find_prompt(self):
            return "<Core>"

        def send_command(self, command, **_kwargs):
            if command == "display cpu-usage":
                return "CPU utilization for five seconds: 20%"
            if command == "display current-configuration":
                return (
                    "#\nsysname Core-SW-01\n#\ninterface GigabitEthernet0/0/1\n"
                    " description uplink\n#\nreturn\n"
                )
            return "sysname Core-SW-01\ninterface GigabitEthernet0/0/1\ndescription uplink"

    monkeypatch.setattr(collectors, "ConnectHandler", FakeConnection)
    monkeypatch.setattr(collectors, "decrypt_secret", lambda value: "secret" if value else None)
    monkeypatch.setattr(
        collectors,
        "_verify_ssh_host_fingerprint",
        lambda *_args: ProtocolResult(True, "SSH", host_fingerprint="SHA256:trusted"),
    )

    assert collectors._test_ssh_connectivity_sync(device, 1).ok is True
    assert collectors._collect_ssh_sync(device, "quick", 1, 1, {"cpu"}).ok is True
    assert asyncio.run(collectors.backup_configuration(device, 1, 1)).ok is True

    assert len(connection_configs) == 3
    assert all(
        config.get("disabled_algorithms") == {"keys": ["ssh-rsa"]}
        for config in connection_configs
    )
    assert all(config.get("ssh_strict") is True for config in connection_configs)


class _FakeHostKey:
    def get_name(self):
        return "ssh-ed25519"

    def get_base64(self):
        return "AAAAC3NzaC1lZDI1NTE5AAAAITestPinnedHostKey"


def test_netmiko_session_is_pinned_to_the_verified_public_key(monkeypatch):
    observed = {}

    class FakeConnection:
        def __init__(self, **config):
            observed["config"] = config
            observed["known_hosts"] = Path(config["alt_key_file"]).read_text(encoding="ascii")

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

    monkeypatch.setattr(collectors, "ConnectHandler", FakeConnection)
    fingerprint = ProtocolResult(
        True,
        "SSH",
        host_fingerprint="SHA256:trusted",
        host_key_algorithm="ssh-ed25519",
        host_key=_FakeHostKey(),
    )
    config = {
        "device_type": "huawei",
        "host": "192.0.2.201",
        "port": 2222,
        "disabled_algorithms": collectors.NETMIKO_DISABLED_ALGORITHMS,
    }

    with collectors._pinned_netmiko_connection(config, fingerprint):
        key_file = Path(observed["config"]["alt_key_file"])
        assert key_file.is_file()

    assert observed["config"]["ssh_strict"] is True
    assert observed["config"]["alt_host_keys"] is True
    assert observed["known_hosts"] == (
        "[192.0.2.201]:2222 ssh-ed25519 "
        "AAAAC3NzaC1lZDI1NTE5AAAAITestPinnedHostKey\n"
    )
    assert not key_file.exists()


class _OversizedRemoteChannel:
    def __init__(self):
        self.requests = []

    def recv_ready(self):
        return True

    def recv(self, size):
        self.requests.append(size)
        return b"x" * size


class _BoundedConnection:
    instances = []

    def __init__(self, **_config):
        self.remote_conn = _OversizedRemoteChannel()
        self.encoding = "utf-8"
        self.ansi_escape_codes = False
        self._read_buffer = ""
        type(self).instances.append(self)

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def find_prompt(self):
        return "<Core>"

    def normalize_cmd(self, command):
        return command + "\n"

    def write_channel(self, _command):
        pass

    def send_command(self, *_args, **_kwargs):
        raise AssertionError("bounded SSH sessions must not call send_command")


def _patch_bounded_connection(monkeypatch):
    _BoundedConnection.instances = []
    monkeypatch.setattr(collectors, "ConnectHandler", _BoundedConnection)
    monkeypatch.setattr(collectors, "decrypt_secret", lambda value: value)
    monkeypatch.setattr(
        collectors,
        "_verify_ssh_host_fingerprint",
        lambda *_args: ProtocolResult(
            True,
            "SSH",
            host_fingerprint="SHA256:trusted",
            host_key_algorithm="ssh-ed25519",
        ),
    )
    return Device(
        ip="192.0.2.202",
        vendor="Huawei",
        device_type="switch",
        ssh_username="ops",
        ssh_password="password",
        ssh_host_fingerprint="SHA256:trusted",
    )


def test_inspection_stops_receiving_as_soon_as_the_command_limit_is_crossed(monkeypatch):
    device = _patch_bounded_connection(monkeypatch)
    monkeypatch.setattr(collectors, "MAX_SINGLE_RAW_OUTPUT_BYTES", 64)

    result = collectors._collect_ssh_sync(
        device,
        "quick",
        command_timeout=1,
        required_checks={"cpu"},
    )

    assert result.ok is False
    assert result.failure_stage == "output_truncated"
    assert len(result.raw_output.encode("utf-8")) <= 64
    assert _BoundedConnection.instances[0].remote_conn.requests == [
        64 - len("$ display cpu-usage\n".encode("utf-8")) + 1
    ]


def test_backup_stops_receiving_as_soon_as_its_hard_limit_is_crossed(monkeypatch):
    device = _patch_bounded_connection(monkeypatch)
    monkeypatch.setattr(collectors, "MAX_BACKUP_OUTPUT_BYTES", 64)

    result = asyncio.run(collectors.backup_configuration(device, 1, 1))

    assert result.ok is False
    assert result.failure_stage == "output_truncated"
    assert result.raw_output_status == "truncated"
    assert len(result.raw_output.encode("utf-8")) == 64
    assert _BoundedConnection.instances[0].remote_conn.requests == [65]


class _ChunkedRemoteChannel:
    def __init__(self, chunks):
        self.chunks = list(chunks)
        self.requests = []

    def recv_ready(self):
        return bool(self.chunks)

    def recv(self, size):
        self.requests.append(size)
        return self.chunks.pop(0)


class _StreamingConnection:
    def __init__(self, chunks):
        self.remote_conn = _ChunkedRemoteChannel(chunks)
        self.encoding = "utf-8"
        self.ansi_escape_codes = False
        self._read_buffer = ""
        self.writes = []

    def find_prompt(self):
        return "<Core>"

    def normalize_cmd(self, command):
        return command + "\n"

    def write_channel(self, command):
        self.writes.append(command)


def test_bounded_read_ignores_prompt_text_until_a_complete_final_prompt_line():
    connection = _StreamingConnection([
        b"show status\r\nfirst line\r\n<Core>\r\noutput continues\r\n",
        b"last line\r\n<Core>",
    ])

    output, _received = collectors._send_command_with_output_limit(
        connection, "show status", read_timeout=1, max_bytes=4096
    )

    assert "output continues" in output
    assert "last line" in output
    assert len(connection.remote_conn.requests) == 2


def test_bounded_read_falls_back_to_gb18030_for_chinese_device_output():
    payload = (
        "display current-configuration\r\n"
        "sysname 核心交换机\r\n"
        "备份成功\r\n"
        "<Core>"
    ).encode("gb18030")
    connection = _StreamingConnection([payload])

    output, received = collectors._send_command_with_output_limit(
        connection, "display current-configuration", read_timeout=1, max_bytes=4096
    )

    assert "sysname 核心交换机" in output
    assert "备份成功" in output
    assert received == len(payload)


def test_read_timeout_is_not_a_netmiko_connection_timeout():
    assert not issubclass(collectors.ReadTimeout, collectors.NetmikoTimeoutException)


def test_first_command_read_timeout_closes_session_without_sending_second(monkeypatch):
    device = _patch_bounded_connection(monkeypatch)
    sent_commands = []
    connection_closed = []

    class TimeoutConnection:
        def __init__(self, **_config):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            connection_closed.append(True)
            return False

    def timeout_on_command(_connection, command, _read_timeout, _max_bytes):
        sent_commands.append(command)
        raise collectors.ReadTimeout("prompt not received")

    monkeypatch.setattr(collectors, "ConnectHandler", TimeoutConnection)
    monkeypatch.setattr(collectors, "_send_command_with_output_limit", timeout_on_command)

    result = collectors._collect_ssh_sync(
        device,
        "quick",
        command_timeout=1,
        required_checks={"cpu", "memory"},
    )

    assert result.ok is False
    assert result.failure_stage == "collection"
    assert "timed out" in (result.message or "").lower()
    assert sent_commands == ["display cpu-usage"]
    assert connection_closed == [True]


def test_invalid_ssh_bytes_close_session_and_return_decode_failure(monkeypatch):
    device = _patch_bounded_connection(monkeypatch)
    connection_closed = []

    class InvalidBytesConnection(_StreamingConnection):
        def __init__(self, **_config):
            super().__init__([b"\xff\r\n<Core>"])

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            connection_closed.append(True)
            return False

    monkeypatch.setattr(collectors, "ConnectHandler", InvalidBytesConnection)

    result = collectors._collect_ssh_sync(
        device,
        "quick",
        command_timeout=1,
        required_checks={"cpu", "memory"},
    )

    assert result.ok is False
    assert result.failure_stage == "collection"
    assert "decoding failed" in (result.message or "").lower()
    assert connection_closed == [True]


def test_known_hosts_file_is_removed_when_initial_write_fails(monkeypatch):
    real_named_temporary_file = collectors.tempfile.NamedTemporaryFile
    created_path = None

    class FailingTemporary:
        def __init__(self, *args, **kwargs):
            nonlocal created_path
            self.file = real_named_temporary_file(*args, **kwargs)
            self.name = self.file.name
            created_path = Path(self.name)

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            self.file.close()
            return False

        def write(self, _content):
            raise OSError("known_hosts write failed")

    monkeypatch.setattr(collectors.tempfile, "NamedTemporaryFile", FailingTemporary)
    fingerprint = ProtocolResult(True, "SSH", host_key=_FakeHostKey())

    with pytest.raises(OSError, match="known_hosts write failed"):
        with collectors._pinned_netmiko_connection(
            {"host": "192.0.2.210", "port": 22}, fingerprint
        ):
            pass

    assert created_path is not None
    assert not created_path.exists()


def test_observed_rsa_algorithm_uses_the_negotiated_transport_type(monkeypatch):
    class RsaHostKey:
        def get_name(self):
            return "ssh-rsa"

        def asbytes(self):
            return b"rsa host key"

    host_key = RsaHostKey()

    class FakeSocket:
        def close(self):
            pass

    class FakeTransport:
        host_key_type = "rsa-sha2-512"

        def __init__(self, _socket):
            self._preferred_keys = ()

        def start_client(self, timeout):
            assert timeout == 3

        def get_remote_server_key(self):
            return host_key

        def close(self):
            pass

    monkeypatch.setattr(collectors.socket, "create_connection", lambda *_args, **_kwargs: FakeSocket())
    monkeypatch.setattr(collectors.paramiko, "Transport", FakeTransport)

    observed = collectors._read_ssh_host_fingerprint("192.0.2.211", 22, 3)

    assert observed.algorithm == "rsa-sha2-512"
    assert observed.key is host_key
