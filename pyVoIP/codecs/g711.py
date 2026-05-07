import audioop

from pyVoIP.codecs.base import CodecAvailability, RTPCodec


class PCMUCodec(RTPCodec):
    name = "PCMU"
    description = "PCMU"
    rate = 8000
    channels = 1
    default_payload_type = 0

    @classmethod
    def availability(cls) -> CodecAvailability:
        return CodecAvailability(True, "built-in audioop")

    def encode(self, payload: bytes) -> bytes:
        payload = audioop.bias(payload, 1, -128)
        return audioop.lin2ulaw(payload, 1)

    def decode(self, payload: bytes) -> bytes:
        data = audioop.ulaw2lin(payload, 1)
        return audioop.bias(data, 1, 128)


class PCMACodec(RTPCodec):
    name = "PCMA"
    description = "PCMA"
    rate = 8000
    channels = 1
    default_payload_type = 8

    @classmethod
    def availability(cls) -> CodecAvailability:
        return CodecAvailability(True, "built-in audioop")

    def encode(self, payload: bytes) -> bytes:
        payload = audioop.bias(payload, 1, -128)
        return audioop.lin2alaw(payload, 1)

    def decode(self, payload: bytes) -> bytes:
        data = audioop.alaw2lin(payload, 1)
        return audioop.bias(data, 1, 128)
