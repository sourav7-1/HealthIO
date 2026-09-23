import uuid
from datetime import date, datetime

from fastapi import APIRouter, Request, status
from pydantic import BaseModel, ConfigDict, Field

from app.core.enums import RecordSource
from app.modules.access.context import PatientRequest, patient_request
from app.modules.access.permissions import Permission
from app.modules.records import service
from app.modules.records.models import DocumentType, HealthDocument, ScanStatus

router = APIRouter(tags=["documents"])

_view = patient_request(Permission.VIEW_REPORTS)
_upload = patient_request(Permission.UPLOAD_REPORTS)


class UploadIn(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    document_type: DocumentType
    content_type: str = Field(max_length=100)
    size_bytes: int = Field(gt=0)
    filename: str | None = Field(default=None, max_length=255)
    title: str | None = Field(default=None, max_length=200)
    document_date: date | None = None
    visit_id: uuid.UUID | None = None


class UploadOut(BaseModel):
    document_id: uuid.UUID
    upload_url: str
    fields: dict[str, str]
    max_bytes: int


class DocumentOut(BaseModel):
    id: uuid.UUID
    document_type: DocumentType
    title: str | None
    document_date: date | None
    content_type: str
    size_bytes: int | None
    source: str
    scan_status: ScanStatus
    visit_id: uuid.UUID | None
    created_at: datetime


class DownloadOut(BaseModel):
    url: str
    expires_in: int


def _source(ctx: PatientRequest) -> RecordSource:
    if "doctor" in ctx.access.via:
        return RecordSource.DOCTOR
    if "self" in ctx.access.via:
        return RecordSource.PATIENT
    return RecordSource.CAREGIVER


def _out(d: HealthDocument) -> DocumentOut:
    return DocumentOut(
        id=d.id,
        document_type=d.document_type,
        title=d.title,
        document_date=d.document_date,
        content_type=d.content_type,
        size_bytes=d.size_bytes,
        source=d.source.value,
        scan_status=d.scan_status,
        visit_id=d.visit_id,
        created_at=d.created_at,
    )


@router.post(
    "/patients/{patient_id}/documents/uploads",
    response_model=UploadOut,
    status_code=status.HTTP_201_CREATED,
)
async def start_upload(
    patient_id: uuid.UUID, body: UploadIn, request: Request, ctx: PatientRequest = _upload
) -> UploadOut:
    settings = request.app.state.settings
    started = await service.start_upload(
        ctx.session,
        request.app.state.storage,
        settings,
        patient_id=ctx.patient_id,
        actor=ctx.actor_id,
        source=_source(ctx),
        document_type=body.document_type,
        content_type=body.content_type,
        size_bytes=body.size_bytes,
        filename=body.filename,
        title=body.title,
        document_date=body.document_date,
        visit_id=body.visit_id,
    )
    await ctx.audit(
        "document.upload_started", resource_type="health_document", resource_id=started.document.id
    )
    await ctx.session.commit()
    return UploadOut(
        document_id=started.document.id,
        upload_url=started.url,
        fields=started.fields,
        max_bytes=settings.upload_max_bytes,
    )


@router.post("/patients/{patient_id}/documents/{document_id}/complete", response_model=DocumentOut)
async def complete_upload(
    patient_id: uuid.UUID, document_id: uuid.UUID, request: Request, ctx: PatientRequest = _upload
) -> DocumentOut:
    try:
        doc = await service.complete_upload(
            ctx.session,
            request.app.state.storage,
            request.app.state.settings,
            patient_id=ctx.patient_id,
            document_id=document_id,
            actor=ctx.actor_id,
        )
    except Exception:
        await ctx.session.commit()  # keep a QUARANTINED verdict
        raise
    await ctx.audit(
        "document.upload_completed", resource_type="health_document", resource_id=doc.id
    )
    await ctx.session.commit()
    return _out(doc)


@router.get("/patients/{patient_id}/documents", response_model=list[DocumentOut])
async def list_documents(patient_id: uuid.UUID, ctx: PatientRequest = _view) -> list[DocumentOut]:
    docs = await service.list_documents(ctx.session, ctx.patient_id)
    await ctx.audit("document.list", resource_type="health_document")
    await ctx.session.commit()
    return [_out(d) for d in docs]


@router.get("/patients/{patient_id}/documents/{document_id}/download", response_model=DownloadOut)
async def download(
    patient_id: uuid.UUID, document_id: uuid.UUID, request: Request, ctx: PatientRequest = _view
) -> DownloadOut:
    """A 60-second link; every download is audited."""
    doc = await service.usable_document(ctx.session, ctx.patient_id, document_id)
    url = service.download_url(request.app.state.storage, doc)
    await ctx.audit("document.download", resource_type="health_document", resource_id=doc.id)
    await ctx.session.commit()
    return DownloadOut(url=url, expires_in=60)
