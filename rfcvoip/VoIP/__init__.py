from rfcvoip.VoIP import status

PhoneStatus = status.PhoneStatus

__all__ = [
    "InvalidRangeError",
    "InvalidStateError",
    "NoPortsAvailableError",
    "CallState",
    "PhoneStatus",
    "VoIPCall",
    "VoIPPhone",
]


def __getattr__(name):
    if name in {
        "InvalidRangeError",
        "InvalidStateError",
        "NoPortsAvailableError",
        "CallState",
        "VoIPCall",
        "VoIPPhone",
    }:
        from rfcvoip.VoIP.VoIP import (
            CallState,
            InvalidRangeError,
            InvalidStateError,
            NoPortsAvailableError,
            VoIPCall,
            VoIPPhone,
        )

        mapping = {
            "InvalidRangeError": InvalidRangeError,
            "InvalidStateError": InvalidStateError,
            "NoPortsAvailableError": NoPortsAvailableError,
            "CallState": CallState,
            "VoIPCall": VoIPCall,
            "VoIPPhone": VoIPPhone,
        }
        return mapping[name]
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
