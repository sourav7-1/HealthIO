"""What the assistant may know about the patient: a bounded, permission-checked extract.

- Only sections the caller may see are included (the same permissions as the rest of the
  app; a caregiver without `view_reports` gives the assistant no reports).
- No identifiers are sent: no name, date of birth, phone, ABHA number or address. Age
  in years and sex are enough context.
- Every item has an id (e.g. `med:<uuid>`) that answers must cite, so a claim about the
  record can be checked against what was actually provided.
- Structured fields (names, doses, dates) come from the database. Free text that people
  typed or that came from uploaded documents (instructions, notes, report conclusions,
  titles) goes through the injection filter (app/ai/injection.py) and is wrapped as
  untrusted data, or withheld.
"""

import contextlib
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.injection import neutralise, untrusted_block
from app.modules.access.permissions import Permission
from app.modules.access.service import PatientAccess
from app.modules.appointments import service as appointments
from app.modules.appointments.models import FollowUpStatus
from app.modules.care_team import service as care_team
from app.modules.chart.router import age
from app.modules.clinical import service as clinical
from app.modules.labs import service as labs
from app.modules.labs.models import TestReportStatus
from app.modules.medications import service as medications
from app.modules.medications.doses import active_schedules
from app.modules.medications.models import MedicationSchedule, MedicationStatus
from app.modules.medications.schedules import InvalidPattern, parse_rule
from app.modules.patients import service as patients
from app.modules.prescriptions import service as prescriptions
from app.modules.prescriptions.models import PrescriptionStatus

MAX_CHARS = 14_000


@dataclass
class RecordItem:
    id: str
    type: str
    fields: list[tuple[str, str]]
    untrusted: list[tuple[str, str]] = field(default_factory=list)  # (label, text)


@dataclass
class RecordContext:
    items: list[RecordItem] = field(default_factory=list)
    sections: list[str] = field(default_factory=list)  # what the caller may see
    medicine_names: list[str] = field(default_factory=list)
    withheld: list[str] = field(default_factory=list)  # item ids with blocked text
    rendered: str = ""
    texts: dict[str, str] = field(default_factory=dict)  # id -> everything the model saw


def _num(value: Decimal | None) -> str | None:
    return None if value is None else format(value.normalize(), "f")


def _dt(value: datetime | None) -> str | None:
    return value.astimezone(UTC).strftime("%Y-%m-%d %H:%M UTC") if value else None


def _schedule(s: MedicationSchedule | None) -> str | None:
    if s is None:
        return None
    parts: list[str] = []
    dose = " ".join(x for x in (_num(s.dose_amount), s.dose_unit) if x)
    if dose:
        parts.append(dose)
    if s.times_of_day:
        parts.append("at " + ", ".join(t.strftime("%H:%M") for t in s.times_of_day))
    if s.interval_minutes:
        parts.append(f"every {s.interval_minutes // 60} hours")
    with contextlib.suppress(InvalidPattern):
        parts.append(parse_rule(s.recurrence_rule).describe())
    if s.meal_relation:
        parts.append(s.meal_relation.value.replace("_", " "))
    return ", ".join(parts) or None


async def build(
    session: AsyncSession, access: PatientAccess, viewer_doctor_id: None = None
) -> RecordContext:
    pid = access.patient_id
    ctx = RecordContext()
    allows = access.allows

    if allows(Permission.VIEW_PROFILE):
        ctx.sections.append("profile")
        profile = await patients.get_live_profile(session, pid)
        if profile:
            ctx.items.append(
                RecordItem(
                    "profile",
                    "profile",
                    [
                        (
                            "Age",
                            f"{years} years"
                            if (years := age(profile.date_of_birth)) is not None
                            else "not recorded",
                        ),
                        ("Sex at birth", profile.sex_at_birth.value.replace("_", " ")),
                        ("Blood group", profile.blood_group.value),
                    ],
                )
            )

    if allows(Permission.VIEW_MEDICATIONS):
        ctx.sections.append("medications")
        schedules = await active_schedules(session, pid)
        for m in await medications.list_for_patient(session, pid):
            if m.status not in (
                MedicationStatus.ACTIVE,
                MedicationStatus.PAUSED,
                MedicationStatus.PENDING_CONFIRMATION,
            ):
                continue
            ctx.medicine_names += [n.lower() for n in (m.generic_name, m.name) if n]
            ctx.items.append(
                RecordItem(
                    f"med:{m.id}",
                    "medication",
                    [
                        ("Medicine", m.name),
                        ("Generic name", m.generic_name or ""),
                        ("Strength", m.strength or ""),
                        ("Form", m.dosage_form or ""),
                        (
                            "Schedule",
                            _schedule(schedules.get(m.id)) or ("as needed" if m.is_prn else ""),
                        ),
                        ("Status", m.status.value.replace("_", " ")),
                        ("Where it came from", m.origin.value.replace("_", " ")),
                        ("Started", str(m.start_date or "")),
                        ("Ends", str(m.end_date or "")),
                        (
                            "Paused until",
                            str(m.resume_on or "") if m.status == MedicationStatus.PAUSED else "",
                        ),
                    ],
                    [("Instructions", m.instructions or "")],
                )
            )

    if allows(Permission.VIEW_PRESCRIPTIONS):
        ctx.sections.append("prescriptions")
        rows = [
            r
            for r in await prescriptions.list_for_patient(session, pid, viewer_doctor_id=None)
            if r.prescription.status not in (PrescriptionStatus.DRAFT,)
        ][:5]
        names = await care_team.doctor_names(
            session,
            {
                r.prescription.prescriber_doctor_id
                for r in rows
                if r.prescription.prescriber_doctor_id
            },
        )
        for r in rows:
            p = r.prescription
            prescriber = (
                names.get(p.prescriber_doctor_id)
                if p.prescriber_doctor_id
                else p.external_prescriber_name
            )
            ctx.items.append(
                RecordItem(
                    f"rx:{p.id}",
                    "prescription",
                    [
                        ("Prescribed by", prescriber or "not recorded"),
                        (
                            "Date",
                            str(p.prescribed_on or (p.issued_at.date() if p.issued_at else "")),
                        ),
                        ("Status", p.status.value),
                        ("Follow-up on", str(p.follow_up_on or "")),
                    ],
                    [
                        ("Advice as written", p.advice or ""),
                        ("Follow-up instructions", p.follow_up_instructions or ""),
                    ],
                )
            )
            for i in r.items:
                ctx.medicine_names += [n.lower() for n in (i.generic_name, i.drug_name) if n]
                ctx.items.append(
                    RecordItem(
                        f"rxi:{i.id}",
                        "prescription_line",
                        [
                            ("Prescription", f"rx:{p.id}"),
                            ("Medicine", i.drug_name),
                            ("Strength", i.strength or ""),
                            ("Dose", " ".join(x for x in (_num(i.dose_amount), i.dose_unit) if x)),
                            ("How often (as written)", i.frequency_text or ""),
                            (
                                "Food",
                                i.meal_relation.value.replace("_", " ") if i.meal_relation else "",
                            ),
                            ("Duration", f"{i.duration_days} days" if i.duration_days else ""),
                            (
                                "As needed",
                                f"yes ({i.prn_reason})"
                                if i.is_prn and i.prn_reason
                                else "yes"
                                if i.is_prn
                                else "",
                            ),
                        ],
                        [("Instructions as written", i.instructions or "")],
                    )
                )

    if allows(Permission.VIEW_APPOINTMENTS):
        ctx.sections.append("appointments")
        now = datetime.now(UTC)
        appts = [
            a
            for a in await appointments.list_for_patient(session, pid)
            if a.starts_at >= now and a.status in appointments.LIVE
        ]
        appts.sort(key=lambda a: a.starts_at)
        follow = [
            f
            for f in await appointments.list_follow_ups(session, pid)
            if f.status in (FollowUpStatus.OPEN, FollowUpStatus.BOOKED)
        ]
        names = await care_team.doctor_names(
            session, {a.doctor_id for a in appts[:5]} | {f.doctor_id for f in follow[:5]}
        )
        for a in appts[:5]:
            ctx.items.append(
                RecordItem(
                    f"appt:{a.id}",
                    "appointment",
                    [
                        ("When", _dt(a.starts_at) or ""),
                        ("With", names.get(a.doctor_id, "a doctor")),
                        ("Type", a.mode.value.replace("_", " ")),
                        ("Status", a.status.value.replace("_", " ")),
                        ("Place", a.location or ""),
                    ],
                    [("Reason", a.reason or "")],
                )
            )
        for f in follow[:5]:
            ctx.items.append(
                RecordItem(
                    f"fu:{f.id}",
                    "follow_up",
                    [
                        ("Due", str(f.due_date)),
                        ("With", names.get(f.doctor_id, "a doctor")),
                        ("Status", f.status.value),
                    ],
                    [("Reason", f.reason or "")],
                )
            )

    if allows(Permission.VIEW_MEDICAL_HISTORY):
        ctx.sections.append("medical_history")
        for c in (await clinical.list_conditions(session, pid))[:15]:
            ctx.items.append(
                RecordItem(
                    f"cond:{c.id}",
                    "condition",
                    [
                        ("Condition", c.name),
                        (
                            "Recorded by",
                            "doctor"
                            if c.source.value == "doctor"
                            else f"{c.source.value} (not confirmed by a doctor)",
                        ),
                        (
                            "Status",
                            f"{c.clinical_status.value}, {c.verification_status.value}".replace(
                                "_", " "
                            ),
                        ),
                        ("Since", str(c.onset_date or "")),
                    ],
                )
            )
        for al in (await clinical.list_allergies(session, pid))[:15]:
            ctx.items.append(
                RecordItem(
                    f"allergy:{al.id}",
                    "allergy",
                    [
                        ("Allergy to", al.substance),
                        ("Reaction", al.reaction or ""),
                        ("Severity", al.severity.value if al.severity else ""),
                        ("Confirmed", al.verification_status.value.replace("_", " ")),
                    ],
                )
            )
        for s in (await clinical.list_symptoms(session, pid))[:10]:
            if s.status.value == "entered_in_error":
                continue
            ctx.items.append(
                RecordItem(
                    f"symptom:{s.id}",
                    "symptom",
                    [
                        ("Reported", str(s.reported_at.date())),
                        ("Reported by", s.source.value),
                        ("Severity", s.severity.value if s.severity else ""),
                        ("Since", str(s.onset_date or "")),
                        ("Status", s.status.value),
                    ],
                    [("Symptom in their words", s.symptom), ("Notes", s.notes or "")],
                )
            )

    if allows(Permission.VIEW_REPORTS):
        ctx.sections.append("reports")
        for rw in (await labs.list_reports(session, pid))[:5]:
            rep = rw.report
            if rep.status in (TestReportStatus.REJECTED, TestReportStatus.ENTERED_IN_ERROR):
                continue
            verified = rep.status == TestReportStatus.VERIFIED
            fields = [
                ("Test", rep.test_name or ""),
                (
                    "Date",
                    str(rep.report_date or (rep.collected_at.date() if rep.collected_at else "")),
                ),
                ("Laboratory", rep.lab_name or ""),
                (
                    "Review",
                    "verified by a doctor"
                    if verified
                    else "uploaded, not yet reviewed by a doctor",
                ),
            ]
            if verified:
                for res in rw.results[:30]:
                    value = _num(res.value_numeric) or (res.value_text or "")
                    rng = res.reference_text or (
                        f"{_num(res.reference_low)}-{_num(res.reference_high)}"
                        if res.reference_low is not None and res.reference_high is not None
                        else ""
                    )
                    flag = (
                        "" if res.flag.value == "unknown" else f", flag printed: {res.flag.value}"
                    )
                    fields.append(
                        (
                            res.analyte_name,
                            f"{value} {res.unit or ''} "
                            f"(range on report: {rng or 'not printed'}{flag})",
                        )
                    )
            ctx.items.append(
                RecordItem(
                    f"report:{rep.id}",
                    "test_report",
                    fields,
                    [("Conclusion as written", rep.conclusion or ""), ("Notes", rep.notes or "")],
                )
            )

    _render(ctx)
    return ctx


def _render(ctx: RecordContext) -> None:
    parts: list[str] = []
    total = 0
    for item in ctx.items:
        lines = [f"{k}: {neutralise(v, 300)}" for k, v in item.fields if v]
        seen_text = " ".join(v for _, v in item.fields if v)
        for label, raw in item.untrusted:
            block = untrusted_block(item.id, label, raw)
            if block is None:
                continue
            if block.withheld:
                ctx.withheld.append(item.id)
            else:
                seen_text += " " + raw
            lines.append(block.text)
        rendered = f'<item id="{item.id}" type="{item.type}">\n' + "\n".join(lines) + "\n</item>"
        if total + len(rendered) > MAX_CHARS:
            break
        total += len(rendered)
        parts.append(rendered)
        ctx.texts[item.id] = seen_text
    ctx.rendered = "\n".join(parts)
    ctx.medicine_names = list(dict.fromkeys(ctx.medicine_names))
