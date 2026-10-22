import pytest

from chatmaid.connectors.evolution.sources import MissingAccount, load_accounts, parse_folder_uri
from conftest import make_sources


def test_parse_folder_uri_decodes_percent_and_brackets():
    assert parse_folder_uri("folder://2bb8/%5bGmail%5d/Sent%20Mail") == ("2bb8", "[Gmail]/Sent Mail")


def test_parse_folder_uri_decodes_utf8():
    assert parse_folder_uri("folder://19f3/%e5%b7%b2%e5%8f%91%e9%80%81%e9%82%ae%e4%bb%b6") == ("19f3", "已发送邮件")


def test_load_accounts_reads_identity_and_sent_folder(tmp_path):
    d = make_sources(tmp_path, [dict(uid="a1", display_name="me@x.org", backend="imapx", identity_uid="i1",
                                     address="Me@X.org", sent_folder="folder://a1/%5bGmail%5d/Sent%20Mail", drafts_folder="folder://a1/Drafts")])
    [acct] = load_accounts(d, ["a1"])
    assert acct.backend == "imapx" and acct.identity_addresses == ("me@x.org",)
    assert acct.sent_folder == "[Gmail]/Sent Mail" and acct.drafts_folder == "Drafts" and acct.enabled


def test_load_accounts_missing_uid_raises(tmp_path):
    with pytest.raises(MissingAccount):
        load_accounts(make_sources(tmp_path, []), ["nope"])


def test_load_accounts_missing_identity_file_is_tolerated(tmp_path):
    d = make_sources(tmp_path, [dict(uid="a1", display_name="x", backend="ews", identity_uid="i1",
                                     address="a@x.org", sent_folder="folder://a1/Sent", drafts_folder="folder://a1/D")])
    (d / "i1.source").unlink()
    [acct] = load_accounts(d, ["a1"])
    assert acct.identity_addresses == () and acct.sent_folder is None
