import pytest

from chatmaid.llm.base import LLMError
from chatmaid.llm.fake import FakeLLM
from chatmaid.llm.jsoncall import complete_json, schema_skeleton
from chatmaid.llm.prompts import Prompt

P = Prompt("p", "# p\nreturn json", {"type": "object", "properties": {"summary": {"type": "string"}}, "required": ["summary"]})


def test_retries_once_on_invalid_json():
    llm = FakeLLM({"p": ["not json", '{"summary":"ok"}']})
    assert complete_json(llm, P, "u", "m") == {"summary": "ok"}
    assert len(llm.calls) == 2 and "not json" in llm.calls[1]["user"]


def test_raises_after_two_bad_replies():
    llm = FakeLLM({"p": ["{}", "{}"]})
    with pytest.raises(LLMError):
        complete_json(llm, P, "u", "m")


def test_skeleton_hint_always_appended_with_the_schema():
    llm = FakeLLM({"p": '{"summary":"ok"}'})
    complete_json(llm, P, "u", "m")
    assert llm.calls[0]["user"] == f"u\n\nReply with exactly this JSON shape: {schema_skeleton(P.schema)}"
    assert llm.calls[0]["json_schema"] == P.schema
