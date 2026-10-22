from types import SimpleNamespace

import pytest

from chatmaid.llm.base import LLMError
from chatmaid.llm.fake import FakeLLM
from chatmaid.llm.lmstudio import LMStudioClient


def test_fake_llm_records_calls_and_fails_on_request():
    f = FakeLLM({"SUM": '{"summary": "s"}'})
    f.fail_next(1)
    with pytest.raises(LLMError):
        f.complete(system="SUM\nmore", user="u", model="m")
    assert f.complete(system="# SUM\nmore", user="u", model="m") == '{"summary": "s"}'
    assert len(f.calls) >= 1
    assert set(f.calls[-1]) == {"system", "user", "model", "json_schema", "max_tokens", "temperature"}


def test_fake_llm_unknown_key_returns_empty_object():
    assert FakeLLM().complete(system="X", user="u", model="m") == "{}"


def test_fake_embed_is_deterministic():
    f = FakeLLM(dim=4)
    assert f.embed(["a"], model="e") == f.embed(["a"], model="e")
    assert len(f.embed(["a"], model="e")[0]) == 4
    assert all(0 <= x < 1 for x in f.embed(["a", "b"], model="e")[1])


def test_lmstudio_client_builds_json_schema_request(monkeypatch):
    captured = {}

    class FakeCompletions:
        def create(self, **kw):
            captured.update(kw)
            return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content='{"a":1}'))])

    c = LMStudioClient("http://127.0.0.1:1/v1", "tok")
    monkeypatch.setattr(c._client.chat, "completions", FakeCompletions())
    assert c.complete(system="s", user="u", model="m", json_schema={"type": "object"}) == '{"a":1}'
    assert captured["response_format"]["json_schema"]["schema"] == {"type": "object"}
    assert captured["model"] == "m"


def test_lmstudio_client_wraps_errors():
    c = LMStudioClient("http://127.0.0.1:1/v1", "tok", timeout_s=1)
    with pytest.raises(LLMError):
        c.list_models()
