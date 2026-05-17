"""Tests for PropertyRecord + CommEntry round-trips and coercion."""

from __future__ import annotations

from datetime import date

from property_assistant.core.communication import CommEntry
from property_assistant.core.property_record import (
    PropertyRecord,
    _coerce_bool,
    _coerce_float,
    _coerce_int,
)


# ---------- Coercion ----------

def test_coerce_float_handles_pound_and_comma():
    assert _coerce_float("£450,000") == 450000.0
    assert _coerce_float(" 320,500.50 ") == 320500.5
    assert _coerce_float(None) is None
    assert _coerce_float("") is None
    assert _coerce_float("not a number") is None


def test_coerce_int_handles_float_strings():
    assert _coerce_int("3") == 3
    assert _coerce_int("3.7") == 3
    assert _coerce_int(None) is None
    assert _coerce_int("") is None
    assert _coerce_int(True) == 1


def test_coerce_bool_handles_checkmarks():
    assert _coerce_bool("✓") is True
    assert _coerce_bool("✅") is True
    assert _coerce_bool("✗") is False
    assert _coerce_bool("yes") is True
    assert _coerce_bool("no") is False
    assert _coerce_bool(None) is None
    assert _coerce_bool("maybe") is None


# ---------- PropertyRecord factory ----------

def test_from_parsed_extracts_value_and_derived():
    parsed = {
        "regex_extracted": {
            "address": {"value": "10 Marchmont Rd, Edinburgh EH9 1HZ", "page": 1, "source": "title"},
            "postcode": {"value": "EH9 1HZ", "page": 1, "source": "title"},
            "hr_valuation": {"value": "£450,000", "page": 5, "source": "valuation"},
            "bedrooms": {"value": "3", "page": 7, "source": "accommodation"},
            "epc_rating": {"value": "C", "page": 29, "source": "epc"},
        },
        "derived": {
            "category2_count": 6,
            "category3_count": 0,
            "roof_issue": True,
        },
    }
    rec = PropertyRecord.from_parsed(parsed)
    assert rec.address == "10 Marchmont Rd, Edinburgh EH9 1HZ"
    assert rec.postcode == "EH9 1HZ"
    assert rec.hr_valuation == 450000.0
    assert rec.bedrooms == 3
    assert rec.epc_rating == "C"
    assert rec.cat2_count == 6
    assert rec.cat3_count == 0
    assert rec.roof_issue is True
    # Unset fields default to None / empty list
    assert rec.asking_price is None
    assert rec.school_zone == []


def test_from_parsed_handles_missing_fields():
    rec = PropertyRecord.from_parsed({"regex_extracted": {}, "derived": {}})
    assert rec.address == "Unknown address"
    assert rec.bedrooms is None


# ---------- Round-trip ----------

def test_to_dict_roundtrip():
    rec = PropertyRecord(
        address="24 Forth St, EH1 3LH",
        hr_valuation=300000.0,
        bedrooms=2,
        viewing_date=date(2026, 5, 23),
        school_zone=["James Gillespie's ✅", "Boroughmuir ✅"],
    )
    d = rec.to_dict()
    assert d["viewing_date"] == "2026-05-23"
    assert d["school_zone"] == ["James Gillespie's ✅", "Boroughmuir ✅"]

    rec2 = PropertyRecord.from_dict(d)
    assert rec2 == rec


def test_from_dict_coerces_iso_date_string():
    rec = PropertyRecord.from_dict({
        "address": "x",
        "viewing_date": "2026-06-01T11:00:00+01:00",
    })
    assert rec.viewing_date == date(2026, 6, 1)


def test_from_dict_ignores_unknown_keys():
    rec = PropertyRecord.from_dict({"address": "x", "future_field_we_dont_know": 42})
    assert rec.address == "x"


def test_from_dict_school_zone_str_becomes_list():
    rec = PropertyRecord.from_dict({"address": "x", "school_zone": "James Gillespie's ✅"})
    assert rec.school_zone == ["James Gillespie's ✅"]


def test_address_slug():
    assert PropertyRecord(address="24 Forth St, EH1 3LH").address_slug() == "24_forth_st_eh1_3lh"
    assert PropertyRecord(address="").address_slug() == "unknown"


# ---------- CommEntry ----------

def test_commentry_make_truncates_long_body():
    e = CommEntry.make(
        category="viewing",
        sender="agent@espc.com",
        subject="Viewing confirmed for Saturday",
        body="x" * 1000,
    )
    assert e.category == "viewing"
    assert e.sender == "agent@espc.com"
    assert len(e.body_excerpt) == 500
    assert e.body_excerpt.endswith("...")


def test_commentry_roundtrip():
    e = CommEntry.make(category="other", sender="me", subject="hi", body="hello")
    d = e.to_dict()
    e2 = CommEntry.from_dict(d)
    assert e == e2
