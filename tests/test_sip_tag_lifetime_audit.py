from types import SimpleNamespace

import rfcvoip.SIP as sip_module
from rfcvoip.SIP import SIPClient, SIPMessage


def client():
    return SIPClient(
        "127.0.0.1",
        5060,
        "alice",
        "secret",
        phone=SimpleNamespace(),
        myIP="127.0.0.1",
    )


def test_tag_generation_does_not_require_md5(monkeypatch):
    def unavailable(*args, **kwargs):
        raise ValueError("MD5 is disabled")

    monkeypatch.setattr(sip_module.hashlib, "md5", unavailable)

    instance = client()
    tags = [instance.gen_tag() for _ in range(3)]

    assert len(set(tags)) == 3
    assert all(len(tag) == 8 for tag in tags)
    assert instance.tagLibrary["register"] not in tags


def test_recent_tag_history_has_a_fixed_bound():
    instance = client()

    for _ in range(2048):
        instance.gen_tag()

    assert len(instance.tags) <= 1024
    assert len(set(instance.tags)) == len(instance.tags)


def test_tag_generation_never_reuses_an_aged_out_active_tag(monkeypatch):
    instance = client()
    instance.tagLibrary["register"] = "deadbeef"
    instance.tags = [f"{index:08x}" for index in range(1024)]
    candidates = iter(["deadbeef", "facefeed"])
    monkeypatch.setattr(
        sip_module.uuid,
        "uuid4",
        lambda: SimpleNamespace(hex=next(candidates) + "0" * 24),
    )

    assert instance.gen_tag() == "facefeed"
    assert len(instance.tags) <= 1024


def test_call_ids_do_not_repeat_across_clients_at_the_same_address():
    first, second = client(), client()

    first_ids = {first.gen_call_id() for _ in range(3)}
    second_ids = {second.gen_call_id() for _ in range(3)}

    assert first_ids.isdisjoint(second_ids)


def test_last_call_id_matches_each_most_recent_generated_id():
    instance = client()

    for _ in range(3):
        generated = instance.gen_call_id()
        assert instance.gen_last_call_id() == generated


def test_register_reuses_call_id_and_increments_cseq_through_authentication():
    instance = client()
    first = SIPMessage(instance.gen_first_response().encode())
    refresh = SIPMessage(instance.gen_first_response().encode())
    challenge = SIPMessage(
        (
            "SIP/2.0 401 Unauthorized\r\n"
            f"Call-ID: {first.headers['Call-ID']}\r\n"
            'WWW-Authenticate: Digest realm="test", nonce="n", qop="auth"\r\n'
            "Content-Length: 0\r\n\r\n"
        ).encode()
    )

    authenticated = SIPMessage(instance.gen_register(challenge).encode())

    requests = [first, refresh, authenticated]
    assert len({request.headers["Call-ID"] for request in requests}) == 1
    assert [request.headers["CSeq"]["check"] for request in requests] == [
        "1",
        "2",
        "3",
    ]
    assert instance.gen_last_call_id() == first.headers["Call-ID"]
