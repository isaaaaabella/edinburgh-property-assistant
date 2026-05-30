#!/usr/bin/env python3
"""Legacy CLI shim — kept so existing `python parse_home_report.py <pdf>` calls
(skills, docs, pipelines/home_report.py) keep working after the package split.

The implementation lives in `property_assistant.parsers.*`.
"""

from __future__ import annotations

import os
import sys

# Allow running as a loose script (no package import path) by adding the
# parent of this file's directory to sys.path before the package import.
_pkg_parent = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _pkg_parent not in sys.path:
    sys.path.insert(0, _pkg_parent)

from property_assistant.parsers.dispatcher import main  # noqa: E402


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
