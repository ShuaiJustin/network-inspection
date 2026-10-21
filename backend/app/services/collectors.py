import asyncio
import base64
import codecs
from contextlib import aclosing, contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
import functools
import hashlib
from pathlib import Path
import re
import socket
import tempfile
import time
from typing import Any

from netmiko import ConnectHandler
from netmiko.exceptions import NetmikoAuthenticationException, NetmikoTimeoutException, ReadTimeout
import paramiko
from pysnmp.hlapi.v3arch.asyncio import (
    CommunityData,
    ContextData,
    ObjectIdentity,
    ObjectType,
    SnmpEngine,
    UdpTransportTarget,
    UsmUserData,
    get_cmd,
    bulk_walk_cmd,
    walk_cmd,
    usm3DESEDEPrivProtocol,
    usmAesCfb128Protocol,
    usmAesCfb192Protocol,
    usmAesCfb256Protocol,
    usmDESPrivProtocol,
    usmHMAC128SHA224AuthProtocol,
    usmHMAC192SHA256AuthProtocol,
    usmHMAC256SHA384AuthProtocol,
    usmHMAC384SHA512AuthProtocol,
    usmHMACMD5AuthProtocol,
    usmHMACSHAAuthProtocol,
    usmNoAuthProtocol,
    usmNoPrivProtocol,
)

from app.models.device import Device
from app.services.configuration_validation import validate_configuration
from app.services.parsers import METRIC_ITEMS, parse_snmp_identity, parse_ssh_output
from app.services.security import CredentialDecryptionError, decrypt_secret_or_fail as decrypt_secret
from app.services.vendor_registry import VENDORS, resolve_platform


@dataclass(frozen=True)
class RawOutputCapture:
    content: str
    byte_count: int
    truncated: bool
    status: str

@dataclass(frozen=True)
class _ObservedSshHostKey:
    algorithm: str
    fingerprint: str
    key: paramiko.PKey

    def __iter__(self):
        # Keep the historical two-value unpacking contract for internal tests
        # and callers while retaining the public key used by the strict session.
        yield self.algorithm
        yield self.fingerprint


def limit_raw_output(content: str, max_bytes: int) -> RawOutputCapture:
    encoded = content.encode("utf-8")
    if len(encoded) <= max_bytes:
        return RawOutputCapture(content, len(encoded), False, "complete")
    retained = encoded[:max_bytes].decode("utf-8", errors="ignore")
    return RawOutputCapture(retained, len(encoded), True, "truncated")


SYSTEM_OIDS = {
    "sys_name": "1.3.6.1.2.1.1.5.0",
    "sys_description": "1.3.6.1.2.1.1.1.0",
    "uptime_ticks": "1.3.6.1.2.1.1.3.0",
    "interface_count": "1.3.6.1.2.1.2.1.0",
}
SYS_OBJECT_ID_OID = "1.3.6.1.2.1.1.2.0"
# ENTITY-MIB::entPhysicalClass; RFC 6933 class cpu(12) identifies a processor.
ENTITY_PHYSICAL_CLASS_OID = "1.3.6.1.2.1.47.1.1.1.1.5"
INTERFACE_OIDS = {
    "name": "1.3.6.1.2.1.31.1.1.1.1",
    "admin_status": "1.3.6.1.2.1.2.2.1.7",
    "oper_status": "1.3.6.1.2.1.2.2.1.8",
    "in_errors": "1.3.6.1.2.1.2.2.1.14",
    "out_errors": "1.3.6.1.2.1.2.2.1.20",
    "in_discards": "1.3.6.1.2.1.2.2.1.13",
    "out_discards": "1.3.6.1.2.1.2.2.1.19",
    "in_octets": "1.3.6.1.2.1.31.1.1.1.6",
    "out_octets": "1.3.6.1.2.1.31.1.1.1.10",
}
AUTH_PROTOCOLS = {
    "MD5": usmHMACMD5AuthProtocol,
    "SHA": usmHMACSHAAuthProtocol,
    "SHA224": usmHMAC128SHA224AuthProtocol,
    "SHA256": usmHMAC192SHA256AuthProtocol,
    "SHA384": usmHMAC256SHA384AuthProtocol,
    "SHA512": usmHMAC384SHA512AuthProtocol,
}
PRIV_PROTOCOLS = {
    "DES": usmDESPrivProtocol,
    "3DES": usm3DESEDEPrivProtocol,
    "AES": usmAesCfb128Protocol,
    "AES192": usmAesCfb192Protocol,
    "AES256": usmAesCfb256Protocol,
}
# Compatibility views for existing callers; the registry remains the only
# source of truth for commands, item ownership, and Netmiko device types.
NETMIKO_DEVICE_TYPES = {
    vendor_id: resolve_platform(vendor_id).netmiko_type for vendor_id in VENDORS
}
SSH_COMMANDS = {
    vendor_id: {
        "quick": [spec.command for spec in resolve_platform(vendor_id).commands if spec.level == "quick"],
        "full": [spec.command for spec in resolve_platform(vendor_id).commands if spec.level == "full"],
        "backup": resolve_platform(vendor_id).backup_command,
    }
    for vendor_id in VENDORS
}
SSH_COMMAND_ITEMS = {
    vendor_id: resolve_platform(vendor_id).command_items for vendor_id in VENDORS
}
MAX_SINGLE_RAW_OUTPUT_BYTES = 1 * 1024 * 1024
MAX_BACKUP_OUTPUT_BYTES = 10 * 1024 * 1024
SSH_CHANNEL_READ_SIZE = 65535
MAX_DEVICE_RAW_OUTPUT_BYTES = 10 * 1024 * 1024


@dataclass
class _SnmpEvidenceSection:
    received_bytes: int = 0


class _SnmpRawEvidence:
    def __init__(self, single_limit: int, device_limit: int):
        self.single_limit = single_limit
        self.device_limit = device_limit
        self.parts: list[str] = []
        self.byte_count = 0
        self.stored_bytes = 0
        self.truncated = False

    def start_section(self, title: str) -> _SnmpEvidenceSection:
        section = _SnmpEvidenceSection()
        prefix = "" if not self.parts else "\n"
        self._append(section, f"{prefix}=== {title} ===\n")
        return section

    def append_var_bind(self, section: _SnmpEvidenceSection, var_bind) -> None:
        oid = _snmp_oid_text(var_bind[0])
        value = var_bind[1].prettyPrint()
        self._append(section, f"{oid} = {value}\n")

    def _append(self, section: _SnmpEvidenceSection, content: str) -> None:
        encoded = content.encode("utf-8")
        section_remaining = max(0, self.single_limit - section.received_bytes)
        device_remaining = max(0, self.device_limit - self.stored_bytes)
        retained = encoded[:min(section_remaining, device_remaining)].decode(
            "utf-8", errors="ignore"
        )
        retained_bytes = len(retained.encode("utf-8"))
        if retained:
            self.parts.append(retained)
        section.received_bytes += len(encoded)
        self.byte_count += len(encoded)
        self.stored_bytes += retained_bytes
        if retained_bytes < len(encoded):
            self.truncated = True

    def capture(self) -> RawOutputCapture:
        return RawOutputCapture(
            "".join(self.parts),
            self.byte_count,
            self.truncated,
            "truncated" if self.truncated else "complete",
        )


_ACTIVE_SNMP_EVIDENCE: ContextVar[_SnmpRawEvidence | None] = ContextVar(
    "active_snmp_evidence", default=None
)
_ACTIVE_SNMP_RETRY_BUDGET: ContextVar[int] = ContextVar("snmp_retry_budget", default=1)
_ACTIVE_SNMP_DEADLINE: ContextVar[float | None] = ContextVar("snmp_deadline", default=None)
MAX_SNMP_RETRIES = 5
MAX_SNMP_WALK_ROWS = 4096
MAX_SNMP_WALK_SECONDS = 20
MAX_SNMP_DEVICE_SECONDS = 60
SNMP_BULK_REPETITIONS = 10


@dataclass
class ProtocolResult:
    ok: bool
    source: str
    metrics: dict[str, Any] = field(default_factory=dict)
    raw_output: str = ""
    failure_stage: str | None = None
    message: str | None = None
    authentication_failed: bool = False
    host_fingerprint: str | None = None
    host_key: paramiko.PKey | None = field(default=None, repr=False, compare=False)
    host_key_algorithm: str | None = None
    raw_output_status: str = "complete"
    raw_output_bytes: int = 0
    unsupported_checks: set[str] = field(default_factory=set)
    unsupported_reasons: dict[str, str] = field(default_factory=dict)
    failed_checks: set[str] = field(default_factory=set)
    failed_reasons: dict[str, str] = field(default_factory=dict)
    item_messages: dict[str, str] = field(default_factory=dict)
    item_references: dict[str, str] = field(default_factory=dict)


class CollectionConfigurationError(RuntimeError):
    """A stored device points at a platform the current registry cannot use."""


def _resolve_platform_for_collection(vendor: str, platform_id: str | None):
    try:
        return resolve_platform(vendor, platform_id)
    except (ValueError, KeyError) as exc:
        raise CollectionConfigurationError(
            "Device platform configuration is invalid; edit the device and select a supported platform"
        ) from exc


def ssh_commands_for_checks(
    vendor: str,
    inspection_level: str,
    required_checks: set[str] | None,
    platform_id: str | None = None,
    include_fallbacks: bool = False,
) -> list[str]:
    platform = _resolve_platform_for_collection(vendor, platform_id)
    levels = {"quick", "full"} if inspection_level == "full" else {"quick"}
    commands = [
        spec.command
        for spec in platform.commands
        if spec.level in levels and (include_fallbacks or not spec.is_fallback)
    ]
    if required_checks is None:
        return commands
    item_map = platform.command_items
    return [command for command in commands if item_map.get(command, set()) & required_checks]


# pysnmp reports USM failures through errind objects whose wording does not
# repeat the SNMP error name, so matching on names like "unknownUserName"
# never fires and every credential problem reaches the operator as a bare
# timeout. Match the messages pysnmp actually emits.
SNMP_AUTHENTICATION_MARKERS = (
    "authorization",
    "noaccess",
    "unknownuser",
    "wrongdigest",
    "unknownusmuser",
    "wrongsnmppdudigest",
    "unknownsnmpsecurityname",
    "unsupportedsnmpsecuritylevel",
    "cipheringservicesnotavailable",
    "ciphertextisbroken",
    "notinwindowsoftrust",
    "authenticatormismatched",
    "unknownsnmpcommunityname",
)


SNMP_TIMEOUT_MARKER = "nosnmpresponsereceivedbeforetimeout"
SSH_AUTHENTICATION_MARKERS = (
    "failed to enter enable mode",
    "enable password",
    "enable secret",
    "authentication failed",
    "authorization failed",
    "privilege escalation",
    "access denied",
)


def _snmp_error_is_timeout(message: str) -> bool:
    return SNMP_TIMEOUT_MARKER in "".join(message.lower().split()).replace("_", "")


def _snmp_error_is_authentication(message: str) -> bool:
    normalized = "".join(message.lower().split()).replace("_", "")
    return any(marker in normalized for marker in SNMP_AUTHENTICATION_MARKERS)


def _ssh_error_is_authentication(message: str) -> bool:
    normalized = " ".join(message.lower().split())
    return any(marker in normalized for marker in SSH_AUTHENTICATION_MARKERS)


def _format_uptime(ticks: int) -> str:
    seconds = ticks // 100
    days, seconds = divmod(seconds, 86400)
    hours, seconds = divmod(seconds, 3600)
    minutes, seconds = divmod(seconds, 60)
    return f"{days}d {hours}h {minutes}m {seconds}s"


# CVE-2026-44405: paramiko through 4.0.0 still offers the SHA-1 based "ssh-rsa"
# host-key algorithm, and netmiko pins paramiko <5.0 so we cannot upgrade past it.
# Restrict negotiation to the modern algorithms instead.
SECURE_HOST_KEY_ALGORITHMS = tuple(
    algorithm for algorithm in paramiko.Transport._preferred_keys if algorithm != "ssh-rsa"
)
NETMIKO_DISABLED_ALGORITHMS = {"keys": ["ssh-rsa"]}


def _credential_errors_as_result(protocol: str):
    """Turn an undecryptable stored credential into a normal authentication failure.

    DPAPI blobs are bound to the Windows user that created them, so a database
    restored under another user cannot be decrypted. Without this the raw
    OSError/binascii error escapes and aborts the whole inspection task.
    """

    def decorate(function):
        if asyncio.iscoroutinefunction(function):
            @functools.wraps(function)
            async def async_wrapper(*args, **kwargs):
                try:
                    return await function(*args, **kwargs)
                except CredentialDecryptionError as exc:
                    return ProtocolResult(
                        False,
                        protocol,
                        failure_stage="authentication",
                        message=str(exc),
                        authentication_failed=True,
                    )
                except CollectionConfigurationError as exc:
                    return ProtocolResult(
                        False,
                        protocol,
                        failure_stage="configuration",
                        message=str(exc),
                    )
            return async_wrapper

        @functools.wraps(function)
        def wrapper(*args, **kwargs):
            try:
                return function(*args, **kwargs)
            except CredentialDecryptionError as exc:
                return ProtocolResult(
                    False,
                    protocol,
                    failure_stage="authentication",
                    message=str(exc),
                    authentication_failed=True,
                )
            except CollectionConfigurationError as exc:
                return ProtocolResult(
                    False,
                    protocol,
                    failure_stage="configuration",
                    message=str(exc),
                )
        return wrapper

    return decorate


def _read_ssh_host_fingerprint(host: str, port: int, timeout: int) -> _ObservedSshHostKey:
    client_socket = socket.create_connection((host, port), timeout=timeout)
    transport: paramiko.Transport | None = None
    try:
        transport = paramiko.Transport(client_socket)
        transport._preferred_keys = SECURE_HOST_KEY_ALGORITHMS
        transport.start_client(timeout=timeout)
        remote_key = transport.get_remote_server_key()
        digest = hashlib.sha256(remote_key.asbytes()).digest()
        fingerprint = "SHA256:" + base64.b64encode(digest).decode("ascii").rstrip("=")
        return _ObservedSshHostKey(transport.host_key_type, fingerprint, remote_key)
    finally:
        if transport:
            transport.close()
        else:
            client_socket.close()


def _observe_ssh_host_fingerprint(device: Device, connection_timeout: int) -> ProtocolResult:
    try:
        observation = _read_ssh_host_fingerprint(device.ip, device.ssh_port, connection_timeout)
        algorithm, observed = observation
    except (OSError, paramiko.SSHException) as exc:
        return ProtocolResult(False, "SSH", failure_stage="connection", message=str(exc))
    return ProtocolResult(
        True,
        "SSH",
        host_fingerprint=observed,
        host_key_algorithm=algorithm,
        host_key=getattr(observation, "key", None),
    )


def _verify_ssh_host_fingerprint(device: Device, connection_timeout: int) -> ProtocolResult:
    observed_result = _observe_ssh_host_fingerprint(device, connection_timeout)
    if not observed_result.ok:
        return observed_result
    observed = observed_result.host_fingerprint
    algorithm = observed_result.host_key_algorithm
    if not device.ssh_host_fingerprint:
        return ProtocolResult(
            False,
            "SSH",
            failure_stage="host_fingerprint_untrusted",
            message="SSH host fingerprint is not trusted; administrator confirmation required",
            host_fingerprint=observed,
            host_key_algorithm=algorithm,
        )
    if device.ssh_host_fingerprint != observed:
        return ProtocolResult(
            False,
            "SSH",
            failure_stage="host_fingerprint_changed",
            message="SSH host fingerprint changed; administrator confirmation required",
            host_fingerprint=observed,
            host_key_algorithm=algorithm,
        )
    return ProtocolResult(
        True,
        "SSH",
        host_fingerprint=observed,
        host_key_algorithm=algorithm,
        host_key=observed_result.host_key,
    )


@contextmanager
def _pinned_netmiko_connection(config: dict[str, Any], fingerprint_check: ProtocolResult):
    """Open Netmiko with the exact key observed and matched to the saved fingerprint."""
    key_file: Path | None = None
    strict_config = {**config, "ssh_strict": True}
    host_key = fingerprint_check.host_key
    try:
        if host_key is not None:
            host = str(config["host"])
            port = int(config["port"])
            known_host = host if port == 22 else f"[{host}]:{port}"
            with tempfile.NamedTemporaryFile(
                mode="w", encoding="ascii", prefix="network-inspection-known-host-", delete=False
            ) as temporary:
                key_file = Path(temporary.name)
                temporary.write(f"{known_host} {host_key.get_name()} {host_key.get_base64()}\n")
            strict_config.update({"alt_host_keys": True, "alt_key_file": str(key_file)})
        with ConnectHandler(**strict_config) as connection:
            yield connection
    finally:
        if key_file is not None:
            try:
                key_file.unlink(missing_ok=True)
            except OSError:
                pass


class SshOutputLimitExceeded(RuntimeError):
    def __init__(self, content: str, received_bytes: int, max_bytes: int):
        super().__init__(f"SSH command output exceeded {max_bytes} bytes")
        self.content = content
        self.received_bytes = received_bytes
        self.max_bytes = max_bytes


def _sanitize_bounded_command_output(connection, output: str, command: str) -> str:
    normalize_linefeeds = getattr(connection, "normalize_linefeeds", None)
    if callable(normalize_linefeeds):
        output = normalize_linefeeds(output)
    if getattr(connection, "ansi_escape_codes", False):
        output = connection.strip_ansi_escape_codes(output)
    sanitize = getattr(connection, "_sanitize_output", None)
    if callable(sanitize):
        output = sanitize(
            output,
            strip_command=True,
            command_string=command,
            strip_prompt=True,
        )
    return output


def _send_command_with_output_limit(
    connection,
    command: str,
    read_timeout: int,
    max_bytes: int,
) -> tuple[str, int]:
    """Read a command directly from the channel and stop at the byte budget."""
    remote_channel = getattr(connection, "remote_conn", None)
    if not (
        remote_channel is not None
        and callable(getattr(remote_channel, "recv_ready", None))
        and callable(getattr(remote_channel, "recv", None))
        and callable(getattr(connection, "write_channel", None))
        and callable(getattr(connection, "find_prompt", None))
    ):
        # Test doubles and non-SSH compatibility implementations do not expose
        # a Paramiko channel. Production SSH sessions always take the bounded path.
        output = connection.send_command(command, read_timeout=read_timeout)
        captured = limit_raw_output(str(output), max_bytes)
        if captured.truncated:
            raise SshOutputLimitExceeded(captured.content, captured.byte_count, max_bytes)
        return captured.content, captured.byte_count

    encoding = getattr(connection, "encoding", "utf-8")
    decoder = codecs.getincrementaldecoder(encoding)(errors="strict")
    decoder_encoding = encoding
    fallback_encoding = "gb18030"
    received_wire_bytes = bytearray()
    prompt = connection.find_prompt()
    if not prompt:
        raise ValueError("Unable to find prompt")
    prompt_pattern = re.compile(
        rf"(?m)^[ \t]*{re.escape(prompt.strip())}[ \t]*(?:\r?\n)?\Z"
    )
    normalize_cmd = getattr(connection, "normalize_cmd", None)
    wire_command = normalize_cmd(command) if callable(normalize_cmd) else f"{command}\n"
    connection.write_channel(wire_command)

    parts: list[str] = []
    received_bytes = 0
    buffered = getattr(connection, "_read_buffer", "")
    if buffered:
        connection._read_buffer = ""
        buffered_bytes = buffered.encode(encoding)
        received_bytes = len(buffered_bytes)
        parts.append(buffered)
    deadline = time.monotonic() + read_timeout
    while time.monotonic() < deadline:
        if remote_channel.recv_ready():
            remaining = max(0, max_bytes - received_bytes)
            raw = remote_channel.recv(min(SSH_CHANNEL_READ_SIZE, remaining + 1))
            if isinstance(raw, str):
                raw = raw.encode(encoding)
            if not raw:
                raise RuntimeError("SSH channel stream closed by remote device")
            retained = raw[:remaining]
            received_bytes += len(raw)
            if retained:
                received_wire_bytes.extend(retained)
                try:
                    parts.append(decoder.decode(retained, final=False))
                except UnicodeDecodeError:
                    if decoder_encoding.lower().replace("_", "-") not in {"utf-8", "utf8"}:
                        raise
                    decoder_encoding = fallback_encoding
                    decoder = codecs.getincrementaldecoder(fallback_encoding)(errors="strict")
                    parts = [buffered] if buffered else []
                    parts.append(decoder.decode(bytes(received_wire_bytes), final=False))
            if received_bytes > max_bytes:
                raise SshOutputLimitExceeded("".join(parts), received_bytes, max_bytes)
            if prompt_pattern.search("".join(parts[-20:])):
                parts.append(decoder.decode(b"", final=True))
                output = "".join(parts)
                return _sanitize_bounded_command_output(connection, output, wire_command), received_bytes
        else:
            time.sleep(0.01)
    raise ReadTimeout(f"Pattern not detected: {prompt_pattern.pattern!r} in output")


async def observe_ssh_host_fingerprint(device: Device, connection_timeout: int = 10) -> ProtocolResult:
    return await asyncio.to_thread(_observe_ssh_host_fingerprint, device, connection_timeout)


def _counter(value: str | None) -> int | None:
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _snmp_oid_text(value) -> str:
    candidate = value
    get_oid = getattr(candidate, "getOid", None)
    if callable(get_oid):
        try:
            candidate = get_oid()
        except Exception:
            candidate = value
    as_tuple = getattr(candidate, "asTuple", None)
    if callable(as_tuple):
        try:
            components = as_tuple()
            if components and all(isinstance(component, int) for component in components):
                return ".".join(str(component) for component in components)
        except Exception:
            pass
    return str(candidate).lstrip(".")


def _snmp_scalar(value: str | None) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    normalized = "".join(text.lower().split())
    if not text or any(marker in normalized for marker in ("nosuchobject", "nosuchinstance", "endofmibview")):
        return None
    return text


def build_interface_metrics(columns: dict[str, dict[str, str]]) -> dict[str, Any]:
    interface_indexes = set(columns.get("oper_status", {}))
    indexes = sorted(interface_indexes, key=lambda value: int(value))

    def complete_counter_columns(*names: str) -> bool:
        return bool(interface_indexes) and all(
            name in columns
            and all(_counter(columns[name].get(index)) is not None for index in interface_indexes)
            for name in names
        )

    interfaces: list[dict[str, Any]] = []
    for index in indexes:
        status_value = columns.get("oper_status", {}).get(index)
        status_code = _counter(status_value)
        admin_code = _counter(columns.get("admin_status", {}).get(index))
        # RFC 2863 keeps operational and administrative state separate.
        status = {
            1: "up", 2: "down", 3: "testing", 4: "unknown", 5: "dormant",
            6: "notPresent", 7: "lowerLayerDown",
        }.get(status_code, "unknown")
        interface = {
            "index": int(index),
            "name": columns.get("name", {}).get(index, f"ifIndex {index}"),
            "oper_status": status,
            "oper_status_code": status_code,
            "admin_status": {1: "up", 2: "down", 3: "testing"}.get(admin_code, "unknown"),
            "admin_status_code": admin_code,
            "in_errors": _counter(columns.get("in_errors", {}).get(index)),
            "out_errors": _counter(columns.get("out_errors", {}).get(index)),
            "in_discards": _counter(columns.get("in_discards", {}).get(index)),
            "out_discards": _counter(columns.get("out_discards", {}).get(index)),
            "in_octets": _counter(columns.get("in_octets", {}).get(index)),
            "out_octets": _counter(columns.get("out_octets", {}).get(index)),
        }
        interfaces.append(interface)
    result = {
        "interface_collection_status": "success",
        "interfaces": interfaces,
        "interfaces_up": sum(interface["oper_status"] == "up" for interface in interfaces),
        "interfaces_down": sum(interface["oper_status"] in {"down", "lowerLayerDown"} for interface in interfaces),
        "interfaces_admin_down": sum(
            interface["admin_status"] == "down" and interface["oper_status"] in {"down", "lowerLayerDown"}
            for interface in interfaces
        ),
        "interfaces_unknown": sum(interface["oper_status"] == "unknown" for interface in interfaces),
    }
    ignored_indexes = {
        name: sorted(set(values) - interface_indexes, key=lambda value: int(value))
        for name, values in columns.items()
        if set(values) - interface_indexes
    }
    if ignored_indexes:
        details = "; ".join(
            f"{name}={','.join(indexes)}" for name, indexes in sorted(ignored_indexes.items())
        )
        result["interface_collection_warnings"] = {
            "ignored_indexes": f"IF-MIB columns returned rows outside ifOperStatus indexes: {details}"
        }
    if complete_counter_columns("in_errors", "out_errors"):
        result["interface_error_count"] = sum(
            value or 0
            for interface in interfaces
            for value in (interface["in_errors"], interface["out_errors"])
        )
    if complete_counter_columns("in_discards", "out_discards"):
        result["interface_discard_count"] = sum(
            value or 0
            for interface in interfaces
            for value in (interface["in_discards"], interface["out_discards"])
        )
    return result


async def _walk_column(
    snmp_engine: SnmpEngine,
    auth_data: CommunityData | UsmUserData,
    transport: UdpTransportTarget,
    oid: str,
    *,
    max_rows: int = MAX_SNMP_WALK_ROWS,
    max_seconds: float = MAX_SNMP_WALK_SECONDS,
) -> dict[str, str]:
    if max_rows <= 0 or max_seconds <= 0:
        raise ValueError("SNMP walk limits must be positive")
    values: dict[str, str] = {}
    root = tuple(int(component) for component in oid.lstrip(".").split("."))
    previous = root
    evidence = _ACTIVE_SNMP_EVIDENCE.get()
    evidence_section = evidence.start_section(f"SNMP walk {oid}") if evidence else None
    try:
        async with asyncio.timeout(max_seconds):
            # GETBULK is supported by the configured v2c/v3 transports. A small
            # batch bounds response size; tooBig alone triggers GETNEXT fallback.
            for use_bulk in (True, False):
                args = (snmp_engine, auth_data, transport, ContextData())
                start_oid = ".".join(map(str, previous))
                if use_bulk:
                    args += (0, SNMP_BULK_REPETITIONS)
                iterator = (bulk_walk_cmd if use_bulk else walk_cmd)(
                    *args, ObjectType(ObjectIdentity(start_oid)),
                    lexicographicMode=False, lookupMib=False,
                    maxRows=max_rows + 1, maxCalls=max_rows + 1,
                )
                async with aclosing(iterator):
                    async for error_indication, error_status, _, var_binds in iterator:
                        if error_indication:
                            raise RuntimeError(str(error_indication))
                        if error_status:
                            message = error_status.prettyPrint()
                            if use_bulk and message.lower() == "toobig":
                                # Restart GETNEXT at the column root: walk_cmd's
                                # subtree guard treats an instance as a leaf.
                                values.clear()
                                previous = root
                                break
                            raise RuntimeError(message)
                        for var_bind in var_binds:
                            if evidence and evidence_section:
                                evidence.append_var_bind(evidence_section, var_bind)
                            full_oid = tuple(int(part) for part in _snmp_oid_text(var_bind[0]).split("."))
                            if full_oid[:len(root)] != root or len(full_oid) <= len(root):
                                return values
                            value = _snmp_scalar(var_bind[1].prettyPrint())
                            if value is None:
                                return values
                            if full_oid <= previous:
                                raise RuntimeError("SNMP table returned a non-increasing OID")
                            previous = full_oid
                            if len(values) >= max_rows:
                                raise RuntimeError(f"SNMP table exceeded row limit ({max_rows})")
                            index = ".".join(map(str, full_oid[len(root):]))
                            values[index] = value
                    else:
                        return values
    except TimeoutError as exc:
        raise RuntimeError(f"SNMP table exceeded elapsed time limit ({max_seconds}s)") from exc
    return values


def _bounded_snmp_retries(retries: int) -> int:
    return min(MAX_SNMP_RETRIES, max(0, int(retries)))


async def _get_snmp_response(snmp_engine, auth_data, transport, oids, retries: int):
    # Keep pysnmp retransmission disabled. Only an actual timeout is retried;
    # an authentication/permission response must survive as the final result.
    retry_budget = _bounded_snmp_retries(retries)
    for attempt in range(retry_budget + 1):
        deadline = _ACTIVE_SNMP_DEADLINE.get()
        remaining = (
            MAX_SNMP_DEVICE_SECONDS
            if deadline is None
            else max(0.0, deadline - asyncio.get_running_loop().time())
        )
        if remaining <= 0:
            raise RuntimeError(
                f"SNMP device collection exceeded elapsed time limit ({MAX_SNMP_DEVICE_SECONDS}s)"
            )
        try:
            async with asyncio.timeout(remaining):
                response = await get_cmd(
                    snmp_engine, auth_data, transport, ContextData(),
                    *[ObjectType(ObjectIdentity(oid)) for oid in oids],
                )
        except TimeoutError as exc:
            raise RuntimeError(
                f"SNMP device collection exceeded elapsed time limit ({MAX_SNMP_DEVICE_SECONDS}s)"
            ) from exc
        except Exception as exc:
            if attempt < retry_budget and _snmp_error_is_timeout(str(exc)):
                continue
            raise
        indication, status, _, _ = response
        if not status and indication and _snmp_error_is_timeout(str(indication)) and attempt < retry_budget:
            continue
        return response


async def _walk_with_retry(snmp_engine, auth_data, transport, oid):
    # This is the only retry layer for table reads. The same elapsed budget
    # covers every attempt so a large or stalled table cannot multiply it.
    deadline = _ACTIVE_SNMP_DEADLINE.get()
    remaining = (
        MAX_SNMP_WALK_SECONDS
        if deadline is None
        else min(
            MAX_SNMP_WALK_SECONDS,
            max(0.0, deadline - asyncio.get_running_loop().time()),
        )
    )
    if remaining <= 0:
        return {}, f"SNMP device collection exceeded elapsed time limit ({MAX_SNMP_DEVICE_SECONDS}s)"
    try:
        async with asyncio.timeout(remaining):
            for attempt in range(_ACTIVE_SNMP_RETRY_BUDGET.get() + 1):
                try:
                    return await _walk_column(snmp_engine, auth_data, transport, oid), None
                except Exception as exc:
                    if _snmp_error_is_timeout(str(exc)) and attempt < _ACTIVE_SNMP_RETRY_BUDGET.get():
                        continue
                    return {}, str(exc)
    except TimeoutError:
        if deadline is not None and remaining < MAX_SNMP_WALK_SECONDS:
            return {}, f"SNMP device collection exceeded elapsed time limit ({MAX_SNMP_DEVICE_SECONDS}s)"
        return {}, f"SNMP table exceeded elapsed time limit ({MAX_SNMP_WALK_SECONDS}s)"


# HUAWEI-ENTITY-EXTENT-MIB. Only vendors whose CPU/memory MIB has been read
# from a real device are listed: an unverified OID would either time out or
# report a number the vendor never meant as a utilisation percentage.
# Verified on USG6300 and S5720-32X-EI-AC, where only the main control entity
# reports a non-zero value and the remaining slots stay at 0.
HUAWEI_CPU_OID = resolve_platform("Huawei").snmp_usage_oids["cpu"]
HUAWEI_MEMORY_OID = resolve_platform("Huawei").snmp_usage_oids["memory"]
# HH3C-ENTITY-EXT-MIB. hh3c is enterprise 25506 and hh3cCommon is {hh3c 2};
# the entity state entry publishes hh3cEntityExtCpuUsage(6) and
# hh3cEntityExtMemUsage(8), both Integer32 (0..100).
H3C_CPU_OID = resolve_platform("H3C").snmp_usage_oids["cpu"]
H3C_MEMORY_OID = resolve_platform("H3C").snmp_usage_oids["memory"]

# RUIJIE-PROCESS-MIB and RUIJIE-MEMORY-MIB. ruijie is enterprise 4881 and
# ruijieMgmt resolves through products(1).switch(1).switchMib(10).ruijieMgmt(2).
# Both objects use the Percent textual convention.
RUIJIE_CPU_OID = resolve_platform("Ruijie").snmp_usage_oids["cpu"]
RUIJIE_MEMORY_OID = resolve_platform("Ruijie").snmp_usage_oids["memory"]

# Vendors whose CPU and memory are published directly as a percentage, so the
# reading is the highest value in the column: idle entities answer 0.
VENDOR_USAGE_OIDS = {
    vendor_id: dict(resolve_platform(vendor_id).snmp_usage_oids)
    for vendor_id in VENDORS
    if resolve_platform(vendor_id).snmp_usage_profile == "entity-percent"
}

# CISCO-PROCESS-MIB and CISCO-MEMORY-POOL-MIB, resolved from the ASN.1 sources
# Cisco publishes in its own cisco-mibs repository. cpmCPUTotal5sec is
# deprecated there in favour of cpmCPUTotal5secRev, whose range is 0..100 and
# whose UNITS are "percent". The memory pool is published as byte gauges, so a
# percentage has to be derived from used/(used+free) rather than read directly.
CISCO_CPU_OID = resolve_platform("Cisco").snmp_usage_oids["cpu"]
CISCO_MEMORY_USED_OID = resolve_platform("Cisco").snmp_usage_oids["memory_used"]
CISCO_MEMORY_FREE_OID = resolve_platform("Cisco").snmp_usage_oids["memory_free"]
CISCO_MEMORY_VALID_OID = resolve_platform("Cisco").snmp_usage_oids["memory_valid"]

# BRIDGE-MIB::dot1dStpPortState. Unlike the CPU/memory columns this is a
# standard MIB rather than a vendor extension, so it is walked for every
# vendor. RFC 1493 defines the states as disabled(1), blocking(2),
# listening(3), learning(4), forwarding(5) and broken(6). Only blocking and
# broken keep a port out of service unexpectedly: disabled is an operator
# choice and listening/learning are transient states of a converging port.
STP_PORT_STATE_OID = "1.3.6.1.2.1.17.2.15.1.3"
STP_BLOCKING_STATE = "2"
STP_BROKEN_STATE = "6"
STP_FAULT_STATES = frozenset({STP_BLOCKING_STATE, STP_BROKEN_STATE})
STP_SNMP_REFERENCE = "BRIDGE-MIB::dot1dStpPortState"


def _entity_usage_percent(
    values: dict[str, str], valid_zero_indexes: set[str] | None = None,
) -> int | None:
    """Highest valid percentage; retain zero only with the profile's evidence."""
    readings = []
    for index, value in values.items():
        try:
            reading = int(value)
        except (TypeError, ValueError):
            continue
        if 0 <= reading <= 100 and (reading > 0 or valid_zero_indexes is None or index in valid_zero_indexes):
            readings.append(reading)
    return max(readings) if readings else None


def _sum_gauge(values: dict[str, str]) -> int | None:
    readings = []
    for value in values.values():
        try:
            readings.append(int(value))
        except (TypeError, ValueError):
            continue
    return sum(readings) if readings else None


async def _collect_cisco_usage(
    snmp_engine: SnmpEngine,
    auth_data: CommunityData | UsmUserData,
    transport: UdpTransportTarget,
) -> dict[str, Any]:
    async def walk(oid):
        return await _walk_with_retry(snmp_engine, auth_data, transport, oid)

    failed_reasons: dict[str, str] = {}
    cpu_values, cpu_error = await walk(CISCO_CPU_OID)
    cpu = _entity_usage_percent(cpu_values)
    if cpu_error:
        failed_reasons["cpu"] = cpu_error

    used_values, used_error = await walk(CISCO_MEMORY_USED_OID)
    free_values, free_error = await walk(CISCO_MEMORY_FREE_OID)
    valid_values, valid_error = await walk(CISCO_MEMORY_VALID_OID)
    common_indexes = set(used_values) & set(free_values)
    if valid_values:
        common_indexes &= {
            index for index, value in valid_values.items() if str(value).strip() == "1"
        }
    used = _sum_gauge({index: used_values[index] for index in common_indexes})
    free = _sum_gauge({index: free_values[index] for index in common_indexes})
    memory = None
    if used is not None and free is not None and used + free > 0:
        memory = round(used / (used + free) * 100)
    memory_errors = list(dict.fromkeys(filter(None, (used_error, free_error, valid_error))))
    if not common_indexes and not memory_errors:
        memory_errors.append("Cisco memory pool used/free values had no matching valid pool index")
    if memory_errors:
        failed_reasons["memory"] = "; ".join(memory_errors)
    return {"cpu": cpu, "memory": memory, "failed_reasons": failed_reasons}


async def _collect_usage_metrics(
    snmp_engine: SnmpEngine,
    auth_data: CommunityData | UsmUserData,
    transport: UdpTransportTarget,
    vendor: str,
    platform_id: str | None = None,
) -> dict[str, Any]:
    platform = _resolve_platform_for_collection(vendor, platform_id)
    if platform.snmp_usage_profile == "cisco-pools":
        return await _collect_cisco_usage(snmp_engine, auth_data, transport)
    oids = platform.snmp_usage_oids if platform.snmp_usage_profile == "entity-percent" else None
    if not oids:
        return {"cpu": None, "memory": None, "failed_reasons": {}}
    metrics: dict[str, Any] = {}
    failed_reasons: dict[str, str] = {}
    columns: dict[str, dict[str, str]] = {}
    for item, oid in oids.items():
        columns[item], error = await _walk_with_retry(snmp_engine, auth_data, transport, oid)
        if error:
            failed_reasons[item] = error
    valid_zero_indexes = None
    if platform.snmp_usage_zero_policy == "require-active-entity":
        # Huawei/H3C publish placeholder zero rows for unused slots. A valid
        # non-zero CPU or memory reading at the exact same entity index proves
        # the other zero belongs to an active entity, including composite IDs.
        valid_zero_indexes = {
            index for values in columns.values() for index, value in values.items()
            if (reading := _counter(value)) is not None and 0 < reading <= 100
        }
    for item, values in columns.items():
        metrics[item] = _entity_usage_percent(values, valid_zero_indexes)
    if valid_zero_indexes is not None and metrics.get("cpu") is None and any(
        _counter(value) == 0 for value in columns.get("cpu", {}).values()
    ):
        classes, error = await _walk_with_retry(snmp_engine, auth_data, transport, ENTITY_PHYSICAL_CLASS_OID)
        cpu_indexes = {index for index, value in classes.items() if _counter(value) == 12}
        metrics["cpu"] = _entity_usage_percent(columns["cpu"], valid_zero_indexes | cpu_indexes)
        if error:
            failed_reasons.setdefault("cpu", f"Zero CPU reading lacks entity evidence: {error}")
    metrics["failed_reasons"] = failed_reasons
    return metrics


def _stp_port_counts(values: dict[str, str]) -> tuple[int | None, int | None]:
    """Ports held out of forwarding by STP, or None when the MIB is absent.

    An empty walk means the device does not publish BRIDGE-MIB, which is not
    the same as a switch whose ports all forward.
    """
    if not values:
        return None, None
    return (
        sum(value in STP_FAULT_STATES for value in values.values()),
        sum(value == STP_BROKEN_STATE for value in values.values()),
    )


async def _collect_stp_metrics(
    snmp_engine: SnmpEngine,
    auth_data: CommunityData | UsmUserData,
    transport: UdpTransportTarget,
) -> dict[str, Any]:
    values, error = await _walk_with_retry(snmp_engine, auth_data, transport, STP_PORT_STATE_OID)
    discarding, broken = _stp_port_counts(values)
    return {
        "stp_discarding_ports": discarding,
        "stp_broken_ports": broken,
        "failed_reasons": {"stp": error} if error else {},
    }


async def _collect_interface_metrics(
    snmp_engine: SnmpEngine,
    auth_data: CommunityData | UsmUserData,
    transport: UdpTransportTarget,
) -> dict[str, Any]:
    columns: dict[str, dict[str, str]] = {}
    warnings: dict[str, str] = {}
    for name, oid in INTERFACE_OIDS.items():
        values, error = await _walk_with_retry(snmp_engine, auth_data, transport, oid)
        if not error and any(
            not index.isdigit() or not 1 <= int(index) <= 2147483647 for index in values
        ):
            error = "IF-MIB column returned an invalid ifIndex"
        if error:
            warnings[name] = error
        else:
            columns[name] = values
    if not columns.get("oper_status"):
        return {
            "interface_collection_status": "data_fetch_failed",
            "interface_collection_message": warnings.get("oper_status", "IF-MIB returned no interface status rows"),
            "interface_collection_warnings": warnings,
            "interfaces": [],
        }
    interface_indexes = set(columns["oper_status"])
    for name in ("in_errors", "out_errors", "in_discards", "out_discards"):
        values = columns.get(name, {})
        if any(_counter(values.get(index)) is None for index in interface_indexes):
            warnings.setdefault(name, "IF-MIB column did not return valid rows for all interfaces")
    if any(
        _counter(columns.get("admin_status", {}).get(index)) not in {1, 2, 3}
        for index in interface_indexes
    ):
        warnings.setdefault("admin_status", "IF-MIB column did not return valid administrative state for all interfaces")
    metrics = build_interface_metrics(columns)
    if warnings:
        metrics["interface_collection_warnings"] = {
            **metrics.get("interface_collection_warnings", {}),
            **warnings,
        }
    return metrics


def _snmp_user(device: Device) -> UsmUserData:
    level = device.snmp_v3_security_level or "noAuthNoPriv"
    username = device.snmp_v3_username or ""
    if level == "noAuthNoPriv":
        return UsmUserData(username, authProtocol=usmNoAuthProtocol, privProtocol=usmNoPrivProtocol)
    auth_key = decrypt_secret(device.snmp_v3_auth_key)
    auth_protocol = AUTH_PROTOCOLS[device.snmp_v3_auth_protocol or "SHA"]
    if level == "authNoPriv":
        return UsmUserData(username, auth_key, authProtocol=auth_protocol, privProtocol=usmNoPrivProtocol)
    return UsmUserData(
        username,
        auth_key,
        decrypt_secret(device.snmp_v3_priv_key),
        authProtocol=auth_protocol,
        privProtocol=PRIV_PROTOCOLS[device.snmp_v3_priv_protocol or "AES"],
    )


@_credential_errors_as_result("SNMP")
async def test_snmp_connectivity(device: Device, timeout: int = 3, retries: int = 1) -> ProtocolResult:
    _resolve_platform_for_collection(device.vendor, getattr(device, "platform_id", None))
    if device.snmp_version == "v2c":
        community = decrypt_secret(device.snmp_community)
        if not community:
            return ProtocolResult(False, "SNMP", failure_stage="credentials", message="SNMPv2c Community not configured")
        auth_data = CommunityData(community, mpModel=1)
    elif device.snmp_version == "v3" and device.snmp_v3_username:
        auth_data = _snmp_user(device)
    else:
        return ProtocolResult(False, "SNMP", failure_stage="credentials", message="SNMP credentials not configured")

    snmp_engine = SnmpEngine()
    deadline_token = _ACTIVE_SNMP_DEADLINE.set(
        asyncio.get_running_loop().time() + MAX_SNMP_DEVICE_SECONDS
    )
    try:
        transport = await UdpTransportTarget.create(
            (device.ip, device.snmp_port), timeout=timeout, retries=0
        )
        error_indication, error_status, _, var_binds = await _get_snmp_response(
            snmp_engine, auth_data, transport,
            (SYSTEM_OIDS["sys_name"], SYSTEM_OIDS["interface_count"]), retries,
        )
    except Exception as exc:
        return ProtocolResult(False, "SNMP", failure_stage="connection", message=str(exc))
    finally:
        _ACTIVE_SNMP_DEADLINE.reset(deadline_token)
        snmp_engine.close_dispatcher()

    if error_indication:
        message = str(error_indication)
        auth_failed = _snmp_error_is_authentication(message)
        return ProtocolResult(
            False,
            "SNMP",
            failure_stage="authentication" if auth_failed else "connection",
            message=message,
            authentication_failed=auth_failed,
        )
    if error_status:
        message = error_status.prettyPrint()
        auth_failed = _snmp_error_is_authentication(message)
        return ProtocolResult(
            False,
            "SNMP",
            failure_stage="authentication" if auth_failed else "collection",
            message=message,
            authentication_failed=auth_failed,
        )
    usable_probe_oids = {
        _snmp_oid_text(var_bind[0])
        for var_bind in var_binds
        if _snmp_scalar(var_bind[1].prettyPrint()) is not None
    }
    required_probe_oids = (
        SYSTEM_OIDS["sys_name"],
        SYSTEM_OIDS["interface_count"],
    )
    if any(oid not in usable_probe_oids for oid in required_probe_oids):
        return ProtocolResult(
            False,
            "SNMP",
            failure_stage="collection",
            message="SNMP probe requires usable sysName and ifNumber values",
        )
    return ProtocolResult(True, "SNMP")


@_credential_errors_as_result("SNMP")
async def collect_snmp(device: Device, timeout: int = 3, retries: int = 1) -> ProtocolResult:
    platform = _resolve_platform_for_collection(device.vendor, getattr(device, "platform_id", None))
    if device.snmp_version == "v2c":
        community = decrypt_secret(device.snmp_community)
        if not community:
            return ProtocolResult(False, "SNMP", failure_stage="credentials", message="SNMPv2c Community not configured")
        auth_data = CommunityData(community, mpModel=1)
    elif device.snmp_version == "v3" and device.snmp_v3_username:
        auth_data = _snmp_user(device)
    else:
        return ProtocolResult(False, "SNMP", failure_stage="credentials", message="SNMP credentials not configured")

    snmp_engine = SnmpEngine()
    deadline_token = _ACTIVE_SNMP_DEADLINE.set(
        asyncio.get_running_loop().time() + MAX_SNMP_DEVICE_SECONDS
    )
    retry_token = _ACTIVE_SNMP_RETRY_BUDGET.set(_bounded_snmp_retries(retries))
    try:
        try:
            transport = await UdpTransportTarget.create(
                (device.ip, device.snmp_port), timeout=timeout, retries=0
            )
            error_indication, error_status, error_index, var_binds = await _get_snmp_response(
                snmp_engine, auth_data, transport, tuple(SYSTEM_OIDS.values()), retries,
            )
        except Exception as exc:
            return ProtocolResult(False, "SNMP", failure_stage="connection", message=str(exc))

        if error_indication:
            message = str(error_indication)
            auth_failed = _snmp_error_is_authentication(message)
            return ProtocolResult(False, "SNMP", failure_stage="authentication" if auth_failed else "connection", message=message, authentication_failed=auth_failed)
        if error_status:
            message = error_status.prettyPrint()
            auth_failed = _snmp_error_is_authentication(message)
            return ProtocolResult(
                False,
                "SNMP",
                failure_stage="authentication" if auth_failed else "collection",
                message=message,
                authentication_failed=auth_failed,
            )

        evidence = _SnmpRawEvidence(
            MAX_SINGLE_RAW_OUTPUT_BYTES,
            MAX_DEVICE_RAW_OUTPUT_BYTES,
        )
        scalar_section = evidence.start_section("SNMP scalar")
        for var_bind in var_binds:
            evidence.append_var_bind(scalar_section, var_bind)

        response_values = {
            _snmp_oid_text(var_bind[0]): var_bind[1].prettyPrint() for var_bind in var_binds
        }
        values = {key: response_values.get(oid) for key, oid in SYSTEM_OIDS.items()}
        hostname = _snmp_scalar(values.get("sys_name"))
        model = _snmp_scalar(values.get("sys_description"))
        uptime_ticks = _counter(_snmp_scalar(values.get("uptime_ticks")))
        interface_count = _counter(_snmp_scalar(values.get("interface_count")))
        # Read optional identity independently so a restricted sysObjectID does
        # not reject an otherwise successful system probe. It gets no retries.
        sys_object_id = None
        try:
            identity_error, identity_status, _, identity_binds = await _get_snmp_response(
                snmp_engine, auth_data, transport, (SYS_OBJECT_ID_OID,), 0,
            )
            if identity_error or identity_status:
                message = str(identity_error) if identity_error else identity_status.prettyPrint()
                section = evidence.start_section("SNMP sysObjectID")
                evidence._append(section, f"Collection error: {message}\n")
            else:
                for var_bind in identity_binds:
                    if _snmp_oid_text(var_bind[0]) == SYS_OBJECT_ID_OID:
                        section = evidence.start_section("SNMP sysObjectID")
                        evidence.append_var_bind(section, var_bind)
                        value = var_bind[1]
                        candidate = _snmp_scalar(
                            _snmp_oid_text(value)
                            if callable(getattr(value, "asTuple", None)) or callable(getattr(value, "getOid", None))
                            else value.prettyPrint()
                        )
                        if candidate and all(part.isdigit() for part in candidate.lstrip(".").split(".")):
                            sys_object_id = candidate.lstrip(".")
        except Exception as exc:
            section = evidence.start_section("SNMP sysObjectID")
            evidence._append(section, f"Collection error: {exc}\n")
        failed_checks: set[str] = set()
        failed_reasons: dict[str, str] = {}
        if not hostname or not model:
            failed_checks.add("device_information")
            failed_reasons["device_information"] = "SNMP sysName/sysDescr returned an invalid value"
        if uptime_ticks is None:
            failed_checks.add("uptime")
            failed_reasons["uptime"] = "SNMP sysUpTime returned an invalid value"
        evidence_token = _ACTIVE_SNMP_EVIDENCE.set(evidence)
        try:
            interface_metrics = await _collect_interface_metrics(snmp_engine, auth_data, transport)
            if interface_count is None and interface_metrics.get("interface_collection_status") == "success":
                interfaces = interface_metrics.get("interfaces")
                if isinstance(interfaces, list) and interfaces:
                    interface_count = len(interfaces)
            if interface_count is None and interface_metrics.get("interface_collection_status") == "success":
                interface_metrics = {
                    **interface_metrics,
                    "interface_collection_status": "data_fetch_failed",
                    "interface_collection_message": "SNMP interface count is unavailable and IF-MIB returned no rows",
                }
            if interface_metrics.get("interface_collection_status") != "success":
                failed_checks.add("interfaces")
                failed_reasons["interfaces"] = str(
                    interface_metrics.get("interface_collection_message") or "SNMP interface status could not be collected"
                )
            interface_warnings = interface_metrics.get("interface_collection_warnings")
            if isinstance(interface_warnings, dict) and "admin_status" in interface_warnings:
                failed_checks.add("interfaces")
                failed_reasons.setdefault(
                    "interfaces", "IF-MIB administrative status column unavailable: ifAdminStatus"
                )
            unavailable_error_columns = [
                label
                for key, label in (
                    ("in_errors", "ifInErrors"),
                    ("out_errors", "ifOutErrors"),
                    ("in_discards", "ifInDiscards"),
                    ("out_discards", "ifOutDiscards"),
                )
                if isinstance(interface_warnings, dict) and key in interface_warnings
            ]
            if unavailable_error_columns:
                failed_checks.add("interface_errors")
                failed_reasons["interface_errors"] = (
                    "IF-MIB error counter columns unavailable: " + ", ".join(unavailable_error_columns)
                )
            usage_metrics = await _collect_usage_metrics(
                snmp_engine, auth_data, transport, device.vendor, platform.id
            )
            if "stp" in platform.not_applicable_by_device_type.get(device.device_type, frozenset()):
                stp_metrics = {
                    "stp_discarding_ports": None,
                    "stp_broken_ports": None,
                    "failed_reasons": {},
                }
            else:
                stp_metrics = await _collect_stp_metrics(snmp_engine, auth_data, transport)
        finally:
            _ACTIVE_SNMP_EVIDENCE.reset(evidence_token)
        for item_id, reason in {
            **usage_metrics.get("failed_reasons", {}),
            **stp_metrics.get("failed_reasons", {}),
        }.items():
            failed_checks.add(item_id)
            failed_reasons[item_id] = reason
        item_references = (
            {"stp": STP_SNMP_REFERENCE}
            if stp_metrics["stp_discarding_ports"] is not None or "stp" in failed_checks
            else {}
        )
        raw_capture = evidence.capture()
        # sysDescr usually carries the exact model and release; reading them
        # here gives SNMP-only devices an identity and spares an SSH session.
        snmp_identity = parse_snmp_identity(platform.parser_profile, model)
        return ProtocolResult(
            True,
            "SNMP",
            metrics={
                **({"hostname": hostname} if hostname is not None else {}),
                **({"model": model} if model is not None else {}),
                **({"sys_description": model} if snmp_identity else {}),
                **snmp_identity,
                **({"sys_object_id": sys_object_id} if sys_object_id is not None else {}),
                **({"uptime": _format_uptime(uptime_ticks)} if uptime_ticks is not None else {}),
                **({"interfaces_total": interface_count} if interface_count is not None else {}),
                "cpu": usage_metrics["cpu"],
                "memory": usage_metrics["memory"],
                "stp_discarding_ports": stp_metrics["stp_discarding_ports"],
                "stp_broken_ports": stp_metrics.get("stp_broken_ports"),
                **interface_metrics,
            },
            raw_output=raw_capture.content,
            raw_output_status=raw_capture.status,
            raw_output_bytes=raw_capture.byte_count,
            failed_checks=failed_checks,
            failed_reasons=failed_reasons,
            item_references=item_references,
        )
    finally:
        _ACTIVE_SNMP_RETRY_BUDGET.reset(retry_token)
        _ACTIVE_SNMP_DEADLINE.reset(deadline_token)
        snmp_engine.close_dispatcher()


@_credential_errors_as_result("SSH")
def _collect_ssh_sync(
    device: Device,
    inspection_level: str,
    connection_timeout: int = 10,
    command_timeout: int = 30,
    required_checks: set[str] | None = None,
) -> ProtocolResult:
    username = device.ssh_username
    password = decrypt_secret(device.ssh_password)
    if not (username and password):
        return ProtocolResult(False, "SSH", failure_stage="credentials", message="SSH credentials not configured")
    fingerprint_check = _verify_ssh_host_fingerprint(device, connection_timeout)
    if not fingerprint_check.ok:
        return fingerprint_check
    platform = _resolve_platform_for_collection(device.vendor, getattr(device, "platform_id", None))
    commands = ssh_commands_for_checks(
        device.vendor,
        inspection_level,
        required_checks,
        platform.id,
        include_fallbacks=True,
    )
    command_items = platform.command_items
    command_specs = {spec.command: spec for spec in platform.commands}
    covered_checks = {item for command in commands for item in command_items.get(command, set())}
    unsupported_checks = set(required_checks or ()) - covered_checks
    item_references: dict[str, str] = {}
    if not commands:
        return ProtocolResult(
            True,
            "SSH",
            unsupported_checks=unsupported_checks,
            item_references=item_references,
        )
    config = {
        "device_type": platform.netmiko_type,
        "host": device.ip,
        "port": device.ssh_port,
        "username": username,
        "password": password,
        "secret": decrypt_secret(device.enable_password),
        "conn_timeout": connection_timeout,
        "banner_timeout": connection_timeout,
        "auth_timeout": connection_timeout,
        "disabled_algorithms": NETMIKO_DISABLED_ALGORITHMS,
    }
    try:
        with _pinned_netmiko_connection(config, fingerprint_check) as connection:
            if config["secret"]:
                connection.enable()
            output_parts: list[str] = []
            collected_bytes = 0
            successful_commands = 0
            failed_checks: set[str] = set()
            failed_reasons: dict[str, str] = {}
            for command in commands:
                relevant_items = {
                    item
                    for item in command_items.get(command, set())
                    if required_checks is None or item in required_checks
                }
                spec = command_specs[command]
                if spec.is_fallback and output_parts:
                    partial = parse_ssh_output(
                        platform.parser_profile,
                        "\n\n".join(output_parts),
                        command_items,
                    )
                    parsed_items = {
                        mapped_item
                        for metric_key, mapped_item in METRIC_ITEMS.items()
                        if partial.metrics.get(metric_key) is not None
                    }
                    if partial.metrics.get("interfaces_total") is not None:
                        parsed_items.add("interfaces")
                    if relevant_items and relevant_items <= parsed_items:
                        continue
                for item_id in relevant_items:
                    item_references[item_id] = command
                prefix = f"$ {command}\n"
                prefix_bytes = len(prefix.encode("utf-8"))
                single_remaining = max(0, MAX_SINGLE_RAW_OUTPUT_BYTES - prefix_bytes)
                device_remaining = max(
                    0,
                    MAX_DEVICE_RAW_OUTPUT_BYTES - collected_bytes - prefix_bytes,
                )
                receive_limit = min(single_remaining, device_remaining)
                try:
                    command_output, _ = _send_command_with_output_limit(
                        connection,
                        command,
                        command_timeout,
                        receive_limit,
                    )
                except NetmikoAuthenticationException:
                    raise
                except ReadTimeout as exc:
                    if not successful_commands:
                        raise
                    # The channel state is unknown after a missed prompt, so no
                    # further command is sent. Earlier commands already returned
                    # to the prompt and their output is still valid evidence.
                    reason = f"SSH command timed out before the device prompt was received: {exc}"
                    pending = commands[commands.index(command):]
                    for pending_command in pending:
                        for item_id in command_items.get(pending_command, set()):
                            if required_checks is None or item_id in required_checks:
                                failed_checks.add(item_id)
                                failed_reasons.setdefault(item_id, reason)
                    break
                except UnicodeDecodeError:
                    raise
                except SshOutputLimitExceeded as exc:
                    retained = limit_raw_output(prefix + exc.content, min(
                        MAX_SINGLE_RAW_OUTPUT_BYTES,
                        max(0, MAX_DEVICE_RAW_OUTPUT_BYTES - collected_bytes),
                    ))
                    output_parts.append(retained.content)
                    device_limit_hit = device_remaining < single_remaining
                    limit = MAX_DEVICE_RAW_OUTPUT_BYTES if device_limit_hit else MAX_SINGLE_RAW_OUTPUT_BYTES
                    scope = "device" if device_limit_hit else "command"
                    return ProtocolResult(
                        False,
                        "SSH",
                        raw_output="\n\n".join(output_parts),
                        raw_output_status="truncated",
                        raw_output_bytes=collected_bytes + prefix_bytes + exc.received_bytes,
                        failure_stage="output_truncated",
                        message=f"SSH {scope} output exceeded {limit} bytes",
                        host_fingerprint=fingerprint_check.host_fingerprint,
                        host_key_algorithm=fingerprint_check.host_key_algorithm,
                        unsupported_checks=unsupported_checks,
                        item_references=item_references,
                    )
                except Exception as exc:
                    reason = f"SSH command failed: {type(exc).__name__}: {exc}"
                    for item_id in command_items.get(command, set()):
                        if required_checks is None or item_id in required_checks:
                            failed_checks.add(item_id)
                            failed_reasons.setdefault(item_id, reason)
                    continue
                captured = limit_raw_output(prefix + command_output, MAX_SINGLE_RAW_OUTPUT_BYTES)
                if captured.truncated or collected_bytes + captured.byte_count > MAX_DEVICE_RAW_OUTPUT_BYTES:
                    remaining = max(0, MAX_DEVICE_RAW_OUTPUT_BYTES - collected_bytes)
                    output_parts.append(limit_raw_output(captured.content, remaining).content)
                    device_limit_hit = collected_bytes + captured.byte_count > MAX_DEVICE_RAW_OUTPUT_BYTES
                    limit = MAX_DEVICE_RAW_OUTPUT_BYTES if device_limit_hit else MAX_SINGLE_RAW_OUTPUT_BYTES
                    scope = "device" if device_limit_hit else "command"
                    return ProtocolResult(
                        False,
                        "SSH",
                        raw_output="\n\n".join(output_parts),
                        raw_output_status="truncated",
                        raw_output_bytes=collected_bytes + captured.byte_count,
                        failure_stage="output_truncated",
                        message=f"SSH {scope} output exceeded {limit} bytes",
                        host_fingerprint=fingerprint_check.host_fingerprint,
                        host_key_algorithm=fingerprint_check.host_key_algorithm,
                        unsupported_checks=unsupported_checks,
                        item_references=item_references,
                    )
                output_parts.append(captured.content)
                collected_bytes += captured.byte_count
                successful_commands += 1
            output = "\n\n".join(output_parts)
    except NetmikoAuthenticationException as exc:
        return ProtocolResult(False, "SSH", failure_stage="authentication", message=str(exc), authentication_failed=True, host_fingerprint=fingerprint_check.host_fingerprint, host_key_algorithm=fingerprint_check.host_key_algorithm)
    except ReadTimeout as exc:
        return ProtocolResult(False, "SSH", failure_stage="collection", message=f"SSH command timed out before the device prompt was received: {exc}", host_fingerprint=fingerprint_check.host_fingerprint, host_key_algorithm=fingerprint_check.host_key_algorithm)
    except UnicodeDecodeError as exc:
        return ProtocolResult(False, "SSH", failure_stage="collection", message=f"SSH command output decoding failed: {exc}", host_fingerprint=fingerprint_check.host_fingerprint, host_key_algorithm=fingerprint_check.host_key_algorithm)
    except NetmikoTimeoutException as exc:
        return ProtocolResult(False, "SSH", failure_stage="connection", message=str(exc), host_fingerprint=fingerprint_check.host_fingerprint, host_key_algorithm=fingerprint_check.host_key_algorithm)
    except Exception as exc:
        authentication_failed = _ssh_error_is_authentication(str(exc))
        return ProtocolResult(
            False,
            "SSH",
            failure_stage="authentication" if authentication_failed else "collection",
            message=str(exc),
            authentication_failed=authentication_failed,
            host_fingerprint=fingerprint_check.host_fingerprint,
            host_key_algorithm=fingerprint_check.host_key_algorithm,
        )
    parsed = parse_ssh_output(platform.parser_profile, output, command_items)
    metrics = parsed.metrics
    item_references.update(parsed.item_references)
    unsupported_checks |= parsed.unsupported_checks
    collected_items = {
        mapped_item
        for metric_key, mapped_item in METRIC_ITEMS.items()
        if metrics.get(metric_key) is not None
    }
    unsupported_checks -= collected_items
    failed_checks -= {
        item_id for item_id in failed_checks if any(
            metrics.get(metric_key) is not None
            for metric_key, mapped_item in METRIC_ITEMS.items()
            if mapped_item == item_id
        )
    }
    failed_reasons = {
        item_id: reason for item_id, reason in failed_reasons.items() if item_id in failed_checks
    }
    if not successful_commands:
        first_reason = next(iter(failed_reasons.values()), "No SSH inspection command completed")
        return ProtocolResult(
            False,
            "SSH",
            failure_stage="collection",
            message=first_reason,
            host_fingerprint=fingerprint_check.host_fingerprint,
            host_key_algorithm=fingerprint_check.host_key_algorithm,
            failed_checks=failed_checks,
            failed_reasons=failed_reasons,
            item_references=item_references,
        )
    if not metrics and not unsupported_checks:
        return ProtocolResult(
            False,
            "SSH",
            raw_output=output,
            failure_stage="parsing",
            message="No supported inspection metrics could be parsed from SSH output",
            host_fingerprint=fingerprint_check.host_fingerprint,
            host_key_algorithm=fingerprint_check.host_key_algorithm,
            raw_output_bytes=collected_bytes,
            unsupported_checks=unsupported_checks,
            unsupported_reasons=parsed.unsupported_reasons,
            failed_checks=failed_checks,
            failed_reasons=failed_reasons,
            item_messages=parsed.item_messages,
            item_references=item_references,
        )
    metrics["raw_collected"] = True
    return ProtocolResult(
        True,
        "SSH",
        raw_output=output,
        metrics=metrics,
        host_fingerprint=fingerprint_check.host_fingerprint,
        host_key_algorithm=fingerprint_check.host_key_algorithm,
        raw_output_bytes=collected_bytes,
        unsupported_checks=unsupported_checks,
        unsupported_reasons=parsed.unsupported_reasons,
        failed_checks=failed_checks,
        failed_reasons=failed_reasons,
        item_messages=parsed.item_messages,
        item_references=item_references,
    )


async def collect_ssh(
    device: Device,
    inspection_level: str,
    connection_timeout: int = 10,
    command_timeout: int = 30,
    required_checks: set[str] | None = None,
) -> ProtocolResult:
    return await asyncio.to_thread(
        _collect_ssh_sync,
        device,
        inspection_level,
        connection_timeout,
        command_timeout,
        required_checks,
    )


@_credential_errors_as_result("SSH")
def _test_ssh_connectivity_sync(device: Device, connection_timeout: int = 10) -> ProtocolResult:
    username = device.ssh_username
    password = decrypt_secret(device.ssh_password)
    if not (username and password):
        return ProtocolResult(False, "SSH", failure_stage="credentials", message="SSH credentials not configured")
    fingerprint_check = _verify_ssh_host_fingerprint(device, connection_timeout)
    if not fingerprint_check.ok:
        return fingerprint_check
    platform = _resolve_platform_for_collection(device.vendor, getattr(device, "platform_id", None))
    config = {
        "device_type": platform.netmiko_type,
        "host": device.ip,
        "port": device.ssh_port,
        "username": username,
        "password": password,
        "secret": decrypt_secret(device.enable_password),
        "conn_timeout": connection_timeout,
        "banner_timeout": connection_timeout,
        "auth_timeout": connection_timeout,
        "disabled_algorithms": NETMIKO_DISABLED_ALGORITHMS,
    }
    try:
        with _pinned_netmiko_connection(config, fingerprint_check) as connection:
            if config["secret"]:
                connection.enable()
            connection.find_prompt()
    except NetmikoAuthenticationException as exc:
        return ProtocolResult(False, "SSH", failure_stage="authentication", message=str(exc), authentication_failed=True, host_fingerprint=fingerprint_check.host_fingerprint, host_key_algorithm=fingerprint_check.host_key_algorithm)
    except NetmikoTimeoutException as exc:
        return ProtocolResult(False, "SSH", failure_stage="connection", message=str(exc), host_fingerprint=fingerprint_check.host_fingerprint, host_key_algorithm=fingerprint_check.host_key_algorithm)
    except Exception as exc:
        authentication_failed = _ssh_error_is_authentication(str(exc))
        return ProtocolResult(
            False,
            "SSH",
            failure_stage="authentication" if authentication_failed else "connection",
            message=str(exc),
            authentication_failed=authentication_failed,
            host_fingerprint=fingerprint_check.host_fingerprint,
            host_key_algorithm=fingerprint_check.host_key_algorithm,
        )
    return ProtocolResult(True, "SSH", host_fingerprint=fingerprint_check.host_fingerprint, host_key_algorithm=fingerprint_check.host_key_algorithm)


async def test_ssh_connectivity(device: Device, connection_timeout: int = 10) -> ProtocolResult:
    return await asyncio.to_thread(_test_ssh_connectivity_sync, device, connection_timeout)


@_credential_errors_as_result("SSH")
async def backup_configuration(
    device: Device,
    connection_timeout: int = 10,
    command_timeout: int = 60,
) -> ProtocolResult:
    username = device.ssh_username
    password = decrypt_secret(device.ssh_password)
    if not (username and password):
        return ProtocolResult(False, "SSH", failure_stage="credentials", message="SSH credentials not configured")
    fingerprint_check = await asyncio.to_thread(_verify_ssh_host_fingerprint, device, connection_timeout)
    if not fingerprint_check.ok:
        return fingerprint_check
    platform = _resolve_platform_for_collection(device.vendor, getattr(device, "platform_id", None))
    command = platform.backup_command
    config = {
        "device_type": platform.netmiko_type,
        "host": device.ip,
        "port": device.ssh_port,
        "username": username,
        "password": password,
        "secret": decrypt_secret(device.enable_password),
        "conn_timeout": connection_timeout,
        "banner_timeout": connection_timeout,
        "auth_timeout": connection_timeout,
        "disabled_algorithms": NETMIKO_DISABLED_ALGORITHMS,
    }
    try:
        def _run() -> tuple[str, int]:
            with _pinned_netmiko_connection(config, fingerprint_check) as connection:
                if config["secret"]:
                    connection.enable()
                return _send_command_with_output_limit(
                    connection,
                    command,
                    command_timeout,
                    MAX_BACKUP_OUTPUT_BYTES,
                )
        output, received_bytes = await asyncio.to_thread(_run)
    except SshOutputLimitExceeded as exc:
        return ProtocolResult(
            False,
            "SSH",
            raw_output=exc.content,
            raw_output_status="truncated",
            raw_output_bytes=exc.received_bytes,
            failure_stage="output_truncated",
            message=f"SSH backup output exceeded {MAX_BACKUP_OUTPUT_BYTES} bytes",
            host_fingerprint=fingerprint_check.host_fingerprint,
            host_key_algorithm=fingerprint_check.host_key_algorithm,
        )
    except NetmikoAuthenticationException as exc:
        return ProtocolResult(False, "SSH", failure_stage="authentication", message=str(exc), authentication_failed=True, host_fingerprint=fingerprint_check.host_fingerprint, host_key_algorithm=fingerprint_check.host_key_algorithm)
    except ReadTimeout as exc:
        return ProtocolResult(False, "SSH", failure_stage="collection", message=f"SSH command timed out before the device prompt was received: {exc}", host_fingerprint=fingerprint_check.host_fingerprint, host_key_algorithm=fingerprint_check.host_key_algorithm)
    except UnicodeDecodeError as exc:
        return ProtocolResult(False, "SSH", failure_stage="collection", message=f"SSH command output decoding failed: {exc}", host_fingerprint=fingerprint_check.host_fingerprint, host_key_algorithm=fingerprint_check.host_key_algorithm)
    except NetmikoTimeoutException as exc:
        return ProtocolResult(False, "SSH", failure_stage="connection", message=str(exc), host_fingerprint=fingerprint_check.host_fingerprint, host_key_algorithm=fingerprint_check.host_key_algorithm)
    except Exception as exc:
        authentication_failed = _ssh_error_is_authentication(str(exc))
        return ProtocolResult(
            False,
            "SSH",
            failure_stage="authentication" if authentication_failed else "collection",
            message=str(exc),
            authentication_failed=authentication_failed,
            host_fingerprint=fingerprint_check.host_fingerprint,
            host_key_algorithm=fingerprint_check.host_key_algorithm,
        )
    validation_error = validate_configuration(output, device.vendor, platform.id)
    if validation_error:
        return ProtocolResult(False, "SSH", failure_stage="validation", message=validation_error, host_fingerprint=fingerprint_check.host_fingerprint, host_key_algorithm=fingerprint_check.host_key_algorithm)
    return ProtocolResult(
        True,
        "SSH",
        raw_output=output,
        raw_output_bytes=received_bytes,
        host_fingerprint=fingerprint_check.host_fingerprint,
        host_key_algorithm=fingerprint_check.host_key_algorithm,
    )
