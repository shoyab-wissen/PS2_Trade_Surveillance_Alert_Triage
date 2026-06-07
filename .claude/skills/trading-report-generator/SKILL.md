---
name: trading-report-generator
description: Expert in generating compliance reports, market intelligence summaries, and regulatory documentation. Use when creating audit trails, exporting findings, preparing for regulatory review, or generating executive summaries for leadership.
---

<objective>Generate comprehensive trading compliance reports, market intelligence summaries, and regulatory documentation from detection and investigation data.</objective>

<essential_principles>

## Report Types and Purposes

1. **Compliance Report**: Formal documentation for audit and regulatory review with full evidence chain
2. **Executive Summary**: High-level findings for leadership with actionable insights
3. **Market Intelligence**: Trend analysis and pattern identification for strategic decision-making
4. **Regulatory Filing**: SEC/FINRA-compliant documentation for regulatory submission
5. **Audit Trail**: Complete timestamped record of all analysis decisions and actions

## Content Standards

- **Accuracy**: All facts verified against source data
- **Completeness**: All relevant evidence included with traceability
- **Clarity**: Executive summaries understandable to non-technical stakeholders
- **Compliance**: Meets regulatory requirements and audit standards
- **Timeliness**: Generated with appropriate priority based on urgency

## Report Integration Points

1. **Data Source**: Feeds from detection and triage workflows
2. **Formatting**: Multiple export formats (PDF, JSON, CSV, Excel)
3. **Distribution**: Appropriate channels by report type and sensitivity
4. **Archival**: Long-term storage for regulatory hold requirements
5. **Analysis**: Input for pattern trending and strategic planning

</essential_principles>

<intake>
What report would you like to generate?

1. **Compliance report** - Full documentation with evidence for audit/regulatory review
2. **Executive summary** - High-level findings for leadership with recommendations
3. **Market intelligence** - Trend analysis and pattern identification
4. **Regulatory filing** - SEC/FINRA-compliant submission document
5. **Audit trail** - Complete timestamped record of all decisions
6. **Performance metrics** - Detection accuracy and efficiency statistics
7. **Get guidance** - Understand reporting capabilities

**Select option or describe your report need.**
</intake>

<routing>

| Response | Workflow |
|----------|----------|
| 1, "compliance", "formal", "audit" | workflows/generate-compliance-report.md |
| 2, "executive", "summary", "leadership" | workflows/generate-executive-summary.md |
| 3, "intelligence", "trends", "analysis" | workflows/generate-market-intelligence.md |
| 4, "regulatory", "filing", "sec", "finra" | workflows/generate-regulatory-filing.md |
| 5, "audit", "trail", "record", "log" | workflows/generate-audit-trail.md |
| 6, "metrics", "statistics", "performance" | workflows/generate-performance-metrics.md |
| 7, "guidance", "help", "capabilities" | workflows/reporting-guidance.md |

**After selecting, follow the workflow exactly.**

</routing>

<references_preview>
See `references/` for:
- **compliance-standards.md** - Regulatory requirements and audit standards
- **report-templates.md** - Structure and content requirements
- **export-formats.md** - PDF, JSON, CSV, Excel specifications
- **distribution-matrix.md** - Who gets what reports and when

</references_preview>

<quick_reference>

**Key Files in Project:**
- `src/workflows/report_generator.py` - Report generation engine
- `reports/` - Directory for saved reports
- `src/api/routes.py` - Report export endpoints
- `src/triage/prompt_templates.py` - Claude reasoning templates

**Typical Report Lifecycle**:
Detection → Triage → Investigation → Report → Leadership Review → Regulatory Filing (if needed)

**Export Formats Supported**:
- PDF (human-readable with formatting)
- JSON (machine-readable with full structure)
- CSV (tabular data for analysis)
- Excel (detailed workbooks with charts)
- HTML (web-viewable with interactive elements)

**Storage & Retention**:
- Active cases: `reports/active/`
- Resolved cases: `reports/closed/`
- Regulatory hold: `reports/hold/` (7-year minimum)

</quick_reference>
