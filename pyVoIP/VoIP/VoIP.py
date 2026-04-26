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
                    pr = x.get("protocol")
                    if pr and pr != RTP.RTPProtocol.AVP:
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
                if i["type"] == "video":
                    continue
                pr = i.get("protocol")
                if pr and pr != RTP.RTPProtocol.AVP:
                    continue
                assoc = {}
                e = False
                pt = None
                for x in i["methods"]:
                    try:
                        p = RTP.PayloadType(int(x))
                        assoc[int(x)] = p
                    except ValueError:
                        try:
                            p = RTP.PayloadType(
                                i["attributes"][x]["rtpmap"]["name"]
                            )
                            assoc[int(x)] = p
                        except ValueError:
                            # Sometimes rtpmap raise a KeyError because fmtp
                            # is set instate
                            pt = i["attributes"][x]["rtpmap"]["name"]
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
                for m in assoc:
                    if assoc[m] in pyVoIP.RTPCompatibleCodecs:
                        codecs[m] = assoc[m]
                        try:
                            int(assoc[m])
                        except Exception:
                            pass
                        else:
                            has_transmittable_codec = True

                if not has_transmittable_codec:
                    continue

                if not codecs:
                    continue

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

        remote_ip = request.body["c"][0]["address"]
        c = RTP.RTPClient(
            codecs,
            ip,
            port,
            remote_ip,
            baseport,
            self.sendmode,
            dtmf=self.dtmf_callback,
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

    def remote_supported_codecs(self) -> List[Dict[str, Any]]:
        """Return codecs advertised by the remote endpoint's SDP."""
        if self.remote_sip_message is None:
            return []
        return self.remote_sip_message.supported_codecs()

    def codec_support_report(self) -> Dict[str, List[Dict[str, Any]]]:
        """Compare the remote SDP codecs against PyVoIP support."""
        if self.remote_sip_message is None:
            pyvoip_codecs = RTP.supported_codecs()
            return {
                "remote": [],
                "pyvoip": pyvoip_codecs,
                "compatible": [],
                "unsupported": [],
                "good": [],
                "missing": [],
                "pyvoip_missing_from_remote": pyvoip_codecs,
            }
        return self.remote_sip_message.codec_support_report()

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
            if i["type"] == "video":
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
            if i["type"] == "video":
                continue
            assoc = {}
            p = None
            for x in i["methods"]:
                try:
                    p = RTP.PayloadType(int(x))
                    assoc[int(x)] = p
                except ValueError:
                    try:
                        p = RTP.PayloadType(
                            i["attributes"][x]["rtpmap"]["name"]
                        )
                        assoc[int(x)] = p
                    except ValueError:
                        if p:
                            raise RTP.RTPParseError(
                                f"RTP Payload type {p} not found."
                            )
                        raise RTP.RTPParseError(
                            "RTP Payload type could not be derived from SDP."
                        )

            self.create_rtp_clients(
                assoc, self.myIP, self.port, request, i["port"]
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
        self.sip.bye(self.request)
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

    def readAudio(self, length=160, blocking=True) -> bytes:
        warnings.warn(
            "readAudio is deprecated due to PEP8 compliance. "
            + "Use read_audio instead.",
            DeprecationWarning,
            stacklevel=2,
        )
        return self.read_audio(length, blocking)

    def read_audio(self, length=160, blocking=True) -> bytes:
        if blocking:
            while self.state not in (CallState.ANSWERED, CallState.ENDED):
                time.sleep(0.01)

        if self.state != CallState.ANSWERED or len(self.RTPClients) == 0:
            return b"\x80" * length

        if len(self.RTPClients) == 1:
            return self.RTPClients[0].read(length, blocking)

        data = [client.read(length, blocking) for client in self.RTPClients]
        mixed = data[0]
        for frame in data[1:]:
            mixed = audioop.add(mixed, frame, 1)
        return mixed

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
        self.callCallback = callCallback
        self._status = PhoneStatus.INACTIVE

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
        self.sip.out.sendto(ack.encode("utf8"), (host, port))
        setattr(request, "_pyvoip_ack_sent", True)

    def callback(self, request: SIP.SIPMessage) -> None:
        if request.type == pyVoIP.SIP.SIPMessageType.MESSAGE:
            if request.method == "INVITE":
                self._callback_MSG_Invite(request)
            elif request.method == "BYE":
                self._callback_MSG_Bye(request)
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

    def supported_codecs(self) -> List[Dict[str, Any]]:
        return RTP.supported_codecs()


    def _has_assignable_audio_ports(self, request: SIP.SIPMessage) -> bool:
        connections = 0
        for connection in request.body.get("c", []):
            connections += connection.get("address_count", 1)

        audio_media = []
        audio_ports = 0
        for media in request.body.get("m", []):
            if media.get("protocol") != RTP.RTPProtocol.AVP:
                continue
            if media.get("type") != "audio":
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
            if media.get("protocol") != RTP.RTPProtocol.AVP:
                continue

            for method in media.get("methods", []):
                try:
                    codec = RTP.PayloadType(int(method))
                except ValueError:
                    try:
                        codec_name = media["attributes"][method]["rtpmap"]["name"]
                    except KeyError:
                        continue
                    try:
                        codec = RTP.PayloadType(codec_name)
                    except ValueError:
                        continue

                if codec not in pyVoIP.RTPCompatibleCodecs:
                    continue
                try:
                    int(codec)
                except Exception:
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

        self.calls[call_id].answered(request)
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
        for x in self.calls.copy():
            try:
                self.calls[x].hangup()
            except InvalidStateError:
                pass
        self.sip.stop()
        self._status = PhoneStatus.INACTIVE
        if failed:
            self._status = PhoneStatus.FAILED

    def fatal(self) -> None:
        self.stop(failed=True)

    def call(self, number: str) -> VoIPCall:
        port = self.request_port()
        medias = {}
        medias[port] = {0: RTP.PayloadType.PCMU, 101: RTP.PayloadType.EVENT}
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
                "Applying queued final INVITE response "
                + f"call_id={call_id} status={int(pending_response.status)} "
                + f"{pending_response.status.phrase}",
            )
            self.callback(pending_response)

        return call

    def request_port(self, blocking=True) -> int:
        ports_available = [
            port
            for port in range(self.rtpPortLow, self.rtpPortHigh + 1)
            if port not in self.assignedPorts
        ]
        if len(ports_available) == 0:
            # If no ports are available attempt to cleanup any missed calls.
            self.release_ports()
            ports_available = [
                port
                for port in range(self.rtpPortLow, self.rtpPortHigh + 1)
                if (port not in self.assignedPorts)
            ]

        while self.NSD and blocking and len(ports_available) == 0:
            ports_available = [
                port
                for port in range(self.rtpPortLow, self.rtpPortHigh + 1)
                if (port not in self.assignedPorts)
            ]
            time.sleep(0.5)
            self.release_ports()

            if len(ports_available) == 0:
                raise NoPortsAvailableError(
                    "No ports were available to be assigned"
                )

        selection = random.choice(ports_available)
        self.assignedPorts.append(selection)

        return selection

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
                call_id = self.threadLookup[thread]
                try:
                    del self.calls[call_id]
                except KeyError:
                    debug("Unable to delete from calls dictionary!")
                    debug(f"call_id={call_id} calls={self.calls}")
                try:
                    del self.threadLookup[thread]
                except KeyError:
                    debug("Unable to delete from threadLookup dictionary!")
                    debug(f"thread={thread} threadLookup={self.threadLookup}")
                to_delete.append(thread)
        for thread in to_delete:
            self.threads.remove(thread)
