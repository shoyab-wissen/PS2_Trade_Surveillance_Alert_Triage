"""
report_generator.py — Generates a PDF compliance case summary for escalated alerts.

This is the third automated workflow action (alongside Jira + Slack).
Requires: reportlab  (already in requirements.txt)

Usage:
    gen = ComplianceReportGenerator()
    pdf_bytes = gen.generate_case_pdf(alert, triage_result)
    if pdf_bytes:
        with open("case_COMP-8812.pdf", "wb") as f:
            f.write(pdf_bytes)
"""
from __future__ import annotations

import io
from datetime import datetime

from src.ingestion.schemas import Alert, TriageResult


def _reportlab_available() -> bool:
    try:
        import reportlab  # noqa: F401
        return True
    except ImportError:
        return False


# Severity / verdict colour mapping (raw RGB tuples, no reportlab dependency)
_SEVERITY_COLORS: dict[str, tuple] = {
    "CRITICAL": (0.85, 0.10, 0.10),
    "HIGH":     (0.90, 0.40, 0.00),
    "MEDIUM":   (0.95, 0.75, 0.00),
    "LOW":      (0.20, 0.65, 0.20),
}
_VERDICT_COLORS: dict[str, tuple] = {
    "ESCALATE": (0.85, 0.10, 0.10),
    "REVIEW":   (0.90, 0.55, 0.00),
    "DISMISS":  (0.15, 0.60, 0.20),
}


class ComplianceReportGenerator:
    """
    Generates a PDF compliance case summary for a single escalated alert.

    The PDF includes:
    - Alert header (ID, pattern, trader, instrument, severity, z-score)
    - AI triage verdict (confidence, FP probability, rationale)
    - Key factors bullet list
    - Recommended action
    - Jira ticket reference
    - Footer with generation timestamp
    """

    def generate_case_pdf(self, alert: Alert, triage: TriageResult) -> bytes | None:
        """
        Generate a compliance case PDF.

        Returns raw PDF bytes on success, or None if reportlab is unavailable.
        """
        if not _reportlab_available():
            print("  [PDF] reportlab not installed — skipping PDF generation")
            return None

        # All reportlab imports are local so Pylance sees them as bound
        from reportlab.lib import colors
        from reportlab.lib.pagesizes import A4
        from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
        from reportlab.lib.units import cm
        from reportlab.platypus import (
            Paragraph,
            SimpleDocTemplate,
            Spacer,
            Table,
            TableStyle,
        )

        buffer = io.BytesIO()
        doc = SimpleDocTemplate(
            buffer,
            pagesize=A4,
            leftMargin=2 * cm,
            rightMargin=2 * cm,
            topMargin=2 * cm,
            bottomMargin=2 * cm,
        )

        styles = getSampleStyleSheet()
        story = self._build_story(alert, triage, styles, colors,
                                   Paragraph, ParagraphStyle, Spacer,
                                   Table, TableStyle, cm)
        doc.build(story)
        buffer.seek(0)
        return buffer.read()

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _build_story(self, alert: Alert, triage: TriageResult, styles,  # type: ignore[no-untyped-def]
                     colors, Paragraph, ParagraphStyle, Spacer,
                     Table, TableStyle, cm) -> list:
        story = []

        # ── Title ──────────────────────────────────────────────────────
        title_style = ParagraphStyle(
            "ReportTitle",
            parent=styles["Title"],
            fontSize=16,
            spaceAfter=4,
            textColor=colors.HexColor("#1a1a2e"),
        )
        story.append(Paragraph("COMPLIANCE CASE SUMMARY", title_style))

        subtitle_style = ParagraphStyle(
            "Subtitle",
            parent=styles["Normal"],
            fontSize=9,
            textColor=colors.grey,
            spaceAfter=14,
        )
        story.append(
            Paragraph(
                f"Trade Surveillance &amp; Alert Triage Engine &nbsp;|&nbsp; "
                f"Generated {datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S UTC')}",
                subtitle_style,
            )
        )

        # ── Alert details table ────────────────────────────────────────
        sev_rgb = _SEVERITY_COLORS.get(alert.severity, (0.5, 0.5, 0.5))
        sev_color = colors.Color(*sev_rgb)
        story.append(Paragraph("Alert Details", styles["Heading2"]))
        alert_data = [
            ["Alert ID", alert.alert_id],
            ["Pattern", alert.pattern_type.replace("_", " ")],
            ["Trader", alert.trader_id],
            ["Instrument", alert.instrument],
            ["Severity", alert.severity],
            ["Anomaly", f"+{alert.z_score:.1f}\u03c3 vs 30-day baseline"],
            ["Detected At", alert.detected_at.strftime("%Y-%m-%d %H:%M:%S UTC")],
        ]
        t = Table(alert_data, colWidths=[4.5 * cm, 12 * cm])
        t.setStyle(
            TableStyle(
                [
                    ("BACKGROUND", (0, 0), (0, -1), colors.HexColor("#f0f4f8")),
                    ("FONTNAME", (0, 0), (-1, -1), "Helvetica"),
                    ("FONTNAME", (0, 0), (0, -1), "Helvetica-Bold"),
                    ("FONTSIZE", (0, 0), (-1, -1), 9),
                    ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#c0c8d0")),
                    ("ROWBACKGROUNDS", (1, 4), (1, 4), [sev_color]),
                    ("TEXTCOLOR", (1, 4), (1, 4), colors.white),
                    ("FONTNAME", (1, 4), (1, 4), "Helvetica-Bold"),
                    ("PADDING", (0, 0), (-1, -1), 5),
                    ("TOPPADDING", (0, 0), (-1, -1), 5),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
                ]
            )
        )
        story.append(t)
        story.append(Spacer(1, 0.4 * cm))

        # ── Triage verdict table ───────────────────────────────────────
        vrd_rgb = _VERDICT_COLORS.get(triage.verdict, (0.3, 0.3, 0.3))
        vrd_color = colors.Color(*vrd_rgb)
        story.append(Paragraph("AI Triage Verdict (Claude)", styles["Heading2"]))
        verdict_data = [
            ["Verdict", triage.verdict],
            ["Confidence", f"{triage.confidence * 100:.0f}%"],
            ["False Positive Probability", f"{triage.false_positive_probability * 100:.0f}%"],
            ["API Call Cost", f"${triage.call_cost_usd:.4f} USD"
             if triage.call_cost_usd else "mock / cached"],
        ]
        vt = Table(verdict_data, colWidths=[5.5 * cm, 11 * cm])
        vt.setStyle(
            TableStyle(
                [
                    ("BACKGROUND", (0, 0), (0, -1), colors.HexColor("#f0f4f8")),
                    ("FONTNAME", (0, 0), (-1, -1), "Helvetica"),
                    ("FONTNAME", (0, 0), (0, -1), "Helvetica-Bold"),
                    ("FONTSIZE", (0, 0), (-1, -1), 9),
                    ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#c0c8d0")),
                    ("BACKGROUND", (1, 0), (1, 0), vrd_color),
                    ("TEXTCOLOR", (1, 0), (1, 0), colors.white),
                    ("FONTNAME", (1, 0), (1, 0), "Helvetica-Bold"),
                    ("PADDING", (0, 0), (-1, -1), 5),
                    ("TOPPADDING", (0, 0), (-1, -1), 5),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
                ]
            )
        )
        story.append(vt)
        story.append(Spacer(1, 0.4 * cm))

        # ── Rationale ─────────────────────────────────────────────────
        story.append(Paragraph("Rationale", styles["Heading3"]))
        body_style = ParagraphStyle(
            "Body", parent=styles["Normal"], fontSize=9, leading=13
        )
        story.append(Paragraph(triage.rationale or "(no rationale)", body_style))
        story.append(Spacer(1, 0.3 * cm))

        # ── Key factors ───────────────────────────────────────────────
        if triage.key_factors:
            story.append(Paragraph("Key Factors", styles["Heading3"]))
            for factor in triage.key_factors:
                story.append(Paragraph(f"\u2022&nbsp;&nbsp;{factor}", body_style))
            story.append(Spacer(1, 0.3 * cm))

        # ── Recommended action ────────────────────────────────────────
        story.append(Paragraph("Recommended Action", styles["Heading3"]))
        action_style = ParagraphStyle(
            "Action",
            parent=styles["Normal"],
            fontSize=9,
            leading=13,
            borderPad=6,
            borderColor=colors.HexColor("#e8a000"),
            borderWidth=1,
            backColor=colors.HexColor("#fffbf0"),
        )
        story.append(
            Paragraph(triage.recommended_action or "(none specified)", action_style)
        )
        story.append(Spacer(1, 0.4 * cm))

        # ── Jira reference ────────────────────────────────────────────
        if triage.jira_ticket_id:
            story.append(Paragraph("Case Reference", styles["Heading3"]))
            story.append(
                Paragraph(
                    f"Jira Ticket: <b>{triage.jira_ticket_id}</b> &nbsp;|&nbsp; "
                    f"Assigned to Surveillance Desk L2",
                    body_style,
                )
            )

        # ── Footer divider ────────────────────────────────────────────
        story.append(Spacer(1, 0.8 * cm))
        footer_style = ParagraphStyle(
            "Footer",
            parent=styles["Normal"],
            fontSize=7,
            textColor=colors.grey,
            alignment=1,  # centre
        )
        story.append(
            Paragraph(
                "CONFIDENTIAL \u2014 Internal Compliance Use Only &nbsp;|&nbsp; "
                "Trade Surveillance &amp; Alert Triage Engine &nbsp;|&nbsp; "
                "Wissen Hackathon 2026",
                footer_style,
            )
        )
        return story


