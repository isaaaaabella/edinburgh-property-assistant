"""Parity tests across StorageBackend implementations.

Two layers:
1. Pure conversion symmetry — runs unconditionally, no network.
2. Live round-trip on Notion — opt-in via NOTION_PARITY_TEST=1.
   Creates and DELETES a test page with a timestamped address.
"""

from __future__ import annotations

import os
import time
from datetime import date

import pytest

from property_assistant.core.property_record import PropertyRecord
from property_assistant.storage.notion_storage import (
    NOTION_FIELD_MAP,
    NotionStorage,
)


def _make_full_record(addr: str) -> PropertyRecord:
    return PropertyRecord(
        address=addr,
        hr_valuation=420000.0,
        asking_price=410000.0,
        bedrooms=3,
        floor_area=92.5,
        floor="2F ✅",
        is_main_door=True,
        building_type="维多利亚Tenement ✅",
        era=1890,
        epc_rating="C",
        epc_score=72,
        cat2_count=4,
        cat3_count=0,
        roof_issue=False,
        gas_heating=True,
        building_insurance=True,
        factor_status="专业Factor含保险 ✅",
        factor_monthly=18.5,
        ownership_years=12,
        school_zone=["James Gillespie's ✅", "Boroughmuir ✅"],
        simd_decile=9,
        flood_risk="无 ✅",
        commute_user_min=18,
        commute_partner_min=35,
        status="🔍 待看",
        viewing_date=date(2026, 5, 23),
        closing_date=date(2026, 5, 30),
        self_score=8.5,
        partner_score=8.0,
        worth_second_visit=True,
        notes="south-facing, looks promising",
        listing_url="https://www.rightmove.co.uk/properties/12345",
        area="Marchmont",
    )


# ---------- Pure conversion symmetry (no network) ----------

def test_notion_record_to_properties_covers_all_mapped_fields():
    rec = _make_full_record("Symmetry Test 1")
    ns = NotionStorage.__new__(NotionStorage)  # skip __init__
    props = ns._record_to_properties(rec)
    # Every mapped field with non-None value should appear
    expected = {
        notion_name
        for field_name, (notion_name, _) in NOTION_FIELD_MAP.items()
        if getattr(rec, field_name, None) not in (None, [])
    }
    assert set(props.keys()) >= expected - {"HTML报告"}  # html_report_url is None


def test_notion_roundtrip_via_synthetic_page():
    """Build a fake Notion page from a PropertyRecord, parse it back."""
    rec = _make_full_record("Roundtrip Test")
    ns = NotionStorage.__new__(NotionStorage)
    properties = ns._record_to_properties(rec)
    fake_page = {"id": "fake-page-id", "properties": properties}
    rec2 = ns._page_to_record(fake_page)

    # Compare fields known to roundtrip cleanly through Notion
    compare = [
        "address", "hr_valuation", "asking_price", "bedrooms", "floor_area",
        "floor", "is_main_door", "building_type", "era", "epc_rating",
        "epc_score", "cat2_count", "cat3_count", "roof_issue",
        "gas_heating", "building_insurance", "factor_status", "factor_monthly",
        "ownership_years", "school_zone", "simd_decile", "flood_risk",
        "commute_user_min", "commute_partner_min", "status", "viewing_date",
        "closing_date", "self_score", "partner_score", "worth_second_visit",
        "notes", "listing_url", "area",
    ]
    for f in compare:
        assert getattr(rec2, f) == getattr(rec, f), (
            f"field {f}: notion={getattr(rec2, f)!r} vs orig={getattr(rec, f)!r}"
        )


def test_notion_field_map_only_uses_supported_types():
    SUPPORTED = {"title", "rich_text", "number", "select", "multi_select",
                 "checkbox", "date", "url"}
    for field_name, (_, ntype) in NOTION_FIELD_MAP.items():
        assert ntype in SUPPORTED, f"{field_name} uses unsupported type {ntype!r}"


def test_address_is_only_title_field():
    title_fields = [
        f for f, (_, ntype) in NOTION_FIELD_MAP.items() if ntype == "title"
    ]
    assert title_fields == ["address"]


# ---------- Live Notion round-trip (opt-in) ----------

LIVE = os.environ.get("NOTION_PARITY_TEST") == "1"


@pytest.mark.skipif(not LIVE, reason="set NOTION_PARITY_TEST=1 to run live round-trip")
def test_notion_live_upsert_find_delete():
    ns = NotionStorage()
    unique_addr = f"PARITY TEST {int(time.time())} — DELETE ME"
    rec = _make_full_record(unique_addr)
    pid = ns.upsert_property(rec)
    assert pid

    try:
        # Find round-trip
        found = ns.find_by_address(unique_addr)
        assert found is not None
        assert found.storage_id == pid
        assert found.hr_valuation == 420000.0
        assert found.school_zone == ["James Gillespie's ✅", "Boroughmuir ✅"]
        assert found.viewing_date == date(2026, 5, 23)

        # list_by_filter
        results = ns.list_by_filter(
            viewing_date_from=date(2026, 5, 22),
            viewing_date_to=date(2026, 5, 24),
        )
        assert any(r.storage_id == pid for r in results)
    finally:
        # Archive the test page (Notion's soft-delete)
        ns._request("PATCH", f"/pages/{pid}", {"archived": True})


# ---------- Storage factory ----------

def test_factory_returns_local_by_default(monkeypatch, tmp_path):
    monkeypatch.delenv("STORAGE_BACKEND", raising=False)
    monkeypatch.setenv("PROPERTY_DATA_DIR", str(tmp_path / "data"))
    from property_assistant.storage import get_storage
    s = get_storage()
    assert s.name == "local"


def test_factory_returns_notion_when_env_set(monkeypatch):
    monkeypatch.setenv("STORAGE_BACKEND", "notion")
    from property_assistant.storage import get_storage
    s = get_storage()
    assert s.name == "notion"


def test_factory_rejects_unknown_backend(monkeypatch):
    monkeypatch.setenv("STORAGE_BACKEND", "mysql")
    from property_assistant.storage import get_storage
    with pytest.raises(ValueError, match="Unknown STORAGE_BACKEND"):
        get_storage()
