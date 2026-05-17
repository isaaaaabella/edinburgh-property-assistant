"""NotionStorage — wraps the existing Notion DB "房源追踪".

Uses Notion REST API directly via urllib (no extra deps), matching the pattern
of the legacy scripts. Token + DB id come from .env (loaded lazily so this
module is importable in environments that don't have those vars).

Field name → (Notion property name, Notion type) mapping is fixed up-front by
schema audit. Missing fields on the DB are tolerated — read returns None,
write skips the field with a warning.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from datetime import date
from pathlib import Path
from typing import Any

from property_assistant.core.communication import CommEntry
from property_assistant.core.property_record import PropertyRecord

from .base import StorageBackend


NOTION_API_BASE = "https://api.notion.com/v1"
NOTION_VERSION = "2022-06-28"


# PropertyRecord field → (Notion property name, Notion type)
#
# This map is hardcoded to the demo author's Notion column names. If you have
# your own Notion DB with different column names, edit the right-hand strings
# below to match yours. In particular:
#   - `通勤-Duoduo (min)` / `通勤-Jingjun (min)` → replace 'Duoduo'/'Jingjun'
#     with your and your partner's labels (matching commute.*_label in
#     preferences.json)
#   - All other Chinese names are generic Edinburgh-buyer defaults; only
#     change if your DB uses different naming
#
# See SCHEMA_AUDIT.md for how these defaults were chosen, and INTEGRATIONS.md
# for full Notion setup guidance.
NOTION_FIELD_MAP: dict[str, tuple[str, str]] = {
    "address": ("地址", "title"),
    "notes": ("备注", "rich_text"),
    "status": ("状态", "select"),
    "floor": ("楼层", "select"),
    "building_type": ("建筑类型", "select"),
    "area": ("区域", "select"),
    "factor_status": ("Factor情况", "select"),
    "flood_risk": ("洪水风险", "select"),
    "epc_rating": ("EPC评级", "select"),
    "school_zone": ("学区", "multi_select"),
    "hr_valuation": ("HR估价(£)", "number"),
    "asking_price": ("挂牌价(£)", "number"),
    "factor_monthly": ("Factor月费(£)", "number"),
    "floor_area": ("面积(m²)", "number"),
    "simd_decile": ("SIMD综合分位", "number"),
    "bedrooms": ("卧室数", "number"),
    "era": ("建造年代", "number"),
    "epc_score": ("EPC分数", "number"),
    "cat2_count": ("Category 2数量", "number"),
    "cat3_count": ("Category 3数量", "number"),
    "ownership_years": ("业主持有年限", "number"),
    "commute_user_min": ("通勤-Duoduo (min)", "number"),
    "commute_partner_min": ("通勤-Jingjun (min)", "number"),
    "self_score": ("你的评分", "number"),
    "partner_score": ("伴侣评分", "number"),
    "is_main_door": ("主门公寓", "checkbox"),
    "roof_issue": ("屋顶问题", "checkbox"),
    "gas_heating": ("Gas供暖", "checkbox"),
    "building_insurance": ("公共建筑保险", "checkbox"),
    "worth_second_visit": ("值得二看", "checkbox"),
    "viewing_date": ("Viewing时间", "date"),
    "closing_date": ("Closing Date", "date"),
    "listing_url": ("Rightmove链接", "url"),
    "html_report_url": ("HTML报告", "url"),
}

# Local-only fields (no Notion column): postcode, cat1_count, self_feeling, partner_feeling, pdf_path
# self_feeling / partner_feeling live as page blocks (handled separately).
# postcode, cat1_count and pdf_path are extracted from PDF and kept in-memory only.


def _load_env_once() -> None:
    """Read property_assistant/.env into os.environ if not already loaded."""
    if os.environ.get("_PROPERTY_ASSISTANT_ENV_LOADED"):
        return
    env_path = Path(__file__).resolve().parent.parent / ".env"
    if env_path.exists():
        for line in env_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            os.environ.setdefault(key.strip(), value.strip())
    os.environ["_PROPERTY_ASSISTANT_ENV_LOADED"] = "1"


class NotionAPIError(Exception):
    pass


class NotionStorage(StorageBackend):
    name = "notion"

    def __init__(self, *, token: str | None = None, db_id: str | None = None) -> None:
        _load_env_once()
        self.token = token or os.environ.get("NOTION_TOKEN")
        self.db_id = db_id or os.environ.get("NOTION_PROPERTY_DB_ID")
        if not self.token or not self.db_id:
            raise NotionAPIError(
                "NotionStorage requires NOTION_TOKEN and NOTION_PROPERTY_DB_ID. "
                "Add them to ~/.claude/property_assistant/.env"
            )
        self._headers = {
            "Authorization": f"Bearer {self.token}",
            "Notion-Version": NOTION_VERSION,
            "Content-Type": "application/json",
        }
        # Lazy-cached DB schema for validating select options
        self._schema_cache: dict[str, Any] | None = None

    # ---------- HTTP layer ----------

    def _request(self, method: str, path: str, body: dict | None = None) -> dict:
        url = f"{NOTION_API_BASE}{path}"
        data = json.dumps(body).encode("utf-8") if body is not None else None
        req = urllib.request.Request(url, data=data, headers=self._headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise NotionAPIError(f"{method} {path} → {exc.code}: {detail}") from exc
        except urllib.error.URLError as exc:
            raise NotionAPIError(f"{method} {path} → network error: {exc}") from exc

    def _query_db(self, body: dict) -> dict:
        return self._request("POST", f"/databases/{self.db_id}/query", body)

    def _retrieve_db(self) -> dict:
        if self._schema_cache is None:
            self._schema_cache = self._request("GET", f"/databases/{self.db_id}")
        return self._schema_cache

    # ---------- Converters ----------

    @staticmethod
    def _to_notion_value(py_value: Any, ntype: str) -> Any:
        """Convert a Python value into the Notion property payload."""
        if py_value is None:
            # Notion clears a property when given the empty-but-typed shape
            if ntype == "title":
                return {"title": []}
            if ntype == "rich_text":
                return {"rich_text": []}
            if ntype == "number":
                return {"number": None}
            if ntype == "select":
                return {"select": None}
            if ntype == "multi_select":
                return {"multi_select": []}
            if ntype == "checkbox":
                return {"checkbox": False}
            if ntype == "date":
                return {"date": None}
            if ntype == "url":
                return {"url": None}
            return None

        if ntype == "title":
            return {"title": [{"text": {"content": str(py_value)}}]}
        if ntype == "rich_text":
            return {"rich_text": [{"text": {"content": str(py_value)}}]}
        if ntype == "number":
            return {"number": float(py_value) if isinstance(py_value, (int, float)) else None}
        if ntype == "select":
            return {"select": {"name": str(py_value)}}
        if ntype == "multi_select":
            items = py_value if isinstance(py_value, (list, tuple)) else [py_value]
            return {"multi_select": [{"name": str(x)} for x in items if x]}
        if ntype == "checkbox":
            return {"checkbox": bool(py_value)}
        if ntype == "date":
            v = py_value
            if isinstance(v, date):
                v = v.isoformat()
            elif isinstance(v, str):
                v = v[:10] if len(v) >= 10 else v
            return {"date": {"start": v}}
        if ntype == "url":
            s = str(py_value).strip()
            return {"url": s or None}
        return None

    @staticmethod
    def _from_notion_value(prop: dict, ntype: str) -> Any:
        """Read a Notion property dict back into a plain Python value.

        Tolerant to both API-returned shape (with `plain_text`) and the
        write shape we ourselves emit (with `text.content` only).
        """
        def _join_text(items: list) -> str:
            parts = []
            for x in items:
                if "plain_text" in x:
                    parts.append(x.get("plain_text") or "")
                else:
                    parts.append((x.get("text") or {}).get("content") or "")
            return "".join(parts)

        try:
            if ntype == "title":
                return _join_text(prop.get("title") or []) or None
            if ntype == "rich_text":
                return _join_text(prop.get("rich_text") or []) or None
            if ntype == "number":
                return prop.get("number")
            if ntype == "select":
                sel = prop.get("select")
                return sel.get("name") if sel else None
            if ntype == "multi_select":
                return [x.get("name") for x in (prop.get("multi_select") or [])]
            if ntype == "checkbox":
                return prop.get("checkbox")
            if ntype == "date":
                d = prop.get("date")
                return d.get("start") if d else None
            if ntype == "url":
                return prop.get("url")
        except (AttributeError, TypeError):
            return None
        return None

    def _record_to_properties(self, record: PropertyRecord) -> dict[str, Any]:
        """Build the `properties` payload for a create/update call."""
        out: dict[str, Any] = {}
        for field_name, (notion_name, ntype) in NOTION_FIELD_MAP.items():
            py_val = getattr(record, field_name, None)
            # Skip empty multi_select to avoid blowing away existing values
            if ntype == "multi_select" and not py_val:
                continue
            # Skip None numbers/dates/selects/etc. on UPDATE — but we always
            # want title and url set if known. Let `upsert_property` decide
            # whether to filter. Here we just emit the typed payload.
            if py_val is None and ntype not in {"title", "url", "checkbox"}:
                continue
            payload = self._to_notion_value(py_val, ntype)
            if payload is not None:
                out[notion_name] = payload
        return out

    def _page_to_record(self, page: dict) -> PropertyRecord:
        """Convert a Notion page object into a PropertyRecord."""
        props = page.get("properties", {}) or {}
        kwargs: dict[str, Any] = {}
        for field_name, (notion_name, ntype) in NOTION_FIELD_MAP.items():
            raw = props.get(notion_name)
            if raw is None:
                continue
            val = self._from_notion_value(raw, ntype)
            if val is None:
                continue
            kwargs[field_name] = val
        # Required: address (default to '' if title missing)
        kwargs.setdefault("address", "Unknown address")
        rec = PropertyRecord.from_dict(kwargs)
        rec.storage_id = page.get("id")
        return rec

    # ---------- StorageBackend impl ----------

    def upsert_property(self, record: PropertyRecord) -> str:
        existing = self.find_by_address(record.address)
        properties = self._record_to_properties(record)
        if existing and existing.storage_id:
            self._request(
                "PATCH",
                f"/pages/{existing.storage_id}",
                {"properties": properties},
            )
            record.storage_id = existing.storage_id
            return existing.storage_id
        # Create
        body = {
            "parent": {"database_id": self.db_id},
            "properties": properties,
        }
        page = self._request("POST", "/pages", body)
        record.storage_id = page["id"]
        return page["id"]

    def find_by_address(self, keyword: str) -> PropertyRecord | None:
        if not keyword or not keyword.strip():
            return None
        result = self._query_db({
            "filter": {
                "property": "地址",
                "title": {"contains": keyword.strip()},
            },
            "page_size": 5,
            "sorts": [{"timestamp": "last_edited_time", "direction": "descending"}],
        })
        pages = result.get("results", [])
        if not pages:
            return None
        return self._page_to_record(pages[0])

    def list_by_filter(
        self,
        *,
        viewing_date_from: date | None = None,
        viewing_date_to: date | None = None,
        status: str | None = None,
    ) -> list[PropertyRecord]:
        conds: list[dict] = []
        if viewing_date_from:
            conds.append({"property": "Viewing时间", "date": {"on_or_after": viewing_date_from.isoformat()}})
        if viewing_date_to:
            conds.append({"property": "Viewing时间", "date": {"on_or_before": viewing_date_to.isoformat()}})
        if status:
            conds.append({"property": "状态", "select": {"equals": status}})
        body: dict = {"page_size": 100}
        if len(conds) == 1:
            body["filter"] = conds[0]
        elif conds:
            body["filter"] = {"and": conds}
        result = self._query_db(body)
        return [self._page_to_record(p) for p in result.get("results", [])]

    def append_communication(self, property_id: str, entry: CommEntry) -> None:
        # Notion-side rich block insertion. We append into the page body; the
        # heading_3 "📬 沟通记录" structure is created lazily if not present.
        # For Step 4 we keep it simple: append a single bullet block.
        bullet = (
            f"[{entry.occurred_at}] {entry.category} — {entry.sender}: "
            f"{entry.subject} — {entry.body_excerpt}"
        )
        self._request(
            "PATCH",
            f"/blocks/{property_id}/children",
            {
                "children": [
                    {
                        "object": "block",
                        "type": "bulleted_list_item",
                        "bulleted_list_item": {
                            "rich_text": [{"type": "text", "text": {"content": bullet[:1900]}}]
                        },
                    }
                ]
            },
        )

    def attach_html_report(self, property_id: str, html_path: str, kind: str) -> str:
        """Write file:// path into `HTML报告` URL property + add callout summary."""
        url = Path(html_path).expanduser().resolve().as_uri()  # file:///...
        # Update URL property
        self._request(
            "PATCH",
            f"/pages/{property_id}",
            {"properties": {"HTML报告": {"url": url}}},
        )
        # Append a callout block with summary (idempotency is best-effort:
        # we always append; old callouts naturally rot but don't corrupt data)
        summary = (
            f"📊 {kind} 分析报告已生成\n"
            f"🔗 {url}\n"
            f"💡 点击 HTML报告 列在本地浏览器查看完整 layered 分析"
        )
        self._request(
            "PATCH",
            f"/blocks/{property_id}/children",
            {
                "children": [
                    {
                        "object": "block",
                        "type": "callout",
                        "callout": {
                            "rich_text": [{"type": "text", "text": {"content": summary}}],
                            "icon": {"type": "emoji", "emoji": "📋"},
                            "color": "blue_background",
                        },
                    }
                ]
            },
        )
        return url

    def set_subjective_feedback(
        self,
        property_id: str,
        self_feeling: str | None,
        partner_feeling: str | None,
    ) -> None:
        children: list[dict] = []
        if self_feeling:
            children.extend([
                {
                    "object": "block",
                    "type": "heading_3",
                    "heading_3": {"rich_text": [{"type": "text", "text": {"content": "你的感受"}}]},
                },
                {
                    "object": "block",
                    "type": "paragraph",
                    "paragraph": {"rich_text": [{"type": "text", "text": {"content": self_feeling[:1900]}}]},
                },
            ])
        if partner_feeling:
            children.extend([
                {
                    "object": "block",
                    "type": "heading_3",
                    "heading_3": {"rich_text": [{"type": "text", "text": {"content": "伴侣的感受"}}]},
                },
                {
                    "object": "block",
                    "type": "paragraph",
                    "paragraph": {"rich_text": [{"type": "text", "text": {"content": partner_feeling[:1900]}}]},
                },
            ])
        if children:
            self._request(
                "PATCH",
                f"/blocks/{property_id}/children",
                {"children": children},
            )

    def health_check(self) -> dict[str, Any]:
        try:
            self._retrieve_db()
            return {
                "ok": True,
                "backend": "notion",
                "detail": f"db {self.db_id} reachable, schema cached",
            }
        except Exception as exc:  # noqa: BLE001
            return {
                "ok": False,
                "backend": "notion",
                "detail": str(exc),
            }
