"""StorageBackend abstract base class.

Two concrete implementations:
- `LocalJSONStorage` — zero-config; suitable for sharing with friends who
  don't have Notion. Stores property data + reports under ~/.property_data/
- `NotionStorage` — your usual workflow; reads/writes the existing Notion DB
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import date
from typing import Any

from property_assistant.core.communication import CommEntry
from property_assistant.core.property_record import PropertyRecord


class StorageBackend(ABC):
    """Contract that every storage backend must honour.

    Methods are intentionally minimal — anything that's truly backend-specific
    (e.g., Notion's rich page blocks) lives behind generic verbs like
    `attach_html_report`.
    """

    name: str  # 'notion' | 'local'

    # ---- CRUD on properties ----

    @abstractmethod
    def upsert_property(self, record: PropertyRecord) -> str:
        """Create or update a property; return its storage_id.

        Notion: page_id (uuid). Local: address_slug.
        """

    @abstractmethod
    def find_by_address(self, keyword: str) -> PropertyRecord | None:
        """Find one property whose address contains `keyword` (case-insensitive).

        Returns None if no match. If multiple match, return the most-recently
        updated.
        """

    @abstractmethod
    def list_by_filter(
        self,
        *,
        viewing_date_from: date | None = None,
        viewing_date_to: date | None = None,
        status: str | None = None,
    ) -> list[PropertyRecord]:
        """List properties matching the given filters (any combination)."""

    # ---- Communications ----

    @abstractmethod
    def append_communication(self, property_id: str, entry: CommEntry) -> None:
        """Append one comm entry to the property's communication log.

        Notion: insert into "📬 沟通记录" section under the appropriate
        heading_3 bucket. Local: append to <slug>_comms.jsonl.
        """

    # ---- Artefacts ----

    @abstractmethod
    def attach_html_report(self, property_id: str, html_path: str, kind: str) -> str:
        """Attach an HTML report file to the property.

        `kind`: 'home_report' | 'viewing_brief' | 'compare'

        Notion: write file:// path into the `HTML报告` URL property; add a
                callout block with a 5-line summary at the top of the page.
        Local: copy/link into ~/.property_data/reports/<slug>/ and update index.

        Returns the URL/path that was stored.
        """

    @abstractmethod
    def set_subjective_feedback(
        self,
        property_id: str,
        self_feeling: str | None,
        partner_feeling: str | None,
    ) -> None:
        """Persist post-viewing impressions ("你的感受" / "伴侣的感受")."""

    # ---- Diagnostics ----

    @abstractmethod
    def health_check(self) -> dict[str, Any]:
        """Cheap probe used by `/property` before any subcommand runs.

        Returns {'ok': bool, 'backend': str, 'detail': str}. Must never raise.
        """
