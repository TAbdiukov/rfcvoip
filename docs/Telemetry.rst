Telemetry
#########

The ``rfcvoip.Telemetry`` module returns serializable dictionaries and concise
text reports for phones, calls, SIP clients, SIP messages, RTP clients, and
frontend wrapper objects.

Telemetry intentionally avoids secrets. It records metadata such as the digest
algorithm, qop, header type, selected codecs, SDP support, and call state, but
does not expose passwords or digest responses.

Snapshots
*********

Telemetry. **snapshot**\ (source=None, media_type="audio") -> dict
    Returns a dictionary for a phone, call, SIP client, SIP message, RTP
    client, wrapper, or the package itself.

Telemetry. **phone_snapshot**\ (phone, media_type="audio") -> dict
    Returns phone status, SIP target, authentication telemetry, codec
    telemetry, and active call snapshots.

Telemetry. **call_snapshot**\ (call) -> dict
    Returns call state, selected codecs, local RTP ports, and related
    authentication telemetry.

Telemetry. **sip_client_snapshot**\ (client) -> dict
    Returns SIP target, transport, running state, and authentication
    telemetry.

Telemetry. **sip_message_snapshot**\ (message, media_type="audio") -> dict
    Returns a compact SIP message summary, SDP codec support, and parsed
    authentication challenge or authorization metadata.

Reports
*******

Telemetry. **report**\ (source=None, platform="discord", media_type="audio") -> str
    Returns a concise Markdown report. Supported platforms are ``discord`` and
    ``telegram``.

Telemetry. **discord_report**\ (source=None, media_type="audio") -> str
    Convenience wrapper for Discord Markdown.

Telemetry. **telegram_report**\ (source=None, media_type="audio") -> str
    Convenience wrapper for Telegram MarkdownV2 escaping.

Example:

.. code-block:: python

  from rfcvoip import Telemetry


  print(Telemetry.discord_report(phone))

Authentication telemetry
************************

Telemetry. **auth_snapshot**\ (source) -> dict
    Returns the latest digest auth metadata found on a SIP client, phone,
    call, SIP message, wrapper object, or process fallback.

Telemetry. **record_digest_auth**\ (source, record) -> dict
    Internal helper used by ``SIPClient`` after building an authorization
    header.

Example:

.. code-block:: python

  algorithm = Telemetry.get(
      phone,
      "auth.last_digest.algorithm",
      default="none",
  )

Codec telemetry
***************

Telemetry. **codec_availability**\ (codec=None, refresh=False) -> dict | list
    Returns availability for one codec, or for all registered codecs when no
    codec is provided.

Telemetry. **local_supported_codecs**\ (include_unavailable=False, ...) -> list
    Returns local codec support and availability.

Telemetry. **local_codec_offer**\ (phone=None) -> list
    Returns the audio payloads a phone would advertise in an INVITE.

Telemetry. **local_codec_report**\ (phone=None) -> dict
    Returns local codec support, local offer, transmittable codecs, and public
    audio format.

Telemetry. **sip_supported_codecs**\ (message, media_type="audio") -> list
    Returns codecs advertised by a SIP message's SDP body.

Telemetry. **codec_support_report**\ (message, media_type="audio") -> dict
    Compares remote SDP against local support and identifies compatible,
    unsupported, and transmittable audio codecs.

Telemetry. **phone_codec_report**\ (phone, target=None, media_type="audio", timeout=None) -> dict
    With no target, returns local codec telemetry. With a target, sends SIP
    OPTIONS and reports codecs from the response SDP.

Telemetry. **remote_supported_codecs**\ (phone, target, media_type="audio", timeout=None) -> list
    Sends SIP OPTIONS and returns remote SDP codec telemetry.

Telemetry. **call_active_codecs**\ (call) -> list
    Returns active RTP codec selections for a call.

Telemetry. **call_codec_report**\ (call) -> dict
    Returns remote SDP compatibility and active RTP codec information for a
    call.

Reading a single value
**********************

Telemetry. **get**\ (source, path, default=None, media_type="audio") -> Any
    Reads a single value using dot and list-index path notation.

Examples:

.. code-block:: python

  from rfcvoip import Telemetry


  print(Telemetry.get(phone, "phone.status"))
  print(Telemetry.get(phone, "codecs.local_offer[0].name"))
  print(Telemetry.get(call, "codecs.active_codecs[0].name", default="none"))