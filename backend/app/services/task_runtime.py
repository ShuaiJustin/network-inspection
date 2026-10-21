from dataclasses import dataclass
from threading import Lock

from app.models.device import Device


@dataclass(frozen=True)
class TaskRuntimeDevice:
    id: int
    name: str | None
    ip: str
    vendor: str
    platform_id: str | None
    device_type: str
    group_name: str | None
    note: str | None
    ssh_port: int
    ssh_username: str | None
    ssh_password: str | None
    enable_password: str | None
    ssh_host_fingerprint: str | None
    snmp_version: str | None
    snmp_port: int
    snmp_community: str | None
    snmp_v3_username: str | None
    snmp_v3_security_level: str | None
    snmp_v3_auth_protocol: str | None
    snmp_v3_auth_key: str | None
    snmp_v3_priv_protocol: str | None
    snmp_v3_priv_key: str | None

    @classmethod
    def from_device(cls, device: Device) -> "TaskRuntimeDevice":
        return cls(
            id=device.id,
            name=device.name,
            ip=device.ip,
            vendor=device.vendor,
            platform_id=device.platform_id,
            device_type=device.device_type,
            group_name=device.group_name,
            note=device.note,
            ssh_port=device.ssh_port,
            ssh_username=device.ssh_username,
            ssh_password=device.ssh_password,
            enable_password=device.enable_password,
            ssh_host_fingerprint=device.ssh_host_fingerprint,
            snmp_version=device.snmp_version,
            snmp_port=device.snmp_port,
            snmp_community=device.snmp_community,
            snmp_v3_username=device.snmp_v3_username,
            snmp_v3_security_level=device.snmp_v3_security_level,
            snmp_v3_auth_protocol=device.snmp_v3_auth_protocol,
            snmp_v3_auth_key=device.snmp_v3_auth_key,
            snmp_v3_priv_protocol=device.snmp_v3_priv_protocol,
            snmp_v3_priv_key=device.snmp_v3_priv_key,
        )


_runtime_devices: dict[int, dict[int, TaskRuntimeDevice]] = {}
_runtime_devices_lock = Lock()


def capture_task_devices(task_id: int, devices: list[Device]) -> None:
    snapshots = {device.id: TaskRuntimeDevice.from_device(device) for device in devices}
    with _runtime_devices_lock:
        _runtime_devices[task_id] = snapshots


def get_task_devices(task_id: int) -> dict[int, TaskRuntimeDevice] | None:
    with _runtime_devices_lock:
        devices = _runtime_devices.get(task_id)
        return dict(devices) if devices is not None else None


def discard_task_devices(task_id: int) -> None:
    with _runtime_devices_lock:
        _runtime_devices.pop(task_id, None)
