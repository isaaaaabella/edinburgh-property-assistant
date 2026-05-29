"""Rightmove listing scraper — extracts og:image cover + asking price.

Used by pipelines/home_report.py when invoked with --listing <RIGHTMOVE_URL>.
Pure-stdlib; no requests / bs4 dependency.

Rightmove serves SSR HTML so urllib + regex works (no Playwright needed).
Key gotcha: og meta tags use SINGLE quotes, not double.
"""
from __future__ import annotations

import re
import urllib.request

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)

_OG_IMAGE_RE = re.compile(r"og:image['\"]\s+content=['\"]([^'\"]+)['\"]")
_PRIMARY_PRICE_RE = re.compile(r'primaryPrice"><span>£([\d,]+)')
_OG_TITLE_RE = re.compile(r"og:title['\"]\s+content=['\"]([^'\"]+)['\"]")


def fetch_listing_meta(url: str, *, timeout: int = 15) -> dict:
    """Fetch a Rightmove listing page and extract cover image + price.

    Returns dict with keys: image_url, asking_price (int £), title.
    Missing fields → None. Network/parse errors → 'error' key set, others None.
    """
    out = {"image_url": None, "asking_price": None, "title": None, "error": None}
    try:
        req = urllib.request.Request(url, headers={
            "User-Agent": USER_AGENT,
            "Accept-Language": "en-GB,en;q=0.9",
        })
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            html = resp.read().decode("utf-8", errors="replace")
    except Exception as exc:  # noqa: BLE001
        out["error"] = f"fetch failed: {exc}"
        return out

    m = _OG_IMAGE_RE.search(html)
    if m:
        out["image_url"] = m.group(1)

    m = _PRIMARY_PRICE_RE.search(html)
    if m:
        try:
            out["asking_price"] = int(m.group(1).replace(",", ""))
        except ValueError:
            pass

    m = _OG_TITLE_RE.search(html)
    if m:
        out["title"] = m.group(1)

    if not out["image_url"] and not out["asking_price"]:
        out["error"] = "neither image_url nor asking_price extracted (page format may have changed)"

    return out


if __name__ == "__main__":
    import sys, json
    if len(sys.argv) != 2:
        print("usage: python -m property_assistant.parsers.rightmove <URL>", file=sys.stderr)
        sys.exit(2)
    print(json.dumps(fetch_listing_meta(sys.argv[1]), ensure_ascii=False, indent=2))
