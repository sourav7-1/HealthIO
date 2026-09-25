# AI report summaries: design (not enabled)

Health Io does not let AI diagnose from reports. This document fixes the rules for a future feature that restates a report in plain words. Nothing calls a model today. `DisabledReportSummarizer` is the only implementation.

## Pipeline
```
verified TestReport + results
   │ build_input()        only printed values; no name, DOB, IDs, notes, conclusion, file
   ▼
ReportSummaryInput ──► ReportSummarizer (provider interface, like VisionExtractor)
   │                      prompt: report_summary_v1 (to be written and evaluated)
   ▼
ReportSummary (fixed schema: overview, outside_printed_range[], questions_for_doctor[])
   │ check_summary()      rejects: unknown tests/values, flags or numbers not printed,
   │                      diagnostic/causal/treatment/reassurance wording
   ▼
stored as an AI draft (future table: report_summaries with report id + content hash,
model, prompt version, checks passed, created_at) and shown with DISCLAIMER
```

## Rules
1. **Input:** only VERIFIED reports, because unverified uploads might not even be the patient's. Only values as printed.
2. **No interpretation:** no diagnosis, no cause, no "normal/nothing to worry about", no dose or treatment advice. The schema has no field for them, and the wording check blocks them in free text.
3. **Only what is printed:**
   - "Outside range" can repeat only a flag the report printed (`ResultFlag` from the report). A value is never computed against a range.
   - Every number in the text must appear on the report.
4. **Checked before use:** a summary that fails any check is discarded and logged (without content), never shown.
5. **Presentation:**
   - Labelled "AI summary: not a diagnosis", with `DISCLAIMER`, next to the original report and values.
   - Shown to doctors first. Showing it to patients needs a separate clinical safety review.
6. **Audit and cost:** every call is audited (provider, model, prompt version, token counts), rate-limited per user, and needs the `use_ai_assistant` permission.
7. **Evals before release:** a golden set of synthetic reports with expected pass/fail outcomes. CI blocks a prompt or model change that lets through any forbidden output.

## Code
- `app/ai/report_summary.py`: `build_input`, `ReportSummary`, `ReportSummarizer`, `DisabledReportSummarizer`, `check_summary`, `DISCLAIMER`.
- Tests: `tests/test_records_and_reports.py`.
