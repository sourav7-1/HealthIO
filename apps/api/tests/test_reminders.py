"""Reminder engine pure parts: recurring schedules across time zones and DST, quiet hours,
lock-screen privacy, and missed-dose guidance that gives no advice (no database)."""

from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal
from itertools import pairwise
from zoneinfo import ZoneInfo

import pytest

from app.modules.medications.models import Medication, MedicationDose, MedicationOrigin
from app.modules.medications.schedules import DayPattern, expand, parse_rule
from app.modules.reminders.engine import (
    MISSED_GUIDANCE,
    PRIVATE_BODY,
    REMINDER_TITLE,
    content_for,
    missed_guidance,
    reminder_body,
)
from app.modules.reminders.service import Preferences

EPOCH = datetime(2020, 1, 1, tzinfo=UTC)


def at_local(instants: list[datetime], tz: str) -> list[tuple[date, time]]:
    zone = ZoneInfo(tz)
    return [(i.astimezone(zone).date(), i.astimezone(zone).time()) for i in instants]


def daily(tz: str, times: list[time], start: datetime, days: int, **kw: object) -> list[datetime]:
    return expand(
        times_of_day=times,
        timezone=tz,
        window_start=start,
        window_end=start + timedelta(days=days),
        effective_from=EPOCH,
        effective_until=None,
        **kw,  # type: ignore[arg-type]
    )


# --- time zones and daylight saving -----------------------------------------------------------


def test_india_has_a_fixed_offset() -> None:
    out = daily("Asia/Kolkata", [time(8, 0)], datetime(2026, 9, 1, tzinfo=UTC), 7)
    assert {i.time() for i in out} == {time(2, 30)}  # 08:00 IST is 02:30 UTC every day
    assert len(out) == 7


def test_wall_clock_is_kept_across_dst_end() -> None:
    # New York leaves DST on 2026-11-01: 08:00 local is 12:00 UTC before, 13:00 UTC after.
    out = daily("America/New_York", [time(8, 0)], datetime(2026, 10, 30, tzinfo=UTC), 4)
    assert [t for _, t in at_local(out, "America/New_York")] == [time(8, 0)] * len(out)
    assert {i.hour for i in out} == {12, 13}


def test_nonexistent_spring_forward_time_moves_after_the_gap() -> None:
    # 2026-03-08 02:30 does not exist in New York; the dose is still given that day, at 03:30.
    out = daily("America/New_York", [time(2, 30)], datetime(2026, 3, 7, 12, tzinfo=UTC), 2)
    local = at_local(out, "America/New_York")
    assert (date(2026, 3, 8), time(3, 30)) in local
    assert len(out) == 2


def test_ambiguous_autumn_time_is_given_once() -> None:
    out = daily("America/New_York", [time(1, 30)], datetime(2026, 11, 1, tzinfo=UTC), 1)
    assert len([d for d, _ in at_local(out, "America/New_York") if d == date(2026, 11, 1)]) == 1


def test_weekdays_follow_the_patients_local_day_not_utc() -> None:
    # 00:30 on Mondays in Auckland (UTC+13) is Sunday 11:30 UTC.
    out = daily(
        "Pacific/Auckland",
        [time(0, 30)],
        datetime(2026, 9, 26, tzinfo=UTC),
        9,
        pattern=parse_rule("FREQ=WEEKLY;BYDAY=MO"),
    )
    local = at_local(out, "Pacific/Auckland")
    assert all(d.isoweekday() == 1 for d, _ in local)
    assert all(i.isoweekday() == 7 for i in out)  # Sunday in UTC


def test_every_other_day_keeps_local_time_across_dst() -> None:
    out = daily(
        "Europe/London",
        [time(9, 0)],
        datetime(2026, 10, 20, tzinfo=UTC),
        14,
        start_date=date(2026, 10, 20),
        pattern=DayPattern("every_n_days", 2),
    )
    local = at_local(out, "Europe/London")
    assert [t for _, t in local] == [time(9, 0)] * 7
    assert [(b - a).days for (a, _), (b, _) in pairwise(local)] == [2] * 6


def test_course_dates_are_local_days() -> None:
    out = daily(
        "Asia/Kolkata",
        [time(8, 0), time(20, 0)],
        datetime(2026, 9, 1, tzinfo=UTC),
        10,
        start_date=date(2026, 9, 3),
        end_date=date(2026, 9, 5),
    )
    assert len(out) == 6
    assert {d for d, _ in at_local(out, "Asia/Kolkata")} == {
        date(2026, 9, 3),
        date(2026, 9, 4),
        date(2026, 9, 5),
    }


# --- quiet hours ------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("start", "end", "t", "quiet"),
    [
        (time(22, 0), time(7, 0), time(23, 30), True),
        (time(22, 0), time(7, 0), time(6, 59), True),
        (time(22, 0), time(7, 0), time(7, 0), False),
        (time(13, 0), time(15, 0), time(14, 0), True),
        (time(13, 0), time(15, 0), time(15, 30), False),
        (None, None, time(3, 0), False),
    ],
)
def test_quiet_hours(start: time | None, end: time | None, t: time, quiet: bool) -> None:
    assert Preferences(quiet_hours_start=start, quiet_hours_end=end).in_quiet_hours(t) is quiet


# --- what reminders say -----------------------------------------------------------------------


def _content(with_names: bool = False) -> tuple[str, str]:
    med = Medication(
        name="Tab. Samplemycin",
        strength="500 mg",
        origin=MedicationOrigin.DOCTOR_PRESCRIPTION,
        instructions=None,
    )
    dose = MedicationDose(dose_amount=Decimal("1.000"), dose_unit="tablet")
    c = content_for(dose, med, None, None, None)
    return c.dose or "", reminder_body(c, show_names=with_names)


def test_lock_screen_text_is_private_by_default() -> None:
    dose, body = _content(with_names=False)
    assert body == PRIVATE_BODY
    assert "Samplemycin" not in body
    assert dose == "1 tablet"  # exactly as stored, never recomputed
    assert REMINDER_TITLE == "Time for your medication"


def test_names_only_with_opt_in() -> None:
    _, body = _content(with_names=True)
    assert "Tab. Samplemycin 500 mg" in body
    assert "1 tablet" in body


def test_missed_guidance_is_not_advice() -> None:
    med = Medication(
        name="Tab. Samplemycin",
        strength=None,
        origin=MedicationOrigin.SELF_REPORTED,
        instructions="with water",
    )
    g = missed_guidance(content_for(MedicationDose(), med, None, None, None))
    assert g["message"] == MISSED_GUIDANCE
    assert g["instructions_as_written"] == "with water"
    assert g["instructions_verified"] is False  # the patient's own note, not a prescription
    text = str(g["message"]).lower()
    assert "doctor or pharmacist" in text
    for advice in ("take it now", "take it as soon as", "double", "skip it"):
        assert advice not in text
