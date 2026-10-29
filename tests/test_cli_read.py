import re

import pytest

from chatmaid.cli import main
from chatmaid.init.pipeline import DigestStats, InitOptions, InitPipeline
from chatmaid.llm.base import LLMError
from chatmaid.llm.fake import FakeLLM
from chatmaid.store import Store


@pytest.fixture
def cli_env(fake_env):
    """A digested index at the config's own index path, so the CLI sees it."""
    store = Store(fake_env.config.index_path, embedding_dim=4)
    llm = FakeLLM(
        {
            "chunk_summary": '{"category":"conversation","summary":"S1","events":[],"facts":[]}',
            "rolling_summary": '{"summary":"S"}',
        },
        dim=4,
    )
    p = InitPipeline(store, fake_env.connector, llm, fake_env.config)
    p.ingest(InitOptions())
    p.digest(InitOptions())
    store.close()
    return fake_env


def test_cli_conversations_and_show(cli_env, capsys):
    cfg = str(cli_env.config_path)
    assert main(["--config", cfg, "conversations", "--limit", "5"]) == 0
    lines = capsys.readouterr().out.splitlines()
    assert lines[0].split()[:3] == ["id", "last", "count"]
    first_id = lines[1].split()[0]
    assert re.search(r"\d{4}-\d{2}-\d{2}", lines[1])
    assert main(["--config", cfg, "show", first_id]) == 0
    out = capsys.readouterr().out
    assert re.search(r"\d{4}-\d{2}-\d{2}", out) and "participants" in out.lower()


def test_cli_show_unknown_id(cli_env, capsys):
    assert main(["--config", str(cli_env.config_path), "show", "99999"]) == 1
    assert "99999" in capsys.readouterr().err


def test_cli_conversations_all_includes_bulk(cli_env, capsys):
    cfg = str(cli_env.config_path)
    main(["--config", cfg, "conversations"])
    default = len(capsys.readouterr().out.splitlines())
    main(["--config", cfg, "conversations", "--all"])
    assert len(capsys.readouterr().out.splitlines()) > default


def test_cli_lookup_no_vector(cli_env, capsys):
    cfg = str(cli_env.config_path)
    assert main(["--config", cfg, "lookup", "hello", "--no-vector", "--since", "2020-01-01"]) == 0
    out = capsys.readouterr().out
    assert re.match(r"\d{4}-\d{2}-\d{2}  ", out) and "hello" in out.lower()


def test_cli_init_ingest_needs_no_token(fake_env, capsys):
    assert not fake_env.config.lmstudio.token_file.exists()
    assert main(["--config", str(fake_env.config_path), "init", "--pass", "ingest"]) == 0
    assert capsys.readouterr().out.startswith("ingest: IngestStats(")
    store = Store(fake_env.config.index_path, embedding_dim=4)
    try:
        assert store.counts()["messages"] >= 3
    finally:
        store.close()


def test_cli_init_model_pass_without_token_fails_cleanly(fake_env, capsys):
    assert main(["--config", str(fake_env.config_path), "init", "--pass", "digest"]) == 1
    assert "token" in capsys.readouterr().err.lower()


def test_cli_init_reports_llm_error(fake_env, monkeypatch, capsys):
    import chatmaid.cli as cli

    def broken(self, opts):
        raise LLMError("embedding model returned garbage")

    monkeypatch.setattr(cli.InitPipeline, "run", broken)
    assert main(["--config", str(fake_env.config_path), "init", "--pass", "embed", "--fake-llm"]) == 1
    assert "embedding model returned garbage" in capsys.readouterr().err


def test_cli_llm_check_builds_client_from_config(fake_env, monkeypatch, capsys):
    import chatmaid.cli as cli

    fake_env.config.lmstudio.token_file.write_text("tok\n")
    captured = {}
    built = {}

    class Stub:
        def __init__(self, base_url, token, **k):
            built.update(k, base_url=base_url, token=token)

        def list_models(self):
            return ["m"]

        def complete(self, **kw):
            captured.update(kw)
            return "OK"

    monkeypatch.setattr(cli, "LMStudioClient", Stub)
    assert main(["--config", str(fake_env.config_path), "llm-check"]) == 0
    assert "reply: OK" in capsys.readouterr().out
    assert captured["model"] == fake_env.config.lmstudio.model
    assert "grammar" not in captured and "max_tokens" not in captured
    assert built == {
        "base_url": fake_env.config.lmstudio.base_url, "token": "tok",
        "timeout_s": 600, "think": True,
    }


def test_cli_init_stopped_exits_130(fake_env, monkeypatch, capsys):
    def stopped_digest(self, opts):
        self.stopped = True
        return DigestStats(0, 0, 0, 0, 0, stopped=True)

    monkeypatch.setattr("chatmaid.cli.InitPipeline.digest", stopped_digest)
    rc = main(["--config", str(fake_env.config_path), "init", "--pass", "digest", "--fake-llm"])
    assert rc == 130 and "stopped; run the same command to resume" in capsys.readouterr().err
