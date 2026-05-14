import threading
import warnings
from types import SimpleNamespace

import pyVoIP
from pyVoIP import RTP, SIP
from pyVoIP.VoIP.VoIP import CallState, VoIPCall, VoIPPhone


def _invite_response(call_id: str = "race-call@example.test") -> SIP.SIPMessage:
    return SIP.SIPMessage(
        (
            "SIP/2.0 200 OK\r\n"
            "Via: SIP/2.0/UDP 127.0.0.1:5060;branch=z9hG4bKrace\r\n"
            "From: <sip:100@example.test>;tag=localtag\r\n"
            "To: <sip:101@example.test>;tag=remotetag\r\n"
            f"Call-ID: {call_id}\r\n"
            "CSeq: 1 INVITE\r\n"
            "Contact: <sip:101@127.0.0.1:5060>\r\n"
            "Content-Length: 0\r\n"
            "\r\n"
        ).encode("utf8")
    )


def test_outbound_invite_response_is_queued_during_call_construction():
    phone = VoIPPhone(
        "127.0.0.1",
        5060,
        "100",
        "secret",
        myIP="127.0.0.1",
    )
    phone._send_ack = lambda request: None

    call_id = "race-call@example.test"
    response = _invite_response(call_id)
    phone.sip._set_last_invite_debug(call_id=call_id)

    with phone._call_state_lock:
        phone._outbound_call_creation_depth += 1
    try:
        assert phone._queue_unmatched_final_invite_response(response) is True
    finally:
        with phone._call_state_lock:
            phone._outbound_call_creation_depth -= 1

    assert phone.sip.pop_pending_invite_response(call_id) is response


def test_unrelated_invite_response_is_not_queued_during_call_construction():
    phone = VoIPPhone(
        "127.0.0.1",
        5060,
        "100",
        "secret",
        myIP="127.0.0.1",
    )
    phone._send_ack = lambda request: None

    phone.sip._set_last_invite_debug(call_id="active-call@example.test")
    response = _invite_response("other-call@example.test")

    with phone._call_state_lock:
        phone._outbound_call_creation_depth += 1
    try:
        assert phone._queue_unmatched_final_invite_response(response) is False
    finally:
        with phone._call_state_lock:
            phone._outbound_call_creation_depth -= 1


def test_rtp_client_nsd_is_event_backed_but_public_name_is_preserved():
    pyVoIP.refresh_supported_codecs()
    client = RTP.RTPClient(
        {0: RTP.PayloadType.PCMU},
        "127.0.0.1",
        10000,
        "127.0.0.1",
        10002,
        RTP.TransmitType.SENDRECV,
    )

    assert client.NSD is False
    client.NSD = True
    assert client.NSD is True
    client.NSD = False
    assert client.NSD is False


def test_pending_invite_response_helpers_survive_parallel_writes():
    phone = VoIPPhone(
        "127.0.0.1",
        5060,
        "100",
        "secret",
        myIP="127.0.0.1",
    )

    responses = {
        f"race-call-{index}@example.test": _invite_response(
            f"race-call-{index}@example.test"
        )
        for index in range(16)
    }

    threads = [
        threading.Thread(
            target=phone.sip.queue_pending_invite_response,
            args=(call_id, response),
        )
        for call_id, response in responses.items()
    ]

    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    for call_id, response in responses.items():
        assert phone.sip.pop_pending_invite_response(call_id) is response
        assert phone.sip.pop_pending_invite_response(call_id) is None


def test_unmatched_invite_response_is_not_queued_after_call_exists():
    phone = VoIPPhone(
        "127.0.0.1",
        5060,
        "100",
        "secret",
        myIP="127.0.0.1",
    )
    phone._send_ack = lambda request: None

    call_id = "already-created@example.test"
    response = _invite_response(call_id)
    phone.sip._set_last_invite_debug(call_id=call_id)

    with phone._call_state_lock:
        phone._outbound_call_creation_depth += 1
        phone.calls[call_id] = SimpleNamespace(
            state=CallState.DIALING,
            assignedPorts={},
        )
    try:
        assert phone._queue_unmatched_final_invite_response(response) is False
    finally:
        with phone._call_state_lock:
            phone._outbound_call_creation_depth -= 1
            phone.calls.pop(call_id, None)

    assert phone.sip.pop_pending_invite_response(call_id) is None


def test_voip_call_lazy_state_lock_supports_object_new_doubles():
    call = object.__new__(VoIPCall)
    client = object()
    call.RTPClients = [client]

    assert call._rtp_clients_snapshot() == [client]

    call._set_state(CallState.RINGING)

    assert call._get_state() == CallState.RINGING
    assert hasattr(call, "_state_lock")


def test_active_codecs_works_for_object_new_call_doubles():
    class FakeRTPClient:
        def selected_codec_info(self):
            return {"name": "PCMU", "payload_type": 0}

    call = object.__new__(VoIPCall)
    call.remote_sip_message = None
    call.RTPClients = [FakeRTPClient()]

    assert call.active_codecs() == [{"name": "PCMU", "payload_type": 0}]


def test_write_audio_uses_rtp_client_snapshot_when_list_mutates():
    call = object.__new__(VoIPCall)
    writes = []

    class MutatingClient:
        def __init__(self, name):
            self.name = name

        def write(self, data):
            writes.append((self.name, data))
            if self.name == "first":
                call.RTPClients.clear()

    call.RTPClients = [
        MutatingClient("first"),
        MutatingClient("second"),
    ]

    call.write_audio(b"\x80")

    assert writes == [
        ("first", b"\x80"),
        ("second", b"\x80"),
    ]


def test_stop_rtp_clients_uses_snapshot_when_list_mutates():
    call = object.__new__(VoIPCall)
    stopped = []

    class MutatingClient:
        def __init__(self, name):
            self.name = name

        def stop(self):
            stopped.append(self.name)
            if self.name == "first":
                call.RTPClients.clear()

    call.RTPClients = [
        MutatingClient("first"),
        MutatingClient("second"),
    ]

    call._stop_rtp_clients()

    assert stopped == ["first", "second"]


def test_cleanup_dead_calls_handles_legacy_call_doubles():
    class DeadThread:
        def is_alive(self):
            return False

    phone = VoIPPhone(
        "127.0.0.1",
        5060,
        "100",
        "secret",
        myIP="127.0.0.1",
    )

    answered_thread = DeadThread()
    ended_thread = DeadThread()
    phone.calls["answered"] = SimpleNamespace(
        state=CallState.ANSWERED,
        assignedPorts={10000: {}},
    )
    phone.calls["ended"] = SimpleNamespace(
        state=CallState.ENDED,
        assignedPorts={10002: {}},
    )
    phone.threads = [answered_thread, ended_thread]
    phone.threadLookup = {
        answered_thread: "answered",
        ended_thread: "ended",
    }

    phone._cleanup_dead_calls()

    assert "answered" in phone.calls
    assert "ended" not in phone.calls
    assert phone.threads == []
    assert phone.threadLookup == {}


def test_release_ports_handles_legacy_active_call_doubles():
    phone = VoIPPhone(
        "127.0.0.1",
        5060,
        "100",
        "secret",
        myIP="127.0.0.1",
    )
    phone.assignedPorts = [10000, 10001, 10002]
    phone.calls["active"] = SimpleNamespace(
        state=CallState.ANSWERED,
        assignedPorts={10001: {}},
    )

    phone.release_ports()

    assert phone.assignedPorts == [10001]


def test_list_subscriptions_returns_isolated_snapshots():
    phone = VoIPPhone(
        "127.0.0.1",
        5060,
        "100",
        "secret",
        myIP="127.0.0.1",
    )
    subscription = SIP.SIPSubscription(
        call_id="subscription-call@example.test",
        target="101",
        target_uri="sip:101@example.test",
        event="presence",
        accept=["application/pidf+xml"],
        local_tag="localtag",
    )

    with phone.sip._subscription_lock:
        phone.sip.subscriptions[subscription.call_id] = subscription

    first_snapshot = phone.sip.list_subscriptions()
    first_snapshot[0]["status"] = "mutated-outside-lock"

    second_snapshot = phone.sip.list_subscriptions()

    assert second_snapshot[0]["status"] == "pending"


def test_rtp_stop_before_start_is_idempotent():
    client = RTP.RTPClient(
        {0: RTP.PayloadType.PCMU},
        "127.0.0.1",
        10000,
        "127.0.0.1",
        10002,
        RTP.TransmitType.SENDRECV,
    )

    client.stop()
    client.stop()

    assert client.NSD is False


def test_rtp_send_without_started_socket_is_noop():
    client = RTP.RTPClient(
        {0: RTP.PayloadType.PCMU},
        "127.0.0.1",
        10000,
        "127.0.0.1",
        10002,
        RTP.TransmitType.SENDRECV,
    )

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        client._send_rtp_packet(
            0,
            b"\x80",
            marker=False,
            timestamp=0,
        )

    assert caught == []