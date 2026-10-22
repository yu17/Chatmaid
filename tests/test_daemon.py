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
    watch_env.add_folder("Exchange Inbox", time_mode=True)
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
