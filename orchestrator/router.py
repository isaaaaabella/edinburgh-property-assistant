"""/property command router — precise subcommand parsing + dispatch.

NL routing (e.g., "下周末看房" → prep --weekend) is NOT done here. That's
delegated to SKILL.md (Claude in the orchestrator turn). This module only
parses explicit subcommands.

Subcommands:
  (default)            → intake dry-run + suggestions
  prep                 → viewing prep for one or more properties
  review               → post-viewing review + gap analysis
  compare              → side-by-side comparison
  brief                → single-property pre-viewing brief
  emails               → email intake (alias for default with --hours)
  analyze              → single PDF home_report (= /home-report)
  health               → health check

Global:
  --apply              → actually write changes (otherwise dry-run for intake)
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any, Callable


@dataclass
class ParsedCommand:
    subcommand: str
    args: dict[str, Any] = field(default_factory=dict)
    apply: bool = False


def _weekend_range() -> tuple[date, date]:
    """Return (Saturday, Sunday) of the upcoming/current weekend."""
    today = date.today()
    # Monday=0, ..., Saturday=5, Sunday=6
    days_to_sat = (5 - today.weekday()) % 7
    sat = today + timedelta(days=days_to_sat)
    return sat, sat + timedelta(days=1)


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        prog="/property",
        description="Scottish property assistant — main entry point.",
    )
    ap.add_argument("--apply", action="store_true",
                    help="Actually write changes (default subcommand is dry-run)")

    sub = ap.add_subparsers(dest="subcommand")

    # health
    sub.add_parser("health", help="Storage + integrations health check")

    # prep
    p_prep = sub.add_parser("prep", help="Pre-viewing brief(s)")
    p_prep.add_argument("--addr", help="Specific address keyword (default: nearest future viewing)")
    p_prep.add_argument("--date", help="YYYY-MM-DD specific viewing date")
    p_prep.add_argument("--weekend", action="store_true",
                        help="All viewings this Sat+Sun")
    p_prep.add_argument("--strategy", help="ViewingStrategy JSON path")
    p_prep.add_argument("--opinion", help="SurveyorOpinion JSON path (optional)")
    p_prep.add_argument("--viewing-time", help="e.g. 'Sat 11:00'")
    p_prep.add_argument("--agent", help="Agent / agency name")
    p_prep.add_argument("--tldr", help="One-line executive summary override")
    p_prep.add_argument("--out", help="HTML output path")

    # review
    p_review = sub.add_parser("review", help="Post-viewing review + gap analysis")
    p_review.add_argument("--shortlist", action="store_true",
                          help="Only status='⭐ 感兴趣'")
    p_review.add_argument("--json", action="store_true")

    # compare
    p_cmp = sub.add_parser("compare", help="Side-by-side comparison")
    p_cmp.add_argument("--addr", action="append", default=[],
                       help="Address keyword (repeat for multiple)")
    p_cmp.add_argument("--viewing-from", help="YYYY-MM-DD")
    p_cmp.add_argument("--viewing-to", help="YYYY-MM-DD")
    p_cmp.add_argument("--weekend", action="store_true")
    p_cmp.add_argument("--status", help="Notion status select value")
    p_cmp.add_argument("--ranking", help="Optional PropertyRanking JSON path")
    p_cmp.add_argument("--out", help="HTML output path")

    # brief = alias for prep with single --addr (kept for parity with old /viewing-brief)
    p_brief = sub.add_parser("brief", help="Single-property pre-viewing brief (alias of prep --addr)")
    p_brief.add_argument("--addr", required=True)
    p_brief.add_argument("--strategy", required=True)
    p_brief.add_argument("--opinion")
    p_brief.add_argument("--viewing-time")
    p_brief.add_argument("--agent")
    p_brief.add_argument("--tldr")
    p_brief.add_argument("--out")

    # emails
    p_em = sub.add_parser("emails", help="Email intake")
    p_em.add_argument("--hours", type=int, default=48)
    p_em.add_argument("--json", action="store_true")

    # analyze
    p_an = sub.add_parser("analyze", help="Single PDF home_report (equivalent to /home-report)")
    p_an.add_argument("pdf", help="Home Report PDF path")
    p_an.add_argument("--opinion", required=True)
    p_an.add_argument("--parsed", help="Pre-parsed Home Report JSON")
    p_an.add_argument("--out")

    return ap


def parse_args(argv: list[str]) -> ParsedCommand:
    parser = build_parser()
    args = parser.parse_args(argv)
    return ParsedCommand(
        subcommand=args.subcommand or "default",
        args={k: v for k, v in vars(args).items()
              if k not in {"subcommand", "apply"}},
        apply=args.apply,
    )


# ---------- Dispatchers ----------

def _dispatch_health() -> int:
    from property_assistant.storage import get_storage
    try:
        h = get_storage().health_check()
    except Exception as exc:  # noqa: BLE001
        print(f"💥 storage init failed: {exc}", file=sys.stderr)
        return 1
    mark = "✅" if h["ok"] else "❌"
    print(f"{mark} backend={h['backend']} · {h['detail']}")
    return 0 if h["ok"] else 1


def _dispatch_default(apply: bool) -> int:
    """Default = email intake + summary. dry-run unless --apply."""
    from property_assistant.pipelines.intake import run as intake_run
    try:
        result = intake_run(hours=48, apply=apply)
    except Exception as exc:  # noqa: BLE001
        print(f"intake failed: {exc}", file=sys.stderr)
        return 1
    _print_intake_summary(result)
    return 0


def _print_intake_summary(result) -> None:
    mode = "🔍 DRY-RUN" if result.dry_run else "✏️  APPLIED"
    print(f"\n{mode} · 处理了 {result.emails_processed} 封邮件\n")
    if result.matched:
        print(f"📬 匹配到房源 ({len(result.matched)} 封):")
        for m in result.matched:
            print(f"  · [{m.category}] {m.subject[:60]}")
            print(f"      → {m.property_address[:40]} · {m.action}")
    if result.new_pdfs:
        print(f"\n📄 发现新 PDF ({len(result.new_pdfs)}):")
        for p in result.new_pdfs:
            print(f"  · {p}")
    if result.unmatched:
        print(f"\n❌ 未匹配 ({len(result.unmatched)} 封)")
    if result.suggestions:
        print("\n💡 建议下一步:")
        for s in result.suggestions:
            print(f"  · {s}")


def _dispatch_prep(args: dict[str, Any]) -> int:
    from property_assistant.pipelines.viewing_prep import run as prep_run

    if args.get("addr"):
        # Single property prep
        if not args.get("strategy"):
            print("/property prep --addr 需要 --strategy <path>", file=sys.stderr)
            return 2
        result = prep_run(
            args["addr"],
            strategy_path=args["strategy"],
            opinion_path=args.get("opinion"),
            out_html=args.get("out"),
            viewing_time=args.get("viewing_time"),
            agent_name=args.get("agent"),
            tldr=args.get("tldr"),
        )
        print(f"✓ {result.html_path}")
        print(f"  ⭐ {result.record.address} — {result.breakdown.total}/100")
        return 0

    if args.get("weekend"):
        from property_assistant.storage import get_storage
        sat, sun = _weekend_range()
        viewings = get_storage().list_by_filter(viewing_date_from=sat, viewing_date_to=sun)
        if not viewings:
            print(f"📅 本周末 ({sat} - {sun}) 没有看房安排")
            return 0
        print(f"📅 本周末 {len(viewings)} 个看房:")
        for v in viewings:
            print(f"  · {v.viewing_date} {v.address}")
        print("\n要生成 brief，需要为每套提供 --strategy JSON。"
              "请逐套调 /property prep --addr <X> --strategy <path>")
        return 0

    if args.get("date"):
        from property_assistant.storage import get_storage
        d = date.fromisoformat(args["date"])
        viewings = get_storage().list_by_filter(viewing_date_from=d, viewing_date_to=d)
        if not viewings:
            print(f"📅 {d} 没有看房安排")
            return 0
        print(f"📅 {d} 共 {len(viewings)} 个看房:")
        for v in viewings:
            print(f"  · {v.address}")
        return 0

    print("/property prep 必须指定: --addr ADDR | --date YYYY-MM-DD | --weekend",
          file=sys.stderr)
    return 2


def _dispatch_review(args: dict[str, Any]) -> int:
    from property_assistant.pipelines.viewing_review import run as review_run
    result = review_run(only_shortlist=bool(args.get("shortlist")))
    if args.get("json"):
        print(json.dumps(result.to_dict(), ensure_ascii=False, indent=2))
        return 0
    _print_review_summary(result)
    return 0


def _print_review_summary(result) -> None:
    print(f"\n📋 review: {result.viewed_count} 套已看房源\n")
    for p in result.properties:
        print(f"  · {p.address[:40]:40s} {p.score}/100 · {p.status or '—'}")
        if p.self_feeling:
            print(f"      你: {p.self_feeling[:80]}")
        if p.partner_feeling:
            print(f"      伴侣: {p.partner_feeling[:80]}")
    if result.gaps:
        print(f"\n⚠️ {len(result.gaps)} 个 gap:")
        for g in result.gaps:
            print(f"  · [{g.kind}] {g.address[:30]}: {g.description}")
    if result.shortlist:
        print(f"\n⭐ shortlist ({len(result.shortlist)}):")
        for a in result.shortlist:
            print(f"  · {a}")


def _dispatch_compare(args: dict[str, Any]) -> int:
    from property_assistant.pipelines.property_compare import run as compare_run

    viewing_from = (
        date.fromisoformat(args["viewing_from"]) if args.get("viewing_from") else None
    )
    viewing_to = (
        date.fromisoformat(args["viewing_to"]) if args.get("viewing_to") else None
    )
    if args.get("weekend"):
        viewing_from, viewing_to = _weekend_range()

    try:
        result = compare_run(
            addresses=args.get("addr") or None,
            viewing_from=viewing_from,
            viewing_to=viewing_to,
            status=args.get("status"),
            ranking_path=args.get("ranking"),
            out_html=args.get("out"),
        )
    except ValueError as exc:
        print(f"compare error: {exc}", file=sys.stderr)
        return 1
    print(f"✓ {result.html_path}")
    print(f"  📊 {len(result.comparison.properties)} 套对比")
    if result.ranking:
        print(f"  🏆 1st: {result.ranking.ranked[0].address}")
    return 0


def _dispatch_brief(args: dict[str, Any]) -> int:
    from property_assistant.pipelines.viewing_prep import run as prep_run
    result = prep_run(
        args["addr"],
        strategy_path=args["strategy"],
        opinion_path=args.get("opinion"),
        out_html=args.get("out"),
        viewing_time=args.get("viewing_time"),
        agent_name=args.get("agent"),
        tldr=args.get("tldr"),
    )
    print(f"✓ {result.html_path}")
    print(f"  ⭐ {result.record.address} — {result.breakdown.total}/100")
    return 0


def _dispatch_emails(args: dict[str, Any], apply: bool) -> int:
    from property_assistant.pipelines.intake import run as intake_run
    result = intake_run(hours=args.get("hours", 48), apply=apply)
    if args.get("json"):
        print(json.dumps(result.to_dict(), ensure_ascii=False, indent=2))
        return 0
    _print_intake_summary(result)
    return 0


def _dispatch_analyze(args: dict[str, Any]) -> int:
    from property_assistant.pipelines.home_report import run as hr_run
    parsed_dict = None
    if args.get("parsed"):
        with open(args["parsed"], encoding="utf-8") as f:
            parsed_dict = json.load(f)
    result = hr_run(
        args["pdf"],
        opinion_path=args["opinion"],
        parsed=parsed_dict,
        out_html=args.get("out"),
    )
    print(f"✓ {result.html_path}")
    print(f"  ⭐ {result.record.address} — {result.breakdown.total}/100")
    return 0


_DISPATCHERS: dict[str, Callable] = {
    "health":  lambda cmd: _dispatch_health(),
    "default": lambda cmd: _dispatch_default(cmd.apply),
    "prep":    lambda cmd: _dispatch_prep(cmd.args),
    "review":  lambda cmd: _dispatch_review(cmd.args),
    "compare": lambda cmd: _dispatch_compare(cmd.args),
    "brief":   lambda cmd: _dispatch_brief(cmd.args),
    "emails":  lambda cmd: _dispatch_emails(cmd.args, cmd.apply),
    "analyze": lambda cmd: _dispatch_analyze(cmd.args),
}


def dispatch(cmd: ParsedCommand) -> int:
    handler = _DISPATCHERS.get(cmd.subcommand)
    if handler is None:
        print(f"未知子命令: {cmd.subcommand}", file=sys.stderr)
        return 2
    return handler(cmd)


def main(argv: list[str] | None = None) -> int:
    cmd = parse_args(argv if argv is not None else sys.argv[1:])
    return dispatch(cmd)


if __name__ == "__main__":
    sys.exit(main())
