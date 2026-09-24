"""Pure schedule expansion: which dose times fall in a window (no database access).

Supported schedules
- fixed times: wall-clock times of day ("08:00", "20:00") in the patient's timezone.
  Converted to UTC day by day, so 08:00 stays 08:00 local across daylight-saving changes.
  A local time that does not exist on a spring-forward day is moved forward by the gap
  (a skipped 02:30 becomes 03:30); an ambiguous autumn time uses its first occurrence.
- interval: every N minutes/hours from an anchor time, in *elapsed* time (an 8-hourly
  antibiotic stays 8 hours apart even across a DST change).
- as needed (PRN): no scheduled doses.

Day patterns (fixed times only): every day, every N days (counted from the start date),
or chosen weekdays. Stored as a restricted RRULE subset:
    FREQ=DAILY | FREQ=DAILY;INTERVAL=2 | FREQ=WEEKLY;BYDAY=MO,TH

Every result respects the schedule's validity window and the medicine's start/end dates.
"""

import re
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

WEEKDAYS = ("MO", "TU", "WE", "TH", "FR", "SA", "SU")  # ISO order, Monday = 1


class InvalidPattern(ValueError):  # noqa: N818 - a ValueError subtype, named for its meaning
    pass


@dataclass(frozen=True)
class DayPattern:
    kind: str = "daily"  # daily | every_n_days | weekdays
    every: int = 1
    weekdays: frozenset[int] = field(default_factory=frozenset)  # ISO 1..7

    def matches(self, day: date, anchor: date | None) -> bool:
        if self.kind == "weekdays":
            return day.isoweekday() in self.weekdays
        if self.kind == "every_n_days":
            if anchor is None:
                return True
            delta = (day - anchor).days
            return delta >= 0 and delta % self.every == 0
        return True

    def describe(self) -> str:
        if self.kind == "weekdays":
            names = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
            return "on " + ", ".join(names[d - 1] for d in sorted(self.weekdays))
        if self.kind == "every_n_days":
            return "every other day" if self.every == 2 else f"every {self.every} days"
        return "every day"


DAILY = DayPattern()
_RULE = re.compile(r"^FREQ=(DAILY|WEEKLY)(?:;(INTERVAL=\d{1,2}|BYDAY=[A-Z,]+))?$")


def parse_rule(rule: str | None) -> DayPattern:
    if not rule:
        return DAILY
    m = _RULE.match(rule.strip().upper())
    if not m:
        raise InvalidPattern(f"Unsupported repeat rule: {rule!r}")
    freq, part = m.group(1), m.group(2)
    if freq == "DAILY":
        if part is None:
            return DAILY
        if not part.startswith("INTERVAL="):
            raise InvalidPattern("DAILY supports only INTERVAL.")
        n = int(part.split("=")[1])
        if not 1 <= n <= 30:
            raise InvalidPattern("Repeat every 1 to 30 days.")
        return DAILY if n == 1 else DayPattern("every_n_days", n)
    if part is None or not part.startswith("BYDAY="):
        raise InvalidPattern("WEEKLY needs BYDAY.")
    days = part.split("=")[1].split(",")
    if not days or any(d not in WEEKDAYS for d in days) or len(set(days)) != len(days):
        raise InvalidPattern("Unknown weekday.")
    return DayPattern("weekdays", 1, frozenset(WEEKDAYS.index(d) + 1 for d in days))


def format_rule(pattern: DayPattern) -> str | None:
    if pattern.kind == "every_n_days":
        return f"FREQ=DAILY;INTERVAL={pattern.every}"
    if pattern.kind == "weekdays":
        return "FREQ=WEEKLY;BYDAY=" + ",".join(WEEKDAYS[d - 1] for d in sorted(pattern.weekdays))
    return None


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
    pattern: DayPattern = DAILY,
) -> list[datetime]:
    """UTC instants of a fixed-times schedule within [window_start, window_end)."""
    tz = ZoneInfo(timezone)
    lo = max(window_start, effective_from)
    hi = min(window_end, effective_until) if effective_until else window_end
    if lo >= hi:
        return []
    anchor = start_date or effective_from.astimezone(tz).date()
    out: list[datetime] = []
    day = lo.astimezone(tz).date() - timedelta(days=1)
    last_day = hi.astimezone(tz).date() + timedelta(days=1)
    while day <= last_day:
        in_dates = (start_date is None or day >= start_date) and (
            end_date is None or day <= end_date
        )
        if in_dates and pattern.matches(day, anchor):
            for at in times_of_day:
                instant = local_to_utc(day, at, tz)
                if lo <= instant < hi:
                    out.append(instant)
        day += timedelta(days=1)
    return sorted(set(out))


def expand_interval(
    *,
    interval_minutes: int,
    anchor_time: time,
    timezone: str,
    window_start: datetime,
    window_end: datetime,
    effective_from: datetime,
    effective_until: datetime | None,
    start_date: date | None = None,
    end_date: date | None = None,
) -> list[datetime]:
    """Every `interval_minutes` of elapsed time, anchored at `anchor_time` local on the
    first day of the schedule (the later of start_date and the day it took effect)."""
    if interval_minutes < 15:
        raise InvalidPattern("Intervals shorter than 15 minutes are not supported.")
    tz = ZoneInfo(timezone)
    first_day = effective_from.astimezone(tz).date()
    if start_date and start_date > first_day:
        first_day = start_date
    anchor = local_to_utc(first_day, anchor_time, tz)
    lo = max(window_start, effective_from)
    hi = min(window_end, effective_until) if effective_until else window_end
    if end_date is not None:
        hi = min(hi, local_to_utc(end_date + timedelta(days=1), time.min, tz))
    if start_date is not None:
        lo = max(lo, local_to_utc(start_date, time.min, tz))
    if lo >= hi:
        return []
    step = timedelta(minutes=interval_minutes)
    k = max(0, -(-int((lo - anchor).total_seconds()) // int(step.total_seconds())))
    out = []
    instant = anchor + k * step
    while instant < hi:
        if instant >= lo:
            out.append(instant)
        instant += step
    return out


def local_day_bounds(day: date, timezone: str) -> tuple[datetime, datetime]:
    """[start, end) of a calendar day in the patient's timezone, in UTC."""
    tz = ZoneInfo(timezone)
    start = datetime.combine(day, time.min, tzinfo=tz).astimezone(UTC)
    end = datetime.combine(day + timedelta(days=1), time.min, tzinfo=tz).astimezone(UTC)
    return start, end


def doses_per_day(
    *, schedule_type: str, times: list[time], interval_minutes: int | None
) -> float | None:
    if schedule_type == "fixed_times":
        return float(len(times))
    if schedule_type == "interval" and interval_minutes:
        return 1440 / interval_minutes
    return None
