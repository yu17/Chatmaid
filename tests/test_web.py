from pathlib import Path

import pytest

from chatmaid.config import ConfigError, load
from chatmaid.web.serve import bind_sockets


def test_inbox_lists_incoming_mail_and_escapes_it(web_env):
    web_env.arrive("201", subject="<script>alert(1)</script>", sender="<b>Eve</b> <eve@x.org>")
    r = web_env.client.get("/")
    assert r.status_code == 200
    assert "&lt;script&gt;" in r.text
    assert "<script>alert" not in r.text
    assert "<b>Eve</b>" not in r.text


def test_inbox_filters_and_links(web_env):
    row = web_env.arrive("202", subject="plain subject")
    r = web_env.client.get("/", params={"filter": "all"})
    assert f"/messages/{row['id']}" in r.text
    assert "plain subject" in r.text
    assert web_env.client.get("/", params={"filter": "bogus"}).status_code == 400
    assert web_env.client.get("/", params={"before": "x"}).status_code == 422


def test_inbox_older_link_when_page_is_full(web_env):
    for i in range(51):
        web_env.arrive(str(300 + i), date=1_700_000_000 + i, subject=f"s{i}")
    r = web_env.client.get("/", params={"filter": "all"})
    assert "before=" in r.text


def test_unknown_host_is_rejected(web_env):
    r = web_env.client.get("/", headers={"Host": "evil.example"})
    assert r.status_code == 400
    assert r.text == "unknown host"
    assert web_env.client.get("/", headers={"Host": "127.0.0.1:8765"}).status_code == 200


def test_static_assets_are_served(web_env):
    assert web_env.client.get("/static/app.css").status_code == 200
    assert web_env.client.get("/static/app.js").status_code == 200


def test_bind_sockets_listens_on_each_host():
    socks = bind_sockets(["127.0.0.1"], 0)
    try:
        assert len(socks) == 1
        assert socks[0].getsockname()[0] == "127.0.0.1"
        assert socks[0].getsockname()[1] != 0
    finally:
        for s in socks:
            s.close()


def test_web_config_defaults_and_validation(tmp_path):
    cfg = load(Path("config.example.toml"), project_root=tmp_path)
    assert cfg.web.hosts == ("127.0.0.1",)
    assert cfg.web.port == 8765
    assert cfg.web.allowed_hosts == cfg.web.hosts
    assert cfg.web.base_path == ""

    def write(body):
        p = tmp_path / "c.toml"
        p.write_text("[web]\n" + body)
        return p

    two = load(write('hosts = ["127.0.0.1", "10.0.0.2"]'), project_root=tmp_path)
    assert two.web.allowed_hosts == ("127.0.0.1", "10.0.0.2")
    for bad in ("port = 0", "port = 70000", 'port = "80"', "hosts = []", 'hosts = "x"',
                'base_path = "x"', 'base_path = "/x/"', "allowed_hosts = [1]"):
        with pytest.raises(ConfigError):
            load(write(bad), project_root=tmp_path)
