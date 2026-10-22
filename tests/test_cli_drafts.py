import pytest

from chatmaid.cli import build_parser, main
from chatmaid.cloud.fake import FakeCloud
from chatmaid.init.pipeline import InitOptions, InitPipeline
from chatmaid.llm.fake import FakeLLM
from chatmaid.model import SourceRef
from chatmaid.store import Store


def _ingest(fake_env):
    store = Store(fake_env.config.index_path, embedding_dim=4)
    InitPipeline(store, fake_env.connector, FakeLLM(dim=4), fake_env.config).ingest(InitOptions())
    fake_env.row_id = store.message_by_ref(SourceRef("evolution", "a1", "INBOX", "1").key())["id"]
    store.close()
    return fake_env


@pytest.fixture
def old_mail_env(fake_env):
    return _ingest(fake_env)  # INBOX/1 is from 2020: past the default 14-day draft window


@pytest.fixture
def cli_draft_env(fake_env):
    text = fake_env.config_path.read_text(encoding="utf-8")
    assert "max_age_days = 14 " in text
    fake_env.config_path.write_text(
        text.replace("max_age_days = 14 ", "max_age_days = 100000 "), encoding="utf-8"
    )
    return _ingest(fake_env)


def run(env, *argv):
    return main(["--config", str(env.config_path), *argv])


def make_draft(env):
    assert run(env, "draft", "run", "--message", str(env.row_id), "--fake-llm") == 0


def test_cli_draft_run_and_list(cli_draft_env, capsys):
    assert run(cli_draft_env, "draft", "run", "--message", str(cli_draft_env.row_id), "--fake-llm") == 0
    assert "draft 1" in capsys.readouterr().out
    assert run(cli_draft_env, "drafts") == 0
    out = capsys.readouterr().out.splitlines()
    assert out[0].split()[:3] == ["id", "conv", "status"]
    assert "pending" in out[1] and "test" in out[1]


def test_cli_draft_run_applies_the_age_rule(old_mail_env, capsys):
    assert run(old_mail_env, "draft", "run", "--message", str(old_mail_env.row_id), "--fake-llm") == 0
    assert capsys.readouterr().out == "no draft: informational (older than 14 days)\n"


def test_cli_draft_run_unknown_message(cli_draft_env, capsys):
    assert run(cli_draft_env, "draft", "run", "--message", "99999", "--fake-llm") == 1
    assert "99999" in capsys.readouterr().err


def test_cli_draft_show_lists_versions(cli_draft_env, capsys):
    make_draft(cli_draft_env)
    capsys.readouterr()
    assert run(cli_draft_env, "draft", "show", "1") == 0
    out = capsys.readouterr().out
    assert "pending" in out and "round 1.1" in out and "round 1.2" in out and "local" in out


def test_cli_regenerate_adds_version(cli_draft_env, capsys):
    make_draft(cli_draft_env)
    capsys.readouterr()
    assert run(cli_draft_env, "draft", "regenerate", "1", "--comment", "shorter", "--fake-llm") == 0
    assert capsys.readouterr().out.startswith("v")
    run(cli_draft_env, "draft", "show", "1")
    out = capsys.readouterr().out
    assert "round 2.1" in out and "comment: shorter" in out


def test_cli_approve_edit_dismiss(cli_draft_env, capsys):
    make_draft(cli_draft_env)
    assert run(cli_draft_env, "draft", "approve", "1") == 0
    run(cli_draft_env, "draft", "show", "1")
    assert "approved" in capsys.readouterr().out
    assert run(cli_draft_env, "draft", "approve", "1") == 1
    assert "1" in capsys.readouterr().err
    make_draft(cli_draft_env)
    assert run(cli_draft_env, "draft", "edit", "2", "--text", "my own words") == 0
    assert run(cli_draft_env, "draft", "dismiss", "2") == 1  # no longer pending
    make_draft(cli_draft_env)
    assert run(cli_draft_env, "draft", "dismiss", "3") == 0


def test_cli_cloud_propose_non_tty_then_send(cli_draft_env, capsys, monkeypatch):
    monkeypatch.setattr("chatmaid.cli.make_backend", lambda cfg, name: FakeCloud(name="codex", model="m"))
    make_draft(cli_draft_env)
    capsys.readouterr()
    assert run(cli_draft_env, "draft", "cloud", "1", "--fake-llm") == 0
    out = capsys.readouterr().out
    assert "----- payload to codex (m)," in out and "proposal 1 -----" in out
    assert "chatmaid draft send 1" in out
    run(cli_draft_env, "cloud-calls")
    assert "proposed" in capsys.readouterr().out
    assert run(cli_draft_env, "draft", "send", "1") == 0
    assert capsys.readouterr().out.startswith("v")
    run(cli_draft_env, "draft", "show", "1")
    assert "sent" in capsys.readouterr().out


def test_cli_cloud_decline(cli_draft_env, capsys, monkeypatch):
    monkeypatch.setattr("chatmaid.cli.make_backend", lambda cfg, name: FakeCloud(name="codex", model="m"))
    make_draft(cli_draft_env)
    run(cli_draft_env, "draft", "cloud", "1", "--fake-llm")
    capsys.readouterr()
    assert run(cli_draft_env, "draft", "decline", "1") == 0
    run(cli_draft_env, "cloud-calls")
    assert "declined" in capsys.readouterr().out
    assert run(cli_draft_env, "draft", "send", "1") == 1


def test_cli_watch_without_token_fails_cleanly(fake_env, capsys):
    assert not fake_env.config.lmstudio.token_file.exists()
    assert main(["--config", str(fake_env.config_path), "watch"]) == 1
    assert "token" in capsys.readouterr().err.lower()
    parser, _ = build_parser()
    assert parser.parse_args(["watch", "--no-draft"]).no_draft is True
