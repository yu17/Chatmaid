import asyncio

import pytest

from chatmaid.connectors.evolution.events import EventSource


async def test_event_source_timer_and_file(tmp_path):
    db = tmp_path / "folders.db"
    db.write_bytes(b"")
    src = EventSource([db], interval_s=1, enable_dbus=False)
    it = src.triggers()
    try:
        first = await asyncio.wait_for(anext(it), 3)
        db.write_bytes(b"x")
        kinds = {first, await asyncio.wait_for(anext(it), 3)}
        assert kinds & {"timer", "file"}
    finally:
        await it.aclose()


async def test_file_trigger_is_debounced(tmp_path):
    db = tmp_path / "folders.db"
    db.write_bytes(b"")
    src = EventSource([db], interval_s=3600, enable_dbus=False)
    it = src.triggers()
    first = asyncio.ensure_future(anext(it))  # the generator body (and its watchers) starts here
    try:
        await asyncio.sleep(0.5)
        for i in range(3):
            db.write_bytes(b"x" * (i + 1))
            await asyncio.sleep(0.1)
        assert await asyncio.wait_for(first, 3) == "file"
        with pytest.raises(asyncio.TimeoutError):
            await asyncio.wait_for(anext(it), 1)
    finally:
        await it.aclose()


async def test_file_watch_is_not_recursive(tmp_path, monkeypatch):
    from chatmaid.connectors.evolution import events

    captured = {}

    async def fake_awatch(*paths, **kwargs):
        captured["paths"] = paths
        captured.update(kwargs)
        yield {(1, str(tmp_path / "a" / "folders.db"))}
        await asyncio.Event().wait()  # keep watching until cancelled, like the real one

    monkeypatch.setattr(events.watchfiles, "awatch", fake_awatch)
    src = EventSource(
        [tmp_path / "a" / "folders.db", tmp_path / "b" / "folders.db"], interval_s=3600, enable_dbus=False
    )
    it = src.triggers()
    try:
        assert await asyncio.wait_for(anext(it), 5) == "file"
    finally:
        await it.aclose()
    assert captured["recursive"] is False
    assert sorted(captured["paths"]) == [str(tmp_path / "a"), str(tmp_path / "b")]
