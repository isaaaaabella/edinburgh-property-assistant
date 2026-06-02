"""CommEntry: a single communication record (email, viewing slot, closing notice...).

Stored in Notion under the "📬 沟通记录" page section (heading_3 buckets) or
locally appended to a per-property JSONL file. Backends translate this neutral
shape into their native representation.
"""

from __future__ import annotations

import html as _html
import json
import re
from dataclasses import asdict, dataclass
from datetime import datetime
from typing import Any, Literal


# HTML emails arrive with <style> blocks whose CSS (e.g. "@font-face { ... }",
# "mso-…", "panose-1: …") leaks into the body. Strip markup + CSS so the stored
# excerpt is the readable text a human would actually want to skim.
_STYLE_SCRIPT_RE = re.compile(r"<(style|script)\b[^>]*>.*?</\1>", re.I | re.S)
_TAG_RE = re.compile(r"<[^>]+>")
_CSS_RULE_RE = re.compile(r"[^{}]*\{[^{}]*\}")           # complete bare CSS rules
# A CSS rule truncated mid-way (no closing brace), e.g. "div.MsoNormal { margin:0".
# The selector part is kept tight (a single token, no spaces) so this can never
# span backwards across sentence prose like "…your enquiry. .ReadMsgBody {".
_CSS_DANGLING_RE = re.compile(r"[.#]?[\w][\w.\-]*\s*\{[^}]*$")
_CSS_DECL_RE = re.compile(r"(?:mso-|panose-|font-family|-ms-[\w-]+|@font-face)[^\n;]*;?", re.I)
_WS_RE = re.compile(r"\s+")


def clean_excerpt(body: str) -> str:
    """Turn a raw (often HTML) email body into a short, readable plain-text snippet.

    Removes <style>/<script> blocks, all tags, complete *and* truncated CSS rules
    (HTML emails carry a long <style> block that, after a 500-char cut, leaves a
    dangling `selector { decl; decl…`), HTML entities, and collapses whitespace.
    Plain-text bodies pass through essentially unchanged. As a final safety net,
    if any brace survives, everything from the first brace on is dropped.
    """
    if not body:
        return ""
    text = _STYLE_SCRIPT_RE.sub(" ", body)
    text = _TAG_RE.sub(" ", text)
    text = re.sub(r"<[^>]*$", " ", text)      # truncated opening tag at the cut boundary
    text = _CSS_RULE_RE.sub(" ", text)        # closed rules anywhere
    text = _CSS_DANGLING_RE.sub(" ", text)    # a rule truncated mid-way
    text = _CSS_DECL_RE.sub(" ", text)        # stray declarations
    text = _html.unescape(text)
    if "{" in text or "}" in text:            # safety net: never leak CSS
        text = re.split(r"[{}]", text, maxsplit=1)[0]
    text = _WS_RE.sub(" ", text).strip()
    return text


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
        excerpt = clean_excerpt(body)
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
