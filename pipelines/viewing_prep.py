"""Viewing prep pipeline — single-property pre-viewing brief.

Flow:
  1. Load PropertyRecord from storage (by address keyword)
  2. (optional) Load cached parsed.json next to PDF if available
  3. Compute scoring
  4. Load + validate ViewingStrategy JSON (caller-provided; SKILL.md generates)
  5. (optional) Load + validate SurveyorOpinion JSON for layered summary
  6. Render viewing_brief HTML
  7. attach_html_report to storage

This pipeline doesn't run the PDF parser — it uses whatever PropertyRecord
already exists in storage (typically populated earlier by /home-report).
"""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from property_assistant.analysis.scoring import ScoreBreakdown, compute
from property_assistant.analysis.surveyor_opinion import SurveyorOpinion
from property_assistant.analysis.viewing_strategy import ViewingStrategy
from property_assistant.core.property_record import PropertyRecord
from property_assistant.render.renderer import render_viewing_brief
from property_assistant.storage import get_storage


class StrategyValidationError(Exception):
    def __init__(self, errors: list[str]):
        self.errors = errors
        super().__init__("ViewingStrategy validation failed:\n" + "\n".join(f"- {e}" for e in errors))


@dataclass
class RunResult:
    record: PropertyRecord
    breakdown: ScoreBreakdown
    html_path: Path
    property_id: str


def _try_load_parsed(record: PropertyRecord) -> dict[str, Any] | None:
    """Look for cached parsed.json next to PDF (written by home_report pipeline)."""
    # PDF path isn't on PropertyRecord — caller can pass parsed if known.
    # Best-effort: skip if not found.
    return None


def run(
    address_keyword: str,
    *,
    strategy_path: Path | str,
    opinion_path: Path | str | None = None,
    parsed: dict[str, Any] | None = None,
    area: dict[str, Any] | None = None,
    out_html: Path | str | None = None,
    viewing_time: str | None = None,
    agent_name: str | None = None,
    tldr: str | None = None,
    skip_storage: bool = False,
) -> RunResult:
    storage = get_storage()
    record = storage.find_by_address(address_keyword)
    if record is None:
        raise ValueError(
            f"找不到匹配地址 {address_keyword!r} 的房源。"
            f"先跑 /home-report <PDF> 把它入库。"
        )

    breakdown = compute(record)

    strategy = ViewingStrategy.from_json_file(str(strategy_path))
    errs = strategy.validate()
    if errs:
        raise StrategyValidationError(errs)

    opinion = None
    if opinion_path:
        opinion = SurveyorOpinion.from_json_file(str(opinion_path))
        # We can't strictly validate opinion against parsed here (parsed may
        # be missing); only sanity-check non-empty required sections.
        soft_errs = [e for e in opinion.validate({}) if "矛盾项" not in e]
        if soft_errs:
            print(f"warning: SurveyorOpinion 软校验有 {len(soft_errs)} 条提示，继续渲染",
                  file=sys.stderr)

    viewing_meta = {}
    if viewing_time:
        viewing_meta["time"] = viewing_time
    if agent_name:
        viewing_meta["agent"] = agent_name

    if not out_html:
        slug = record.address_slug()
        out_html = Path.home() / "Downloads" / f"{slug}_viewing_brief.html"
    out_html = Path(out_html).expanduser().resolve()

    render_viewing_brief(
        record=record,
        breakdown=breakdown,
        strategy=strategy,
        opinion=opinion,
        parsed=parsed or {},
        area=area,
        out_path=out_html,
        viewing_meta=viewing_meta or None,
        storage_backend=storage.name if not skip_storage else None,
        tldr=tldr,
    )

    pid = record.storage_id or ""
    if not skip_storage and record.storage_id:
        try:
            storage.attach_html_report(record.storage_id, str(out_html), "viewing_brief")
            pid = record.storage_id
        except Exception as exc:  # noqa: BLE001
            print(f"warning: attach_html_report failed: {exc}", file=sys.stderr)

    return RunResult(record=record, breakdown=breakdown, html_path=out_html, property_id=pid)


def _cli() -> int:
    import argparse
    ap = argparse.ArgumentParser(
        prog="python -m property_assistant.pipelines.viewing_prep",
        description="Generate a pre-viewing brief HTML for one property.",
    )
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run", help="Run viewing prep")
    r.add_argument("address", help="Address keyword (substring match)")
    r.add_argument("--strategy", required=True, help="ViewingStrategy JSON path")
    r.add_argument("--opinion", help="SurveyorOpinion JSON path (optional)")
    r.add_argument("--parsed", help="Pre-parsed Home Report JSON (optional)")
    r.add_argument("--out", help="HTML output path")
    r.add_argument("--viewing-time", help="e.g. 2026-05-23 11:00")
    r.add_argument("--agent", help="Agent / agency name")
    r.add_argument("--tldr", help="Explicit one-line TL;DR override")
    r.add_argument("--skip-storage", action="store_true")
    args = ap.parse_args()
    if args.cmd != "run":
        return 2

    parsed_dict = None
    if args.parsed:
        with open(args.parsed, encoding="utf-8") as f:
            parsed_dict = json.load(f)

    try:
        result = run(
            args.address,
            strategy_path=args.strategy,
            opinion_path=args.opinion,
            parsed=parsed_dict,
            out_html=args.out,
            viewing_time=args.viewing_time,
            agent_name=args.agent,
            tldr=args.tldr,
            skip_storage=args.skip_storage,
        )
    except StrategyValidationError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    except Exception as exc:  # noqa: BLE001
        print(f"viewing_prep failed: {exc}", file=sys.stderr)
        return 1

    print(f"✓ HTML: {result.html_path}")
    print(f"  📍 {result.record.address}")
    print(f"  ⭐ {result.breakdown.recommendation} · {result.breakdown.total}/100")
    if result.property_id:
        print(f"  🗄️ id={result.property_id}")
    return 0


if __name__ == "__main__":
    sys.exit(_cli())
