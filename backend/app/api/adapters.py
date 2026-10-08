from fastapi import APIRouter

from app.domain.schemas import AdapterValidationRequest
from app.services.runtime_adapters import list_runtime_adapters, validate_runtime_adapter

router = APIRouter(tags=["adapters"])


@router.get("/adapters")
async def adapters() -> dict[str, object]:
    return {"items": list_runtime_adapters()}


@router.post("/adapters/validate")
async def validate_adapter(request: AdapterValidationRequest) -> dict[str, object]:
    return validate_runtime_adapter(request.adapter)
