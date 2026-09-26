"""Pure checks for Phases 11-12: file rules, the ClamAV integration point, AI report
summary guardrails, the timeline cursor and filters. Placeholder values only."""

import asyncio
import importlib.util
import struct
import uuid
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from app.ai.report_summary import (
    DisabledReportSummarizer,
    NotSummarizableError,
    ReportSummary,
    SummariesDisabledError,
    ValueMention,
    build_input,
    check_summary,
)
from app.core.malware import ClamdScanner, Verdict, parse_clamd_reply
from app.modules.access.permissions import Permission
from app.modules.labs.models import ResultFlag, TestReportStatus
from app.modules.records.models import DocumentType
from app.modules.records.service import allowed_types, can_view, pdf_has_active_content
from app.modules.timeline.models import VERSIONED_TABLES
from app.modules.timeline.service import (
    DoctorRef,
    TimelineEvent,
    TimelineFilter,
    decode_cursor,
    encode_cursor,
    paginate,
)

MIGRATIONS = Path(__file__).resolve().parents[1] / "alembic" / "versions"


# --- files --------------------------------------------------------------------------------


def test_reports_accept_only_pdf_jpeg_png() -> None:
    assert allowed_types(DocumentType.LAB_REPORT) == {"application/pdf", "image/jpeg", "image/png"}
    assert allowed_types(DocumentType.IMAGING_REPORT) == allowed_types(DocumentType.LAB_REPORT)
    assert "image/heic" in allowed_types(DocumentType.PRESCRIPTION)  # phone photos


@pytest.mark.parametrize(
    "marker", [b"/JavaScript", b"/JS (x)", b"/Launch", b"/EmbeddedFile", b"/XFA"]
)
def test_pdfs_with_active_content_are_detected(marker: bytes) -> None:
    assert pdf_has_active_content(b"%PDF-1.7\n<< " + marker + b" >>\n%%EOF")


def test_plain_pdf_is_not_flagged() -> None:
    assert not pdf_has_active_content(b"%PDF-1.7\n<< /Type /Page /JSON 1 >>\n%%EOF")


def test_document_visibility_needs_the_permission_for_its_type() -> None:
    reports = frozenset({Permission.VIEW_REPORTS})
    assert can_view(reports, DocumentType.LAB_REPORT)
    assert not can_view(reports, DocumentType.DISCHARGE_SUMMARY)
    assert not can_view(frozenset({Permission.VIEW_VISITS}), DocumentType.DISCHARGE_SUMMARY)
    assert can_view(reports | {Permission.VIEW_VISITS}, DocumentType.DISCHARGE_SUMMARY)


@pytest.mark.parametrize(
    ("reply", "verdict", "signature"),
    [
        ("stream: OK", Verdict.CLEAN, None),
        ("stream: Eicar-Test-Signature FOUND", Verdict.INFECTED, "Eicar-Test-Signature"),
        ("INSTREAM size limit exceeded. ERROR", Verdict.ERROR, None),
        ("", Verdict.ERROR, None),
    ],
)
def test_clamd_replies(reply: str, verdict: Verdict, signature: str | None) -> None:
    result = parse_clamd_reply(reply)
    assert result.verdict == verdict
    assert result.signature == signature


def test_clamd_scanner_speaks_instream() -> None:
    """A fake clamd: check the INSTREAM framing and answer from the content."""
    received: list[bytes] = []

    async def handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        assert await reader.readexactly(10) == b"zINSTREAM\0"
        data = b""
        while True:
            (size,) = struct.unpack("!L", await reader.readexactly(4))
            if size == 0:
                break
            data += await reader.readexactly(size)
        received.append(data)
        writer.write(b"stream: Test-Sig FOUND\0" if b"BAD" in data else b"stream: OK\0")
        await writer.drain()
        writer.close()

    async def run() -> tuple[Verdict, Verdict, Verdict]:
        server = await asyncio.start_server(handle, "127.0.0.1", 0)
        port = server.sockets[0].getsockname()[1]
        scanner = ClamdScanner("127.0.0.1", port, timeout=5)
        scanner.CHUNK = 7  # force several chunks
        async with server:
            clean = (await scanner.scan(b"placeholder file content")).verdict
            bad = (await scanner.scan(b"placeholder BAD content")).verdict
        down = (await ClamdScanner("127.0.0.1", port, timeout=1).scan(b"x")).verdict
        return clean, bad, down

    clean, bad, down = asyncio.run(run())
    assert (clean, bad, down) == (Verdict.CLEAN, Verdict.INFECTED, Verdict.ERROR)
    assert received == [b"placeholder file content", b"placeholder BAD content"]


def test_versioned_tables_match_the_migrations() -> None:
    found: dict[str, tuple[str, ...]] = {}
    for name in ("0011_record_timeline", "0012_test_report_management"):
        spec = importlib.util.spec_from_file_location(name, MIGRATIONS / f"{name}.py")
        assert spec
        assert spec.loader
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        found |= getattr(module, "VERSIONED", {})
    found["report_shares"] = ()  # 0012 adds its trigger directly
    found["safety_warnings"] = ("last_checked_at",)  # 0014 adds its trigger directly
    assert found == VERSIONED_TABLES


# --- AI report summaries (architecture only) ----------------------------------------------


def _report(status: TestReportStatus = TestReportStatus.VERIFIED) -> Any:
    report = SimpleNamespace(status=status, test_name="Test A", report_date=date(2026, 1, 2))
    results = [
        SimpleNamespace(
            analyte_name="Analyte X",
            value_numeric=Decimal("5.20"),
            value_text=None,
            unit="u",
            reference_low=Decimal("1"),
            reference_high=Decimal("4"),
            reference_text=None,
            flag=ResultFlag.HIGH,
        ),
        SimpleNamespace(
            analyte_name="Analyte Y",
            value_numeric=None,
            value_text="negative",
            unit=None,
            reference_low=None,
            reference_high=None,
            reference_text=None,
            flag=ResultFlag.UNKNOWN,
        ),
    ]
    return SimpleNamespace(report=report, results=results)


def test_only_verified_reports_with_values_can_be_summarized() -> None:
    with pytest.raises(NotSummarizableError):
        build_input(_report(TestReportStatus.PENDING_REVIEW))
    source = build_input(_report())
    assert [v.value for v in source.values] == ["5.2", "negative"]
    assert source.values[0].printed_range == "1-4"
    # No identifiers or free text are part of the input schema.
    assert set(source.model_dump()) == {"test_name", "report_date", "values"}


def test_summaries_are_disabled() -> None:
    with pytest.raises(SummariesDisabledError):
        asyncio.run(DisabledReportSummarizer().summarize(build_input(_report())))


def test_a_restating_summary_passes() -> None:
    source = build_input(_report())
    ok = ReportSummary(
        overview="This report lists two values. Analyte X is 5.2 u; the report prints 1-4.",
        outside_printed_range=[
            ValueMention(analyte="Analyte X", value="5.2", printed_flag=ResultFlag.HIGH)
        ],
        questions_for_doctor=["What do these results mean for me?"],
    )
    assert check_summary(ok, source) == []


@pytest.mark.parametrize(
    "overview",
    [
        "These results suggest a problem.",
        "You have a condition.",
        "This is consistent with an infection.",
        "You should increase the dose.",
        "Nothing to worry about here.",
        "Analyte X is 9.9 u.",  # a number not on the report
    ],
)
def test_diagnostic_or_invented_summaries_are_rejected(overview: str) -> None:
    assert check_summary(ReportSummary(overview=overview), build_input(_report()))


def test_summary_cannot_flag_what_the_report_did_not() -> None:
    source = build_input(_report())
    wrong = ReportSummary(
        overview="Two values.",
        outside_printed_range=[
            ValueMention(analyte="Analyte Y", value="negative", printed_flag=ResultFlag.ABNORMAL),
            ValueMention(analyte="Analyte Z", value="1", printed_flag=ResultFlag.LOW),
        ],
    )
    problems = check_summary(wrong, source)
    assert any("flag not printed" in p for p in problems)
    assert any("not in the report" in p for p in problems)


# --- timeline -----------------------------------------------------------------------------


def _event(day: int, kind: str = "visit", specialty: str | None = "Specialty A") -> TimelineEvent:
    return TimelineEvent(
        at=datetime(2026, 1, day, 10, tzinfo=UTC),
        kind=kind,
        title="t",
        resource_id=uuid.UUID(int=day),
        doctor=DoctorRef(uuid.UUID(int=1000 + day % 2), "Dr", specialty),
    )


def test_cursor_round_trip_and_rejects_garbage() -> None:
    e = _event(3)
    assert decode_cursor(encode_cursor(e)) == (e.at, e.key)
    with pytest.raises(Exception, match="Invalid cursor"):
        decode_cursor("not-a-cursor")


def test_filters_and_pages_both_directions() -> None:
    events = [_event(d, "visit" if d % 2 else "report") for d in range(1, 8)]
    flt = TimelineFilter(kinds=frozenset({"visit"}), date_from=date(2026, 1, 2))
    page = paginate(events, flt, newest_first=True, cursor=None, limit=2)
    assert [e.at.day for e in page.events] == [7, 5]
    assert page.total == 3
    assert page.next_cursor
    rest = paginate(events, flt, newest_first=True, cursor=page.next_cursor, limit=2)
    assert [e.at.day for e in rest.events] == [3]
    assert rest.next_cursor is None
    oldest = paginate(events, TimelineFilter(), newest_first=False, cursor=None, limit=50)
    assert [e.at.day for e in oldest.events] == list(range(1, 8))
    assert oldest.facets.kinds == {"report": 3, "visit": 4}
    none = paginate(
        events, TimelineFilter(specialty="specialty b"), newest_first=True, cursor=None, limit=5
    )
    assert none.events == []
