"""LocalJSONStorage — zero-config backend.

Layout under ~/.property_data/:
    index.json                       # {slug: {address, last_updated, viewing_date, status, html_reports: {kind: path}}}
    properties/<slug>.json           # PropertyRecord serialised
    communications/<slug>.jsonl      # CommEntry append-only
    reports/<slug>/                  # HTML reports keyed by date_kind
"""

from __future__ import annotations

import json
import os
import shutil
from datetime import date, datetime
from pathlib import Path
from typing import Any

from property_assistant.core.communication import CommEntry
from property_assistant.core.property_record import PropertyRecord

from .base import StorageBackend


def _data_root() -> Path:
    """Resolve ~/.property_data/ (overridable via PROPERTY_DATA_DIR)."""
    custom = os.getenv("PROPERTY_DATA_DIR")
    root = Path(custom).expanduser() if custom else Path.home() / ".property_data"
    return root


class LocalJSONStorage(StorageBackend):
    name = "local"

    def __init__(self, root: Path | None = None) -> None:
        self.root = root or _data_root()
        self.properties_dir = self.root / "properties"
        self.comms_dir = self.root / "communications"
        self.reports_dir = self.root / "reports"
        self.index_path = self.root / "index.json"
        for d in (self.root, self.properties_dir, self.comms_dir, self.reports_dir):
            d.mkdir(parents=True, exist_ok=True)
        if not self.index_path.exists():
            self.index_path.write_text("{}", encoding="utf-8")

    # ---- index helpers ----

    def _load_index(self) -> dict[str, dict[str, Any]]:
        try:
            return json.loads(self.index_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return {}

    def _save_index(self, idx: dict[str, dict[str, Any]]) -> None:
        self.index_path.write_text(
            json.dumps(idx, ensure_ascii=False, indent=2, sort_keys=True),
            encoding="utf-8",
        )

    def _property_path(self, slug: str) -> Path:
        return self.properties_dir / f"{slug}.json"

    def _comms_path(self, slug: str) -> Path:
        return self.comms_dir / f"{slug}.jsonl"

    def _reports_dir(self, slug: str) -> Path:
        d = self.reports_dir / slug
        d.mkdir(parents=True, exist_ok=True)
        return d

    # ---- StorageBackend impl ----

    def upsert_property(self, record: PropertyRecord) -> str:
        slug = record.address_slug()
        record.storage_id = slug

        # Merge with existing on disk to preserve fields not provided this call
        existing_path = self._property_path(slug)
        if existing_path.exists():
            try:
                existing = json.loads(existing_path.read_text(encoding="utf-8"))
                merged = {**existing, **{k: v for k, v in record.to_dict().items() if v is not None or k in {"school_zone"}}}
                if not record.school_zone and existing.get("school_zone"):
                    merged["school_zone"] = existing["school_zone"]
            except (json.JSONDecodeError, OSError):
                merged = record.to_dict()
        else:
            merged = record.to_dict()

        existing_path.write_text(
            json.dumps(merged, ensure_ascii=False, indent=2, sort_keys=True),
            encoding="utf-8",
        )

        # Update index
        idx = self._load_index()
        entry = idx.get(slug, {})
        entry.update({
            "address": record.address,
            "last_updated": datetime.now().isoformat(timespec="seconds"),
            "viewing_date": merged.get("viewing_date"),
            "status": merged.get("status"),
            "html_reports": entry.get("html_reports", {}),
        })
        idx[slug] = entry
        self._save_index(idx)

        return slug

    def find_by_address(self, keyword: str) -> PropertyRecord | None:
        key = keyword.lower().strip()
        if not key:
            return None
        idx = self._load_index()
        candidates = [
            (slug, meta)
            for slug, meta in idx.items()
            if key in (meta.get("address") or "").lower() or key in slug
        ]
        if not candidates:
            return None
        candidates.sort(key=lambda kv: kv[1].get("last_updated") or "", reverse=True)
        slug = candidates[0][0]
        return self._load_property(slug)

    def list_by_filter(
        self,
        *,
        viewing_date_from: date | None = None,
        viewing_date_to: date | None = None,
        status: str | None = None,
    ) -> list[PropertyRecord]:
        idx = self._load_index()
        out: list[PropertyRecord] = []
        for slug, meta in idx.items():
            if status and meta.get("status") != status:
                continue
            vd_raw = meta.get("viewing_date")
            if viewing_date_from or viewing_date_to:
                if not vd_raw:
                    continue
                try:
                    vd = date.fromisoformat(vd_raw[:10])
                except ValueError:
                    continue
                if viewing_date_from and vd < viewing_date_from:
                    continue
                if viewing_date_to and vd > viewing_date_to:
                    continue
            rec = self._load_property(slug)
            if rec is not None:
                out.append(rec)
        return out

    def append_communication(self, property_id: str, entry: CommEntry) -> None:
        path = self._comms_path(property_id)
        with path.open("a", encoding="utf-8") as f:
            f.write(entry.to_json() + "\n")

    def set_tldr(self, property_id: str, tldr: str | None) -> None:
        """Persist TL;DR on the PropertyRecord file (LocalJSON has no page body)."""
        path = self._property_path(property_id)
        if not path.exists():
            return
        try:
            d = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return
        if tldr:
            d["tldr"] = tldr
        else:
            d.pop("tldr", None)
        path.write_text(
            json.dumps(d, ensure_ascii=False, indent=2, sort_keys=True),
            encoding="utf-8",
        )

    def attach_html_report(self, property_id: str, html_path: str, kind: str) -> str:
        src = Path(html_path).expanduser().resolve()
        if not src.exists():
            raise FileNotFoundError(f"HTML report not found: {src}")
        dst_dir = self._reports_dir(property_id)
        stamp = datetime.now().strftime("%Y-%m-%d_%H%M%S")
        dst = dst_dir / f"{stamp}_{kind}.html"
        shutil.copy2(src, dst)

        # Update index
        idx = self._load_index()
        entry = idx.setdefault(property_id, {})
        reports = entry.setdefault("html_reports", {})
        reports[kind] = str(dst)
        entry["last_updated"] = datetime.now().isoformat(timespec="seconds")
        self._save_index(idx)

        # Also reflect on PropertyRecord file
        rec_path = self._property_path(property_id)
        if rec_path.exists():
            try:
                d = json.loads(rec_path.read_text(encoding="utf-8"))
                d["html_report_url"] = str(dst)
                rec_path.write_text(
                    json.dumps(d, ensure_ascii=False, indent=2, sort_keys=True),
                    encoding="utf-8",
                )
            except (json.JSONDecodeError, OSError):
                pass

        return str(dst)

    def set_subjective_feedback(
        self,
        property_id: str,
        self_feeling: str | None,
        partner_feeling: str | None,
    ) -> None:
        path = self._property_path(property_id)
        if not path.exists():
            raise FileNotFoundError(f"Property not found: {property_id}")
        d = json.loads(path.read_text(encoding="utf-8"))
        if self_feeling is not None:
            d["self_feeling"] = self_feeling
        if partner_feeling is not None:
            d["partner_feeling"] = partner_feeling
        path.write_text(
            json.dumps(d, ensure_ascii=False, indent=2, sort_keys=True),
            encoding="utf-8",
        )

    def health_check(self) -> dict[str, Any]:
        try:
            test = self.root / ".health_probe"
            test.write_text("ok", encoding="utf-8")
            test.unlink()
            return {
                "ok": True,
                "backend": "local",
                "detail": f"writable: {self.root}",
            }
        except Exception as exc:  # noqa: BLE001
            return {
                "ok": False,
                "backend": "local",
                "detail": f"cannot write to {self.root}: {exc}",
            }

    # ---- internal ----

    def _load_property(self, slug: str) -> PropertyRecord | None:
        path = self._property_path(slug)
        if not path.exists():
            return None
        try:
            d = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return None
        rec = PropertyRecord.from_dict(d)
        rec.storage_id = slug
        return rec
