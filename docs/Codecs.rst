Codecs and audio
################

rfcvoip separates RTP payload definitions from runtime codec implementations.
The enum ``RTP.PayloadType`` describes payload names and RTP clock rates. The
``rfcvoip.codecs`` package provides implementations and availability checks.

Public audio format
*******************

User code reads and writes unsigned 8-bit linear mono bytes. Codecs convert
between this public format and their RTP/native format internally.

The public sample rate is selected as follows:

* If ``VoIPPhone(audio_sample_rate=...)`` is provided, that value is used.
* Otherwise, rfcvoip uses the selected codec's preferred source sample rate.
* Before a codec has been negotiated, ``VoIPPhone.public_audio_frame_size``
  falls back to 8000 Hz unless a fixed sample rate was configured.

Use these helpers instead of hard-coding frame lengths:

.. code-block:: python

  frame = call.audio_frame_size(duration_ms=20)
  fmt = call.audio_format()

Built-in codecs
***************

PCMU
    G.711 u-law, static payload 0, RTP clock 8000 Hz.

PCMA
    G.711 A-law, static payload 8, RTP clock 8000 Hz.

PCMU-WB
    G.711.1 core-layer wideband adapter, dynamic payload, RTP clock 16000 Hz,
    default payload 112, advertises ``mode-set=1``.

PCMA-WB
    G.711.1 core-layer wideband adapter, dynamic payload, RTP clock 16000 Hz,
    default payload 113, advertises ``mode-set=1``.

telephone-event
    RTP DTMF events. This is not a continuous audio codec. It is advertised in
    audio SDP sections and is used by ``send_dtmf`` and ``get_dtmf``.

Optional codecs
***************

Opus
    Payload name ``opus``, RTP clock 48000 Hz, default payload 111. Requires
    a loadable ``libopus`` library. The ``opus`` extra installs
    ``discord.py``, which can help provide or load libopus in some
    environments.

SILK
    Payload names ``SILK/24000``, ``SILK/16000``, ``SILK/12000``, and
    ``SILK/8000``. Requires the optional SILK Python dependency that provides
    the ``pysilk`` module. The ``silk`` extra installs ``silk-python``.

The runtime list of enabled codecs is stored in
``rfcvoip.RTPCompatibleCodecs``. Optional codecs are only enabled when their
availability checks pass.

Refreshing availability
***********************

.. code-block:: python

  import rfcvoip


  enabled = rfcvoip.refresh_supported_codecs()
  print([str(codec) for codec in enabled])

Codec priority
**************

Each codec has a default priority. Larger scores are preferred. Priority
affects local SDP offers and the codec selected from remote SDP.

Process-wide helpers:

.. code-block:: python

  import rfcvoip
  from rfcvoip import RTP


  rfcvoip.set_codec_priority(RTP.PayloadType.PCMU, 1200)
  rfcvoip.reset_codec_priorities()

Per-phone priorities:

.. code-block:: python

  from rfcvoip import RTP
  from rfcvoip.VoIP import VoIPPhone


  phone = VoIPPhone(
      "sip.example.net",
      5060,
      "1000",
      "password",
      codec_priorities={RTP.PayloadType.PCMU: 1200},
  )

FMTP and bandwidth checks
*************************

rfcvoip validates negotiated FMTP where a codec exposes constraints. Examples:

* telephone-event accepts DTMF event ranges such as ``0-15``.
* PCMU-WB and PCMA-WB require compatibility with ``mode-set=1`` when a
  ``mode-set`` is supplied.
* SILK validates ``usedtx`` and ``maxaveragebitrate`` values.

SDP bandwidth lines are parsed and stored. ``AS`` and ``TIAS`` are treated as
enforceable media bitrate limits. ``CT``, ``RS``, and ``RR`` are tracked but
are not used as codec payload caps.

Codec registry helpers
**********************

``rfcvoip.codecs`` exposes lower-level helpers for applications that need
direct codec inspection:

.. code-block:: python

  from rfcvoip import RTP
  from rfcvoip.codecs import codec_availability, enabled_payload_types


  print(codec_availability(RTP.PayloadType.PCMU))
  print(enabled_payload_types(include_events=True))