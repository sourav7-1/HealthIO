from datetime import UTC, date, datetime, time
from zoneinfo import ZoneInfo

from app.modules.medications.schedules import expand, local_day_bounds, local_to_utc

EPOCH = datetime(2020, 1, 1, tzinfo=UTC)


def test_kolkata_times_convert_to_utc() -> None:
    doses = expand(
        times_of_day=[time(8, 0), time(20, 0)],
        timezone="Asia/Kolkata",
        window_start=datetime(2026, 3, 1, 0, 0, tzinfo=UTC),
        window_end=datetime(2026, 3, 2, 0, 0, tzinfo=UTC),
        effective_from=EPOCH,
        effective_until=None,
    )
    # 08:00 IST = 02:30 UTC, 20:00 IST = 14:30 UTC
    assert doses == [
        datetime(2026, 3, 1, 2, 30, tzinfo=UTC),
        datetime(2026, 3, 1, 14, 30, tzinfo=UTC),
    ]


def test_wall_clock_is_kept_across_dst_changes() -> None:
    ny = ZoneInfo("America/New_York")
    doses = expand(
        times_of_day=[time(8, 0)],
        timezone="America/New_York",
        window_start=datetime(2026, 3, 7, tzinfo=UTC),
        window_end=datetime(2026, 3, 10, tzinfo=UTC),
        effective_from=EPOCH,
        effective_until=None,
    )
    assert [d.astimezone(ny).hour for d in doses] == [8, 8, 8]
    assert len({d.utcoffset() for d in doses}) == 1  # all UTC
    assert doses[0].hour == 13  # EST
    assert doses[-1].hour == 12  # EDT


def test_nonexistent_spring_forward_time_moves_forward() -> None:
    ny = ZoneInfo("America/New_York")
    instant = local_to_utc(date(2026, 3, 8), time(2, 30), ny)
    assert instant.astimezone(ny).hour == 3


def test_effective_period_and_course_dates_are_respected() -> None:
    doses = expand(
        times_of_day=[time(9, 0)],
        timezone="Asia/Kolkata",
        window_start=datetime(2026, 5, 1, tzinfo=UTC),
        window_end=datetime(2026, 5, 10, tzinfo=UTC),
        effective_from=datetime(2026, 5, 3, 12, 0, tzinfo=UTC),
        effective_until=datetime(2026, 5, 8, 0, 0, tzinfo=UTC),
        start_date=date(2026, 5, 1),
        end_date=date(2026, 5, 6),
    )
    ist = ZoneInfo("Asia/Kolkata")
    assert [d.astimezone(ist).date().day for d in doses] == [4, 5, 6]


def test_local_day_bounds() -> None:
    start, end = local_day_bounds(date(2026, 9, 24), "Asia/Kolkata")
    assert start == datetime(2026, 9, 23, 18, 30, tzinfo=UTC)
    assert (end - start).total_seconds() == 86400
