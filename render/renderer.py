"""Unified Jinja2 renderer.

`render(template_name, **ctx)` loads the named template from
`render/templates/` and writes the result to disk. Returns the output path.

Templates currently supported:
  home_report.html.j2        — single-property full Home Report analysis
  viewing_brief.html.j2      — pre-viewing single-property brief (Step 14)
  property_compare.html.j2   — multi-property comparison (Step 15)
"""

from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path
from typing import Any

import jinja2
from markupsafe import Markup, escape


TEMPLATES_DIR = Path(__file__).resolve().parent / "templates"


# Regexes for metric highlighting in rationale prose.
_RE_MONEY = re.compile(r"£\s?[\d,]+(?:\.\d+)?\s?(?:[kKmM])?")
_RE_PCT   = re.compile(r"[+\-−]?\d+(?:\.\d+)?\s?%")
_RE_PAGE  = re.compile(r"\bp\.\s?\d+\b", re.IGNORECASE)


def highlight_metrics(value: str | None) -> Markup:
    """Wrap £ amounts, percentages, and page refs in styled spans.

    Always returns Markup (safe HTML). Plain text is escaped first.
    """
    if not value:
        return Markup("")
    text = str(escape(value))  # escape first; we'll inject safe spans
    text = _RE_MONEY.sub(lambda m: f'<span class="hi-money">{m.group(0)}</span>', text)
    text = _RE_PCT.sub(lambda m: f'<span class="hi-pct">{m.group(0)}</span>', text)
    text = _RE_PAGE.sub(lambda m: f'<span class="hi-page">{m.group(0)}</span>', text)
    return Markup(text)


def get_env() -> jinja2.Environment:
    env = jinja2.Environment(
        loader=jinja2.FileSystemLoader(str(TEMPLATES_DIR)),
        autoescape=jinja2.select_autoescape(["html"]),
        trim_blocks=True,
        lstrip_blocks=True,
    )
    env.filters["highlight_metrics"] = highlight_metrics
    return env


def render(
    template_name: str,
    *,
    out_path: Path | str,
    **context: Any,
) -> Path:
    """Render the named template to `out_path` and return the path."""
    env = get_env()
    tpl = env.get_template(template_name)

    # Inject common context if not provided
    context.setdefault(
        "generated_at",
        datetime.now().strftime("%Y-%m-%d %H:%M"),
    )

    html = tpl.render(**context)
    out = Path(out_path).expanduser().resolve()
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(html, encoding="utf-8")
    return out


def render_viewing_brief(
    *,
    record,
    breakdown,
    strategy,
    opinion=None,
    parsed: dict[str, Any] | None = None,
    area: dict[str, Any] | None = None,
    out_path: Path | str,
    viewing_meta: dict[str, Any] | None = None,
    storage_backend: str | None = None,
    tldr: str | None = None,
) -> Path:
    """Render the viewing-day brief HTML.

    `viewing_meta`: optional {time: str, agent: str} shown under the address.
    `opinion`: optional SurveyorOpinion to include layered summary + 6-section detail.
    `area`: optional {simd: {decile}, flood, commute: {user_min, partner_min}}.
    `parsed`: optional parsed Home Report dict for HR summary / condition table.
    """
    return render(
        "viewing_brief.html.j2",
        out_path=out_path,
        record=record,
        breakdown=breakdown,
        strategy=strategy,
        opinion=opinion,
        parsed=parsed or {},
        area=area,
        viewing_meta=viewing_meta,
        storage_backend=storage_backend,
        tldr=tldr,
    )


def render_property_compare(
    *,
    comparison,
    ranking=None,
    out_path: Path | str,
    storage_backend: str | None = None,
) -> Path:
    """Render the side-by-side property comparison HTML.

    `comparison`: a Comparison dataclass instance (analysis.comparison).
    `ranking`: optional PropertyRanking instance for the 🏆 section.
    """
    return render(
        "property_compare.html.j2",
        out_path=out_path,
        comparison=comparison,
        ranking=ranking,
        storage_backend=storage_backend,
    )


def render_home_report(
    *,
    record,
    breakdown,
    opinion,
    parsed: dict[str, Any],
    area: dict[str, Any] | None = None,
    out_path: Path | str,
    storage_backend: str | None = None,
    tldr: str | None = None,
) -> Path:
    """Convenience wrapper with the home_report context shape baked in.

    `tldr`: optional explicit one-line executive summary; if omitted, the
    template derives one from opinion.overall_positioning[0] + offer_direction[0].
    """
    return render(
        "home_report.html.j2",
        out_path=out_path,
        record=record,
        breakdown=breakdown,
        opinion=opinion,
        parsed=parsed,
        area=area,
        storage_backend=storage_backend,
        tldr=tldr,
    )
