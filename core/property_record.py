"""Canonical PropertyRecord dataclass.

This is the single source of truth for property data shared across all storage
backends, analysis modules, and renderers. It is intentionally serialisable
to plain JSON (no nested dataclasses with custom encoders required).
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from datetime import date
from pathlib import Path
from typing import Any


@dataclass
class PropertyRecord:
    """Canonical per-property data shape.

    Fields are typed permissively — None means "unknown / not yet captured".
    """

    # Identity
    address: str
    postcode: str | None = None
    area: str | None = None
    listing_url: str | None = None

    # Valuation
    hr_valuation: float | None = None
    asking_price: float | None = None

    # Physical
    bedrooms: int | None = None
    floor_area: float | None = None  # m²
    floor: str | None = None  # raw select value, e.g. "Ground ⚠️" / "2F ✅"
    is_main_door: bool | None = None
    building_type: str | None = None  # select value
    era: int | None = None  # build year / decade

    # EPC
    epc_rating: str | None = None  # A-G
    epc_score: int | None = None

    # Condition
    cat1_count: int | None = None
    cat2_count: int | None = None
    cat3_count: int | None = None
    roof_issue: bool | None = None

    # Systems / management
    gas_heating: bool | None = None
    building_insurance: bool | None = None
    factor_status: str | None = None
    factor_monthly: float | None = None
    ownership_years: int | None = None

    # Schools / area data
    school_zone: list[str] = field(default_factory=list)
    simd_decile: int | None = None
    flood_risk: str | None = None
    commute_user_min: int | None = None      # 通勤-Duoduo (min)
    commute_partner_min: int | None = None   # 通勤-Jingjun (min)

    # Workflow state
    status: str | None = None              # 状态 select
    viewing_date: date | None = None       # Viewing时间
    closing_date: date | None = None       # Closing Date
    self_score: float | None = None
    partner_score: float | None = None
    worth_second_visit: bool | None = None
    notes: str | None = None

    # Subjective feedback (Notion page blocks; Local stores as fields)
    self_feeling: str | None = None
    partner_feeling: str | None = None

    # Output artefacts
    html_report_url: str | None = None     # file:// or external URL
    pdf_path: str | None = None            # absolute path to source Home Report PDF

    # Storage metadata (not user-facing)
    storage_id: str | None = None          # Notion page_id or LocalJSON address_slug

    # ---------- Factory constructors ----------

    @classmethod
    def from_parsed(cls, parsed: dict[str, Any]) -> "PropertyRecord":
        """Build from the JSON produced by `parse_home_report.py`.

        `parsed` shape: {regex_extracted: {field: {value, page, source}},
                         condition_table: [...], derived: {...}, warnings: [...]}

        Parser uses its own field names (market_valuation, floor_area_m2,
        construction_year_approx, etc.) — we normalize here.
        """
        regex = parsed.get("regex_extracted", {}) or {}
        derived = parsed.get("derived", {}) or {}

        def rv(key: str) -> Any:
            """Read .value from regex_extracted[key] if present."""
            entry = regex.get(key)
            if isinstance(entry, dict):
                return entry.get("value")
            return entry

        def first_present(*keys: str) -> Any:
            """Return the first key's value that is non-None (handles `False` correctly)."""
            for k in keys:
                v = rv(k)
                if v is not None:
                    return v
            return None

        # Tolerate both new naming (used here) and old fallback keys
        is_main_door = _coerce_bool(first_present("main_door_flat", "is_main_door"))
        floor_raw = rv("floor")
        era = _coerce_int(first_present("construction_year_approx", "era", "build_year"))
        has_factor = _coerce_bool(rv("has_factor"))
        has_insurance = _coerce_bool(rv("has_building_insurance"))
        property_type_raw = first_present("property_type", "building_type")

        rec = cls(
            address=rv("address") or "Unknown address",
            postcode=rv("postcode"),
            hr_valuation=_coerce_float(first_present("market_valuation", "hr_valuation", "valuation")),
            asking_price=_coerce_float(rv("asking_price")),
            bedrooms=_coerce_int(rv("bedrooms")),
            floor_area=_coerce_float(first_present("floor_area_m2", "floor_area")),
            floor=_normalize_floor(floor_raw, is_main_door),
            is_main_door=is_main_door,
            building_type=_normalize_building_type(property_type_raw, era),
            era=era,
            epc_rating=rv("epc_rating"),
            epc_score=_coerce_int(rv("epc_score")),
            cat1_count=_coerce_int(derived.get("category1_count")),
            cat2_count=_coerce_int(derived.get("category2_count")),
            cat3_count=_coerce_int(derived.get("category3_count")),
            roof_issue=_coerce_bool(derived.get("roof_issue")),
            gas_heating=_coerce_bool(first_present("gas_central_heating", "gas_heating")),
            building_insurance=has_insurance,
            factor_status=_normalize_factor_status(has_factor, has_insurance),
            factor_monthly=_coerce_float(first_present("factor_cost_monthly", "factor_monthly")),
            ownership_years=_coerce_int(first_present("owner_years", "ownership_years")),
        )
        return rec

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "PropertyRecord":
        """Build from a serialised dict (LocalJSON load / test fixture)."""
        d = dict(data)
        # Coerce date strings → date objects
        for k in ("viewing_date", "closing_date"):
            v = d.get(k)
            if isinstance(v, str) and v:
                try:
                    d[k] = date.fromisoformat(v[:10])
                except ValueError:
                    d[k] = None
        # Ensure school_zone is a list
        sz = d.get("school_zone")
        if sz is None:
            d["school_zone"] = []
        elif isinstance(sz, str):
            d["school_zone"] = [sz] if sz else []
        # Drop unknown keys to stay forward-compatible
        known = {f.name for f in cls.__dataclass_fields__.values()}
        d = {k: v for k, v in d.items() if k in known}
        return cls(**d)

    # ---------- Serialisation ----------

    def to_dict(self) -> dict[str, Any]:
        """Serialise to plain JSON-safe dict (dates → ISO strings)."""
        d = asdict(self)
        for k in ("viewing_date", "closing_date"):
            v = d.get(k)
            if isinstance(v, date):
                d[k] = v.isoformat()
        return d

    def to_json(self, **kwargs: Any) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, **kwargs)

    # ---------- Convenience ----------

    def address_slug(self) -> str:
        """Filesystem-safe identifier derived from address."""
        slug = re.sub(r"[^\w\-]+", "_", self.address.strip().lower(), flags=re.UNICODE)
        slug = re.sub(r"_+", "_", slug).strip("_")
        return slug or "unknown"


# ---------- normalization helpers (PDF parser → Notion-aligned values) ----------

def _normalize_floor(raw: Any, is_main_door: bool | None) -> str | None:
    """Map parser's free-text floor → Notion select option."""
    if raw is None:
        return None
    s = str(raw).lower().strip()
    if "ground" in s:
        return "Ground ⚠️"
    if "first" in s or s.startswith("1f") or s.startswith("1st") or "1楼" in s:
        return "1F ✅" if is_main_door else "1F"
    if "second" in s or s.startswith("2f") or s.startswith("2nd") or "2楼" in s:
        return "2F ✅"
    if "third" in s or s.startswith("3f") or s.startswith("3rd") or "3楼" in s:
        return "3F"
    if "top" in s or "顶" in s:
        return "顶层 ⚠️"
    return str(raw)


def _normalize_building_type(raw: Any, era: int | None) -> str | None:
    """Map parser's free-text property_type → Notion select option.

    Many Scottish HRs don't use the literal word "tenement" — they describe
    "X storey block" or just "flat". For Pre-1919 stone-built flats in
    multi-storey blocks, we infer traditional tenement.
    """
    if raw is None:
        return None
    s = str(raw).lower()
    looks_like_flat_in_block = any(kw in s for kw in [
        "tenement", "block", "storey", "storeys", "story", "stories",
    ]) and any(kw in s for kw in ["flat", "apartment", "maisonette"])

    if "tenement" in s and era and era < 1919:
        return "维多利亚Tenement ✅"
    if era and era < 1919 and looks_like_flat_in_block:
        # Pre-1919 stone-built flat in a multi-storey block ≡ traditional tenement
        return "维多利亚Tenement ✅"
    if "tenement" in s or looks_like_flat_in_block:
        return "Tenement flat"
    if "purpose" in s or "modern" in s or "现代" in s:
        return "现代公寓 ⚠️"
    if "interwar" in s or "战间期" in s:
        return "战间期"
    return "其他"


def _normalize_factor_status(has_factor: bool | None, has_insurance: bool | None) -> str | None:
    """Map (has_factor, has_insurance) booleans → Notion select option."""
    if has_factor is None:
        return None
    if not has_factor:
        return "无 ❌"
    if has_insurance:
        return "专业Factor含保险 ✅"
    return "仅清洁 ⚠️"


# ---------- coercion helpers ----------

def _coerce_int(v: Any) -> int | None:
    if v is None or v == "":
        return None
    if isinstance(v, bool):
        return int(v)
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return None


def _coerce_float(v: Any) -> float | None:
    if v is None or v == "":
        return None
    try:
        s = str(v).replace(",", "").replace("£", "").strip()
        return float(s)
    except (TypeError, ValueError):
        return None


def _coerce_bool(v: Any) -> bool | None:
    if v is None:
        return None
    if isinstance(v, bool):
        return v
    if isinstance(v, (int, float)):
        return bool(v)
    s = str(v).strip().lower()
    if s in {"true", "yes", "y", "1", "✓", "✅"}:
        return True
    if s in {"false", "no", "n", "0", "✗", "❌"}:
        return False
    return None
