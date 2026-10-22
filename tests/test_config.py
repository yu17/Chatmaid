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
