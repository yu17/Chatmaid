import pytest

from chatmaid.cloud.pseudonym import Pseudonymizer, deny_list_for, tag_persons
from chatmaid.llm.fake import FakeLLM
from chatmaid.model import SourceRef


def test_names_and_emails_become_placeholders():
    p = Pseudonymizer([("Ann Lee", "PERSON"), ("ann@x.org", "EMAIL")])
    out = p.redact("Hi Ann Lee, mail me at ann@x.org or bob@y.com")
    assert out == "Hi [PERSON_1], mail me at [EMAIL_1] or [EMAIL_2]"
    assert p.mapping == {"[PERSON_1]": "Ann Lee", "[EMAIL_1]": "ann@x.org", "[EMAIL_2]": "bob@y.com"}


def test_same_name_same_placeholder_across_calls():
    p = Pseudonymizer([("Ann Lee", "PERSON")])
    assert p.redact("ann lee") == "[PERSON_1]"
    assert p.redact("Ann Lee again") == "[PERSON_1] again"
    assert p.mapping == {"[PERSON_1]": "ann lee"}


def test_longest_match_first():
    p = Pseudonymizer([("Ann", "PERSON"), ("Ann Lee", "PERSON")])
    assert p.redact("Ann Lee and Ann") == "[PERSON_1] and [PERSON_2]"


def test_whole_word_for_ascii():
    p = Pseudonymizer([("Ann", "PERSON")])
    assert p.redact("Annie met Ann") == "Annie met [PERSON_1]"


def test_chinese_name_is_substring_matched():
    p = Pseudonymizer([("李强", "PERSON")])
    assert p.redact("李强明天来") == "[PERSON_1]明天来"


def test_phone_and_amount():
    assert Pseudonymizer([]).redact("call +86 138 0013 8000") == "call [PHONE_1]"
    assert Pseudonymizer([]).redact("pay ¥1,200") == "pay ¥1,200"
    assert Pseudonymizer([], redact_amounts=True).redact("pay ¥1,200") == "pay [AMOUNT_1]"


def test_iso_date_is_not_a_phone():
    assert Pseudonymizer([]).redact("due 2026-10-22 at 10:00") == "due 2026-10-22 at 10:00"


@pytest.mark.parametrize(
    "phone",
    ["(312) 555-0142", "312.555.0142", "+1 (312) 555-0142", "+86 138 0013 8000", "312-555-0142"],
)
def test_common_phone_formats_are_redacted(phone):
    assert Pseudonymizer([]).redact(f"call {phone} today") == "call [PHONE_1] today"
    assert Pseudonymizer([]).redact(f"Call {phone}.") == "Call [PHONE_1]."


@pytest.mark.parametrize(
    "text",
    ["due 2026-10-22 at 10:00", "starts 2026-10-22 10:00 sharp", "on 22/10/2026", "3.14 and 2.71",
     "order 12345"],
)
def test_dates_and_short_numbers_are_not_phones(text):
    assert Pseudonymizer([]).redact(text) == text


def test_rehydrate_restores_and_reports_unknown():
    out = Pseudonymizer.rehydrate("[PERSON_1] owes [AMOUNT_9]", {"[PERSON_1]": "Ann"})
    assert out == ("Ann owes [AMOUNT_9]", ["[AMOUNT_9]"])


def test_tag_persons_filters_absent_and_short_names():
    llm = FakeLLM({"pii_tag": '{"persons":["Ann","Zed","李"]}'})
    assert tag_persons(llm, "m", "Ann and 李强") == ["Ann"]


def test_deny_list_for_includes_owner_and_participants(ingested_env):
    store = ingested_env.store
    cid = store.message_by_ref(SourceRef("evolution", "a1", "INBOX", "1").key())["conversation_id"]
    deny = deny_list_for(store, ingested_env.config, cid)
    for pair in [("Jiaqi", "PERSON"), ("fishy", "PERSON"), ("me@x.org", "EMAIL"),
                 ("ann@x.org", "EMAIL"), ("Ann", "PERSON")]:
        assert pair in deny


def test_name_inside_email_local_part_does_not_split_the_address():
    p = Pseudonymizer([("ann", "PERSON")])
    assert p.redact("write to lee.ann@x.org") == "write to [EMAIL_1]"
    assert p.mapping == {"[EMAIL_1]": "lee.ann@x.org"}


def test_name_with_punctuation_or_whitespace_is_matched():
    p = Pseudonymizer([("John (Acme)", "PERSON"), ("Ann ", "PERSON")])
    assert p.redact("John (Acme) and Ann met") == "[PERSON_1] and [PERSON_2] met"
    assert p.redact("Annie") == "Annie"


def test_name_parts_are_redacted_alone():
    p = Pseudonymizer([("Ann Lee", "PERSON")])
    assert p.redact("Lee said hi") == "[PERSON_1] said hi"
    p = Pseudonymizer([("Ann Lee", "PERSON")])
    assert p.redact("Ann Lee and Lee") == "[PERSON_1] and [PERSON_2]"


def test_urls_and_domains_become_placeholders():
    p = Pseudonymizer([])
    out = p.redact("see https://annlee.example.com/x and annlee.com or www.foo.org")
    assert out == "see [URL_1] and [URL_2] or [URL_3]"
    assert Pseudonymizer([]).redact("Hi Ann, ok.") == "Hi Ann, ok."


def test_domain_boundaries():
    assert Pseudonymizer([]).redact("Visit annlee.com.") == "Visit [URL_1]."
    assert Pseudonymizer([]).redact("微信公众号abc.com好") == "微信公众号[URL_1]好"
    assert Pseudonymizer([]).redact("网站annlee.com。") == "网站[URL_1]。"
    text = "v1.2.3 report.pdf 3.14 e.g. notes.md"
    assert Pseudonymizer([]).redact(text) == text


def test_country_code_domains_become_placeholders():
    assert Pseudonymizer([]).redact("anna.ca and lee.fr") == "[URL_1] and [URL_2]"


def test_lower_case_and_stopword_name_parts_are_not_redacted():
    assert Pseudonymizer([("The Support Team", "PERSON")]).redact("The team will reply") == (
        "The team will reply"
    )
    p = Pseudonymizer([("Will Smith", "PERSON")])
    assert p.redact("I will send it to Smith") == "I will send it to [PERSON_1]"
    assert p.mapping == {"[PERSON_1]": "Smith"}


def test_name_parts_drop_punctuation():
    p = Pseudonymizer([("Smith, John", "PERSON")])
    assert p.redact("Smith wrote to John") == "[PERSON_1] wrote to [PERSON_2]"
