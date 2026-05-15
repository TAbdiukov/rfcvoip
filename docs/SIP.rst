SIP - Session Initiation Protocol
#################################

The SIP module parses SIP messages, handles registration, creates SIP
requests and responses, performs digest authentication, manages subscriptions,
and routes call-control messages to ``VoIPPhone``.

Top-level helpers
*****************

SIP. **extract_sdp_bodies**\ (content_type, body: bytes) -> list[bytes]
  Extracts ``application/sdp`` bodies from direct or multipart SIP bodies.

SIP. **extract_sdp_body**\ (content_type, body: bytes) -> bytes | None
  Returns the first SDP body from ``extract_sdp_bodies``.

SIP. **codec_bandwidth_supported**\ (codec, session_bandwidth=None, media_bandwidth=None) -> bool
  Returns whether an SDP ``AS`` or ``TIAS`` limit can carry a codec whose
  payload bitrate is known.

Errors
******

*exception* SIP. **InvalidAccountInfoError**
  Raised when registration authentication fails after a challenge response.

*exception* SIP. **SIPParseError**
  Raised when a SIP message, header, SDP body, digest challenge, or stream
  frame cannot be parsed.

*exception* SIP. **SIPRequestError**
  Raised when a generated SIP request receives an unrecoverable failure.

Enums
*****

SIP. **SIPMessageType**
  ``MESSAGE`` for SIP requests and ``RESPONSE`` for SIP responses.

SIP. **SIPStatus**
  Enum of common SIP response statuses. Unknown response codes are represented
  by an internal status-code object with the parsed code and phrase.

  Common statuses include ``TRYING`` (100), ``RINGING`` (180), ``OK`` (200),
  ``BAD_REQUEST`` (400), ``UNAUTHORIZED`` (401),
  ``PROXY_AUTHENTICATION_REQUIRED`` (407), ``NOT_FOUND`` (404),
  ``BUSY_HERE`` (486), ``NOT_ACCEPTABLE_HERE`` (488),
  ``SERVICE_UNAVAILABLE`` (503), and many others.

Transports and DNS
******************

SIP. **SIPTransport**
  Enum values ``UDP``, ``TCP``, and ``TLS``. ``SIPS`` URIs select TLS.

``SIPResolver`` parses SIP and SIPS URIs, honors ``transport=`` parameters,
supports bracketed IPv6 host-port formatting, and uses NAPTR/SRV lookup when
``dnspython`` is installed and the target is suitable for DNS resolution.

``SIPConnection`` wraps UDP datagrams and TCP/TLS streams. Stream mode frames
messages using ``Content-Length`` and discards CRLF keepalives.

.. _SIPMessage:

SIPMessage
**********

*class* SIP. **SIPMessage**\ (data: bytes)
  Parses a SIP request or response.

Parsed attributes:

``heading``
  Raw start line as bytes.

``type``
  ``SIPMessageType.MESSAGE`` or ``SIPMessageType.RESPONSE``.

``method``
  Request method for SIP requests.

``uri``
  Request URI for SIP requests.

``status``
  ``SIPStatus`` or status-code object for SIP responses.

``headers``
  Parsed headers. Compact header names are expanded. Duplicate single-value
  headers are rejected. Multiple Via headers are stored as a list.

``body``
  Parsed SDP fields when an SDP body is present.

``body_raw`` and ``body_text``
  Raw and decoded body content.

``authentication``
  Most recently parsed digest authentication header.

``authentication_challenges``
  All parsed WWW-Authenticate and Proxy-Authenticate challenges.

``authentication_header``
  Header name that populated ``authentication``.

``raw``
  Original bytes.

Methods:

  **summary**\ () -> str
    Returns a readable summary with Authorization and Proxy-Authorization
    redacted.

  **parse**\ (data: bytes) -> None
    Detects request versus response and dispatches parsing.

  **parse_sip_response**\ (data: bytes) -> None
    Parses response start line, headers, and body.

  **parse_sip_message**\ (data: bytes) -> None
    Parses request start line, headers, and body.

  **parse_header**\ (header: str, data: str) -> None
    Parses one SIP header.

  **parse_body**\ (header: str, data: str) -> None
    Parses one SDP field or stores non-SDP body data.

SDP parsing supports session and media ``c=`` lines, ``b=`` bandwidth lines,
``m=`` media sections, ``rtpmap``, ``fmtp``, media direction attributes, and
multipart bodies containing a single SDP part.

.. _SIPClient:

SIPClient
*********

*class* SIP. **SIPClient**\ (
    server,
    port,
    username,
    password,
    phone,
    myIP="0.0.0.0",
    myPort=5060,
    callCallback=None,
    fatalCallback=None,
    auth_username=None,
    proxy=None,
    proxy_port=None,
    proxyPort=None,
    transport=None,
    tls_context=None,
    tls_server_name=None,
)

``server`` and ``port``
  Registrar or outbound SIP target. ``server`` may be a host, SIP URI, or
  SIPS URI.

``username`` and ``password``
  Public SIP username and password.

``auth_username``
  Digest authentication identity. Defaults to ``username``.

``proxy`` and ``proxy_port`` / ``proxyPort``
  Optional outbound proxy target.

``transport``
  ``udp``, ``tcp``, or ``tls``. URI parameters and SIPS may also select the
  transport.

``tls_context`` and ``tls_server_name``
  Optional TLS settings for TLS signaling.

Registration:

  **start**\ () -> None
    Opens the SIP connection, registers, and starts the receive thread.

  **stop**\ () -> None
    Cancels pending registration refresh, deregisters when possible, and
    closes sockets.

  **register**\ () -> bool
    Sends REGISTER and handles 401 or 407 digest challenges.

  **deregister**\ () -> bool
    Sends REGISTER with expiration zero.

Requests:

  **invite**\ (number, ms, sendtype) -> tuple[SIPMessage, str, int]
    Sends an INVITE and returns the generated request message, Call-ID, and
    session id. Provisional and final responses may be queued for the owning
    ``VoIPPhone``.

  **options**\ (target, timeout=None) -> SIPMessage
    Sends SIP OPTIONS and returns the final response. Handles digest
    challenges.

  **subscribe_to**\ (target, event="presence", expires=3600, accept=None) -> dict
    Sends SUBSCRIBE and stores subscription state.

  **unsubscribe_from**\ (identifier, event=None) -> dict
    Sends SUBSCRIBE with ``Expires: 0`` for an active subscription.

  **list_subscriptions**\ () -> list[dict]
    Returns active subscription snapshots.

Message routing:

  **recv_loop**\ () -> None
    Receive-thread loop.

  **recv**\ () -> None
    Reads one SIP message and dispatches it.

  **parse_message**\ (message) -> None
    Routes responses, INVITE, BYE, CANCEL, OPTIONS, NOTIFY, ACK, and
    unsupported methods.

  **send_raw**\ (data, target=None) -> None
    Sends raw SIP bytes.

  **send_response**\ (request, response) -> None
    Sends a generated response to the correct response target.

Address helpers:

  **signal_target**\ () -> tuple[str, int]
    Returns proxy target when configured, otherwise registrar target.

  **signal_transport**\ () -> SIPTransport
    Returns active signaling transport.

  **response_target**\ (request) -> tuple[str, int]
    Chooses a response target using Via, rport, received, and source address.

  **dialog_target**\ (request) -> tuple[str, int]
    Chooses target for in-dialog requests such as BYE.

  **ack_target**\ (request) -> tuple[str, int]
    Chooses ACK target, including Contact for 2xx INVITE responses.

Generators:

  ``SIPClient`` provides ``gen_*`` helpers for REGISTER, INVITE, ANSWER,
  RINGING, BUSY, OK, BYE, ACK, CANCEL, OPTIONS OK, and generic responses.
  These are mostly used internally by ``VoIPPhone`` and ``VoIPCall``.

Digest authentication:

  **gen_authorization**\ (request) -> bytes
    Legacy digest response helper.

  Digest auth generation supports multiple challenges, proxy challenges,
  qop ``auth`` and ``auth-int``, session algorithms, cnonce generation, and
  telemetry recording. Authorization headers are redacted in debug summaries.

Deprecated camelCase aliases remain available for compatibility and emit
``DeprecationWarning``.