import pytest

from chatmaid.connectors.evolution.rfc822 import ParsedMail
from chatmaid.model import Address
from chatmaid.triage.rules import is_bulk


@pytest.mark.parametrize("hdrs,sender,expected", [
    ({"list-unsubscribe": "<mailto:u@x>"}, "news@x.org", True),
    ({"precedence": "bulk"}, "a@x.org", True),
    ({"auto-submitted": "auto-generated"}, "a@x.org", True),
    ({"auto-submitted": "no"}, "a@x.org", False),
    ({}, "no-reply@x.org", True),
    ({}, "ann@x.org", False),
])
def test_is_bulk(hdrs, sender, expected):
    assert is_bulk(ParsedMail(None, None, (), Address("", sender), (), (), None, "", "", (), hdrs)) is expected
