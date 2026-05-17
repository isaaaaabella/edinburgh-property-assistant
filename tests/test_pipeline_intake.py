"""Tests for pipelines.intake."""

from __future__ import annotations

from pathlib import Path

import pytest

from property_assistant.core.property_record import PropertyRecord
from property_assistant.pipelines.intake import (
    classify_email,
    extract_address_hints,
    match_property,
    run,
)


# ---- Classification ----

def test_classify_home_report_via_pdf_name():
    e = {"subject": "see attached", "body": "", "pdf_paths": ["/x/homereport_268.pdf"]}
    assert classify_email(e) == "home_report"


def test_classify_home_report_via_subject():
    e = {"subject": "Your Home Report is ready", "body": "...", "pdf_paths": []}
    assert classify_email(e) == "home_report"


def test_classify_viewing_confirmed():
    e = {"subject": "Viewing confirmed for Saturday", "body": "See you on Saturday 11am"}
    assert classify_email(e) == "viewing_confirmed"


def test_classify_closing_date():
    e = {"subject": "Closing date set", "body": "Best and final by 5pm Friday"}
    assert classify_email(e) == "closing_date"


def test_classify_mortgage():
    e = {"subject": "Your AIP from Nationwide", "body": "Decision in principle approved"}
    assert classify_email(e) == "mortgage"


def test_classify_solicitor():
    e = {"subject": "Title deed update", "body": "Solicitor needs missive signing"}
    assert classify_email(e) == "solicitor"


def test_classify_general_fallback():
    e = {"subject": "Hi", "body": "Just checking in"}
    assert classify_email(e) == "general"


def test_classify_chinese_keywords():
    e = {"subject": "看房时间确认", "body": "周六上午 11 点"}
    assert classify_email(e) == "viewing_confirmed"


# ---- Address extraction ----

def test_extract_address_hints_postcode():
    e = {"subject": "10 Marchmont Rd EH9 1HZ — Home Report", "body": ""}
    hints = extract_address_hints(e)
    assert any("EH9 1HZ" in h for h in hints)


def test_extract_address_hints_street_number():
    e = {"subject": "Re: 29/1 Lutton Place viewing", "body": ""}
    hints = extract_address_hints(e)
    assert any("29/1 Lutton Place" in h for h in hints)


def test_extract_address_hints_dedup():
    e = {"subject": "EH9 1HZ", "body": "Address is EH9 1HZ. See EH9 1HZ on the report."}
    hints = extract_address_hints(e)
    assert sum(1 for h in hints if "EH9 1HZ" in h) == 1


# ---- Match property ----

@pytest.fixture
def local_env(tmp_path, monkeypatch):
    monkeypatch.setenv("STORAGE_BACKEND", "local")
    monkeypatch.setenv("PROPERTY_DATA_DIR", str(tmp_path / "data"))


def test_match_property_by_postcode(local_env):
    from property_assistant.storage import get_storage
    s = get_storage()
    s.upsert_property(PropertyRecord(address="10 Marchmont Rd, Edinburgh EH9 1HZ"))
    e = {"subject": "Viewing for EH9 1HZ", "body": ""}
    rec = match_property(e, s)
    assert rec is not None
    assert "Marchmont" in rec.address


def test_match_property_returns_none_when_no_match(local_env):
    from property_assistant.storage import get_storage
    s = get_storage()
    e = {"subject": "10 Marchmont Rd", "body": ""}
    assert match_property(e, s) is None


# ---- Pipeline run ----

def test_run_dry_run_doesnt_mutate(local_env):
    from property_assistant.storage import get_storage
    s = get_storage()
    rec = PropertyRecord(address="10 Marchmont Rd, EH9 1HZ")
    s.upsert_property(rec)
    emails = [{
        "id": "e1", "sender": "agent@x.com",
        "subject": "Viewing for EH9 1HZ confirmed",
        "body": "See you on Saturday 11am", "pdf_paths": [],
    }]
    result = run(emails=emails, apply=False)
    assert result.dry_run is True
    assert len(result.matched) == 1
    assert "would:" in result.matched[0].action
    # Verify no comm entry was actually written
    reloaded = s.find_by_address("Marchmont")
    assert not s._comms_path(reloaded.address_slug()).exists()


def test_run_apply_writes_comm_entry(local_env):
    from property_assistant.storage import get_storage
    s = get_storage()
    rec = PropertyRecord(address="10 Marchmont Rd, EH9 1HZ")
    s.upsert_property(rec)
    emails = [{
        "id": "e1", "sender": "agent@x.com",
        "subject": "Viewing for EH9 1HZ",
        "body": "See you on Saturday at 11:00am", "pdf_paths": [],
    }]
    result = run(emails=emails, apply=True)
    assert result.dry_run is False
    assert "APPLIED:" in result.matched[0].action
    lines = s._comms_path(rec.address_slug()).read_text().splitlines()
    assert len(lines) == 1
    assert "viewing_confirmed" in lines[0]


def test_run_unmatched_email(local_env):
    emails = [{
        "id": "e1", "sender": "spam@x.com",
        "subject": "Random newsletter", "body": "no addresses here",
    }]
    result = run(emails=emails, apply=False)
    assert len(result.unmatched) == 1
    assert result.unmatched[0].reason == "no_address_hints"


def test_run_surfaces_new_pdfs(local_env):
    emails = [{
        "id": "e1", "sender": "espc@x.com",
        "subject": "Home Report for 30 New Street EH1 1AA",
        "body": "PDF attached", "pdf_paths": ["/tmp/homereport_new.pdf"],
    }]
    result = run(emails=emails, apply=False)
    assert "/tmp/homereport_new.pdf" in result.new_pdfs
    assert any("/home-report" in s for s in result.suggestions)
