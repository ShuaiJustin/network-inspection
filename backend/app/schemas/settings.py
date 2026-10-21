import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


REQUIRED_SETTING_FIELDS = {
    "max_concurrency", "default_collection_mode", "snmp_timeout_seconds",
    "snmp_retries", "connection_timeout_seconds", "command_timeout_seconds",
    "network_retry_count", "cpu_warning_threshold", "cpu_critical_threshold",
    "memory_warning_threshold", "memory_critical_threshold", "log_retention_days",
    "backup_retention_count", "backup_ignore_patterns", "storage_warning_threshold",
    "storage_critical_threshold", "raw_output_retention_days",
}


class SettingsUpdate(BaseModel):
    max_concurrency: int | None = Field(default=None, ge=1, le=50)
    default_collection_mode: Literal["auto", "snmp", "ssh"] | None = None
    snmp_timeout_seconds: int | None = Field(default=None, ge=1, le=60)
    snmp_retries: int | None = Field(default=None, ge=0, le=5)
    connection_timeout_seconds: int | None = Field(default=None, ge=1, le=120)
    command_timeout_seconds: int | None = Field(default=None, ge=1, le=300)
    network_retry_count: int | None = Field(default=None, ge=0, le=5)
    cpu_warning_threshold: int | None = Field(default=None, ge=1, le=100)
    cpu_critical_threshold: int | None = Field(default=None, ge=1, le=100)
    memory_warning_threshold: int | None = Field(default=None, ge=1, le=100)
    memory_critical_threshold: int | None = Field(default=None, ge=1, le=100)
    report_directory: str | None = Field(default=None, max_length=1024)
    backup_directory: str | None = Field(default=None, max_length=1024)
    log_retention_days: int | None = Field(default=None, ge=1, le=3650)
    backup_retention_count: int | None = Field(default=None, ge=1, le=1000)
    backup_ignore_patterns: list[str] | None = Field(default=None, max_length=20)
    storage_warning_threshold: int | None = Field(default=None, ge=1, le=100)
    storage_critical_threshold: int | None = Field(default=None, ge=1, le=100)
    raw_output_retention_days: int | None = Field(default=None, ge=1, le=365)

    @model_validator(mode="after")
    def reject_explicit_null_for_required_fields(self):
        null_fields = sorted(
            field for field in self.model_fields_set
            if field in REQUIRED_SETTING_FIELDS and getattr(self, field) is None
        )
        if null_fields:
            raise ValueError(f"Settings fields cannot be null: {', '.join(null_fields)}")
        return self

    @field_validator("backup_ignore_patterns")
    @classmethod
    def validate_backup_ignore_patterns(cls, value: list[str] | None) -> list[str] | None:
        if value is None:
            return None
        patterns = [pattern.strip() for pattern in value if pattern.strip()]
        for pattern in patterns:
            if len(pattern) > 200:
                raise ValueError("Backup ignore pattern is too long")
            try:
                re.compile(pattern)
            except re.error as exc:
                raise ValueError(f"Invalid backup ignore pattern: {exc}") from exc
        return patterns


class SettingsResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    max_concurrency: int
    default_collection_mode: str
    snmp_timeout_seconds: int
    snmp_retries: int
    connection_timeout_seconds: int
    command_timeout_seconds: int
    network_retry_count: int
    cpu_warning_threshold: int
    cpu_critical_threshold: int
    memory_warning_threshold: int
    memory_critical_threshold: int
    report_directory: str | None
    backup_directory: str | None
    log_retention_days: int
    backup_retention_count: int
    backup_ignore_patterns: list[str]
    storage_warning_threshold: int
    storage_critical_threshold: int
    raw_output_retention_days: int
