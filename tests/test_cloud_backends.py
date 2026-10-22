import json
import subprocess
from pathlib import Path

import pytest

from chatmaid.cli import main
from chatmaid.cloud.base import CLOUD_SCHEMA, CloudError
from chatmaid.cloud.claude import ClaudeBackend
from chatmaid.cloud.codex import CodexBackend
from chatmaid.cloud.fake import FakeCloud
from chatmaid.cloud.registry import make_backend


class Runner:
    """Stands in for subprocess.run: records the call and returns a canned process."""

    def __init__(self, stdout="", returncode=0, stderr="", write_out=None, raises=None):
        self.stdout, self.returncode, self.stderr = stdout, returncode, stderr
        self.write_out, self.raises = write_out, raises
        self.argv = None
        self.cwd = None
        self.env = None
        self.kwargs = None
        self.cwd_entries = None

    def __call__(self, argv, *, cwd, env, stdin, capture_output, text, timeout):
        self.argv, self.cwd, self.env = argv, cwd, env
        self.kwargs = dict(stdin=stdin, capture_output=capture_output, text=text, timeout=timeout)
        self.cwd_entries = list(Path(cwd).iterdir())
        if self.raises:
            raise self.raises
        if self.write_out is not None:
            Path(argv[argv.index("-o") + 1]).write_text(json.dumps(self.write_out))
        return subprocess.CompletedProcess(argv, self.returncode, self.stdout, self.stderr)


GOOD = {"text": "t", "rationale": "r"}


def test_codex_command_shape_and_output_file():
    r = Runner(write_out=GOOD)
    out = CodexBackend("gpt-x", timeout_s=30, runner=r).complete("PROMPT", CLOUD_SCHEMA)
    assert out == GOOD
    assert r.argv[:9] == ["codex", "exec", "--ephemeral", "--ignore-user-config", "--ignore-rules",
                          "-s", "read-only", "-C", str(r.cwd)]
    i = r.argv.index("--skip-git-repo-check")
    assert r.argv[i : i + 3] == ["--skip-git-repo-check", "--color", "never"]
    assert r.argv[r.argv.index("-m") + 1] == "gpt-x"
    assert r.argv[-1] == "PROMPT"
    assert r.cwd_entries == []
    assert r.kwargs["stdin"] is subprocess.DEVNULL
    assert r.kwargs["timeout"] == 30
    assert not Path(r.cwd).exists()


def test_codex_omits_model_flag_when_empty():
    r = Runner(write_out=GOOD)
    CodexBackend("", timeout_s=30, runner=r).complete("P", CLOUD_SCHEMA)
    assert "-m" not in r.argv


def test_codex_nonzero_exit_raises():
    r = Runner(returncode=2, stderr="boom")
    with pytest.raises(CloudError, match="codex exited 2: boom"):
        CodexBackend("m", timeout_s=30, runner=r).complete("P", CLOUD_SCHEMA)


def test_claude_command_shape_and_structured_output():
    stdout = json.dumps({"type": "result", "subtype": "success", "is_error": False, "structured_output": GOOD})
    r = Runner(stdout=stdout)
    out = ClaudeBackend("sonnet", timeout_s=30, runner=r).complete("PROMPT", CLOUD_SCHEMA)
    assert out == GOOD
    assert r.argv[:3] == ["claude", "-p", "--tools"]
    assert r.argv[r.argv.index("--json-schema") + 1] == json.dumps(CLOUD_SCHEMA)
    assert r.argv[r.argv.index("--model") + 1] == "sonnet"
    assert "--bare" not in r.argv
    assert r.argv[-1] == "PROMPT"
    assert r.cwd_entries == []
    assert r.kwargs["stdin"] is subprocess.DEVNULL


def test_claude_error_envelope_raises():
    r = Runner(stdout=json.dumps({"is_error": True, "result": "Not logged in"}), returncode=1)
    with pytest.raises(CloudError, match="Not logged in"):
        ClaudeBackend("", timeout_s=30, runner=r).complete("P", CLOUD_SCHEMA)


def test_env_scrubbed_of_claudecode(monkeypatch):
    monkeypatch.setenv("CLAUDECODE", "1")
    monkeypatch.setenv("CLAUDE_CODE_ENTRYPOINT", "cli")
    r = Runner(write_out=GOOD)
    CodexBackend("m", timeout_s=30, runner=r).complete("P", CLOUD_SCHEMA)
    assert "CLAUDECODE" not in r.env
    assert "CLAUDE_CODE_ENTRYPOINT" not in r.env


def test_timeout_raises():
    r = Runner(raises=subprocess.TimeoutExpired(["codex"], 5))
    with pytest.raises(CloudError, match="timed out"):
        CodexBackend("m", timeout_s=5, runner=r).complete("P", CLOUD_SCHEMA)


def test_registry_default_is_codex_with_configured_model(fake_env):
    b = make_backend(fake_env.config)
    assert b.name == "codex"
    assert b.model == fake_env.config.cloud.codex_model
    assert make_backend(fake_env.config, "claude").name == "claude"
    with pytest.raises(CloudError, match="unknown cloud backend 'x'"):
        make_backend(fake_env.config, "x")


def test_cli_cloud_check_prints_fields(fake_env, monkeypatch, capsys):
    fake = FakeCloud(name="codex", model="gpt-x")
    monkeypatch.setattr("chatmaid.cli.make_backend", lambda cfg, name=None: fake)
    assert main(["--config", str(fake_env.config_path), "cloud-check"]) == 0
    out = capsys.readouterr().out
    assert "backend=codex model=gpt-x" in out
    assert "ok" in out and "fake" in out
    assert "[PERSON_1]" in fake.prompts[0]


def test_cli_cloud_check_error_exits_1(fake_env, monkeypatch, capsys):
    fake = FakeCloud(response=CloudError("nope"))
    monkeypatch.setattr("chatmaid.cli.make_backend", lambda cfg, name=None: fake)
    assert main(["--config", str(fake_env.config_path), "cloud-check"]) == 1
    assert "nope" in capsys.readouterr().err
