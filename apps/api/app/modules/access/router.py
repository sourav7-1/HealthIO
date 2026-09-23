import uuid

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from app.modules.access.dependencies import require_patient_relationship
from app.modules.access.service import PatientAccess

router = APIRouter(tags=["access"])


class PatientAccessOut(BaseModel):
    patient_id: uuid.UUID
    via: list[str]
    permissions: list[str]


@router.get(
    "/patients/{patient_id}/access",
    response_model=PatientAccessOut,
    summary="What the caller may do for this patient (drives what the UI shows)",
)
async def my_access(
    patient_id: uuid.UUID,
    access: PatientAccess = Depends(require_patient_relationship()),
) -> PatientAccessOut:
    return PatientAccessOut(
        patient_id=access.patient_id,
        via=sorted(access.via),
        permissions=sorted(p.value for p in access.permissions),
    )
