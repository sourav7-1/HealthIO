"""Scheduling engine and change policy (pure code, no database)."""

from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal
from itertools import pairwise

import pytest

from app.modules.medications import change_policy as policy
from app.modules.medications.schedules import (
    DAILY,
    DayPattern,
    InvalidPattern,
    expand,
    expand_interval,
    format_rule,
    parse_rule,
)

FAR = datetime(2020, 1, 1, tzinfo=UTC)


# --- day patterns -----------------------------------------------------------------------------


@pytest.mark.parametrize(
    "rule", [None, "FREQ=DAILY", "FREQ=DAILY;INTERVAL=2", "FREQ=WEEKLY;BYDAY=MO,TH"]
)
def test_rules_round_trip(rule: str | None) -> None:
    pattern = parse_rule(rule)
    assert parse_rule(format_rule(pattern)) == pattern


@pytest.mark.parametrize(
    "rule", ["FREQ=HOURLY", "FREQ=WEEKLY", "FREQ=WEEKLY;BYDAY=XX", "FREQ=DAILY;INTERVAL=99", "junk"]
)
def test_unsupported_rules_are_refused(rule: str) -> None:
    with pytest.raises(InvalidPattern):
        parse_rule(rule)


def test_every_other_day_counts_from_the_start_date() -> None:
    out = expand(
        times_of_day=[time(9, 0)],
        timezone="Asia/Kolkata",
        window_start=datetime(2026, 9, 1, tzinfo=UTC),
        window_end=datetime(2026, 9, 8, tzinfo=UTC),
        effective_from=FAR,
        effective_until=None,
        start_date=date(2026, 9, 2),
        pattern=DayPattern("every_n_days", 2),
    )
    assert [d.date() for d in out] == [date(2026, 9, 2), date(2026, 9, 4), date(2026, 9, 6)]


def test_weekdays_only() -> None:
    out = expand(
        times_of_day=[time(20, 0)],
        timezone="UTC",
        window_start=datetime(2026, 9, 21, tzinfo=UTC),  # a Monday
        window_end=datetime(2026, 9, 28, tzinfo=UTC),
        effective_from=FAR,
        effective_until=None,
        pattern=parse_rule("FREQ=WEEKLY;BYDAY=MO,TH"),
    )
    assert [d.isoweekday() for d in out] == [1, 4]


# --- interval schedules -----------------------------------------------------------------------


def test_interval_keeps_elapsed_time_across_dst() -> None:
    # US clocks go back on 2026-11-01; an 8-hourly dose stays exactly 8 hours apart.
    out = expand_interval(
        interval_minutes=8 * 60,
        anchor_time=time(6, 0),
        timezone="America/New_York",
        window_start=datetime(2026, 10, 31, tzinfo=UTC),
        window_end=datetime(2026, 11, 3, tzinfo=UTC),
        effective_from=datetime(2026, 10, 31, tzinfo=UTC),
        effective_until=None,
    )
    gaps = {b - a for a, b in pairwise(out)}
    assert gaps == {timedelta(hours=8)}
    assert len(out) >= 8


def test_interval_respects_the_course_dates() -> None:
    out = expand_interval(
        interval_minutes=12 * 60,
        anchor_time=time(8, 0),
        timezone="Asia/Kolkata",
        window_start=datetime(2026, 9, 1, tzinfo=UTC),
        window_end=datetime(2026, 9, 10, tzinfo=UTC),
        effective_from=datetime(2026, 9, 1, tzinfo=UTC),
        effective_until=None,
        start_date=date(2026, 9, 2),
        end_date=date(2026, 9, 3),
    )
    assert len(out) == 4  # 2 days x twice a day


# --- change policy ----------------------------------------------------------------------------

BD = policy.Prescribed(
    times_per_day=2,
    dose_amount=Decimal("1"),
    dose_unit="tablet",
    meal_relation="after_food",
    is_prn=False,
    frequency_text="1-0-1",
)


def regimen(times: tuple[str, ...] = ("08:00", "20:00"), **kw: object) -> policy.Regimen:
    base: dict[str, object] = {
        "schedule_type": "fixed_times",
        "times": tuple(time.fromisoformat(t) for t in times),
        "dose_amount": Decimal("1"),
        "dose_unit": "tablet",
        "meal_relation": "after_food",
    }
    base.update(kw)
    return policy.Regimen(**base)  # type: ignore[arg-type]


def codes(a: policy.Assessment) -> set[str]:
    return {f.code for f in a.findings}


def test_moving_reminder_times_is_the_patients_choice() -> None:
    a = policy.assess(regimen(("07:30", "21:00")), prescribed=BD)
    assert a.requires == "none"


def test_timing_warnings_need_acknowledgement() -> None:
    close = policy.assess(regimen(("08:00", "09:00")), prescribed=BD)
    assert "doses_very_close" in codes(close)
    assert "times_outside_pattern" in codes(close)  # 1-0-1 means morning and night
    assert close.requires == "acknowledgement"
    night = policy.assess(regimen(("02:00", "14:00")), prescribed=None)
    assert "night_time" in codes(night)


@pytest.mark.parametrize(
    ("change", "code"),
    [
        ({"times": (time(8, 0), time(14, 0), time(20, 0))}, "frequency_differs"),
        ({"dose_amount": Decimal("2")}, "dose_differs"),
        ({"meal_relation": "before_food"}, "meal_differs"),
        ({"pattern": DayPattern("every_n_days", 2)}, "days_differ"),
        ({"schedule_type": "as_needed", "times": ()}, "prn_differs"),
        (
            {"schedule_type": "interval", "times": (time(8, 0),), "interval_minutes": 480},
            "frequency_differs",
        ),
    ],
)
def test_clinically_relevant_changes_need_a_clinician(change: dict[str, object], code: str) -> None:
    base = regimen()
    proposed = policy.Regimen(**{**base.__dict__, **change})  # type: ignore[arg-type]
    a = policy.assess(proposed, prescribed=BD)
    assert code in codes(a)
    assert a.requires == "clinician"


def test_a_dose_the_prescription_does_not_state_needs_a_clinician() -> None:
    no_dose = policy.Prescribed(2, None, None, None, False, "BD")
    a = policy.assess(regimen(), prescribed=no_dose)
    assert "dose_not_prescribed" in codes(a)


def test_own_medicines_only_get_warnings() -> None:
    a = policy.assess(
        regimen(("08:00", "14:00", "20:00"), dose_amount=Decimal("3")), prescribed=None
    )
    assert a.requires == "none"
    assert regimen().pattern == DAILY
