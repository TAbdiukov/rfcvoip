import audioop

from pyVoIP.codecs.base import CodecAvailability, RTPCodec


class PCMUCodec(RTPCodec):
    name = "PCMU"
    description = "PCMU"
    rate = 8000
    channels = 1
    default_payload_type = 0
    priority_score = 700

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
    priority_score = 650

    @classmethod
    def availability(cls) -> CodecAvailability:
        return CodecAvailability(True, "built-in audioop")

    def encode(self, payload: bytes) -> bytes:
        payload = audioop.bias(payload, 1, -128)
        return audioop.lin2alaw(payload, 1)

    def decode(self, payload: bytes) -> bytes:
        data = audioop.alaw2lin(payload, 1)
        return audioop.bias(data, 1, 128)


class _G711WidebandCoreCodec(RTPCodec):
    """G.711.1 core-layer RTP codec adapter.

    ``PCMU-WB`` and ``PCMA-WB`` are the SDP/RTP names for G.711.1.  PyVoIP's
    public audio API is currently unsigned 8-bit, 8 kHz mono, so this adapter
    transmits only G.711.1 R1 (L0/core) frames and advertises ``mode-set=1``.
    On receive, it extracts the L0 layer from any RFC 5391 mode and ignores
    enhancement layers.  This keeps the packets valid G.711.1 while preserving
    PyVoIP's existing audio API.
    """

    rate = 16000
    channels = 1
    dynamic = True
    default_fmtp = ["mode-set=1"]
    frame_duration_ms = 5
    _mode_index = 1
    _core_frame_bytes = 40
    _mode_frame_bytes = {
        1: 40,
        2: 50,
        3: 50,
        4: 60,
    }

    @classmethod
    def availability(cls) -> CodecAvailability:
        return CodecAvailability(True, "built-in G.711.1 R1 core via audioop")

    @classmethod
    def fmtp_supported(cls, fmtp) -> bool:
        text = " ".join(str(item) for item in (fmtp or []))
        text = text.replace(";", " ")
        mode_set = None
        for token in text.split():
            if "=" not in token:
                continue
            key, value = token.split("=", 1)
            if key.strip().lower() == "mode-set":
                mode_set = value.strip()
                break

        if not mode_set:
            return True

        modes = {mode.strip() for mode in mode_set.split(",") if mode.strip()}
        return "1" in modes

    def _encode_core(self, payload: bytes) -> bytes:
        raise NotImplementedError

    def _decode_core(self, payload: bytes) -> bytes:
        raise NotImplementedError

    def _pad_to_core_frames(self, payload: bytes) -> bytes:
        if not payload:
            payload = b"\x80" * 160

        remainder = len(payload) % self._core_frame_bytes
        if remainder:
            payload += b"\x80" * (self._core_frame_bytes - remainder)
        return payload

    def encode(self, payload: bytes) -> bytes:
        payload = self._pad_to_core_frames(payload)
        core_payload = self._encode_core(payload)
        return bytes([self._mode_index]) + core_payload

    def decode(self, payload: bytes) -> bytes:
        if not payload:
            return b"\x80" * 160

        mode_index = payload[0] & 0x07
        frame_size = self._mode_frame_bytes.get(mode_index)
        if frame_size is None:
            raise ValueError(f"Unsupported G.711.1 mode index {mode_index}")

        audio_data = payload[1:]
        core_payload = bytearray()
        for offset in range(0, len(audio_data) - frame_size + 1, frame_size):
            frame = audio_data[offset : offset + frame_size]
            core_payload.extend(frame[: self._core_frame_bytes])

        if not core_payload:
            return b""
        return self._decode_core(bytes(core_payload))


class PCMUWBCodec(_G711WidebandCoreCodec):
    name = "PCMU-WB"
    description = "PCMU-WB"
    default_payload_type = 112
    priority_score = 900

    def _encode_core(self, payload: bytes) -> bytes:
        payload = audioop.bias(payload, 1, -128)
        return audioop.lin2ulaw(payload, 1)

    def _decode_core(self, payload: bytes) -> bytes:
        data = audioop.ulaw2lin(payload, 1)
        return audioop.bias(data, 1, 128)


class PCMAWBCodec(_G711WidebandCoreCodec):
    name = "PCMA-WB"
    description = "PCMA-WB"
    default_payload_type = 113
    priority_score = 850

    def _encode_core(self, payload: bytes) -> bytes:
        payload = audioop.bias(payload, 1, -128)
        return audioop.lin2alaw(payload, 1)

    def _decode_core(self, payload: bytes) -> bytes:
        data = audioop.alaw2lin(payload, 1)
        return audioop.bias(data, 1, 128)
