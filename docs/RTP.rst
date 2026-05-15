RTP - Real-time Transport Protocol
##################################

The RTP module parses RTP packets, sends and receives media, handles
telephone-event DTMF, and delegates audio encoding and decoding to the codec
registry.

Utility functions
*****************

RTP. **byte_to_bits**\ (byte: bytes) -> str
  Converts a single byte into an 8-character string of ones and zeros.

RTP. **add_bytes**\ (byte_string: bytes) -> int
  Converts big-endian bytes into an unsigned integer.

RTP. **payload_type_from_name**\ (name, rate=None, channels=None) -> PayloadType
  Resolves an SDP ``rtpmap`` codec name to a ``PayloadType``. Rate and channel
  count are used to disambiguate codec families such as DVI4 and SILK.

RTP. **payload_type_media_kind**\ (codec) -> str
  Returns ``audio``, ``video``, ``audio/video``, ``event``, or ``unknown``.

RTP. **is_audio_codec**\ (codec) -> bool
  Returns whether a payload type represents audio media.

RTP. **is_video_codec**\ (codec) -> bool
  Returns whether a payload type represents video media.

RTP. **is_transmittable_audio_codec**\ (codec, enabled_codecs=None) -> bool
  Returns whether rfcvoip can encode the codec as the continuous audio stream.

RTP. **select_transmittable_audio_codec**\ (assoc, priority_scores=None, enabled_codecs=None) -> tuple[int, PayloadType]
  Selects the negotiated payload number and codec used for RTP audio.

RTP. **codec_priority_score**\ (codec, priority_scores=None) -> int
  Returns the local preference score for a codec.

RTP. **set_codec_priority**\ (codec, score) -> None
  Sets a process-wide codec priority override.

RTP. **reset_codec_priorities**\ () -> None
  Clears process-wide codec priority overrides.

RTP. **prioritize_payload_type_map**\ (assoc, priority_scores=None) -> dict
  Returns an ordered payload map sorted by codec priority while preserving SDP
  order for ties.

RTP. **codec_fmtp_supported**\ (codec, fmtp=None) -> bool
  Returns whether rfcvoip can satisfy negotiated FMTP constraints.

RTP. **default_payload_type**\ (codec) -> int | None
  Returns the preferred static or dynamic payload number for local SDP offers.

RTP. **rtpmap_for_payload_type**\ (payload_type, codec) -> str
  Builds an SDP ``a=rtpmap`` value.

RTP. **fmtp_for_payload_type**\ (payload_type, codec) -> list[str]
  Builds SDP ``a=fmtp`` values for a local offer or answer.

Errors
******

*exception* RTP. **DynamicPayloadType**
  Raised when an integer is requested for a dynamic payload type that has no
  fixed static RTP payload number.

*exception* RTP. **RTPParseError**
  Raised when RTP parsing, RTP setup, codec selection, or audio codec
  processing fails.

Enums
*****

RTP. **RTPProtocol**
  RTP transport profile values parsed from SDP.

  RTPProtocol. **UDP**
    Raw UDP. String value ``udp``.

  RTPProtocol. **AVP**
    RTP Audio/Video Profile. String value ``RTP/AVP``. This is the RTP
    profile currently supported for audio calls.

  RTPProtocol. **SAVP**
    Secure RTP Audio/Video Profile. String value ``RTP/SAVP``. Parsed but not
    used for media setup.

.. _transmittype:

RTP. **TransmitType**
  SDP media direction.

  TransmitType. **RECVONLY**
    Receive only. String value ``recvonly``.

  TransmitType. **SENDRECV**
    Send and receive. String value ``sendrecv``.

  TransmitType. **SENDONLY**
    Send only. String value ``sendonly``.

  TransmitType. **INACTIVE**
    Do not send or receive. String value ``inactive``.

.. _payload-type:

RTP. **PayloadType**
  Enum of known RTP payload types. Static payloads have an integer value.
  Dynamic payloads have a string value and must be assigned a negotiated
  payload number in SDP.

  Important attributes:

  **value**
    Static payload number or dynamic payload name.

  **rate**
    RTP clock rate.

  **channel**
    Number of channels where known.

  **description**
    Codec description used for display and SDP names.

  Audio payloads include ``PCMU``, ``GSM``, ``G723``, ``DVI4_8000``,
  ``DVI4_16000``, ``LPC``, ``PCMA``, ``PCMU_WB``, ``PCMA_WB``, ``G722``,
  ``L16_2``, ``L16``, ``QCELP``, ``CN``, ``MPA``, ``G728``, ``DVI4_11025``,
  ``DVI4_22050``, ``G729``, ``OPUS``, ``SILK_24000``, ``SILK_16000``,
  ``SILK_12000``, and ``SILK_8000``.

  Video payloads include ``CELB``, ``JPEG``, ``NV``, ``H261``, ``MPV``,
  ``MP2T``, and ``H263``. rfcvoip parses video payload definitions but does not
  create video RTP clients.

  Non-codec payloads include ``EVENT`` for telephone-event and ``UNKNOWN`` for
  unsupported payloads.

Classes
*******

.. _RTPPacketManager:

RTPPacketManager
================

``RTPPacketManager`` stores incoming or outgoing audio bytes in a seekable
buffer while tracking RTP timestamp offsets. It handles out-of-order writes and
fills missing data with silence bytes.

RTP. **RTPPacketManager**\ ()

  **available**\ () -> int
    Returns bytes currently available from the current read position.

  **read**\ (length=160) -> bytes
    Reads ``length`` bytes. If fewer bytes are available, the result is padded
    with ``b"\x80"``.

  **rebuild**\ (reset: bool, offset=0, data=b"") -> None
    Rebuilds the internal buffer after out-of-order packets.

  **write**\ (offset: int, data: bytes) -> None
    Writes data at the RTP timestamp-derived offset.

.. _RTPMessage:

RTPMessage
==========

``RTPMessage`` parses an RTP packet and exposes header fields from RFC 3550.
It supports CSRC lists, extension headers, padding validation, and negotiated
dynamic payload mappings.

RTP. **RTPMessage**\ (data: bytes, assoc: dict[int, PayloadType])

  ``assoc`` maps negotiated RTP payload numbers to ``PayloadType`` values.

  Attributes include ``version``, ``padding``, ``extension``, ``CC``,
  ``marker``, ``payload_type``, ``sequence``, ``timestamp``, ``SSRC``,
  ``CSRC``, ``extension_profile``, ``extension_payload``, ``payload``, and
  ``raw``.

  **summary**\ () -> str
    Returns a readable summary of the RTP header.

  **parse**\ (packet: bytes) -> None
    Parses the packet and raises ``RTPParseError`` for invalid or truncated
    packets.

.. _RTPClient:

RTPClient
=========

``RTPClient`` owns one UDP socket, one receive loop, one transmit loop, and
one negotiated continuous audio codec. It can also send and receive RTP
telephone-event DTMF when that payload is negotiated.

*class* RTP. **RTPClient**\ (
    assoc,
    inIP,
    inPort,
    outIP,
    outPort,
    sendrecv,
    dtmf=None,
    audio_sample_rate=None,
    audio_sample_width=1,
    audio_channels=1,
    codec_priority_scores=None,
    enabled_codecs=None,
)

  ``assoc``
    Negotiated RTP payload map.

  ``inIP`` / ``inPort``
    Local address and port to bind.

  ``outIP`` / ``outPort``
    Remote RTP target.

  ``sendrecv``
    ``TransmitType`` or matching string.

  ``dtmf``
    Callback invoked with one DTMF character when a telephone-event start
    packet is received.

  ``audio_sample_rate``
    Public audio sample rate. When omitted, the selected codec's preferred
    source sample rate is used.

  ``audio_sample_width`` and ``audio_channels``
    Must currently be ``1`` and ``1``. Only the public sample rate is
    configurable.

  ``codec_priority_scores`` and ``enabled_codecs``
    Optional per-client codec selection overrides.

Methods:

  **start**\ () -> None
    Binds the socket and starts receive and transmit threads.

  **stop**\ () -> None
    Stops threads and closes sockets.

  **read**\ (length=None, blocking=True) -> bytes
    Reads decoded public audio. If ``length`` is omitted, one codec frame is
    read. In non-blocking mode, silence is returned when no data is available.

  **write**\ (data: bytes) -> None
    Queues public audio bytes for encoding and transmission.

  **audio_frame_size**\ (duration_ms=None) -> int
    Returns public audio bytes for a frame duration.

  **send_dtmf**\ (code: str) -> bool
    Queues one DTMF character for RTP telephone-event transmission.

  **transmit_dtmf**\ (code, duration_ms=200, packet_ms=50, volume=10) -> bool
    Sends one telephone-event sequence immediately from the transmit thread.

  **parse_packet**\ (packet: bytes) -> None
    Parses one RTP packet and dispatches it to audio or telephone-event
    handling.

  **parse_audio**\ (packet, codec=None) -> None
    Decodes audio with the selected or provided codec.

  **encode_packet**\ (payload: bytes) -> bytes
    Encodes one public audio payload with the selected codec.

  **parse_pcmu**, **encode_pcmu**, **parse_pcma**, **encode_pcma**
    Convenience wrappers for G.711 codecs.

  **parse_telephone_event**\ (packet) -> None
    Parses telephone-event payloads and calls the DTMF callback on marker
    packets.

Deprecated camelCase aliases remain available for compatibility and emit
``DeprecationWarning``.