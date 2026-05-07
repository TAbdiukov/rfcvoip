__all__ = [
    "SIP", "RTP", "VoIP",
    "codec_availability",
    "codec_priorities",
    "codec_priority_score",
    "codec_support_report",
    "refresh_supported_codecs",
    "reset_codec_priorities",
    "set_codec_priority",
    "sip_supported_codecs",
    "supported_codecs",
]

from datetime import datetime, timezone

from pyVoIP._version import __version__, version_info

DEBUG = False

"""
The higher this variable is, the more often RTP packets are sent.
This should only ever need to be 0.0. However, when testing on Windows,
there has sometimes been jittering, setting this to 0.75 fixed this in testing.
"""
TRANSMIT_DELAY_REDUCTION = 0.0

"""
If registration fails this many times, VoIPPhone's status will be set to FAILED
and the phone will stop.
"""
REGISTER_FAILURE_THRESHOLD = 3


def debug(s, e=None):
    stamp = datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")
    if DEBUG:
        print(f"[pyVoIP {stamp}] {s}")
    elif e is not None:
        print(f"[pyVoIP {stamp}] {e}")


# noqa because import will fail if debug is not defined
from pyVoIP.RTP import PayloadType  # noqa: E402


def _build_rtp_compatible_codecs():
    from pyVoIP.codecs import enabled_payload_types

    return enabled_payload_types(include_events=True)


SIPCompatibleMethods = [
    "INVITE",
    "ACK",
    "BYE",
    "CANCEL",
    "OPTIONS",
    "SUBSCRIBE",
    "NOTIFY",
]
SIPCompatibleVersions = ["SIP/2.0"]

RTPCompatibleVersions = [2]
RTPCompatibleCodecs = _build_rtp_compatible_codecs()


def refresh_supported_codecs():
    """Refresh optional codec availability and return enabled payload types."""
    global RTPCompatibleCodecs

    from pyVoIP.codecs import enabled_payload_types, refresh_codec_availability

    refresh_codec_availability()
    RTPCompatibleCodecs = enabled_payload_types(include_events=True)
    return list(RTPCompatibleCodecs)


def set_codec_priority(payload_type, score):
    """Override a codec priority score and refresh the enabled codec order."""
    global RTPCompatibleCodecs

    from pyVoIP.codecs import enabled_payload_types
    from pyVoIP.codecs import set_codec_priority as _set_codec_priority

    _set_codec_priority(payload_type, score)
    RTPCompatibleCodecs = enabled_payload_types(include_events=True)
    return list(RTPCompatibleCodecs)


def reset_codec_priorities():
    """Reset codec priority overrides and refresh the enabled codec order."""
    global RTPCompatibleCodecs

    from pyVoIP.codecs import enabled_payload_types, reset_codec_priorities as _reset

    _reset()
    RTPCompatibleCodecs = enabled_payload_types(include_events=True)
    return list(RTPCompatibleCodecs)


def codec_priority_score(payload_type):
    """Return the current priority score for one payload type."""
    from pyVoIP.codecs import codec_priority_score as _codec_priority_score

    return _codec_priority_score(payload_type)


def codec_priorities(include_events=True):
    """Return current codec priority scores keyed by codec name."""
    from pyVoIP.codecs import codec_priorities as _codec_priorities

    return {
        str(codec): score
        for codec, score in _codec_priorities(
            include_events=include_events
        ).items()
    }


def codec_availability(refresh=False):
    """Return all known codec availability details, including unavailable ones."""
    from pyVoIP.codecs import availability_report

    return availability_report(refresh=refresh)


def supported_codecs(include_unavailable=False):
    """Return codecs supported by this PyVoIP build/configuration."""
    from pyVoIP.RTP import supported_codecs as _supported_codecs

    return _supported_codecs(include_unavailable=include_unavailable)


def sip_supported_codecs(message, media_type="audio"):
    """Return codecs advertised by a parsed SIP message's SDP body."""
    from pyVoIP.SIP import sip_supported_codecs as _sip_supported_codecs

    return _sip_supported_codecs(message, media_type=media_type)


def codec_support_report(message, media_type="audio"):
    """Compare a SIP message's SDP codecs against PyVoIP support."""
    from pyVoIP.SIP import codec_support_report as _codec_support_report

    return _codec_support_report(message, media_type=media_type)
