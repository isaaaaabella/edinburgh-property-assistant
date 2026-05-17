"""Property comparison pipeline — load 2-5 records, render side-by-side HTML.

Input modes:
  - Explicit addresses: --addr "A" --addr "B" --addr "C"
  - Date filter (e.g. weekend): --viewing-from / --viewing-to
  - Status filter: --status "🔍 待看"

The pipeline loads records from current STORAGE_BACKEND, computes scoring
+ mechanical comparison rows, optionally overlays an LLM-generated ranking
(from --ranking JSON path), renders HTML, and attaches it to each property's
report list in storage.
"""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

from property_assistant.analysis.comparison import (
    Comparison,
    PropertyRanking,
    compute_comparison,
)
from property_assistant.core.property_record import PropertyRecord
from property_assistant.render.renderer import render_property_compare
from property_assistant.storage import get_storage


class RankingValidationError(Exception):
    def __init__(self, errors: list[str]):
        self.errors = errors
        super().__init__("PropertyRanking validation failed:\n" + "\n".join(f"- {e}" for e in errors))


@dataclass
class RunResult:
    comparison: Comparison
    ranking: PropertyRanking | None
    html_path: Path
    property_ids: list[str]


def _load_by_addresses(addresses: list[str]) -> list[PropertyRecord]:
    storage = get_storage()
    records: list[PropertyRecord] = []
    missing: list[str] = []
    for addr in addresses:
        rec = storage.find_by_address(addr)
        if rec is None:
            missing.append(addr)
        else:
            records.append(rec)
    if missing:
        raise ValueError(
            f"以下地址在 storage 找不到: {missing}。"
            f"先用 /home-report 入库这些房源。"
        )
    return records


def _load_by_filter(
    viewing_from: date | None,
    viewing_to: date | None,
    status: str | None,
) -> list[PropertyRecord]:
    storage = get_storage()
    return storage.list_by_filter(
        viewing_date_from=viewing_from,
        viewing_date_to=viewing_to,
        status=status,
    )


def run(
    *,
    addresses: list[str] | None = None,
    viewing_from: date | None = None,
    viewing_to: date | None = None,
    status: str | None = None,
    ranking_path: Path | str | None = None,
    out_html: Path | str | None = None,
    skip_storage: bool = False,
) -> RunResult:
    if addresses:
        records = _load_by_addresses(addresses)
    else:
        records = _load_by_filter(viewing_from, viewing_to, status)
    if len(records) < 2:
        raise ValueError(
            f"对比至少需要 2 套房子（当前 {len(records)} 套）。"
            f"用 --addr 显式列地址，或放宽 filter。"
        )
    if len(records) > 5:
        print(f"warning: 对比 {len(records)} 套可能信息过载（建议 ≤5）", file=sys.stderr)

    comparison = compute_comparison(records)

    ranking: PropertyRanking | None = None
    if ranking_path:
        ranking = PropertyRanking.from_json_file(str(ranking_path))
        errs = ranking.validate(records)
        if errs:
            raise RankingValidationError(errs)

    if not out_html:
        stamp = date.today().isoformat()
        out_html = Path.home() / "Downloads" / f"property_compare_{stamp}.html"
    out_html = Path(out_html).expanduser().resolve()

    storage = get_storage() if not skip_storage else None
    render_property_compare(
        comparison=comparison,
        ranking=ranking,
        out_path=out_html,
        storage_backend=storage.name if storage else None,
    )

    pids: list[str] = []
    if not skip_storage and storage:
        for rec in records:
            if rec.storage_id:
                try:
                    storage.attach_html_report(rec.storage_id, str(out_html), "compare")
                    pids.append(rec.storage_id)
                except Exception as exc:  # noqa: BLE001
                    print(f"warning: attach_html_report failed for {rec.address}: {exc}",
                          file=sys.stderr)

    return RunResult(comparison=comparison, ranking=ranking,
                     html_path=out_html, property_ids=pids)


def _cli() -> int:
    import argparse
    ap = argparse.ArgumentParser(
        prog="python -m property_assistant.pipelines.property_compare",
        description="Side-by-side comparison of 2-5 properties.",
    )
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run", help="Run comparison")
    r.add_argument("--addr", action="append", default=[],
                   help="Address keyword (repeat for multiple)")
    r.add_argument("--viewing-from", help="YYYY-MM-DD")
    r.add_argument("--viewing-to", help="YYYY-MM-DD")
    r.add_argument("--status", help="Notion status select value")
    r.add_argument("--ranking", help="Optional ranking JSON path")
    r.add_argument("--out", help="HTML output path")
    r.add_argument("--skip-storage", action="store_true")
    args = ap.parse_args()
    if args.cmd != "run":
        return 2

    def _parse_date(s: str | None) -> date | None:
        return date.fromisoformat(s) if s else None

    try:
        result = run(
            addresses=args.addr or None,
            viewing_from=_parse_date(args.viewing_from),
            viewing_to=_parse_date(args.viewing_to),
            status=args.status,
            ranking_path=args.ranking,
            out_html=args.out,
            skip_storage=args.skip_storage,
        )
    except RankingValidationError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    except Exception as exc:  # noqa: BLE001
        print(f"property_compare failed: {exc}", file=sys.stderr)
        return 1

    print(f"✓ HTML: {result.html_path}")
    print(f"  📊 Compared {len(result.comparison.properties)} properties:")
    for p, b in zip(result.comparison.properties, result.comparison.breakdowns):
        print(f"    · {p.address} — {b.total}/100")
    if result.ranking:
        print(f"  🏆 1st: {result.ranking.ranked[0].address}")
    return 0


if __name__ == "__main__":
    sys.exit(_cli())
