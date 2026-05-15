Global configuration
####################

The top-level ``rfcvoip`` package exposes process-wide configuration values and
helpers. These are intentionally simple globals so existing applications can
adjust behavior without replacing the SIP or RTP classes.

Debug output
************

rfcvoip. **DEBUG** = False
    When set to ``True``, rfcvoip prints debug messages with timestamps.
    Sensitive SIP authorization headers are redacted in summaries.

rfcvoip. **debug**\ (message, error_message=None) -> None
    Internal logging helper. When ``DEBUG`` is false, only ``error_message`` is
    printed if it is provided.

Timing and registration
***********************

rfcvoip. **TRANSMIT_DELAY_REDUCTION** = 0.0
    RTP transmission delay adjustment. The effective divisor is
    ``TRANSMIT_DELAY_REDUCTION + 1.0``. Most applications should leave this at
    ``0.0``.

rfcvoip. **REGISTER_FAILURE_THRESHOLD** = 3
    Number of consecutive registration failures before a ``VoIPPhone`` stops
    and moves to ``PhoneStatus.FAILED``.

Compatibility lists
*******************

rfcvoip. **SIPCompatibleMethods**
    SIP methods rfcvoip can process or generate:
    ``INVITE``, ``ACK``, ``BYE``, ``CANCEL``, ``OPTIONS``, ``SUBSCRIBE``, and
    ``NOTIFY``.

rfcvoip. **SIPCompatibleVersions**
    SIP versions accepted by the parser. Currently ``["SIP/2.0"]``.

rfcvoip. **RTPCompatibleVersions**
    RTP protocol versions accepted by the RTP parser. Currently ``[2]``.

rfcvoip. **RTPCompatibleCodecs**
    Runtime list of enabled RTP payload types. This list is built from the
    codec registry and optional dependency availability. It normally contains
    PCMU, PCMA, PCMU-WB, PCMA-WB, any available optional codecs such as Opus
    or SILK, and telephone-event.

Codec helper functions
**********************

rfcvoip. **refresh_supported_codecs**\ () -> list
    Refreshes optional codec availability and rebuilds
    ``RTPCompatibleCodecs``.

rfcvoip. **set_codec_priority**\ (payload_type, score) -> list
    Sets a process-wide codec priority override and refreshes
    ``RTPCompatibleCodecs``. Larger scores are preferred.

rfcvoip. **reset_codec_priorities**\ () -> list
    Clears process-wide codec priority overrides and refreshes
    ``RTPCompatibleCodecs``.

rfcvoip. **codec_priority_score**\ (payload_type) -> int
    Returns the current process-wide priority score for a payload type.

rfcvoip. **codec_priorities**\ (include_events=True) -> dict
    Returns current codec priorities keyed by codec name.

Version
*******

rfcvoip. **__version__**
    Package version string.

rfcvoip. **version_info**
    Tuple form of the public version number.