import pytest

from rfcvoip.RTP import PayloadType
import rfcvoip.codecs as codecs


@pytest.fixture(autouse=True)
def reset_codec_priority_overrides():
    codecs.reset_codec_priorities()
    yield
    codecs.reset_codec_priorities()


def test_known_payload_types_exposes_registered_codecs_and_event_marker():
    registered = codecs.known_payload_types(include_events=False)
    advertised = codecs.known_payload_types(include_events=True)

    assert PayloadType.PCMU in registered
    assert PayloadType.PCMA in registered
    assert PayloadType.EVENT not in registered
    assert len(registered) == len(set(registered))

    assert advertised[:-1] == registered
    assert advertised[-1] == PayloadType.EVENT


def test_enabled_payload_types_are_available_and_keep_event_separate():
    enabled = codecs.enabled_payload_types(include_events=False)

    assert PayloadType.PCMU in enabled
    assert PayloadType.PCMA in enabled
    assert PayloadType.EVENT not in enabled
    assert all(
        codecs.codec_availability(payload_type)["available"]
        for payload_type in enabled
    )

    enabled_with_event = codecs.enabled_payload_types(include_events=True)

    assert enabled_with_event[:-1] == enabled
    assert enabled_with_event[-1] == PayloadType.EVENT


def test_availability_report_matches_the_registry_shape():
    report = codecs.availability_report(include_events=True)

    assert len(report) == len(codecs.known_payload_types(include_events=True))
    assert any(
        item["name"] == "PCMU" and item["available"] is True
        for item in report
    )
    assert any(
        item["name"] == "telephone-event"
        and item["payload_kind"] == "event"
        for item in report
    )


def test_event_payload_uses_special_non_audio_metadata():
    availability = codecs.codec_availability(PayloadType.EVENT)

    assert availability["available"] is True
    assert availability["name"] == "telephone-event"
    assert availability["payload_kind"] == "event"
    assert availability["can_transmit_audio"] is False
    assert availability["default_payload_type"] == 101

    assert codecs.codec_can_transmit_audio(PayloadType.EVENT) is False
    assert codecs.codec_priority_score(PayloadType.EVENT) == -1000
    assert codecs.default_payload_type(PayloadType.EVENT) == 101
    assert codecs.rtpmap_for_codec(PayloadType.EVENT, 101) == (
        "101 telephone-event/8000"
    )
    assert codecs.fmtp_for_codec(PayloadType.EVENT) == ["0-15"]
    assert codecs.codec_fmtp_supported(PayloadType.EVENT, ["0-15"]) is True


def test_builtin_pcmu_codec_can_be_resolved_created_and_used():
    pcmu_class = codecs.codec_class(PayloadType.PCMU)

    assert pcmu_class is not None
    assert codecs.codec_class(0) is pcmu_class
    assert codecs.codec_class("PCMU") is pcmu_class
    assert codecs.default_payload_type(PayloadType.PCMU) == 0
    assert codecs.rtpmap_for_codec(PayloadType.PCMU, 0) == "0 PCMU/8000"
    assert codecs.fmtp_for_codec(PayloadType.PCMU) == []

    availability = codecs.codec_availability("PCMU")
    assert availability["available"] is True
    assert availability["name"] == "PCMU"
    assert availability["payload_kind"] == "audio"
    assert availability["can_transmit_audio"] is True

    adapter = codecs.create_codec(
        "PCMU",
        source_sample_rate=8000,
        source_sample_width=2,
        source_bit_depth=16,
        source_channels=1,
    )

    assert adapter is not None
    assert adapter.name == "PCMU"
    assert adapter.source_sample_rate == 8000
    assert adapter.source_sample_width == 2
    assert adapter.source_bit_depth == 16
    assert adapter.source_channels == 1

    pcm16_frame = b"\x00\x00" * 160
    encoded = adapter.encode(pcm16_frame)
    decoded = adapter.decode(encoded)

    assert len(encoded) == 160
    assert len(decoded) == len(pcm16_frame)


def test_priority_overrides_affect_scores_and_sorting_not_payload_numbers():
    payloads = [PayloadType.PCMU, PayloadType.PCMA]

    assert codecs.codec_priority_score(PayloadType.PCMU) > (
        codecs.codec_priority_score(PayloadType.PCMA)
    )
    assert codecs.sorted_payload_types(payloads) == payloads
    assert codecs.default_payload_type(PayloadType.PCMU) == 0
    assert codecs.default_payload_type(PayloadType.PCMA) == 8

    codecs.set_codec_priority(PayloadType.PCMA, 10_000)

    assert codecs.codec_priority_score(PayloadType.PCMA) == 10_000
    assert codecs.sorted_payload_types(payloads) == [
        PayloadType.PCMA,
        PayloadType.PCMU,
    ]
    assert codecs.default_payload_type(PayloadType.PCMA) == 8


def test_unregistered_static_payload_reports_unavailable_but_keeps_default():
    assert codecs.codec_class(PayloadType.GSM) is None

    availability = codecs.codec_availability(PayloadType.GSM)

    assert availability["available"] is False
    assert availability["reason"] == "No codec implementation registered"
    assert availability["payload_kind"] == "unknown"
    assert availability["can_transmit_audio"] is False

    assert codecs.default_payload_type(PayloadType.GSM) == 3
    assert codecs.codec_can_transmit_audio(PayloadType.GSM) is False
    assert codecs.codec_fmtp_supported(PayloadType.GSM, []) is False
    assert codecs.rtpmap_for_codec(PayloadType.GSM, 3) == ""
    assert codecs.fmtp_for_codec(PayloadType.GSM) == []
    assert codecs.create_codec(PayloadType.GSM) is None


def test_unknown_payload_name_is_rejected():
    with pytest.raises(ValueError, match="not found"):
        codecs.codec_class("not-a-codec")

    with pytest.raises(ValueError, match="not found"):
        codecs.create_codec("not-a-codec")