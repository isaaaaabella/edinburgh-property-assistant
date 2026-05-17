"""Tests for orchestrator.router — precise subcommand parsing only."""

from __future__ import annotations

import pytest

from property_assistant.orchestrator.router import (
    ParsedCommand,
    _weekend_range,
    parse_args,
)


def test_default_when_no_args():
    cmd = parse_args([])
    assert cmd.subcommand == "default"
    assert cmd.apply is False


def test_apply_flag_global():
    cmd = parse_args(["--apply"])
    assert cmd.subcommand == "default"
    assert cmd.apply is True


def test_health():
    cmd = parse_args(["health"])
    assert cmd.subcommand == "health"


def test_prep_with_addr():
    cmd = parse_args(["prep", "--addr", "Marchmont", "--strategy", "/tmp/s.json"])
    assert cmd.subcommand == "prep"
    assert cmd.args["addr"] == "Marchmont"
    assert cmd.args["strategy"] == "/tmp/s.json"


def test_prep_weekend():
    cmd = parse_args(["prep", "--weekend"])
    assert cmd.subcommand == "prep"
    assert cmd.args["weekend"] is True


def test_review_shortlist():
    cmd = parse_args(["review", "--shortlist"])
    assert cmd.subcommand == "review"
    assert cmd.args["shortlist"] is True


def test_compare_multiple_addr():
    cmd = parse_args(["compare", "--addr", "A", "--addr", "B", "--addr", "C"])
    assert cmd.args["addr"] == ["A", "B", "C"]


def test_compare_weekend_filter():
    cmd = parse_args(["compare", "--weekend"])
    assert cmd.args["weekend"] is True


def test_brief_required_args():
    cmd = parse_args(["brief", "--addr", "X", "--strategy", "/tmp/s.json"])
    assert cmd.subcommand == "brief"
    assert cmd.args["addr"] == "X"


def test_brief_missing_required_fails():
    with pytest.raises(SystemExit):
        parse_args(["brief", "--addr", "X"])  # missing --strategy


def test_emails_hours():
    cmd = parse_args(["emails", "--hours", "72"])
    assert cmd.subcommand == "emails"
    assert cmd.args["hours"] == 72


def test_emails_apply():
    cmd = parse_args(["--apply", "emails", "--hours", "24"])
    assert cmd.apply is True
    assert cmd.args["hours"] == 24


def test_analyze_required_pdf():
    cmd = parse_args(["analyze", "/tmp/x.pdf", "--opinion", "/tmp/o.json"])
    assert cmd.subcommand == "analyze"
    assert cmd.args["pdf"] == "/tmp/x.pdf"
    assert cmd.args["opinion"] == "/tmp/o.json"


def test_analyze_with_pre_parsed():
    cmd = parse_args(["analyze", "/tmp/x.pdf", "--opinion", "/tmp/o.json", "--parsed", "/tmp/p.json"])
    assert cmd.args["parsed"] == "/tmp/p.json"


def test_unknown_subcommand_fails():
    with pytest.raises(SystemExit):
        parse_args(["nonsense"])


def test_weekend_range_returns_sat_sun():
    sat, sun = _weekend_range()
    assert (sun - sat).days == 1
    assert sat.weekday() == 5  # Saturday
    assert sun.weekday() == 6  # Sunday
