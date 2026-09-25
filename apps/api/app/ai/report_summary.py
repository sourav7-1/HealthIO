"""Future AI report summaries: the interface and guardrails, with no model wired up.

Health Io does not let AI diagnose from reports. No endpoint calls this module yet.
When summaries are switched on, they must go through this module and follow the rules
below (docs/ai/report-summaries.md):

1. **Input:** only VERIFIED reports, and only what is printed on them (test names,
   values, units, printed reference ranges and printed flags). No patient identifiers,
   free-text notes or conclusions. `build_input()` enforces this.
2. **Output:** a fixed schema (`ReportSummary`). It restates what the report contains in
   plain words and lists values the report itself printed as outside its range, plus
   questions to ask the doctor. It has no field for a diagnosis, cause or treatment.
3. **Checks:** `check_summary()` rejects any summary that mentions a value or test not in
   the input, calls a value abnormal or normal when the report did not print that, or
   uses diagnostic or treatment language. A summary that fails a check is discarded,
   never shown.
4. **Presentation:** always labelled as an AI summary that is not a diagnosis, with the
   fixed `DISCLAIMER`, next to the original report. It is shown to doctors first; showing
   it to patients is a separate decision.
"""

import re
from dataclasses import dataclass
from decimal import Decimal
from typing import Protocol

from pydantic import BaseModel, Field

from app.modules.labs.models import ResultFlag, TestReportStatus
from app.modules.labs.service import ReportWithResults

PROMPT_VERSION = "report_summary_v0"  # no prompt exists yet: summaries are not enabled

DISCLAIMER = (
    "This is an automatic summary of what the report says. It is not a diagnosis and "
    "does not say what the results mean for you. Talk to your doctor about your results."
)

# --- input --------------------------------------------------------------------------------


class SummaryValue(BaseModel):
    analyte: str
    value: str  # exactly as printed (number or text)
    unit: str | None = None
    printed_range: str | None = None
    printed_flag: ResultFlag = ResultFlag.UNKNOWN


class ReportSummaryInput(BaseModel):
    """Everything a summarizer may see. Deliberately excludes names, dates of birth,
    identifiers, notes, conclusions and the file itself."""

    test_name: str | None
    report_date: str | None
    values: list[SummaryValue]


class NotSummarizableError(Exception):
    pass


def _fmt(d: Decimal | None) -> str | None:
    return None if d is None else format(d.normalize(), "f")


def build_input(row: ReportWithResults) -> ReportSummaryInput:
    report = row.report
    if report.status != TestReportStatus.VERIFIED:
        raise NotSummarizableError("Only verified reports can be summarized.")
    if not row.results:
        raise NotSummarizableError("The report has no entered values to summarize.")
    values: list[SummaryValue] = []
    for r in row.results:
        low, high = _fmt(r.reference_low), _fmt(r.reference_high)
        printed_range = r.reference_text or (f"{low}-{high}" if low and high else None)
        values.append(
            SummaryValue(
                analyte=r.analyte_name,
                value=_fmt(r.value_numeric) or (r.value_text or ""),
                unit=r.unit,
                printed_range=printed_range,
                printed_flag=r.flag,
            )
        )
    return ReportSummaryInput(
        test_name=report.test_name,
        report_date=report.report_date.isoformat() if report.report_date else None,
        values=values,
    )


# --- output -------------------------------------------------------------------------------


class ValueMention(BaseModel):
    analyte: str
    value: str
    printed_flag: ResultFlag


class ReportSummary(BaseModel):
    """The only shape a summary can have: no diagnosis, cause or treatment fields."""

    overview: str = Field(max_length=800, description="What the report contains, in plain words")
    outside_printed_range: list[ValueMention] = Field(default_factory=list, max_length=50)
    questions_for_doctor: list[str] = Field(default_factory=list, max_length=5)


@dataclass(frozen=True)
class SummaryDraft:
    summary: ReportSummary
    model: str
    prompt_version: str
    disclaimer: str = DISCLAIMER


class ReportSummarizer(Protocol):
    async def summarize(self, source: ReportSummaryInput) -> SummaryDraft: ...


class SummariesDisabledError(Exception):
    pass


class DisabledReportSummarizer:
    """The only implementation today."""

    async def summarize(self, source: ReportSummaryInput) -> SummaryDraft:
        raise SummariesDisabledError("AI report summaries are not enabled.")


# --- guardrails ---------------------------------------------------------------------------

# Words that turn a restatement into a diagnosis, an interpretation or treatment advice.
_FORBIDDEN = re.compile(
    r"\b("
    r"diagnos\w*|you (?:have|may have|might have|probably have)|suffer\w*|"
    r"consistent with|suggest\w*|indicat\w*|likely|unlikely|sign of|signs of|caused by|"
    r"due to|disease|disorder|infection|deficien\w*|cancer|tumou?r|"
    r"treat\w*|prescri\w*|dose|dosage|increase|decrease|stop taking|start taking|"
    r"nothing to worry|no need to see|don't need to|do not need to|healthy|dangerous"
    r")\b",
    re.IGNORECASE,
)
_OUTSIDE = {
    ResultFlag.LOW,
    ResultFlag.HIGH,
    ResultFlag.CRITICAL_LOW,
    ResultFlag.CRITICAL_HIGH,
    ResultFlag.ABNORMAL,
}


def check_summary(summary: ReportSummary, source: ReportSummaryInput) -> list[str]:
    """Problems that make a summary unusable (empty list = passes)."""
    problems: list[str] = []
    texts = [summary.overview, *summary.questions_for_doctor]
    for text in texts:
        match = _FORBIDDEN.search(text)
        if match:
            problems.append(f"forbidden wording: {match.group(0)!r}")
    printed = {(v.analyte.casefold(), v.value): v for v in source.values}
    for m in summary.outside_printed_range:
        v = printed.get((m.analyte.casefold(), m.value))
        if v is None:
            problems.append(f"value not in the report: {m.analyte} {m.value}")
        elif v.printed_flag not in _OUTSIDE or m.printed_flag != v.printed_flag:
            problems.append(f"flag not printed on the report: {m.analyte}")
    # A number in the prose must be one printed on the report (no computed values).
    allowed = {v.value for v in source.values} | {
        n for v in source.values for n in re.findall(r"\d+(?:\.\d+)?", v.printed_range or "")
    }
    for text in texts:
        for number in re.findall(r"\d+(?:\.\d+)?", text):
            if number not in allowed:
                problems.append(f"number not in the report: {number}")
    return problems
