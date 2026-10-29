from dataclasses import replace

import pytest

from chatmaid.quoting import strip_quoted, strip_reply_chrome


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Yes.\n\nOn Mon, 21 Oct 2026, Ann <ann@x.org> wrote:\n> q\n> more", "Yes.\n\n"),
        ("Yes.\n-----Original Message-----\nFrom: Ann", "Yes.\n"),
        ("好的。\n\n在 2026年10月21日，Ann 写道：\n> 周五见？", "好的。\n\n"),
        ("好的。\n2026年10月21日 10:00 Ann 写道:\n> 周五见？", "好的。\n"),
        ("Yes.\n> quoted\nmore", "Yes.\n"),
        ("Yes.\n-- \nJiaqi\nPhone", "Yes.\n"),
        ("Yes.\n--\nJiaqi", "Yes.\n"),
        ("Plain reply, no chrome.", "Plain reply, no chrome."),
        ("Price -- not final\nOk", "Price -- not final\nOk"),
    ],
)
def test_strip_reply_chrome_cases(text, expected):
    assert strip_reply_chrome(text) == expected


def test_strip_quoted_keeps_first_and_drops_emptied(sample_message):
    first = replace(sample_message, text="Can we meet?\n> old quote")
    reply = replace(sample_message, text="Yes.\n\nOn Mon, Ann wrote:\n> Can we meet?")
    only_quote = replace(sample_message, text="> Can we meet?\n> Yes.")
    out = strip_quoted([first, reply, only_quote])
    assert [m.text for m in out] == ["Can we meet?\n> old quote", "Yes.\n\n"]
    assert [m.text for m in strip_quoted([reply], keep_first=False)] == ["Yes.\n\n"]
    assert strip_quoted([only_quote], keep_first=False) == []
