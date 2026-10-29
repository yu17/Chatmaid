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
    assert len(f.calls) == 2
    assert set(f.calls[-1]) == {"system", "user", "model", "json_schema"}


def test_fake_llm_unknown_key_returns_empty_object():
    assert FakeLLM().complete(system="X", user="u", model="m") == "{}"


def test_fake_embed_is_deterministic():
    f = FakeLLM(dim=4)
    assert f.embed(["a"], model="e") == f.embed(["a"], model="e")
    assert len(f.embed(["a"], model="e")[0]) == 4
    assert all(0 <= x < 1 for x in f.embed(["a", "b"], model="e")[1])


def _capture(monkeypatch, client, content):
    captured = {}

    class FakeCompletions:
        def create(self, **kw):
            captured.update(kw)
            return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=content))])

    monkeypatch.setattr(client._client.chat, "completions", FakeCompletions())
    return captured


def test_lmstudio_client_thinking_enforces_schema_without_prefill_or_cap(monkeypatch):
    c = LMStudioClient("http://127.0.0.1:1/v1", "tok")  # think defaults to on
    captured = _capture(monkeypatch, c, '{"a":1}')
    assert c.complete(system="s", user="u", model="m", json_schema={"type": "object"}) == '{"a":1}'
    assert captured["response_format"]["json_schema"]["schema"] == {"type": "object"}
    assert captured["response_format"]["json_schema"]["strict"] is True
    assert [m["role"] for m in captured["messages"]] == ["system", "user"]
    assert captured["model"] == "m"
    assert "max_tokens" not in captured
    assert "temperature" not in captured


def test_lmstudio_client_think_off_prefills_and_skips_response_format(monkeypatch):
    c = LMStudioClient("http://127.0.0.1:1/v1", "tok", think=False)
    captured = _capture(monkeypatch, c, "{}")
    c.complete(system="s", user="u", model="m", json_schema={"type": "object"})
    assert "response_format" not in captured
    assert captured["messages"][-1] == {"role": "assistant", "content": "<think>\n\n</think>\n\n"}
    assert "max_tokens" not in captured


def test_lmstudio_client_leaves_sampling_to_lm_studio(monkeypatch):
    c = LMStudioClient("http://127.0.0.1:1/v1", "tok", think=False)
    captured = _capture(monkeypatch, c, "{}")
    c.complete(system="s", user="u", model="m")
    assert set(captured) == {"model", "messages"}
    with pytest.raises(TypeError):
        LMStudioClient("http://127.0.0.1:1/v1", "tok", temperature=0.9)


def test_lmstudio_client_strips_inline_think_block(monkeypatch):
    c = LMStudioClient("http://127.0.0.1:1/v1", "tok")
    _capture(monkeypatch, c, '<think>\nplan the answer\n</think>\n\n{"a":1}')
    assert c.complete(system="s", user="u", model="m") == '{"a":1}'


def test_lmstudio_client_wraps_errors():
    c = LMStudioClient("http://127.0.0.1:1/v1", "tok", timeout_s=1)
    with pytest.raises(LLMError):
        c.list_models()


def test_lmstudio_client_empty_choices_is_an_llm_error(monkeypatch):
    class FakeCompletions:
        def create(self, **kw):
            return SimpleNamespace(choices=[])

    c = LMStudioClient("http://127.0.0.1:1/v1", "tok")
    monkeypatch.setattr(c._client.chat, "completions", FakeCompletions())
    with pytest.raises(LLMError):
        c.complete(system="s", user="u", model="m")


def test_fake_llm_sequence_responses():
    llm = FakeLLM({"p": ['{"a":1}', '{"a":2}']})
    out = [llm.complete(system="# p\nx", user="u", model="m") for _ in range(3)]
    assert out == ['{"a":1}', '{"a":2}', '{"a":2}']
