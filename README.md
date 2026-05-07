# pyVoIP
PyVoIP is a pure python VoIP/SIP/RTP library.  Currently, it supports PCMA, PCMU, and telephone-event.

This library does not depend on a sound library, i.e. you can use any sound library that can handle linear sound data i.e. pyaudio or even wave.  Keep in mind PCMU/PCMA only supports 8000Hz, 1 channel, 8 bit audio.

## Getting Started
Simply run `pip install pyVoIP`, or if installing from source:

```bash
git clone https://github.com/tayler6000/pyVoIP.git
cd pyVoIP
pip install .
```

Don't forget to check out [the documentation](https://pyvoip.readthedocs.io/)!

### Basic Example
This basic code will simple make a phone that will automatically answer then hang up.

```python
from pyVoIP.VoIP import VoIPPhone, InvalidStateError


def answer(call):  # This will be your callback function for when you receive a phone call.
    try:
        call.answer()
        call.hangup()
    except InvalidStateError:
        pass


if __name__ == "__main__":
    phone = VoIPPhone(
        <SIP Server IP>,
        <SIP Server Port>,
        <SIP Server Username>,
        <SIP Server Password>,
        callCallback=answer,
        myIP=<Your computer's local IP>,
        sipPort=<Port to use for SIP (int, default 5060)>,
        rtpPortLow=<low end of the RTP Port Range>,
        rtpPortHigh=<high end of the RTP Port Range>,
    )
    phone.start()
    input("Press enter to disable the phone")
    phone.stop()
```

### Using an outbound SIP proxy
If your provider requires a separate outbound proxy, keep `server` pointed at the SIP domain / registrar and pass the proxy host separately.

```python
phone = VoIPPhone(
    "sip.example.com",
    5060,
    "alice",
    "secret",
    myIP="192.0.2.10",
    proxy="pbx.example.net",
    proxyPort=5060,
    auth_username="alice-auth-id",  # Optional: used for Proxy-Authorization
    callCallback=answer,
)
```

`proxy` may be a hostname, `host:port`, or a SIP URI such as `sip:pbx.example.net:5060`.

### TCP/TLS SIP transport with RFC 3263-compliant resolution

```python
# TCP, explicit URI transport wins.
phone = VoIPPhone(
    "sip:registrar.example.com;transport=tcp",
    None,
    "alice",
    "secret",
    myIP="192.0.2.10",
)

# TLS via sips: and RFC 3263 DNS if no explicit port is present.
phone = VoIPPhone(
    "sips:registrar.example.com",
    None,
    "alice",
    "secret",
    myIP="192.0.2.10",
    tls_server_name="registrar.example.com",
)

# Legacy host + explicit port stays simple unless transport is supplied.
phone = VoIPPhone(
    "registrar.example.com",
    5060,
    "alice",
    "secret",
    myIP="192.0.2.10",
    transport="tcp",
)
```

### Inspecting supported codecs
Parsed SIP/SDP messages and active calls can report the codecs offered by the
remote endpoint and the codecs supported by PyVoIP.

```python
import pyVoIP


def answer(call):
    report = call.codec_support_report()

    print("Remote codecs:", report["remote"])
    print("PyVoIP codecs:", report["pyvoip"])
    print("Compatible codecs:", report["compatible"])
    print("Unsupported remote codecs:", report["unsupported"])


# Also available for any parsed SIPMessage containing SDP:
remote_codecs = sip_message.supported_codecs()
report = sip_message.codec_support_report()

# And at module level:
pyvoip_codecs = pyVoIP.supported_codecs()
```

### Pre-call codec inspection
Local codec information is available without an active SIP session. Remote
codec information can be probed before starting a call with SIP `OPTIONS` when
the peer/provider returns SDP in its `OPTIONS` response.

```python
from pyVoIP.VoIP import VoIPPhone


phone = VoIPPhone(
    "sip.example.com",
    5060,
    "alice",
    "secret",
    myIP="192.0.2.10",
)

# Purely local; this works before phone.start().
local_report = phone.available_codecs()
print("Local codec offer:", local_report["local_offer"])

# Remote probing requires SIP signalling, so start/register first.
phone.start()
remote_report = phone.available_codecs("1001")
print("Remote codecs:", remote_report["remote"])
print("Call-compatible audio:", remote_report["call_compatible"])
print("Can start call from codec data:", remote_report["can_start_call"])
```

If the remote side does not include SDP in the `OPTIONS` response,
`remote_report["remote"]` will be empty and `can_start_call` will be `None`.

### Sponsors

- [Nabu Casa](https://www.nabucasa.com/)
- [Home Assistant](https://www.home-assistant.io/)
