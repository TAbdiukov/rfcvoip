VoIP - High-level phone and call API
####################################

The VoIP module coordinates SIP signaling and RTP media. Most applications use
``VoIPPhone`` and ``VoIPCall`` rather than constructing ``SIPClient`` or
``RTPClient`` directly.

Errors
******

*exception* VoIP. **InvalidStateError**
  Raised when a call operation is not valid for the current state, such as
  answering a non-ringing call or hanging up a call that is not answered.

*exception* VoIP. **InvalidRangeError**
  Raised for invalid RTP port ranges, invalid audio sample rates, or invalid
  port request counts.

*exception* VoIP. **NoPortsAvailableError**
  Raised when no RTP ports are available.

Enums
*****

.. _callstate:

VoIP. **CallState**
  ``DIALING``
    Outbound INVITE has been sent and the call has not connected.

  ``RINGING``
    Incoming call is waiting for answer, or outbound call has received ringing
    or session progress.

  ``ANSWERED``
    Call is active and RTP clients have been started.

  ``ENDED``
    Call is locally ended. No call-control actions are valid.

.. _phonestatus:

VoIP. **PhoneStatus**
  ``INACTIVE``
    Phone is stopped.

  ``REGISTERING``
    Phone is starting or refreshing registration.

  ``REGISTERED``
    Phone is registered and ready.

  ``DEREGISTERING``
    Phone is stopping.

  ``FAILED``
    Phone stopped after registration or startup failure.

.. _VoIPPhone:

VoIPPhone
*********

*class* VoIP. **VoIPPhone**\ (
    server,
    port,
    username,
    password,
    myIP="0.0.0.0",
    callCallback=None,
    sipPort=5060,
    rtpPortLow=10000,
    rtpPortHigh=20000,
    auth_username=None,
    proxy=None,
    proxyPort=None,
    proxy_port=None,
    transport=None,
    tls_context=None,
    tls_server_name=None,
    codec_priorities=None,
    audio_sample_rate=None,
)

Required arguments:

``server`` and ``port``
  SIP registrar or target. ``server`` may be a host, SIP URI, or SIPS URI.

``username`` and ``password``
  SIP account credentials.

Common optional arguments:

``myIP``
  Local IP address advertised in SIP Contact and SDP and used for SIP/RTP
  binding. Use an address family compatible with remote RTP SDP.

``callCallback``
  Callable receiving a ``VoIPCall`` for inbound calls. If omitted, inbound
  calls are answered with ``486 Busy Here``.

``sipPort``
  Local SIP signaling port.

``rtpPortLow`` and ``rtpPortHigh``
  Range used for RTP media sockets.

``auth_username``
  Digest authentication username when different from the public SIP username.

``proxy`` and ``proxy_port`` / ``proxyPort``
  Outbound SIP proxy.

``transport``
  ``udp``, ``tcp``, or ``tls``.

``tls_context`` and ``tls_server_name``
  TLS configuration for TLS signaling.

``codec_priorities``
  Per-phone codec priority overrides. Larger scores are preferred.

``audio_sample_rate``
  Fixed public audio sample rate. When omitted, rfcvoip uses the selected
  codec's preferred source sample rate.

Lifecycle:

  **start**\ () -> None
    Starts SIP signaling and registration.

  **stop**\ (failed=False) -> None
    Ends active calls, deregisters, closes SIP/RTP sockets, and updates phone
    status.

  **fatal**\ () -> None
    Stops the phone and marks it failed.

  **get_status**\ () -> PhoneStatus
    Returns current phone status.

Calling:

  **call**\ (number: str) -> VoIPCall
    Originates an outbound call. Local SDP offer includes enabled
    transmittable audio codecs plus telephone-event when available.

  **callback**\ (request: SIPMessage) -> None
    Internal entry point used by ``SIPClient`` for call-control messages.

RTP ports:

  **request_port**\ (blocking=True) -> int
    Reserves one RTP port.

  **request_ports**\ (count, blocking=True) -> list[int]
    Reserves contiguous RTP ports.

  **reserve_ports**\ (ports, allow_existing=None) -> list[int]
    Reserves specific ports.

  **release_ports**\ (call=None) -> None
    Releases unused ports, or ports owned by a specific call.

Codec and audio helpers:

  **refresh_supported_codecs**\ () -> list[PayloadType]
    Refreshes optional codec availability.

  **set_codec_priority**\ (codec, score) -> list[PayloadType]
    Sets a per-phone codec priority override.

  **reset_codec_priorities**\ () -> list[PayloadType]
    Clears per-phone codec priority overrides.

  **public_audio_frame_size**\ (duration_ms=20) -> int
    Returns bytes for the phone's public audio format before negotiation.

  **audio_format**\ () -> dict
    Returns public audio format metadata.

Negotiation behavior:

* Inbound calls with unsupported RTP profiles, incompatible address families,
  no assignable audio ports, video-only media, or no compatible transmittable
  audio codec are rejected with ``488 Not Acceptable Here``.
* Outbound calls ACK final INVITE responses. If a 200 OK contains incompatible
  SDP, rfcvoip sends BYE when possible and ends the local call.
* Media direction is negotiated from local desired direction and remote SDP
  direction.
* Multiple media connections require contiguous local RTP ports so the answer
  can advertise ``m=audio port/count`` correctly.

.. _VoIPCall:

VoIPCall
********

*class* VoIP. **VoIPCall**\ (
    phone,
    callstate,
    request,
    session_id,
    myIP,
    ms=None,
    sendmode="sendonly",
)

``VoIPCall`` is normally created by ``VoIPPhone``. It stores the SIP request,
Call-ID, RTP clients, negotiated media, DTMF buffer, and assigned ports.

Attributes:

``state``
  Current ``CallState``.

``call_id``
  SIP Call-ID.

``request``
  Original SIP INVITE or generated outbound INVITE.

``remote_sip_message``
  Remote SDP message for inbound ringing calls or answered outbound calls.

``session_id``
  SDP session id used in answers.

``RTPClients``
  Active RTP clients for the call.

Call control:

  **answer**\ () -> None
    Answers a ringing inbound call, starts RTP clients, and sends 200 OK with
    SDP.

  **answered**\ (request: SIPMessage) -> None
    Internal handler for successful outbound INVITE responses.

  **deny**\ () -> None
    Rejects a ringing inbound call with busy.

  **cancel**\ () -> None
    Cancels a dialing or ringing outbound call.

  **hangup**\ () -> None
    Stops RTP and sends BYE for an answered call.

  **bye**\ () -> None
    Internal handler for remote BYE. User code should call ``hangup`` instead.

  **renegotiate**\ (request: SIPMessage) -> None
    Handles in-dialog INVITE when compatible with the current RTP setup.

Audio:

  **write_audio**\ (data: bytes) -> None
    Queues public audio bytes for every RTP client.

  **read_audio**\ (length=None, blocking=True) -> bytes
    Reads decoded public audio. If multiple RTP clients exist, audio is mixed.
    If the call is not answered, silence is returned.

  **audio_frame_size**\ (duration_ms=20) -> int
    Returns the number of public audio bytes for one frame.

  **audio_format**\ () -> dict
    Returns call audio format metadata.

DTMF:

  **dtmf_callback**\ (code: str) -> None
    Internal callback used by RTP telephone-event handling.

  **get_dtmf**\ (length=1) -> str
    Reads queued DTMF digits. Returns ``""`` when no digit is available.

  **send_dtmf**\ (digits: str) -> bool
    Queues outbound telephone-event DTMF for active RTP clients.

  **send_dtmf_sequence**\ (digits: str) -> bool
    Alias for ``send_dtmf``.

SDP:

  **gen_ms**\ () -> dict
    Builds the local SDP media map for an answer and starts RTP clients.

Deprecated camelCase aliases remain available for compatibility and emit
``DeprecationWarning``.