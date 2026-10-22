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
    assert cfg.draft.model_role == "main"
    assert cfg.draft.candidates == 2
    assert cfg.draft.recent_messages == 12
    assert cfg.draft.memories == 8
    assert cfg.draft.message_chars == 6000
    assert cfg.cloud.default_backend == "codex"
    assert cfg.cloud.codex_model == "gpt-6-astra"
    assert cfg.cloud.claude_model == ""
    assert cfg.cloud.timeout_s == 300
    assert cfg.cloud.redact_amounts is False
    assert cfg.draft_model == cfg.lmstudio.model_main


def test_heavy_role_selects_heavy_model(tmp_path):
    p = tmp_path / "config.toml"
    p.write_text('[draft]\nmodel_role = "heavy"\n')
    cfg = load(p, project_root=tmp_path)
    assert cfg.draft_model == cfg.lmstudio.model_heavy


def test_invalid_model_role_rejected(tmp_path):
    p = tmp_path / "config.toml"
    p.write_text('[draft]\nmodel_role = "huge"\n')
    with pytest.raises(ConfigError):
        load(p, project_root=tmp_path)


def test_invalid_backend_rejected(tmp_path):
    p = tmp_path / "config.toml"
    p.write_text('[cloud]\ndefault_backend = "gemini"\n')
    with pytest.raises(ConfigError):
        load(p, project_root=tmp_path)
