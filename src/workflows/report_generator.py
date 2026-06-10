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

    # ==================================================================
    # Daily Compliance Report PDF
    # ==================================================================

    def generate_daily_report_pdf(self, report_data: dict) -> bytes | None:
        """
        Generate a daily compliance report PDF from the structured report data
        returned by Claude's generate_daily_report().

        Returns raw PDF bytes on success, or None if reportlab is unavailable.
        """
        if not _reportlab_available():
            print("  [PDF] reportlab not installed \u2014 skipping PDF generation")
            return None

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
            KeepTogether,
        )
        import json as _json

        buffer = io.BytesIO()
        doc = SimpleDocTemplate(
            buffer,
            pagesize=A4,
            leftMargin=2 * cm,
            rightMargin=2 * cm,
            topMargin=1.5 * cm,
            bottomMargin=1.5 * cm,
        )

        styles = getSampleStyleSheet()
        story = []

        # \u2500\u2500 Styles \u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500
        title_style = ParagraphStyle(
            "DRTitle", parent=styles["Title"],
            fontSize=18, spaceAfter=4, textColor=colors.HexColor("#1a237e"),
        )
        subtitle_style = ParagraphStyle(
            "DRSubtitle", parent=styles["Normal"],
            fontSize=9, textColor=colors.grey, spaceAfter=12,
        )
        section_title_style = ParagraphStyle(
            "DRSection", parent=styles["Heading2"],
            fontSize=12, spaceBefore=14, spaceAfter=6,
            textColor=colors.HexColor("#0d47a1"),
            borderWidth=0, borderPadding=0,
        )
        body_style = ParagraphStyle(
            "DRBody", parent=styles["Normal"],
            fontSize=9.5, leading=14, spaceAfter=8,
            textColor=colors.HexColor("#212121"),
        )
        note_style = ParagraphStyle(
            "DRNote", parent=styles["Normal"],
            fontSize=8.5, leading=12, textColor=colors.HexColor("#546e7a"),
            leftIndent=12, rightIndent=12, spaceBefore=4, spaceAfter=4,
        )
        bullet_style = ParagraphStyle(
            "DRBullet", parent=styles["Normal"],
            fontSize=9.5, leading=14, leftIndent=20,
            bulletIndent=8, textColor=colors.HexColor("#212121"),
        )

        # \u2500\u2500 Unwrap double-encoded full_report \u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500
        d = report_data
        if (
            isinstance(d.get("full_report"), str)
            and d["full_report"].strip().startswith("{")
        ):
            try:
                inner = _json.loads(d["full_report"])
                if isinstance(inner, dict) and (
                    inner.get("risk_level") or inner.get("executive_summary")
                ):
                    d = inner
            except (ValueError, _json.JSONDecodeError):
                pass

        report_date = d.get("report_date", datetime.utcnow().strftime("%Y-%m-%d"))
        risk_level = d.get("risk_level", "MEDIUM")

        # \u2500\u2500 Title \u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500
        story.append(Paragraph("DAILY SURVEILLANCE REPORT", title_style))
        story.append(Paragraph(
            f"Date: {report_date} &nbsp;&nbsp;|&nbsp;&nbsp; "
            f"Classification: CONFIDENTIAL &nbsp;&nbsp;|&nbsp;&nbsp; "
            f"Generated: {datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')}",
            subtitle_style,
        ))

        # \u2500\u2500 Risk level badge \u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500
        risk_colors = {
            "CRITICAL": colors.HexColor("#c62828"),
            "HIGH": colors.HexColor("#e65100"),
            "MEDIUM": colors.HexColor("#f9a825"),
            "LOW": colors.HexColor("#2e7d32"),
        }
        risk_bg = {
            "CRITICAL": colors.HexColor("#ffebee"),
            "HIGH": colors.HexColor("#fff3e0"),
            "MEDIUM": colors.HexColor("#fffde7"),
            "LOW": colors.HexColor("#e8f5e9"),
        }
        rc = risk_colors.get(risk_level, colors.HexColor("#f9a825"))
        rb = risk_bg.get(risk_level, colors.HexColor("#fffde7"))

        risk_table = Table(
            [[Paragraph(
                f"<b>OVERALL RISK LEVEL: {risk_level}</b>",
                ParagraphStyle("RiskBadge", parent=styles["Normal"],
                               fontSize=11, textColor=rc, alignment=1),
            )]],
            colWidths=[doc.width],
        )
        risk_table.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, -1), rb),
            ("ALIGN", (0, 0), (-1, -1), "CENTER"),
            ("TOPPADDING", (0, 0), (-1, -1), 8),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
            ("BOX", (0, 0), (-1, -1), 1, rc),
            ("ROUNDEDCORNERS", [4, 4, 4, 4]),
        ]))
        story.append(risk_table)
        story.append(Spacer(1, 0.4 * cm))

        # \u2500\u2500 Executive Summary \u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500
        exec_summary = d.get("executive_summary", "")
        if exec_summary:
            story.append(Paragraph("EXECUTIVE SUMMARY", section_title_style))
            summary_table = Table(
                [[Paragraph(exec_summary, ParagraphStyle(
                    "ExecBody", parent=body_style, fontSize=10, leading=15,
                    textColor=colors.HexColor("#1a237e"),
                ))]],
                colWidths=[doc.width],
            )
            summary_table.setStyle(TableStyle([
                ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#e8eaf6")),
                ("TOPPADDING", (0, 0), (-1, -1), 10),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 10),
                ("LEFTPADDING", (0, 0), (-1, -1), 12),
                ("RIGHTPADDING", (0, 0), (-1, -1), 12),
                ("BOX", (0, 0), (-1, -1), 0.5, colors.HexColor("#c5cae9")),
            ]))
            story.append(summary_table)
            story.append(Spacer(1, 0.3 * cm))

        # \u2500\u2500 Full Report \u2014 split into sections \u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500
        full_report = d.get("full_report", "")
        if full_report:
            report_text = str(full_report) if not isinstance(full_report, str) else full_report
            sections = report_text.split("\n\n")
            current_title = ""
            current_body = ""

            def _flush_section():
                nonlocal current_title, current_body
                if not current_title and not current_body.strip():
                    return
                title = current_title or "Report Details"
                # Skip meta lines
                if title.upper().startswith("DAILY SURVEILLANCE REPORT"):
                    title = "Overview"
                if any(skip in title.upper() for skip in ["PREPARED FOR", "CLASSIFICATION"]):
                    return
                story.append(Paragraph(
                    title.title() if title.isupper() else title,
                    section_title_style,
                ))
                # Clean body and wrap paragraphs
                for para in current_body.strip().split("\n"):
                    para = para.strip()
                    if not para:
                        continue
                    # Numbered items
                    if para[0].isdigit() and ". " in para[:5]:
                        story.append(Paragraph(
                            f"<b>{para[:para.index('. ')+2]}</b>{para[para.index('. ')+2:]}",
                            bullet_style,
                        ))
                    # Dash items
                    elif para.startswith("\u2014") or para.startswith("-"):
                        dash_chars = "\u2014- "
                        story.append(Paragraph(
                            f"\u2022 {para.lstrip(dash_chars)}",
                            bullet_style,
                        ))
                    else:
                        story.append(Paragraph(para, body_style))
                current_title = ""
                current_body = ""

            for section in sections:
                section = section.strip()
                if not section:
                    continue
                lines = section.split("\n")
                first_line = lines[0].strip()

                # Detect section headers
                is_header = (
                    bool(first_line)
                    and (
                        all(c.isupper() or c in " \u2014-:/&\n\t0123456789" for c in first_line)
                        or (first_line[0].isdigit() and ". " in first_line[:5]
                            and first_line.split(". ", 1)[1][0].isupper())
                    )
                )
                # Skip "Prepared for" / "Classification" lines
                if any(first_line.upper().startswith(s) for s in [
                    "PREPARED FOR", "CLASSIFICATION"
                ]):
                    continue

                if is_header:
                    _flush_section()
                    current_title = first_line.rstrip(":")
                    current_body = "\n".join(lines[1:])
                else:
                    current_body += "\n\n" + section
            _flush_section()

        # \u2500\u2500 Priority Actions \u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500
        actions = d.get("priority_actions", [])
        if actions:
            story.append(Paragraph("PRIORITY ACTIONS", section_title_style))
            for i, action in enumerate(actions, 1):
                text = action if isinstance(action, str) else str(
                    action.get("action", action.get("description", action))
                )
                story.append(Paragraph(
                    f"<b>{i}.</b> {text}",
                    bullet_style,
                ))
            story.append(Spacer(1, 0.2 * cm))

        # \u2500\u2500 Next Day Focus \u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500
        ndf = d.get("next_day_focus", "")
        if ndf:
            ndf_text = ndf if isinstance(ndf, str) else str(ndf)
            story.append(Paragraph("NEXT DAY FOCUS", section_title_style))
            focus_table = Table(
                [[Paragraph(ndf_text, ParagraphStyle(
                    "FocusBody", parent=body_style,
                    textColor=colors.HexColor("#00695c"),
                ))]],
                colWidths=[doc.width],
            )
            focus_table.setStyle(TableStyle([
                ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#e0f2f1")),
                ("TOPPADDING", (0, 0), (-1, -1), 8),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
                ("LEFTPADDING", (0, 0), (-1, -1), 12),
                ("RIGHTPADDING", (0, 0), (-1, -1), 12),
                ("BOX", (0, 0), (-1, -1), 0.5, colors.HexColor("#80cbc4")),
            ]))
            story.append(focus_table)

        # \u2500\u2500 Footer \u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500
        story.append(Spacer(1, 1 * cm))
        footer_style = ParagraphStyle(
            "DRFooter", parent=styles["Normal"],
            fontSize=7, textColor=colors.grey, alignment=1,
        )
        story.append(Paragraph(
            "CONFIDENTIAL \u2014 Internal Compliance Use Only &nbsp;|&nbsp; "
            "Trade Surveillance &amp; Alert Triage Engine &nbsp;|&nbsp; "
            "Wissen Hackathon 2026",
            footer_style,
        ))

        doc.build(story)
        buffer.seek(0)
        return buffer.read()


