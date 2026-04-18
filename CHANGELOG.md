# Changelog


## 2.7.4+RFC

- RTP: safe lock handling in RTPPacketManager.write()
- VoIP: Ignore unsupported RTP/SAVP offers and reject invalid RTP audio port layouts before ringing.
- SIP: ACK unmatched final INVITE responses after local call state is gone.
- SIP: Fix unbounded retry/recursion in SIP deregistration
- VoIP: Improve "no compatible codecs" codec-negotiation 
- pyVOIP: Overall improve IPv4/IPv6 RTP address-family handling
- SIP: Do Check IPv4/IPv6
- SIP&VoIP: If no codecs are compatible then send error to PBX
- SIP: Fix unsupported SIP request handling
- SIP: Fix missing dialog / transaction around inbound BYE and CANCEL.
- SIP: Add outbound proxy support across REGISTER, INVITE, ACK, CANCEL, BYE, and SUBSCRIBE flows.
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
