"""PDF rendering of a prescription document (ReportLab, in-process).

Lays out exactly what `PrescriptionDocument` holds. Non-current versions carry a banner
on every page. The footer prints the revision and the content fingerprint so a printed
copy can be checked against the record.

Fonts: the built-in Helvetica covers Latin-1 only. Set HIO_PDF_FONT_PATH (and optionally
HIO_PDF_FONT_BOLD_PATH) to a Unicode TTF, e.g. Noto Sans, to print names and
instructions in other scripts. Without it, characters outside Latin-1 are replaced so
the PDF still renders; the web view always shows the original text.
"""

import io
import os
from xml.sax.saxutils import escape

from reportlab.lib import colors
from reportlab.lib.enums import TA_RIGHT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen.canvas import Canvas
from reportlab.platypus import (
    Flowable,
    HRFlowable,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

from app.modules.prescriptions.document import (
    PrescriptionDocument,
    meal_words,
    status_banner,
)


def _fonts() -> tuple[str, str, bool]:
    """(regular, bold, unicode) font names, registering a TTF once if configured."""
    path = os.environ.get("HIO_PDF_FONT_PATH")
    if path and os.path.exists(path):
        if "HioSans" not in pdfmetrics.getRegisteredFontNames():
            pdfmetrics.registerFont(TTFont("HioSans", path))
            bold = os.environ.get("HIO_PDF_FONT_BOLD_PATH")
            pdfmetrics.registerFont(
                TTFont("HioSans-Bold", bold if bold and os.path.exists(bold) else path)
            )
        return "HioSans", "HioSans-Bold", True
    return "Helvetica", "Helvetica-Bold", False


class ReportLabPdfRenderer:
    content_type = "application/pdf"
    file_extension = "pdf"

    def render(self, document: PrescriptionDocument) -> bytes:
        regular, bold, unicode_ok = _fonts()

        def t(value: object) -> str:
            text = "" if value is None else str(value)
            if not unicode_ok:
                text = text.encode("latin-1", "replace").decode("latin-1")
            return escape(text).replace("\n", "<br/>")

        base = getSampleStyleSheet()["Normal"]
        body = ParagraphStyle("body", parent=base, fontName=regular, fontSize=9.5, leading=12.5)
        small = ParagraphStyle("small", parent=body, fontSize=8, leading=10, textColor=colors.grey)
        head = ParagraphStyle("head", parent=body, fontName=bold, fontSize=14, leading=17)
        label = ParagraphStyle("label", parent=body, fontName=bold, fontSize=9.5)
        right = ParagraphStyle("right", parent=body, alignment=TA_RIGHT)
        rx_mark = ParagraphStyle("rx", parent=head, fontSize=20, leading=22)

        story: list[Flowable] = []
        p = document.prescriber
        if p is not None:
            story.append(Paragraph(t(p.name), head))
            creds = ", ".join(p.qualifications)
            line = " · ".join(x for x in (creds, p.specialty) if x)
            if line:
                story.append(Paragraph(t(line), body))
            if p.registration_number:
                reg = f"Reg. No. {p.registration_number}"
                if p.registration_council:
                    reg += f" ({p.registration_council})"
                story.append(Paragraph(t(reg), body))
            practice = " · ".join(x for x in (p.practice_name, p.practice_address) if x)
            if practice:
                story.append(Paragraph(t(practice), small))
        story.append(Spacer(1, 3 * mm))
        story.append(HRFlowable(width="100%", thickness=0.8, color=colors.black))
        story.append(Spacer(1, 2 * mm))

        pt = document.patient
        patient_line = "Patient details not shared"
        if pt is not None:
            bits = [pt.name]
            if pt.age_years is not None:
                bits.append(f"{pt.age_years} y")
            if pt.sex and pt.sex != "unknown":
                bits.append(pt.sex.capitalize())
            patient_line = " · ".join(bits)
        date_line = (
            f"Date: {document.prescribed_on.strftime('%d %b %Y')}" if document.prescribed_on else ""
        )
        story.append(
            Table(
                [
                    [
                        Paragraph(f"<b>Patient:</b> {t(patient_line)}", body),
                        Paragraph(t(date_line), right),
                    ]
                ],
                colWidths=["70%", "30%"],
                style=TableStyle(
                    [
                        ("VALIGN", (0, 0), (-1, -1), "TOP"),
                        ("LEFTPADDING", (0, 0), (-1, -1), 0),
                        ("RIGHTPADDING", (0, 0), (-1, -1), 0),
                    ]
                ),
            )
        )
        if document.diagnosis_as_written:
            story.append(Spacer(1, 2 * mm))
            story.append(
                Paragraph(
                    "<b>Diagnosis / assessment (as documented):</b> "
                    + t(document.diagnosis_as_written),
                    body,
                )
            )
        story.append(Spacer(1, 3 * mm))
        story.append(Paragraph("Rx", rx_mark))

        rows: list[list[Flowable]] = [
            [
                Paragraph("#", label),
                Paragraph("Medicine", label),
                Paragraph("Dose &amp; frequency", label),
                Paragraph("Duration", label),
            ]
        ]
        for i in document.items:
            name = f"<b>{t(i.medicine)}</b>"
            if i.strength:
                name += f" {t(i.strength)}"
            if i.dosage_form:
                name += f" <font color='grey'>({t(i.dosage_form)})</font>"
            if i.generic_name:
                name += f"<br/><font size='8' color='grey'>Generic: {t(i.generic_name)}</font>"
            parts = [x for x in (i.dose, i.frequency, meal_words(i.meal_relation), i.route) if x]
            if i.is_prn:
                parts.append(f"when needed: {i.prn_reason}" if i.prn_reason else "when needed")
            how = t(" · ".join(parts)) if parts else ""
            if i.instructions:
                how += ("<br/>" if how else "") + f"<i>{t(i.instructions)}</i>"
            how_par = Paragraph(how or "-", body)
            duration = (
                f"{i.duration_days} days" if i.duration_days else ("As needed" if i.is_prn else "-")
            )
            rows.append(
                [
                    Paragraph(str(i.sequence), body),
                    Paragraph(name, body),
                    how_par,
                    Paragraph(t(duration), body),
                ]
            )
        story.append(
            Table(
                rows,
                colWidths=[8 * mm, 70 * mm, 72 * mm, 25 * mm],
                repeatRows=1,
                style=TableStyle(
                    [
                        ("LINEBELOW", (0, 0), (-1, 0), 0.6, colors.black),
                        ("LINEBELOW", (0, 1), (-1, -1), 0.25, colors.lightgrey),
                        ("VALIGN", (0, 0), (-1, -1), "TOP"),
                        ("LEFTPADDING", (0, 0), (-1, -1), 2),
                    ]
                ),
            )
        )
        if document.advice:
            story.append(Spacer(1, 4 * mm))
            story.append(Paragraph(f"<b>Notes and advice:</b> {t(document.advice)}", body))
        if document.follow_up_on:
            story.append(Spacer(1, 2 * mm))
            fu = f"<b>Follow-up:</b> on or before {document.follow_up_on.strftime('%d %b %Y')}"
            if document.follow_up_instructions:
                fu += f" - {t(document.follow_up_instructions)}"
            story.append(Paragraph(fu, body))
        if document.valid_until:
            story.append(
                Paragraph(f"Valid until {document.valid_until.strftime('%d %b %Y')}", small)
            )
        if document.revision > 1:
            story.append(Spacer(1, 2 * mm))
            story.append(
                Paragraph(
                    f"<b>Corrected prescription (version {document.revision}).</b> "
                    f"Reason: {t(document.revision_reason)}",
                    body,
                )
            )
        if document.status == "cancelled" and document.cancel_reason:
            story.append(Paragraph(f"<b>Cancelled:</b> {t(document.cancel_reason)}", body))

        story.append(Spacer(1, 12 * mm))
        signed = "Electronically issued" if document.issued_at else "Not issued"
        if document.issued_at:
            signed += f" on {document.issued_at.strftime('%d %b %Y %H:%M UTC')}"
        story.append(Paragraph(t(signed), right))
        if p is not None:
            story.append(Paragraph(f"<b>{t(p.name)}</b>", right))

        banner = status_banner(document)
        footer = f"Health Io prescription {document.id} · version {document.revision}"
        if document.content_sha256:
            footer += f" · SHA-256 {document.content_sha256}"

        def decorate(canvas: Canvas, _doc: object) -> None:
            canvas.saveState()
            width, height = A4
            if banner:
                canvas.setFillColor(colors.HexColor("#b42318"))
                canvas.setFont(bold, 10)
                canvas.drawCentredString(width / 2, height - 10 * mm, banner)
                canvas.setFillColor(colors.Color(0.7, 0.1, 0.1, alpha=0.12))
                canvas.setFont(bold, 60)
                canvas.translate(width / 2, height / 2)
                canvas.rotate(35)
                canvas.drawCentredString(0, 0, document.status.replace("_", " ").upper())
                canvas.rotate(-35)
                canvas.translate(-width / 2, -height / 2)
            canvas.setFillColor(colors.grey)
            canvas.setFont(regular, 6.5)
            canvas.drawString(15 * mm, 10 * mm, footer)
            canvas.drawRightString(width - 15 * mm, 10 * mm, f"Page {canvas.getPageNumber()}")
            canvas.restoreState()

        buf = io.BytesIO()
        doc = SimpleDocTemplate(
            buf,
            pagesize=A4,
            leftMargin=15 * mm,
            rightMargin=15 * mm,
            topMargin=16 * mm,
            bottomMargin=18 * mm,
            title=f"Prescription {document.prescribed_on or ''}",
            author=p.name if p else "Health Io",
            subject="Prescription",
            creator="Health Io",
        )
        doc.build(story, onFirstPage=decorate, onLaterPages=decorate)
        return buf.getvalue()


RENDERERS = {"pdf": ReportLabPdfRenderer()}
