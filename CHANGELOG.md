# Changelog

## 2.9.0+RFC

- New project name: rfcvoip, derived from PyVoIP
- Update documentation

## 2.8.8+RFC

- Standalone Telemetry module.
- SIP: Modularize SIPSubscription

## 2.8.7+RFC

- VoIP: Treat any SIP `2xx` INVITE response as success, not only `200 OK`.
- VoIP: Pre-validate DTMF sequences before enqueueing partial sends.
- VoIP: Require at least one assignable audio section before accepting an offer.
- RTP: Fix packet buffer reset so `offset` is updated with rebuilt data.
- RTP: Fix client lifecycle so `NSD` starts false and only becomes true after socket bind succeeds.
- SIPAuth: Add support for `MD5-sess` digest authentication.
- SIP: Add helper to extract request body bytes for digest hashing.
- SIP: Fix SUBSCRIBE response matching to require matching Call-ID, CSeq number, and method.
- SIP: Fix authenticated INVITE retry so `qop=auth-int` hashes the SDP body.
- SIP: Limit auth-header insertion to the first `Content-Length` occurrence.
- SIP: Extend digest auth builder to accept request body bytes.
- SIP: Do not generate request `Via` headers with response-only `received` / filled `rport`
- SIP: Redact authenticated INVITE retry logs.
- SIP: Store generic SDP `a=` attributes on the active media section.
- SIP: fix local BYE CSeq for inbound calls
- SIP: Parse comma-separated `Via` headers correctly.
- SIP: Avoid `Contact` `KeyError` in `gen_sip_version_not_supported()`.
- SIP: Redact sensitive SIP auth data in message summaries
- SIP: Fix wildcard address handling for RTP and SIP transport
- RTP: Fix wildcard address handling for RTP and SIP transport
- RTP: Remove the packet manager rebuild race by making rebuild re-entrant and doing it while holding the buffer lock.
- Codec: Make module discovery deterministic
- Misc: Spelling mistakes
- Packaging: Replace `exec()`-based version loading in `setup.py` with safe AST parsing for `__version__`
- Documentation: Replace `exec()`-based version loading in `docs/conf.py` with safe AST parsing for `__version__`
- SIPAuth: Redact folded `Authorization` and `Proxy-Authorization` continuation lines in SIP debug logs
- SIP: Reject SIP messages whose body is shorter than the declared `Content-Length`
- SIP: Preserve original INVITE request URI for non-2xx ACK generation
- SIP: Improve INVITE transaction matching by validating both `Call-ID` and `CSeq`
- SIP: Store outbound INVITE target URI in invite debug state for delayed ACK handling
- SIP: Attach original INVITE request URI to queued final INVITE responses
- SIP: Reuse original INVITE request URI for authentication retries and delayed ACKs
- SIPTransport: Normalize bracketed IPv6 addresses before socket operations
- RTP: Normalize bracketed IPv6 RTP addresses before socket operations
- SIP: Normalize bracketed IPv6 SDP addresses before address-family detection
- VoIP: Improve ACK generation for late or queued INVITE final responses
- VoIP: Preserve ACK routing correctness for unmatched outbound INVITE responses
- VoIP: Fix SDP media sections inheriting connection lines from unrelated media sections.
- RTP: Add guard against starting an RTP client twice and clean up sockets on start failure.
- SIP: Preserve explicitly configured registrar/user URI ports, including explicit `:5060`.

## 2.8.6+RFC

- Race-proof hardened thread-safety bounded concurrency
- Replace Timer-based workers with Thread-based execution while preserving delayed registration refresh behavior.
- SIP: Implement a response builder with method-specific body/header additions
- SIPAuth: Modularize SIP digest authentication into dedicated helpers for easier maintenance and reuse.
- SIPAuth: Add RFC 7616 digest authentication support for `SHA-256`, `SHA-256-sess`, `SHA-512-256`, and `SHA-512-256-sess` while preserving MD5 compatibility.
- SIPAuth: Negotiate the strongest mutually supported digest algorithm with preference ordering `SHA-512-256` > `SHA-256` > `MD5`.
- SIPAuth: Add reusable nonce generation, nonce validation, digest response computation, and server-side verification helpers.
- SIPAuth: Support stored H(A1) credential hashes for both plain and `-sess` digest algorithms.
- SIPAuth: Add digest authentication tests covering RFC 7616 vectors, nonce validation, strongest-algorithm selection, and session-algorithm verification.

## 2.8.5+RFC

- SIP: Redact SIP `Authorization` and `Proxy-Authorization` headers from failed REGISTER debug logs.
- SIP: Remove unexpected socket blocking reset after SIP response handling.
- SIP: Unfold folded SIP headers before parsing header fields.
- SIP: Parse SIP URI user/host separately from URI parameters in `From` and `To` headers.
- SIP: Raise `SIPParseError` for malformed SDP `o=`, `t=`, and `r=` lines.
- SIP: Raise `SIPParseError` for malformed or negative Content-Length headers.
- SIP: Honor session-level SDP direction attributes when creating media sections.
- SIP: Report non-auth REGISTER failures as SIP request errors instead of credential errors.
- SIP: Reject duplicate but conflicting `Content-Length` headers
- SIP: Make registration math more robust
- SIP: Harden SIP header parsing by rejecting malformed lines and duplicate singleton headers instead of silently ignoring them.
- SIP: Improve SIP TCP/TLS framing validation with strict `Content-Length` handling and malformed stream detection.
- SIP: Reject malformed SDP body lines and unsupported multipart messages containing multiple SDP payloads.
- SIP: Preserve duplicate list-style SIP headers (for example `Allow` and `Supported`) instead of dropping later values.
- SIP: Improve `From`/`To` SIP URI parsing for quoted display names and parameterized SIP URIs.
- SIP: Add parser hardening tests covering malformed headers, SDP validation, multipart SDP rejection, and stream framing edge cases.
- VoIP: Fix phone shutdown state by clearing `VoIPPhone.NSD` when `stop()` completes.
- VoIP: Scope RTP address-family validation to enabled audio media sections only.
- VoIP: Reject inbound calls cleanly when no RTP ports are available instead of blocking SIP receive handling
- VoIP: Treat unsupported video SDP as non-fatal during audio call negotiation
- VoIP: Restrict RTP port allocation validation to negotiated audio media sections
- VoIP: Convert RTP port reservation failures into RTP negotiation errors for proper SIP rejection handling
- VoIP: Preserve contiguous RTP port validation while avoiding receive-loop deadlocks
- RTP/VoIP: Improve robustness of RTP media setup error propagation during inbound negotiation
- RTP: Clamp invalid transmit delay reduction values to prevent negative sleep intervals

## 2.8.4+RFC

- Implement multi-connection RTP media sections

## 2.8.3+RFC

- RTP: accept `telephone-event` regardless of advertised clock rate
- RTP: reject SDP channel mismatches while still allowing Opus /2
- SIP: Advertise local SDP codec capabilities in OPTIONS responses when possible
- VoIP: fail RTP negotiation clearly instead of creating answered calls with no media
- VoIP: release only the port reserved by a failed outbound call setup
- VoIP: reject outbound answers with unusable RTP connection data before marking the call answered
- VoIP: honor remote SDP direction when creating RTP clients
- SIP: send raw SIP-version error responses back to the packet source
- SIP: Ignore SIP keepalive packets during transaction waits and receive loop
- Misc: normalize version_info

## 2.8.2+RFC

- Codec: Implement per-instance codec ordering.
- util/SIPTransport: Preserve original socket timeout state when temporarily switching sockets to non-blocking mode
- SIPTransport: IPv6-safe UDP send addresses in `SIPConnection`
- RTP: Reject SDP codec name lookups with mismatched RTP clock rates
- RTP: Harden RTP packet buffering against huge timestamp gaps that could cause excessive memory allocation.
- VoIP/RTP: Apply per-phone codec priorities consistently during SDP offer generation and RTP codec negotiation
- VoIP: Release incoming call session ids on finalization
- VoIP: Make codec priority overrides instance-local instead of mutating global process state
- VoIP: Include per-phone codec priority scores in supported/local codec reporting
- VoIP: Fix media-level SDP `c=` handling
- VoIP: Fix potential RTP port leak in `VoIPPhone.call()`
- VoIP: Handle malformed `200 OK` ingestion
- SIP: Fix bug where `sips:` accepts invalid `transport=` values as TLS
- SIP: Harden SDP `m=` parsing against extra spaces and malformed lines
- SIP: Handle CRLF keepalives
- SIP: Preserve extra `Via` parameters in responses
- SIP: Make Counter increments/current reads thread-safe with locking
- SIP: Reply 200 OK to CANCEL before invoking call teardown callbacks
- SIP: Use local Contact header in 486 Busy Here responses instead of echoing remote Contact
- SIP: Fix a shutdown race in `SIPClient.recv`
- SIP: More robust parsing in `parse_sip_message()`
- SIP: Prevent malformed `a=rtpmap` lines from throwing an index error during parsing
- SIP: SDP payload resolution prefer `rtpmap` mappings over static RTP payload numbers.
- SIP: Preserved media-level SDP direction attributes on their owning `m=` sections.

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
- SIP: RFC 3261-compliant compact headers support (supersedes [PyVoIP#309](https://github.com/tayler6000/PyVoIP/pull/309))
- SIP: RFC 5621-compliant multipart SDP support (Robust MIME walker supports recursive data; much supersedes [PyVoIP#259](https://github.com/tayler6000/PyVoIP/pull/259))
- SIP: More robust proxy hangling, update docs
- PyVoIP: Fix phantom calls due to ambiguous INVITE handling.
- RTP: Fix call deny flow crashing (methodically fixes [PyVoIP#243](https://github.com/tayler6000/PyVoIP/issues/243))
- Docs: Fix 'ANSWERED' typo (fixes: [PyVoIP#310](https://github.com/tayler6000/PyVoIP/issues/310))

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
- PyVoIP: Overall improve IPv4/IPv6 RTP address-family handling
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

- PyVoIP-RFC: Fix double free() for ports.
- PyVoIP-RFC: Bump version to 2.x.. (since PyVoIP v2 changes were backported)
- PyVoIP-RFC: Consolidate version handling
- Fix changelog
- SIP: More thorough subscription support
- VoIP: Add DTMF support
- VoIP: Backport authentication support from PyVoIP 2.x.x
- VoIP: Hangup call support
- SIP: call cancellations
- PyVoIP-RFC: Fix workflows
- PyVoIP-RFC: Begin using telemetry for workflows
- Debug: Add detailed outbound call telemetry (Call-ID, session ID, last SIP response, RTP ports, worker thread state)
- Debug: Timestamp PyVoIP debug output and trace INVITE progress/auth/final responses in more detail
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
