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


# -- Task 9: background jobs and the Draft page -----------------------------------------


def wait_job(client, job_id, timeout=5.0):
    import time

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        data = client.get(f"/jobs/{job_id}").json()
        if data["state"] in ("done", "failed"):
            return data
        time.sleep(0.02)
    raise AssertionError("job did not finish")


def post(web_env, path, **data):
    return web_env.client.post(
        path, data={"csrf": web_env.app.state.csrf, **data}, follow_redirects=False
    )


def draft_of(web_env, row):
    return web_env.store.pending_draft_for(row["conversation_id"])


def test_post_without_csrf_is_forbidden_on_draft_actions(web_env):
    row = web_env.arrive("500")
    d = draft_of(web_env, row)
    r = web_env.client.post(f"/drafts/{d['id']}/dismiss", follow_redirects=False)
    assert r.status_code == 403
    assert web_env.store.draft(d["id"])["status"] == "pending"


def test_draft_button_creates_a_draft_via_the_worker(web_env):
    row = web_env.arrive("501", subject="Plan")
    r = post(web_env, f"/messages/{row['id']}/draft")
    assert r.status_code == 303 and r.headers["location"].endswith(f"/messages/{row['id']}")
    (job,) = web_env.app.state.jobs.for_subject("draft", row["id"])
    done = wait_job(web_env.client, job.id)
    assert done["state"] == "done" and done["error"] is None
    page = web_env.client.get(f"/drafts/{done['result']}")
    assert page.status_code == 200
    assert "Sure, Friday works." in page.text and "Friday is fine, thanks." in page.text
    assert "short confirmation" in page.text and "Plan" in page.text and "Ann" in page.text
    assert f"/messages/{row['id']}" in page.text


def test_message_page_shows_a_running_job_and_a_failed_one(web_env):
    import threading

    row = web_env.arrive("502")
    gate = threading.Event()
    jobs = web_env.app.state.jobs
    jobs.submit("draft", "Drafting a reply", row["id"], lambda flow: gate.wait(5) and None)
    jobs.submit("draft", "Drafting a reply", row["id"], lambda flow: None)
    page = web_env.client.get(f"/messages/{row['id']}").text
    assert "data-job" in page and "queued" in page
    gate.set()
    for j in jobs.for_subject("draft", row["id"]):
        wait_job(web_env.client, j.id)

    def boom(flow):
        raise ValueError("model said no <b>")

    failed = jobs.submit("draft", "Drafting a reply", row["id"], boom)
    wait_job(web_env.client, failed.id)
    page = web_env.client.get(f"/messages/{row['id']}").text
    assert "failed" in page and "model said no &lt;b&gt;" in page


def test_regenerate_approve_edit_dismiss_from_the_page(web_env):
    row = web_env.arrive("503")
    did = draft_of(web_env, row)["id"]
    r = post(web_env, f"/drafts/{did}/regenerate", comment="warmer please")
    assert r.status_code == 303
    (job,) = web_env.app.state.jobs.for_subject("regenerate", did)
    assert wait_job(web_env.client, job.id)["state"] == "done"
    page = web_env.client.get(f"/drafts/{did}").text
    assert "Thanks for the reminder" in page and "warmer please" in page
    assert "<details" in page

    versions = web_env.store.draft_versions(did)
    r = post(web_env, f"/drafts/{did}/approve", version_id=str(versions[0]["id"]))
    assert r.status_code == 303
    after = web_env.store.draft(did)
    assert after["status"] == "approved" and after["final_text"] == versions[0]["text"]
    assert "approved" in web_env.client.get(f"/drafts/{did}").text

    row2 = web_env.arrive("504", subject="other", message_id="<o@x>")
    d2 = draft_of(web_env, row2)["id"]
    assert post(web_env, f"/drafts/{d2}/edit", text="My own words").status_code == 303
    assert web_env.store.draft(d2)["status"] == "edited"
    assert web_env.store.draft(d2)["final_text"] == "My own words"

    row3 = web_env.arrive("505", subject="third", message_id="<t@x>")
    d3 = draft_of(web_env, row3)["id"]
    assert post(web_env, f"/drafts/{d3}/dismiss").status_code == 303
    assert web_env.store.draft(d3)["status"] == "dismissed"


def test_action_on_a_draft_no_longer_pending_shows_the_error(web_env):
    row = web_env.arrive("506")
    did = draft_of(web_env, row)["id"]
    assert post(web_env, f"/drafts/{did}/dismiss").status_code == 303
    for action, data in (
        ("approve", {}), ("dismiss", {}), ("edit", {"text": "x"}),
        ("regenerate", {"comment": "again"}),
    ):
        r = post(web_env, f"/drafts/{did}/{action}", **data)
        assert r.status_code == 200, action
        assert f"draft {did} is dismissed, not pending" in r.text
    assert web_env.app.state.jobs.for_subject("regenerate", did) == []


def test_unknown_draft_is_404(web_env):
    assert web_env.client.get("/drafts/999").status_code == 404
    assert post(web_env, "/drafts/999/dismiss").status_code == 404
    assert web_env.client.get("/jobs/nope").status_code == 404


def test_draft_page_escapes_candidate_text(web_env):
    row = web_env.arrive("507")
    did = draft_of(web_env, row)["id"]
    web_env.store.add_draft_version(
        did, round=2, candidate=1, origin="local", text="<script>alert(1)</script>",
        rationale="<img onerror=x>", comment="<b>c</b>",
    )
    page = web_env.client.get(f"/drafts/{did}").text
    assert "<script>alert" not in page and "&lt;script&gt;alert(1)" in page
    assert "<img onerror" not in page and "&lt;img onerror=x&gt;" in page
    assert "<b>c</b>" not in page


def test_app_js_is_loaded_and_has_job_and_copy_behaviour(web_env):
    assert 'src="/static/app.js"' in web_env.client.get("/").text
    js = web_env.client.get("/static/app.js").text
    assert "data-job" in js and "navigator.clipboard.writeText" in js and "isSecureContext" in js


def test_sync_draft_actions_work_without_an_lm_studio_token(web_env):
    from chatmaid.config import ConfigError

    def no_token():
        raise ConfigError("no token")

    web_env.services.make_llm = no_token
    row = web_env.arrive("510")
    did = draft_of(web_env, row)["id"]
    assert post(web_env, f"/drafts/{did}/dismiss").status_code == 303
    assert web_env.store.draft(did)["status"] == "dismissed"
    # a model job without a token is a failed job, not a crash
    job = web_env.app.state.jobs.submit("draft", "Drafting", row["id"], lambda flow: None)
    assert wait_job(web_env.client, job.id)["state"] == "failed"
    assert "no token" in web_env.client.get(f"/messages/{row['id']}").text


def test_no_second_job_while_one_is_unfinished(web_env):
    import threading

    row = web_env.arrive("511")
    did = draft_of(web_env, row)["id"]
    jobs = web_env.app.state.jobs
    gate = threading.Event()
    jobs.submit("draft", "Drafting a reply", row["id"], lambda flow: gate.wait(5) and None)
    page = web_env.client.get(f"/messages/{row['id']}")
    assert "/draft\"" not in page.text.replace(f"/drafts/{did}", "")  # no Draft button
    assert post(web_env, f"/messages/{row['id']}/draft").status_code == 303
    assert len(jobs.for_subject("draft", row["id"])) == 1
    jobs.submit("regenerate", "Regenerating", did, lambda flow: gate.wait(5) and None)
    r = post(web_env, f"/drafts/{did}/regenerate", comment="again")
    assert r.status_code == 200 and "Already working on it." in r.text
    assert len(jobs.for_subject("regenerate", did)) == 1
    gate.set()
    for j in jobs.for_subject("", row["id"]) + jobs.for_subject("", did):
        wait_job(web_env.client, j.id)


def test_resolve_does_not_overwrite_a_draft_settled_meanwhile(web_env):
    import pytest

    row = web_env.arrive("512")
    did = draft_of(web_env, row)["id"]
    web_env.store.set_draft_status(did, "superseded")
    with pytest.raises(ValueError, match=f"draft {did} is superseded, not pending"):
        web_env.flow._settle(did, "approved", "text")
    assert web_env.store.draft(did)["status"] == "superseded"
    assert web_env.store.draft(did)["final_text"] is None


# -- Task 10: cloud improvement with consent and the audit page -------------------------


def recording_cloud(web_env, **response):
    from chatmaid.cloud.fake import FakeCloud

    fakes = []

    def make(name):
        fake = FakeCloud(name or "codex", "m", response or {"text": "Cloud text.", "rationale": "why"})
        fakes.append(fake)
        return fake

    web_env.services.make_backend = make
    return fakes


def propose(web_env, did, **data):
    r = post(web_env, f"/drafts/{did}/cloud", **data)
    assert r.status_code == 303
    job = web_env.app.state.jobs.for_subject("propose", did)[0]
    done = wait_job(web_env.client, job.id)
    assert done["state"] == "done", done
    return done["result"]


def test_cloud_flow_shows_payload_before_send_and_nothing_is_sent_until_send(web_env):
    fakes = recording_cloud(web_env)
    row = web_env.arrive("600")
    did = draft_of(web_env, row)["id"]
    call_id = propose(web_env, did, backend="claude", comment="more <b>formal</b>")
    call = web_env.store.cloud_call(call_id)
    assert call["status"] == "proposed" and call["backend"] == "claude"
    assert all(f.prompts == [] for f in fakes)
    assert f"/cloud/{call_id}" in web_env.client.get(f"/drafts/{did}").text
    page = web_env.client.get(f"/cloud/{call_id}").text
    assert "Send this to claude" in page and "Decline" in page and "<pre" in page
    assert "more &lt;b&gt;formal&lt;/b&gt;" in page
    assert "mapping" not in page.lower()
    assert all(f.prompts == [] for f in fakes)
    r = post(web_env, f"/cloud/{call_id}/send")
    assert r.status_code == 303
    (job,) = web_env.app.state.jobs.for_subject("send", call_id)
    assert wait_job(web_env.client, job.id)["state"] == "done"
    assert [f.prompts for f in fakes if f.prompts] == [[call["payload"]]]
    assert "Cloud text." in web_env.client.get(f"/drafts/{did}").text
    assert web_env.store.cloud_call(call_id)["status"] == "sent"
    # a second Send shows the error and sends nothing more
    again = post(web_env, f"/cloud/{call_id}/send")
    assert again.status_code == 200 and "not proposed" in again.text
    assert sum(len(f.prompts) for f in fakes) == 1


def test_decline_marks_the_call_declined(web_env):
    fakes = recording_cloud(web_env)
    row = web_env.arrive("601")
    did = draft_of(web_env, row)["id"]
    call_id = propose(web_env, did)
    assert web_env.store.cloud_call(call_id)["backend"] == web_env.config.cloud.default_backend
    r = post(web_env, f"/cloud/{call_id}/decline")
    assert r.status_code == 303
    assert web_env.store.cloud_call(call_id)["status"] == "declined"
    assert all(f.prompts == [] for f in fakes)
    page = web_env.client.get(f"/cloud/{call_id}").text
    assert "Send this to" not in page and "declined" in page
    assert "not proposed" in post(web_env, f"/cloud/{call_id}/decline").text


def test_cloud_audit_lists_calls(web_env):
    recording_cloud(web_env)
    row = web_env.arrive("602")
    did = draft_of(web_env, row)["id"]
    call_id = propose(web_env, did)
    page = web_env.client.get("/cloud").text
    assert f"/cloud/{call_id}" in page and f"/drafts/{did}" in page and "proposed" in page
    assert web_env.client.get("/cloud/9999").status_code == 404
    assert post(web_env, "/cloud/9999/send").status_code == 404
    assert post(web_env, "/cloud/9999/decline").status_code == 404
    assert post(web_env, f"/drafts/{did}/cloud", backend="gpt").status_code == 400


def untriaged(web_env, uid, **kw):
    web_env.llm.fail_next(10)  # triage fails, so the message stays untriaged
    try:
        row = web_env.arrive(uid, **kw)
    finally:
        web_env.llm.fail_next(0)
    assert web_env.store.triage_for(row["id"]) is None
    return row


def test_status_page_shows_counts_failures_and_untriaged(web_env):
    web_env.app.state.now = lambda: 1_700_001_000  # the fixtures' clock
    web_env.arrive("700")
    row = untriaged(web_env, "701", subject="<i>stuck</i>", date=1_700_000_900)
    web_env.store.log_event("reply", "error", "x", {"error": "boom <b>bad</b>"})
    page = web_env.client.get("/status").text
    assert "Messages" in page and "Summarized" in page and "Pending summary" in page
    assert "Recent failures" in page and "Recent events" in page
    assert f"/status/retry/{row['id']}" in page and 'name="csrf"' in page
    assert "&lt;i&gt;stuck&lt;/i&gt;" in page and "<i>stuck" not in page
    assert "boom &lt;b&gt;bad&lt;/b&gt;" in page and "<b>bad" not in page


def test_retry_button_triages_an_untriaged_message(web_env):
    row = untriaged(web_env, "702", date=1_700_000_900)
    assert post(web_env, f"/status/retry/{row['id']}").status_code == 303
    (job,) = web_env.app.state.jobs.for_subject("retry", row["id"])
    assert wait_job(web_env.client, job.id)["state"] == "done"
    assert web_env.store.triage_for(row["id"]) is not None
    assert f"/status/retry/{row['id']}" not in web_env.client.get("/status").text
    assert web_env.client.post(f"/status/retry/{row['id']}").status_code == 403
    assert post(web_env, "/status/retry/9999").status_code == 404


def test_retry_of_a_message_gone_from_the_mail_store_fails_readably(web_env):
    row = untriaged(web_env, "703", date=1_700_000_900)

    def gone(ref):
        raise KeyError(ref)

    web_env.connector.fetch = gone
    web_env.services.open_connector = lambda: web_env.connector
    post(web_env, f"/status/retry/{row['id']}")
    (job,) = web_env.app.state.jobs.for_subject("retry", row["id"])
    done = wait_job(web_env.client, job.id)
    assert done["state"] == "failed" and "mail store" in done["error"]
