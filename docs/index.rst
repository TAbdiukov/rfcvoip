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
* RTP audio using PCMU, PCMA, telephone-event DTMF, and optional codecs when
  their dependencies are available.
* Built-in codec negotiation, codec priority tuning, FMTP validation, SDP
  bandwidth checks, telemetry reports, and active call cleanup.

Public audio format
-------------------

rfcvoip exposes audio to user code as unsigned 8-bit linear bytes. Stereo
audio is interleaved left/right. The sample rate and channel count are
negotiated automatically from the selected codec by default. For legacy
behavior, or for a fixed application audio pipeline, pass
``audio_sample_rate`` and ``audio_channels`` to ``VoIPPhone``.

For example, a 20 ms frame at 8000 Hz mono is 160 bytes, while a 20 ms frame
at 48000 Hz stereo is 1920 bytes. Use ``call.audio_frame_size()`` instead of
hard-coding 160 when your application may negotiate wideband or
stereo-capable codecs.

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