# Changelog

## 2.8.2+RFC

- Codec: Implement per-instance codec ordering. 
- VoIP: Fix media-level SDP `c=` handling
- VoIP: Fix potential RTP port leak in `VoIPPhone.call()`
- VoIP: Handle malformed `200 OK` ingestion
- SIP: Fix a shutdown race in `SIPClient.recv`
- SIP: More robust parsing in `parse_sip_message()`
- SIP: Prevent malformed `a=rtpmap` lines from throwing an index error during parsing
- SIP: SDP payload resolution prefer `rtpmap` mappings over static RTP payload numbers.
- SIP Preserved media-level SDP direction attributes on their owning `m=` sections.
- RTP: Harden RTP packet buffering against huge timestamp gaps that could cause excessive memory allocation.


## 2.8.1+RFC

- RTP/SIPTransport: Fix plain `sip:` RFC3263 fallback resolution incorrectly considering SIPS/TLS SRV records for non-secure SIP URIs
- RTP: Make RTP packet manager rebuilding lock-safe
- SIP: Preserve repeated auth headers and pick usable digest challenge
- SIP: Preserve `sips:` for subscription targets
- SIP: Fix `Via` header parsing when only a single `Via` header is present
- SIP: Fix SDP `Content-Length` generation to use UTF-8 byte length instead of Python string length
- VoIP: Ignore disabled SDP media sections (`m=` port `0`) during codec negotiation and RTP setup
- VoIP: Restrict RTP negotiation/setup to supported RTP/AVP media profiles only
- VoIP: Prevent RTP clients from being created for rejected or inactive SDP audio streams
- VoIP: Improve SDP media filtering consistency across inbound call handling, renegotiation, and answered-call processing
- Packaging: test exclusion cleanup

## 2.8.0+RFC

- Packaging: Raise minimum supported Python version to 3.8 for stdlib `dataclasses` compatibility.
- RTP: make RTP buffer rebuild deterministic
- RTP: Reject invalid RTP payload types instead of masking them
- RTP: Fix RTPPacketManager.read() does not advance over padded silence
- VoIP: preserve consumed outbound provisional INVITE responses
- VoIP: Fix theoretical _add_codec_to_offer() forever loop
- SIP: Fix outbound INVITE response matching to validate CSeq method alongside Call-ID
- SIP: Fix inconsistent To-tag generation across responses within the same INVITE dialog
- VoIP: Fix stop() leaking ringing/dialing calls and assigned RTP ports during shutdown
- SIP: Improve REGISTER interoperability by reusing a stable Call-ID across refreshes and deregistration
- RTP: Prune RTP packet history to reduce long-call memory growth
- SIP: More robust header parsing
- SIP: Preserve UDP source address and honor `rport`
- SIP: Continue waiting after 100 Trying so INVITE auth challenges can be handled.
- SIP: Preserve unsupported SDP RTP profiles instead of failing message parsing.
- SIP: Route in-flight SIP requests while waiting for INVITE responses.
- SIP: Distinguish codec report entries by name and rate.
- VoIP: Remove duplicate CANCEL dispatch and handler definition.
- VoIP: Harden dead-call cleanup against missing thread lookup entries.
- SIPTransport: TCP stream connection timeout
- SDP: SDP bandwidth checks to reject constrained SILK offers
- RTP: Cancel callback
- SIP: Quit catching BaseException during register/deregister
- RTP: Fix RTP silence blocking and gap fill
- SIP: Fix dialog state handling for early media
- RTP: Improve timestamp validation for reordered packets
- SIP: Preserve outbound INVITE `From` tags across authenticated retries to keep dialog identity stable.
- SIP: Support full SIP URI and `user@domain` targets for outbound INVITE calls.
- SIP: Handle inbound CANCEL for ringing calls by ending local call state and replying `487 Request Terminated` to the original INVITE.
- SDP: Attach `rtpmap` and `fmtp` attributes to the current media section instead of matching payload IDs globally.
- SDP: Preserve attribute values containing additional colons by splitting SDP attributes only once.
- RTP: Ignore disabled SDP media streams with port `0` during codec compatibility checks and RTP client setup.
- SIP: Preserve deregistration response handling during TCP/TLS shutdown by delaying NSD teardown until after deregister completes.
- SIP: Parse unknown SIP response codes safely instead of crashing on unrecognized status values.
- SIP: Reject unsupported digest auth algorithms and qop modes instead of generating invalid authentication headers.
- SIP: Guard optional fatalCallback execution to avoid NoneType crashes during registration failure handling.
- RTP: Normalize RTP transmit direction handling to always use TransmitType enums internally.
- RTP: Honor recvonly/inactive SDP directions by suppressing outbound RTP audio transmission.
- SIP: Fix digest auth parsing for quoted values containing commas such as `qop="auth,auth-int"`.
- SIP: Correct SDP generation so media attributes are scoped to their corresponding `m=` sections.
- SIP: Reject `SIPS+D2T` NAPTR records for plain `sip:` URI resolution.
- SIP: Support compact `l:` headers when framing SIP TCP/TLS messages.
- RTP: Validate minimum RTP packet length before header parsing.
- RTP: Properly parse and skip RTP header extensions before payload decoding.
- RTP: Strip RTP padding bytes from payloads before codec processing.
- RTP: Validate `telephone-event` payload length and DTMF event ranges.
- VoIP: Fix unsigned 8-bit audio mixing for multi-stream RTP calls.
- VoIP: Make RTP port allocation thread-safe and raise `NoPortsAvailableError` reliably.
- Opus: Return codec-sized silence frames instead of hardcoded 160-byte payloads.
- SILK: Return codec-sized silence frames instead of hardcoded 160-byte payloads.
- Packaging: Correct `Documentation` project URL metadata typo.


## 2.7.9+RFC

- Codecs are now auto-discovered
- Codec implementation modules own their own metadata and payload-type binding
- Cleanup

## 2.7.8+RFC

- Design and implement codec abstraction
- Implement codec priorities
- Set up G.111 codec
- Implement opus codec (through libopus)
- Implement PCMA-WB/PCMU-WB codecs (G.111 extension 1)
- Implement Silk codec via [pysilk](https://github.com/synodriver/pysilk)
- Remove hardcoded 8000hz mono bottleneck (to allow for more advanced audio processing)
- Even more robust error-state handling on stop()/hangup()/unexpected BYE received/unexpected socket closure
- CI/CD automated quality control tests

## 2.7.7+RFC

- SIP Experimental: Add TCP/TLS SIP transport with RFC 3263-compliant resolution (NAPTR/SRV, `;transport=` support, and TLS via SIPS)
- SIP: RFC 3261-compliant compact headers support (supersedes [PyVOIP#309](https://github.com/tayler6000/pyVoIP/pull/309))
- SIP: RFC 5621-compliant multipart SDP support (Robust MIME walker supports recursive data; much supersedes [PyVOIP#259](https://github.com/tayler6000/pyVoIP/pull/259))
- SIP: More robust proxy hangling, update docs
- PyVOIP: Fix phantom calls due to ambiguous INVITE handling.
- RTP: Fix call deny flow crashing (methodically fixes [PyVOIP#243](https://github.com/tayler6000/pyVoIP/issues/243))
- Docs: Fix 'ANSWERED' typo (fixes: [PyVOIP#310](https://github.com/tayler6000/pyVoIP/issues/310))

## 2.7.6+RFC

- SIP (misc): Keep the local contact port explicit (prepare for different transport support)
- SIP: Consolidate Contact header generation and explicitly advertise transport across SIP requests (fixing inconsistent URI handling).
- RTP: Fix codec negotiation to use negotiated payload types and correctly handle dynamic `rtpmap` codecs
- SDP: Implement structural SDP b= bandwidth handling
- VoIP: Fix an outbound INVITE race where final SIP responses could arrive before the call object was registered (Real race encountered)
- Misc: Fix typos and clearer language

## 2.7.5+RFC

- API: Add codec introspection helpers for remote SIP SDP offers and PyVoIP RTP compatibility.
- VoIP: Handle final INVITE failure received while call is not dialing
- RTP: IPv4/IPv6 address-family validation during VoIP renegotiation.
- RTP: avoid duplicate RTP clients / duplicate local RTP socket binds during SDP RTP setup.
- SIP: Improve SIP registration response reliability
- VoIP: Safer renegotiation for service recovery
- VoIP: invalid RTP/media port layouts ought to fail cleanly
- SIP: BYE dialog routing / connected-client handling
- SIP: Implement REGISTER/DEREGISTER 400 Bad Request handling
- SIP: Implement and wire "Supported" header support
- RTP: safe lock handling in RTPPacketManager.write()
- VoIP: Ignore unsupported RTP/SAVP offers and reject invalid RTP audio port layouts before ringing.
- SIP: ACK unmatched final INVITE responses after local call state is gone.
- SIP: Fix unbounded retry/recursion in SIP deregistration
- VoIP: Improve "no compatible codecs" codec-negotiation
- pyVOIP: Overall improve IPv4/IPv6 RTP address-family handling
- SIP: Do check IPv4/IPv6
- SIP&VoIP: If no codecs are compatible then send error to PBX
- SIP: Fix unsupported SIP request handling
- SIP: Fix missing dialog / transaction around inbound BYE and CANCEL.
- Regression rollback: multiline def .. string descriptions
- Unit tests

## 2.7.4+RFC

- SIP: Add outbound PROXY support across REGISTER, INVITE, ACK, CANCEL, BYE, and SUBSCRIBE flows.
- SIP: Route inbound INVITE responses (180/200/486/etc.) back to the requester instead of always using the configured registrar target.
- VoIP: Expose proxy configuration on `VoIPPhone` via `proxy` / `proxyPort`.
- Docs: Add README example for outbound proxy usage.

## 2.7.3+RFC

- PyVOIP-RFC: Fix double free() for ports.
- PyVOIP-RFC: Bump version to 2.x.. (since PyVOIP v2 changes were backported)
- PyVOIP-RFC: Consolidate version handling
- Fix changelog
- SIP: More thorough subscription support
- VoIP: Add DTMF support
- VoIP: Backport authentication support from pyVoIP 2.x.x
- VoIP: Hangup call support
- SIP: call cancellations
- PyVOIP-RFC: Fix workflows
- PyVOIP-RFC: Begin using telemetry for workflows
- Debug: Add detailed outbound call telemetry (Call-ID, session ID, last SIP response, RTP ports, worker thread state)
- Debug: Timestamp pyVoIP debug output and trace INVITE progress/auth/final responses in more detail
- VoIP: Preserve fast final INVITE responses so they can still be inspected after the VoIPCall object is created

## 1.7.0+RFC

- RTP: Fix integer overflow after ~22 min at 20ms for PCMU/PCMA
- RTP: Fix and optimize byte formation (for bytes over 0x7F)
- RTP: Optimized add_bytes() and byte_to_bits() (more Pythonic approach)
- SIP: ACK messages, fix to use proper "To" field based on the original packet received
- SIP: Fix Request-URI when parsing SIP requests
- SIP: Fix 'headers, body, parsing for malformed requests
- SIP: Fix usage of uninitiated variable
- VoIP: Record port used

## 1.7.0+RFC (RC2)

- RTP: Fix RTP sequence/timestamp overflow resulting in corruption
- RTP: Better payload bytes handling
- SIP: Fix: Header parsing was too strict and could cause crashes
- SIP: Fix: Body parsing was too strict and could cause crashes
- SIP: More robust rtpmap / a=fmtp parsing
- SIP: Fix: SIPParseError was never raised in SIPClient.recv()
- setup.py: Update package name and bump version

## 1.7.0+RFC (RC1)

- SIP: Fix crash on unknown request type
- SIP: Implement and wire basic OPTIONS support
- Misc: Update .gitignore
- Misc: lint
