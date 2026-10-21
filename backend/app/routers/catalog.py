from fastapi import APIRouter

from app.services.vendor_registry import device_catalog


router = APIRouter()


@router.get("/devices")
def get_device_catalog():
    return device_catalog()
