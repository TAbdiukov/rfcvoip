"""AMR signaling tests do not require optional native codec libraries."""

from types import SimpleNamespace

import pytest

import rfcvoip
from rfcvoip import RTP, SIP
from rfcvoip.VoIP import CallState, VoIPCall
from rfcvoip.VoIP.VoIP import _amr_answer_matches_offer
from rfcvoip.codecs import codec_class
from rfcvoip.codecs.base import CodecAvailability
from tests.test_multi_connection_rtp import FakeRTPClient
from tests.test_voip_lifecycle_regressions import invite, phone
from tests.test_voip_second_audit import outgoing_call

RealRTPClient = RTP.RTPClient


class ConfiguredRTPClient(FakeRTPClient):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.codec_fmtp = kwargs.get("codec_fmtp", {})
        self.receive_assoc = kwargs.get("receive_assoc", self.assoc)
        self.receive_codec_fmtp = kwargs.get(
            "receive_codec_fmtp", self.codec_fmtp.copy()
        )
        self._receive_codec_adapters = {}

    def stop(self):
        pass


@pytest.fixture(autouse=True)
def enable_amr_without_native_libraries(monkeypatch):
    for codec in (RTP.PayloadType.AMR, RTP.PayloadType.AMR_WB):
        monkeypatch.setattr(
            codec_class(codec),
            "availability",
            classmethod(lambda cls: CodecAvailability(True, "test library")),
        )
    monkeypatch.setattr(
        rfcvoip,
        "RTPCompatibleCodecs",
        [
            RTP.PayloadType.PCMU,
            RTP.PayloadType.PCMA,
            RTP.PayloadType.AMR,
            RTP.PayloadType.AMR_WB,
            RTP.PayloadType.EVENT,
        ],
    )
    monkeypatch.setattr(RTP, "RTPClient", ConfiguredRTPClient)


def amr_message(
    codec,
    settings=None,
    *,
    payload=96,
    message=None,
    alternatives=(),
    bandwidth=None,
):
    message = invite() if message is None else message
    formats = [(payload, settings)] + list(alternatives)
    payloads = " ".join(str(pt) for pt, _settings in formats)
    body = (
        "v=0\r\no=remote 1 1 IN IP4 127.0.0.1\r\n"
        "s=amr\r\nc=IN IP4 127.0.0.1\r\nt=0 0\r\n"
        f"m=audio 40000 RTP/AVP {payloads}\r\n"
    )
    if bandwidth is not None:
        body += f"b=TIAS:{bandwidth}\r\n"
    for pt, fmtp in formats:
        body += f"a=rtpmap:{pt} {codec}/{codec.rate}\r\n"
        if fmtp is not None:
            body += f"a=fmtp:{pt} {fmtp}\r\n"
    body += "a=sendrecv\r\n"
    headers = [
        line
        for line in message.raw.split(b"\r\n\r\n", 1)[0].split(b"\r\n")
        if not line.lower().startswith(b"content-length:")
    ]
    headers.append(f"Content-Length: {len(body)}".encode("ascii"))
    return SIP.SIPMessage(
        b"\r\n".join(headers) + b"\r\n\r\n" + body.encode("ascii")
    )


def inbound_call(phone, codec, settings=None):
    request = amr_message(codec, settings)
    phone.sip.tagLibrary[request.headers["Call-ID"]] = "local"
    return VoIPCall(phone, CallState.RINGING, request, 123, phone.myIP)


@pytest.mark.parametrize(
    "codec", [RTP.PayloadType.AMR, RTP.PayloadType.AMR_WB]
)
def test_amr_names_rates_channels_and_dynamic_payloads(codec):
    assert (
        RTP.payload_type_from_name(str(codec).lower(), rate=codec.rate)
        is codec
    )
    assert RTP.is_audio_codec(codec)
    assert RTP.is_transmittable_audio_codec(codec)
    with pytest.raises(ValueError):
        RTP.payload_type_from_name(str(codec), rate=48000)
    with pytest.raises(ValueError):
        RTP.payload_type_from_name(str(codec), rate=codec.rate, channels=2)


def test_amr_offer_payloads_and_priority(phone):
    offer = phone._default_audio_offer()
    assert offer[118] is RTP.PayloadType.AMR
    assert offer[119] is RTP.PayloadType.AMR_WB
    values = list(offer.values())
    assert values.index(RTP.PayloadType.AMR_WB) < values.index(
        RTP.PayloadType.AMR
    )
    assert values.index(RTP.PayloadType.AMR) < values.index(
        RTP.PayloadType.PCMU
    )


@pytest.mark.parametrize(
    "codec", [RTP.PayloadType.AMR, RTP.PayloadType.AMR_WB]
)
@pytest.mark.parametrize(
    "settings,answer",
    [
        (None, "octet-align=0"),
        ("octet-align=0", "octet-align=0"),
        (
            "octet-align=1;mode-set=2,0;mode-change-period=2",
            "octet-align=1;mode-set=2,0;mode-change-period=2",
        ),
    ],
)
def test_inbound_amr_configuration_reaches_rtp_and_answer(
    phone, codec, settings, answer
):
    call = inbound_call(phone, codec, settings)
    client = call.RTPClients[0]
    assert client.codec_fmtp[96] == ([] if settings is None else [settings])
    call.answer()
    assert f"a=rtpmap:96 {codec}/{codec.rate}\r\n" in phone.responses[-1]
    assert (
        f"a=fmtp:96 {answer};mode-change-capability=2\r\n"
        in phone.responses[-1]
    )


@pytest.mark.parametrize(
    "settings",
    [
        "octet-align=1;crc=1",
        "octet-align=1;robust-sorting=1",
        "octet-align=1;interleaving=0",
        "mode-set=9",
        "octet-align=2",
    ],
)
def test_unsupported_amr_configuration_is_not_negotiated(phone, settings):
    with pytest.raises(RTP.RTPParseError):
        inbound_call(phone, RTP.PayloadType.AMR, settings)


@pytest.mark.parametrize(
    "initial,next_settings,status",
    [
        (None, "octet-align=0", 200),
        ("octet-align=1;mode-set=0,2", "mode-set=2,0;octet-align=1", 200),
        ("octet-align=1", "octet-align=0", 488),
        ("octet-align=1;mode-set=0,2", "octet-align=1;mode-set=0", 488),
        ("octet-align=1;mode-change-period=2", "octet-align=1", 488),
    ],
)
def test_reinvite_preserves_retained_amr_configuration(
    phone, initial, next_settings, status
):
    call = inbound_call(phone, RTP.PayloadType.AMR_WB, initial)
    call.answer()
    client = call.RTPClients[0]
    call.renegotiate(amr_message(RTP.PayloadType.AMR_WB, next_settings))
    assert phone.responses[-1].startswith(f"SIP/2.0 {status} ")
    assert call.RTPClients == [client]
    assert client.codec_fmtp[96] == ([] if initial is None else [initial])


@pytest.mark.parametrize(
    "codec,offered_payload",
    [
        (RTP.PayloadType.AMR, 118),
        (RTP.PayloadType.AMR_WB, 119),
    ],
)
def test_outbound_amr_configuration_reaches_remapped_receive_payload(
    phone, monkeypatch, codec, offered_payload
):
    call, response = outgoing_call(phone, monkeypatch)
    response = amr_message(codec, "octet-align=1", message=response)
    call.answered(response)
    client = call.RTPClients[0]
    assert client.assoc == {96: codec}
    assert client.receive_assoc == {offered_payload: codec}
    assert client.codec_fmtp == {96: ["octet-align=1"]}
    assert client.receive_codec_fmtp == {
        offered_payload: ["octet-align=1;mode-change-capability=2"],
    }


def test_outbound_answer_cannot_change_offered_amr_packetization(
    phone, monkeypatch
):
    call, response = outgoing_call(phone, monkeypatch)
    response = amr_message(RTP.PayloadType.AMR_WB, None, message=response)
    with pytest.raises(RTP.RTPParseError, match="No RTP clients"):
        call.answered(response)
    assert call.RTPClients == []


@pytest.mark.parametrize(
    "codec,offered_payload",
    [(RTP.PayloadType.AMR, 118), (RTP.PayloadType.AMR_WB, 119)],
)
def test_answer_added_mode_set_binds_receive_but_period_is_directional(
    phone, monkeypatch, codec, offered_payload
):
    call, response = outgoing_call(phone, monkeypatch)
    settings = "octet-align=1;mode-set=0,2;mode-change-period=2;mode-change-neighbor=1"
    call.answered(amr_message(codec, settings, message=response))
    client = call.RTPClients[0]
    assert client.codec_fmtp == {96: [settings]}
    receive = codec_class(codec).fmtp_parameters(
        client.receive_codec_fmtp[offered_payload]
    )
    assert receive["mode-set"] == (0, 2)
    assert receive["mode-change-period"] == 1
    assert receive["mode-change-neighbor"] is False


def test_retained_amr_payload_cannot_match_another_offered_format(
    phone, monkeypatch
):
    codec = RTP.PayloadType.AMR
    call, response = outgoing_call(phone, monkeypatch)
    call.request = amr_message(
        codec,
        "octet-align=1",
        payload=118,
        message=call.request,
        alternatives=[(120, "octet-align=0")],
    )
    response = amr_message(
        codec, "octet-align=0", payload=118, message=response
    )
    with pytest.raises(RTP.RTPParseError, match="No RTP clients"):
        call.answered(response)


def test_remapped_amr_answer_retains_only_matching_receive_format(
    phone, monkeypatch
):
    codec = RTP.PayloadType.AMR
    call, response = outgoing_call(phone, monkeypatch)
    call.request = amr_message(
        codec,
        "octet-align=1",
        payload=118,
        message=call.request,
        alternatives=[(120, "octet-align=0")],
    )
    call.answered(amr_message(codec, "octet-align=0", message=response))
    client = call.RTPClients[0]
    assert client.receive_assoc == {120: codec}
    assert client.receive_codec_fmtp == {120: ["octet-align=0"]}


def test_multiple_retained_amr_formats_preserve_each_mode_set(
    phone, monkeypatch
):
    codec = RTP.PayloadType.AMR_WB
    first = "octet-align=1;mode-set=0,2"
    second = "octet-align=1;mode-set=1,3"
    call, response = outgoing_call(phone, monkeypatch)
    call.request = amr_message(
        codec,
        first,
        payload=119,
        message=call.request,
        alternatives=[(120, second)],
    )
    call.answered(
        amr_message(
            codec,
            first,
            payload=119,
            message=response,
            alternatives=[(120, second)],
        )
    )
    client = call.RTPClients[0]
    assert client.receive_codec_fmtp == {119: [first], 120: [second]}


@pytest.mark.parametrize(
    "settings",
    [
        "octet-align=1;mode-set=0",
        "octet-align=1",
    ],
)
def test_answer_cannot_change_an_explicit_offered_mode_set(
    phone, monkeypatch, settings
):
    codec = RTP.PayloadType.AMR
    call, response = outgoing_call(phone, monkeypatch)
    call.request = amr_message(
        codec,
        "octet-align=1;mode-set=0,2",
        payload=118,
        message=call.request,
    )
    with pytest.raises(RTP.RTPParseError, match="No RTP clients"):
        call.answered(amr_message(codec, settings, message=response))


@pytest.mark.parametrize(
    "offered,accepted",
    [
        ("octet-align=1", False),
        ("octet-align=1;mode-change-capability=1", False),
        ("octet-align=1;mode-change-period=2", True),
        ("octet-align=1;mode-change-capability=2", True),
    ],
)
def test_answer_period_two_requires_advertised_capability(
    phone, monkeypatch, offered, accepted
):
    codec = RTP.PayloadType.AMR_WB
    call, response = outgoing_call(phone, monkeypatch)
    call.request = amr_message(
        codec, offered, payload=119, message=call.request
    )
    response = amr_message(
        codec, "octet-align=1;mode-change-period=2", message=response
    )
    if accepted:
        call.answered(response)
        assert call.RTPClients
    else:
        with pytest.raises(RTP.RTPParseError, match="No RTP clients"):
            call.answered(response)


@pytest.mark.parametrize(
    "capability,accepted",
    [
        (None, True),
        ("1", False),
        ("2", True),
    ],
)
def test_answer_cannot_explicitly_decline_offered_period_two(
    capability, accepted
):
    codec = RTP.PayloadType.AMR_WB
    offered = amr_message(codec, "octet-align=1;mode-change-period=2").body[
        "m"
    ][0]
    answer = "octet-align=1;mode-change-period=1"
    if capability is not None:
        answer += f";mode-change-capability={capability}"
    assert _amr_answer_matches_offer(codec, [answer], offered, 96) is accepted


def test_amr_answer_echoes_offered_disabled_features(phone):
    settings = "octet-align=1;crc=0;robust-sorting=0;channels=1"
    call = inbound_call(phone, RTP.PayloadType.AMR, settings)
    call.answer()
    assert (
        f"a=fmtp:96 {settings};mode-change-capability=2\r\n"
        in phone.responses[-1]
    )


@pytest.mark.parametrize(
    "codec,limit,accepted",
    [
        (RTP.PayloadType.AMR, 6000, True),
        (RTP.PayloadType.AMR, 4000, False),
        (RTP.PayloadType.AMR_WB, 10000, True),
        (RTP.PayloadType.AMR_WB, 6000, False),
    ],
)
def test_amr_low_mode_bandwidth_is_used_for_offer_and_media_setup(
    phone, codec, limit, accepted
):
    request = amr_message(codec, "octet-align=1;mode-set=0", bandwidth=limit)
    assert phone._has_compatible_audio_offer(request) is accepted
    if accepted:
        phone.sip.tagLibrary[request.headers["Call-ID"]] = "local"
        call = VoIPCall(phone, CallState.RINGING, request, 123, phone.myIP)
        assert call.RTPClients[0].assoc == {96: codec}
        call.answer()
        call.renegotiate(request)
        assert phone.responses[-1].startswith("SIP/2.0 200 ")
    else:
        with pytest.raises(RTP.RTPParseError):
            VoIPCall(phone, CallState.RINGING, request, 123, phone.myIP)


@pytest.mark.parametrize(
    "codec,limit",
    [
        (RTP.PayloadType.AMR, 6000),
        (RTP.PayloadType.AMR_WB, 10000),
    ],
)
def test_outbound_low_mode_bandwidth_is_admitted(
    phone, monkeypatch, codec, limit
):
    call, response = outgoing_call(phone, monkeypatch)
    call.answered(
        amr_message(
            codec,
            "octet-align=1;mode-set=0",
            message=response,
            bandwidth=limit,
        )
    )
    assert call.RTPClients[0].assoc == {96: codec}


@pytest.mark.parametrize(
    "codec", [RTP.PayloadType.AMR, RTP.PayloadType.AMR_WB]
)
def test_amr_alternative_payload_formats_use_independent_adapters(
    monkeypatch, codec
):
    class Adapter:
        rate = codec.rate

        def configure_fmtp(self, settings):
            self.settings = settings

        def decode(self, payload):
            expected = b"OA" if "octet-align=1" in self.settings else b"BE"
            assert payload[1:] == expected
            return bytes(codec.rate // 50)

        def output_offset(self, timestamp):
            return timestamp

        def request_mode(self, mode):
            pass

    monkeypatch.setattr(
        "rfcvoip.codecs.create_codec", lambda *args, **kwargs: Adapter()
    )
    client = RealRTPClient(
        {96: codec, 97: codec},
        "127.0.0.1",
        30000,
        "127.0.0.1",
        40000,
        "sendrecv",
        codec_fmtp={96: ["octet-align=0"], 97: ["octet-align=1"]},
    )

    def packet(sequence, timestamp, pt, body, ssrc=1):
        return SimpleNamespace(
            SSRC=ssrc,
            sequence=sequence,
            timestamp=timestamp,
            payload_type=codec,
            payload_number=pt,
            payload=b"\xf0" + body,
        )

    client.parse_audio(packet(1, 0, 96, b"BE"))
    client.parse_audio(packet(2, 160, 97, b"OA"))
    client.parse_audio(packet(3, 320, 96, b"BE"))
    assert len(client._receive_codec_adapters) == 2
    assert client._codec_adapter(codec) is client._codec_adapter(codec, 96)
    assert client._codec_adapter(codec, 97) is not client._codec_adapter(codec)
    original = client._codec_adapter(codec, 97, receive=True)
    client.parse_audio(packet(1, 0, 97, b"OA", ssrc=2))
    assert list(client._receive_codec_adapters) == [(codec, 97)]
    assert client._codec_adapter(codec, 97, receive=True) is not original


def test_send_and_receive_payload_id_collision_keeps_directional_fmtp(
    monkeypatch,
):
    class Adapter:
        def configure_fmtp(self, settings):
            self.settings = settings

    monkeypatch.setattr(
        "rfcvoip.codecs.create_codec", lambda *args, **kwargs: Adapter()
    )
    codec = RTP.PayloadType.AMR_WB
    client = RealRTPClient(
        {96: codec},
        "127.0.0.1",
        30000,
        "127.0.0.1",
        40000,
        "sendrecv",
        receive_assoc={96: RTP.PayloadType.AMR},
        codec_fmtp={96: ["octet-align=1"]},
        receive_codec_fmtp={96: ["octet-align=0"]},
    )
    assert client._codec_adapter(codec, 96).settings == ["octet-align=1"]
    assert client._codec_adapter(
        RTP.PayloadType.AMR, 96, receive=True
    ).settings == ["octet-align=0"]


@pytest.mark.parametrize(
    "codec", [RTP.PayloadType.AMR, RTP.PayloadType.AMR_WB]
)
def test_cmr_only_updates_encoder_after_valid_forward_audio(
    monkeypatch, codec
):
    client = RealRTPClient(
        {96: codec}, "127.0.0.1", 30000, "127.0.0.1", 40000, "sendrecv"
    )
    requests = []

    def decode(payload):
        if payload.endswith(b"bad"):
            raise ValueError("malformed AMR")
        return bytes(codec.rate // 50)

    receiver = SimpleNamespace(
        decode=decode, rate=codec.rate, output_offset=lambda value: value
    )
    client._receive_codec_adapters[codec, 96] = receiver
    client._codec_adapters[codec, 96] = SimpleNamespace(
        request_mode=requests.append
    )
    monkeypatch.setattr(
        client, "_new_codec_adapter", lambda *args, **kwargs: receiver
    )

    def packet(sequence, timestamp, mode, body=b"good"):
        return SimpleNamespace(
            SSRC=1,
            sequence=sequence,
            timestamp=timestamp,
            payload_type=codec,
            payload_number=96,
            payload=bytes([mode << 4]) + body,
        )

    client.parse_audio(packet(1, 100, 2))
    client.parse_audio(packet(1, 100, 3))  # duplicate sequence
    client.parse_audio(packet(2, 100, 4))  # same timestamp
    client.parse_audio(packet(3, 80, 5))  # reordered frame
    assert requests == [2]
    with pytest.raises(RTP.RTPParseError, match="malformed AMR"):
        client.parse_audio(packet(4, 260, 6, b"bad"))
    assert requests == [2]
    client.parse_audio(packet(4, 260, 7))
    assert requests == [2, 7]
    empty = packet(5, 420, 0)
    empty.payload = b""
    client.parse_audio(empty)
    assert requests == [2, 7]


def test_cmr_does_not_control_another_codec_encoder():
    codec = RTP.PayloadType.AMR
    client = RealRTPClient(
        {96: codec, 97: RTP.PayloadType.AMR_WB},
        "127.0.0.1",
        30000,
        "127.0.0.1",
        40000,
        "sendrecv",
    )
    requests = []
    client._receive_codec_adapters[codec, 96] = SimpleNamespace(
        decode=lambda payload: bytes(160),
        rate=8000,
        output_offset=lambda value: value,
    )
    client._codec_adapters[codec, 96] = SimpleNamespace(
        request_mode=requests.append
    )
    client.parse_audio(
        SimpleNamespace(
            SSRC=1,
            sequence=1,
            timestamp=0,
            payload_number=96,
            payload_type=codec,
            payload=b"\x20good",
        )
    )
    assert client.preference is RTP.PayloadType.AMR_WB
    assert requests == []
