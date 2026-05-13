import pyVoIP
from pyVoIP import RTP
from pyVoIP.RTP import RTPPacketManager
from pyVoIP.SIP import SIPMessage
from pyVoIP.VoIP.VoIP import _payload_type_from_media_method


def test_rtp_packet_manager_drops_huge_forward_gap():
    pm = RTPPacketManager()
    pm.write(1000, b"a" * 160)
    pm.write(10**9, b"b" * 160)

    assert pm.offset == 10**9
    assert pm.available() <= 160


def test_voip_payload_mapping_prefers_rtpmap_over_static_number():
    media = {
        "attributes": {
            "9": {
                "rtpmap": {
                    "name": "PCMU",
                    "frequency": "8000",
                    "encoding": None,
                }
            }
        }
    }

    assert _payload_type_from_media_method(media, "9") == RTP.PayloadType.PCMU


def test_media_level_direction_is_stored_on_media_section():
    sdp = (
        b"v=0\r\n"
        b"o=- 1 1 IN IP4 127.0.0.1\r\n"
        b"s=-\r\n"
        b"c=IN IP4 127.0.0.1\r\n"
        b"t=0 0\r\n"
        b"m=audio 4000 RTP/AVP 0\r\n"
        b"a=sendonly\r\n"
    )
    raw = (
        b"SIP/2.0 200 OK\r\n"
        b"Via: SIP/2.0/UDP 127.0.0.1:5060\r\n"
        b"From: <sip:a@example.com>;tag=a\r\n"
        b"To: <sip:b@example.com>;tag=b\r\n"
        b"Call-ID: x\r\n"
        b"CSeq: 1 INVITE\r\n"
        b"Content-Type: application/sdp\r\n"
        b"Content-Length: " + str(len(sdp)).encode("ascii") + b"\r\n\r\n" + sdp
    )

    message = SIPMessage(raw)

    assert message.body["m"][0]["transmit_type"] == pyVoIP.RTP.TransmitType.SENDONLY

