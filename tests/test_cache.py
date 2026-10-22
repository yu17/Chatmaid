import hashlib
import logging
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
        assert [r.uid for r in c.iter_rows(inbox, after=1)] == ["2"]
        assert c.uid_mode(inbox) == "int"
        assert c.watermark(inbox, c.iter_rows(inbox)) == 2
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


def test_watermark_empty_folder(tmp_path):
    cache_dir = make_cache(tmp_path, "a1", {"INBOX": []})
    c = EvolutionCache(cache_dir, "a1")
    try:
        [inbox] = c.folders()
        assert c.uid_mode(inbox) == "int"
        assert c.watermark(inbox, c.iter_rows(inbox)) is None
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


def test_row_lookup(tmp_path):
    cache_dir = make_cache(tmp_path, "a1", {"INBOX": [dict(uid="1", subject="s1"), dict(uid="2", subject="s2")]})
    c = EvolutionCache(cache_dir, "a1")
    try:
        [inbox] = c.folders()
        assert c.row(inbox, "2").subject == "s2"
        assert c.row(inbox, "7") is None
    finally:
        c.close()


def test_time_mode_folder(tmp_path):
    rows = [
        dict(uid="AQ2", dreceived=200),
        dict(uid="AQ1", dreceived=100),
        dict(uid="AQ3", dreceived=200),
    ]
    cache_dir = make_cache(tmp_path, "a1", {"X": rows})
    c = EvolutionCache(cache_dir, "a1")
    try:
        [x] = c.folders()
        assert c.uid_mode(x) == "time"
        assert [r.uid for r in c.iter_rows(x)] == ["AQ1", "AQ2", "AQ3"]
        assert [r.uid for r in c.iter_rows(x, after=200)] == ["AQ2", "AQ3"]
        assert c.watermark(x, c.iter_rows(x)) == 200
        assert c.watermark(x, c.iter_rows(x, after=999)) is None
    finally:
        c.close()


def test_iter_rows_holds_no_lock_between_rows(tmp_path):
    """A writer with a short busy timeout can COMMIT while a consumer is paused mid-folder (R19)."""
    cache_dir = make_cache(tmp_path, "a1", {"Big": [dict(uid=str(i)) for i in range(1, 601)]})
    c = EvolutionCache(cache_dir, "a1")
    try:
        [big] = c.folders()
        it = c.iter_rows(big)
        assert next(it).uid == "1"
        writer = sqlite3.connect(cache_dir / "a1" / "folders.db", timeout=0.5)
        try:
            writer.execute("INSERT INTO keys VALUES ('probe', '1')")
            writer.commit()
        finally:
            writer.close()
        assert len(list(it)) == 599
    finally:
        c.close()


def test_body_path_resolves_sha256_name_for_opaque_uid(tmp_path):
    uid = "AQMkADAwATM0MDAAMS1hNjQ3LTQ5NGYtMDACLTAwCgBGAAAD"
    cache_dir = make_cache(tmp_path, "a1", {"Exch": [dict(uid=uid, dreceived=1, body="opaque body")]})
    c = EvolutionCache(cache_dir, "a1")
    try:
        [exch] = c.folders()
        found = c.body_path(exch, uid)
        assert found is not None and found.name == hashlib.sha256(uid.encode()).hexdigest()
        assert found.read_text() == "opaque body"
    finally:
        c.close()


def test_body_path_resolves_uri_escaped_name(tmp_path):
    cache_dir = make_cache(tmp_path, "a1", {"X": [dict(uid="a b", dreceived=1)]})
    bucket = cache_dir / "a1" / "folders" / "X" / "cur" / "3f"
    bucket.mkdir(parents=True)
    (bucket / "a%20b").write_text("escaped")
    c = EvolutionCache(cache_dir, "a1")
    try:
        [x] = c.folders()
        assert c.body_path(x, "a b").read_text() == "escaped"
        assert c.body_path(x, "nope") is None
    finally:
        c.close()


def test_watermark_skips_stray_non_numeric_uid(tmp_path, caplog):
    rows = [dict(uid="1"), dict(uid="2"), dict(uid="stray-uid")]
    cache_dir = make_cache(tmp_path, "a1", {"INBOX": rows})
    c = EvolutionCache(cache_dir, "a1")
    try:
        [inbox] = c.folders()
        assert c.uid_mode(inbox) == "int"
        with caplog.at_level(logging.WARNING):
            assert c.watermark(inbox, c.iter_rows(inbox)) == 2
        assert any("stray-uid" in r.getMessage() for r in caplog.records)
    finally:
        c.close()
