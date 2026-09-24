"""Pure schedule expansion: which dose times fall in a window (no database access).

Times of day are wall-clock times in the patient's timezone. They are converted to UTC
day by day, so "08:00" stays 08:00 local across daylight-saving changes. A local time
that does not exist on a spring-forward day is moved forward by the gap (a skipped
02:30 becomes 03:30); an ambiguous autumn time uses its first occurrence.
"""

from collections.abc import Iterable
from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo


def local_to_utc(day: date, at: time, tz: ZoneInfo) -> datetime:
    # PEP 495: with fold=0 a non-existent (spring-forward) time resolves with the
    # pre-transition offset, which lands after the gap; an ambiguous (autumn) time
    # resolves to its first occurrence.
    return datetime.combine(day, at).replace(tzinfo=tz, fold=0).astimezone(UTC)


def expand(
    *,
    times_of_day: Iterable[time],
    timezone: str,
    window_start: datetime,
    window_end: datetime,
    effective_from: datetime,
    effective_until: datetime | None,
    start_date: date | None = None,
    end_date: date | None = None,
) -> list[datetime]:
    """UTC instants within [window_start, window_end) and the schedule's validity."""
    tz = ZoneInfo(timezone)
    lo = max(window_start, effective_from)
    hi = min(window_end, effective_until) if effective_until else window_end
    if lo >= hi:
        return []
    out: list[datetime] = []
    day = lo.astimezone(tz).date() - timedelta(days=1)
    last_day = hi.astimezone(tz).date() + timedelta(days=1)
    while day <= last_day:
        if (start_date is None or day >= start_date) and (end_date is None or day <= end_date):
            for at in times_of_day:
                instant = local_to_utc(day, at, tz)
                if lo <= instant < hi:
                    out.append(instant)
        day += timedelta(days=1)
    return sorted(set(out))


def local_day_bounds(day: date, timezone: str) -> tuple[datetime, datetime]:
    """[start, end) of a calendar day in the patient's timezone, in UTC."""
    tz = ZoneInfo(timezone)
    start = datetime.combine(day, time.min, tzinfo=tz).astimezone(UTC)
    end = datetime.combine(day + timedelta(days=1), time.min, tzinfo=tz).astimezone(UTC)
    return start, end
