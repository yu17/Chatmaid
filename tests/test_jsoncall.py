import pytest

from chatmaid.llm.base import LLMError
from chatmaid.llm.fake import FakeLLM
from chatmaid.llm.jsoncall import complete_json, schema_skeleton
from chatmaid.llm.prompts import Prompt

P = Prompt("p", "# p\nreturn json", {"type": "object", "properties": {"summary": {"type": "string"}}, "required": ["summary"]})


def test_retries_once_on_invalid_json():
    llm = FakeLLM({"p": ["not json", '{"summary":"ok"}']})
    assert complete_json(llm, P, "u", "m", grammar=False) == {"summary": "ok"}
    assert len(llm.calls) == 2 and "not json" in llm.calls[1]["user"]


def test_raises_after_two_bad_replies():
    llm = FakeLLM({"p": ["{}", "{}"]})
    with pytest.raises(LLMError):
        complete_json(llm, P, "u", "m", grammar=False)


def test_skeleton_hint_only_without_grammar():
    llm = FakeLLM({"p": '{"summary":"ok"}'})
    complete_json(llm, P, "u", "m", grammar=False)
    complete_json(llm, P, "u", "m", grammar=True)
    assert schema_skeleton(P.schema) in llm.calls[0]["user"]
    assert schema_skeleton(P.schema) not in llm.calls[1]["user"]
