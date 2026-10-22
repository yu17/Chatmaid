import asyncio
import sqlite3

ANN = dict(sender="ann@x.org", to="me@x.org")


def test_route_owner_to_owner_is_owner(watch_env):
    msg = watch_env.message(sender="me@x.org", to="me2@x.org", folder="INBOX")
    assert watch_env.watcher.route(msg) == "owner"
    assert watch_env.watcher.route(watch_env.message(sender="ann@x.org", to="me@x.org")) == "incoming"


def test_first_trigger_after_ingest_routes_nothing(watch_env):
    assert watch_env.watcher.handle_trigger("timer") == 0 and watch_env.store.events() == []


def test_handle_trigger_routes_and_logs(watch_env):
    watch_env.watcher.handle_trigger("timer")
    watch_env.add_row("INBOX", uid="7", message_id="<n7@x>", date=1_700_000_500, body="new question", **ANN)
    watch_env.add_row("[Gmail]/Sent Mail", uid="8", message_id="<s8@x>", sender="me@x.org", to="ann@x.org", date=1_700_000_600, body="my answer")
    n = watch_env.watcher.handle_trigger("timer")
    events = watch_env.store.events()
    assert n == 2 and sorted(e["kind"] for e in events) == ["incoming", "owner"]
    assert {e["source"] for e in events} == {"evolution"}
    assert watch_env.watcher.handle_trigger("timer") == 0


def test_handle_trigger_time_mode_folder(watch_env):
    watch_env.add_folder("Exchange Inbox")
    watch_env.watcher.handle_trigger("timer")
    watch_env.add_row("Exchange Inbox", uid="AQMkADMy1", message_id="<e1@x>", sender="bob@x.org", to="me@x.org", date=1_700_000_700, dreceived=1_700_000_700, body="hi")
    assert watch_env.watcher.handle_trigger("timer") == 1
    assert watch_env.watcher.handle_trigger("timer") == 0


def test_duplicate_message_id_in_other_folder_is_skipped(watch_env):
    watch_env.add_row("[Gmail]/All Mail", uid="20", message_id="<a@x>", date=1_600_000_000, body="hello there", **ANN)
    assert watch_env.watcher.handle_trigger("timer") == 0 and watch_env.store.events() == []


def test_scan_survives_locked_db(watch_env):
    watch_env.watcher.handle_trigger("timer")
    watch_env.add_row("INBOX", uid="7", message_id="<n7@x>", date=1_700_000_500, body="new question", **ANN)
    watch_env.add_row("[Gmail]/Sent Mail", uid="8", message_id="<s8@x>", sender="me@x.org", to="ann@x.org", date=1_700_000_600, body="my answer")
    lock = sqlite3.connect(watch_env.db_path)
    lock.execute("BEGIN EXCLUSIVE")
    try:
        assert watch_env.watcher.handle_trigger("timer") == 0
    finally:
        lock.rollback()
        lock.close()
    assert watch_env.watcher.handle_trigger("timer") == 2


def test_reply_joins_existing_conversation(watch_env):
    before = watch_env.store.counts()["conversations"]
    watch_env.add_row("INBOX", uid="7", message_id="<n7@x>", date=1_700_000_500, body="re", in_reply_to="<a@x>", **ANN)
    assert watch_env.watcher.handle_trigger("timer") == 1
    assert watch_env.store.counts()["conversations"] == before


async def test_run_handles_triggers_until_stopped(watch_env):
    watch_env.add_row("INBOX", uid="7", message_id="<n7@x>", date=1_700_000_500, body="new question", **ANN)
    watch_env.watcher.events.interval_s = 1
    stop = asyncio.Event()
    task = asyncio.create_task(watch_env.watcher.run(stop))
    for _ in range(40):
        if watch_env.store.events():
            break
        await asyncio.sleep(0.1)
    stop.set()
    await asyncio.wait_for(task, 5)
    assert [e["kind"] for e in watch_env.store.events()] == ["incoming"]


def test_folder_without_ingest_checkpoint_is_baselined(watch_env):
    watch_env.add_folder("Archive")
    watch_env.add_row("Archive", uid="1", message_id="<old1@x>", date=1_600_000_001, body="old backlog", **ANN)
    assert watch_env.watcher.handle_trigger("timer") == 0 and watch_env.store.events() == []
    watch_env.add_row("Archive", uid="2", message_id="<new2@x>", date=1_700_000_800, body="fresh", **ANN)
    assert watch_env.watcher.handle_trigger("timer") == 1


def _body_path(env, folder, uid):
    return env.db_path.parent / "folders" / folder.replace("/", "/subfolders/") / "cur" / "00" / uid


def _add_bodyless(env, uid):
    env.add_row("INBOX", uid=uid, message_id=f"<n{uid}@x>", date=1_700_000_500, body="late body", **ANN)
    path = _body_path(env, "INBOX", uid)
    text = path.read_text(encoding="utf-8")
    path.unlink()
    return path, text


def test_row_without_body_is_deferred_until_body_arrives(watch_env):
    watch_env.watcher.handle_trigger("timer")
    path, text = _add_bodyless(watch_env, "7")
    watch_env.add_row("INBOX", uid="8", message_id="<n8@x>", date=1_700_000_501, body="later row", **ANN)
    key = "a1/INBOX"
    before = watch_env.store.get_checkpoint("watch", "evolution")[key]
    assert watch_env.watcher.handle_trigger("timer") == 1  # uid 8 is routed, uid 7 waits
    assert watch_env.watcher.handle_trigger("timer") == 0
    state = watch_env.store.get_checkpoint("watch", "evolution")
    assert state[key] == before and list(state["_pending"].values()) == [2]
    path.write_text(text, encoding="utf-8")
    assert watch_env.watcher.handle_trigger("timer") == 1
    state = watch_env.store.get_checkpoint("watch", "evolution")
    assert state[key] == 8 and "_pending" not in state


def test_row_that_never_gets_a_body_is_routed_on_fourth_trigger(watch_env):
    watch_env.watcher.handle_trigger("timer")
    _add_bodyless(watch_env, "7")
    assert [watch_env.watcher.handle_trigger("timer") for _ in range(4)] == [0, 0, 0, 1]
    assert "_pending" not in watch_env.store.get_checkpoint("watch", "evolution")


def test_folder_appearing_later_is_baselined_not_routed(watch_env):
    watch_env.watcher.handle_trigger("timer")
    watch_env.add_folder("Later")
    watch_env.add_row("Later", uid="1", message_id="<l1@x>", date=1_700_000_501, body="one", **ANN)
    watch_env.add_row("Later", uid="2", message_id="<l2@x>", date=1_700_000_502, body="two", **ANN)
    assert watch_env.watcher.handle_trigger("timer") == 0
    assert watch_env.store.get_checkpoint("watch", "evolution")["a1/Later"] == 2
    watch_env.add_row("Later", uid="3", message_id="<l3@x>", date=1_700_000_503, body="three", **ANN)
    assert watch_env.watcher.handle_trigger("timer") == 1


def test_poison_message_is_logged_and_others_still_routed(watch_env, monkeypatch):
    import chatmaid.daemon as daemon

    real = daemon.store_message

    def flaky(store, index, connector, msg):
        if msg.message_id == "<n7@x>":
            raise RuntimeError("boom")
        return real(store, index, connector, msg)

    monkeypatch.setattr(daemon, "store_message", flaky)
    watch_env.watcher.handle_trigger("timer")
    watch_env.add_row("INBOX", uid="7", message_id="<n7@x>", date=1_700_000_500, body="poison", **ANN)
    watch_env.add_row("INBOX", uid="8", message_id="<n8@x>", date=1_700_000_501, body="fine", **ANN)
    assert watch_env.watcher.handle_trigger("timer") == 1
    kinds = sorted(e["kind"] for e in watch_env.store.events())
    assert kinds == ["error", "incoming"]
    assert watch_env.store.get_checkpoint("watch", "evolution")["a1/INBOX"] == 8
    assert watch_env.watcher.handle_trigger("timer") == 0


def test_log_event_failure_rolls_back_message_and_keeps_state(watch_env, monkeypatch):
    watch_env.watcher.handle_trigger("timer")
    before = watch_env.store.get_checkpoint("watch", "evolution")
    watch_env.add_row("INBOX", uid="7", message_id="<n7@x>", date=1_700_000_500, body="new", **ANN)
    real = watch_env.store.log_event

    def locked(*a, **k):
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(watch_env.store, "log_event", locked)
    assert watch_env.watcher.handle_trigger("timer") == 0
    assert not watch_env.store.has_message_id("email", "a1", "<n7@x>")
    assert watch_env.store.get_checkpoint("watch", "evolution") == before
    monkeypatch.setattr(watch_env.store, "log_event", real)
    assert watch_env.watcher.handle_trigger("timer") == 1


def test_time_mode_folder_appearing_later_is_baselined(watch_env):
    watch_env.watcher.handle_trigger("timer")
    watch_env.add_folder("Exchange Later")
    watch_env.add_row("Exchange Later", uid="AQ1", message_id="<x1@x>", date=1_700_000_501, body="one", **ANN)
    watch_env.add_row("Exchange Later", uid="AQ2", message_id="<x2@x>", date=1_700_000_502, body="two", **ANN)
    assert watch_env.watcher.handle_trigger("timer") == 0
    watch_env.add_row("Exchange Later", uid="AQ3", message_id="<x3@x>", date=1_700_000_600, body="three", **ANN)
    assert watch_env.watcher.handle_trigger("timer") == 1
    assert watch_env.watcher.handle_trigger("timer") == 0


def test_raising_on_routed_callback_does_not_lose_state(watch_env):
    watch_env.watcher.handle_trigger("timer")

    def boom(*_):
        raise RuntimeError("printer on fire")

    watch_env.watcher.on_routed = boom
    watch_env.add_row("INBOX", uid="7", message_id="<n7@x>", date=1_700_000_500, body="new", **ANN)
    assert watch_env.watcher.handle_trigger("timer") == 1
    assert watch_env.store.get_checkpoint("watch", "evolution")["a1/INBOX"] == 7
    assert watch_env.watcher.handle_trigger("timer") == 0
