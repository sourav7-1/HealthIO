"""The patient's medical record timeline and the change history of each record.

One chronological list across visits, symptoms, notes, assessments, prescriptions,
medicines, test orders, reports, appointments, follow-ups and uploaded documents.

* **Permissions and consent:** each kind is included only when the caller holds the
  permission for it (a doctor's permissions are already narrowed by the patient's
  consent). Draft notes and prescriptions appear only for their author. A report shared
  with one doctor appears for that doctor even without tests-and-reports consent. A
  document appears only if the caller may see that type of document.
* **Neutral wording:** titles say what was recorded and by whom. They never summarise or
  interpret clinical content.
* **History:** versioned records (migration 0011) list every earlier version with who
  changed what and why. Notes and prescriptions show their amendment and revision chains.
"""

import base64
import uuid
from collections import Counter
from dataclasses import dataclass, field, replace
from datetime import UTC, date, datetime, time
from typing import Any

from sqlalchemy import or_, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.crypto import EncryptedString, get_keyring
from app.core.enums import RecordSource
from app.core.errors import NotFoundError, ValidationFailedError
from app.core.models import Base
from app.modules.access.permissions import Permission
from app.modules.access.service import PatientAccess
from app.modules.appointments import service as appointments
from app.modules.care_team.models import DoctorProfile
from app.modules.clinical import service as clinical
from app.modules.clinical.models import ClinicalNote, NoteStatus
from app.modules.labs import service as labs
from app.modules.medications import service as medications
from app.modules.patients.models import PatientProfile
from app.modules.prescriptions import service as prescriptions
from app.modules.prescriptions.models import Prescription, PrescriptionStatus
from app.modules.records import service as records
from app.modules.timeline.models import BOOKKEEPING, RecordVersion

KINDS = (
    "visit",
    "symptom",
    "note",
    "assessment",
    "reported_condition",
    "prescription",
    "medication",
    "test_order",
    "report",
    "appointment",
    "follow_up",
    "document",
)

# Kinds whose records are versioned by the database, and their tables.
KIND_TABLE: dict[str, str] = {
    "visit": "doctor_visits",
    "symptom": "symptom_reports",
    "assessment": "medical_conditions",
    "reported_condition": "medical_conditions",
    "test_order": "test_orders",
    "report": "test_reports",
    "appointment": "appointments",
    "follow_up": "follow_ups",
    "document": "health_documents",
}

KIND_PERMISSION: dict[str, Permission] = {
    "visit": Permission.VIEW_VISITS,
    "note": Permission.VIEW_VISITS,
    "symptom": Permission.VIEW_MEDICAL_HISTORY,
    "assessment": Permission.VIEW_MEDICAL_HISTORY,
    "reported_condition": Permission.VIEW_MEDICAL_HISTORY,
    "prescription": Permission.VIEW_PRESCRIPTIONS,
    "medication": Permission.VIEW_MEDICATIONS,
    "test_order": Permission.VIEW_REPORTS,
    "report": Permission.VIEW_REPORTS,
    "appointment": Permission.VIEW_APPOINTMENTS,
    "follow_up": Permission.VIEW_APPOINTMENTS,
    "document": Permission.VIEW_REPORTS,
}

MAX_LIMIT = 200


def _label(value: str) -> str:
    return value.replace("_", " ")


def _day(d: date) -> datetime:
    return datetime.combine(d, time(12, 0), tzinfo=UTC)


@dataclass(frozen=True)
class DoctorRef:
    id: uuid.UUID | None
    name: str | None
    specialty: str | None


@dataclass(frozen=True)
class TimelineEvent:
    at: datetime
    kind: str
    title: str
    resource_id: uuid.UUID
    detail: str | None = None
    status: str | None = None
    date_only: bool = False
    visit_id: uuid.UUID | None = None
    source: str | None = None
    amended: bool = False
    has_history: bool = False
    doctor: DoctorRef | None = None
    # Resolved to `doctor` at the end (one query for all events).
    doctor_id: uuid.UUID | None = None
    doctor_user_id: uuid.UUID | None = None
    external_doctor: str | None = None

    @property
    def key(self) -> str:
        return f"{self.kind}:{self.resource_id}"


@dataclass(frozen=True)
class TimelineFilter:
    kinds: frozenset[str] = frozenset()
    date_from: date | None = None
    date_to: date | None = None
    doctor_id: uuid.UUID | None = None
    specialty: str | None = None

    def matches(self, e: TimelineEvent) -> bool:
        if self.kinds and e.kind not in self.kinds:
            return False
        day = e.at.date()
        if self.date_from and day < self.date_from:
            return False
        if self.date_to and day > self.date_to:
            return False
        if self.doctor_id and (e.doctor is None or e.doctor.id != self.doctor_id):
            return False
        if self.specialty:
            spec = (e.doctor.specialty if e.doctor else None) or ""
            if spec.casefold() != self.specialty.casefold():
                return False
        return True


@dataclass
class Facets:
    kinds: dict[str, int] = field(default_factory=dict)
    doctors: list[tuple[DoctorRef, int]] = field(default_factory=list)
    specialties: list[tuple[str, int]] = field(default_factory=list)
    earliest: datetime | None = None
    latest: datetime | None = None


@dataclass
class TimelinePage:
    events: list[TimelineEvent]
    next_cursor: str | None
    total: int  # after filters
    facets: Facets


# --- cursor -------------------------------------------------------------------------------


def encode_cursor(e: TimelineEvent) -> str:
    raw = f"{e.at.isoformat()}|{e.key}".encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def decode_cursor(cursor: str) -> tuple[datetime, str]:
    try:
        raw = base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4)).decode()
        at, key = raw.split("|", 1)
        return datetime.fromisoformat(at), key
    except (ValueError, UnicodeDecodeError) as exc:
        raise ValidationFailedError("Invalid cursor.") from exc


# --- collecting events --------------------------------------------------------------------


def _from_user(source: RecordSource, user_id: uuid.UUID | None) -> uuid.UUID | None:
    """Doctor-authored rows without a visit: the doctor is whoever created the row."""
    return user_id if source == RecordSource.DOCTOR else None


async def collect(
    session: AsyncSession,
    access: PatientAccess,
    *,
    viewer_doctor_id: uuid.UUID | None,
) -> list[TimelineEvent]:
    pid = access.patient_id
    allows = access.allows
    events: list[TimelineEvent] = []
    visit_doctor: dict[uuid.UUID, uuid.UUID] = {}
    versioned = await _versioned_ids(session, pid)

    def hist(kind: str, rid: uuid.UUID) -> bool:
        return (KIND_TABLE.get(kind), rid) in versioned

    if allows(Permission.VIEW_VISITS) or allows(Permission.VIEW_MEDICAL_HISTORY):
        visit_doctor = {v.id: v.doctor_id for v in await clinical.list_visits(session, pid)}

    if allows(Permission.VIEW_VISITS):
        for v in await clinical.list_visits(session, pid):
            events.append(
                TimelineEvent(
                    at=v.started_at or v.created_at,
                    kind="visit",
                    title=f"Doctor visit ({_label(v.visit_type.value)})",
                    detail=v.chief_complaint,
                    status=v.status.value,
                    resource_id=v.id,
                    visit_id=v.id,
                    source=RecordSource.DOCTOR.value,
                    doctor_id=v.doctor_id,
                    has_history=hist("visit", v.id),
                )
            )
        notes = [
            n
            for n in await clinical.list_notes(
                session, patient_id=pid, viewer_doctor_id=viewer_doctor_id
            )
            if n.status != NoteStatus.DRAFT
        ]
        superseded = {n.supersedes_note_id for n in notes if n.supersedes_note_id}
        for n in notes:
            kind_label = _label(n.note_type.value)
            events.append(
                TimelineEvent(
                    at=n.signed_at or n.created_at,
                    kind="note",
                    title=(
                        f"Amended {kind_label} note signed"
                        if n.supersedes_note_id
                        else f"{kind_label.capitalize()} note signed"
                    ),
                    detail=f"Amendment reason: {n.amendment_reason}"
                    if n.amendment_reason
                    else None,
                    status=n.status.value,
                    resource_id=n.id,
                    visit_id=n.visit_id,
                    source=RecordSource.DOCTOR.value,
                    doctor_id=n.author_doctor_id,
                    amended=n.supersedes_note_id is not None,
                    has_history=n.supersedes_note_id is not None or n.id in superseded,
                )
            )

    if allows(Permission.VIEW_MEDICAL_HISTORY):
        for s in await clinical.list_symptoms(session, pid):
            detail = ", ".join(
                x
                for x in (
                    s.severity.value if s.severity else None,
                    s.body_site,
                    f"since {s.onset_date.isoformat()}" if s.onset_date else None,
                    f"stopped {s.resolved_on.isoformat()}" if s.resolved_on else None,
                )
                if x
            )
            events.append(
                TimelineEvent(
                    at=s.reported_at,
                    kind="symptom",
                    title=f"Symptom reported: {s.symptom}",
                    detail=detail or None,
                    status=s.status.value,
                    resource_id=s.id,
                    visit_id=s.visit_id,
                    source=s.source.value,
                    doctor_id=visit_doctor.get(s.visit_id) if s.visit_id else None,
                    doctor_user_id=None if s.visit_id else _from_user(s.source, s.created_by),
                    amended=s.version > 1,
                    has_history=hist("symptom", s.id),
                )
            )
        for c in await clinical.list_conditions(session, pid):
            by_doctor = c.source == RecordSource.DOCTOR
            kind = "assessment" if by_doctor else "reported_condition"
            events.append(
                TimelineEvent(
                    at=c.created_at,
                    kind=kind,
                    title=(
                        f"Assessment: {c.name}" if by_doctor else f"Condition reported: {c.name}"
                    ),
                    detail=(
                        f"{_label(c.verification_status.value)}, {_label(c.clinical_status.value)}"
                        + (f" (ICD-10 {c.icd10_code})" if c.icd10_code else "")
                    ),
                    status=c.verification_status.value,
                    resource_id=c.id,
                    visit_id=c.visit_id,
                    source=c.source.value,
                    doctor_id=visit_doctor.get(c.visit_id) if c.visit_id else None,
                    doctor_user_id=None if c.visit_id else _from_user(c.source, c.created_by),
                    amended=c.version > 1,
                    has_history=hist(kind, c.id),
                )
            )

    attached_docs: set[uuid.UUID] = set()
    if allows(Permission.VIEW_PRESCRIPTIONS):
        rx_rows = await prescriptions.list_for_patient(
            session, pid, viewer_doctor_id=viewer_doctor_id
        )
        replaced = {r.prescription.supersedes_prescription_id for r in rx_rows}
        for rx in rx_rows:
            p = rx.prescription
            if p.document_id:
                attached_docs.add(p.document_id)
            if p.status == PrescriptionStatus.DRAFT:
                continue
            count = len(rx.items)
            events.append(
                TimelineEvent(
                    at=p.issued_at or (_day(p.prescribed_on) if p.prescribed_on else p.created_at),
                    date_only=p.issued_at is None and p.prescribed_on is not None,
                    kind="prescription",
                    title=(
                        f"Prescription {_label(p.status.value)} "
                        f"({count} medicine{'s' if count != 1 else ''})"
                    ),
                    detail=", ".join(i.drug_name for i in rx.items[:5])
                    + ("…" if count > 5 else ""),
                    status=p.status.value,
                    resource_id=p.id,
                    visit_id=p.visit_id,
                    source=p.source.value,
                    doctor_id=p.prescriber_doctor_id,
                    external_doctor=p.external_prescriber_name,
                    amended=p.revision > 1,
                    has_history=p.revision > 1 or p.id in replaced,
                )
            )

    if allows(Permission.VIEW_MEDICATIONS):
        for m in await medications.list_for_patient(session, pid):
            if m.prescription_item_id is not None:
                continue  # shown with its prescription
            events.append(
                TimelineEvent(
                    at=m.created_at,
                    kind="medication",
                    title=f"Medicine added: {m.name}",
                    detail=_label(m.source.value),
                    status=m.status.value,
                    resource_id=m.id,
                    source=m.source.value,
                )
            )

    order_doctor: dict[uuid.UUID, uuid.UUID] = {}
    if allows(Permission.VIEW_REPORTS):
        for o in await labs.list_orders(session, pid):
            order_doctor[o.order.id] = o.order.ordering_doctor_id
            events.append(
                TimelineEvent(
                    at=o.order.ordered_at,
                    kind="test_order",
                    title=f"Tests ordered ({len(o.items)})",
                    detail=", ".join(i.test_name for i in o.items[:6])
                    + ("…" if len(o.items) > 6 else ""),
                    status=o.order.status.value,
                    resource_id=o.order.id,
                    visit_id=o.order.visit_id,
                    source=RecordSource.DOCTOR.value,
                    doctor_id=o.order.ordering_doctor_id,
                    has_history=hist("test_order", o.order.id),
                )
            )

    shared = (
        None
        if allows(Permission.VIEW_REPORTS)
        else await labs.shared_report_ids(session, pid, viewer_doctor_id)
    )
    if shared is None or shared:
        if not order_doctor and shared is not None:
            order_doctor = {
                o.order.id: o.order.ordering_doctor_id for o in await labs.list_orders(session, pid)
            }
        for r in await labs.list_reports(session, pid):
            rep = r.report
            if rep.document_id:
                attached_docs.add(rep.document_id)
            if shared is not None and rep.id not in shared:
                continue
            what = rep.test_name or rep.lab_name or "Test"
            uploaded = rep.source in (RecordSource.PATIENT, RecordSource.CAREGIVER)
            if rep.report_date:
                at, date_only = _day(rep.report_date), True
            else:
                at, date_only = rep.collected_at or rep.created_at, False
            parts = [
                rep.lab_name if rep.test_name and rep.lab_name else None,
                f"{len(r.results)} value{'s' if len(r.results) != 1 else ''} entered"
                if r.results
                else ("File attached" if rep.document_id else None),
                "shared with you" if shared is not None else None,
            ]
            events.append(
                TimelineEvent(
                    at=at,
                    date_only=date_only,
                    kind="report",
                    title=f"Report {'uploaded' if uploaded else 'recorded'}: {what}",
                    detail=", ".join(x for x in parts if x) or None,
                    status=rep.status.value,
                    resource_id=rep.id,
                    source=rep.source.value,
                    doctor_id=order_doctor.get(rep.order_id) if rep.order_id else None,
                    doctor_user_id=None if rep.order_id else _from_user(rep.source, rep.created_by),
                    has_history=hist("report", rep.id),
                )
            )

    if allows(Permission.VIEW_APPOINTMENTS):
        for a in await appointments.list_for_patient(session, pid):
            events.append(
                TimelineEvent(
                    at=a.starts_at,
                    kind="appointment",
                    title=f"Appointment ({_label(a.mode.value)})",
                    detail=a.reason,
                    status=a.status.value,
                    resource_id=a.id,
                    doctor_id=a.doctor_id,
                    has_history=hist("appointment", a.id),
                )
            )
        for f in await appointments.list_follow_ups(session, pid):
            events.append(
                TimelineEvent(
                    at=_day(f.due_date),
                    date_only=True,
                    kind="follow_up",
                    title="Follow-up due",
                    detail=f.reason,
                    status=f.status.value,
                    resource_id=f.id,
                    visit_id=f.source_visit_id,
                    doctor_id=f.doctor_id,
                    has_history=hist("follow_up", f.id),
                )
            )

    if allows(Permission.VIEW_REPORTS):
        for d in await records.list_documents(session, pid):
            if d.id in attached_docs or not records.can_view(access.permissions, d.document_type):
                continue
            events.append(
                TimelineEvent(
                    at=_day(d.document_date) if d.document_date else d.created_at,
                    date_only=d.document_date is not None,
                    kind="document",
                    title="Document added: "
                    + (d.title or _label(d.document_type.value).capitalize()),
                    detail=_label(d.document_type.value).capitalize() if d.title else None,
                    status=d.scan_status.value,
                    resource_id=d.id,
                    visit_id=d.visit_id,
                    source=d.source.value,
                    doctor_id=visit_doctor.get(d.visit_id) if d.visit_id else None,
                    doctor_user_id=None if d.visit_id else _from_user(d.source, d.created_by),
                    amended=hist("document", d.id),
                    has_history=hist("document", d.id),
                )
            )

    return await _resolve_doctors(session, events)


async def _versioned_ids(session: AsyncSession, pid: uuid.UUID) -> set[tuple[str, uuid.UUID]]:
    rows = await session.execute(
        select(RecordVersion.table_name, RecordVersion.record_id)
        .where(RecordVersion.patient_id == pid)
        .distinct()
    )
    return {(t, r) for t, r in rows.tuples()}


async def _resolve_doctors(
    session: AsyncSession, events: list[TimelineEvent]
) -> list[TimelineEvent]:
    ids = {e.doctor_id for e in events if e.doctor_id}
    user_ids = {e.doctor_user_id for e in events if e.doctor_user_id}
    by_id: dict[uuid.UUID, DoctorProfile] = {}
    by_user: dict[uuid.UUID, DoctorProfile] = {}
    if ids or user_ids:
        stmt = select(DoctorProfile).where(
            or_(DoctorProfile.id.in_(ids), DoctorProfile.user_id.in_(user_ids))
        )
        for d in (await session.scalars(stmt)).all():
            by_id[d.id] = d
            by_user[d.user_id] = d
    out: list[TimelineEvent] = []
    for e in events:
        doc = by_id.get(e.doctor_id) if e.doctor_id else None
        doc = doc or (by_user.get(e.doctor_user_id) if e.doctor_user_id else None)
        ref: DoctorRef | None = None
        if doc is not None:
            ref = DoctorRef(doc.id, doc.display_name, doc.primary_specialty)
        elif e.external_doctor:
            ref = DoctorRef(None, e.external_doctor, None)
        out.append(replace(e, doctor=ref))
    return out


def facets(events: list[TimelineEvent]) -> Facets:
    kinds = Counter(e.kind for e in events)
    doctors: dict[uuid.UUID, tuple[DoctorRef, int]] = {}
    specialties: Counter[str] = Counter()
    for e in events:
        if e.doctor and e.doctor.id:
            ref, n = doctors.get(e.doctor.id, (e.doctor, 0))
            doctors[e.doctor.id] = (ref, n + 1)
        if e.doctor and e.doctor.specialty:
            specialties[e.doctor.specialty] += 1
    return Facets(
        kinds=dict(sorted(kinds.items())),
        doctors=sorted(doctors.values(), key=lambda x: (x[0].name or "").casefold()),
        specialties=sorted(specialties.items(), key=lambda x: x[0].casefold()),
        earliest=min((e.at for e in events), default=None),
        latest=max((e.at for e in events), default=None),
    )


def paginate(
    events: list[TimelineEvent],
    flt: TimelineFilter,
    *,
    newest_first: bool,
    cursor: str | None,
    limit: int,
) -> TimelinePage:
    all_facets = facets(events)
    matching = [e for e in events if flt.matches(e)]
    matching.sort(key=lambda e: (e.at, e.key), reverse=newest_first)
    if cursor:
        at, key = decode_cursor(cursor)
        pos = (at, key)
        matching_after = [
            e for e in matching if ((e.at, e.key) < pos if newest_first else (e.at, e.key) > pos)
        ]
    else:
        matching_after = matching
    limit = max(1, min(limit, MAX_LIMIT))
    page = matching_after[:limit]
    more = len(matching_after) > limit
    return TimelinePage(
        events=page,
        next_cursor=encode_cursor(page[-1]) if more and page else None,
        total=len(matching),
        facets=all_facets,
    )


# --- history ------------------------------------------------------------------------------

HIDDEN_FIELDS = {
    "id",
    "patient_id",
    "created_at",
    "created_by",
    "storage_key",
    "sha256",
    "size_bytes",
    "scan_status",
    "content_type",
    "original_filename",
    *BOOKKEEPING,
}


@dataclass(frozen=True)
class Actor:
    role: str  # doctor | patient | caregiver | system
    name: str | None = None


@dataclass(frozen=True)
class FieldChange:
    field: str
    before: str | None
    after: str | None


@dataclass(frozen=True)
class HistoryEntry:
    at: datetime
    action: str  # created | changed | signed | amended | revised
    actor: Actor
    reason: str | None = None
    version: int | None = None
    changes: list[FieldChange] = field(default_factory=list)


def _table_model(table: str) -> Any:
    for mapper in Base.registry.mappers:
        if getattr(mapper.class_, "__tablename__", None) == table:
            return mapper.class_
    raise KeyError(table)


def _decrypt_snapshot(table: str, snap: dict[str, Any]) -> dict[str, Any]:
    model = _table_model(table)
    out = dict(snap)
    keyring = get_keyring()
    for col in model.__table__.columns:
        if isinstance(col.type, EncryptedString) and out.get(col.name) is not None:
            out[col.name] = keyring.decrypt(out[col.name], col.type.context)
    return out


def _text(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, list):
        return ", ".join(str(v) for v in value)
    return str(value)


async def _actors(
    session: AsyncSession, pid: uuid.UUID, user_ids: set[uuid.UUID | None]
) -> dict[uuid.UUID | None, Actor]:
    real = {u for u in user_ids if u}
    doctors = (
        {
            d.user_id: d
            for d in (
                await session.scalars(select(DoctorProfile).where(DoctorProfile.user_id.in_(real)))
            ).all()
        }
        if real
        else {}
    )
    patient_user = await session.scalar(
        select(PatientProfile.user_id).where(PatientProfile.id == pid)
    )
    out: dict[uuid.UUID | None, Actor] = {None: Actor("system")}
    for u in real:
        if u in doctors:
            out[u] = Actor("doctor", doctors[u].display_name)
        elif u == patient_user:
            out[u] = Actor("patient")
        else:
            out[u] = Actor("caregiver")
    return out


async def _versioned_history(
    session: AsyncSession, pid: uuid.UUID, table: str, rid: uuid.UUID
) -> list[HistoryEntry]:
    current = await session.scalar(
        text(f"SELECT to_jsonb(t) FROM {table} t WHERE t.id = :id AND t.patient_id = :pid"),  # noqa: S608
        {"id": rid, "pid": pid},
    )
    if current is None:
        raise NotFoundError()
    versions = list(
        (
            await session.scalars(
                select(RecordVersion)
                .where(
                    RecordVersion.patient_id == pid,
                    RecordVersion.table_name == table,
                    RecordVersion.record_id == rid,
                )
                .order_by(RecordVersion.created_at, RecordVersion.id)
            )
        ).all()
    )
    states = [_decrypt_snapshot(table, v.snapshot) for v in versions]
    states.append(_decrypt_snapshot(table, current))
    actors = await _actors(
        session,
        pid,
        {states[0].get("created_by") and uuid.UUID(states[0]["created_by"])}
        | {v.created_by for v in versions},
    )
    first = states[0]
    creator = uuid.UUID(first["created_by"]) if first.get("created_by") else None
    entries = [
        HistoryEntry(
            at=datetime.fromisoformat(first["created_at"]),
            action="created",
            actor=actors.get(creator, Actor("system")),
            version=1 if "version" in first else None,
        )
    ]
    for i, v in enumerate(versions):
        before, after = states[i], states[i + 1]
        changes = [
            FieldChange(col, _text(before.get(col)), _text(after.get(col)))
            for col in v.changed_columns
            if col not in HIDDEN_FIELDS
        ]
        if not changes:
            continue
        entries.append(
            HistoryEntry(
                at=v.created_at,
                action="changed",
                actor=actors.get(v.created_by, Actor("system")),
                reason=v.reason,
                version=(v.version + 1) if v.version is not None else None,
                changes=changes,
            )
        )
    return entries


async def _note_history(
    session: AsyncSession, pid: uuid.UUID, note_id: uuid.UUID, viewer_doctor_id: uuid.UUID | None
) -> list[HistoryEntry]:
    target = await session.scalar(
        select(ClinicalNote).where(ClinicalNote.id == note_id, ClinicalNote.patient_id == pid)
    )
    if target is None or (
        target.status == NoteStatus.DRAFT and target.author_doctor_id != viewer_doctor_id
    ):
        raise NotFoundError()
    notes = {
        n.id: n
        for n in (
            await session.scalars(
                select(ClinicalNote).where(
                    ClinicalNote.patient_id == pid,
                    ClinicalNote.visit_id == target.visit_id,
                    ClinicalNote.status != NoteStatus.DRAFT,
                )
            )
        ).all()
    }
    chain = _chain(target.id, notes, lambda n: n.supersedes_note_id)
    doctors = {
        d.id: d.display_name
        for d in (
            await session.scalars(
                select(DoctorProfile).where(
                    DoctorProfile.id.in_({n.author_doctor_id for n in chain})
                )
            )
        ).all()
    }
    return [
        HistoryEntry(
            at=n.signed_at or n.created_at,
            action="amended" if n.supersedes_note_id else "signed",
            actor=Actor("doctor", doctors.get(n.author_doctor_id)),
            reason=n.amendment_reason,
            changes=[FieldChange("status", None, n.status.value)],
        )
        for n in chain
    ]


async def _prescription_history(
    session: AsyncSession, pid: uuid.UUID, rx_id: uuid.UUID, viewer_doctor_id: uuid.UUID | None
) -> list[HistoryEntry]:
    rows = {
        p.id: p
        for p in (
            await session.scalars(select(Prescription).where(Prescription.patient_id == pid))
        ).all()
        if p.status != PrescriptionStatus.DRAFT or p.prescriber_doctor_id == viewer_doctor_id
    }
    if rx_id not in rows:
        raise NotFoundError()
    chain = _chain(rx_id, rows, lambda p: p.supersedes_prescription_id)
    doctors = {
        d.id: d.display_name
        for d in (
            await session.scalars(
                select(DoctorProfile).where(
                    DoctorProfile.id.in_(
                        {p.prescriber_doctor_id for p in chain if p.prescriber_doctor_id}
                    )
                )
            )
        ).all()
    }
    entries: list[HistoryEntry] = []
    for p in chain:
        actor = (
            Actor("doctor", doctors.get(p.prescriber_doctor_id))
            if p.prescriber_doctor_id
            else Actor("patient" if p.created_by else "system")
        )
        entries.append(
            HistoryEntry(
                at=p.issued_at or p.created_at,
                action="revised" if p.revision > 1 else "created",
                actor=actor,
                reason=p.revision_reason,
                version=p.revision,
                changes=[FieldChange("status", None, p.status.value)],
            )
        )
        if p.cancelled_at:
            entries.append(
                HistoryEntry(
                    at=p.cancelled_at,
                    action="changed",
                    actor=actor,
                    reason=p.cancel_reason,
                    version=p.revision,
                    changes=[FieldChange("status", "issued", "cancelled")],
                )
            )
    return entries


def _chain(start: uuid.UUID, rows: dict[uuid.UUID, Any], parent: Any) -> list[Any]:
    """All versions linked to `start` (older and newer), oldest first."""
    children = {parent(r): r for r in rows.values() if parent(r)}
    node = rows[start]
    while parent(node) and parent(node) in rows:
        node = rows[parent(node)]
    chain = [node]
    while chain[-1].id in children:
        chain.append(children[chain[-1].id])
    return chain


async def history(
    session: AsyncSession,
    access: PatientAccess,
    *,
    kind: str,
    resource_id: uuid.UUID,
    viewer_doctor_id: uuid.UUID | None,
) -> list[HistoryEntry]:
    """Every version of one record, oldest first. Caller must be allowed to see the kind."""
    pid = access.patient_id
    if kind not in KINDS:
        raise NotFoundError()
    allowed = access.allows(KIND_PERMISSION[kind])
    if kind == "report" and not allowed:
        allowed = resource_id in await labs.shared_report_ids(session, pid, viewer_doctor_id)
    if not allowed:
        raise NotFoundError()
    if kind == "note":
        return await _note_history(session, pid, resource_id, viewer_doctor_id)
    if kind == "prescription":
        return await _prescription_history(session, pid, resource_id, viewer_doctor_id)
    if kind == "document":
        doc = await records.get_document(session, pid, resource_id)
        if not records.can_view(access.permissions, doc.document_type):
            raise NotFoundError()
    if kind == "report":
        await labs.get_report(session, pid, resource_id)  # 404 for another patient's id
        return await _versioned_history(session, pid, "test_reports", resource_id)
    table = KIND_TABLE.get(kind)
    if table is None:  # medications keep their own event log (medication history page)
        raise NotFoundError()
    if kind in ("assessment", "reported_condition"):
        cond_source = await session.scalar(
            text("SELECT source FROM medical_conditions WHERE id = :id AND patient_id = :pid"),
            {"id": resource_id, "pid": pid},
        )
        if cond_source is None or (cond_source == "doctor") != (kind == "assessment"):
            raise NotFoundError()
    return await _versioned_history(session, pid, table, resource_id)
