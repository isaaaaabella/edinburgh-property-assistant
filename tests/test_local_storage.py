"""Tests for LocalJSONStorage."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from property_assistant.core.communication import CommEntry
from property_assistant.core.property_record import PropertyRecord
from property_assistant.storage.local_json_storage import LocalJSONStorage


@pytest.fixture
def storage(tmp_path: Path) -> LocalJSONStorage:
    return LocalJSONStorage(root=tmp_path / "data")


def _rec(addr: str = "10 Marchmont Rd, EH9 1HZ", **kwargs) -> PropertyRecord:
    return PropertyRecord(address=addr, **kwargs)


def test_health_check_succeeds_on_writable_dir(storage: LocalJSONStorage):
    h = storage.health_check()
    assert h["ok"] is True
    assert h["backend"] == "local"


def test_upsert_creates_files_and_index(storage: LocalJSONStorage):
    rec = _rec(hr_valuation=450000.0, bedrooms=3, status="🔍 待看")
    pid = storage.upsert_property(rec)
    assert pid == rec.address_slug()
    assert storage._property_path(pid).exists()
    idx = storage._load_index()
    assert pid in idx
    assert idx[pid]["address"] == rec.address
    assert idx[pid]["status"] == "🔍 待看"


def test_upsert_merges_existing_fields(storage: LocalJSONStorage):
    pid = storage.upsert_property(_rec(hr_valuation=450000.0, bedrooms=3))
    # Second upsert with only viewing_date set should keep hr_valuation
    storage.upsert_property(_rec(viewing_date=date(2026, 5, 23)))
    loaded = storage._load_property(pid)
    assert loaded.hr_valuation == 450000.0
    assert loaded.bedrooms == 3
    assert loaded.viewing_date == date(2026, 5, 23)


def test_find_by_address_case_insensitive(storage: LocalJSONStorage):
    storage.upsert_property(_rec("24 Forth Street, EH1 3LH"))
    storage.upsert_property(_rec("10 Marchmont Rd, EH9 1HZ"))
    rec = storage.find_by_address("FORTH")
    assert rec is not None
    assert "Forth" in rec.address


def test_find_by_address_returns_none_when_no_match(storage: LocalJSONStorage):
    storage.upsert_property(_rec("24 Forth Street, EH1 3LH"))
    assert storage.find_by_address("nonexistent") is None


def test_list_by_filter_status(storage: LocalJSONStorage):
    storage.upsert_property(_rec("A", status="🔍 待看"))
    storage.upsert_property(_rec("B", status="👀 已看"))
    storage.upsert_property(_rec("C", status="🔍 待看"))
    out = storage.list_by_filter(status="🔍 待看")
    assert {r.address for r in out} == {"A", "C"}


def test_list_by_filter_viewing_date_range(storage: LocalJSONStorage):
    storage.upsert_property(_rec("A", viewing_date=date(2026, 5, 20)))
    storage.upsert_property(_rec("B", viewing_date=date(2026, 5, 23)))
    storage.upsert_property(_rec("C", viewing_date=date(2026, 5, 25)))
    out = storage.list_by_filter(
        viewing_date_from=date(2026, 5, 22),
        viewing_date_to=date(2026, 5, 24),
    )
    assert [r.address for r in out] == ["B"]


def test_append_communication_writes_jsonl(storage: LocalJSONStorage):
    pid = storage.upsert_property(_rec())
    entry = CommEntry.make(category="viewing", sender="agent@x.com", subject="Visit", body="Saturday 11am")
    storage.append_communication(pid, entry)
    lines = storage._comms_path(pid).read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    assert "Saturday 11am" in lines[0]


def test_attach_html_report_copies_and_indexes(storage: LocalJSONStorage, tmp_path: Path):
    pid = storage.upsert_property(_rec())
    fake = tmp_path / "report.html"
    fake.write_text("<html>hi</html>", encoding="utf-8")
    url = storage.attach_html_report(pid, str(fake), "home_report")
    assert Path(url).exists()
    idx = storage._load_index()
    assert idx[pid]["html_reports"]["home_report"] == url
    loaded = storage._load_property(pid)
    assert loaded.html_report_url == url


def test_set_subjective_feedback(storage: LocalJSONStorage):
    pid = storage.upsert_property(_rec())
    storage.set_subjective_feedback(pid, "明亮宽敞", "采光一般")
    loaded = storage._load_property(pid)
    assert loaded.self_feeling == "明亮宽敞"
    assert loaded.partner_feeling == "采光一般"


def test_attach_html_report_missing_file_raises(storage: LocalJSONStorage):
    pid = storage.upsert_property(_rec())
    with pytest.raises(FileNotFoundError):
        storage.attach_html_report(pid, "/nonexistent/file.html", "home_report")
