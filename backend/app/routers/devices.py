from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import or_
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db import get_db
from app.models.device import ActivityLog, BackupRecord, Device, InspectionResult
from app.schemas.device import ConnectionTestRequest, ConnectionTestResponse, DeviceCreate, DeviceResponse, DeviceUpdate, SshHostKeyTrustRequest, validate_snmp_v3_credentials
from app.services.collectors import observe_ssh_host_fingerprint, test_snmp_connectivity, test_ssh_connectivity
from app.services.device_operations import device_operation_lock
from app.services.security import encrypt_secret, redact_runtime_text
from app.services.settings import get_settings
from app.services.time import utc_for_api
from app.services.vendor_registry import resolve_platform


router = APIRouter()


SECRET_FIELDS = {
    "ssh_password",
    "enable_password",
    "snmp_community",
    "snmp_v3_auth_key",
    "snmp_v3_priv_key",
}


def _response(device: Device) -> dict:
    try:
        platform_id = resolve_platform(device.vendor, device.platform_id).id
    except (ValueError, KeyError):
        # Keep legacy records visible so an administrator can repair an old
        # platform choice instead of losing the entire device list to a 500.
        platform_id = device.platform_id or "unsupported"
    return {
        "id": device.id,
        "name": device.name,
        "ip": device.ip,
        "vendor": device.vendor,
        "platform_id": platform_id,
        "device_type": device.device_type,
        "group_name": device.group_name,
        "note": device.note,
        "ssh_port": device.ssh_port,
        "has_ssh": bool(device.ssh_username and device.ssh_password),
        "snmp_version": device.snmp_version,
        "snmp_port": device.snmp_port,
        "has_snmp_credentials": bool(
            (device.snmp_version == "v2c" and device.snmp_community)
            or (device.snmp_version == "v3" and device.snmp_v3_username)
        ),
        "snmp_v3_username": device.snmp_v3_username,
        "snmp_v3_security_level": device.snmp_v3_security_level,
        "snmp_v3_auth_protocol": device.snmp_v3_auth_protocol,
        "snmp_v3_priv_protocol": device.snmp_v3_priv_protocol,
        "is_active": device.is_active,
        "created_at": utc_for_api(device.created_at),
        "updated_at": utc_for_api(device.updated_at),
    }


def _build_device(payload: dict) -> Device:
    for field in SECRET_FIELDS:
        if payload.get(field):
            payload[field] = encrypt_secret(payload[field])
    return Device(**payload)


@router.get("", response_model=list[DeviceResponse])
def list_devices(
    keyword: str | None = None,
    vendor: str | None = None,
    group_name: str | None = None,
    device_type: str | None = None,
    db: Session = Depends(get_db),
):
    query = db.query(Device)
    if keyword:
        query = query.filter(
            or_(Device.ip.contains(keyword), Device.name.contains(keyword))
        )
    if vendor:
        query = query.filter(Device.vendor == vendor)
    if group_name:
        query = query.filter(Device.group_name == group_name)
    if device_type:
        query = query.filter(Device.device_type == device_type)
    return [_response(device) for device in query.order_by(Device.ip).all()]


@router.post("", response_model=DeviceResponse, status_code=status.HTTP_201_CREATED)
def create_device(payload: DeviceCreate, db: Session = Depends(get_db)):
    device = _build_device(payload.model_dump())
    db.add(device)
    db.add(ActivityLog(event="device_created", device_ip=device.ip, message="Device added"))
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=409, detail="Management IP already exists")
    db.refresh(device)
    return _response(device)


@router.get("/{device_id}", response_model=DeviceResponse)
def get_device(device_id: int, db: Session = Depends(get_db)):
    device = db.get(Device, device_id)
    if not device:
        raise HTTPException(status_code=404, detail="Device not found")
    return _response(device)


@router.post(
    "/{device_id}/connection-test",
    response_model=ConnectionTestResponse,
    response_model_exclude_unset=True,
)
async def connection_test(
    device_id: int,
    payload: ConnectionTestRequest,
    db: Session = Depends(get_db),
):
    async with device_operation_lock(device_id):
        db.expire_all()
        device = db.query(Device).filter(Device.id == device_id).first()
        if not device:
            raise HTTPException(status_code=404, detail="Device not found")
        settings = get_settings(db)
        if payload.protocol == "snmp":
            result = await test_snmp_connectivity(
                device,
                timeout=settings.snmp_timeout_seconds,
                retries=settings.snmp_retries,
            )
        else:
            result = await test_ssh_connectivity(
                device,
                connection_timeout=settings.connection_timeout_seconds,
            )

    message = redact_runtime_text(result.message) if result.message else None
    if result.ok:
        status_value = "success"
    elif result.authentication_failed or result.failure_stage in {"authentication", "credentials"}:
        status_value = "authentication_failed"
    elif result.failure_stage == "connection":
        status_value = "connection_failed"
    else:
        status_value = "data_fetch_failed"
    protocol = "SNMP" if payload.protocol == "snmp" else "SSH"
    db.add(ActivityLog(
        level="INFO" if result.ok else "ERROR",
        event="device_connectivity_test",
        device_ip=device.ip,
        message=f"{protocol} {status_value.replace('_', ' ')}" + (f": {message}" if message else ""),
    ))
    db.commit()
    response = {
        "protocol": protocol,
        "status": status_value,
        "failure_stage": result.failure_stage,
        "message": message,
    }
    if result.host_fingerprint:
        response["host_fingerprint"] = result.host_fingerprint
    if result.host_key_algorithm:
        response["host_key_algorithm"] = result.host_key_algorithm
    return ConnectionTestResponse(**response)


@router.post("/{device_id}/ssh-host-key/trust", response_model=DeviceResponse)
async def trust_ssh_host_key(
    device_id: int,
    payload: SshHostKeyTrustRequest,
    db: Session = Depends(get_db),
):
    async with device_operation_lock(device_id):
        device = db.get(Device, device_id)
        if not device:
            raise HTTPException(status_code=404, detail="Device not found")
        if not (device.ssh_username and device.ssh_password):
            raise HTTPException(status_code=422, detail="SSH credentials not configured")
        settings = get_settings(db)
        observed = await observe_ssh_host_fingerprint(device, settings.connection_timeout_seconds)
        if not observed.ok or not observed.host_fingerprint:
            raise HTTPException(status_code=400, detail=redact_runtime_text(observed.message or "Could not read SSH host fingerprint"))
        if (
            observed.host_fingerprint != payload.expected_host_fingerprint
            or observed.host_key_algorithm != payload.expected_host_key_algorithm
        ):
            db.add(ActivityLog(
                level="ERROR",
                event="ssh_host_fingerprint_confirmation_mismatch",
                device_ip=device.ip,
                message="SSH host key changed after administrator confirmation was displayed",
            ))
            db.commit()
            raise HTTPException(status_code=409, detail="SSH host key changed after confirmation; inspect and confirm the current key again")
        device.ssh_host_fingerprint = observed.host_fingerprint
        db.add(ActivityLog(
            event="ssh_host_fingerprint_trusted",
            device_ip=device.ip,
            message="Administrator confirmed a new SSH host fingerprint",
        ))
        db.commit()
        db.refresh(device)
        return _response(device)


@router.put("/{device_id}", response_model=DeviceResponse)
async def update_device(device_id: int, payload: DeviceUpdate, db: Session = Depends(get_db)):
    async with device_operation_lock(device_id):
        device = db.get(Device, device_id)
        if not device:
            raise HTTPException(status_code=404, detail="Device not found")
        previous_ssh_port = device.ssh_port
        previous_vendor = device.vendor
        clear_protocols = payload.clear_protocols
        if "snmp" in clear_protocols:
            for field in {
                "snmp_community",
                "snmp_v3_username",
                "snmp_v3_security_level",
                "snmp_v3_auth_protocol",
                "snmp_v3_auth_key",
                "snmp_v3_priv_protocol",
                "snmp_v3_priv_key",
            }:
                setattr(device, field, None)
            device.snmp_version = None
        if "ssh" in clear_protocols:
            for field in {"ssh_username", "ssh_password", "enable_password"}:
                setattr(device, field, None)
        for field, value in payload.model_dump(exclude_unset=True, exclude={"clear_protocols"}).items():
            if field in SECRET_FIELDS and value:
                value = encrypt_secret(value)
            setattr(device, field, value)
        if device.vendor != previous_vendor and "platform_id" not in payload.model_fields_set:
            device.platform_id = resolve_platform(device.vendor).id
        try:
            platform = resolve_platform(device.vendor, device.platform_id)
            if device.device_type not in platform.device_types:
                raise ValueError(f"Device type {device.device_type} is not supported by platform {platform.id}")
            device.platform_id = platform.id
        except ValueError as exc:
            db.rollback()
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        if device.ssh_port != previous_ssh_port:
            device.ssh_host_fingerprint = None
        has_ssh = bool(device.ssh_username and device.ssh_password)
        has_v2c = device.snmp_version == "v2c" and bool(device.snmp_community)
        has_v3 = device.snmp_version == "v3" and bool(device.snmp_v3_username)
        if not (has_ssh or has_v2c or has_v3):
            db.rollback()
            raise HTTPException(
                status_code=422,
                detail="At least one complete SNMP or SSH credential set is required",
            )
        try:
            validate_snmp_v3_credentials(
                device.snmp_version,
                device.snmp_v3_username,
                device.snmp_v3_security_level,
                device.snmp_v3_auth_protocol,
                device.snmp_v3_auth_key,
                device.snmp_v3_priv_protocol,
                device.snmp_v3_priv_key,
            )
        except ValueError as exc:
            db.rollback()
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        db.add(ActivityLog(event="device_updated", device_ip=device.ip, message="Device updated"))
        try:
            db.commit()
        except IntegrityError as exc:
            db.rollback()
            raise HTTPException(status_code=409, detail="Management IP already exists") from exc
        db.refresh(device)
        return _response(device)


@router.delete("/{device_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_device(device_id: int, db: Session = Depends(get_db)):
    async with device_operation_lock(device_id):
        device = db.query(Device).filter(Device.id == device_id).first()
        if not device:
            raise HTTPException(status_code=404, detail="Device not found")
        db.add(ActivityLog(event="device_deleted", device_ip=device.ip, message="Device deleted"))
        db.query(InspectionResult).filter(InspectionResult.device_id == device_id).update(
            {InspectionResult.device_id: None}, synchronize_session=False
        )
        db.query(BackupRecord).filter(BackupRecord.device_id == device_id).update(
            {BackupRecord.device_id: None}, synchronize_session=False
        )
        db.delete(device)
        db.commit()
