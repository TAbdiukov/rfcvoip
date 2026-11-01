import pytest

from rfcvoip import RTP, SIP
from rfcvoip.VoIP import CallState
from rfcvoip.audio_format import s16le_to_public_pcm
from rfcvoip.codecs.opus import OpusCodec
from tests import test_opus_peer_stereo as opus_helpers
from tests.test_voip_lifecycle_regressions import invite, phone
from tests.test_voip_second_audit import outgoing_call


@pytest.fixture(autouse=True)
def require_native_opus():
    if not OpusCodec.availability().available:
        pytest.skip("optional libopus is not installed")


def opus_sdp_message(message, stereo):
    port = message.body["m"][0]["port"]
    body = (
        "v=0\r\n"
        "o=remote 1 1 IN IP4 127.0.0.1\r\n"
        "s=opus negotiation\r\n"
        "c=IN IP4 127.0.0.1\r\n"
        "t=0 0\r\n"
        f"m=audio {port} RTP/AVP 111\r\n"
        "a=rtpmap:111 opus/48000/2\r\n"
        + (f"a=fmtp:111 stereo={stereo}\r\n" if stereo is not None else "")
        + "a=sendrecv\r\n"
    ).encode("utf8")
    headers = [
        line
        for line in message.raw.split(b"\r\n\r\n", 1)[0].split(b"\r\n")
        if not line.lower().startswith(b"content-length:")
    ]
    headers.append(f"Content-Length: {len(body)}".encode("ascii"))
    return SIP.SIPMessage(b"\r\n".join(headers) + b"\r\n\r\n" + body)


def inbound_opus_call(phone, stereo):
    request = opus_sdp_message(invite(), stereo)
    phone.sip.tagLibrary[request.headers["Call-ID"]] = "local"
    phone.session_ids.append(123)
    phone._create_Call(request, 123)
    call = phone._get_call(request.headers["Call-ID"])
    call.answer()
    return call


def assert_wire_channels(client, expected_channels):
    assert client.preference is RTP.PayloadType.OPUS
    assert client.audio_sample_rate == 48000
    assert client.audio_channels == 2
    source = s16le_to_public_pcm(
        opus_helpers.stereo_pcm(), client.audio_bit_depth
    )
    packet = client.encode_packet(source)
    adapter = client._codec_adapter(client.preference)
    assert opus_helpers.packet_channels(adapter, packet) == expected_channels


@pytest.mark.parametrize("stereo", [None, 0, 1])
def test_inbound_sdp_stereo_preference_reaches_native_encoder(phone, stereo):
    call = inbound_opus_call(phone, stereo)

    assert call.state is CallState.ANSWERED
    assert_wire_channels(call.RTPClients[0], 2 if stereo == 1 else 1)


@pytest.mark.parametrize("stereo", [None, 0, 1])
def test_outbound_sdp_stereo_preference_reaches_native_encoder(
    phone, monkeypatch, stereo
):
    call, response = outgoing_call(phone, monkeypatch)
    response = opus_sdp_message(response, stereo)
    monkeypatch.setattr(phone, "_send_ack", lambda _response: None)

    phone.callback(response)

    assert call.state is CallState.ANSWERED
    assert_wire_channels(call.RTPClients[0], 2 if stereo == 1 else 1)


@pytest.mark.parametrize(
    ("initial_stereo", "next_stereo", "response_code"),
    [
        (None, None, 200),
        (None, 0, 200),
        (0, None, 200),
        (1, 1, 200),
        (0, 1, 488),
        (1, 0, 488),
        (1, None, 488),
    ],
)
def test_retained_opus_adapter_rejects_changed_effective_stereo_preference(
    phone, initial_stereo, next_stereo, response_code
):
    call = inbound_opus_call(phone, initial_stereo)
    client = call.RTPClients[0]
    expected_channels = 2 if initial_stereo == 1 else 1
    assert_wire_channels(client, expected_channels)
    phone.responses.clear()

    call.renegotiate(opus_sdp_message(invite(), next_stereo))

    assert phone.responses[-1].startswith(f"SIP/2.0 {response_code} ")
    assert call.state is CallState.ANSWERED
    assert call.RTPClients == [client]
    assert_wire_channels(client, expected_channels)
