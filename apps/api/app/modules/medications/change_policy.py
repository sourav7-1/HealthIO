"""Which changes to a medicine a patient may make on their own, which need them to
acknowledge a warning, and which need a clinician (pure code; no database, no AI).

The prescription itself is never changed here: it stays exactly as the doctor wrote it.
What changes is the patient's *regimen* (reminders, dose recorded per reminder).

- Timing only (same number of doses a day, same food relation): the patient's routine.
  Allowed; warnings (doses very close together, night-time doses, times that do not fit
  a written pattern such as 1-0-1) must be acknowledged.
- Clinically relevant, for a medicine from a prescription: a different number of doses
  a day, a different dose, a different food relation, different days, switching between
  scheduled and as-needed, pausing or stopping. These need a clinician: a linked doctor
  approves in the app, or the patient records which doctor or pharmacist advised it.
- A medicine the patient added themselves: their own entry; only warnings apply.

Nothing here decides what is medically right. It only decides who must confirm.
"""

from dataclasses import dataclass, field
from datetime import time
from decimal import Decimal
from itertools import pairwise

from app.modules.medications.schedules import DAILY, DayPattern

MIN_GAP_MINUTES = 4 * 60
VERY_CLOSE_MINUTES = 2 * 60


@dataclass(frozen=True)
class Regimen:
    schedule_type: str  # fixed_times | interval | as_needed
    times: tuple[time, ...] = ()
    interval_minutes: int | None = None
    pattern: DayPattern = DAILY
    dose_amount: Decimal | None = None
    dose_unit: str | None = None
    meal_relation: str | None = None

    def per_day(self) -> float | None:
        if self.schedule_type == "fixed_times":
            return float(len(self.times)) if self.pattern == DAILY else None
        if self.schedule_type == "interval" and self.interval_minutes:
            return 1440 / self.interval_minutes
        return None


@dataclass(frozen=True)
class Prescribed:
    """What the prescription says (as structured from the written line)."""

    times_per_day: int | None
    dose_amount: Decimal | None
    dose_unit: str | None
    meal_relation: str | None
    is_prn: bool
    frequency_text: str | None


@dataclass(frozen=True)
class Finding:
    code: str
    message: str
    clinical: bool


@dataclass
class Assessment:
    findings: list[Finding] = field(default_factory=list)

    @property
    def requires(self) -> str:
        """none | acknowledgement | clinician"""
        if any(f.clinical for f in self.findings):
            return "clinician"
        if self.findings:
            return "acknowledgement"
        return "none"


def _minutes(t: time) -> int:
    return t.hour * 60 + t.minute


def _gaps(times: tuple[time, ...]) -> list[int]:
    ms = sorted(_minutes(t) for t in times)
    if len(ms) < 2:
        return []
    return [b - a for a, b in pairwise(ms)] + [ms[0] + 1440 - ms[-1]]


# Slots for written patterns like 1-0-1 / 1-1-1 / 1-0-0-1.
_SLOTS_3 = ((5 * 60, 11 * 60), (11 * 60, 17 * 60), (17 * 60, 24 * 60))
_SLOTS_4 = ((5 * 60, 11 * 60), (11 * 60, 15 * 60), (15 * 60, 20 * 60), (20 * 60, 24 * 60))


def _pattern_slots(text: str | None) -> list[tuple[int, int]] | None:
    if not text:
        return None
    parts = [p.strip() for p in text.replace(chr(0x2013), "-").split("-")]
    if len(parts) not in (3, 4) or not all(p.isdigit() for p in parts):
        return None
    slots = _SLOTS_3 if len(parts) == 3 else _SLOTS_4
    return [slot for n, slot in zip(parts, slots, strict=True) if int(n) > 0]


def _fits_slots(times: tuple[time, ...], slots: list[tuple[int, int]]) -> bool:
    remaining = list(slots)
    for t in sorted(times):
        m = _minutes(t) or 24 * 60  # midnight counts as night
        hit = next((s for s in remaining if s[0] <= m < s[1] or (s[1] == 1440 and m == 1440)), None)
        if hit is None:
            return False
        remaining.remove(hit)
    return not remaining


def assess(
    proposed: Regimen,
    *,
    prescribed: Prescribed | None,
    current: Regimen | None = None,
) -> Assessment:
    out = Assessment()
    add = out.findings.append

    if prescribed is not None:
        wants_prn = proposed.schedule_type == "as_needed"
        if wants_prn != prescribed.is_prn:
            add(
                Finding(
                    "prn_differs",
                    "The prescription says to take this "
                    + ("only when needed" if prescribed.is_prn else "on a schedule")
                    + ".",
                    clinical=True,
                )
            )
        per_day = proposed.per_day()
        if (
            prescribed.times_per_day is not None
            and per_day is not None
            and not wants_prn
            and abs(per_day - prescribed.times_per_day) > 1e-9
        ):
            add(
                Finding(
                    "frequency_differs",
                    f"The prescription says {prescribed.times_per_day} time"
                    f"{'s' if prescribed.times_per_day != 1 else ''} a day; this would be "
                    f"{per_day:g}.",
                    clinical=True,
                )
            )
        if proposed.pattern != DAILY and prescribed.times_per_day is not None:
            add(
                Finding(
                    "days_differ",
                    "The prescription is for every day; this would skip days.",
                    clinical=True,
                )
            )
        if proposed.dose_amount is not None or proposed.dose_unit is not None:
            same = prescribed.dose_amount == proposed.dose_amount and (
                prescribed.dose_unit or None
            ) == (proposed.dose_unit or None)
            if not same:
                add(
                    Finding(
                        "dose_differs"
                        if prescribed.dose_amount is not None
                        else "dose_not_prescribed",
                        "This dose is different from the prescription."
                        if prescribed.dose_amount is not None
                        else "The prescription does not state this dose.",
                        clinical=True,
                    )
                )
        if (
            prescribed.meal_relation
            and prescribed.meal_relation != "any"
            and proposed.meal_relation
            and proposed.meal_relation != prescribed.meal_relation
        ):
            add(
                Finding(
                    "meal_differs",
                    "The prescription gives a different instruction about food.",
                    clinical=True,
                )
            )
        slots = _pattern_slots(prescribed.frequency_text)
        if (
            slots
            and proposed.schedule_type == "fixed_times"
            and len(proposed.times) == len(slots)
            and not _fits_slots(proposed.times, slots)
        ):
            add(
                Finding(
                    "times_outside_pattern",
                    f"These times do not match the written pattern {prescribed.frequency_text} "
                    "(morning, afternoon, night).",
                    clinical=False,
                )
            )

    if proposed.schedule_type == "fixed_times":
        gaps = _gaps(proposed.times)
        if gaps and min(gaps) < VERY_CLOSE_MINUTES:
            add(
                Finding(
                    "doses_very_close", "Two doses are less than 2 hours apart.", clinical=False
                )
            )
        elif gaps and min(gaps) < MIN_GAP_MINUTES:
            add(Finding("doses_close", "Two doses are less than 4 hours apart.", clinical=False))
        if any(_minutes(t) < 5 * 60 for t in proposed.times) and (
            current is None or not any(_minutes(t) < 5 * 60 for t in current.times)
        ):
            add(
                Finding(
                    "night_time", "A reminder is set between midnight and 5 am.", clinical=False
                )
            )
    return out


STOP_OR_PAUSE_PRESCRIBED = Finding(
    "prescribed_medicine",
    "This medicine was prescribed. Stopping or pausing it without talking to the doctor "
    "can be harmful.",
    clinical=True,
)
