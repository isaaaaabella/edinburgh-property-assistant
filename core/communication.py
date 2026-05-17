"""CommEntry: a single communication record (email, viewing slot, closing notice...).

Stored in Notion under the "📬 沟通记录" page section (heading_3 buckets) or
locally appended to a per-property JSONL file. Backends translate this neutral
shape into their native representation.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import datetime
from typing import Any, Literal


CommCategory = Literal["viewing", "closing_date", "mortgage", "solicitor", "other"]


@dataclass
class CommEntry:
    """One communication entry."""

    category: str           # one of CommCategory values
    occurred_at: str        # ISO 8601 datetime string, British timezone-aware
    sender: str             # 'agent@espc.com' or '我 / Duoduo' etc.
    subject: str
    body_excerpt: str       # ≤500 chars
    source: str = "email"   # 'email' | 'manual' | 'forward'

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "CommEntry":
        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})

    @classmethod
    def make(
        cls,
        *,
        category: str,
        sender: str,
        subject: str,
        body: str,
        occurred_at: datetime | str | None = None,
        source: str = "email",
    ) -> "CommEntry":
        """Convenience builder that ensures occurred_at is ISO string."""
        if occurred_at is None:
            occurred_at = datetime.now().isoformat(timespec="minutes")
        elif isinstance(occurred_at, datetime):
            occurred_at = occurred_at.isoformat(timespec="minutes")
        excerpt = (body or "").strip()
        if len(excerpt) > 500:
            excerpt = excerpt[:497] + "..."
        return cls(
            category=category,
            occurred_at=occurred_at,
            sender=sender,
            subject=subject.strip(),
            body_excerpt=excerpt,
            source=source,
        )
