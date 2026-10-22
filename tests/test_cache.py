import sqlite3

import pytest

from chatmaid.connectors.evolution.cache import EvolutionCache
from conftest import make_cache


def test_folders_and_rows(tmp_path):
    cache_dir = make_cache(tmp_path, "a1", {"INBOX": [dict(uid="1", subject="s1", body="From: a@x.org\n\nhi"), dict(uid="2", subject="s2", body="From: b@x.org\n\nyo")]})
    c = EvolutionCache(cache_dir, "a1")
    try:
        [inbox] = c.folders()
        assert inbox.name == "INBOX" and inbox.saved_count == 2
        assert [r.uid for r in c.iter_rows(inbox)] == ["1", "2"]
        assert [r.uid for r in c.iter_rows(inbox, after_uid=1)] == ["2"]
        assert c.max_uid(inbox) == 2
    finally:
        c.close()


def test_body_path_and_missing(tmp_path):
    cache_dir = make_cache(tmp_path, "a1", {"INBOX": [dict(uid="1", body="x"), dict(uid="2")]})
    c = EvolutionCache(cache_dir, "a1")
    try:
        [inbox] = c.folders()
        assert c.body_path(inbox, "1").read_text() == "x"
        assert c.body_path(inbox, "2") is None
    finally:
        c.close()


def test_max_uid_empty_folder(tmp_path):
    cache_dir = make_cache(tmp_path, "a1", {"INBOX": []})
    c = EvolutionCache(cache_dir, "a1")
    try:
        [inbox] = c.folders()
        assert c.max_uid(inbox) is None
    finally:
        c.close()


def test_folder_dir_maps_slash_to_subfolders(tmp_path):
    c = EvolutionCache(tmp_path, "a1")
    try:
        assert c.folder_dir("[Gmail]/Sent Mail") == tmp_path / "a1" / "folders" / "[Gmail]" / "subfolders" / "Sent Mail"
    finally:
        c.close()


def test_opens_read_only(tmp_path):
    cache_dir = make_cache(tmp_path, "a1", {"INBOX": []})
    c = EvolutionCache(cache_dir, "a1")
    try:
        with pytest.raises(sqlite3.OperationalError):
            c._conn.execute("INSERT INTO keys VALUES ('x','y')")
    finally:
        c.close()
