"""Native AMR libraries remain optional across supported desktop platforms."""

import ctypes
from itertools import product
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

import pytest

from rfcvoip import RTP
from rfcvoip import codecs
from rfcvoip.codecs import amr

NB_SYMBOLS = (
    "Encoder_Interface_init",
    "Encoder_Interface_Encode",
    "Encoder_Interface_exit",
    "Decoder_Interface_init",
    "Decoder_Interface_Decode",
    "Decoder_Interface_exit",
)
WB_ENCODER_SYMBOLS = ("E_IF_init", "E_IF_encode", "E_IF_exit")
WB_DECODER_SYMBOLS = ("D_IF_init", "D_IF_decode", "D_IF_exit")
SYMBOLS = {
    "opencore-amrnb": NB_SYMBOLS,
    "vo-amrwbenc": WB_ENCODER_SYMBOLS,
    "opencore-amrwb": WB_DECODER_SYMBOLS,
}


class NativeFunction:
    def __call__(self, *args):
        raise AssertionError("Library discovery must not process audio")


def library(name, missing=()):
    return SimpleNamespace(
        _name=name,
        **{
            symbol: NativeFunction()
            for symbol in SYMBOLS[name]
            if symbol not in missing
        },
    )


@pytest.fixture(autouse=True)
def isolated_library_state(monkeypatch):
    monkeypatch.setattr(amr, "_LIBRARIES", {})
    monkeypatch.setattr(amr, "_AVAILABILITY", {})
    monkeypatch.setattr(amr, "_DLL_DIRECTORIES", {})
    for name in (
        "RFCVOIP_AMR_LIBRARY_DIR",
        "RFCVOIP_AMRNB_LIBRARY",
        "RFCVOIP_AMRWB_LIBRARY",
        "RFCVOIP_AMRWBENC_LIBRARY",
    ):
        monkeypatch.delenv(name, raising=False)


@pytest.mark.parametrize(
    "nb,wb_encoder,wb_decoder", list(product((False, True), repeat=3))
)
def test_only_complete_codecs_are_transmittable(
    nb, wb_encoder, wb_decoder, monkeypatch
):
    installed = {
        name: library(name)
        for name, available in (
            ("opencore-amrnb", nb),
            ("vo-amrwbenc", wb_encoder),
            ("opencore-amrwb", wb_decoder),
        )
        if available
    }
    monkeypatch.setattr(amr, "_library_candidates", lambda name: [name])

    def load(name):
        if name not in installed:
            raise OSError("optional library absent")
        return installed[name]

    monkeypatch.setattr(amr.ctypes, "CDLL", load)

    assert amr.AMRCodec.availability().available is nb
    assert amr.AMRWBCodec.availability().available is (
        wb_encoder and wb_decoder
    )
    assert codecs.codec_can_transmit_audio(RTP.PayloadType.AMR) is nb
    assert codecs.codec_can_transmit_audio(RTP.PayloadType.AMR_WB) is (
        wb_encoder and wb_decoder
    )
    assert codecs.codec_can_transmit_audio(RTP.PayloadType.PCMU)
    for payload, available in (
        (RTP.PayloadType.AMR, nb),
        (RTP.PayloadType.AMR_WB, wb_encoder and wb_decoder),
    ):
        report = codecs.codec_availability(payload)
        assert report["available"] is available
        assert report["can_transmit_audio"] is available
        if not available:
            with pytest.raises(RuntimeError, match="unavailable"):
                codecs.create_codec(payload)


@pytest.mark.parametrize(
    "name,symbol",
    [
        (name, symbol)
        for name, symbols in SYMBOLS.items()
        for symbol in symbols
    ],
)
def test_missing_required_native_symbol_disables_codec(
    name, symbol, monkeypatch
):
    monkeypatch.setattr(amr, "_library_candidates", lambda wanted: [wanted])
    monkeypatch.setattr(
        amr.ctypes,
        "CDLL",
        lambda wanted: library(wanted, (symbol,) if wanted == name else ()),
    )
    cls = amr.AMRCodec if name == "opencore-amrnb" else amr.AMRWBCodec

    result = cls.availability()

    assert not result.available
    assert symbol in result.reason
    assert name not in amr._LIBRARIES


@pytest.mark.parametrize("name", tuple(SYMBOLS))
def test_c_interfaces_preserve_pointer_and_pcm_argument_types(name):
    backend = library(name)
    amr._prepare_api(backend, name)
    pointer = ctypes.c_void_p
    samples = ctypes.POINTER(ctypes.c_int16)
    octets = ctypes.POINTER(ctypes.c_ubyte)
    integer = ctypes.c_int
    contracts = {
        "Encoder_Interface_init": ([integer], pointer),
        "Encoder_Interface_Encode": (
            [pointer, integer, samples, octets, integer],
            integer,
        ),
        "Encoder_Interface_exit": ([pointer], None),
        "Decoder_Interface_init": ([], pointer),
        "Decoder_Interface_Decode": (
            [pointer, octets, samples, integer],
            None,
        ),
        "Decoder_Interface_exit": ([pointer], None),
        "E_IF_init": ([], pointer),
        "E_IF_encode": ([pointer, integer, samples, octets, integer], integer),
        "E_IF_exit": ([pointer], None),
        "D_IF_init": ([], pointer),
        "D_IF_decode": ([pointer, octets, samples, integer], None),
        "D_IF_exit": ([pointer], None),
    }
    for symbol in SYMBOLS[name]:
        function = getattr(backend, symbol)
        arguments, result = contracts[symbol]
        assert function.argtypes == arguments
        assert function.restype is result


@pytest.mark.parametrize(
    "platform,expected",
    (
        ("win32", "libopencore-amrnb-0.dll"),
        ("linux", "libopencore-amrnb.so.0"),
        ("darwin", "libopencore-amrnb.0.dylib"),
    ),
)
def test_system_library_discovery_includes_platform_names(
    platform, expected, monkeypatch
):
    monkeypatch.setattr(
        amr,
        "os",
        SimpleNamespace(
            environ={},
            sys=SimpleNamespace(platform=platform),
        ),
    )
    monkeypatch.setattr(
        amr.ctypes.util, "find_library", lambda name: f"resolved-{name}"
    )

    candidates = amr._library_candidates("opencore-amrnb")

    assert "resolved-opencore-amrnb" in candidates
    assert expected in candidates
    assert len(candidates) == len(set(candidates))
    if platform == "darwin":
        assert str(Path("/opt/homebrew/lib") / expected) in candidates
        assert str(Path("/usr/local/lib") / expected) in candidates


@pytest.mark.parametrize(
    "name,variable",
    (
        ("opencore-amrnb", "RFCVOIP_AMRNB_LIBRARY"),
        ("opencore-amrwb", "RFCVOIP_AMRWB_LIBRARY"),
        ("vo-amrwbenc", "RFCVOIP_AMRWBENC_LIBRARY"),
    ),
)
def test_explicit_native_library_overrides_are_authoritative(
    name, variable, monkeypatch, tmp_path
):
    wanted = str(tmp_path / "chosen-native-library")
    monkeypatch.setenv(variable, wanted)
    monkeypatch.setenv(
        "RFCVOIP_AMR_LIBRARY_DIR", str(tmp_path / "other-directory")
    )
    monkeypatch.setattr(
        amr.ctypes.util,
        "find_library",
        lambda name: pytest.fail("Override must bypass system search"),
    )

    assert amr._library_candidates(name) == [wanted]


def test_configured_library_directory_precedes_system_search(
    monkeypatch, tmp_path
):
    monkeypatch.setenv("RFCVOIP_AMR_LIBRARY_DIR", str(tmp_path))
    monkeypatch.setattr(
        amr.ctypes.util, "find_library", lambda name: "system-found"
    )

    candidates = amr._library_candidates("vo-amrwbenc")

    assert str(tmp_path / "libvo-amrwbenc-0.dll") in candidates
    assert str(tmp_path / "libvo-amrwbenc.so.0") in candidates
    assert str(tmp_path / "libvo-amrwbenc.0.dylib") in candidates
    assert candidates.index(
        str(tmp_path / "libvo-amrwbenc-0.dll")
    ) < candidates.index("system-found")


def test_windows_dll_dependency_directory_remains_registered(
    monkeypatch, tmp_path
):
    native = tmp_path / "libopencore-amrnb-0.dll"
    native.touch()
    registrations = []
    handle = object()
    monkeypatch.setattr(
        amr,
        "os",
        SimpleNamespace(
            name="nt",
            add_dll_directory=lambda path: registrations.append(path)
            or handle,
        ),
    )
    monkeypatch.setattr(amr, "_library_candidates", lambda name: [str(native)])
    backend = library("opencore-amrnb")
    monkeypatch.setattr(amr.ctypes, "CDLL", lambda name: backend)

    assert amr._load_library("opencore-amrnb") is backend
    assert amr._load_library("opencore-amrnb") is backend
    assert registrations == [str(tmp_path.resolve())]
    assert amr._DLL_DIRECTORIES[str(tmp_path.resolve())] is handle


@pytest.mark.parametrize("cls", (amr.AMRCodec, amr.AMRWBCodec))
def test_refresh_retries_after_native_libraries_become_available(
    cls, monkeypatch
):
    installed = False
    attempts = []
    monkeypatch.setattr(amr, "_library_candidates", lambda name: [name])

    def load(name):
        attempts.append(name)
        if not installed:
            raise OSError("not installed yet")
        return library(name)

    monkeypatch.setattr(amr.ctypes, "CDLL", load)
    assert not cls.availability().available
    failed_attempts = len(attempts)
    installed = True
    assert not cls.availability().available
    assert len(attempts) == failed_attempts

    cls.refresh_availability_cache()

    assert cls.availability().available
    assert len(attempts) > failed_attempts


def test_missing_optional_libraries_never_prevent_package_import():
    program = """
import ctypes
def unavailable(*args, **kwargs):
    raise OSError("all optional native libraries unavailable")
ctypes.CDLL = unavailable
import rfcvoip
from rfcvoip import RTP, codecs
assert codecs.codec_class(RTP.PayloadType.AMR) is not None
assert codecs.codec_class(RTP.PayloadType.AMR_WB) is not None
assert RTP.PayloadType.AMR not in codecs.enabled_payload_types()
assert RTP.PayloadType.AMR_WB not in codecs.enabled_payload_types()
assert RTP.PayloadType.PCMU in codecs.enabled_payload_types()
assert codecs.create_codec(RTP.PayloadType.PCMU).encode(bytes([128]) * 160)
"""
    result = subprocess.run(
        [sys.executable, "-c", program],
        cwd=str(Path(__file__).resolve().parents[1]),
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
