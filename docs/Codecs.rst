Codecs and audio
################

rfcvoip separates RTP payload definitions from runtime codec implementations.
The enum ``RTP.PayloadType`` describes payload names and RTP clock rates. The
``rfcvoip.codecs`` package provides implementations and availability checks.

Public audio format
*******************

User code reads and writes linear PCM bytes. By default this remains unsigned
8-bit linear PCM for compatibility. Applications may select the public bit
depth with ``VoIPPhone(audio_bit_depth=8|16|24|32|64|"best")``. Stereo audio
is interleaved left/right. Codecs convert between this public format and their
RTP/native format internally.

The public PCM formats are:

* 8-bit: unsigned linear PCM, midpoint 128.
* 16-bit: signed little-endian PCM.
* 24-bit: signed little-endian packed PCM.
* 32-bit: signed little-endian integer PCM.
* 64-bit: signed little-endian integer PCM.

The public sample rate, channel count, and bit depth are selected as follows:

* If ``VoIPPhone(audio_sample_rate=...)`` is provided, that value is used.
* If ``VoIPPhone(audio_channels=1)`` or ``VoIPPhone(audio_channels=2)`` is
  provided, that channel count is used.
* If ``VoIPPhone(audio_bit_depth=...)`` is a fixed value, that bit depth is
  used.
* If ``audio_bit_depth="best"``, rfcvoip uses the selected codec's preferred
  public bit depth after codec negotiation.
* Otherwise, rfcvoip uses the selected codec's preferred source sample rate.
* Otherwise, rfcvoip uses the selected codec's preferred source channel count.
* Before a codec has been negotiated, ``VoIPPhone.public_audio_frame_size``
  falls back to 8000 Hz mono 8-bit unless fixed values were configured.

When a call has several audio media sections, its first negotiated RTP stream
selects the shared public PCM format. The other codecs convert to that format
internally. Fixed phone settings apply to every media section.

In ``"best"`` bit-depth mode, PCMU and PCMA prefer 8-bit public PCM. PCMU-WB,
PCMA-WB, G.722, Opus, SILK, AMR, and AMR-WB prefer 16-bit public PCM. ``telephone-event`` is
not continuous audio and is not used to select public bit depth.

Use these helpers instead of hard-coding frame lengths:

.. code-block:: python

  frame = call.audio_frame_size(duration_ms=20)
  fmt = call.audio_format()
  print(fmt["sample_rate"], fmt["channels"], fmt["bit_depth"])

For example, 20 ms at 8000 Hz mono is 160 bytes at 8-bit, 320 bytes at
16-bit, 480 bytes at 24-bit, 640 bytes at 32-bit, and 1280 bytes at 64-bit. At
48000 Hz stereo, 20 ms is 1920 bytes at 8-bit, 3840 bytes at 16-bit, 5760
bytes at 24-bit, 7680 bytes at 32-bit, and 15360 bytes at 64-bit.

Built-in codecs
***************

PCMU
    G.711 u-law, static payload 0, RTP clock 8000 Hz. In ``"best"`` public
    bit-depth mode, PCMU prefers 8-bit.

PCMA
    G.711 A-law, static payload 8, RTP clock 8000 Hz. In ``"best"`` public
    bit-depth mode, PCMA prefers 8-bit.

PCMU-WB
    G.711.1 core-layer wideband adapter, dynamic payload, RTP clock 16000 Hz,
    default payload 112, advertises ``mode-set=1``. In ``"best"`` public
    bit-depth mode, PCMU-WB prefers 16-bit.

PCMA-WB
    G.711.1 core-layer wideband adapter, dynamic payload, RTP clock 16000 Hz,
    default payload 113, advertises ``mode-set=1``. In ``"best"`` public
    bit-depth mode, PCMA-WB prefers 16-bit.

telephone-event
    RTP DTMF events. This is not a continuous audio codec. It is advertised in
    audio SDP sections and is used by ``send_dtmf`` and ``get_dtmf``.

Optional codecs
***************

G.722
    Static RTP payload 9. Requires the optional ``G722>=1.2.8`` PyPI package.
    Install it with ``pip install "rfcvoip[g722]"`` or install
    ``G722>=1.2.8`` separately. Older ``G722`` versions are not supported.
    Starting with ``G722`` 1.2.8, this dependency no longer requires a C++
    compiler.
    RFC 3551 keeps G.722 on an 8000 Hz RTP timestamp clock for compatibility,
    even though the codec encodes 16000 Hz wideband audio. In automatic
    sample-rate/channel mode, G.722 uses 16000 Hz mono. In ``"best"`` public
    bit-depth mode, it prefers 16-bit.

Opus
    Payload name ``opus``, RTP clock 48000 Hz, default payload 111. Requires
    a loadable ``libopus`` library. The ``opus`` extra installs
    ``discord.py``, which can help provide or load libopus in some
    environments. In automatic sample-rate/channel mode, Opus uses 48000 Hz
    stereo. In ``"best"`` public bit-depth mode, it prefers 16-bit.
    Outgoing Opus audio follows the peer's receive preference: mono by default,
    stereo when the peer advertises ``stereo=1``. The public PCM format and
    decoder can remain stereo while the outgoing stream is mono. Changing
    the stereo preference in a reINVITE is rejected with 488 while existing
    media continues with its established configuration.

SILK
    Payload names ``SILK/24000``, ``SILK/16000``, ``SILK/12000``, and
    ``SILK/8000``. Requires the optional SILK Python dependency that provides
    the ``pysilk`` module. The ``silk`` extra installs ``silk-python``. In
    automatic sample-rate/channel mode, SILK uses its negotiated sample rate
    and mono audio. In ``"best"`` public bit-depth mode, it prefers 16-bit.

AMR and AMR-WB
    AMR uses OpenCORE's AMR-NB encoder and decoder. AMR-WB uses the VisualOn
    ``vo-amrwbenc`` encoder and OpenCORE AMR-WB decoder. These are established
    native libraries also integrated by FFmpeg; rfcvoip calls their public C
    interfaces directly through Python's ``ctypes``. Speech coding is not
    reimplemented in Python. No external executable or temporary audio file
    is used for live media.

    AMR is mono at 8000 Hz (default dynamic payload 118); AMR-WB is mono at
    16000 Hz (default dynamic payload 119). Both use 20 ms speech frames and
    prefer 16-bit public PCM. All eight AMR and nine AMR-WB speech modes are
    supported, with the highest negotiated mode selected initially. Native
    PCM conversion handles host endianness explicitly. Public audio can
    still use every supported bit depth, rate, and mono/stereo setting.
    SDP bandwidth checks and telemetry use the highest mode in the negotiated
    mode set, so a low-bitrate-only offer can operate under its bandwidth cap.

    Install the native runtime libraries separately:

    * Debian/Ubuntu: ``sudo apt-get install libopencore-amrnb0 libopencore-amrwb0 libvo-amrwbenc0``.
    * macOS: ``brew install opencore-amr vo-amrwbenc``.
    * Windows, MSYS2 MINGW64: ``pacman -S mingw-w64-x86_64-opencore-amr mingw-w64-x86_64-vo-amrwbenc``.

    Windows users should set ``RFCVOIP_AMR_LIBRARY_DIR`` to the directory
    containing the DLLs, typically ``C:\\msys64\\mingw64\\bin``. This also
    configures Windows' dependency search directory. The optional
    ``RFCVOIP_AMRNB_LIBRARY``, ``RFCVOIP_AMRWB_LIBRARY``, and
    ``RFCVOIP_AMRWBENC_LIBRARY`` environment variables select individual
    library paths. Binaries must match Python's architecture. Linux shared
    objects and macOS dylibs are discovered automatically, including normal
    Intel and Apple Silicon Homebrew locations. The Python extras do not
    install these native libraries. If a required encoder or decoder is
    missing, that codec is reported unavailable and omitted from offers;
    existing codecs remain usable.

    RFC 4867 octet-aligned and bandwidth-efficient RTP are supported. Offers
    advertise ``octet-align=1;mode-change-capability=2``; omitted ``octet-align`` in a peer's offer
    correctly selects bandwidth-efficient operation. Answers preserve the
    offered packet format and mode set. Aggregated packets, SID comfort-noise
    frames, no-data frames, and AMR-WB speech-lost frames are accepted. Up to
    50 frames (one second) may be carried in one packet; live transmission
    sends one 20 ms frame per packet. Invalid/truncated packets are validated
    completely before native decoding. The RTP Q bit is passed to the native
    decoder as its bad-frame indicator.

    ``mode-set``, ``mode-change-period`` (1 or 2), and
    ``mode-change-neighbor`` are honored, including valid incoming codec mode
    requests. CRC, robust sorting, interleaving, and multichannel AMR are not
    implemented and are rejected during negotiation. Transmit DTX is disabled;
    receive DTX/comfort noise is supported. ReINVITEs which change the active
    AMR format or mode constraints are rejected while existing media continues.

    Default preference scores are Opus 1000, AMR-WB 960, SILK 920--950,
    G.722 910, G.711 wideband 850--900, AMR 750, PCMU 700, and PCMA 650.
    Process-wide and per-phone overrides apply to AMR as to all other codecs.

    Upstream references: `OpenCORE AMR <https://opencore-amr.sourceforge.net/>`_,
    `VisualOn encoder <https://github.com/mstorsjo/vo-amrwbenc>`_,
    `FFmpeg integration <https://ffmpeg.org/general.html#OpenCORE-AMR>`_, and
    `RFC 4867 <https://www.rfc-editor.org/rfc/rfc4867.html>`_.
    Prebuilt packages: `Homebrew OpenCORE <https://formulae.brew.sh/formula/opencore-amr>`_,
    `Homebrew VisualOn <https://formulae.brew.sh/formula/vo-amrwbenc>`_,
    `MSYS2 OpenCORE <https://packages.msys2.org/packages/mingw-w64-x86_64-opencore-amr>`_,
    `MSYS2 VisualOn <https://packages.msys2.org/packages/mingw-w64-x86_64-vo-amrwbenc>`_.

The ``no-compiler`` and ``all`` extras install the optional G.722, Opus, and
SILK dependencies. The ``compiler`` extra is retained for compatibility and
currently does not install codec dependencies.

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
