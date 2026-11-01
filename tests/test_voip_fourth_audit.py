import pytest

from rfcvoip import RTP
from rfcvoip.VoIP import CallState, InvalidStateError
from tests.test_voip_hilevel import ImmediateThread
from tests.test_voip_lifecycle_regressions import invite, phone, ringing_call
from tests.test_voip_third_audit import reinvite


def immediate_calls(phone, monkeypatch):
    seen = []
    phone.callCallback = seen.append
    monkeypatch.setattr("rfcvoip.VoIP.VoIP.Thread", ImmediateThread)
    monkeypatch.setattr(
        phone, "_run_call_callback", lambda callback, call: callback(call)
    )
    return seen


@pytest.mark.parametrize("ending", ["deny", "cancel", "bye"])
def test_ended_invite_retransmission_does_not_create_another_call(
    phone,
    monkeypatch,
    ending,
):
    seen = immediate_calls(phone, monkeypatch)
    phone.callback(invite())
    call = seen[0]
    if ending == "deny":
        call.deny()
    elif ending == "cancel":
        phone._callback_MSG_Cancel(invite())
    else:
        call.answer()
        call.bye()
    final_response = phone.responses[-1]
    assert call.state is CallState.ENDED
    assert phone.calls == {}

    phone.callback(invite())

    assert seen == [call]
    assert phone.calls == {}
    assert phone.assignedPorts == []
    assert phone.responses[-1] == final_response


def test_busy_invite_retransmission_keeps_original_final_response(phone):
    phone.callCallback = None
    phone.callback(invite())
    first_response = phone.responses[-1]

    phone.callback(invite())

    assert phone.responses[-1] == first_response
    assert phone.calls == {}


def test_failed_deny_send_still_prevents_duplicate_frontend_call(
    phone,
    monkeypatch,
):
    seen = immediate_calls(phone, monkeypatch)
    phone.callback(invite())
    call = seen[0]
    send = phone.sip.send_response

    def fail_send(*args):
        raise OSError("response send failed")

    monkeypatch.setattr(phone.sip, "send_response", fail_send)
    with pytest.raises(OSError, match="response send failed"):
        call.deny()
    monkeypatch.setattr(phone.sip, "send_response", send)

    phone.callback(invite())

    assert seen == [call]
    assert phone.responses[-1].startswith("SIP/2.0 486 ")
    assert phone.calls == {}


def test_committed_deny_cannot_race_into_sending_an_answer(phone, monkeypatch):
    call = ringing_call(phone)
    generate_busy = phone.sip.gen_busy

    def answer_while_generating_busy(request):
        with pytest.raises(InvalidStateError):
            call.answer()
        return generate_busy(request)

    monkeypatch.setattr(phone.sip, "gen_busy", answer_while_generating_busy)

    call.deny()

    assert call.state is CallState.ENDED
    assert phone.calls == {}
    assert phone.responses[-1].startswith("SIP/2.0 486 ")
    assert not any(
        response.startswith("SIP/2.0 200 ") for response in phone.responses
    )


def test_completed_invite_cache_expires(phone, monkeypatch):
    now = [100.0]
    monkeypatch.setattr("rfcvoip.VoIP.VoIP.time.monotonic", lambda: now[0])
    seen = immediate_calls(phone, monkeypatch)
    phone.callback(invite())
    seen[0].deny()
    phone.callback(invite())
    assert len(seen) == 1

    now[0] += 33.0
    phone.callback(invite())

    assert len(seen) == 2
    seen[1].deny()


def test_completed_invite_response_cache_has_a_finite_entry_limit(phone):
    phone.callCallback = None
    first = None
    for index in range(300):
        request = invite()
        request.headers["Call-ID"] = f"cache-call-{index}"
        if first is None:
            first = request
        phone.callback(request)

    assert len(phone._invite_response_cache) == 256
    assert phone._cached_invite_response(first) is None
    assert phone._cached_invite_response(request).startswith("SIP/2.0 486 ")


def test_failed_reinvite_rejection_advances_remote_sequence(
    phone, monkeypatch
):
    call = ringing_call(phone)
    call.answer()
    bad = reinvite(call)
    bad.body["m"][0]["methods"] = ["8"]
    send = phone.sip.send_response

    def fail_send(*args):
        raise OSError("response send failed")

    monkeypatch.setattr(phone.sip, "send_response", fail_send)
    with pytest.raises(OSError, match="response send failed"):
        phone.callback(bad)
    monkeypatch.setattr(phone.sip, "send_response", send)

    phone.callback(reinvite(call, branch="z9hG4bKanother", port=43000))

    assert phone.responses[-1].startswith("SIP/2.0 500 ")
    assert call.RTPClients[0].outPort == 40000


@pytest.mark.parametrize("bit_depth", [8, "best", 16])
def test_multiple_codecs_share_one_public_audio_format(phone, bit_depth):
    if not RTP.is_transmittable_audio_codec(RTP.PayloadType.OPUS):
        pytest.skip("optional libopus is not installed")
    phone.audio_bit_depth = bit_depth
    phone.audio_sample_width = 2 if bit_depth == 16 else 1
    request = invite(count=2)
    second = request.body["m"][1]
    second["methods"] = ["111"]
    second["attributes"] = {
        "111": {
            "rtpmap": {
                "name": "opus",
                "frequency": "48000",
                "encoding": "2",
            }
        }
    }
    phone.sip.tagLibrary[request.headers["Call-ID"]] = "local"
    phone._create_Call(request, 123)
    call = phone._get_call(request.headers["Call-ID"])
    assert [client.preference for client in call.RTPClients] == [
        RTP.PayloadType.PCMU,
        RTP.PayloadType.OPUS,
    ]
    call.answer()
    fmt = call.public_audio_format()

    assert all(
        client.public_audio_format() == fmt for client in call.RTPClients
    )
    assert call.audio_frame_size() == fmt.frame_size
    frame = (b"\x00" if fmt.bit_depth != 8 else b"\x80") * fmt.frame_size
    assert call.read_audio(blocking=False) == frame
    call.write_audio(frame)
    assert all(
        client.pmout.available() == len(frame) for client in call.RTPClients
    )
