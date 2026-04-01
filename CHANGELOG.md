# Changelog

## 1.7.0+RFC

- Debug: Add detailed outbound call telemetry (Call-ID, session ID, last SIP response, RTP ports, worker thread state)
- Debug: Timestamp pyVoIP debug output and trace INVITE progress/auth/final responses in more detail
- VoIP: Preserve fast final INVITE responses so they can still be inspected after the VoIPCall object is created


- RTP: Fix integer overflow after ~22 min at 20ms for PCMU/PCMA
- RTP: Fix and optimize byte formation (for bytes over 0x7F)
- RTP: Optimized add_bytes() and byte_to_bits() (more Pythonic approach)
- SIP: ACK messages, fix to use proper "To" field based on the original packet received
- SIP: Fix Request-URI when parsing SIP requests
- SIP: Fix 'headers, body, parsing for malformed requests
- SIP: Fix usage of uninitiated variable
- VoIP: Record port used

- RTP: Fix RTP sequence/timestamp overflow resulting in corruption
- RTP: Better payload bytes handling
- SIP: Fix: Header parsing was too strict and could cause crashes
- SIP: Fix: Body parsing was too strict and could cause crashes
- SIP: More robust rtpmap / a=fmtp parsing
- SIP: Fix: SIPParseError was never raised in SIPClient.recv()
- setup.py - Update package name and bump version

- SIP: Fix crash on unknown request type
- SIP: Implement and wire basic OPTIONS support
- Misc: Update .gitignore
- Misc: lint
