"""Deterministic interpretation of transcribed prescription text (AI_SAFETY.md §4.3).

Only fixed dictionaries and exact patterns are used; no model is involved. When text does
not match, the answer is `None` and the text is kept exactly as written. Each result
carries a plain-language `meaning` shown next to the original text, so a person
confirms the interpretation rather than it being applied silently.
"""

import re
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from app.core.enums import MealRelation


@dataclass(frozen=True)
class Frequency:
    times_per_day: int | None
    is_prn: bool
    meaning: str


@dataclass(frozen=True)
class Dose:
    amount: Decimal
    unit: str | None
    meaning: str


@dataclass(frozen=True)
class Duration:
    days: int
    meaning: str


@dataclass(frozen=True)
class Meal:
    relation: MealRelation
    meaning: str


def _clean(text: str | None) -> str:
    return re.sub(r"\s+", " ", (text or "").strip().lower().rstrip("."))


# --- frequency --------------------------------------------------------------------------------

_FREQUENCY_WORDS: dict[str, Frequency] = {}
_FREQUENCY_TABLE: list[tuple[tuple[str, ...], int, str]] = [
    (("od", "o.d", "qd", "q.d", "once daily", "once a day", "1 time a day", "daily"), 1,
     "once a day"),
    (("bd", "b.d", "bid", "b.i.d", "twice daily", "twice a day", "2 times a day"), 2,
     "twice a day"),
    (("tds", "t.d.s", "tid", "t.i.d", "thrice daily", "three times a day", "3 times a day"), 3,
     "three times a day"),
    (("qid", "q.i.d", "qds", "four times a day", "4 times a day"), 4, "four times a day"),
    (("hs", "h.s", "at bedtime", "bedtime"), 1, "once a day, at bedtime"),
]  # fmt: skip
for words, per_day, meaning in _FREQUENCY_TABLE:
    for w in words:
        _FREQUENCY_WORDS[w] = Frequency(per_day, False, meaning)
for w in (
    "sos",
    "s.o.s",
    "prn",
    "p.r.n",
    "as needed",
    "as required",
    "when needed",
    "when required",
):
    _FREQUENCY_WORDS[w] = Frequency(None, True, "only when needed")

_DOSE_PATTERN = re.compile(r"^(\d)\s*[-\u2013]\s*(\d)\s*[-\u2013]\s*(\d)(?:\s*[-\u2013]\s*(\d))?$")
_SLOTS = ("morning", "afternoon", "evening", "night")


def frequency(text: str | None) -> Frequency | None:
    t = _clean(text)
    if not t:
        return None
    if t in _FREQUENCY_WORDS:
        return _FREQUENCY_WORDS[t]
    m = _DOSE_PATTERN.match(t)
    if m:
        parts = [int(p) for p in m.groups() if p is not None]
        slots = ("morning", "afternoon", "night") if len(parts) == 3 else _SLOTS
        given = [
            f"{n} in the {s}" if s != "night" else f"{n} at night"
            for n, s in zip(parts, slots, strict=True)
            if n
        ]
        if not given:
            return None
        # "1-0-1" means intakes at those times; times_per_day counts the slots taken.
        return Frequency(sum(1 for n in parts if n), False, ", ".join(given))
    return None


# --- meal relation ----------------------------------------------------------------------------

_MEAL: dict[str, Meal] = {}
_MEAL_TABLE: list[tuple[tuple[str, ...], MealRelation, str]] = [
    (("ac", "a.c", "before food", "before meals", "before meal", "before eating"),
     MealRelation.BEFORE_FOOD, "before food"),
    (("pc", "p.c", "after food", "after meals", "after meal", "after eating"),
     MealRelation.AFTER_FOOD, "after food"),
    (("with food", "with meals", "with meal"), MealRelation.WITH_FOOD, "with food"),
    (("empty stomach", "on empty stomach", "on an empty stomach"),
     MealRelation.EMPTY_STOMACH, "on an empty stomach"),
    (("hs", "h.s", "at bedtime", "bedtime"), MealRelation.BEDTIME, "at bedtime"),
]  # fmt: skip
for words, relation, meaning in _MEAL_TABLE:
    for w in words:
        _MEAL[w] = Meal(relation, meaning)


def meal(text: str | None) -> Meal | None:
    return _MEAL.get(_clean(text))


# --- duration ---------------------------------------------------------------------------------

_DAYS = re.compile(r"^(?:x\s*|for\s+)?(\d{1,3})\s*(?:d|day|days)$")
_WEEKS = re.compile(r"^(?:x\s*|for\s+)?(\d{1,2})\s*(?:w|wk|wks|week|weeks)$")
# Indian notation: 5/7 = 5 days, 2/52 = 2 weeks.
_SLASH = re.compile(r"^(\d{1,3})\s*/\s*(7|52)$")


def duration(text: str | None) -> Duration | None:
    """Days and weeks convert exactly. Months do not (a month is not a fixed number of
    days), so '1 month' returns None and stays as written."""
    t = _clean(text)
    if not t:
        return None
    if m := _DAYS.match(t):
        n = int(m.group(1))
        return Duration(n, f"{n} day{'s' if n != 1 else ''}") if n > 0 else None
    if m := _WEEKS.match(t):
        n = int(m.group(1))
        return Duration(n * 7, f"{n} week{'s' if n != 1 else ''} = {n * 7} days") if n > 0 else None
    if m := _SLASH.match(t):
        n = int(m.group(1))
        if n <= 0:
            return None
        if m.group(2) == "7":
            return Duration(n, f"{n} day{'s' if n != 1 else ''} (written as {n}/7)")
        return Duration(
            n * 7, f"{n} week{'s' if n != 1 else ''} = {n * 7} days (written as {n}/52)"
        )
    return None


# --- dose -------------------------------------------------------------------------------------

_UNITS = {
    "tab": "tablet", "tabs": "tablet", "tablet": "tablet", "tablets": "tablet",
    "cap": "capsule", "caps": "capsule", "capsule": "capsule", "capsules": "capsule",
    "ml": "ml", "drop": "drop", "drops": "drop", "puff": "puff", "puffs": "puff",
    "sachet": "sachet", "sachets": "sachet", "unit": "unit", "units": "unit",
    "tsp": "teaspoon", "teaspoon": "teaspoon", "teaspoons": "teaspoon",
}  # fmt: skip
_FRACTIONS = {
    "½": Decimal("0.5"),
    "1/2": Decimal("0.5"),
    "¼": Decimal("0.25"),
    "1/4": Decimal("0.25"),
}
_DOSE = re.compile(r"^(½|¼|1/2|1/4|\d+(?:\.\d+)?)\s*([a-z]+)?$")


def dose(text: str | None) -> Dose | None:
    t = _clean(text)
    m = _DOSE.match(t)
    if not m:
        return None
    raw_amount, raw_unit = m.group(1), m.group(2)
    amount = _FRACTIONS.get(raw_amount) or Decimal(raw_amount)
    if amount <= 0:
        return None
    unit = None
    if raw_unit is not None:
        unit = _UNITS.get(raw_unit)
        if unit is None:
            return None  # unknown unit: keep as written
    shown = format(amount.normalize(), "f")
    return Dose(amount, unit, f"{shown} {unit}" if unit else shown)


# --- strength ---------------------------------------------------------------------------------

_STRENGTH = re.compile(
    r"^(\d+(?:\.\d+)?)\s*(mg|mcg|µg|g|gm|ml|iu|%)(?:\s*/\s*(\d+(?:\.\d+)?)\s*(ml|g|mg))?$"
)


def strength(text: str | None) -> str | None:
    """Spacing and unit case only (500MG → 500 mg); never a different amount or unit."""
    m = _STRENGTH.match(_clean(text))
    if not m:
        return None
    unit = {"gm": "g", "µg": "mcg", "iu": "IU"}.get(m.group(2), m.group(2))
    out = f"{m.group(1)} {unit}" if unit != "%" else f"{m.group(1)}%"
    if m.group(3):
        out += f"/{m.group(3)} {m.group(4)}"
    return out


# --- date -------------------------------------------------------------------------------------

_MONTH_NAMES = ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"]
_MONTHS = {m: i for i, m in enumerate(_MONTH_NAMES, start=1)}
_NUMERIC_DATE = re.compile(r"^(\d{1,2})[./-](\d{1,2})[./-](\d{2}|\d{4})$")
_TEXT_DATE = re.compile(r"^(\d{1,2})(?:st|nd|rd|th)?\s+([a-z]{3})[a-z]*\.?,?\s+(\d{4})$")


def prescription_date(text: str | None, today: date) -> date | None:
    """Day-first (as used in India). Dates in the future are not accepted."""
    t = _clean(text)
    parsed: date | None = None
    try:
        if m := _NUMERIC_DATE.match(t):
            year = int(m.group(3))
            year = year + 2000 if year < 100 else year
            parsed = date(year, int(m.group(2)), int(m.group(1)))
        elif (m := _TEXT_DATE.match(t)) and m.group(2) in _MONTHS:
            parsed = date(int(m.group(3)), _MONTHS[m.group(2)], int(m.group(1)))
    except ValueError:
        return None
    if parsed is None or parsed > today:
        return None
    return parsed
