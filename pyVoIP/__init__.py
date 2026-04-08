__all__ = ["SIP", "RTP", "VoIP"]

from datetime import datetime, timezone

version_info = *(1,7,"3+RFC")

__version__ = "1.7.3+RFC"

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
RTPCompatibleCodecs = [PayloadType.PCMU, PayloadType.PCMA, PayloadType.EVENT]
