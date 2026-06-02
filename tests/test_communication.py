"""Tests for CommEntry excerpt cleaning (keeps HTML-email CSS out of Notion)."""

from property_assistant.core.communication import CommEntry, clean_excerpt


def test_clean_excerpt_strips_style_block():
    body = ('<html><head><style>@font-face { font-family: "Cambria Math"; '
            'panose-1: 2 4 5 3; mso-font-charset:0; }</style></head>'
            '<body><p>Hi Duoduo, viewing confirmed for Saturday 11am.</p></body></html>')
    assert clean_excerpt(body) == "Hi Duoduo, viewing confirmed for Saturday 11am."


def test_clean_excerpt_drops_truncated_css_rule():
    # body cut mid-rule (the 500-char excerpt limit) leaves a dangling "selector {"
    assert clean_excerpt("body { margin: 0; padding: 0; -ms-text-size-adjust: 100%;") == ""
    assert clean_excerpt("div.MsoNormal { margin: 0cm; font-size: 12.0pt;") == ""


def test_clean_excerpt_drops_truncated_tag():
    assert clean_excerpt("Viewing request received <tabl") == "Viewing request received"


def test_clean_excerpt_keeps_prose_after_empty_rule():
    assert clean_excerpt("{ } If you no longer wish to receive these alerts") == \
        "If you no longer wish to receive these alerts"


def test_clean_excerpt_plaintext_passthrough():
    assert clean_excerpt("Yes we can do 5pm today instead.") == "Yes we can do 5pm today instead."


def test_clean_excerpt_keeps_prose_before_dangling_rule():
    # The truncated-rule strip must not span backwards across sentence prose.
    assert clean_excerpt("Thanks for your enquiry. .ReadMsgBody { width:100%") == \
        "Thanks for your enquiry."
    assert clean_excerpt("Hi Duoduo, can we move to 5pm? body { margin: 0") == \
        "Hi Duoduo, can we move to 5pm?"


def test_clean_excerpt_never_leaks_braces():
    out = clean_excerpt("prefix .ReadMsgBody { color:red; nested { x }")
    assert "{" not in out and "}" not in out


def test_make_applies_cleaning():
    e = CommEntry.make(category="viewing", sender="a@b.com", subject="Hi",
                       body="<style>p{color:red}</style><p>Real text here</p>")
    assert e.body_excerpt == "Real text here"
