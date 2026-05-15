# rfcvoip

[![GitHub](https://img.shields.io/badge/GitHub-TAbdiukov/rfcvoip-black?logo=github)](https://github.com/TAbdiukov/rfcvoip)
[![PyPI Version](https://img.shields.io/pypi/v/rfcvoip.svg)](https://pypi.org/project/rfcvoip) 
![License](https://img.shields.io/github/license/TAbdiukov/rfcvoip)

[![buymeacoffee](https://www.buymeacoffee.com/assets/img/custom_images/orange_img.png)](https://www.buymeacoffee.com/tabdiukov)

rfcvoip is a maintained, protocol-focused VoIP/SIP/RTP library and a practical
drop-in successor to the original PyVoIP API.

The original PyVoIP project has been on a long project freeze. rfcvoip keeps
the same spirit and familiar high-level API, while focusing on production
reliability, protocol accuracy, safer parsing, better negotiation, and clearer
runtime telemetry. Since the original codebase, rfcvoip has fixed more than
200 issues across SIP signaling, RTP media, SDP parsing, digest
authentication, codec negotiation, transport handling, cleanup, and
thread-safety.

rfcvoip does not require a sound library. You can use any audio backend that
can read or write linear byte data, such as `wave`, PyAudio, sounddevice, a bot
framework, or your own media pipeline.

Original PyVoIP contributors are still credited and honored. rfcvoip builds on
their work while continuing development under the new project name.

## Highlights

- High-level `VoIPPhone` and `VoIPCall` API for inbound and outbound calls.
- Lower-level SIP and RTP modules for applications that need direct protocol
  control.
- SIP registration, deregistration, INVITE, ACK, BYE, CANCEL, OPTIONS,
  SUBSCRIBE, and NOTIFY handling.
- UDP signaling, TCP signaling, TLS signaling, SIPS URI handling, outbound SIP
  proxy support, and RFC 3263-style NAPTR/SRV lookup when `dnspython` is
  available.
- SIP digest authentication with MD5, MD5-sess, SHA-256, SHA-256-sess,
  SHA-512-256, and SHA-512-256-sess.
- RTP audio using PCMU, PCMA, PCMU-WB, PCMA-WB, telephone-event DTMF, optional
  Opus, and optional SILK.
- Codec priority tuning, FMTP validation, SDP bandwidth checks, dynamic payload
  mapping, RTP extension and padding parsing, and robust RTP buffering.
- Built-in telemetry for SIP authentication, local and remote codecs, active
  calls, RTP selections, and frontend-friendly reports.
- Safer behavior around malformed SIP/SDP, duplicate headers, Content-Length
  mismatches, CRLF keepalives, IPv4/IPv6 handling, rport handling, failed call
  setup, and shutdown cleanup.

## Installation

```bash
pip install rfcvoip
```

Optional codec extras:

```bash
pip install "rfcvoip[opus]"
pip install "rfcvoip[silk]"
pip install "rfcvoip[all]"
```

Opus support requires a loadable system `libopus` library. SILK support
requires the optional `pysilk` backend, provided by the declared SILK extra.
Unavailable optional codecs are reported as unavailable and are not included in
SIP offers.

Installing from a source checkout:

```bash
python -m pip install .
```

## Importing and migration

New applications should import rfcvoip:

```python
from rfcvoip.VoIP import VoIPPhone
from rfcvoip import RTP, Telemetry
```

The public API is intentionally familiar to PyVoIP users. In most applications,
migration is limited to installing the new package and updating imports from
`pyVoIP` to `rfcvoip`.

## Public audio format

rfcvoip exposes audio to user code as unsigned 8-bit linear mono bytes.

The sample rate is configurable:

- If `VoIPPhone(audio_sample_rate=...)` is provided, that fixed sample rate is
  used.
- If `audio_sample_rate=None`, rfcvoip uses the selected codec's preferred
  public sample rate.
- Before a codec has been negotiated, the fallback public sample rate is
  8000 Hz unless a fixed sample rate was configured.

For example, 20 ms at 8000 Hz is 160 bytes, 20 ms at 16000 Hz is 320 bytes,
and 20 ms at 48000 Hz is 960 bytes. Use `call.audio_frame_size()` instead of
hard-coding `160` when your application may negotiate wideband codecs.

PCMU and PCMA remain 8000 Hz RTP codecs on the wire. Wideband and optional
codecs convert internally between the public audio format and their native RTP
format.

## Quick start

A minimal inbound-call application creates a `VoIPPhone`, starts it, and
handles calls in a callback:

```python
from rfcvoip.VoIP import InvalidStateError, VoIPPhone


def answer(call):
    try:
        call.answer()
        call.hangup()
    except InvalidStateError:
        pass


phone = VoIPPhone(
    "sip.example.net",
    5060,
    "1000",
    "password",
    myIP="192.0.2.10",
    callCallback=answer,
)
phone.start()
input("Press enter to disable the phone")
phone.stop()
```

For full examples covering playback, IVR flows, outbound calls, and codec
configuration, see the documentation.

## Common features

### Inbound and outbound calls

`VoIPPhone.call("1001")` originates an outbound call and returns a `VoIPCall`.
Calls move through `DIALING`, `RINGING`, `ANSWERED`, and `ENDED` states.

`VoIPCall` supports answering, denying, cancelling, hanging up, reading audio,
writing audio, DTMF input, and outbound DTMF. Use `write_audio`,
`read_audio`, `get_dtmf`, `send_dtmf`, `audio_frame_size`, and
`audio_format`.

### Outbound SIP proxy

If your provider requires a separate outbound proxy, keep `server` pointed at
the SIP domain or registrar and pass `proxy` / `proxyPort` separately.
`proxy` may be a hostname, `host:port`, or a SIP URI such as
`sip:pbx.example.net:5060`.

```python
phone = VoIPPhone(
    "sip.example.com",
    5060,
    "alice",
    "secret",
    myIP="192.0.2.10",
    proxy="pbx.example.net",
    proxyPort=5060,
    auth_username="alice-auth-id",
)
```

### TCP, TLS, SIPS, and DNS resolution

rfcvoip supports explicit transport selection, URI transport parameters, SIPS,
TLS server names, and RFC 3263-style DNS resolution when appropriate.

Examples of supported targets:

- `sip:registrar.example.com;transport=tcp`
- `sips:registrar.example.com`
- `registrar.example.com` with `transport="tcp"`
- `sip:pbx.example.net:5060` as an outbound proxy

### DTMF

rfcvoip supports RTP telephone-event DTMF when the remote endpoint negotiates
the telephone-event payload. Received digits are read with `call.get_dtmf()`.
Outbound DTMF is queued with `call.send_dtmf("123#")` and supports `0-9`,
`*`, `#`, and `A-D`.

## Codecs

Built-in continuous audio codecs:

- PCMU, G.711 u-law, static payload 0.
- PCMA, G.711 A-law, static payload 8.
- PCMU-WB, G.711.1 core-layer wideband adapter, dynamic payload, default 112.
- PCMA-WB, G.711.1 core-layer wideband adapter, dynamic payload, default 113.

Built-in event payloads:

- telephone-event DTMF, default dynamic payload 101.

Optional codecs:

- Opus, default dynamic payload 111, requiring loadable `libopus`.
- SILK at 24000, 16000, 12000, and 8000 Hz, requiring `pysilk`.

PCMA-WB and PCMU-WB are implemented as RFC 5391 / G.711.1 R1 core-layer
payloads. rfcvoip advertises `mode-set=1`, uses a 16000 Hz RTP clock, and
keeps the public read/write format as unsigned 8-bit mono with a configurable
sample rate. Incoming G.711.1 packets in wider modes are decoded from their
G.711-compatible L0 core layer.

Codec priority affects local SDP offer order and the selected RTP codec when a
remote endpoint advertises more than one compatible payload. Larger scores are
preferred.

```python
import rfcvoip
from rfcvoip import RTP

rfcvoip.set_codec_priority(RTP.PayloadType.PCMU, 1200)
rfcvoip.reset_codec_priorities()
```

Per-phone priorities can also be supplied with `VoIPPhone(codec_priorities=...)`.
If optional codec dependencies are loaded after import, call
`rfcvoip.refresh_supported_codecs()` before creating or placing calls.

## Telemetry and codec inspection

The `Telemetry` module provides serializable reports for local codec support,
remote SDP, active calls, SIP authentication, and RTP codec selections.

```python
from rfcvoip import Telemetry

print(Telemetry.report(phone))
```

Common telemetry helpers include:

- `Telemetry.snapshot(...)`
- `Telemetry.report(...)`
- `Telemetry.get(...)`
- `Telemetry.local_codec_report(phone)`
- `Telemetry.phone_codec_report(phone, target="1001")`
- `Telemetry.call_active_codecs(call)`
- `Telemetry.codec_availability(refresh=True)`

Remote codec information can be probed with SIP OPTIONS when the peer or
provider includes SDP in the OPTIONS response. If the remote side does not
include SDP, the remote codec list is empty and `can_start_call` is `None`.

## Reliability and protocol behavior

rfcvoip hardens many areas that commonly cause softphone instability:

- SIP parser validation for malformed headers, duplicate singleton headers,
  folded headers, compact headers, multipart SDP, and Content-Length handling.
- SIP TCP/TLS stream framing with keepalive handling and strict body lengths.
- Correct transaction matching for REGISTER, INVITE, SUBSCRIBE, OPTIONS, ACK,
  CANCEL, BYE, and digest-auth retries.
- Digest auth challenge selection, qop handling, auth-int body hashing,
  stronger algorithms, cnonce generation, and sensitive-header redaction.
- SDP media scoping for `m=`, `c=`, `a=rtpmap`, `a=fmtp`, direction
  attributes, bandwidth lines, disabled streams, unsupported RTP profiles, and
  incompatible address families.
- RTP validation for packet length, payload types, extensions, padding,
  telephone-event payloads, timestamp gaps, jitter buffering, and socket
  lifecycle.
- Safer call cleanup for failed outbound calls, late final INVITE responses,
  unmatched ACKs, remote BYE/CANCEL, shutdown, and RTP port release.

## Development checks

```bash
python -m pip install -r requirements-dev.txt
python -m pip install .
python -m compileall -q rfcvoip
python -m pytest -q
```

## License

rfcvoip is licensed under the GNU General Public License version 3. See
[LICENSE](LICENSE) for the full license text.

rfcvoip is a modified and renamed continuation of the original PyVoIP project.
Original PyVoIP copyright and contributor attribution is preserved in
[NOTICE](NOTICE).

## Contributors and acknowledgements

rfcvoip is built on the original PyVoIP project and continues to honor the
people and projects that made it possible.

- [TJ Porter](https://github.com/tayler6000), original PyVoIP implementation.
- [synodriver](https://github.com/synodriver), pysilk and SILK bindings work.
- [Nabu Casa](https://www.nabucasa.com/).
- [Home Assistant](https://www.home-assistant.io/).

Additional thanks to the open-source development and research
communities whose tools and specifications make this project possible.