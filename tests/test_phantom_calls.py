from types import SimpleNamespace

from rfcvoip import SIP
from rfcvoip.VoIP.VoIP import CallState, VoIPPhone


class DeadThread:
    def is_alive(self):
        return False


def test_cleanup_dead_callback_thread_does_not_delete_answered_call():
    phone = VoIPPhone(
        "127.0.0.1",
        5060,
        "alice",
        "secret",
        myIP="127.0.0.1",
    )

    thread = DeadThread()
    phone.calls["call-1"] = SimpleNamespace(
        state=CallState.ANSWERED,
        assignedPorts={10000: {}},
    )
    phone.threads = [thread]
    phone.threadLookup = {thread: "call-1"}

    phone._cleanup_dead_calls()

    assert "call-1" in phone.calls
    assert phone.threads == []
    assert thread not in phone.threadLookup


def test_unknown_in_dialog_invite_is_rejected_not_reported_as_new_call():
    user_calls = []
    sent_responses = []

    phone = VoIPPhone(
        "127.0.0.1",
        5060,
        "alice",
        "secret",
        myIP="127.0.0.1",
        callCallback=user_calls.append,
    )

    class FakeSip:
        def gen_response(self, request, status):
            assert status == SIP.SIPStatus.CALL_OR_TRANSACTION_DOESNT_EXIST
            return (
                f"SIP/2.0 {int(status)} {status.phrase}\r\n"
                "Content-Length: 0\r\n\r\n"
            )

        def send_response(self, request, response):
            sent_responses.append(response)

    phone.sip = FakeSip()

    request = SIP.SIPMessage(
        b"INVITE sip:alice@127.0.0.1 SIP/2.0\r\n"
        b"Via: SIP/2.0/UDP 192.0.2.10:5060;branch=z9hG4bKphantom\r\n"
        b"From: <sip:bob@example.com>;tag=remote-tag\r\n"
        b"To: <sip:alice@example.com>;tag=local-stale-tag\r\n"
        b"Call-ID: stale-call@example.com\r\n"
        b"CSeq: 2 INVITE\r\n"
        b"Contact: <sip:bob@192.0.2.10:5060>\r\n"
        b"Content-Length: 0\r\n\r\n"
    )

    phone._callback_MSG_Invite(request)

    assert "stale-call@example.com" not in phone.calls
    assert user_calls == []
    assert sent_responses
    assert sent_responses[0].startswith("SIP/2.0 481")
