from fastapi import APIRouter

from app.services.adapters import list_adapter_capabilities, list_verified_adapters


router = APIRouter()


@router.get("/verified")
def verified_adapters():
    return list_verified_adapters()


@router.get("/capabilities")
def adapter_capabilities():
    return list_adapter_capabilities()
