"""Home Report PDF parser package.

Modules:
- base              — shared constants, PDF tooling wrappers, FieldEvidence
- sections          — Single Survey / Energy / PQ page range detection
- extractors        — 18 field-level extract_* functions (template-agnostic)
- condition_table/  — textual and Allied-image strategies + cat_notes_contradictions
- derived           — compute_derived, validate, epc_regulatory_risk
- dispatcher        — detect_template + parse() + CLI entry point

CLI entry point (preserves legacy contract):
    python -m property_assistant.parsers.dispatcher <pdf_path>

Or via the legacy shim:
    python parse_home_report.py <pdf_path>
"""
