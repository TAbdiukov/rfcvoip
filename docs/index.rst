Welcome to rfcvoip's documentation!
==================================

rfcvoip is a pure Python VoIP/SIP/RTP library.
 Formerly a pyVoIP fork, rfcvoip provides a high-level
``VoIPPhone`` API for common softphone use cases, plus lower-level SIP and RTP
modules for applications that need direct protocol control.

Current capabilities include:

* SIP registration, deregistration, inbound calls, outbound calls, OPTIONS,
  SUBSCRIBE, NOTIFY, CANCEL, BYE, and ACK handling.
* UDP signaling, TCP signaling, TLS signaling, SIPS URI handling, outbound
  proxy support, and RFC 3263 style NAPTR/SRV lookup when ``dnspython`` is
  available.
* SIP digest authentication with MD5, MD5-sess, SHA-256, SHA-256-sess,
  SHA-512-256, and SHA-512-256-sess.
* RTP audio using PCMU, PCMA, telephone-event DTMF, and optional codecs such
  as G.722, Opus, and SILK when their dependencies are available.
* Built-in codec negotiation, codec priority tuning, FMTP validation, SDP
  bandwidth checks, telemetry reports, and active call cleanup.

Public audio format
-------------------

rfcvoip exposes audio to user code as linear PCM bytes. By default this
remains unsigned 8-bit linear PCM for compatibility. Applications may select
the public bit depth with
``VoIPPhone(audio_bit_depth=8|16|24|32|64|"best")``. Stereo audio is
interleaved left/right. The sample rate and channel count are negotiated
automatically from the selected codec by default. For legacy behaviour, or for
a fixed application audio pipeline, pass ``audio_sample_rate``,
``audio_channels``, and ``audio_bit_depth`` to ``VoIPPhone``.

For example, a 20 ms frame at 8000 Hz mono is 160 bytes at 8-bit, 320 bytes at
16-bit, 480 bytes at 24-bit, 640 bytes at 32-bit, and 1280 bytes at 64-bit. At
48000 Hz stereo, 20 ms is 1920 bytes at 8-bit, 3840 bytes at 16-bit, 5760
bytes at 24-bit, 7680 bytes at 32-bit, and 15360 bytes at 64-bit. Use
``call.audio_frame_size()`` instead of hard-coding 160 when your application
may negotiate wideband, stereo-capable codecs, or non-8-bit public PCM.

Terminology
-----------

.. glossary::

  client
    The remote SIP endpoint or caller on the other side of a call.

  user
    The programmer using rfcvoip in an application.

Contents
--------

.. toctree::
   :maxdepth: 3
   :caption: Contents:

   Examples
   Globals
   Codecs
   Telemetry
   VoIP
   SIP
   RTP