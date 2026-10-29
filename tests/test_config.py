from pathlib import Path

import pytest

from chatmaid import __version__
from chatmaid.cli import main
from chatmaid.config import ConfigError, load, read_token


def test_load_example_config(tmp_path):
    cfg = load(Path("config.example.toml"), project_root=tmp_path)
    assert cfg.data.hot_window_days == 365
    assert cfg.owner.aliases == ("jiaqi", "fishy")
    assert len(cfg.email.accounts) == 5
    assert cfg.email.skip_folders == ("[Gmail]/All Mail", "[Gmail]/Important", "[Gmail]/Starred")
    assert cfg.index_path == tmp_path / "data" / "index.sqlite"
    assert cfg.lmstudio.base_url == "http://127.0.0.1:40090/v1"


def test_read_token_strips_newline(tmp_path):
    (tmp_path / "LMStudio_token.txt").write_text("abc123\n")
    cfg = load(Path("config.example.toml"), project_root=tmp_path)
    assert read_token(cfg) == "abc123"


def test_read_token_missing_raises(tmp_path):
    cfg = load(Path("config.example.toml"), project_root=tmp_path)
    with pytest.raises(ConfigError):
        read_token(cfg)


def test_cli_version(capsys):
    assert main(["--version"]) == 0
    assert capsys.readouterr().out.strip() == f"chatmaid {__version__}"


def test_defaults_mirror_example_config():
    import tomllib

    from chatmaid.config import _DEFAULTS

    with open("config.example.toml", "rb") as f:
        assert tomllib.load(f) == _DEFAULTS


def test_draft_and_cloud_defaults(tmp_path):
    cfg = load(Path("config.example.toml"), project_root=tmp_path)
    assert cfg.draft.candidates == 2
    assert cfg.draft.recent_messages == 12
    assert cfg.draft.memories == 8
    assert cfg.draft.message_chars == 6000
    assert cfg.draft.max_age_days == 14
    assert cfg.cloud.default_backend == "codex"
    assert cfg.cloud.codex_model == "gpt-6-astra"
    assert cfg.cloud.claude_model == ""
    assert cfg.cloud.timeout_s == 300
    assert cfg.cloud.redact_amounts is False


def test_single_model_and_think_defaults(tmp_path):
    cfg = load(Path("config.example.toml"), project_root=tmp_path)
    assert cfg.lmstudio.model == "qwen3.5-122b-a10b"
    assert cfg.lmstudio.think is True
    assert not hasattr(cfg.lmstudio, "temperature")


def test_model_and_think_are_configurable(tmp_path):
    p = tmp_path / "config.toml"
    p.write_text('[lmstudio]\nmodel = "qwen/qwen3.5-35b-a3b"\nthink = false\n')
    cfg = load(p, project_root=tmp_path)
    assert (cfg.lmstudio.model, cfg.lmstudio.think) == ("qwen/qwen3.5-35b-a3b", False)


def test_stale_temperature_key_rejected(tmp_path):
    p = tmp_path / "config.toml"
    p.write_text('[lmstudio]\ntemperature = 0.6\n')
    with pytest.raises(ConfigError, match="temperature"):
        load(p, project_root=tmp_path)


@pytest.mark.parametrize(
    "text",
    ['[lmstudio]\nmodel_small = "x"\n', '[lmstudio]\nmodel_main = "x"\n', '[lmstudio]\nmodel_heavy = "x"\n'],
)
def test_stale_model_role_keys_rejected(tmp_path, text):
    p = tmp_path / "config.toml"
    p.write_text(text)
    with pytest.raises(ConfigError, match="lmstudio.model"):
        load(p, project_root=tmp_path)


def test_stale_draft_model_role_rejected(tmp_path):
    p = tmp_path / "config.toml"
    p.write_text('[draft]\nmodel_role = "heavy"\n')
    with pytest.raises(ConfigError, match="model_role"):
        load(p, project_root=tmp_path)


def test_think_must_be_bool(tmp_path):
    p = tmp_path / "config.toml"
    p.write_text('[lmstudio]\nthink = "yes"\n')
    with pytest.raises(ConfigError):
        load(p, project_root=tmp_path)


def test_invalid_backend_rejected(tmp_path):
    p = tmp_path / "config.toml"
    p.write_text('[cloud]\ndefault_backend = "gemini"\n')
    with pytest.raises(ConfigError):
        load(p, project_root=tmp_path)


def test_draft_examples_default_and_validation(tmp_path):
    assert load(Path("config.example.toml"), project_root=tmp_path).draft.examples == 3
    p = tmp_path / "config.toml"
    p.write_text('[draft]\nexamples = -1\n')
    with pytest.raises(ConfigError, match="draft.examples"):
        load(p, project_root=tmp_path)
