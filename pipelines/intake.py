"""Intake pipeline — sync new emails, classify, match to properties.

Default behavior is dry-run: lists what would happen but doesn't mutate storage.
Pass `apply=True` to actually write CommEntries / update viewing dates / etc.

This pipeline doesn't auto-run /home-report on newly-discovered PDFs — it just
surfaces them as next-step suggestions. The user (or /property --apply) decides.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from dataclasses import asdict, dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Any, Literal

from property_assistant.core.communication import CommEntry
from property_assistant.core.property_record import PropertyRecord
from property_assistant.storage import get_storage


FETCH_EMAILS_SCRIPT = (
    Path(__file__).resolve().parent.parent / "fetch_emails.py"
)


EmailCategory = Literal[
    "viewing_confirmed",
    "closing_date",
    "mortgage",
    "solicitor",
    "home_report",
    "general",
]


# ---------- Classification ----------

# Each tuple: (category, list of regex patterns matched against subject + body)
_PATTERNS: list[tuple[str, list[str]]] = [
    ("home_report", [
        r"home\s+report",
        r"single\s+survey",
        r"valuation\s+report",
    ]),
    ("viewing_confirmed", [
        r"viewing\s+(confirmed|booked|scheduled)",
        r"see\s+you\s+(on|at)\s+\w+day",
        r"appointment\s+(for|on|confirmed)",
        r"看房\s*确认",
        r"看房时间",
    ]),
    ("closing_date", [
        r"closing\s+date",
        r"deadline\s+for\s+offers?",
        r"best\s+and\s+final",
        r"offers?\s+(by|over|invited)",
        r"截止日期",
    ]),
    ("mortgage", [
        r"\b(mortgage|aip|agreement\s+in\s+principle|decision\s+in\s+principle)\b",
        r"\b(lender|broker|nationwide|halifax|santander)\b",
        r"贷款",
    ]),
    ("solicitor", [
        r"\b(solicitor|missive|title\s+deed|conveyancing|titles?)\b",
        r"律师|公证",
    ]),
]


def classify_email(email: dict[str, Any]) -> str:
    """Return the matching EmailCategory based on subject + body keywords."""
    subject = (email.get("subject") or "")
    body = (email.get("body") or "")
    has_pdf = bool(email.get("pdf_paths"))
    combined = (subject + "\n" + body).lower()

    # PDF attachment is strong signal for home_report
    if has_pdf:
        for pdf in email.get("pdf_paths", []):
            pdf_name = str(pdf).lower()
            if any(kw in pdf_name for kw in ["home_report", "homereport", "single_survey", "hr_"]):
                return "home_report"

    for category, patterns in _PATTERNS:
        for pat in patterns:
            if re.search(pat, combined, re.IGNORECASE):
                return category

    return "general"


# ---------- Address matching ----------

# Edinburgh postcode regex: EH followed by 1-2 digits, space, digit + 2 letters
_POSTCODE_RE = re.compile(r"\bEH\d{1,2}\s*\d[A-Z]{2}\b", re.IGNORECASE)
# Street-number patterns: "10 Marchmont Rd", "29/1 Lutton Place"
_ADDR_HINT_RE = re.compile(r"\b\d{1,4}(?:/\d{1,3})?\s+[A-Z][a-zA-Z']+(?:\s+[A-Z][a-zA-Z']+)*\b")


def extract_address_hints(email: dict[str, Any]) -> list[str]:
    """Pull candidate address keywords from subject + body.

    Returns deduplicated list, prioritizing postcodes (strongest signal)
    then street-number patterns.
    """
    text = (email.get("subject") or "") + "\n" + (email.get("body") or "")
    hints: list[str] = []
    for pc in _POSTCODE_RE.findall(text):
        pc_clean = pc.upper().replace("  ", " ")
        if pc_clean not in hints:
            hints.append(pc_clean)
    for addr in _ADDR_HINT_RE.findall(text):
        if addr not in hints:
            hints.append(addr)
    return hints


def match_property(email: dict[str, Any], storage) -> PropertyRecord | None:
    """Try to find an existing property matching this email.

    Strategy: try each extracted hint (postcode first), return first match.
    """
    for hint in extract_address_hints(email):
        rec = storage.find_by_address(hint)
        if rec is not None:
            return rec
    return None


# ---------- Result types ----------

@dataclass
class MatchedAction:
    email_id: str
    sender: str
    subject: str
    category: str
    property_id: str
    property_address: str
    action: str          # description of what was/would-be done

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class UnmatchedEmail:
    email_id: str
    sender: str
    subject: str
    category: str
    reason: str          # 'no_address_hints' | 'no_property_match'
    hints_tried: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class IntakeResult:
    emails_processed: int
    dry_run: bool
    matched: list[MatchedAction] = field(default_factory=list)
    unmatched: list[UnmatchedEmail] = field(default_factory=list)
    new_pdfs: list[str] = field(default_factory=list)   # paths to PDFs not yet in storage
    suggestions: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "emails_processed": self.emails_processed,
            "dry_run": self.dry_run,
            "matched": [m.to_dict() for m in self.matched],
            "unmatched": [u.to_dict() for u in self.unmatched],
            "new_pdfs": list(self.new_pdfs),
            "suggestions": list(self.suggestions),
        }


# ---------- Email fetching ----------

def fetch_emails(hours: int = 48) -> list[dict[str, Any]]:
    """Subprocess-call fetch_emails.py, return parsed JSON."""
    if not FETCH_EMAILS_SCRIPT.exists():
        raise FileNotFoundError(f"fetch_emails.py not found at {FETCH_EMAILS_SCRIPT}")
    proc = subprocess.run(
        [sys.executable, str(FETCH_EMAILS_SCRIPT), "--hours", str(hours)],
        capture_output=True, text=True, timeout=60,
    )
    if proc.returncode != 0:
        raise RuntimeError(
            f"fetch_emails.py failed (exit {proc.returncode}):\n{proc.stderr[:2000]}"
        )
    try:
        data = json.loads(proc.stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"fetch_emails.py returned non-JSON: {exc}\nFirst 500: {proc.stdout[:500]}")
    if isinstance(data, dict) and "error" in data:
        raise RuntimeError(f"fetch_emails.py error: {data['error']}")
    return data if isinstance(data, list) else data.get("emails", [])


# ---------- Main pipeline ----------

def run(
    *,
    hours: int = 48,
    emails: list[dict[str, Any]] | None = None,
    apply: bool = False,
) -> IntakeResult:
    """Pull emails, classify, match to properties.

    Args:
        hours: how many hours back to fetch (ignored if `emails` is passed)
        emails: pre-fetched list (for testing or custom flows)
        apply: if True, actually mutate storage; if False, dry-run
    """
    if emails is None:
        emails = fetch_emails(hours=hours)
    storage = get_storage()

    result = IntakeResult(emails_processed=len(emails), dry_run=not apply)

    for email in emails:
        category = classify_email(email)
        email_id = str(email.get("id") or email.get("message_id") or "")
        sender = email.get("sender") or email.get("from") or ""
        subject = email.get("subject") or ""

        # Surface any home_report PDFs not yet in storage
        if category == "home_report":
            for pdf_path in email.get("pdf_paths") or []:
                if pdf_path not in result.new_pdfs:
                    result.new_pdfs.append(pdf_path)

        # Try to match to existing property
        rec = match_property(email, storage)
        if rec is None:
            hints = extract_address_hints(email)
            result.unmatched.append(UnmatchedEmail(
                email_id=email_id,
                sender=sender,
                subject=subject,
                category=category,
                reason="no_address_hints" if not hints else "no_property_match",
                hints_tried=hints,
            ))
            continue

        # Compose action description
        action_desc = f"append comm entry (category={category})"

        # Determine if this email updates a key field
        # (viewing_confirmed → viewing_date; closing_date → closing_date)
        field_updates: dict[str, Any] = {}
        if category == "viewing_confirmed":
            vt = _extract_viewing_time(email)
            if vt:
                field_updates["viewing_date"] = vt
                action_desc += f" + set viewing_date={vt}"
        elif category == "closing_date":
            cd = _extract_closing_date(email)
            if cd:
                field_updates["closing_date"] = cd
                action_desc += f" + set closing_date={cd}"

        if apply:
            entry = CommEntry.make(
                category=category,
                sender=sender,
                subject=subject,
                body=email.get("body") or "",
                occurred_at=email.get("date") or email.get("received_at"),
            )
            storage.append_communication(rec.storage_id or rec.address_slug(), entry)
            if field_updates:
                # Apply field updates by upserting modified record
                for key, val in field_updates.items():
                    if isinstance(val, str) and key.endswith("_date"):
                        try:
                            val = date.fromisoformat(val[:10])
                        except ValueError:
                            continue
                    setattr(rec, key, val)
                storage.upsert_property(rec)

        result.matched.append(MatchedAction(
            email_id=email_id,
            sender=sender,
            subject=subject,
            category=category,
            property_id=rec.storage_id or rec.address_slug(),
            property_address=rec.address,
            action=("APPLIED: " if apply else "would: ") + action_desc,
        ))

    # Generate next-step suggestions
    if result.new_pdfs:
        for pdf in result.new_pdfs[:5]:
            result.suggestions.append(f"/home-report {pdf}")
    if result.dry_run and result.matched:
        result.suggestions.append("/property --apply  (to actually write the matched updates above)")
    if result.unmatched:
        result.suggestions.append(
            f"{len(result.unmatched)} 封邮件未匹配 — 多数情况是新房子需先 /home-report 入库"
        )

    return result


# ---------- Light field extractors (best-effort) ----------

_VIEWING_TIME_RE = re.compile(
    r"(\d{1,2}[:.]\d{2})\s*(am|pm|AM|PM)?(?:\s+on\s+(\w+day))?",
    re.IGNORECASE,
)
_CLOSING_DATE_RE = re.compile(
    r"closing\s+(?:date|deadline)[\s:]+(?P<date>\d{1,2}\s+\w+\s+\d{4}|\w+\s+\d{1,2}[a-z]{2}\s+\d{4})",
    re.IGNORECASE,
)


def _extract_viewing_time(email: dict[str, Any]) -> str | None:
    """Very loose — returns raw matched substring for now.
    Real parsing left to SKILL.md / LLM polish layer."""
    text = (email.get("subject") or "") + "\n" + (email.get("body") or "")
    m = _VIEWING_TIME_RE.search(text)
    if m:
        return m.group(0).strip()
    return None


def _extract_closing_date(email: dict[str, Any]) -> str | None:
    text = (email.get("subject") or "") + "\n" + (email.get("body") or "")
    m = _CLOSING_DATE_RE.search(text)
    if m:
        return m.group("date").strip()
    return None


# ---------- CLI ----------

def _cli() -> int:
    import argparse
    ap = argparse.ArgumentParser(
        prog="python -m property_assistant.pipelines.intake",
        description="Pull recent emails, classify, match to properties.",
    )
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run", help="Run intake")
    r.add_argument("--hours", type=int, default=48)
    r.add_argument("--apply", action="store_true",
                   help="Actually write changes (default: dry-run)")
    r.add_argument("--json", action="store_true",
                   help="Output as JSON instead of human summary")
    args = ap.parse_args()
    if args.cmd != "run":
        return 2

    try:
        result = run(hours=args.hours, apply=args.apply)
    except Exception as exc:  # noqa: BLE001
        print(f"intake failed: {exc}", file=sys.stderr)
        return 1

    if args.json:
        print(json.dumps(result.to_dict(), ensure_ascii=False, indent=2))
        return 0

    mode = "🔍 DRY-RUN" if result.dry_run else "✏️  APPLIED"
    print(f"{mode} · processed {result.emails_processed} emails")
    if result.matched:
        print(f"\n✅ matched ({len(result.matched)}):")
        for m in result.matched:
            print(f"  · [{m.category}] {m.subject[:60]} → {m.property_address[:40]}")
            print(f"      {m.action}")
    if result.unmatched:
        print(f"\n❌ unmatched ({len(result.unmatched)}):")
        for u in result.unmatched[:8]:
            print(f"  · [{u.category}] {u.subject[:60]}  ({u.reason})")
        if len(result.unmatched) > 8:
            print(f"  · …还有 {len(result.unmatched) - 8} 封")
    if result.new_pdfs:
        print(f"\n📄 new Home Report PDFs found ({len(result.new_pdfs)}):")
        for p in result.new_pdfs:
            print(f"  · {p}")
    if result.suggestions:
        print(f"\n💡 suggestions:")
        for s in result.suggestions:
            print(f"  · {s}")
    return 0


if __name__ == "__main__":
    sys.exit(_cli())
