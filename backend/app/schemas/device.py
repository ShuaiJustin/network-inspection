from datetime import datetime
from ipaddress import IPv4Address
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.services.vendor_registry import (
    normalize_device_type,
    normalize_vendor,
    resolve_platform,
)


SnmpVersion = Literal["v2c", "v3"]
CollectionMode = Literal["auto", "snmp", "ssh"]


def _normalize_choice(value, aliases):
    if not isinstance(value, str):
        return value
    text = value.strip()
    return aliases.get(text, text)


def validate_snmp_v3_credentials(
    snmp_version: str | None,
    username: str | None,
    security_level: str | None,
    auth_protocol: str | None,
    auth_key: str | None,
    priv_protocol: str | None,
    priv_key: str | None,
) -> None:
    if snmp_version != "v3":
        return
    if not username or not security_level:
        raise ValueError("SNMPv3 requires username and security level")
    if security_level == "noAuthNoPriv":
        if any((auth_protocol, auth_key, priv_protocol, priv_key)):
            raise ValueError("SNMPv3 noAuthNoPriv must not include authentication or privacy parameters")
    elif security_level == "authNoPriv":
        if not (auth_protocol and auth_key):
            raise ValueError("SNMPv3 authNoPriv requires authentication protocol and key")
        if any((priv_protocol, priv_key)):
            raise ValueError("SNMPv3 authNoPriv must not include privacy parameters")
    elif security_level == "authPriv" and not (auth_protocol and auth_key and priv_protocol and priv_key):
        raise ValueError("SNMPv3 authPriv requires authentication and privacy protocols and keys")


class DeviceCredentials(BaseModel):
    ssh_username: str | None = Field(default=None, max_length=120)
    ssh_password: str | None = Field(default=None, max_length=1024)
    enable_password: str | None = Field(default=None, max_length=1024)
    snmp_community: str | None = Field(default=None, max_length=1024)
    snmp_v3_username: str | None = Field(default=None, max_length=120)
    snmp_v3_security_level: Literal["noAuthNoPriv", "authNoPriv", "authPriv"] | None = None
    snmp_v3_auth_protocol: Literal["MD5", "SHA", "SHA224", "SHA256", "SHA384", "SHA512"] | None = None
    snmp_v3_auth_key: str | None = Field(default=None, max_length=1024)
    snmp_v3_priv_protocol: Literal["DES", "3DES", "AES", "AES192", "AES256"] | None = None
    snmp_v3_priv_key: str | None = Field(default=None, max_length=1024)


class DeviceCreate(DeviceCredentials):
    name: str | None = Field(default=None, max_length=120)
    ip: str
    vendor: str = Field(max_length=80)
    platform_id: str | None = Field(default=None, max_length=80)
    device_type: str = Field(max_length=80)
    group_name: str | None = Field(default=None, max_length=80)
    note: str | None = Field(default=None, max_length=2000)
    ssh_port: int = Field(default=22, ge=1, le=65535)
    snmp_version: SnmpVersion | None = None
    snmp_port: int = Field(default=161, ge=1, le=65535)
    is_active: bool = True

    @field_validator("vendor", mode="before")
    @classmethod
    def _accept_chinese_vendor(cls, value):
        return normalize_vendor(value)

    @field_validator("device_type", mode="before")
    @classmethod
    def _accept_chinese_device_type(cls, value):
        return normalize_device_type(value)

    @field_validator("ip")
    @classmethod
    def validate_ip(cls, value: str) -> str:
        return str(IPv4Address(value.strip()))

    @model_validator(mode="after")
    def validate_protocol_credentials(self):
        platform = resolve_platform(self.vendor, self.platform_id)
        if self.device_type not in platform.device_types:
            raise ValueError(f"Device type {self.device_type} is not supported by platform {platform.id}")
        self.platform_id = platform.id
        has_ssh = bool(self.ssh_username and self.ssh_password)
        has_v2c = self.snmp_version == "v2c" and bool(self.snmp_community)
        has_v3 = self.snmp_version == "v3" and bool(self.snmp_v3_username)
        if not (has_ssh or has_v2c or has_v3):
            raise ValueError("At least one complete SNMP or SSH credential set is required")
        validate_snmp_v3_credentials(
            self.snmp_version,
            self.snmp_v3_username,
            self.snmp_v3_security_level,
            self.snmp_v3_auth_protocol,
            self.snmp_v3_auth_key,
            self.snmp_v3_priv_protocol,
            self.snmp_v3_priv_key,
        )
        return self


class DeviceUpdate(DeviceCredentials):
    name: str | None = Field(default=None, max_length=120)
    vendor: str | None = Field(default=None, max_length=80)
    platform_id: str | None = Field(default=None, max_length=80)
    device_type: str | None = Field(default=None, max_length=80)
    group_name: str | None = Field(default=None, max_length=80)
    note: str | None = Field(default=None, max_length=2000)
    ssh_port: int | None = Field(default=None, ge=1, le=65535)
    snmp_version: SnmpVersion | None = None
    snmp_port: int | None = Field(default=None, ge=1, le=65535)
    is_active: bool | None = None
    clear_protocols: set[Literal["snmp", "ssh"]] = Field(default_factory=set)


    @field_validator("vendor", mode="before")
    @classmethod
    def _accept_chinese_vendor(cls, value):
        return normalize_vendor(value) if value is not None else None

    @field_validator("device_type", mode="before")
    @classmethod
    def _accept_chinese_device_type(cls, value):
        return normalize_device_type(value) if value is not None else None

    @field_validator("vendor", "device_type", "ssh_port", "snmp_port", "is_active")
    @classmethod
    def _reject_null_required_fields(cls, value):
        if value is None:
            raise ValueError("Field cannot be null")
        return value

    @model_validator(mode="after")
    def validate_explicit_platform(self):
        if self.vendor and self.platform_id:
            resolve_platform(self.vendor, self.platform_id)
        return self

class DeviceResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str | None
    ip: str
    vendor: str
    platform_id: str
    device_type: str
    group_name: str | None
    note: str | None
    ssh_port: int
    has_ssh: bool
    snmp_version: SnmpVersion | None
    snmp_port: int
    has_snmp_credentials: bool
    snmp_v3_username: str | None
    snmp_v3_security_level: str | None
    snmp_v3_auth_protocol: str | None
    snmp_v3_priv_protocol: str | None
    is_active: bool
    created_at: datetime
    updated_at: datetime


class ConnectionTestRequest(BaseModel):
    protocol: Literal["snmp", "ssh"]


class ConnectionTestResponse(BaseModel):
    protocol: Literal["SNMP", "SSH"]
    status: Literal["success", "connection_failed", "authentication_failed", "data_fetch_failed"]
    failure_stage: str | None
    message: str | None
    host_fingerprint: str | None = None
    host_key_algorithm: str | None = None


class SshHostKeyTrustRequest(BaseModel):
    expected_host_fingerprint: str = Field(min_length=1, max_length=128)
    expected_host_key_algorithm: str = Field(min_length=1, max_length=128)


class TaskCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    device_ids: list[int] = Field(min_length=1, max_length=500)
    inspection_level: Literal["quick", "full"] = "quick"
    collection_mode: CollectionMode | None = None

    @field_validator("name")
    @classmethod
    def normalize_name(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("Task name cannot be blank")
        return value


class TaskDeleteRequest(BaseModel):
    # Capped so one request cannot try to unlink an unbounded number of files.
    task_ids: list[int] = Field(min_length=1, max_length=500)


class TaskResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    inspection_level: str
    collection_mode: str
    status: str
    total_devices: int
    completed_devices: int
    success_devices: int
    failed_devices: int
    normal_devices: int
    warning_devices: int
    critical_devices: int
    connection_failed_devices: int
    authentication_failed_devices: int
    data_fetch_failed_devices: int
    cancelled_devices: int
    unsupported_devices: int
    not_applicable_devices: int
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None


class ResultResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    device_id: int | None
    device_ip: str
    device_name: str | None
    vendor: str | None
    collection_source: str | None
    connection_status: str
    authentication_status: str
    collection_status: str
    parsing_status: str
    conclusion: str
    failure_stage: str | None
    message: str | None
    metrics: dict
    raw_output: str | None
    raw_output_status: str
    raw_output_bytes: int
    created_at: datetime
