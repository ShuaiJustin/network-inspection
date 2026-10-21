from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, JSON, String, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class Device(Base):
    __tablename__ = "devices"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str | None] = mapped_column(String(120))
    ip: Mapped[str] = mapped_column(String(45), unique=True, index=True)
    vendor: Mapped[str] = mapped_column(String(20))
    platform_id: Mapped[str | None] = mapped_column(String(80))
    device_type: Mapped[str] = mapped_column(String(20))
    group_name: Mapped[str | None] = mapped_column(String(80))
    note: Mapped[str | None] = mapped_column(Text)
    ssh_port: Mapped[int] = mapped_column(default=22)
    ssh_username: Mapped[str | None] = mapped_column(String(120))
    ssh_password: Mapped[str | None] = mapped_column(Text)
    enable_password: Mapped[str | None] = mapped_column(Text)
    ssh_host_fingerprint: Mapped[str | None] = mapped_column(String(128))
    snmp_version: Mapped[str | None] = mapped_column(String(10))
    snmp_port: Mapped[int] = mapped_column(default=161)
    snmp_community: Mapped[str | None] = mapped_column(Text)
    snmp_v3_username: Mapped[str | None] = mapped_column(String(120))
    snmp_v3_security_level: Mapped[str | None] = mapped_column(String(20))
    snmp_v3_auth_protocol: Mapped[str | None] = mapped_column(String(10))
    snmp_v3_auth_key: Mapped[str | None] = mapped_column(Text)
    snmp_v3_priv_protocol: Mapped[str | None] = mapped_column(String(10))
    snmp_v3_priv_key: Mapped[str | None] = mapped_column(Text)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow
    )


class InspectionTask(Base):
    __tablename__ = "inspection_tasks"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(120))
    inspection_level: Mapped[str] = mapped_column(String(20), default="quick")
    collection_mode: Mapped[str] = mapped_column(String(20), default="auto")
    status: Mapped[str] = mapped_column(String(30), default="queued")
    total_devices: Mapped[int] = mapped_column(default=0)
    completed_devices: Mapped[int] = mapped_column(default=0)
    success_devices: Mapped[int] = mapped_column(default=0)
    failed_devices: Mapped[int] = mapped_column(default=0)
    selected_device_ids: Mapped[str] = mapped_column(Text, default="")
    selected_device_snapshot: Mapped[list[dict]] = mapped_column(JSON, default=list)
    settings_snapshot: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    started_at: Mapped[datetime | None] = mapped_column(DateTime)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime)
    results: Mapped[list["InspectionResult"]] = relationship(
        back_populates="task", cascade="all, delete-orphan"
    )


class InspectionResult(Base):
    __tablename__ = "inspection_results"

    id: Mapped[int] = mapped_column(primary_key=True)
    task_id: Mapped[int] = mapped_column(ForeignKey("inspection_tasks.id"), index=True)
    device_id: Mapped[int | None] = mapped_column(
        ForeignKey("devices.id", ondelete="SET NULL"), index=True
    )
    device_ip: Mapped[str] = mapped_column(String(45))
    device_name: Mapped[str | None] = mapped_column(String(120))
    vendor: Mapped[str | None] = mapped_column(String(20))
    collection_source: Mapped[str | None] = mapped_column(String(30))
    connection_status: Mapped[str] = mapped_column(String(30))
    authentication_status: Mapped[str] = mapped_column(String(30))
    collection_status: Mapped[str] = mapped_column(String(30))
    parsing_status: Mapped[str] = mapped_column(String(30))
    conclusion: Mapped[str] = mapped_column(String(30))
    failure_stage: Mapped[str | None] = mapped_column(String(30))
    message: Mapped[str | None] = mapped_column(Text)
    metrics: Mapped[dict] = mapped_column(JSON, default=dict)
    raw_output: Mapped[str | None] = mapped_column(Text)
    raw_output_ref: Mapped[str | None] = mapped_column(Text)
    raw_output_status: Mapped[str] = mapped_column(String(30), default="not_available")
    raw_output_bytes: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    task: Mapped[InspectionTask] = relationship(back_populates="results")


class BackupRecord(Base):
    __tablename__ = "backup_records"

    id: Mapped[int] = mapped_column(primary_key=True)
    device_id: Mapped[int | None] = mapped_column(
        ForeignKey("devices.id", ondelete="SET NULL"), index=True
    )
    device_ip: Mapped[str] = mapped_column(String(45))
    file_path: Mapped[str | None] = mapped_column(Text)
    backup_root: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(30))
    message: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class ActivityLog(Base):
    __tablename__ = "activity_logs"

    id: Mapped[int] = mapped_column(primary_key=True)
    level: Mapped[str] = mapped_column(String(20), default="INFO")
    event: Mapped[str] = mapped_column(String(80))
    device_ip: Mapped[str | None] = mapped_column(String(45))
    task_id: Mapped[int | None] = mapped_column(Integer)
    message: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class AppSettings(Base):
    __tablename__ = "app_settings"

    id: Mapped[int] = mapped_column(primary_key=True, default=1)
    max_concurrency: Mapped[int] = mapped_column(default=10)
    default_collection_mode: Mapped[str] = mapped_column(String(20), default="auto")
    snmp_timeout_seconds: Mapped[int] = mapped_column(default=3)
    snmp_retries: Mapped[int] = mapped_column(default=1)
    connection_timeout_seconds: Mapped[int] = mapped_column(default=10)
    command_timeout_seconds: Mapped[int] = mapped_column(default=30)
    network_retry_count: Mapped[int] = mapped_column(default=2)
    cpu_warning_threshold: Mapped[int] = mapped_column(default=80)
    cpu_critical_threshold: Mapped[int] = mapped_column(default=95)
    memory_warning_threshold: Mapped[int] = mapped_column(default=80)
    memory_critical_threshold: Mapped[int] = mapped_column(default=95)
    report_directory: Mapped[str | None] = mapped_column(Text)
    backup_directory: Mapped[str | None] = mapped_column(Text)
    log_retention_days: Mapped[int] = mapped_column(default=30)
    backup_retention_count: Mapped[int] = mapped_column(default=30)
    backup_ignore_patterns: Mapped[list[str]] = mapped_column(JSON, default=list)
    storage_warning_threshold: Mapped[int] = mapped_column(default=80)
    storage_critical_threshold: Mapped[int] = mapped_column(default=95)
    raw_output_retention_days: Mapped[int] = mapped_column(default=30)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow
    )


class AdminUser(Base):
    __tablename__ = "admin_users"

    id: Mapped[int] = mapped_column(primary_key=True)
    username: Mapped[str] = mapped_column(String(80), unique=True)
    password_hash: Mapped[str] = mapped_column(Text)
    failed_attempts: Mapped[int] = mapped_column(default=0)
    locked_until: Mapped[datetime | None] = mapped_column(DateTime)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
