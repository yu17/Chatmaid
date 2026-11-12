import dataclasses
import re
from datetime import datetime
from pathlib import Path

import pytest
from fastapi import Depends
from fastapi.testclient import TestClient

from chatmaid.cli import main
from chatmaid.web import app as app_module
from chatmaid.web.app import _when, check_csrf, create_app

from chatmaid.config import ConfigError, load
from chatmaid.connectors.evolution.connector import EvolutionConnector
from chatmaid.llm.base import LLMError
from chatmaid.web.serve import bind_sockets


def test_inbox_lists_incoming_mail_and_escapes_it(web_env):
    web_env.arrive("201", subject="<script>alert(1)</script>", sender='"<b>Eve</b>" <eve@x.org>')
    r = web_env.client.get("/")
    assert r.status_code == 200
    assert "&lt;script&gt;" in r.text
    assert "<script>alert" not in r.text
    assert "&lt;b&gt;Eve" in r.text
    assert "<b>Eve" not in r.text


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


def _client_allowing(web_env, *hosts):
    web = dataclasses.replace(web_env.config.web, allowed_hosts=hosts)
    services = dataclasses.replace(web_env.services, cfg=dataclasses.replace(web_env.config, web=web))
    return TestClient(create_app(services), base_url="http://127.0.0.1")


@pytest.mark.parametrize(
    "host, status",
    [
        ("evil.example", 400),
        ("127.0.0.1", 200),
        ("127.0.0.1:8765", 200),
        ("LOCALHOST", 200),
        ("[::1]:8765", 200),
        ("[::1]", 200),
        ("127.0.0.1.evil.com", 400),
        ("127.0.0.1.", 400),
        ("", 400),
    ],
)
def test_host_check(web_env, host, status):
    client = _client_allowing(web_env, "127.0.0.1", "localhost", "::1")
    r = client.get("/", headers={"Host": host})
    assert r.status_code == status
    if status == 400:
        assert r.text == "unknown host"


def test_responses_forbid_framing(web_env):
    for r in (
        web_env.client.get("/"),
        web_env.client.get("/static/app.css"),
        web_env.client.get("/", headers={"Host": "evil.example"}),
    ):
        assert r.headers["x-frame-options"] == "DENY"
        csp = r.headers["content-security-policy"]
        assert "frame-ancestors 'none'" in csp and "form-action 'self'" in csp
        assert "default-src 'self'" in csp and "base-uri 'none'" in csp


def test_templates_have_no_inline_script_or_style():
    for path in (Path(app_module.__file__).parent / "templates").glob("*.html"):
        text = path.read_text()
        assert not re.search(r"<script(?![^>]*\ssrc=)|<style|\sstyle=|\son[a-z]+\s*=", text, re.I), path


def test_every_post_route_checks_csrf(web_env):
    def uses_csrf(dependant):
        return any(
            d.call is check_csrf or uses_csrf(d) for d in dependant.dependencies
        )

    for route in web_env.app.routes:
        if "POST" in getattr(route, "methods", ()):
            assert uses_csrf(route.dependant), route.path


def test_post_without_valid_token_is_forbidden(web_env):
    @web_env.app.post("/_probe", dependencies=[Depends(check_csrf)])
    def probe():
        return {"ok": True}

    c = web_env.client
    assert c.post("/_probe").status_code == 403
    assert c.post("/_probe", data={"csrf": "nope"}).status_code == 403
    assert c.post("/_probe", data={"csrf": web_env.app.state.csrf}).status_code == 200


def test_when_filter_uses_local_time_and_dashes_missing():
    assert _when(None) == "\u2014" and _when(0) == "\u2014"
    assert _when(1_700_000_000) == datetime.fromtimestamp(1_700_000_000).astimezone().strftime("%Y-%m-%d %H:%M")


def test_serve_rejects_bad_port(capsys):
    assert main(["serve", "--port", "70000"]) == 2
    assert "--port" in capsys.readouterr().err


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


def test_message_page_shows_text_thread_related_and_reply_panel(web_env):
    first = web_env.arrive("401", subject="Quarterly plan", body="Can we meet Friday?\n\n> old quote")
    second = web_env.arrive(
        "402", subject="Re: Quarterly plan", body="Second note", date=1_700_000_600,
        message_id="<r402@x>", in_reply_to="<m401@x>",
    )
    r = web_env.client.get(f"/messages/{first['id']}")
    assert r.status_code == 200
    assert "Quarterly plan" in r.text and "Ann" in r.text
    assert "Can we meet Friday?" in r.text
    assert '<pre class="mail">' in r.text and "<details" in r.text
    assert "Related" in r.text and "Reply" in r.text
    assert f"/messages/{second['id']}" in r.text and "Second note" in r.text
    assert 'class="thread-msg"' in r.text
    assert f"/messages/{first['id']}/draft" in r.text or "/drafts/" in r.text


def test_message_escapes_html_in_body(web_env):
    row = web_env.arrive("403", body="<img src=x onerror=alert(1)>", subject="<script>x</script>")
    r = web_env.client.get(f"/messages/{row['id']}")
    assert "<img src=x" not in r.text and "<script>x" not in r.text
    assert "&lt;img src=x" in r.text


def test_message_gone_from_source_shows_preview_and_note(web_env, monkeypatch):
    row = web_env.arrive("404", body="Gone soon body text")

    def gone(self, ref):
        raise KeyError(ref)

    monkeypatch.setattr(EvolutionConnector, "fetch", gone)
    r = web_env.client.get(f"/messages/{row['id']}")
    assert r.status_code == 200
    assert "no longer in the mail store" in r.text
    assert "Gone soon" in r.text


def test_long_conversation_pages_at_50(web_env):
    first = web_env.arrive("500", message_id="<root@x>", date=1_700_000_000, subject="long")
    for i in range(1, 60):
        web_env.arrive(
            str(500 + i), message_id=f"<l{i}@x>", in_reply_to="<root@x>",
            date=1_700_000_000 + i, subject="Re: long",
        )
    cid = first["conversation_id"]
    r = web_env.client.get(f"/conversations/{cid}")
    assert r.status_code == 200
    assert r.text.count('class="thread-msg"') == 50
    m = re.search(r"before=(\d+)", r.text)
    assert m, "show older link"
    r2 = web_env.client.get(f"/conversations/{cid}", params={"before": m.group(1)})
    assert 0 < r2.text.count('class="thread-msg"') <= 10
    page = web_env.client.get(f"/messages/{first['id']}")
    assert page.text.count('class="thread-msg"') == 50 and "before=" in page.text
    assert web_env.client.get("/conversations/99999").status_code == 404


def test_search_page_lists_threads_and_escapes_snippets(web_env):
    web_env.arrive("601", subject="<b>Budget</b> review", body="zebracorn budget <script>alert(1)</script>")
    r = web_env.client.get("/search", params={"q": "zebracorn"})
    assert r.status_code == 200
    assert "/conversations/" in r.text
    assert "<script>alert" not in r.text and "<b>Budget" not in r.text
    assert "&lt;b&gt;Budget" in r.text


def test_search_without_token_falls_back_with_notice(web_env, monkeypatch):
    web_env.arrive("602", body="zebracorn again")

    def nope():
        raise ConfigError("no token")

    monkeypatch.setattr(web_env.services, "make_llm", nope)
    r = web_env.client.get("/search", params={"q": "zebracorn"})
    assert r.status_code == 200 and "full-text only: no LM Studio token" in r.text
    assert "/conversations/" in r.text


def test_search_llm_failure_falls_back(web_env, monkeypatch):
    web_env.arrive("603", body="zebracorn once")

    def boom(*a, **k):
        raise LLMError("down")

    monkeypatch.setattr(web_env.llm, "embed", boom)
    r = web_env.client.get("/search", params={"q": "zebracorn"})
    assert r.status_code == 200 and "full-text only" in r.text
    assert "/conversations/" in r.text


def test_search_filters_and_bad_date(web_env):
    web_env.arrive("604", body="zebracorn z")
    assert web_env.client.get("/search").status_code == 200
    ok = web_env.client.get("/search", params={"q": "zebracorn", "since": "2020-01-01", "until": "2030-01-01"})
    assert ok.status_code == 200 and "/conversations/" in ok.text
    assert web_env.client.get("/search", params={"q": "x", "since": "yesterday"}).status_code == 400
    assert web_env.client.get("/search", params={"q": "x", "until": "2020-13-45"}).status_code == 400
    assert web_env.client.get("/search", params={"q": "zebracorn", "category": ["bogus"]}).status_code == 400


def test_unknown_message_is_404(web_env):
    r = web_env.client.get("/messages/99999")
    assert r.status_code == 404
    assert web_env.client.get("/messages/abc").status_code == 422
