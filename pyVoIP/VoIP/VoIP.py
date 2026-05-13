from enum import Enum
from pyVoIP import SIP, RTP
from pyVoIP.VoIP.status import PhoneStatus
from threading import Timer, Lock
from typing import Any, Callable, Dict, List, Optional
import audioop
import io
import pyVoIP
import random
import time
import warnings


__all__ = [
    "CallState",
    "InvalidRangeError",
    "InvalidStateError",
    "NoPortsAvailableError",
    "VoIPCall",
    "VoIPPhone",
]

debug = pyVoIP.debug


def _media_fmtp_settings(media: Dict[str, Any], method: Any) -> List[str]:
    attributes = media.get("attributes", {}).get(str(method), {})
    if not isinstance(attributes, dict):
        return []

    fmtp = attributes.get("fmtp", {})
    if isinstance(fmtp, dict):
        return [str(setting) for setting in fmtp.get("settings", [])]
    return []


def _payload_type_from_media_method(
    media: Dict[str, Any],
    method: Any,
) -> RTP.PayloadType:
    try:
        return RTP.PayloadType(int(method))
    except (TypeError, ValueError):
        pass

    attributes = media.get("attributes", {}).get(str(method), {})
    if not isinstance(attributes, dict):
        raise KeyError(method)

    rtpmap = attributes.get("rtpmap", {})
    if not isinstance(rtpmap, dict):
        raise KeyError("rtpmap")

    return RTP.payload_type_from_name(
        str(rtpmap.get("name") or ""),
        rate=rtpmap.get("frequency"),
        channels=rtpmap.get("encoding"),
    )


def _media_uses_supported_rtp_profile(media: Dict[str, Any]) -> bool:
    protocol = media.get("protocol")
    return protocol in (RTP.RTPProtocol.AVP, RTP.RTPProtocol.AVP.value)


def _media_port_is_enabled(media: Dict[str, Any]) -> bool:
    try:
        return int(media.get("port", 0)) > 0
    except (TypeError, ValueError):
        return False


class InvalidRangeError(Exception):
    pass


class InvalidStateError(Exception):
    pass


class NoPortsAvailableError(Exception):
    pass


class CallState(Enum):
    DIALING = "DIALING"
    RINGING = "RINGING"
    ANSWERED = "ANSWERED"
    ENDED = "ENDED"


class VoIPCall:
    def __init__(
        self,
        phone: "VoIPPhone",
        callstate: CallState,
        request: SIP.SIPMessage,
        session_id: int,
        myIP: str,
        ms: Optional[Dict[int, RTP.PayloadType]] = None,
        sendmode="sendonly",
    ):
        self.state = callstate
        self.phone = phone
        self.sip = self.phone.sip
        self.request = request
        self.call_id = request.headers["Call-ID"]
        self.remote_sip_message = (
            request if callstate == CallState.RINGING else None
        )
        self.session_id = str(session_id)
        self.myIP = myIP
        self.rtpPortHigh = self.phone.rtpPortHigh
        self.rtpPortLow = self.phone.rtpPortLow
        self.sendmode = sendmode

        self.dtmfLock = Lock()
        self.dtmf = io.StringIO()

        self.RTPClients: List[RTP.RTPClient] = []

        self.connections = 0
        self.audioPorts = 0
        self.videoPorts = 0

        # Type checker being weird with this variable.
        # Appears to be because this variable is used differently depending
        # on whether we received or originated the call.
        # Will need to refactor the code later to properly type this.
        self.assignedPorts: Any = {}

        if callstate == CallState.RINGING:
            audio = []
            video = []
            for x in self.request.body["c"]:
                self.connections += x["address_count"]
            for x in self.request.body["m"]:
                if x["type"] == "audio":
                    if (
                        not _media_uses_supported_rtp_profile(x)
                        or not _media_port_is_enabled(x)
                    ):
                        continue
                    self.audioPorts += x["port_count"]
                    audio.append(x)
                elif x["type"] == "video":
                    self.videoPorts += x["port_count"]
                    video.append(x)
                else:
                    warnings.warn(
                        f"Unknown media description: {x['type']}", stacklevel=2
                    )

            # Ports Adjusted is used in case of multiple m tags.
            if len(audio) > 0:
                audioPortsAdj = self.audioPorts / len(audio)
            else:
                audioPortsAdj = 0
            if len(video) > 0:
                videoPortsAdj = self.videoPorts / len(video)
            else:
                videoPortsAdj = 0

            if not (
                (audioPortsAdj == self.connections or self.audioPorts == 0)
                and (videoPortsAdj == self.connections or self.videoPorts == 0)
            ):
                raise RTP.RTPParseError("Unable to assign ports for RTP.")
                return

            for i in request.body["m"]:
                if i.get("type") != "audio":
                    continue
                if (
                    not _media_uses_supported_rtp_profile(i)
                    or not _media_port_is_enabled(i)
                ):
                    continue
                assoc = {}
                e = False
                pt = None
                for x in i["methods"]:
                    try:
                        p = _payload_type_from_media_method(i, x)
                        assoc[int(x)] = p
                    except ValueError:
                        # Sometimes rtpmap raises a KeyError because fmtp
                        # is set instead.
                        try:
                            pt = i["attributes"][x]["rtpmap"]["name"]
                        except KeyError:
                            pt = x
                        warnings.warn(
                            f"RTP Payload type {pt} not found.",
                            stacklevel=20,
                        )
                        # Resets the warning filter so this warning will
                        # come up again if it happens.  However, this
                        # also resets all other warnings.
                        warnings.simplefilter("default")
                        p = RTP.PayloadType("UNKNOWN")
                        assoc[int(x)] = p
                    except KeyError:
                        # fix issue 42
                        # When rtpmap is not found, also set the found
                        # element to UNKNOWN
                        warnings.warn(
                            f"RTP KeyError {x} not found.", stacklevel=20
                        )
                        p = RTP.PayloadType("UNKNOWN")
                        assoc[int(x)] = p

                if e:
                    if pt:
                        raise RTP.RTPParseError(
                            f"RTP Payload type {pt} not found."
                        )
                    raise RTP.RTPParseError(
                        "RTP Payload type could not be derived from SDP."
                    )

                # Make sure codecs are compatible.
                codecs = {}
                has_transmittable_codec = False
                session_bandwidth = request.body.get("b", [])
                media_bandwidth = i.get("bandwidth", [])

                for m in assoc:
                    codec = assoc[m]
                    if (
                        codec in pyVoIP.RTPCompatibleCodecs
                        and RTP.codec_fmtp_supported(
                            codec,
                            _media_fmtp_settings(i, m),
                        )
                        and SIP.codec_bandwidth_supported(
                            codec,
                            session_bandwidth=session_bandwidth,
                            media_bandwidth=media_bandwidth,
                        )
                    ):

                        codecs[m] = codec
                        if RTP.is_transmittable_audio_codec(codec):
                            has_transmittable_codec = True

                if not has_transmittable_codec:
                    continue

                if not codecs:
                    continue

                codecs = RTP.prioritize_payload_type_map(codecs)

                port = self.phone.request_port()
                self.assignedPorts[port] = codecs
                self.create_rtp_clients(
                    codecs, self.myIP, port, request, i["port"]
                )

            if len(self.assignedPorts) == 0:
                raise RTP.RTPParseError(
                    "No transmittable audio codec negotiated."
                )
        elif callstate == CallState.DIALING:
            if ms is None:
                raise RuntimeError(
                    "Media assignments are required when "
                    + "initiating a call"
                )
            self.ms = ms
            for m in self.ms:
                self.port = m
                self.assignedPorts[m] = self.ms[m]

    def createRTPClients(
        self,
        codecs: Dict[int, RTP.PayloadType],
        ip: str,
        port: int,
        request: SIP.SIPMessage,
        baseport: int,
    ) -> None:
        warnings.warn(
            "createRTPClients is deprecated due to PEP8 "
            + "compliance. Use create_rtp_clients instead.",
            DeprecationWarning,
            stacklevel=2,
        )
        return self.create_rtp_clients(codecs, ip, port, request, baseport)

    def create_rtp_clients(
        self,
        codecs: Dict[int, RTP.PayloadType],
        ip: str,
        port: int,
        request: SIP.SIPMessage,
        baseport: int,
    ) -> None:
        debug(
            f"Creating RTP client(s) for {self.call_id}",
            "Call RTP setup "
            + f"call_id={self.call_id} local={ip}:{port} "
            + f"remote_base={baseport} codecs="
            + ",".join(f"{pt}:{codec}" for pt, codec in codecs.items()),
        )

        # RTPClient owns one local UDP socket; do not bind it twice.
        if any(
            client.inIP == ip and client.inPort == port
            for client in self.RTPClients
        ):
            debug(
                f"Skipping duplicate RTP client for {self.call_id}",
                "Skipping duplicate RTP client setup "
                + f"call_id={self.call_id} local={ip}:{port}",
            )
            return

        if not request.body.get("c"):
            return

        phone = getattr(self, "phone", None)
        audio_sample_rate = getattr(phone, "audio_sample_rate", None)
        audio_sample_width = getattr(phone, "audio_sample_width", 1)
        audio_channels = getattr(phone, "audio_channels", 1)

        rtp_kwargs = {"dtmf": self.dtmf_callback}

        # Keep lightweight tests and older RTPClient-compatible doubles working:
        # the real RTPClient defaults are already equivalent for the common
        # unsigned 8-bit mono auto-rate case, so only pass the newer audio
        # format keywords when the phone has a non-default public format.
        if (
            audio_sample_rate is not None
            or audio_sample_width != 1
            or audio_channels != 1
        ):
            rtp_kwargs.update(
                audio_sample_rate=audio_sample_rate,
                audio_sample_width=audio_sample_width,
                audio_channels=audio_channels,
            )

        remote_ip = request.body["c"][0]["address"]
        c = RTP.RTPClient(
            codecs,
            ip,
            port,
            remote_ip,
            baseport,
            self.sendmode,
            **rtp_kwargs,
        )
        self.RTPClients.append(c)

    def dtmfCallback(self, code: str) -> None:
        warnings.warn(
            "dtmfCallback is deprecated due to PEP8 compliance. "
            + "Use dtmf_callback instead.",
            DeprecationWarning,
            stacklevel=2,
        )
        return self.dtmf_callback(code)

    def __del__(self):
        try:
            phone = getattr(self, "phone", None)
            ports = list(getattr(self, "assignedPorts", {}).keys())
            if phone is None or not ports:
                return

            lock = getattr(phone, "portsLock", None)
            assigned_ports = getattr(phone, "assignedPorts", None)
            if lock is None or assigned_ports is None:
                return

            # Interpreter shutdown is a poor place to do blocking cleanup.
            # If another thread is already cleaning up, just let that win.
            if not lock.acquire(blocking=False):
                return
            try:
                for port in ports:
                    if port in assigned_ports:
                        assigned_ports.remove(port)
            finally:
                lock.release()
        except Exception:
            pass

    def dtmf_callback(self, code: str) -> None:
        with self.dtmfLock:
            bufferloc = self.dtmf.tell()
            self.dtmf.seek(0, 2)
            self.dtmf.write(code)
            self.dtmf.seek(bufferloc, 0)

    def getDTMF(self, length=1) -> str:
        warnings.warn(
            "getDTMF is deprecated due to PEP8 compliance. "
            + "Use get_dtmf instead.",
            DeprecationWarning,
            stacklevel=2,
        )
        return self.get_dtmf(length)

    def get_dtmf(self, length=1) -> str:
        with self.dtmfLock:
            packet = self.dtmf.read(length)
            return packet

    def sendDTMF(self, digits: str) -> bool:
        warnings.warn(
            "sendDTMF is deprecated due to PEP8 compliance. Use send_dtmf instead.",
            DeprecationWarning,
            stacklevel=2,
        )
        return self.send_dtmf(digits)

    def send_dtmf(self, digits: str) -> bool:
        digits = "".join(ch for ch in str(digits or "").upper() if not ch.isspace())
        if not digits:
            return False

        sent = False
        for digit in digits:
            digit_sent = False
            for client in self.RTPClients:
                sender = getattr(client, "send_dtmf", None)
                if not callable(sender):
                    continue
                try:
                    result = sender(digit)
                except Exception:
                    continue
                if result is not False:
                    digit_sent = True
                    sent = True

            if not digit_sent:
                return sent

        if sent:
            debug(
                f"Queued outbound DTMF for {self.call_id}",
                f"Call {self.call_id}: queued outbound DTMF digits={digits}",
            )
        return sent

    def send_dtmf_sequence(self, digits: str) -> bool:
        return self.send_dtmf(digits)

    def active_codecs(self) -> List[Dict[str, Any]]:
        """Return codecs currently selected by this call's RTP client(s)."""
        active = []
        for client in self.RTPClients:
            selected_codec_info = getattr(client, "selected_codec_info", None)
            if callable(selected_codec_info):
                active.append(selected_codec_info())
        return active

    def remote_supported_codecs(self) -> List[Dict[str, Any]]:
        """Return codecs advertised by the remote endpoint's SDP."""
        if self.remote_sip_message is None:
            return []
        return self.remote_sip_message.supported_codecs()

    def codec_support_report(self) -> Dict[str, Any]:
        """Compare the remote SDP codecs against PyVoIP support."""
        active_codecs = self.active_codecs()
        if self.remote_sip_message is None:
            pyvoip_codecs = RTP.supported_codecs()
            return {
                "remote": [],
                "pyvoip": pyvoip_codecs,
                "local": pyvoip_codecs,
                "compatible": [],
                "unsupported": [],
                "good": [],
                "missing": [],
                "pyvoip_missing_from_remote": pyvoip_codecs,
                "remote_has_sdp": False,
                "transmittable_audio": [],
                "call_compatible": [],
                "can_start_call": None,
                "active_codecs": active_codecs,
            }
        report = self.remote_sip_message.codec_support_report()
        report["active_codecs"] = active_codecs
        return report

    def _finalize_ended_call(self) -> None:
        try:
            self.phone.release_ports(call=self)
        except Exception:
            pass

        if self.call_id in self.phone.calls:
            del self.phone.calls[self.call_id]

    def genMs(self) -> Dict[int, Dict[int, RTP.PayloadType]]:
        warnings.warn(
            "genMs is deprecated due to PEP8 compliance. "
            + "Use gen_ms instead.",
            DeprecationWarning,
            stacklevel=2,
        )
        return self.gen_ms()

    def gen_ms(self) -> Dict[int, Dict[int, RTP.PayloadType]]:
        """
        Generate m SDP attribute for answering originally and
        for re-negotiations.
        """
        m = {}
        for x in self.RTPClients:
            x.start()
            m[x.inPort] = x.assoc

        return m

    def renegotiate(self, request: SIP.SIPMessage) -> None:
        if not self.phone._has_compatible_rtp_address_family(request):
            message = self.sip.gen_response(
                request, SIP.SIPStatus.NOT_ACCEPTABLE_HERE
            )
            self.sip.send_response(request, message)
            return

        m = {}
        for x in self.RTPClients:
            m[x.inPort] = x.assoc

        message = self.sip.gen_answer(
            request, self.session_id, m, self.sendmode
        )
        self.sip.send_response(request, message)
        for i in request.body["m"]:
            if i.get("type") != "audio":
                continue
            if (
                not _media_uses_supported_rtp_profile(i)
                or not _media_port_is_enabled(i)
            ):
                continue
            for ii, client in zip(
                range(len(request.body["c"])), self.RTPClients
            ):
                client.outIP = request.body["c"][ii]["address"]
                client.outPort = i["port"] + ii

    def answer(self) -> None:
        if self.state != CallState.RINGING:
            raise InvalidStateError("Call is not ringing")
        m = self.gen_ms()
        debug(
            f"Answering call {self.call_id}",
            "Call answer requested "
            + f"call_id={self.call_id} local_rtp_ports={list(m.keys())}",
        )

        message = self.sip.gen_answer(
            self.request, self.session_id, m, self.sendmode
        )
        self.sip.send_response(self.request, message)
        self.state = CallState.ANSWERED
        debug(
            f"Call {self.call_id} answered",
            f"Call {self.call_id}: state -> ANSWERED",
        )

    def answered(self, request: SIP.SIPMessage) -> None:
        if self.state == CallState.ANSWERED:
            return
        if self.state not in (CallState.DIALING, CallState.RINGING):
            return
        debug(
            request.summary(),
            "Call answered by remote party "
            + f"call_id={self.call_id} contact={request.headers.get('Contact')}",
        )

        self.remote_sip_message = request
        for i in request.body["m"]:
            if i.get("type") != "audio":
                continue
            if (
                not _media_uses_supported_rtp_profile(i)
                or not _media_port_is_enabled(i)
            ):
                continue
            assoc = {}
            for x in i["methods"]:
                try:
                    assoc[int(x)] = _payload_type_from_media_method(i, x)
                except (KeyError, ValueError):
                    warnings.warn(
                        f"RTP Payload type {x} could not be derived from SDP.",
                        stacklevel=2,
                    )
                    continue

            if not assoc:
                continue

            codecs = {}
            has_transmittable_codec = False
            session_bandwidth = request.body.get("b", [])
            media_bandwidth = i.get("bandwidth", [])
            for payload_type, codec in assoc.items():
                if (
                    codec in pyVoIP.RTPCompatibleCodecs
                    and RTP.codec_fmtp_supported(
                        codec,
                        _media_fmtp_settings(i, payload_type),
                    )
                    and SIP.codec_bandwidth_supported(
                        codec,
                        session_bandwidth=session_bandwidth,
                        media_bandwidth=media_bandwidth,
                    )
                ):
                    codecs[payload_type] = codec
                    if RTP.is_transmittable_audio_codec(codec):
                        has_transmittable_codec = True

            if not has_transmittable_codec:
                continue

            codecs = RTP.prioritize_payload_type_map(codecs)

            self.create_rtp_clients(
                codecs, self.myIP, self.port, request, i["port"]
            )

        for x in self.RTPClients:
            x.start()
        self.request.headers["Contact"] = request.headers["Contact"]
        self.request.headers["To"]["tag"] = request.headers["To"]["tag"]
        self.state = CallState.ANSWERED
        debug(
            f"Call {self.call_id} connected",
            f"Call {self.call_id}: state -> ANSWERED remote_contact={request.headers.get('Contact')}",
        )

    def notFound(self, request: SIP.SIPMessage) -> None:
        warnings.warn(
            "notFound is deprecated due to PEP8 compliance. "
            + "Use not_found instead.",
            DeprecationWarning,
            stacklevel=2,
        )
        return self.not_found(request)

    def not_found(self, request: SIP.SIPMessage) -> None:
        if self.state not in (CallState.DIALING, CallState.RINGING):
            debug(
                "Ignoring late not found response for call "
                + f"{self.call_id} in state {self.state}"
            )
            return

        for x in self.RTPClients:
            x.stop()
        self.state = CallState.ENDED
        self._finalize_ended_call()
        debug("Call not found and terminated")
        warnings.warn(
            f"The number '{request.headers['To']['number']}' "
            + "was not found.  Did you call the wrong number?  "
            + "CallState set to CallState.ENDED.",
            stacklevel=20,
        )
        # Resets the warning filter so this warning will
        # come up again if it happens.  However, this
        # also resets all other warnings.
        warnings.simplefilter("default")

    def unavailable(self, request: SIP.SIPMessage) -> None:
        if self.state not in (CallState.DIALING, CallState.RINGING):
            debug(
                "Ignoring late unavailable response for call "
                + f"{self.call_id} in state {self.state}"
            )
            return

        for x in self.RTPClients:
            x.stop()
        self.state = CallState.ENDED
        self._finalize_ended_call()
        debug("Call unavailable and terminated")
        warnings.warn(
            f"The number '{request.headers['To']['number']}' "
            + "was unavailable.  CallState set to CallState.ENDED.",
            stacklevel=20,
        )
        # Resets the warning filter so this warning will
        # come up again if it happens.  However, this
        # also resets all other warnings.
        warnings.simplefilter("default")

    def deny(self) -> None:
        if self.state != CallState.RINGING:
            raise InvalidStateError("Call is not ringing")
        debug(
            f"Denying call {self.call_id}",
            f"Call {self.call_id}: denying incoming call",
        )

        message = self.sip.gen_busy(self.request)
        self.sip.send_response(self.request, message)
        for x in self.RTPClients:
            x.stop()
        self.state = CallState.ENDED
        self._finalize_ended_call()

    def cancel(self) -> None:
        if self.state not in (CallState.DIALING, CallState.RINGING):
            raise InvalidStateError("Call is not dialing")
        debug(
            f"Cancelling call {self.call_id}",
            f"Call {self.call_id}: cancel requested",
        )

        for x in self.RTPClients:
            x.stop()
        self.sip.cancel(self.request)
        self.state = CallState.ENDED
        self._finalize_ended_call()

    def hangup(self) -> None:
        if self.state != CallState.ANSWERED:
            raise InvalidStateError("Call is not answered")
        debug(
            f"Hanging up call {self.call_id}",
            f"Call {self.call_id}: hangup requested",
        )

        for x in self.RTPClients:
            x.stop()
        try:
            self.sip.bye(self.request)
        except (OSError, RuntimeError) as ex:
            warnings.warn(
                f"Failed to send SIP BYE for Call-ID={self.call_id}: {ex}. "
                "Ending local call state anyway.",
                RuntimeWarning,
                stacklevel=2,
            )
            debug(
                f"Failed to send BYE for {self.call_id}: {ex}",
                f"Call {self.call_id}: BYE send failed: {ex}",
            )
        self.state = CallState.ENDED
        self._finalize_ended_call()

    def bye(self) -> None:
        if self.state == CallState.ANSWERED:
            debug(
                f"Remote BYE for {self.call_id}",
                f"Call {self.call_id}: remote side ended the call",
            )

            for x in self.RTPClients:
                x.stop()
            self.state = CallState.ENDED
        self._finalize_ended_call()

    def writeAudio(self, data: bytes) -> None:
        warnings.warn(
            "writeAudio is deprecated due to PEP8 compliance. "
            + "Use write_audio instead.",
            DeprecationWarning,
            stacklevel=2,
        )
        return self.write_audio(data)

    def write_audio(self, data: bytes) -> None:
        for x in self.RTPClients:
            x.write(data)

    def audio_frame_size(self, duration_ms: int = 20) -> int:
        if self.RTPClients:
            return self.RTPClients[0].audio_frame_size(duration_ms)
        return self.phone.public_audio_frame_size(duration_ms)

    def audio_format(self) -> Dict[str, Any]:
        sample_rate = (
            self.RTPClients[0].audio_sample_rate
            if self.RTPClients
            else self.phone.audio_sample_rate
        )
        return {
            "sample_rate": sample_rate,
            "sample_rate_mode": (
                "auto" if self.phone.audio_sample_rate is None else "fixed"
            ),
            "sample_width": self.phone.audio_sample_width,
            "channels": self.phone.audio_channels,
            "encoding": "unsigned-8bit-linear",
        }

    def readAudio(self, length=None, blocking=True) -> bytes:
        warnings.warn(
            "readAudio is deprecated due to PEP8 compliance. "
            + "Use read_audio instead.",
            DeprecationWarning,
            stacklevel=2,
        )
        return self.read_audio(length, blocking)

    def read_audio(self, length=None, blocking=True) -> bytes:
        if blocking:
            while self.state not in (CallState.ANSWERED, CallState.ENDED):
                time.sleep(0.01)

        if length is None:
            length = self.audio_frame_size()

        if self.state != CallState.ANSWERED or len(self.RTPClients) == 0:
            return b"\x80" * length

        if len(self.RTPClients) == 1:
            return self.RTPClients[0].read(length, blocking)

        data = [client.read(length, blocking) for client in self.RTPClients]
        mixed = audioop.bias(data[0], 1, -128)
        for frame in data[1:]:
            mixed = audioop.add(mixed, audioop.bias(frame, 1, -128), 1)
        return audioop.bias(mixed, 1, 128)

class VoIPPhone:
    def __init__(
        self,
        server: str,
        port: int,
        username: str,
        password: str,
        myIP="0.0.0.0",
        callCallback: Optional[Callable[["VoIPCall"], None]] = None,
        sipPort=5060,
        rtpPortLow=10000,
        rtpPortHigh=20000,
        auth_username: Optional[str] = None,
        proxy: Optional[str] = None,
        proxyPort: Optional[int] = None,
        proxy_port: Optional[int] = None,
        transport: Optional[str] = None,
        tls_context: Any = None,
        tls_server_name: Optional[str] = None,
        codec_priorities: Optional[Dict[Any, int]] = None,
        audio_sample_rate: Optional[int] = None,
    ):
        if rtpPortLow > rtpPortHigh:
            raise InvalidRangeError("'rtpPortHigh' must be >= 'rtpPortLow'")

        if proxy_port is not None:
            proxyPort = proxy_port

        self.rtpPortLow = rtpPortLow
        self.rtpPortHigh = rtpPortHigh
        self.NSD = False

        self.portsLock = Lock()
        self.assignedPorts: List[int] = []
        self.session_ids: List[int] = []

        self.server = server
        self.port = port
        self.myIP = myIP
        self.username = username
        self.password = password
        self.auth_username = (
            username if auth_username is None else auth_username
        )
        self.proxy = proxy
        self.proxyPort = proxyPort
        self.transport = transport
        self.tls_context = tls_context
        self.tls_server_name = tls_server_name
        self.codec_priorities = dict(codec_priorities or {})
        if audio_sample_rate is not None:
            try:
                audio_sample_rate = int(audio_sample_rate)
            except (TypeError, ValueError) as ex:
                raise InvalidRangeError(
                    "'audio_sample_rate' must be an integer"
                ) from ex
            if audio_sample_rate <= 0:
                raise InvalidRangeError(
                    "'audio_sample_rate' must be positive"
                )
        self.audio_sample_rate = audio_sample_rate
        self.audio_sample_width = 1
        self.audio_channels = 1
        self.callCallback = callCallback
        self._status = PhoneStatus.INACTIVE
        if self.codec_priorities:
            for codec, score in self.codec_priorities.items():
                pyVoIP.set_codec_priority(codec, score)
        else:
            pyVoIP.refresh_supported_codecs()

        # "recvonly", "sendrecv", "sendonly", "inactive"
        self.sendmode = "sendrecv"
        self.recvmode = "sendrecv"

        self.calls: Dict[str, VoIPCall] = {}
        self.threads: List[Timer] = []
        # Allows you to find call ID based off thread.
        self.threadLookup: Dict[Timer, str] = {}
        # Protects the short window between SIPClient.invite() returning and
        # the corresponding VoIPCall being inserted into self.calls.  A final
        # INVITE response can arrive in that window via the receive loop.
        self._outbound_call_creation_depth = 0
        self.sip = SIP.SIPClient(
            server,
            port,
            username,
            password,
            phone=self,
            myIP=self.myIP,
            myPort=sipPort,
            callCallback=self.callback,
            fatalCallback=self.fatal,
            auth_username=self.auth_username,
            proxy=self.proxy,
            proxy_port=self.proxyPort,
            transport=self.transport,
            tls_context=self.tls_context,
            tls_server_name=self.tls_server_name,
        )

    def _queue_unmatched_final_invite_response(
        self,
        request: SIP.SIPMessage,
    ) -> bool:
        """Queue a final outbound INVITE response received during call setup.

        Outbound calls are created in two phases:

        1. SIPClient.invite() sends the INVITE and returns the request/call-id.
        2. VoIPPhone.call() creates VoIPCall and stores it in self.calls.

        If the receive loop sees a final response during that small gap, the
        response would otherwise be treated as an unknown call.  Queue it so
        VoIPPhone.call() can apply it immediately after the call object exists.
        """
        if self._outbound_call_creation_depth <= 0:
            return False

        call_id = str(request.headers.get("Call-ID", "") or "")
        if not call_id:
            return False

        # Avoid queueing stale/unrelated responses where possible.  SIPClient
        # records the active outbound INVITE Call-ID in last_invite_debug.
        invite_debug_snapshot = getattr(
            self.sip,
            "invite_debug_snapshot",
            lambda: {},
        )()
        active_invite_call_id = invite_debug_snapshot.get("call_id")
        if active_invite_call_id and active_invite_call_id != call_id:
            return False

        self.sip.pending_invite_responses[call_id] = request
        debug(
            request.summary(),
            "Queued final INVITE response received before call object "
            + f"was registered call_id={call_id} "
            + f"status={int(request.status)} {request.status.phrase}",
        )

        # ACK final INVITE responses promptly to stop retransmissions.  The
        # same SIPMessage object is later replayed through callback(), and
        # _send_ack() is idempotent for that object.
        try:
            self._send_ack(request)
        except Exception as ex:
            debug(
                f"Failed to ACK queued final INVITE response: {ex}",
                f"Failed to ACK queued final INVITE response "
                + f"Call-ID={call_id}: {ex}",
            )
        return True

    def _send_ack(self, request: SIP.SIPMessage) -> None:
        if getattr(request, "_pyvoip_ack_sent", False):
            return
        ack = self.sip.gen_ack(request)
        host, port = self.sip.ack_target(request)
        self.sip.send_raw(ack.encode("utf8"), (host, port))
        setattr(request, "_pyvoip_ack_sent", True)

    def callback(self, request: SIP.SIPMessage) -> None:
        if request.type == pyVoIP.SIP.SIPMessageType.MESSAGE:
            if request.method == "INVITE":
                self._callback_MSG_Invite(request)
            elif request.method == "BYE":
                self._callback_MSG_Bye(request)
            elif request.method == "CANCEL":
                self._callback_MSG_Cancel(request)
        else:
            # Only treat responses for INVITE as call-control.
            cseq = request.headers.get("CSeq", {})
            cseq_method = cseq.get("method") if isinstance(cseq, dict) else None
            if cseq_method != "INVITE":
                return

            code = int(request.status)
            if 100 <= code < 200:
                self._callback_RESP_Provisional(request)
            elif request.status == SIP.SIPStatus.OK:
                self._callback_RESP_OK(request)
            elif request.status == SIP.SIPStatus.NOT_FOUND:
                self._callback_RESP_NotFound(request)
            elif request.status == SIP.SIPStatus.SERVICE_UNAVAILABLE:
                self._callback_RESP_Unavailable(request)
            else:
                self._callback_RESP_Failed(request)

    def _callback_RESP_Provisional(self, request: SIP.SIPMessage) -> None:
        call_id = request.headers.get("Call-ID")
        debug(
            request.summary(),
            "Call provisional response "
            + f"call_id={call_id} status={int(request.status)} {request.status.phrase}",
        )

        if call_id in self.calls:
            call = self.calls[call_id]
            if request.status in (
                SIP.SIPStatus.RINGING,
                SIP.SIPStatus.SESSION_PROGRESS,
            ):
                if call.state == CallState.DIALING:
                    call.state = CallState.RINGING

        require = request.headers.get("Require", "")
        if isinstance(require, str) and "100rel" in require.lower():
            warnings.warn(
                "Received reliable provisional response (Require: 100rel). "
                "pyVoIP does not implement PRACK; calls may stall at 18x/183.",
                RuntimeWarning,
                stacklevel=2,
            )

    def _callback_RESP_Failed(self, request: SIP.SIPMessage) -> None:
        call_id = request.headers.get("Call-ID", "UNKNOWN")
        code = int(request.status)
        phrase = getattr(request.status, "phrase", "")
        debug(
            request.summary(),
            f"Call did not work out call_id={call_id} status={code} {phrase}",
        )
        if call_id not in self.calls:
            if self._queue_unmatched_final_invite_response(request):
                return

        # ACK final INVITE responses to stop retransmits.
        try:
            self._send_ack(request)
        except Exception as ex:
            debug(
                f"Failed to send ACK: {ex}\n{request.summary()}",
                f"Failed to send ACK for Call-ID={call_id}: {ex}",
            )

        # End the call locally.
        if call_id in self.calls:
            call = self.calls[call_id]
            for rtp in call.RTPClients:
                try:
                    rtp.stop()
                except Exception:
                    pass
            call.state = CallState.ENDED
            self.release_ports(call=call)
            del self.calls[call_id]

        warnings.warn(
            f"Call failed with SIP {code} {phrase} (Call-ID={call_id}).",
            RuntimeWarning,
            stacklevel=2,
        )

    def getStatus(self) -> PhoneStatus:
        warnings.warn(
            "getStatus is deprecated due to PEP8 compliance. "
            + "Use get_status instead.",
            DeprecationWarning,
            stacklevel=2,
        )
        return self.get_status()

    def get_status(self) -> PhoneStatus:
        return self._status

    def public_audio_frame_size(self, duration_ms: int = 20) -> int:
        # Before negotiation, auto mode has no selected codec yet.  Use the
        # legacy 8 kHz frame size as the fallback silence/read size.
        sample_rate = self.audio_sample_rate or 8000
        return max(1, int(round(sample_rate * (duration_ms / 1000.0))))

    def audio_format(self) -> Dict[str, Any]:
        return {
            "sample_rate": self.audio_sample_rate,
            "sample_rate_mode": (
                "auto" if self.audio_sample_rate is None else "fixed"
            ),
            "fallback_sample_rate": self.audio_sample_rate or 8000,
            "sample_width": self.audio_sample_width,
            "channels": self.audio_channels,
            "encoding": "unsigned-8bit-linear",
        }

    def supported_codecs(self) -> List[Dict[str, Any]]:
        return RTP.supported_codecs()

    def codec_availability(self, refresh: bool = False) -> List[Dict[str, Any]]:
        return pyVoIP.codec_availability(refresh=refresh)

    def refresh_supported_codecs(self) -> List[RTP.PayloadType]:
        return pyVoIP.refresh_supported_codecs()

    def set_codec_priority(self, codec: RTP.PayloadType, score: int) -> List[RTP.PayloadType]:
        return pyVoIP.set_codec_priority(codec, score)

    def reset_codec_priorities(self) -> List[RTP.PayloadType]:
        return pyVoIP.reset_codec_priorities()

    def local_supported_codecs(self) -> List[Dict[str, Any]]:
        """Return codecs supported by this PyVoIP build/configuration."""
        return self.supported_codecs()

    def _add_codec_to_offer(
        self,
        offer_codecs: Dict[int, RTP.PayloadType],
        codec: RTP.PayloadType,
    ) -> None:
        payload_type = RTP.default_payload_type(codec)
        if payload_type is None:
            try:
                payload_type = int(codec)
            except Exception:
                payload_type = 96

        seen_payload_types = set()
        while payload_type in offer_codecs:
            if payload_type in seen_payload_types:
                raise RTP.RTPParseError(
                    "No RTP payload numbers are available for SDP offer."
                )
            seen_payload_types.add(payload_type)
            payload_type += 1
            if payload_type > 127:
                payload_type = 96

        offer_codecs[payload_type] = codec

    def local_codec_offer(self) -> List[Dict[str, Any]]:
        """Return the audio codecs this phone would offer in an INVITE.

        This is local-only and is available before the SIP client is started.
        Dynamic payload choices, such as telephone-event, are shown as they
        would be advertised by VoIPPhone.call().
        """
        offer_codecs: Dict[int, RTP.PayloadType] = {}
        for codec in pyVoIP.RTPCompatibleCodecs:
            if RTP.is_transmittable_audio_codec(codec):
                self._add_codec_to_offer(offer_codecs, codec)

        if RTP.PayloadType.EVENT in pyVoIP.RTPCompatibleCodecs:
            self._add_codec_to_offer(offer_codecs, RTP.PayloadType.EVENT)

        offer = []
        for payload_type, codec in offer_codecs.items():
            info = RTP.codec_info(
                codec,
                payload_type=payload_type,
                media_type="audio",
                source="local-offer",
                supported=codec in pyVoIP.RTPCompatibleCodecs,
            )
            info["protocol"] = RTP.RTPProtocol.AVP.value
            info["protocol_supported"] = True
            info["bandwidth_supported"] = True
            info["public_audio_sample_rate"] = (
                self.audio_sample_rate
                or info.get("preferred_source_sample_rate")
                or info.get("rate")
            )
            info["supported"] = bool(
                info["codec_supported"]
                and info["protocol_supported"]
                and info["bandwidth_supported"]
            )
            offer.append(info)
        return offer

    def _local_codec_report(self) -> Dict[str, Any]:
        local_codecs = self.local_supported_codecs()
        local_offer = self.local_codec_offer()
        local_transmittable_audio = [
            codec
            for codec in local_offer
            if codec.get("supported") and codec.get("can_transmit_audio")
        ]
        return {
            "local": local_codecs,
            "pyvoip": local_codecs,
            "local_offer": local_offer,
            "local_transmittable_audio": local_transmittable_audio,
            "local_can_start_call": bool(local_transmittable_audio),
            "audio_format": self.audio_format(),
        }

    def remote_supported_codecs(
        self,
        target: str,
        media_type: Optional[str] = "audio",
        *,
        timeout: Optional[float] = None,
    ) -> List[Dict[str, Any]]:
        """Probe a remote target with SIP OPTIONS and return its SDP codecs.

        The phone must already be started so SIP signalling is available. Some
        providers and endpoints reply to OPTIONS without SDP; in that case the
        returned list is empty even though a later INVITE may still negotiate
        media successfully.
        """
        response = self.sip.options(target, timeout=timeout)
        return response.supported_codecs(media_type=media_type)

    def codec_support_report(
        self,
        target: Optional[str] = None,
        media_type: Optional[str] = "audio",
        *,
        timeout: Optional[float] = None,
    ) -> Dict[str, Any]:
        """Return local and, when requested, remote pre-call codec details.

        If ``target`` is omitted, the result is purely local and works before
        ``start()``. If ``target`` is provided, the phone sends SIP OPTIONS and
        includes remote SDP codecs when the peer supplies them.
        """
        report = self._local_codec_report()
        if target is None:
            report.update(
                {
                    "target": None,
                    "target_uri": None,
                    "source": "local",
                    "response": None,
                    "remote": [],
                    "compatible": [],
                    "unsupported": [],
                    "good": [],
                    "missing": [],
                    "pyvoip_missing_from_remote": report["local"],
                    "remote_has_sdp": False,
                    "transmittable_audio": [],
                    "call_compatible": [],
                    "can_start_call": report["local_can_start_call"],
                }
            )
            return report

        target_uri = self.sip._normalize_request_target(target)
        response = self.sip.options(target_uri, timeout=timeout)
        remote_report = response.codec_support_report(media_type=media_type)
        report.update(remote_report)
        status_code = int(response.status)
        report.update(
            {
                "target": target,
                "target_uri": target_uri,
                "source": "sip-options",
                "response": {
                    "status_code": status_code,
                    "phrase": response.status.phrase,
                    "heading": str(response.heading, "utf8", errors="replace"),
                    "has_sdp": bool(response.body.get("m")),
                },
            }
        )
        if not report.get("remote_has_sdp"):
            report["can_start_call"] = None
        return report

    def available_codecs(
        self,
        target: Optional[str] = None,
        media_type: Optional[str] = "audio",
        *,
        timeout: Optional[float] = None,
    ) -> Dict[str, Any]:
        """Alias for codec_support_report() with a discoverability-focused name."""
        return self.codec_support_report(
            target=target,
            media_type=media_type,
            timeout=timeout,
        )

    def _default_audio_offer(self) -> Dict[int, RTP.PayloadType]:
        codecs: Dict[int, RTP.PayloadType] = {}

        for codec in pyVoIP.RTPCompatibleCodecs:
            if not RTP.is_transmittable_audio_codec(codec):
                continue
            self._add_codec_to_offer(codecs, codec)

        if RTP.PayloadType.EVENT in pyVoIP.RTPCompatibleCodecs:
            self._add_codec_to_offer(codecs, RTP.PayloadType.EVENT)

        if not any(RTP.is_transmittable_audio_codec(codec) for codec in codecs.values()):
            raise RTP.RTPParseError("No transmittable audio codecs are enabled.")

        return codecs

    def _has_assignable_audio_ports(self, request: SIP.SIPMessage) -> bool:
        connections = 0
        for connection in request.body.get("c", []):
            connections += connection.get("address_count", 1)

        audio_media = []
        audio_ports = 0
        for media in request.body.get("m", []):
            if media.get("type") != "audio":
                continue
            if (
                not _media_uses_supported_rtp_profile(media)
                or not _media_port_is_enabled(media)
            ):
                continue
            audio_media.append(media)
            audio_ports += media.get("port_count", 1)

        if not audio_media:
            return True

        if connections == 0:
            return False

        audio_ports_adj = audio_ports / len(audio_media)
        return audio_ports_adj == connections


    def _has_compatible_audio_offer(self, request: SIP.SIPMessage) -> bool:
        for media in request.body.get("m", []):
            if media.get("type") != "audio":
                continue
            if (
                not _media_uses_supported_rtp_profile(media)
                or not _media_port_is_enabled(media)
            ):
                continue

            for method in media.get("methods", []):
                try:
                    codec = _payload_type_from_media_method(media, method)
                except (KeyError, ValueError):
                    continue

                if codec not in pyVoIP.RTPCompatibleCodecs:
                    continue
                if not RTP.codec_fmtp_supported(
                    codec,
                    _media_fmtp_settings(media, method),
                ):
                    continue
                if not SIP.codec_bandwidth_supported(
                    codec,
                    session_bandwidth=request.body.get("b", []),
                    media_bandwidth=media.get("bandwidth", []),
                ):
                    continue

                if not RTP.is_transmittable_audio_codec(codec):
                    continue
                return True

        return False

    def _has_compatible_rtp_address_family(
        self, request: SIP.SIPMessage
    ) -> bool:
        local_address_type = SIP.SIPClient._sdp_address_type(self.myIP)
        for connection in request.body.get("c", []):
            remote_address_type = str(
                connection.get("address_type", "")
            ).upper()
            if remote_address_type != local_address_type:
                return False
        return True

    def _callback_MSG_Invite(self, request: SIP.SIPMessage) -> None:
        call_id = request.headers["Call-ID"]
        debug(
            request.summary(),
            "Inbound INVITE "
            + f"call_id={call_id} from={request.headers.get('From')} "
            + f"to={request.headers.get('To')}",
        )


        if call_id in self.calls:
            debug("Re-negotiation detected!")
            if self.calls[call_id].state != CallState.RINGING:
                self.calls[call_id].renegotiate(request)
            return

        to_header = request.headers.get("To", {})
        to_tag = (
            to_header.get("tag", "")
            if isinstance(to_header, dict)
            else ""
        )
        if to_tag:
            debug(
                request.summary(),
                "Rejecting in-dialog INVITE for unknown call "
                + f"call_id={call_id} to_tag={to_tag}",
            )
            message = self.sip.gen_response(
                request, SIP.SIPStatus.CALL_OR_TRANSACTION_DOESNT_EXIST
            )
            self.sip.send_response(request, message)
            return

        if self.callCallback is None:
            message = self.sip.gen_busy(request)
            self.sip.send_response(request, message)
        else:
            if not self._has_compatible_rtp_address_family(request):
                debug(
                    request.summary(),
                    "Rejecting INVITE with incompatible RTP address family "
                    + f"call_id={call_id}",
                )
                message = self.sip.gen_response(
                    request, SIP.SIPStatus.NOT_ACCEPTABLE_HERE
                )
                self.sip.send_response(request, message)
                return

            if not self._has_assignable_audio_ports(request):
                debug(
                    request.summary(),
                    "Rejecting INVITE with unassignable RTP audio ports "
                    + f"call_id={call_id}",
                )
                message = self.sip.gen_response(
                    request, SIP.SIPStatus.NOT_ACCEPTABLE_HERE
                )
                self.sip.send_response(request, message)
                return



            if not self._has_compatible_audio_offer(request):
                debug(
                    request.summary(),
                    "Rejecting INVITE with no compatible audio codec "
                    + f"call_id={call_id}",
                )
                message = self.sip.gen_response(
                    request, SIP.SIPStatus.NOT_ACCEPTABLE_HERE
                )
                self.sip.send_response(request, message)
                return

            debug("New call!")
            sess_id = None
            while sess_id is None:
                proposed = random.randint(1, 100000)
                if proposed not in self.session_ids:
                    self.session_ids.append(proposed)
                    sess_id = proposed
            message = self.sip.gen_ringing(request)
            self.sip.send_response(request, message)
            try:
                self._create_Call(request, sess_id)
            except RTP.RTPParseError as ex:
                if sess_id in self.session_ids:
                    self.session_ids.remove(sess_id)
                self.release_ports()
                debug(
                    request.summary(),
                    "Rejecting INVITE after codec negotiation failed "
                    + f"call_id={call_id}: {ex}",
                )
                message = self.sip.gen_response(
                    request, SIP.SIPStatus.NOT_ACCEPTABLE_HERE
                )
                self.sip.send_response(request, message)
                return

            try:
                t = Timer(1, self.callCallback, [self.calls[call_id]])
                t.name = f"Phone Call: {call_id}"
                t.daemon = True
                t.start()
                self.threads.append(t)
                self.threadLookup[t] = call_id
            except Exception:
                message = self.sip.gen_busy(request)
                self.sip.send_response(request, message)
                raise

    def _callback_MSG_Bye(self, request: SIP.SIPMessage) -> None:
        debug("BYE recieved")
        call_id = request.headers["Call-ID"]
        debug(
            request.summary(),
            f"Inbound BYE call_id={call_id}",
        )

        if call_id not in self.calls:
            return
        self.calls[call_id].bye()

    def _callback_MSG_Cancel(self, request: SIP.SIPMessage) -> None:
        call_id = request.headers["Call-ID"]
        debug(
            request.summary(),
            f"Inbound CANCEL call_id={call_id}",
        )

        call = self.calls.get(call_id)
        if call is None or call.state != CallState.RINGING:
            return

        for rtp in call.RTPClients:
            try:
                rtp.stop()
            except Exception:
                pass

        response = self.sip.gen_response(
            call.request, SIP.SIPStatus.REQUEST_TERMINATED
        )
        self.sip.send_response(call.request, response)
        call.state = CallState.ENDED
        call._finalize_ended_call()

    def _callback_RESP_OK(self, request: SIP.SIPMessage) -> None:
        debug("OK recieved")
        call_id = request.headers["Call-ID"]
        debug(
            request.summary(),
            f"Call OK response call_id={call_id} status={int(request.status)} {request.status.phrase}",
        )

        if call_id not in self.calls:
            if self._queue_unmatched_final_invite_response(request):
                return
            debug("Unknown/No call")
            # Still ACK 200 OK to stop retransmits.
            self._send_ack(request)
            return

        if not self._has_compatible_rtp_address_family(request):
            debug(
                request.summary(),
                "Ending call after OK with incompatible RTP address family "
                + f"call_id={call_id}",
            )
            self._send_ack(request)
            try:
                self.sip.bye(request)
            except Exception as ex:
                debug(
                    f"Failed to send BYE after RTP address mismatch: {ex}",
                    f"Failed to send BYE for Call-ID={call_id}: {ex}",
                )
            call = self.calls[call_id]
            for rtp in call.RTPClients:
                try:
                    rtp.stop()
                except Exception:
                    pass
            call.state = CallState.ENDED
            call._finalize_ended_call()
            warnings.warn(
                "Remote SDP uses an RTP address family that does not match "
                + "myIP. CallState set to CallState.ENDED. "
                + f"Call-ID={call_id}.",
                RuntimeWarning,
                stacklevel=2,
            )
            return

        if not self._has_compatible_audio_offer(request):
            debug(
                request.summary(),
                "Ending call after OK with no compatible audio codec "
                + "within SDP bandwidth limits "
                + f"call_id={call_id}",
            )
            self._send_ack(request)
            try:
                self.sip.bye(request)
            except Exception as ex:
                debug(
                    f"Failed to send BYE after SDP bandwidth mismatch: {ex}",
                    f"Failed to send BYE for Call-ID={call_id}: {ex}",
                )

            call = self.calls[call_id]
            for rtp in call.RTPClients:
                try:
                    rtp.stop()
                except Exception:
                    pass
            call.state = CallState.ENDED
            call._finalize_ended_call()
            warnings.warn(
                "Remote SDP does not offer a compatible audio codec that "
                + "fits its SDP bandwidth limits. CallState set to "
                + f"CallState.ENDED. Call-ID={call_id}.",
                RuntimeWarning,
                stacklevel=2,
            )
            return

        try:
            self.calls[call_id].answered(request)
        except RTP.RTPParseError as ex:
            debug(
                request.summary(),
                "Ending call after OK because RTP negotiation failed "
                + f"call_id={call_id}: {ex}",
            )
            self._send_ack(request)
            try:
                self.sip.bye(request)
            except Exception as bye_ex:
                debug(
                    f"Failed to send BYE after RTP negotiation failure: {bye_ex}",
                    f"Failed to send BYE for Call-ID={call_id}: {bye_ex}",
                )

            call = self.calls[call_id]
            for rtp in call.RTPClients:
                try:
                    rtp.stop()
                except Exception:
                    pass
            call.state = CallState.ENDED
            call._finalize_ended_call()
            warnings.warn(
                "Remote SDP could not be negotiated for RTP. "
                + f"CallState set to CallState.ENDED. Call-ID={call_id}.",
                RuntimeWarning,
                stacklevel=2,
            )
            return

        debug("Answered")
        self._send_ack(request)

    def _callback_RESP_NotFound(self, request: SIP.SIPMessage) -> None:
        debug("Not Found recieved, invalid number called?")
        call_id = request.headers["Call-ID"]
        debug(
            request.summary(),
            f"Call not found response call_id={call_id}",
        )

        if call_id not in self.calls:
            if self._queue_unmatched_final_invite_response(request):
                return

            debug("Unknown/No call")
            debug("ACKing unmatched final INVITE response")
            self._send_ack(request)
            return

        self.calls[call_id].not_found(request)
        debug("Terminating Call")
        self._send_ack(request)

    def _callback_RESP_Unavailable(self, request: SIP.SIPMessage) -> None:
        debug("Service Unavailable recieved")
        call_id = request.headers["Call-ID"]
        debug(
            request.summary(),
            f"Call unavailable response call_id={call_id}",
        )

        if call_id not in self.calls:
            if self._queue_unmatched_final_invite_response(request):
                return

            debug("Unknown call")
            debug("ACKing unmatched final INVITE response")
            self._send_ack(request)
            return
        self.calls[call_id].unavailable(request)
        debug("Terminating Call")
        self._send_ack(request)

    def _create_Call(self, request: SIP.SIPMessage, sess_id: int) -> None:
        call_id = request.headers["Call-ID"]
        self.calls[call_id] = VoIPCall(
            self,
            CallState.RINGING,
            request,
            sess_id,
            self.myIP,
            sendmode=self.recvmode,
        )

    def start(self) -> None:
        self._status = PhoneStatus.REGISTERING
        try:
            self.sip.start()
            self.NSD = True
        except Exception:
            self._status = PhoneStatus.FAILED
            self.sip.stop()
            self.NSD = False
            raise

    def stop(self, failed=False) -> None:
        self._status = PhoneStatus.DEREGISTERING
        try:
            for call_id in list(self.calls):
                call = self.calls.get(call_id)
                if call is None:
                    continue

                try:
                    if call.state == CallState.ANSWERED:
                        call.hangup()
                    elif call.state in (CallState.DIALING, CallState.RINGING):
                        if (
                            call.state == CallState.RINGING
                            and call.remote_sip_message is not None
                        ):
                            call.deny()
                        else:
                            call.cancel()
                    else:
                        call._finalize_ended_call()
                except InvalidStateError:
                    for rtp in getattr(call, "RTPClients", ()):
                        try:
                            rtp.stop()
                        except Exception:
                            pass
                    call.state = CallState.ENDED
                    call._finalize_ended_call()
                except Exception as ex:
                    debug(
                        f"Error hanging up call during phone stop: {ex}",
                        f"Call {call_id}: forced local cleanup during stop: {ex}",
                    )
                    for rtp in getattr(call, "RTPClients", ()):
                        try:
                            rtp.stop()
                        except Exception:
                            pass
                    try:
                        call.state = CallState.ENDED
                        call._finalize_ended_call()
                    except Exception:
                        self.calls.pop(call_id, None)
        finally:
            try:
                self.sip.stop()
            finally:
                self._status = PhoneStatus.FAILED if failed else PhoneStatus.INACTIVE

    def fatal(self) -> None:
        self.stop(failed=True)

    def call(self, number: str) -> VoIPCall:
        port = self.request_port()
        medias = {}
        medias[port] = self._default_audio_offer()
        call_id: Optional[str] = None
        self._outbound_call_creation_depth += 1
        try:
            request, call_id, sess_id = self.sip.invite(
                number, medias, RTP.TransmitType.SENDRECV
            )

            call = VoIPCall(
                self,
                CallState.DIALING,
                request,
                sess_id,
                self.myIP,
                ms=medias,
                sendmode=self.sendmode,
            )
            assert call_id is not None
            self.calls[call_id] = call
        except Exception:
            if call_id is not None:
                self.sip.pop_pending_invite_response(call_id)
            self.release_ports()
            raise
        finally:
            self._outbound_call_creation_depth -= 1


        debug(
            request.summary(),
            "Outbound call created "
            + f"call_id={call_id} number={number} session_id={sess_id} "
            + f"local_rtp_ports={list(medias.keys())}",
        )

        pending_response = self.sip.pop_pending_invite_response(call_id)
        if pending_response is not None:
            debug(
                pending_response.summary(),
                "Applying queued INVITE response "
                + f"call_id={call_id} status={int(pending_response.status)} "
                + f"{pending_response.status.phrase}",
            )
            self.callback(pending_response)

        return call

    def request_port(self, blocking=True) -> int:
        while True:
            with self.portsLock:
                ports_available = [
                    port
                    for port in range(self.rtpPortLow, self.rtpPortHigh + 1)
                    if port not in self.assignedPorts
                ]
                if ports_available:
                    selection = random.choice(ports_available)
                    self.assignedPorts.append(selection)
                    return selection

            # If no ports are available attempt to cleanup any missed calls.
            self.release_ports()

            with self.portsLock:
                ports_available = [
                    port
                    for port in range(self.rtpPortLow, self.rtpPortHigh + 1)
                    if port not in self.assignedPorts
                ]
                if ports_available:
                    selection = random.choice(ports_available)
                    self.assignedPorts.append(selection)
                    return selection

            if not (self.NSD and blocking):
                raise NoPortsAvailableError(
                    "No ports were available to be assigned"
                )

            time.sleep(0.5)

    def release_ports(self, call: Optional[VoIPCall] = None) -> None:
        with self.portsLock:
            self._cleanup_dead_calls()
            if isinstance(call, VoIPCall):
                ports = list(call.assignedPorts.keys())
            else:
                dnr_ports = []
                for call_id in self.calls:
                    dnr_ports += list(self.calls[call_id].assignedPorts.keys())
                ports = []
                for port in self.assignedPorts:
                    if port not in dnr_ports:
                        ports.append(port)

            for port in ports:
                if port in self.assignedPorts:
                    self.assignedPorts.remove(port)

    def _cleanup_dead_calls(self) -> None:
        to_delete = []
        for thread in self.threads:
            if not thread.is_alive():
                call_id = self.threadLookup.get(thread)
                if call_id is None:
                    to_delete.append(thread)
                    continue

                call = self.calls.get(call_id)
                if call is None:
                    debug("Unable to delete from calls dictionary!")
                    debug(f"call_id={call_id} calls={self.calls}")
                elif call.state == CallState.ENDED:
                    del self.calls[call_id]

                try:
                    del self.threadLookup[thread]
                except KeyError:
                    debug("Unable to delete from threadLookup dictionary!")
                    debug(f"thread={thread} threadLookup={self.threadLookup}")
                to_delete.append(thread)
        for thread in to_delete:
            self.threads.remove(thread)
