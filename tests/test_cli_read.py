import re

import pytest

from chatmaid.cli import main
from chatmaid.init.pipeline import InitOptions, InitPipeline
from chatmaid.llm.fake import FakeLLM
from chatmaid.store import Store


@pytest.fixture
def cli_env(fake_env):
    """A digested index at the config's own index path, so the CLI sees it."""
    store = Store(fake_env.config.index_path, embedding_dim=4)
    llm = FakeLLM(
        {
            "chunk_summary": '{"summary":"S1","events":[],"facts":[]}',
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
