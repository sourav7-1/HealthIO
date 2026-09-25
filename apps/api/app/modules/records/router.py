import uuid
from datetime import UTC, date, datetime

from fastapi import APIRouter, Request, status
from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.core.enums import RecordSource
from app.core.errors import ForbiddenError, NotFoundError, ValidationFailedError
from app.modules.access.context import PatientRequest, patient_request
from app.modules.access.permissions import Permission
from app.modules.care_team import service as care_team
from app.modules.records import service
from app.modules.records.models import DocumentType, HealthDocument, ScanStatus

router = APIRouter(tags=["documents"])

_view = patient_request(Permission.VIEW_REPORTS)
_upload = patient_request(Permission.UPLOAD_REPORTS)


def _not_future(v: date | None) -> date | None:
    if v is not None and v > datetime.now(UTC).date():
        raise ValueError("The date cannot be in the future")
    return v


class UploadIn(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    document_type: DocumentType
    content_type: str = Field(max_length=100)
    size_bytes: int = Field(gt=0)
    filename: str | None = Field(default=None, max_length=255)
    title: str | None = Field(default=None, max_length=200)
    description: str | None = Field(default=None, max_length=2000)
    document_date: date | None = None
    visit_id: uuid.UUID | None = None

    _date = field_validator("document_date")(_not_future)


class UploadOut(BaseModel):
    document_id: uuid.UUID
    upload_url: str
    fields: dict[str, str]
    max_bytes: int
    allowed_types: list[str]


class DocumentOut(BaseModel):
    id: uuid.UUID
    document_type: DocumentType
    title: str | None
    description: str | None
    document_date: date | None
    content_type: str
    size_bytes: int | None
    source: str
    scan_status: ScanStatus
    visit_id: uuid.UUID | None
    created_at: datetime
    added_by_my_side: bool


class DocumentCorrectionIn(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    reason: str = Field(min_length=3, max_length=300)
    document_type: DocumentType | None = None
    title: str | None = Field(default=None, max_length=200)
    description: str | None = Field(default=None, max_length=2000)
    document_date: date | None = None

    _date = field_validator("document_date")(_not_future)


class DownloadOut(BaseModel):
    url: str
    expires_in: int


def _source(ctx: PatientRequest) -> RecordSource:
    if "doctor" in ctx.access.via:
        return RecordSource.DOCTOR
    if "self" in ctx.access.via:
        return RecordSource.PATIENT
    return RecordSource.CAREGIVER


def _side(ctx: PatientRequest) -> str:
    return "doctor" if "doctor" in ctx.access.via else "patient"


def _out(d: HealthDocument, ctx: PatientRequest) -> DocumentOut:
    return DocumentOut(
        id=d.id,
        document_type=d.document_type,
        title=d.title,
        description=d.description,
        document_date=d.document_date,
        content_type=d.content_type,
        size_bytes=d.size_bytes,
        source=d.source.value,
        scan_status=d.scan_status,
        visit_id=d.visit_id,
        created_at=d.created_at,
        added_by_my_side=service.uploader_side(d.source) == _side(ctx),
    )


def _require_type_access(ctx: PatientRequest, doc: HealthDocument) -> None:
    # Hidden rather than refused: the caller should not learn such a document exists.
    if not service.can_view(ctx.access.permissions, doc.document_type):
        raise NotFoundError()


@router.post(
    "/patients/{patient_id}/documents/uploads",
    response_model=UploadOut,
    status_code=status.HTTP_201_CREATED,
)
async def start_upload(
    patient_id: uuid.UUID, body: UploadIn, request: Request, ctx: PatientRequest = _upload
) -> UploadOut:
    """Step 1 of an upload: a presigned form pinned to the file's type and size.
    Reports (lab and imaging) must be PDF, JPEG or PNG."""
    settings = request.app.state.settings
    if "doctor" in ctx.access.via:
        await care_team.require_verified_doctor(ctx.session, ctx.actor_id)
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
        description=body.description,
        document_date=body.document_date,
        visit_id=body.visit_id,
    )
    await ctx.audit(
        "document.upload_started",
        resource_type="health_document",
        resource_id=started.document.id,
        context={"document_type": body.document_type.value, "content_type": body.content_type},
    )
    await ctx.session.commit()
    return UploadOut(
        document_id=started.document.id,
        upload_url=started.url,
        fields=started.fields,
        max_bytes=settings.upload_max_bytes,
        allowed_types=sorted(service.allowed_types(body.document_type)),
    )


@router.post("/patients/{patient_id}/documents/{document_id}/complete", response_model=DocumentOut)
async def complete_upload(
    patient_id: uuid.UUID, document_id: uuid.UUID, request: Request, ctx: PatientRequest = _upload
) -> DocumentOut:
    """Step 2: the file is checked (type, size, active content, malware) before use."""
    try:
        done = await service.complete_upload(
            ctx.session,
            request.app.state.storage,
            request.app.state.settings,
            patient_id=ctx.patient_id,
            document_id=document_id,
            actor=ctx.actor_id,
            scanner=getattr(request.app.state, "scanner", None),
        )
    except ValidationFailedError as exc:
        # Keep the QUARANTINED verdict and record why the file was refused.
        await ctx.audit(
            "document.upload_rejected",
            resource_type="health_document",
            resource_id=document_id,
            context={"reason": exc.detail or "invalid"},
        )
        await ctx.session.commit()
        raise
    doc = done.document
    await ctx.audit(
        "document.upload_completed",
        resource_type="health_document",
        resource_id=doc.id,
        context={
            "scan_status": doc.scan_status.value,
            "scanner": done.scan.engine if done.scan else "none",
            "verdict": done.scan.verdict.value if done.scan else "not_scanned",
        },
    )
    await ctx.session.commit()
    return _out(doc, ctx)


@router.get("/patients/{patient_id}/documents", response_model=list[DocumentOut])
async def list_documents(patient_id: uuid.UUID, ctx: PatientRequest = _view) -> list[DocumentOut]:
    """Documents of the kinds the caller may see (a prescription photo needs prescription
    access, a discharge summary needs visit access, and so on)."""
    docs = [
        d
        for d in await service.list_documents(ctx.session, ctx.patient_id)
        if service.can_view(ctx.access.permissions, d.document_type)
    ]
    await ctx.audit("document.list", resource_type="health_document")
    await ctx.session.commit()
    return [_out(d, ctx) for d in docs]


@router.patch("/patients/{patient_id}/documents/{document_id}", response_model=DocumentOut)
async def correct_document(
    patient_id: uuid.UUID,
    document_id: uuid.UUID,
    body: DocumentCorrectionIn,
    ctx: PatientRequest = _upload,
) -> DocumentOut:
    """Correct a document's details with a reason. The earlier details stay in its
    history; the file itself is never replaced."""
    changes = body.model_dump(exclude_unset=True, exclude={"reason"})
    if not changes:
        raise ValidationFailedError("Nothing to change.")
    if changes.get("document_type", "x") is None:
        raise ValidationFailedError("Choose a document type.")
    doc = await service.get_document(ctx.session, ctx.patient_id, document_id)
    _require_type_access(ctx, doc)
    if "doctor" in ctx.access.via:
        await care_team.require_verified_doctor(ctx.session, ctx.actor_id)
    if "document_type" in changes and not service.can_view(
        ctx.access.permissions, changes["document_type"]
    ):
        raise ForbiddenError("You can't file a document under that type for this patient.")
    doc = await service.correct_metadata(
        ctx.session,
        patient_id=ctx.patient_id,
        document_id=document_id,
        actor=ctx.actor_id,
        actor_side=_side(ctx),
        reason=body.reason,
        changes=changes,
    )
    await ctx.audit(
        "document.correct",
        resource_type="health_document",
        resource_id=doc.id,
        changed_fields=sorted(changes),
    )
    await ctx.session.commit()
    return _out(doc, ctx)


@router.get("/patients/{patient_id}/documents/{document_id}/download", response_model=DownloadOut)
async def download(
    patient_id: uuid.UUID, document_id: uuid.UUID, request: Request, ctx: PatientRequest = _view
) -> DownloadOut:
    """A 60-second link; every download is audited."""
    doc = await service.usable_document(ctx.session, ctx.patient_id, document_id)
    _require_type_access(ctx, doc)
    url = service.download_url(request.app.state.storage, doc)
    await ctx.audit("document.download", resource_type="health_document", resource_id=doc.id)
    await ctx.session.commit()
    return DownloadOut(url=url, expires_in=60)
