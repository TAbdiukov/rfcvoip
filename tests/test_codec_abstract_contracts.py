from inspect import isabstract

import pytest

from rfcvoip.codecs.base import CodecNotImplementedError
from rfcvoip.codecs.g711 import (
    PCMAWBCodec,
    PCMUWBCodec,
    _G711WidebandCoreCodec,
)


class _DelegatingWidebandCodec(_G711WidebandCoreCodec):
    name = "test-g711-wideband"

    def _encode_core_pcm16(self, payload: bytes) -> bytes:
        return super()._encode_core_pcm16(payload)

    def _decode_core_pcm16(self, payload: bytes) -> bytes:
        return super()._decode_core_pcm16(payload)


def test_g711_wideband_core_is_abstract() -> None:
    assert isabstract(_G711WidebandCoreCodec)

    with pytest.raises(TypeError, match="abstract class"):
        _G711WidebandCoreCodec()


def test_g711_wideband_codecs_implement_core_contract() -> None:
    assert not isabstract(PCMUWBCodec)
    assert not isabstract(PCMAWBCodec)


@pytest.mark.parametrize(
    "operation",
    ("_encode_core_pcm16", "_decode_core_pcm16"),
)
def test_g711_core_errors_include_operation_context(operation: str) -> None:
    codec = _DelegatingWidebandCodec()

    with pytest.raises(CodecNotImplementedError) as exc_info:
        getattr(codec, operation)(b"")

    assert exc_info.value.codec is codec
    assert exc_info.value.operation == operation
    assert str(exc_info.value) == (
        "test-g711-wideband codec adapter does not implement "
        f"{operation}()."
    )