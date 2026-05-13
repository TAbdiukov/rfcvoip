from dataclasses import dataclass, field
from email import policy
from email.parser import BytesParser
from enum import Enum, IntEnum
from threading import Timer, Lock
from typing import Any, Callable, Dict, List, Optional, Tuple, TYPE_CHECKING
from pyVoIP.util import acquired_lock_and_unblocked_socket
from pyVoIP.SIPTransport import (
    ResolvedSIPTarget,
    SIPConnection,
    SIPResolver,
    SIPTransport,
    format_hostport,
    split_hostport,
)
from pyVoIP.VoIP.status import PhoneStatus
import pyVoIP
import hashlib
import ipaddress
import socket
import random
import re
import time
import uuid
import select
import ssl
import warnings


if TYPE_CHECKING:
    from pyVoIP.VoIP import VoIPPhone
    from pyVoIP import RTP


__all__ = [
    "Counter",
    "InvalidAccountInfoError",
    "SIPClient",
    "codec_support_report",
    "codec_bandwidth_supported",
    "SIPMessage",
    "SIPMessageType",
    "SIPParseError",
    "SIPRequestError",
    "SIPSubscription",
    "extract_sdp_bodies",
    "extract_sdp_body",
    "SIPStatus",
    "SIPTransport",
    "sip_supported_codecs",
]


debug = pyVoIP.debug


class InvalidAccountInfoError(Exception):
    pass


class SIPParseError(Exception):
    pass


class SIPRequestError(Exception):
    pass


class RetryRequiredError(Exception):
    pass


def _parse_digest_params(value: str) -> Dict[str, str]:
    data = str(value or "").strip()
    if data.lower().startswith("digest"):
        data = data[len("Digest") :].lstrip()

    params: Dict[str, str] = {}
    index = 0
    length = len(data)

    while index < length:
        while index < length and data[index] in " \t\r\n,":
            index += 1
        if index >= length:
            break

        key_start = index
        while index < length and data[index] not in "=, \t\r\n":
            index += 1
        key = data[key_start:index].strip()

        while index < length and data[index].isspace():
            index += 1
        if index >= length or data[index] != "=":
            while index < length and data[index] != ",":
                index += 1
            continue

        index += 1
        while index < length and data[index].isspace():
            index += 1

        if index < length and data[index] == '"':
            index += 1
            chars = []
            while index < length:
                char = data[index]
                if char == "\\" and index + 1 < length:
                    chars.append(data[index + 1])
                    index += 2
                    continue
                if char == '"':
                    index += 1
                    break
                chars.append(char)
                index += 1
            parsed_value = "".join(chars)
        else:
            value_start = index
            while index < length and data[index] != ",":
                index += 1
            parsed_value = data[value_start:index].strip()

        if key:
            params[key.lower()] = parsed_value

        while index < length and data[index] != ",":
            index += 1
        if index < length and data[index] == ",":
            index += 1

    return params

def _content_type_base(value: Any) -> str:
    """Return the normalized media type without parameters."""
    return str(value or "").split(";", 1)[0].strip().lower()


def _mime_payload_bytes(part: Any) -> bytes:
    payload = part.get_payload(decode=True)
    if payload is not None:
        return payload

    payload = part.get_payload()
    if payload is None:
        return b""
    if isinstance(payload, bytes):
        return payload

    charset = part.get_content_charset() or "utf-8"
    return str(payload).encode(charset, errors="replace")


def _mime_message_from_sip_body(content_type: str, body: bytes):
    # email.parser needs a complete MIME entity. SIP already supplied the
    # entity headers outside the body, so synthesize only the MIME headers
    # needed to parse multipart boundaries and part headers.
    safe_content_type = " ".join(str(content_type or "").splitlines()).strip()
    raw = (
        f"Content-Type: {safe_content_type}\r\n"
        "MIME-Version: 1.0\r\n"
        "\r\n"
    ).encode("utf-8") + body
    return BytesParser(policy=policy.default).parsebytes(raw)


def extract_sdp_bodies(content_type: Any, body: bytes) -> List[bytes]:
    """Extract application/sdp payloads from a SIP MIME body.

    Direct ``application/sdp`` bodies are returned as-is. Multipart bodies are
    parsed as MIME and walked recursively, so nested multipart containers are
    supported. Non-SDP bodies return an empty list.
    """
    if not body:
        return []

    media_type = _content_type_base(content_type)
    if media_type == "application/sdp":
        return [body]

    if not media_type.startswith("multipart/"):
        return []

    message = _mime_message_from_sip_body(str(content_type or ""), body)
    sdp_bodies: List[bytes] = []
    for part in message.walk():
        if part.is_multipart():
            continue
        if _content_type_base(part.get_content_type()) == "application/sdp":
            sdp_bodies.append(_mime_payload_bytes(part))
    return sdp_bodies


def extract_sdp_body(content_type: Any, body: bytes) -> Optional[bytes]:
    """Return the first SDP body found in a SIP body, if any."""
    sdp_bodies = extract_sdp_bodies(content_type, body)
    return sdp_bodies[0] if sdp_bodies else None


_SDP_BANDWIDTH_UNITS = {
    # RFC 4566 defines CT/AS values in kilobits per second.
    "CT": "kbps",
    "AS": "kbps",
    # RFC 3890 commonly appears in SIP SDP for transport independent bitrate.
    "TIAS": "bps",
    # RTCP sender/receiver bandwidth modifiers; tracked but not codec limits.
    "RS": "bps",
    "RR": "bps",
}

_SDP_MEDIA_BANDWIDTH_LIMIT_TYPES = {"AS", "TIAS"}


def _parse_sdp_bandwidth(data: str) -> Dict[str, Any]:
    """Parse an SDP b= line value into a normalized dictionary.

    SDP allows multiple b= lines, and their scope depends on where the line
    appears. The caller adds the ``scope`` field once it knows whether this
    line belongs to the session or the current media block.
    """
    if ":" not in data:
        raise SIPParseError(f"Malformed SDP bandwidth line: b={data!r}")

    bw_type, raw_bandwidth = data.split(":", 1)
    bw_type = bw_type.strip().upper()
    raw_bandwidth = raw_bandwidth.strip()
    if not bw_type or not raw_bandwidth:
        raise SIPParseError(f"Malformed SDP bandwidth line: b={data!r}")

    try:
        bandwidth = int(raw_bandwidth)
    except ValueError as ex:
        raise SIPParseError(
            f"SDP bandwidth value must be an integer: b={data!r}"
        ) from ex

    if bandwidth < 0:
        raise SIPParseError(
            f"SDP bandwidth value cannot be negative: b={data!r}"
        )

    unit = _SDP_BANDWIDTH_UNITS.get(bw_type, "unknown")
    bits_per_second = None
    if unit == "kbps":
        bits_per_second = bandwidth * 1000
    elif unit == "bps":
        bits_per_second = bandwidth

    return {
        "type": bw_type,
        "bandwidth": bandwidth,
        "unit": unit,
        "bits_per_second": bits_per_second,
    }


@dataclass
class SIPSubscription:
    call_id: str
    target: str
    target_uri: str
    event: str
    accept: List[str]
    local_tag: str
    remote_tag: str = ""
    remote_target: Optional[str] = None
    expires: int = 3600
    pending_expires: int = 3600
    status: str = "pending"
    subscription_state: Optional[str] = None
    reason: Optional[str] = None
    last_response_code: Optional[int] = None
    last_response_phrase: Optional[str] = None
    last_notify_body: str = ""
    last_notify_headers: Dict[str, str] = field(default_factory=dict)
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)

    def snapshot(self) -> Dict[str, Any]:
        return {
            "call_id": self.call_id,
            "target": self.target,
            "target_uri": self.target_uri,
            "event": self.event,
            "accept": list(self.accept),
            "local_tag": self.local_tag,
            "remote_tag": self.remote_tag,
            "remote_target": self.remote_target,
            "expires": self.expires,
            "pending_expires": self.pending_expires,
            "status": self.status,
            "subscription_state": self.subscription_state,
            "reason": self.reason,
            "last_response_code": self.last_response_code,
            "last_response_phrase": self.last_response_phrase,
            "last_notify_body": self.last_notify_body,
            "last_notify_headers": dict(self.last_notify_headers),
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


class Counter:
    def __init__(self, start: int = 1):
        self.x = start
        self._lock = Lock()

    def count(self) -> int:
        with self._lock:
            x = self.x
            self.x += 1
            return x

    def next(self) -> int:
        return self.count()

    def current(self) -> int:
        with self._lock:
            return self.x


class SIPStatus(Enum):
    def __new__(cls, value: int, phrase: str = "", description: str = ""):
        obj = object.__new__(cls)
        obj._value_ = value

        obj.phrase = phrase
        obj.description = description
        return obj

    def __int__(self) -> int:
        return self._value_

    def __str__(self) -> str:
        return f"{self._value_} {self.phrase}"

    @property
    def phrase(self) -> str:
        return self._phrase

    @phrase.setter
    def phrase(self, value: str) -> None:
        self._phrase = value

    @property
    def description(self) -> str:
        return self._description

    @description.setter
    def description(self, value: str) -> None:
        self._description = value

    # Informational
    TRYING = (
        100,
        "Trying",
        "Extended search being performed, may take a significant time",
    )
    RINGING = (
        180,
        "Ringing",
        "Destination user agent received INVITE, "
        + "and is alerting user of call",
    )
    FORWARDED = 181, "Call is Being Forwarded"
    QUEUED = 182, "Queued"
    SESSION_PROGRESS = 183, "Session Progress"
    TERMINATED = 199, "Early Dialog Terminated"

    # Success
    OK = 200, "OK", "Request successful"
    ACCEPTED = (
        202,
        "Accepted",
        "Request accepted, processing continues (Deprecated.)",
    )
    NO_NOTIFICATION = (
        204,
        "No Notification",
        "Request fulfilled, nothing follows",
    )

    # Redirection
    MULTIPLE_CHOICES = (
        300,
        "Multiple Choices",
        "Object has several resources -- see URI list",
    )
    MOVED_PERMANENTLY = (
        301,
        "Moved Permanently",
        "Object moved permanently -- see URI list",
    )
    MOVED_TEMPORARILY = (
        302,
        "Moved Temporarily",
        "Object moved temporarily -- see URI list",
    )
    USE_PROXY = (
        305,
        "Use Proxy",
        "You must use proxy specified in Location to "
        + "access this resource",
    )
    ALTERNATE_SERVICE = (
        380,
        "Alternate Service",
        "The call failed, but alternatives are available -- see URI list",
    )

    # Client Error
    BAD_REQUEST = (
        400,
        "Bad Request",
        "Bad request syntax or unsupported method",
    )
    UNAUTHORIZED = (
        401,
        "Unauthorized",
        "No permission -- see authorization schemes",
    )
    PAYMENT_REQUIRED = (
        402,
        "Payment Required",
        "No payment -- see charging schemes",
    )
    FORBIDDEN = (
        403,
        "Forbidden",
        "Request forbidden -- authorization will not help",
    )
    NOT_FOUND = (404, "Not Found", "Nothing matches the given URI")
    METHOD_NOT_ALLOWED = (
        405,
        "Method Not Allowed",
        "Specified method is invalid for this resource",
    )
    NOT_ACCEPTABLE = (
        406,
        "Not Acceptable",
        "URI not available in preferred format",
    )
    PROXY_AUTHENTICATION_REQUIRED = (
        407,
        "Proxy Authentication Required",
        "You must authenticate with this proxy before proceeding",
    )
    REQUEST_TIMEOUT = (
        408,
        "Request Timeout",
        "Request timed out; try again later",
    )
    CONFLICT = 409, "Conflict", "Request conflict"
    GONE = (
        410,
        "Gone",
        "URI no longer exists and has been permanently removed",
    )
    LENGTH_REQUIRED = (
        411,
        "Length Required",
        "Client must specify Content-Length",
    )
    CONDITIONAL_REQUEST_FAILED = 412, "Conditional Request Failed"
    REQUEST_ENTITY_TOO_LARGE = (
        413,
        "Request Entity Too Large",
        "Entity is too large",
    )
    REQUEST_URI_TOO_LONG = 414, "Request-URI Too Long", "URI is too long"
    UNSUPPORTED_MEDIA_TYPE = (
        415,
        "Unsupported Media Type",
        "Entity body in unsupported format",
    )
    UNSUPPORTED_URI_SCHEME = (
        416,
        "Unsupported URI Scheme",
        "Cannot satisfy request",
    )
    UNKOWN_RESOURCE_PRIORITY = (
        417,
        "Unkown Resource-Priority",
        "There was a resource-priority option tag, "
        + "but no Resource-Priority header",
    )
    BAD_EXTENSION = (
        420,
        "Bad Extension",
        "Bad SIP Protocol Extension used, not understood by the server.",
    )
    EXTENSION_REQUIRED = (
        421,
        "Extension Required",
        "Server requeires a specific extension to be "
        + "listed in the Supported header.",
    )
    SESSION_INTERVAL_TOO_SMALL = 422, "Session Interval Too Small"
    SESSION_INTERVAL_TOO_BRIEF = 423, "Session Interval Too Breif"
    BAD_LOCATION_INFORMATION = 424, "Bad Location Information"
    USE_IDENTITY_HEADER = (
        428,
        "Use Identity Header",
        "The server requires an Identity header, "
        + "and one has not been provided.",
    )
    PROVIDE_REFERRER_IDENTITY = 429, "Provide Referrer Identity"
    """
    This response is intended for use between proxy devices,
    and should not be seen by an endpoint. If it is seen by one,
    it should be treated as a 400 Bad Request response.
    """
    FLOW_FAILED = (
        430,
        "Flow Failed",
        "A specific flow to a user agent has failed, "
        + "although other flows may succeed.",
    )
    ANONYMITY_DISALLOWED = 433, "Anonymity Disallowed"
    BAD_IDENTITY_INFO = 436, "Bad Identity-Info"
    UNSUPPORTED_CERTIFICATE = 437, "Unsupported Certificate"
    INVALID_IDENTITY_HEADER = 438, "Invalid Identity Header"
    FIRST_HOP_LACKS_OUTBOUND_SUPPORT = 439, "First Hop Lacks Outbound Support"
    MAX_BREADTH_EXCEEDED = 440, "Max-Breadth Exceeded"
    BAD_INFO_PACKAGE = 469, "Bad Info Package"
    CONSENT_NEEDED = 470, "Consent Needed"
    TEMPORARILY_UNAVAILABLE = 480, "Temporarily Unavailable"
    CALL_OR_TRANSACTION_DOESNT_EXIST = 481, "Call/Transaction Does Not Exist"
    LOOP_DETECTED = 482, "Loop Detected"
    TOO_MANY_HOPS = 483, "Too Many Hops"
    ADDRESS_INCOMPLETE = 484, "Address Incomplete"
    AMBIGUOUS = 485, "Ambiguous"
    BUSY_HERE = 486, "Busy Here", "Callee is busy"
    REQUEST_TERMINATED = 487, "Request Terminated"
    NOT_ACCEPTABLE_HERE = 488, "Not Acceptable Here"
    BAD_EVENT = 489, "Bad Event"
    REQUEST_PENDING = 491, "Request Pending"
    UNDECIPHERABLE = 493, "Undecipherable"
    SECURITY_AGREEMENT_REQUIRED = 494, "Security Agreement Required"

    # Server Errors
    INTERNAL_SERVER_ERROR = (
        500,
        "Internal Server Error",
        "Server got itself in trouble",
    )
    NOT_IMPLEMENTED = (
        501,
        "Not Implemented",
        "Server does not support this operation",
    )
    BAD_GATEWAY = (
        502,
        "Bad Gateway",
        "Invalid responses from another server/proxy",
    )
    SERVICE_UNAVAILABLE = (
        503,
        "Service Unavailable",
        "The server cannot process the request due to a high load",
    )
    GATEWAY_TIMEOUT = (
        504,
        "Server Timeout",
        "The server did not receive a timely response",
    )
    SIP_VERSION_NOT_SUPPORTED = (
        505,
        "SIP Version Not Supported",
        "Cannot fulfill request",
    )
    MESSAGE_TOO_LONG = 513, "Message Too Long"
    PUSH_NOTIFICATION_SERVICE_NOT_SUPPORTED = (
        555,
        "Push Notification Service Not Supported",
    )
    PRECONDITION_FAILURE = 580, "Precondition Failure"

    # Global Failure Responses
    BUSY_EVERYWHERE = 600, "Busy Everywhere"
    DECLINE = 603, "Decline"
    DOES_NOT_EXIST_ANYWHERE = 604, "Does Not Exist Anywhere"
    GLOBAL_NOT_ACCEPTABLE = 606, "Not Acceptable"
    UNWANTED = 607, "Unwanted"
    REJECTED = 608, "Rejected"


@dataclass(frozen=True)
class SIPStatusCode:
    value: int
    phrase: str = ""
    description: str = ""

    def __int__(self) -> int:
        return self.value

    def __str__(self) -> str:
        return f"{self.value} {self.phrase}".rstrip()


def _sip_status_from_parts(code: int, phrase: str = ""):
    try:
        return SIPStatus(code)
    except ValueError:
        return SIPStatusCode(code, phrase or "Unknown")


class SIPMessageType(IntEnum):
    def __new__(cls, value: int):
        obj = int.__new__(cls, value)
        obj._value_ = value
        return obj

    MESSAGE = 1
    RESPONSE = 0


class SIPMessage:

    _COMPACT_HEADER_NAMES = {
        "c": "Content-Type",
        "e": "Content-Encoding",
        "f": "From",
        "i": "Call-ID",
        "k": "Supported",
        "l": "Content-Length",
        "m": "Contact",
        "s": "Subject",
        "t": "To",
        "v": "Via",
        "o": "Event",
        "u": "Allow-Events",
    }

    _CANONICAL_HEADER_NAMES = {
        "via": "Via",
        "from": "From",
        "to": "To",
        "call-id": "Call-ID",
        "cseq": "CSeq",
        "allow": "Allow",
        "supported": "Supported",
        "content-length": "Content-Length",
        "content-type": "Content-Type",
        "contact": "Contact",
        "www-authenticate": "WWW-Authenticate",
        "authorization": "Authorization",
        "proxy-authenticate": "Proxy-Authenticate",
        "proxy-authorization": "Proxy-Authorization",
        "event": "Event",
        "subscription-state": "Subscription-State",
        "expires": "Expires",
    }

    def __init__(self, data: bytes):
        self.SIPCompatibleVersions = pyVoIP.SIPCompatibleVersions
        self.SIPCompatibleMethods = pyVoIP.SIPCompatibleMethods
        self.heading = b""
        self.type: Optional[SIPMessageType] = None
        self.status = SIPStatus(491)
        self.headers: Dict[str, Any] = {"Via": []}
        self.body: Dict[str, Any] = {}

        # Backwards-compatible auth storage.  ``authentication`` and
        # ``authentication_header`` keep the most recently parsed digest auth
        # header, while ``authentication_challenges`` preserves per-challenge
        # state so WWW-Authenticate and Proxy-Authenticate can coexist.
        self.authentication: Dict[str, str] = {}
        self.authentication_challenges: Dict[str, List[Dict[str, str]]] = {}
        # Which header populated ``self.authentication`` (WWW-Authenticate,
        # Proxy-Authenticate, Authorization, Proxy-Authorization).
        self.authentication_header: Optional[str] = None
        self.body_raw = b""
        self.body_text = ""
        self._body_content_type = ""
        self.raw = data
        self.parse(data)

    def summary(self) -> str:
        data = ""
        if self.type == SIPMessageType.RESPONSE:
            data += f"Status: {int(self.status)} {self.status.phrase}\n\n"
        else:
            data += f"Method: {self.method}\n\n"
        data += "Headers:\n"
        for x in self.headers:
            data += f"{x}: {self.headers[x]}\n"
        data += "\n"
        data += "Body:\n"
        for x in self.body:
            data += f"{x}: {self.body[x]}\n"
        data += "\n"
        data += "Raw:\n"
        data += str(self.raw)

        return data

    def supported_codecs(
        self, media_type: Optional[str] = "audio"
    ) -> List[Dict[str, Any]]:
        return sip_supported_codecs(self, media_type=media_type)

    def codec_support_report(
        self, media_type: Optional[str] = "audio"
    ) -> Dict[str, Any]:
        return codec_support_report(self, media_type=media_type)

    def parse(self, data: bytes) -> None:
        try:
            headers, body = data.split(b"\r\n\r\n", 1)
        except ValueError as ve:
            debug(f"Error unpacking data, only using header: {ve}")
            headers = data.split(b"\r\n\r\n")[0]

        headers_raw = headers.split(b"\r\n")
        heading = headers_raw.pop(0)
        parts = heading.split(b" ")
        if len(parts) < 1:
            raise SIPParseError("Empty SIP start line")

        first = str(parts[0], "utf8", errors="replace")

        # Response: "SIP/2.0 200 OK"
        if first in self.SIPCompatibleVersions:
            self.type = SIPMessageType.RESPONSE
            self.parse_sip_response(data)
            return

        # Request: "METHOD sip:... SIP/2.0"
        if len(parts) >= 3:
            last = str(parts[-1], "utf8", errors="replace")
            if last in self.SIPCompatibleVersions:
                self.type = SIPMessageType.MESSAGE
                self.parse_sip_message(data)
                return

        raise SIPParseError(
            "Unable to decipher SIP request: " + str(heading, "utf8", errors="replace")
        )

    def parseHeader(self, header: str, data: str) -> None:
        warnings.warn(
            "parseHeader is deprecated due to PEP8 compliance. "
            + "Use parse_header instead.",
            DeprecationWarning,
            stacklevel=2,
        )
        return self.parse_header(header, data)

    def parse_header(self, header: str, data: str) -> None:
        if header == "Via":
            values = data if isinstance(data, list) else [data]
            for d in values:
                pieces = d.split(";")
                sent_by = pieces[0].strip().split()
                if len(sent_by) < 2:
                    continue
                _type = sent_by[0]
                _ip, _port, _explicit = split_hostport(sent_by[1], 5060)
                _via = {
                    "type": _type,
                    "transport": _type.rsplit("/", 1)[-1].upper(),
                    "address": (_ip, str(_port or 5060)),
                }

                """
                Sets branch, maddr, ttl, received, and rport if defined
                as per RFC 3261 20.7
                """
                for x in pieces[1:]:
                    x = x.strip()
                    if not x:
                        continue
                    if "=" in x:
                        key, val = x.split("=", 1)
                        _via[key] = val
                    else:
                        _via[x] = None
                self.headers["Via"].append(_via)
        elif header == "From" or header == "To":
            info = re.split(r";tag=", data, maxsplit=1, flags=re.IGNORECASE)
            tag = ""
            if len(info) >= 2:
                tag = info[1].split(";", 1)[0]
            raw = info[0].strip()

            contact = re.search(r"<?sips?:([^>\s]+)>?", raw, flags=re.IGNORECASE)
            if contact is None:
                raise SIPParseError(f"Malformed {header} header: {data!r}")

            caller = raw[: contact.start()].strip().strip('"').strip("'")
            address = contact.group(1).strip().rstrip(">")
            if len(address.split("@")) == 2:
                number = address.split("@")[0]
                host = address.split("@")[1]
            else:
                number = None
                host = address

            self.headers[header] = {
                "raw": raw,
                "tag": tag,
                "address": address,
                "number": number,
                "caller": caller,
                "host": host,
            }
        elif header == "CSeq":
            parts = data.split()
            if len(parts) < 2:
                raise SIPParseError(f"Malformed CSeq header: {data!r}")
            self.headers[header] = {
                "check": parts[0],
                "method": parts[1],
            }
        elif header == "Allow":
            self.headers[header] = [
                item.strip() for item in data.split(",") if item.strip()
            ]
        elif header == "Supported":
            self.headers[header] = [
                item.strip() for item in data.split(",") if item.strip()
            ]
        elif header == "Content-Length":
            self.headers[header] = int(data.strip())
        elif header in (
            "WWW-Authenticate",
            "Authorization",
            "Proxy-Authenticate",
            "Proxy-Authorization",
        ):
            header_data = _parse_digest_params(data)
            existing = self.headers.get(header)
            if isinstance(existing, list):
                existing.append(header_data)
            elif isinstance(existing, dict):
                self.headers[header] = [existing, header_data]
            else:
                self.headers[header] = header_data
            if header in ("WWW-Authenticate", "Proxy-Authenticate"):
                self.authentication_challenges.setdefault(header, []).append(
                    dict(header_data)
                )
            self.authentication = header_data
            self.authentication_header = header
        else:
            self.headers[header] = data

    def parseBody(self, header: str, data: str) -> None:
        warnings.warn(
            "parseBody is deprecated due to PEP8 compliance. "
            + "Use parse_body instead.",
            DeprecationWarning,
            stacklevel=2,
        )
        return self.parse_body(header, data)

    @staticmethod
    def _parse_sdp_rtp_protocol(protocol: str):
        try:
            return pyVoIP.RTP.RTPProtocol(protocol)
        except ValueError:
            return protocol

    def parse_body(self, header: str, data: str) -> None:
        body_content_type = self._body_content_type or _content_type_base(self.headers.get("Content-Type"))
        if body_content_type == "application/sdp" and "Content-Encoding" in self.headers:
            raise SIPParseError("Unable to parse encoded content.")
        if body_content_type == "application/sdp":
            # Referenced RFC 4566 July 2006
            if header == "v":
                # SDP 5.1 Version
                self.body[header] = int(data)
            elif header == "o":
                # SDP 5.2 Origin
                # o=<username> <sess-id> <sess-version> <nettype> <addrtype> <unicast-address>
                d = data.split(" ")
                self.body[header] = {
                    "username": d[0],
                    "id": d[1],
                    "version": d[2],
                    "network_type": d[3],
                    "address_type": d[4],
                    "address": d[5],
                }
            elif header == "s":
                # SDP 5.3 Session Name
                # s=<session name>
                self.body[header] = data
            elif header == "i":
                # SDP 5.4 Session Information
                # i=<session-description>
                self.body[header] = data
            elif header == "u":
                # SDP 5.5 URI
                # u=<uri>
                self.body[header] = data
            elif header == "e" or header == "p":
                # SDP 5.6 Email Address and Phone Number of person
                # responsible for the conference
                # e=<email-address>
                # p=<phone-number>
                self.body[header] = data
            elif header == "c":
                # SDP 5.7 Connection Data
                # c=<nettype> <addrtype> <connection-address>
                if "c" not in self.body:
                    self.body["c"] = []
                d = data.split()
                if len(d) < 3:
                    raise SIPParseError(f"Malformed SDP connection line: c={data!r}")

                connection = None
                # TTL Data and Multicast addresses may be specified.
                # For IPv4 its listed as addr/ttl/number of addresses.
                # c=IN IP4 224.2.1.1/127/3 means:
                # c=IN IP4 224.2.1.1/127
                # c=IN IP4 224.2.1.2/127
                # c=IN IP4 224.2.1.3/127
                # With the TTL being 127.
                # IPv6 does not support time to live so you will only see a '/'
                # for multicast addresses.
                if "/" in d[2]:
                    if d[1] == "IP6":
                        connection = {
                            "network_type": d[0],
                            "address_type": d[1],
                            "address": d[2].split("/")[0],
                            "ttl": None,
                            "address_count": int(d[2].split("/")[1]),
                        }
                    else:
                        address_data = d[2].split("/")
                        if len(address_data) == 2:
                            connection = {
                                "network_type": d[0],
                                "address_type": d[1],
                                "address": address_data[0],
                                "ttl": int(address_data[1]),
                                "address_count": 1,
                            }
                        else:
                            connection = {
                                "network_type": d[0],
                                "address_type": d[1],
                                "address": address_data[0],
                                "ttl": int(address_data[1]),
                                "address_count": int(address_data[2]),
                            }
                else:
                    connection = {
                        "network_type": d[0],
                        "address_type": d[1],
                        "address": d[2],
                        "ttl": None,
                        "address_count": 1,
                    }

                self.body[header].append(connection)
                if self.body.get("m"):
                    self.body["m"][-1].setdefault("connections", []).append(
                        connection
                    )
                else:
                    self.body.setdefault("session_connections", []).append(
                        connection
                    )
            elif header == "b":
                # SDP 5.8 Bandwidth
                # b=<bwtype>:<bandwidth>
                #
                # b= is scoped by position: before the first m= line it is a
                # session-level limit; after an m= line it belongs to that
                # media description. Preserve multiple lines instead of
                # overwriting earlier restrictions.
                bandwidth = _parse_sdp_bandwidth(data)
                if self.body.get("m"):
                    bandwidth["scope"] = "media"
                    self.body["m"][-1].setdefault("bandwidth", []).append(
                        bandwidth
                    )
                else:
                    bandwidth["scope"] = "session"
                    self.body.setdefault("b", []).append(bandwidth)
            elif header == "t":
                # SDP 5.9 Timing
                # t=<start-time> <stop-time>
                d = data.split(" ")
                self.body[header] = {"start": d[0], "stop": d[1]}
            elif header == "r":
                # SDP 5.10 Repeat Times
                # r=<repeat interval> <active duration> <offsets from start-time> # noqa: E501
                d = data.split(" ")
                self.body[header] = {
                    "repeat": d[0],
                    "duration": d[1],
                    "offset1": d[2],
                    "offset2": d[3],
                }
            elif header == "z":
                # SDP 5.11 Time Zones
                # z=<adjustment time> <offset> <adjustment time> <offset> ....
                # Used for change in timezones such as day light savings time.
                d = data.split()
                amount = len(d) / 2
                self.body[header] = {}
                for x in range(int(amount)):
                    self.body[header]["adjustment-time" + str(x)] = d[x * 2]
                    self.body[header]["offset" + str(x)] = d[x * 2 + 1]
            elif header == "k":
                # SDP 5.12 Encryption Keys
                # k=<method>
                # k=<method>:<encryption key>
                if ":" in data:
                    d = data.split(":", 1)
                    self.body[header] = {"method": d[0], "key": d[1]}
                else:
                    self.body[header] = {"method": data}
            elif header == "m":
                # SDP 5.14 Media Descriptions
                # m=<media> <port>/<number of ports> <proto> <fmt> ...
                # <port> should be even, and <port>+1 should be the RTCP port.
                # <number of ports> should coinside with number of
                # addresses in SDP 5.7 c=
                if "m" not in self.body:
                    self.body["m"] = []
                d = data.split()
                if len(d) < 4:
                    raise SIPParseError(f"Malformed SDP media line: m={data!r}")

                if "/" in d[1]:
                    ports_raw = d[1].split("/")
                    port = ports_raw[0]
                    count = int(ports_raw[1])
                else:
                    port = d[1]
                    count = 1
                methods = d[3:]

                self.body["m"].append(
                    {
                        "type": d[0],
                        "port": int(port),
                        "port_count": count,
                        "protocol": self._parse_sdp_rtp_protocol(d[2]),
                        "methods": methods,
                        "bandwidth": [],
                        "connections": [],
                        "attributes": {},
                    }
                )
                for x in self.body["m"][-1]["methods"]:
                    self.body["m"][-1]["attributes"][x] = {}
            elif header == "a":
                # SDP 5.13 Attributes & 6.0 SDP Attributes
                # a=<attribute>
                # a=<attribute>:<value>
                if "a" not in self.body:
                    self.body["a"] = {}

                if ":" in data:
                    attribute, value = data.split(":", 1)
                else:
                    attribute = data
                    value = None

                if value is not None:
                    if attribute == "rtpmap":
                        # a=rtpmap:<payload type> <encoding name>/<clock rate> [/<encoding parameters>] # noqa: E501
                        parts = value.split(None, 1)
                        if len(parts) != 2:
                            self.body["a"][f"rtpmap:{value}"] = value
                            return

                        payload_id = parts[0]
                        codec_parts = parts[1].split("/")
                        if len(codec_parts) < 2:
                            self.body["a"][f"rtpmap:{payload_id}"] = value
                            return
                        media_sections = self.body.get("m", [])
                        media = media_sections[-1] if media_sections else None

                        if media is None or payload_id not in media.get("methods", []):
                            # Can't attach to a media section; keep as session-level info.
                            self.body["a"][f"rtpmap:{payload_id}"] = value
                            return

                        encoding = codec_parts[2] if len(codec_parts) > 2 else None

                        media["attributes"].setdefault(payload_id, {})
                        media["attributes"][payload_id]["rtpmap"] = {
                            "id": payload_id,
                            "name": codec_parts[0],
                            "frequency": codec_parts[1] if len(codec_parts) > 1 else "",
                            "encoding": encoding,
                        }

                    elif attribute == "fmtp":
                        # a=fmtp:<format> <format specific parameters>
                        d = value.split(None, 1)
                        payload_id = d[0] if d else ""
                        settings = d[1].split() if len(d) > 1 else []
                        media_sections = self.body.get("m", [])
                        media = media_sections[-1] if media_sections else None

                        if media is None or payload_id not in media.get("methods", []):
                            self.body["a"][f"fmtp:{payload_id}"] = " ".join(settings)
                            return
                        media["attributes"].setdefault(payload_id, {})
                        media["attributes"][payload_id]["fmtp"] = {
                            "id": payload_id,
                            "settings": settings,
                        }
                    else:
                        self.body["a"][attribute] = value
                else:
                    if (
                        attribute == "recvonly"
                        or attribute == "sendrecv"
                        or attribute == "sendonly"
                        or attribute == "inactive"
                    ):
                        transmit_type = pyVoIP.RTP.TransmitType(attribute)
                        media_sections = self.body.get("m", [])
                        if media_sections:
                            media_sections[-1]["transmit_type"] = transmit_type
                        else:
                            self.body["a"]["transmit_type"] = transmit_type
            else:
                self.body[header] = data

        else:
            self.body[header] = data

    @classmethod
    def parse_raw_header(
        cls, headers_raw: List[bytes], handle: Callable[[str, str], None]
    ) -> None:
        headers: Dict[str, Any] = {"Via": []}

        for raw_line in headers_raw:
            line = str(raw_line, "utf8", errors="replace")
            if ":" not in line:
                continue

            name, value = line.split(":", 1)
            name = name.strip()
            value = value.lstrip()

            lookup = name.lower()
            name = cls._COMPACT_HEADER_NAMES.get(
                lookup,
                cls._CANONICAL_HEADER_NAMES.get(lookup, name),
            )

            if name == "Via":
                headers["Via"].append(value)
                continue

            if name in ("WWW-Authenticate", "Proxy-Authenticate"):
                existing = headers.get(name)
                if existing is None:
                    headers[name] = value
                elif isinstance(existing, list):
                    existing.append(value)
                else:
                    headers[name] = [existing, value]
                continue

            # Preserve current behavior for most duplicate non-Via headers.
            if name not in headers:
                headers[name] = value

        for key, val in headers.items():
            if isinstance(val, list) and key in (
                "WWW-Authenticate",
                "Proxy-Authenticate",
            ):
                for item in val:
                    handle(key, item)
            else:
                handle(key, val)

    @staticmethod
    def parse_raw_body(
        body: bytes, handle: Callable[[str, str], None]
    ) -> None:
        if len(body) > 0:
            body_raw = body.splitlines()
            for raw_line in body_raw:
                if not raw_line:
                    continue
                line = str(raw_line, "utf8", errors="replace")
                if "=" not in line:
                    continue
                key, value = line.split("=", 1)
                if key:
                    handle(key, value)

    def _body_by_content_length(self, body: bytes) -> bytes:
        content_length = self.headers.get("Content-Length")
        if isinstance(content_length, int):
            return body[: max(0, content_length)]
        return body

    def _parse_message_body(self, body: bytes) -> None:
        self.body_raw = self._body_by_content_length(body)
        self.body_text = str(self.body_raw, "utf8", errors="replace")

        if not self.body_raw:
            return

        content_type = self.headers.get("Content-Type", "")
        sdp_body = extract_sdp_body(content_type, self.body_raw)
        if sdp_body is not None:
            # SIP offer/answer uses one application/sdp body. Keep the raw
            # multipart body in body_raw, but parse the first SDP part into
            # the existing structured SDP fields.
            self._body_content_type = "application/sdp"
            self.parse_raw_body(sdp_body, self.parse_body)
            return

        # Do not accidentally parse text/plain or other multipart parts that
        # happen to contain SDP-looking lines. Multipart without an SDP part
        # has no structured SDP fields.
        if _content_type_base(content_type).startswith("multipart/"):
            return

        self._body_content_type = _content_type_base(content_type)
        self.parse_raw_body(self.body_raw, self.parse_body)

    def parseSIPResponse(self, data: bytes) -> None:
        warnings.warn(
            "parseSIPResponse is deprecated "
            + "due to PEP8 compliance. Use parse_sip_response "
            + "instead.",
            DeprecationWarning,
            stacklevel=2,
        )
        return self.parse_sip_response(data)

    def parse_sip_response(self, data: bytes) -> None:
        try:
            headers, body = data.split(b"\r\n\r\n", 1)
        except ValueError:
            headers, body = data, b""

        headers_raw = headers.split(b"\r\n")
        self.heading = headers_raw.pop(0)
        heading_parts = self.heading.split(b" ")
        if len(heading_parts) < 2:
            raise SIPParseError(
                "Malformed SIP response start line: "
                + str(self.heading, "utf8", errors="replace")
            )

        self.version = str(heading_parts[0], "utf8", errors="replace")
        if self.version not in self.SIPCompatibleVersions:
            raise SIPParseError(f"SIP Version {self.version} not compatible.")

        try:
            status_code = int(heading_parts[1])
        except ValueError as ex:
            raise SIPParseError(
                "Malformed SIP response status code: "
                + str(self.heading, "utf8", errors="replace")
            ) from ex

        status_phrase = str(
            b" ".join(heading_parts[2:]),
            "utf8",
            errors="replace",
        )
        self.status = _sip_status_from_parts(status_code, status_phrase)

        self.parse_raw_header(headers_raw, self.parse_header)
        self._parse_message_body(body)

    def parseSIPMessage(self, data: bytes) -> None:
        warnings.warn(
            "parseSIPMessage is deprecated due to PEP8 compliance."
            + " Use parse_sip_message instead.",
            DeprecationWarning,
            stacklevel=2,
        )
        return self.parse_sip_message(data)

    def parse_sip_message(self, data: bytes) -> None:
        try:
            headers, body = data.split(b"\r\n\r\n", 1)
        except ValueError:
            headers, body = data, b""

        headers_raw = headers.split(b"\r\n")
        self.heading = headers_raw.pop(0)
        parts = self.heading.split(b" ")
        if len(parts) < 3:
            raise SIPParseError(
                "Unable to decipher SIP request: " + str(self.heading, "utf8")
            )

        self.uri = str(parts[1], "utf8", errors="replace")
        self.version = str(parts[2], "utf8", errors="replace")

        if self.version not in self.SIPCompatibleVersions:
            raise SIPParseError(f"SIP Version {self.version} not compatible.")

        self.method = str(
            self.heading.split(b" ")[0],
            "utf8",
            errors="replace",
        )

        self.parse_raw_header(headers_raw, self.parse_header)
        self._parse_message_body(body)


def _safe_int(value: Any) -> Optional[int]:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _protocol_value(protocol: Any) -> str:
    return str(getattr(protocol, "value", protocol))

def _bandwidths_to_list(value: Any) -> List[Dict[str, Any]]:
    if value is None:
        return []
    if isinstance(value, dict):
        return [dict(value)]
    if isinstance(value, list):
        return [dict(item) for item in value if isinstance(item, dict)]
    return []


def _enforceable_bandwidth_limit_bps(
    session_bandwidth: Any = None,
    media_bandwidth: Any = None,
) -> Optional[int]:
    """Return the strictest SDP media bitrate limit we can safely enforce.

    CT is intentionally not enforced here because it describes conference total
    bandwidth, not a specific codec payload cap. RS/RR describe RTCP sender and
    receiver bandwidth, so they are also not codec payload caps.
    """
    limits: List[int] = []
    for bandwidth in _bandwidths_to_list(session_bandwidth) + _bandwidths_to_list(
        media_bandwidth
    ):
        bw_type = str(bandwidth.get("type", "")).upper()
        if bw_type not in _SDP_MEDIA_BANDWIDTH_LIMIT_TYPES:
            continue

        limit = _safe_int(bandwidth.get("bits_per_second"))
        if limit is not None:
            limits.append(limit)

    return min(limits) if limits else None


def _codec_required_bandwidth_bps(codec: Any) -> Optional[int]:
    """Return a registered codec's payload bitrate requirement, if fixed."""
    try:
        from pyVoIP.codecs import codec_required_bandwidth_bps as required_bps

        return required_bps(codec)
    except Exception:
        return None


def codec_bandwidth_supported(
    codec: Any,
    *,
    session_bandwidth: Any = None,
    media_bandwidth: Any = None,
) -> bool:
    """Return whether an SDP bandwidth limit can carry ``codec``.

    Unknown or non-enforceable bandwidth modifiers are treated as compatible;
    only clear AS/TIAS caps below a known codec's payload bitrate reject it.
    """
    required = _codec_required_bandwidth_bps(codec)
    limit = _enforceable_bandwidth_limit_bps(
        session_bandwidth=session_bandwidth,
        media_bandwidth=media_bandwidth,
    )
    return required is None or limit is None or limit >= required


def _bandwidth_context(
    session_bandwidth: Any = None,
    media_bandwidth: Any = None,
    codec: Any = None,
) -> Dict[str, Any]:
    return {
        "session": _bandwidths_to_list(session_bandwidth),
        "media": _bandwidths_to_list(media_bandwidth),
        "limit_bps": _enforceable_bandwidth_limit_bps(
            session_bandwidth=session_bandwidth,
            media_bandwidth=media_bandwidth,
        ),
        "required_bps": _codec_required_bandwidth_bps(codec),
    }


def _media_protocol_supported(media: Dict[str, Any]) -> bool:
    protocol = media.get("protocol")
    return protocol in (pyVoIP.RTP.RTPProtocol.AVP, "RTP/AVP")

def _fmtp_settings(attributes: Dict[str, Any]) -> List[str]:
    fmtp = attributes.get("fmtp", {})
    if isinstance(fmtp, dict):
        settings = fmtp.get("settings", [])
        return [str(setting) for setting in settings]
    return []

def _unknown_codec_info(
    *,
    media: Dict[str, Any],
    payload_type: Optional[int],
    name: str,
    rate: Optional[int],
    channels: Optional[int],
    fmtp: List[str],
    source: str,
    session_bandwidth: Any = None,
    media_bandwidth: Any = None,
) -> Dict[str, Any]:
    return {
        "media_type": media.get("type"),
        "payload_type": payload_type,
        "name": name,
        "description": None,
        "payload_kind": "unknown",
        "can_transmit_audio": False,
        "rate": rate,
        "channels": channels,
        "is_dynamic": payload_type is None or payload_type >= 96,
        "fmtp": list(fmtp),
        "fmtp_supported": False,
        "codec_supported": False,
        "protocol_supported": _media_protocol_supported(media),
        "supported": False,
        "available": False,
        "availability_reason": "Codec is not known to PyVoIP.",
        "library": None,
        "priority_score": 0,
        "default_payload_type": None,
        "rtpmap": None,
        "bandwidth_supported": True,
        "bandwidth": _bandwidth_context(
            session_bandwidth=session_bandwidth,
            media_bandwidth=media_bandwidth,
        ),
        "source": source,
        "protocol": _protocol_value(media.get("protocol")),
    }


def _codec_info_from_media(
    media: Dict[str, Any], method: str, *, session_bandwidth: Any = None
) -> Dict[str, Any]:
    media_bandwidth = _bandwidths_to_list(media.get("bandwidth", []))
    session_bandwidth = _bandwidths_to_list(session_bandwidth)
    attributes = media.get("attributes", {}).get(str(method), {})
    if not isinstance(attributes, dict):
        attributes = {}

    rtpmap = attributes.get("rtpmap", {})
    if not isinstance(rtpmap, dict):
        rtpmap = {}

    fmtp = _fmtp_settings(attributes)
    payload_type = _safe_int(method)
    codec = None
    source = "unknown"
    name = str(method)
    rate = None
    channels = None

    if rtpmap:
        source = "rtpmap"
        name = str(rtpmap.get("name") or name)
        rate = _safe_int(rtpmap.get("frequency"))
        channels = _safe_int(rtpmap.get("encoding"))
        try:
            codec = pyVoIP.RTP.payload_type_from_name(
                name,
                rate=rate,
                channels=channels,
            )
        except ValueError:
            codec = None

    if codec is None and payload_type is not None:
        try:
            codec = pyVoIP.RTP.PayloadType(payload_type)
            source = "static"
        except ValueError:
            pass

    if codec is None:
        return _unknown_codec_info(
            media=media,
            payload_type=payload_type,
            name=name,
            rate=rate,
            channels=channels,
            fmtp=fmtp,
            source=source,
            session_bandwidth=session_bandwidth,
            media_bandwidth=media_bandwidth,
        )

    codec_supported = codec in getattr(pyVoIP, "RTPCompatibleCodecs", [])
    protocol_supported = _media_protocol_supported(media)
    fmtp_supported = pyVoIP.RTP.codec_fmtp_supported(codec, fmtp)
    bandwidth_supported = codec_bandwidth_supported(
        codec,
        session_bandwidth=session_bandwidth,
        media_bandwidth=media_bandwidth,
    )
    info = pyVoIP.RTP.codec_info(
        codec,
        payload_type=payload_type,
        media_type=media.get("type"),
        fmtp=fmtp,
        source=source,
        supported=(
            codec_supported
            and protocol_supported
            and fmtp_supported
            and bandwidth_supported
        ),
    )
    info["codec_supported"] = codec_supported
    info["protocol_supported"] = protocol_supported
    info["fmtp_supported"] = fmtp_supported
    if rate is not None:
        info["rate"] = rate
    if channels is not None:
        info["channels"] = channels
    info["bandwidth_supported"] = bandwidth_supported
    info["required_bandwidth_bps"] = _codec_required_bandwidth_bps(codec)
    info["bandwidth"] = _bandwidth_context(
        session_bandwidth=session_bandwidth,
        media_bandwidth=media_bandwidth,
        codec=codec,
    )
    info["protocol"] = _protocol_value(media.get("protocol"))
    return info


def sip_supported_codecs(
    message: SIPMessage,
    media_type: Optional[str] = "audio",
) -> List[Dict[str, Any]]:
    """Return codecs advertised by a parsed SIP message's SDP body.

    ``media_type`` defaults to ``"audio"``.  Pass ``None`` to return codecs
    from every media section in the SDP body.
    """
    session_bandwidth = _bandwidths_to_list(message.body.get("b", []))
    codecs = []
    for media in message.body.get("m", []):
        if media_type is not None and media.get("type") != media_type:
            continue
        for method in media.get("methods", []):
            codecs.append(
                _codec_info_from_media(
                    media, str(method), session_bandwidth=session_bandwidth
                )
            )
    return codecs

def _codec_name_key(codec: Dict[str, Any]) -> str:
    name = str(codec.get("name") or "").lower()
    rate = codec.get("rate")
    return f"{name}/{rate}" if rate not in (None, "") else name

def codec_support_report(
    message: SIPMessage,
    media_type: Optional[str] = "audio",
) -> Dict[str, Any]:
    """Compare a SIP message's SDP codecs against PyVoIP support."""
    remote = sip_supported_codecs(message, media_type=media_type)
    pyvoip_codecs = pyVoIP.RTP.supported_codecs()
    compatible = [codec for codec in remote if codec.get("supported")]
    unsupported = [codec for codec in remote if not codec.get("supported")]
    remote_names = {_codec_name_key(codec) for codec in remote}
    pyvoip_missing_from_remote = [
        codec
        for codec in pyvoip_codecs
        if _codec_name_key(codec) not in remote_names
    ]
    remote_has_sdp = bool(message.body.get("m"))
    transmittable_audio = [
        codec
        for codec in compatible
        if codec.get("media_type") == "audio"
        and codec.get("can_transmit_audio")
    ]

    return {
        "remote": remote,
        "pyvoip": pyvoip_codecs,
        "compatible": compatible,
        "unsupported": unsupported,
        "good": compatible,
        "missing": unsupported,
        "pyvoip_missing_from_remote": pyvoip_missing_from_remote,
        "remote_has_sdp": remote_has_sdp,
        "transmittable_audio": transmittable_audio,
        "call_compatible": transmittable_audio,
        "can_start_call": bool(transmittable_audio) if remote_has_sdp else None,
    }


class SIPClient:
    def __init__(
        self,
        server: str,
        port: Optional[int],
        username: str,
        password: str,
        phone: "VoIPPhone",
        myIP="0.0.0.0",
        myPort=5060,
        callCallback: Optional[Callable[[SIPMessage], None]] = None,
        fatalCallback: Optional[Callable[..., None]] = None,
        auth_username: Optional[str] = None,
        proxy: Optional[str] = None,
        proxy_port: Optional[int] = None,
        proxyPort: Optional[int] = None,
        transport: Optional[str] = None,
        tls_context: Optional[ssl.SSLContext] = None,
        tls_server_name: Optional[str] = None,
    ):
        self.NSD = False
        self.server = server
        self.requested_transport = (
            None if transport is None else SIPTransport.from_uri(transport)
         )
        self.resolver = SIPResolver(
            transport_preference=(
                [self.requested_transport]
                if self.requested_transport is not None
                else None
            )
        )
        self.server_uri = self.resolver.parse_uri(str(server))
        self.server_scheme = self.server_uri.scheme
        self.server_host = self.server_uri.host
        self.server_uri_port = (
            self.server_uri.port
            if self.server_uri.explicit_port
            else (port if not self.server_uri.has_scheme else None)
        )
        self.server_target = self.resolver.resolve(
            str(server),
            default_port=port,
            default_transport=self.requested_transport,
        )
        self.server_port = self.server_uri_port
        self.port = port if port is not None else self.server_target.port

        self.myIP = myIP
        self.username = username
        self.password = password
        self.auth_username = (
            username if auth_username is None else auth_username
        )

        if proxy_port is None:
            proxy_port = proxyPort
        self.proxy_target = self._normalize_proxy_target(proxy, proxy_port)
        self.proxy = self.proxy_target.host if self.proxy_target else None
        self.proxy_port = self.proxy_target.port if self.proxy_target else None
        self.tls_context = tls_context
        self.tls_server_name = tls_server_name
        self.connection: Optional[SIPConnection] = None

        self.phone = phone

        self.callCallback = callCallback
        self.fatalCallback = fatalCallback

        self.tags: List[str] = []
        self.tagLibrary = {"register": self.gen_tag()}

        self.myPort = myPort

        self.default_expires = 120
        self.register_timeout = 30

        # How long invite() should wait for the *first* response that matches
        # its Call-ID before giving up.
        self.invite_timeout = 30
        # Max times to retry INVITE after 401/407.
        self.invite_max_retries = 1
        self.subscription_timeout = 15
        self.subscription_max_retries = 1
        self.options_timeout = 15
        self.options_max_retries = 1

        self.inviteCounter = Counter()
        self.registerCounter = Counter()
        self.subscribeCounter = Counter()
        self.byeCounter = Counter()
        self.callID = Counter()
        self.sessID = Counter()
        self.optionsCounter = Counter()
        self.register_call_id: Optional[str] = None

        self.urnUUID = self.gen_urn_uuid()

        self.registerThread: Optional[Timer] = None
        self.registerFailures = 0
        self.recvLock = Lock()
        self.pending_invite_responses: Dict[str, SIPMessage] = {}
        self.last_invite_debug: Dict[str, Any] = {}
        self.subscription_callback: Optional[
            Callable[[Dict[str, Any]], None]
        ] = None
        self.subscriptions: Dict[str, SIPSubscription] = {}

    @staticmethod
    def _extract_uri(value: str) -> str:
        value = value.strip()
        if "<" in value and ">" in value:
            value = value.split("<", 1)[1].split(">", 1)[0]
        return value.strip()

    @staticmethod
    def _sip_target_from_uri(
        uri: str, default_port: Optional[int] = 5060
    ) -> Tuple[str, int]:
        target = SIPResolver().resolve(uri, default_port=default_port)
        return target.host, target.port

    def _normalize_proxy_target(
        self,
        proxy: Optional[str],
        proxy_port: Optional[int],
    ) -> Optional[ResolvedSIPTarget]:
        raw = str(proxy or "").strip()
        if not raw:
            return None
        return self.resolver.resolve(
            raw,
            default_port=proxy_port,
            default_transport=self.requested_transport,
        )

    def signal_target(self) -> Tuple[str, int]:
        target = self.proxy_target or self.server_target
        return target.host, target.port

    def signal_transport(self) -> SIPTransport:
        target = self.proxy_target or self.server_target
        return target.transport

    def response_target(self, request: SIPMessage) -> Tuple[str, int]:
        try:
            via = request.headers["Via"][0]
            source_address = getattr(request, "source_address", None)

            if isinstance(via, dict) and "rport" in via:
                source_host = source_address[0] if source_address else None
                source_port = source_address[1] if source_address else None
                sender_address = (
                    via.get("received")
                    or source_host
                    or via["address"][0]
                )
                sender_port = (
                    via.get("rport")
                    or source_port
                    or via["address"][1]
                )
                return str(sender_address), int(sender_port)

            sender_address, sender_port = via["address"]
            return sender_address, int(sender_port)
        except Exception:
            return self.signal_target()

    def dialog_target(self, request: SIPMessage) -> Tuple[str, int]:
        if self.proxy is not None:
            return self.signal_target()
        remote_uri = self._dialog_remote_uri(request)
        if remote_uri:
            return self._sip_target_from_uri(remote_uri, self.port)
        return self.signal_target()

    def _dialog_remote_uri(self, request: SIPMessage) -> str:
        contact = request.headers.get("Contact")
        if contact:
            return self._extract_uri(str(contact))

        call_id = request.headers["Call-ID"]
        tag = self.tagLibrary.get(call_id, "")
        if request.headers["From"]["tag"] == tag:
            return self._extract_uri(str(request.headers["To"]["raw"]))
        return self._extract_uri(str(request.headers["From"]["raw"]))

    @staticmethod
    def _format_hostport(
        host: str,
        port: Optional[int] = None,
        *,
        always_include_port: bool = False,
    ) -> str:
        return format_hostport(
            host, port, always_include_port=always_include_port
        )

    @classmethod
    def _format_sip_uri(
        cls,
        host: str,
        port: Optional[int] = None,
        *,
        user: Optional[str] = None,
        transport: Optional[str] = None,
        always_include_port: bool = False,
        scheme: str = "sip",
    ) -> str:
        uri = f"{scheme}:"
        if user:
            uri += f"{user}@"
        uri += cls._format_hostport(
            host,
            port,
            always_include_port=always_include_port,
        )
        if transport:
            uri += f";transport={transport}"
        return uri

    def _contact_uri(self, *, user: Optional[str] = None) -> str:
        """Return this UA's SIP Contact URI.

        pyVoIP currently listens for SIP signaling over UDP only.  The SIP URI
        transport parameter is therefore advertised explicitly so registrars,
        notifiers, and dialog peers route subsequent requests back over the
        transport pyVoIP can actually receive.

        Keep the local port explicit even when it is the default SIP port
        5060.  RFC 3261 URI comparison does not require an omitted default
        port to compare equal to an explicit port, and a URI with no transport
        parameter can compare differently from the same URI with
        ``;transport=udp``.  Emitting the same explicit Contact binding for
        initial REGISTER, refresh REGISTER, and deregistration avoids stale
        bindings on strict registrars.
        """
        contact_scheme = "sips" if self.server_scheme == "sips" else "sip"
        transport = self.signal_transport()
        transport_token = (
            "TCP"
            if contact_scheme == "sips" and transport == SIPTransport.TLS
            else transport.uri_token
        )

        return self._format_sip_uri(
            self.myIP,
            self.myPort,
            user=user or self.username,
            transport=transport_token,
            always_include_port=True,
            scheme=contact_scheme,
        )

    def _contact_header(self, *, include_instance: bool = False) -> str:
        header = f"Contact: <{self._contact_uri()}>"
        if include_instance:
            header += f';+sip.instance="<urn:uuid:{self.urnUUID}>"'
        return header + "\r\n"

    @staticmethod
    def _sdp_address_type(address: str) -> str:
        try:
            parsed = ipaddress.ip_address(address)
        except ValueError:
            return "IP4"
        return "IP6" if parsed.version == 6 else "IP4"

    def _registrar_uri(
        self,
        *,
        user: Optional[str] = None,
        transport: Optional[str] = None,
    ) -> str:
        return self._format_sip_uri(
            self.server_host,
            self.server_uri_port,
            user=user,
            transport=transport,
            scheme=self.server_scheme,
        )

    def _remote_user_uri(self, user: str) -> str:
        return self._format_sip_uri(
            self.server_host,
            self.server_uri_port,
            user=user,
            scheme=self.server_scheme,
        )

    def _via_header(
        self,
        *,
        branch: Optional[str] = None,
        rport: bool = True,
    ) -> str:
        branch = branch or self.gen_branch()
        line = (
            f"Via: SIP/2.0/{self.signal_transport().via_token} "
            + self._format_hostport(
                self.myIP,
                self.myPort,
                always_include_port=True,
            )
            + f";branch={branch}"
        )
        if rport:
            line += ";rport"
        return line + "\r\n"

    def send_raw(
        self,
        data: bytes,
        target: Optional[Tuple[str, int]] = None,
    ) -> None:
        if target is None:
            target = self.signal_target()

        if self.connection is None:
            sock = getattr(self, "out", None) or getattr(self, "s", None)
            if sock is None:
                raise RuntimeError("SIP client is not connected.")

            try:
                sock.sendto(data, target)
            except OSError as ex:
                fallback = self._udp_send_fallback_target(target)
                if fallback is None:
                    raise
                debug(
                    f"SIP UDP send to {target} failed: {ex}; "
                    + f"retrying via {fallback}"
                )
                sock.sendto(data, fallback)
            return

        try:
            self.connection.send(data, target)
        except OSError as ex:
            fallback = self._udp_send_fallback_target(target)
            if fallback is None:
                raise
            debug(
                f"SIP UDP send to {target} failed: {ex}; "
                + f"retrying via {fallback}"
            )
            self.connection.send(data, fallback)

    def _udp_send_fallback_target(
        self,
        target: Optional[Tuple[str, int]],
    ) -> Optional[Tuple[str, int]]:
        if target is None:
            return None

        try:
            if self.signal_transport() != SIPTransport.UDP:
                return None

            fallback = self.signal_target()
            if (str(target[0]), int(target[1])) == (
                str(fallback[0]),
                int(fallback[1]),
            ):
                return None

            return fallback
        except Exception:
            return None

    def _recv_message_before(self, deadline: float) -> Optional[SIPMessage]:
        if self.connection is None:
            sock = getattr(self, "s", None)
            if sock is None:
                return None

            remaining = max(0.0, deadline - time.monotonic())
            ready = select.select([sock], [], [], remaining)
            if not ready[0]:
                return None

            return self._message_from_raw(sock.recv(8192))

        raw = self.connection.recv_raw_message_before(
            deadline,
            running=lambda: self.NSD,
        )
        return self._message_from_raw(raw) if raw is not None else None

    def _message_from_raw(self, raw: bytes) -> SIPMessage:
        message = SIPMessage(raw)
        source_address = None
        if self.connection is not None:
            source_address = getattr(self.connection, "last_recv_address", None)

        if source_address is not None:
            setattr(message, "source_address", source_address)
            if message.type == SIPMessageType.MESSAGE:
                self._apply_rport_received(message, source_address)

        return message

    @staticmethod
    def _apply_rport_received(
        message: SIPMessage,
        source_address: Tuple[str, int],
    ) -> None:
        try:
            via = message.headers["Via"][0]
        except (KeyError, IndexError, TypeError):
            return

        if not isinstance(via, dict):
            return

        source_host, source_port = source_address
        if "rport" in via and via.get("rport") is None:
            via["rport"] = str(source_port)

        try:
            sent_host = str(via.get("address", ("", ""))[0])
        except Exception:
            sent_host = ""

        if sent_host and sent_host != str(source_host):
            via.setdefault("received", str(source_host))

    def send_response(self, request: SIPMessage, response: str) -> None:
        self._send_request_response(request, response)

    def ack_target(self, request: SIPMessage) -> Tuple[str, int]:
        if self.proxy is not None:
            return self.signal_target()
        if (
            request.type == SIPMessageType.RESPONSE
            and 200 <= int(request.status) < 300
            and request.headers.get("Contact")
        ):
            return self._sip_target_from_uri(
                self._extract_uri(str(request.headers["Contact"])),
                self.port,
            )
        return self.signal_target()

    def _set_last_invite_debug(self, **kwargs) -> None:
        self.last_invite_debug.update(kwargs)
        self.last_invite_debug["updated_at"] = time.time()

    def invite_debug_snapshot(self) -> Dict[str, Any]:
        return dict(self.last_invite_debug)

    def pop_pending_invite_response(self, call_id: str) -> Optional[SIPMessage]:
        return self.pending_invite_responses.pop(call_id, None)

    @staticmethod
    def _summarize_media(
        ms: Dict[int, Dict[str, "RTP.PayloadType"]]
    ) -> Dict[str, Dict[str, str]]:
        summary: Dict[str, Dict[str, str]] = {}
        for port, codecs in ms.items():
            summary[str(port)] = {
                str(payload): str(codec) for payload, codec in codecs.items()
            }
        return summary

    @staticmethod
    def _normalize_subscription_event(event: str) -> str:
        raw = str(event or "presence").strip()
        if not raw:
            raw = "presence"

        parts = [part.strip() for part in raw.split(";") if part.strip()]
        if not parts:
            return "presence"

        aliases = {
            "mwi": "message-summary",
            "message_waiting": "message-summary",
            "registration": "reg",
            "registrations": "reg",
            "call-state": "dialog",
            "dialog-info": "dialog",
        }
        parts[0] = aliases.get(parts[0].lower(), parts[0].lower())
        return ";".join(parts)

    def _default_subscription_accept(self, event: str) -> List[str]:
        event_name = self._normalize_subscription_event(event).split(";", 1)[0]
        if event_name == "presence":
            return [
                "application/pidf+xml",
                "application/pidf-diff+xml",
                "application/xpidf+xml",
                "text/plain",
            ]
        if event_name == "dialog":
            return [
                "application/dialog-info+xml",
                "application/xml",
                "text/plain",
            ]
        if event_name == "message-summary":
            return ["application/simple-message-summary"]
        if event_name == "reg":
            return [
                "application/reginfo+xml",
                "application/xml",
                "text/plain",
            ]
        return ["application/xml", "text/plain"]

    def _normalize_subscription_target(self, target: str) -> str:
        target = str(target or "").strip()
        if not target:
            raise ValueError("Subscription target cannot be empty.")

        if target.startswith("<") and target.endswith(">"):
            target = target[1:-1].strip()

        if re.match(r"^[A-Za-z][A-Za-z0-9+.-]*:", target):
            return target

        if "@" in target:
            return f"{self.server_scheme}:{target}"

        return self._format_sip_uri(
            self.server_host,
            self.server_port,
            user=target,
            scheme=self.server_scheme,
        )

    def _normalize_request_target(self, target: str) -> str:
        """Return a SIP/SIPS request URI for pre-dialog requests.

        Numeric extensions are resolved against the configured registrar, while
        fully-qualified SIP/SIPS URIs and user@domain targets are preserved.
        """
        target = self._extract_uri(str(target or ""))
        if not target:
            raise ValueError("SIP request target cannot be empty.")

        if re.match(r"^[A-Za-z][A-Za-z0-9+.-]*:", target):
            return target

        if "@" in target:
            return f"{self.server_scheme}:{target}"

        return self._remote_user_uri(target)

    def _build_options_request(
        self,
        target_uri: str,
        *,
        call_id: str,
        local_tag: str,
        branch: Optional[str] = None,
        auth_line: str = "",
    ) -> str:
        request = f"OPTIONS {target_uri} SIP/2.0\r\n"
        request += self._via_header(branch=branch, rport=True)
        request += "Max-Forwards: 70\r\n"
        request += self._contact_header()
        request += f"To: <{target_uri}>\r\n"
        request += (
            f"From: <{self._registrar_uri(user=self.username)}>;"
            + f"tag={local_tag}\r\n"
        )
        request += f"Call-ID: {call_id}\r\n"
        request += f"CSeq: {self.optionsCounter.next()} OPTIONS\r\n"
        request += f"Allow: {(', '.join(pyVoIP.SIPCompatibleMethods))}\r\n"
        request += "Accept: application/sdp\r\n"
        request += f"User-Agent: pyVoIP {pyVoIP.__version__}\r\n"
        if auth_line:
            request += auth_line
        request += "Content-Length: 0\r\n\r\n"
        return request

    def options(
        self,
        target: str,
        *,
        timeout: Optional[float] = None,
    ) -> SIPMessage:
        """Send SIP OPTIONS to a peer and return the final response.

        This is useful for pre-call capability and codec discovery. Remote
        codecs are only available when the peer includes an SDP body in the
        OPTIONS response.
        """
        if not self.NSD:
            raise RuntimeError("SIP client is not running.")

        target_uri = self._normalize_request_target(target)
        call_id = self.gen_call_id()
        local_tag = self.gen_tag()
        branch = self.gen_branch()
        request = self._build_options_request(
            target_uri,
            call_id=call_id,
            local_tag=local_tag,
            branch=branch,
        )
        timeout_s = self.options_timeout if timeout is None else float(timeout)
        deadline = time.monotonic() + timeout_s
        retries = 0

        with self.recvLock:
            self.send_raw(request.encode("utf8"), self.signal_target())

            while self.NSD and time.monotonic() < deadline:
                response = self._recv_message_before(deadline)
                if response is None:
                    continue

                if response.type == SIPMessageType.MESSAGE:
                    if response.method == "NOTIFY":
                        self._handle_notify(response)
                    else:
                        self.parse_message(response)
                    continue

                call_id, cseq_check, cseq_method = self._request_transaction(request)
                cseq = response.headers.get("CSeq", {})
                response_cseq_check = cseq.get("check") if isinstance(cseq, dict) else None
                response_cseq_method = cseq.get("method") if isinstance(cseq, dict) else None
                if (
                    response.headers.get("Call-ID") != call_id
                    or response_cseq_check != cseq_check
                    or response_cseq_method != cseq_method
                ):
                    self.parse_message(response)
                    continue

                status_code = int(response.status)
                if 100 <= status_code < 200:
                    continue

                if response.status in (
                    SIPStatus.UNAUTHORIZED,
                    SIPStatus.PROXY_AUTHENTICATION_REQUIRED,
                ) and retries < self.options_max_retries:
                    header_name = "Authorization"
                    if (
                        response.status == SIPStatus.PROXY_AUTHENTICATION_REQUIRED
                        or response.authentication_header == "Proxy-Authenticate"
                    ):
                        header_name = "Proxy-Authorization"

                    auth_line = self._build_digest_auth_header(
                        response,
                        header_name=header_name,
                        method="OPTIONS",
                        uri=target_uri,
                    )
                    branch = self._bump_branch(branch)
                    request = self._build_options_request(
                        target_uri,
                        call_id=call_id,
                        local_tag=local_tag,
                        branch=branch,
                        auth_line=auth_line,
                    )
                    self.send_raw(request.encode("utf8"), self.signal_target())
                    retries += 1
                    continue

                return response

        raise TimeoutError(
            f"OPTIONS timed out after {timeout_s}s "
            + f"(target={target_uri}, Call-ID={call_id})."
        )

    def _subscription_send_target(
        self, subscription: SIPSubscription
    ) -> Tuple[str, int]:
        if self.proxy is not None:
            return self.signal_target()
        if subscription.remote_target:
            return self._sip_target_from_uri(
                subscription.remote_target, self.port
            )
        return self.signal_target()

    def _subscription_to_header(self, subscription: SIPSubscription) -> str:
        line = f"<{subscription.target_uri}>"
        if subscription.remote_tag:
            line += f";tag={subscription.remote_tag}"
        return line

    def _build_subscribe_request(
        self,
        subscription: SIPSubscription,
        *,
        expires: int,
        auth_line: str = "",
        request_uri: Optional[str] = None,
    ) -> str:
        request_uri = (
            request_uri
            or subscription.remote_target
            or subscription.target_uri
        )

        request = f"SUBSCRIBE {request_uri} SIP/2.0\r\n"
        request += self._via_header(rport=True)
        request += "Max-Forwards: 70\r\n"
        request += self._contact_header()
        request += f"To: {self._subscription_to_header(subscription)}\r\n"
        request += (
            f"From: <{self._registrar_uri(user=self.username)}>;"
            + f"tag={subscription.local_tag}\r\n"
        )
        request += f"Call-ID: {subscription.call_id}\r\n"
        request += f"CSeq: {self.subscribeCounter.next()} SUBSCRIBE\r\n"
        request += f"Event: {subscription.event}\r\n"
        request += f"Expires: {max(0, int(expires))}\r\n"
        if subscription.accept:
            request += "Accept: " + ", ".join(subscription.accept) + "\r\n"
        request += f"Allow: {(', '.join(pyVoIP.SIPCompatibleMethods))}\r\n"
        request += f"User-Agent: pyVoIP {pyVoIP.__version__}\r\n"
        if auth_line:
            request += auth_line
        request += "Content-Length: 0\r\n\r\n"
        return request

    def _send_request_response(
        self, request: SIPMessage, response: str
    ) -> None:
        self.send_raw(response.encode("utf8"), self.response_target(request))

    @staticmethod
    def _request_header_value(request: str, header: str) -> str:
        prefix = header.lower() + ":"
        for line in request.split("\r\n"):
            if line.lower().startswith(prefix):
                return line.split(":", 1)[1].strip()
        return ""

    @classmethod
    def _request_transaction(cls, request: str) -> Tuple[str, str, str]:
        cseq = cls._request_header_value(request, "CSeq")
        cseq_parts = cseq.split()
        cseq_check = cseq_parts[0] if cseq_parts else ""
        cseq_method = cseq_parts[1] if len(cseq_parts) > 1 else ""
        return (
            cls._request_header_value(request, "Call-ID"),
            cseq_check,
            cseq_method,
        )

    def _send_register_request(
        self,
        request: str,
        *,
        action: str,
    ) -> SIPMessage:
        self.send_raw(request.encode("utf8"), self.signal_target())
        return self._wait_for_transaction_response(request, action=action)

    def _wait_for_transaction_response(
        self,
        request: str,
        *,
        action: str,
    ) -> SIPMessage:
        call_id, cseq_check, cseq_method = self._request_transaction(request)
        deadline = time.monotonic() + self.register_timeout
        last_provisional: Optional[SIPMessage] = None

        while time.monotonic() < deadline:
            response = self._recv_message_before(deadline)
            if response is None:
                break

            if response.type == SIPMessageType.MESSAGE:
                self.parse_message(response)
                continue

            cseq = response.headers.get("CSeq", {})
            if not isinstance(cseq, dict):
                self.parse_message(response)
                continue

            if (
                response.headers.get("Call-ID") != call_id
                or cseq.get("check") != cseq_check
                or cseq.get("method") != cseq_method
            ):
                self.parse_message(response)
                continue

            if 100 <= int(response.status) < 200:
                last_provisional = response
                continue

            return response

        if last_provisional is not None:
            raise TimeoutError(
                f"Waited {self.register_timeout} seconds but server only "
                + "sent provisional SIP "
                + f"{int(last_provisional.status)} "
                + f"{last_provisional.status.phrase}"
            )
        raise TimeoutError(f"{action} on SIP Server timed out")

    @staticmethod
    def _gen_supported_header() -> str:
        return "Supported:\r\n"

    def gen_response(self, request: SIPMessage, status: SIPStatus) -> str:
        response = f"SIP/2.0 {int(status)} {status.phrase}\r\n"
        response += self._gen_response_via_header(request)

        from_line = request.headers["From"]["raw"]
        if request.headers["From"]["tag"]:
            from_line += f";tag={request.headers['From']['tag']}"
        response += f"From: {from_line}\r\n"

        response += f"To: {self._to_header_with_local_tag(request)}\r\n"

        response += f"Call-ID: {request.headers['Call-ID']}\r\n"
        response += (
            f"CSeq: {request.headers['CSeq']['check']} "
            + f"{request.headers['CSeq']['method']}\r\n"
        )
        response += f"User-Agent: pyVoIP {pyVoIP.__version__}\r\n"
        response += f"Allow: {(', '.join(pyVoIP.SIPCompatibleMethods))}\r\n"
        response += "Content-Length: 0\r\n\r\n"
        return response

    def _to_header_with_local_tag(self, request: SIPMessage) -> str:
        to_line = request.headers["To"]["raw"]
        if request.headers["To"]["tag"]:
            return f"{to_line};tag={request.headers['To']['tag']}"

        call_id = request.headers.get("Call-ID")
        local_tag = self.tagLibrary.get(call_id)
        if local_tag:
            return f"{to_line};tag={local_tag}"

        return f"{to_line};tag={self.gen_tag()}"

    @staticmethod
    def _parse_subscription_state(value: str) -> Dict[str, Any]:
        raw = str(value or "").strip()
        parts = [part.strip() for part in raw.split(";") if part.strip()]

        params: Dict[str, Optional[str]] = {}
        for part in parts[1:]:
            if "=" in part:
                key, val = part.split("=", 1)
                params[key.lower()] = val.strip('"')
            else:
                params[part.lower()] = None

        parsed: Dict[str, Any] = {
            "state": parts[0].lower() if parts else "",
            "reason": params.get("reason"),
            "expires": None,
            "retry_after": None,
            "params": params,
        }
        for field_name, key in (
            ("expires", "expires"),
            ("retry_after", "retry-after"),
        ):
            field_value = params.get(key)
            if field_value is not None:
                try:
                    parsed[field_name] = int(field_value)
                except Exception:
                    parsed[field_name] = None
        return parsed

    def _emit_subscription_event(
        self,
        subscription: SIPSubscription,
        *,
        event_type: str,
        message: Optional[SIPMessage] = None,
    ) -> Dict[str, Any]:
        info = subscription.snapshot()
        info["type"] = event_type

        if message is not None and message.type == SIPMessageType.RESPONSE:
            info["response_code"] = int(message.status)
            info["response_phrase"] = message.status.phrase
            info["response_heading"] = str(
                message.heading, "utf8", errors="replace"
            )
        elif message is not None:
            info["body"] = getattr(message, "body_text", "")
            info["event_header"] = message.headers.get("Event")
            info["content_type"] = message.headers.get("Content-Type")
            info["subscription_state_header"] = message.headers.get(
                "Subscription-State"
            )
        else:
            info["body"] = subscription.last_notify_body

        if self.subscription_callback is not None:
            try:
                self.subscription_callback(dict(info))
            except Exception as ex:
                debug(f"Exception in subscription_callback: {ex}")
        return info

    def _apply_subscribe_response(
        self,
        message: SIPMessage,
        *,
        emit_callback: bool = True,
    ) -> Optional[Dict[str, Any]]:
        call_id = str(message.headers.get("Call-ID", "") or "")
        subscription = self.subscriptions.get(call_id)
        if subscription is None:
            return None

        code = int(message.status)
        subscription.updated_at = time.time()
        subscription.last_response_code = code
        subscription.last_response_phrase = message.status.phrase

        to_header = message.headers.get("To")
        if isinstance(to_header, dict) and to_header.get("tag"):
            subscription.remote_tag = to_header["tag"]

        contact = message.headers.get("Contact")
        if contact:
            subscription.remote_target = self._extract_uri(str(contact))

        expires_header = message.headers.get("Expires")
        if expires_header is not None:
            try:
                subscription.expires = int(expires_header)
            except Exception:
                pass

        if 200 <= code < 300:
            if subscription.pending_expires == 0 or subscription.expires == 0:
                subscription.status = "cancelling"
                subscription.reason = None
            elif subscription.subscription_state in ("active", "pending"):
                subscription.status = subscription.subscription_state
                subscription.reason = None
            else:
                subscription.status = "pending"
                subscription.reason = None
        elif code == int(SIPStatus.CALL_OR_TRANSACTION_DOESNT_EXIST):
            subscription.status = "terminated"
            subscription.reason = f"{code} {message.status.phrase}"
        else:
            if subscription.subscription_state in ("active", "pending"):
                subscription.status = subscription.subscription_state
            else:
                subscription.status = "failed"
            subscription.reason = f"{code} {message.status.phrase}"

        info = subscription.snapshot()
        info["response_code"] = code
        info["response_phrase"] = message.status.phrase
        info["response_heading"] = str(
            message.heading, "utf8", errors="replace"
        )

        if emit_callback:
            info = self._emit_subscription_event(
                subscription,
                event_type="subscribe-response",
                message=message,
            )

        if subscription.status == "terminated":
            self.subscriptions.pop(subscription.call_id, None)

        return info

    def _handle_notify(self, message: SIPMessage) -> None:
        call_id = str(message.headers.get("Call-ID", "") or "")
        subscription = self.subscriptions.get(call_id)
        to_header = message.headers.get("To", {})
        local_tag = to_header.get("tag") if isinstance(to_header, dict) else ""
        event_header = str(message.headers.get("Event", "") or "").strip()

        if (
            subscription is None
            or (
                subscription.local_tag
                and local_tag
                and local_tag != subscription.local_tag
            )
            or (
                event_header
                and self._normalize_subscription_event(event_header)
                != self._normalize_subscription_event(subscription.event)
            )
        ):
            response = self.gen_response(
                message, SIPStatus.CALL_OR_TRANSACTION_DOESNT_EXIST
            )
            self._send_request_response(message, response)
            return

        subscription.updated_at = time.time()
        from_header = message.headers.get("From")
        if isinstance(from_header, dict) and from_header.get("tag"):
            subscription.remote_tag = from_header["tag"]

        contact = message.headers.get("Contact")
        if contact:
            subscription.remote_target = self._extract_uri(str(contact))

        body_text = getattr(message, "body_text", "")
        subscription.last_notify_body = body_text
        subscription.last_notify_headers = {
            "Event": str(message.headers.get("Event", "") or ""),
            "Subscription-State": str(
                message.headers.get("Subscription-State", "") or ""
            ),
            "Content-Type": str(message.headers.get("Content-Type", "") or ""),
        }

        parsed_state = self._parse_subscription_state(
            subscription.last_notify_headers["Subscription-State"]
        )
        if parsed_state["state"]:
            subscription.subscription_state = parsed_state["state"]
            subscription.status = parsed_state["state"]
        if parsed_state["reason"]:
            subscription.reason = parsed_state["reason"]
        if parsed_state.get("expires") is not None:
            subscription.expires = parsed_state["expires"]

        response = self.gen_ok(message)
        self._send_request_response(message, response)

        self._emit_subscription_event(
            subscription,
            event_type="notify",
            message=message,
        )
        if subscription.subscription_state == "terminated":
            self.subscriptions.pop(subscription.call_id, None)

    def list_subscriptions(self) -> List[Dict[str, Any]]:
        subscriptions = sorted(
            self.subscriptions.values(),
            key=lambda item: (item.target_uri, item.event, item.updated_at),
        )
        return [subscription.snapshot() for subscription in subscriptions]

    def _find_subscription(
        self, identifier: str, event: Optional[str] = None
    ) -> Optional[SIPSubscription]:
        ident = str(identifier or "").strip()
        if not ident:
            return None

        event_name = (
            self._normalize_subscription_event(event)
            if event is not None
            else None
        )
        ident_lower = ident.lower()
        matches: List[SIPSubscription] = []
        for subscription in self.subscriptions.values():
            if (
                event_name is not None
                and self._normalize_subscription_event(subscription.event)
                != event_name
            ):
                continue
            if (
                ident_lower == subscription.target.lower()
                or ident_lower == subscription.target_uri.lower()
                or ident_lower == subscription.call_id.lower()
                or subscription.call_id.lower().startswith(ident_lower)
            ):
                matches.append(subscription)

        if not matches:
            return None

        matches.sort(key=lambda item: item.updated_at, reverse=True)
        return matches[0]

    def subscribe_to(
        self,
        target: str,
        *,
        event: str = "presence",
        expires: int = 3600,
        accept: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        if not self.NSD:
            raise RuntimeError("SIP client is not running.")

        target_uri = self._normalize_subscription_target(target)
        event_name = self._normalize_subscription_event(event)
        if accept is None:
            accept_types = self._default_subscription_accept(event_name)
        elif isinstance(accept, str):
            accept_types = [x.strip() for x in accept.split(",") if x.strip()]
        else:
            accept_types = [x for x in accept if str(x).strip()]

        subscription = SIPSubscription(
            call_id=self.gen_call_id(),
            target=str(target).strip(),
            target_uri=target_uri,
            event=event_name,
            accept=accept_types,
            local_tag=self.gen_tag(),
            expires=max(0, int(expires)),
            pending_expires=max(0, int(expires)),
        )
        self.subscriptions[subscription.call_id] = subscription
        self.tagLibrary[subscription.call_id] = subscription.local_tag

        request = self._build_subscribe_request(
            subscription,
            expires=subscription.pending_expires,
        )

        with self.recvLock:
            self.send_raw(
                request.encode("utf8"),
                self._subscription_send_target(subscription),
            )
            deadline = time.monotonic() + self.subscription_timeout
            retries = 0

            while self.NSD and time.monotonic() < deadline:
                response = self._recv_message_before(deadline)
                if response is None:
                    continue

                if response.type == SIPMessageType.MESSAGE:
                    if response.method == "NOTIFY":
                        self._handle_notify(response)
                    else:
                        self.parse_message(response)
                    continue

                cseq = response.headers.get("CSeq", {})
                cseq_method = (
                    cseq.get("method") if isinstance(cseq, dict) else None
                )
                if (
                    response.headers.get("Call-ID") != subscription.call_id
                    or cseq_method != "SUBSCRIBE"
                ):
                    self.parse_message(response)
                    continue

                status_code = int(response.status)
                if 100 <= status_code < 200:
                    continue

                if response.status in (
                    SIPStatus.UNAUTHORIZED,
                    SIPStatus.PROXY_AUTHENTICATION_REQUIRED,
                ) and retries < self.subscription_max_retries:
                    header_name = "Authorization"
                    if (
                        response.status
                        == SIPStatus.PROXY_AUTHENTICATION_REQUIRED
                        or response.authentication_header
                        == "Proxy-Authenticate"
                    ):
                        header_name = "Proxy-Authorization"

                    auth_line = self._build_digest_auth_header(
                        response,
                        header_name=header_name,
                        method="SUBSCRIBE",
                        uri=subscription.remote_target
                        or subscription.target_uri,
                    )
                    request = self._build_subscribe_request(
                        subscription,
                        expires=subscription.pending_expires,
                        auth_line=auth_line,
                    )
                    self.send_raw(
                        request.encode("utf8"),
                        self._subscription_send_target(subscription),
                    )
                    retries += 1
                    continue

                info = self._apply_subscribe_response(
                    response,
                    emit_callback=False,
                ) or subscription.snapshot()
                if 200 <= status_code < 300:
                    return info

                self.subscriptions.pop(subscription.call_id, None)
                raise RuntimeError(
                    f"SUBSCRIBE failed with SIP {status_code} "
                    + f"{response.status.phrase} "
                    + f"(Call-ID={subscription.call_id})."
                )

        self.subscriptions.pop(subscription.call_id, None)
        raise TimeoutError(
            f"SUBSCRIBE timed out after {self.subscription_timeout}s "
            + f"(Call-ID={subscription.call_id})."
        )

    def unsubscribe_from(
        self,
        identifier: str,
        *,
        event: Optional[str] = None,
    ) -> Dict[str, Any]:
        if not self.NSD:
            raise RuntimeError("SIP client is not running.")

        subscription = self._find_subscription(identifier, event=event)
        if subscription is None:
            raise KeyError(f"No active subscription matched '{identifier}'.")

        previous_pending = subscription.pending_expires
        subscription.pending_expires = 0
        request = self._build_subscribe_request(subscription, expires=0)

        with self.recvLock:
            self.send_raw(
                request.encode("utf8"),
                self._subscription_send_target(subscription),
            )
            deadline = time.monotonic() + self.subscription_timeout
            retries = 0

            while self.NSD and time.monotonic() < deadline:
                response = self._recv_message_before(deadline)
                if response is None:
                    continue

                if response.type == SIPMessageType.MESSAGE:
                    if response.method == "NOTIFY":
                        self._handle_notify(response)
                    else:
                        self.parse_message(response)
                    continue

                cseq = response.headers.get("CSeq", {})
                cseq_method = (
                    cseq.get("method") if isinstance(cseq, dict) else None
                )
                if (
                    response.headers.get("Call-ID") != subscription.call_id
                    or cseq_method != "SUBSCRIBE"
                ):
                    self.parse_message(response)
                    continue

                status_code = int(response.status)
                if 100 <= status_code < 200:
                    continue

                if response.status in (
                    SIPStatus.UNAUTHORIZED,
                    SIPStatus.PROXY_AUTHENTICATION_REQUIRED,
                ) and retries < self.subscription_max_retries:
                    header_name = "Authorization"
                    if (
                        response.status
                        == SIPStatus.PROXY_AUTHENTICATION_REQUIRED
                        or response.authentication_header
                        == "Proxy-Authenticate"
                    ):
                        header_name = "Proxy-Authorization"

                    auth_line = self._build_digest_auth_header(
                        response,
                        header_name=header_name,
                        method="SUBSCRIBE",
                        uri=subscription.remote_target
                        or subscription.target_uri,
                    )
                    request = self._build_subscribe_request(
                        subscription,
                        expires=0,
                        auth_line=auth_line,
                    )
                    self.send_raw(
                        request.encode("utf8"),
                        self._subscription_send_target(subscription),
                    )
                    retries += 1
                    continue

                info = self._apply_subscribe_response(
                    response,
                    emit_callback=False,
                ) or subscription.snapshot()
                if 200 <= status_code < 300:
                    return info

                if status_code == int(
                    SIPStatus.CALL_OR_TRANSACTION_DOESNT_EXIST
                ):
                    self.subscriptions.pop(subscription.call_id, None)
                    info["status"] = "terminated"
                    return info

                subscription.pending_expires = previous_pending
                raise RuntimeError(
                    f"Unsubscribe failed with SIP {status_code} "
                    + f"{response.status.phrase} "
                    + f"(Call-ID={subscription.call_id})."
                )

        subscription.pending_expires = previous_pending
        raise TimeoutError(
            f"Unsubscribe timed out after {self.subscription_timeout}s "
            + f"(Call-ID={subscription.call_id})."
        )

    def _gen_sip_version_not_supported_raw(self, raw: bytes) -> str:
        """
        Build a 505 response without requiring SIPMessage parsing (which may
        fail specifically because of the unsupported SIP version).
        """
        lines = [
            str(l, "utf8", errors="replace")
            for l in raw.split(b"\r\n")
            if l
        ]

        def first_header(prefix: str) -> str:
            p = prefix.lower()
            for line in lines:
                if line.lower().startswith(p):
                    return line
            return ""

        via_lines = [l for l in lines if l.lower().startswith("via:")]
        from_line = first_header("From:")
        to_line = first_header("To:")
        call_id_line = first_header("Call-ID:")
        cseq_line = first_header("CSeq:")
        contact_line = first_header("Contact:")

        if to_line and ";tag=" not in to_line.lower():
            to_line = f"{to_line};tag={self.gen_tag()}"

        response = "SIP/2.0 505 SIP Version Not Supported\r\n"
        for v in via_lines:
            response += v + "\r\n"
        if from_line:
            response += from_line + "\r\n"
        if to_line:
            response += to_line + "\r\n"
        if call_id_line:
            response += call_id_line + "\r\n"
        if cseq_line:
            response += cseq_line + "\r\n"
        if contact_line:
            response += contact_line + "\r\n"
        response += f"User-Agent: pyVoIP {pyVoIP.__version__}\r\n"
        response += self._gen_supported_header()
        response += 'Warning: 399 GS "Unable to accept call"\r\n'
        response += f"Allow: {(', '.join(pyVoIP.SIPCompatibleMethods))}\r\n"
        response += "Content-Length: 0\r\n\r\n"
        return response

    def recv_loop(self) -> None:
        while self.NSD:
            try:
                sock = getattr(self, "s", None)
                if sock is None:
                    break
                with acquired_lock_and_unblocked_socket(self.recvLock, sock):
                    self.recv()
            except BlockingIOError:
                time.sleep(0.01)
                continue
            except OSError:
                if self.NSD:
                    time.sleep(0.01)
                    continue
                break

    def recv(self) -> None:
        connection = self.connection
        if connection is None:
            return

        try:
            raw = connection.recv_raw_message()
        except BlockingIOError:
            # Re-raise so recv_loop() can release locks and continue
            raise

        if raw == b"\x00\x00\x00\x00":
            return

        try:
            message = self._message_from_raw(raw)
        except SIPParseError as e:
            if "SIP Version" in str(e):
                try:
                    resp = self._gen_sip_version_not_supported_raw(raw)
                    target = (
                        getattr(connection, "last_recv_address", None)
                        or self.signal_target()
                    )
                    self.send_raw(resp.encode("utf8"), target)
                except Exception as ex:
                    debug(f"Failed sending 505 response: {ex}")
            else:
                start_line = raw.split(b"\r\n", 1)[0]
                debug(
                    f"SIPParseError in SIP.recv: {type(e)}, {e}",
                    f"SIPParseError: {e} (start_line={start_line!r})",
                )
            return
        except Exception as ex:
            start_line = raw.split(b"\r\n", 1)[0]
            debug(
                f"Error on header parsing: {ex}",
                f"Error parsing SIP message: {ex} (start_line={start_line!r})",
            )
            return

        debug(message.summary())
        self.parse_message(message)

    def parseMessage(self, message: SIPMessage) -> None:
        warnings.warn(
            "parseMessage is deprecated due to PEP8 compliance. "
            + "Use parse_message instead.",
            DeprecationWarning,
            stacklevel=2,
        )
        return self.parse_message(message)

    def _has_matching_call(self, message: SIPMessage) -> bool:
        call_id = message.headers.get("Call-ID")
        phone_calls = getattr(self.phone, "calls", None)
        if isinstance(phone_calls, dict):
            return call_id in phone_calls
        return self.callCallback is not None

    def parse_message(self, message: SIPMessage) -> None:
        # Responses (SIP/2.0 ...)
        if message.type != SIPMessageType.MESSAGE:
            cseq = message.headers.get("CSeq", {})
            cseq_method = cseq.get("method") if isinstance(cseq, dict) else cseq
            debug(
                message.summary(),
                "SIP response "
                + f"call_id={message.headers.get('Call-ID')} "
                + f"cseq={cseq_method} "
                + f"status={int(message.status)} {message.status.phrase}",
            )

            if cseq_method == "INVITE":
                code = int(message.status)

                if 100 <= code < 180:
                    event = "invite-provisional"
                    state = "dialing"
                elif 180 <= code < 200:
                    event = "invite-provisional"
                    state = "ringing"
                elif 200 <= code < 300:
                    event = "invite-final"
                    state = "connected"
                else:
                    event = "invite-final"
                    state = "failed"

                self._set_last_invite_debug(
                    event=event,
                    status=state,
                    call_id=message.headers.get("Call-ID"),
                    status_code=code,
                    phrase=message.status.phrase,
                    description=message.status.description,
                    response_heading=str(message.heading, "utf8", errors="replace"),
                    error=None,
                )

            if cseq_method == "SUBSCRIBE":
                self._apply_subscribe_response(message)

            if self.callCallback is not None:
                try:
                    self.callCallback(message)
                except Exception as ex:
                    debug(
                        f"Exception in callCallback: {ex}\n{message.summary()}",
                        f"Exception in callCallback: {ex} "
                        f"(start_line={str(message.heading,'utf8',errors='replace')})",
                    )
            else:
                debug(
                    message.summary(),
                    "Received SIP response but no callCallback is set: "
                    + str(message.heading, "utf8", errors="replace"),
                )
            sock = getattr(self, "s", None)
            if sock is not None:
                sock.setblocking(True)
            return
        elif message.method == "INVITE":
            if self.callCallback is None:
                request = self.gen_busy(message)
                self._send_request_response(message, request)
            else:
                self.callCallback(message)
        elif message.method == "BYE":
            if not self._has_matching_call(message):
                response = self.gen_response(
                    message, SIPStatus.CALL_OR_TRANSACTION_DOESNT_EXIST
                )
                self._send_request_response(message, response)
                return
            if self.callCallback is not None:
                self.callCallback(message)
            response = self.gen_ok(message)
            self._send_request_response(message, response)
            # BYE comes from client cause server only acts as mediator
        elif message.method == "ACK":
            return
        elif message.method == "CANCEL":
            if not self._has_matching_call(message):
                response = self.gen_response(
                    message, SIPStatus.CALL_OR_TRANSACTION_DOESNT_EXIST
                )
                self._send_request_response(message, response)
                return
            response = self.gen_ok(message)
            self._send_request_response(message, response)
            if self.callCallback is not None:
                self.callCallback(message)
        elif message.method == "OPTIONS":
            # Common keep-alive / capability probe. Reply 200 OK.
            response = self.gen_ok(message)
            self._send_request_response(message, response)
        elif message.method == "NOTIFY":
            self._handle_notify(message)
        else:
            response = self.gen_response(
                message, SIPStatus.NOT_IMPLEMENTED
            )
            self._send_request_response(message, response)
            debug(
                message.summary(),
                "Unsupported SIP method "
                + f"{message.method}; replied with "
                + f"{int(SIPStatus.NOT_IMPLEMENTED)} "
                + f"{SIPStatus.NOT_IMPLEMENTED.phrase}",
            )

    def start(self) -> None:
        if self.NSD:
            raise RuntimeError("Attempted to start already started SIPClient")
        self.NSD = True
        target = self.proxy_target or self.server_target
        self.connection = SIPConnection(
            self.myIP,
            self.myPort,
            target,
            tls_context=self.tls_context,
            tls_server_name=self.tls_server_name,
        )
        self.connection.open()
        self.s = self.connection.socket
        self.out = self.s
        self.register()
        t = Timer(1, self.recv_loop)
        t.name = "SIP Receive"
        t.daemon = True
        t.start()

    def stop(self) -> None:
        if not self.NSD:
            return
        try:
            if self.registerThread:
                # Only run if registerThread exists
                self.registerThread.cancel()
                self.deregister()
        finally:
            self.NSD = False
            self._close_sockets()

    def _close_sockets(self) -> None:
        if self.connection is not None:
            self.connection.close()
            self.connection = None
            return

        if hasattr(self, "s") and self.s:
            self.s.close()
        if hasattr(self, "out") and self.out:
            self.out.close()

    def genCallID(self) -> str:
        warnings.warn(
            "genCallID is deprecated due to PEP8 compliance. "
            + "Use gen_call_id instead.",
            DeprecationWarning,
            stacklevel=2,
        )
        return self.gen_call_id()

    def gen_call_id(self) -> str:
        hash = hashlib.sha256(str(self.callID.next()).encode("utf8"))
        hhash = hash.hexdigest()
        return f"{hhash[0:32]}@{self.myIP}:{self.myPort}"

    def _get_register_call_id(self) -> str:
        if self.register_call_id is None:
            self.register_call_id = self.gen_call_id()
        return self.register_call_id

    def lastCallID(self) -> str:
        warnings.warn(
            "lastCallID is deprecated due to PEP8 compliance. "
            + "Use gen_last_call_id instead.",
            DeprecationWarning,
            stacklevel=2,
        )
        return self.gen_last_call_id()

    def gen_last_call_id(self) -> str:
        hash = hashlib.sha256(str(self.callID.current() - 1).encode("utf8"))
        hhash = hash.hexdigest()
        return f"{hhash[0:32]}@{self.myIP}:{self.myPort}"

    def genTag(self) -> str:
        warnings.warn(
            "genTag is deprecated due to PEP8 compliance. "
            + "Use gen_tag instead.",
            DeprecationWarning,
            stacklevel=2,
        )
        return self.gen_tag()

    def gen_tag(self) -> str:
        # Keep as True instead of NSD so it can generate a tag on deregister.
        while True:
            rand = str(random.randint(1, 4294967296)).encode("utf8")
            tag = hashlib.md5(rand).hexdigest()[0:8]
            if tag not in self.tags:
                self.tags.append(tag)
                return tag
        return ""

    def genSIPVersionNotSupported(self, request: SIPMessage) -> str:
        warnings.warn(
            "genSIPVersionNotSupported is deprecated "
            + "due to PEP8 compliance. "
            + "Use gen_sip_version_not_supported instead.",
            DeprecationWarning,
            stacklevel=2,
        )
        return self.gen_sip_version_not_supported(request)

    def gen_sip_version_not_supported(self, request: SIPMessage) -> str:
        response = "SIP/2.0 505 SIP Version Not Supported\r\n"
        response += self._gen_response_via_header(request)
        response += (
            f"From: {request.headers['From']['raw']};tag="
            + f"{request.headers['From']['tag']}\r\n"
        )
        response += (
            f"To: {request.headers['To']['raw']};tag="
            + f"{self.gen_tag()}\r\n"
        )
        response += f"Call-ID: {request.headers['Call-ID']}\r\n"
        response += (
            f"CSeq: {request.headers['CSeq']['check']} "
            + f"{request.headers['CSeq']['method']}\r\n"
        )
        response += f"Contact: {request.headers['Contact']}\r\n"
        response += f"User-Agent: pyVoIP {pyVoIP.__version__}\r\n"
        response += self._gen_supported_header()
        response += 'Warning: 399 GS "Unable to accept call"\r\n'
        response += f"Allow: {(', '.join(pyVoIP.SIPCompatibleMethods))}\r\n"
        response += "Content-Length: 0\r\n\r\n"

        return response

    def genAuthorization(self, request: SIPMessage) -> bytes:
        warnings.warn(
            "genAuthorization is deprecated "
            + "due to PEP8 compliance. Use gen_authorization instead.",
            DeprecationWarning,
            stacklevel=2,
        )
        return self.gen_authorization(request)

    def gen_authorization(self, request: SIPMessage) -> bytes:
        header_name = request.authentication_header
        if (
            header_name is None
            and request.status == SIPStatus.PROXY_AUTHENTICATION_REQUIRED
        ):
            header_name = "Proxy-Authenticate"

        challenge_header = self._authorization_challenge_header(header_name)
        auth = self._select_auth_challenge(request, challenge_header)

        realm = auth["realm"]
        user = self._auth_user_for_header(header_name)

        HA1 = user + ":" + realm + ":" + self.password
        HA1 = hashlib.md5(HA1.encode("utf8")).hexdigest()
        HA2 = (
            ""
            + request.headers["CSeq"]["method"]
            + ":"
            + self._registrar_uri()
        )
        HA2 = hashlib.md5(HA2.encode("utf8")).hexdigest()
        nonce = auth["nonce"]
        response = (HA1 + ":" + nonce + ":" + HA2).encode("utf8")
        response = hashlib.md5(response).hexdigest().encode("utf8")

        return response

    def genBranch(self, length=32) -> str:
        """
        Generate unique branch id according to
        https://datatracker.ietf.org/doc/html/rfc3261#section-8.1.1.7
        """
        warnings.warn(
            "genBranch is deprecated due to PEP8 compliance. "
            + "Use gen_branch instead.",
            DeprecationWarning,
            stacklevel=2,
        )
        return self.gen_branch(length)

    def gen_branch(self, length=32) -> str:
        """
        Generate unique branch id according to
        https://datatracker.ietf.org/doc/html/rfc3261#section-8.1.1.7
        """
        branchid = uuid.uuid4().hex[: length - 7]
        return f"z9hG4bK{branchid}"

    def gen_urn_uuid(self) -> str:
        """
        Generate client instance specific urn:uuid
        """
        return str(uuid.uuid4()).upper()

    def genFirstRequest(self, deregister=False) -> str:
        warnings.warn(
            "genFirstResponse is deprecated "
            + "due to PEP8 compliance. "
            + "Use gen_first_response instead.",
            DeprecationWarning,
            stacklevel=2,
        )
        return self.gen_first_response(deregister)

    def gen_first_response(self, deregister=False) -> str:
        regRequest = f"REGISTER {self._registrar_uri()} SIP/2.0\r\n"
        regRequest += self._via_header(rport=True)
        regRequest += (
            f'From: "{self.username}" '
            + f"<{self._registrar_uri(user=self.username)}>;tag="
            + f'{self.tagLibrary["register"]}\r\n'
        )
        regRequest += (
            f'To: "{self.username}" '
            + f"<{self._registrar_uri(user=self.username)}>\r\n"
        )
        regRequest += f"Call-ID: {self._get_register_call_id()}\r\n"
        regRequest += f"CSeq: {self.registerCounter.next()} REGISTER\r\n"
        regRequest += self._contact_header(include_instance=True)
        regRequest += f'Allow: {(", ".join(pyVoIP.SIPCompatibleMethods))}\r\n'
        regRequest += "Max-Forwards: 70\r\n"
        regRequest += "Allow-Events: org.3gpp.nwinitdereg\r\n"
        regRequest += f"User-Agent: pyVoIP {pyVoIP.__version__}\r\n"
        # Supported: 100rel, replaces, from-change, gruu
        regRequest += (
            "Expires: "
            + f"{self.default_expires if not deregister else 0}\r\n"
        )
        regRequest += "Content-Length: 0"
        regRequest += "\r\n\r\n"

        return regRequest

    def genSubscribe(self, response: SIPMessage) -> str:
        warnings.warn(
            "genSubscribe is deprecated due to PEP8 compliance. "
            + "Use gen_subscribe instead.",
            DeprecationWarning,
            stacklevel=2,
        )
        return self.gen_subscribe(response)

    def gen_subscribe(self, response: SIPMessage) -> str:
        subRequest = (
            f"SUBSCRIBE {self._registrar_uri(user=self.username)} SIP/2.0\r\n"
        )
        subRequest += self._via_header(rport=True)
        subRequest += (
            f'From: "{self.username}" '
            + f"<{self._registrar_uri(user=self.username)}>;tag="
            + f"{self.gen_tag()}\r\n"
        )
        subRequest += f"To: <{self._registrar_uri(user=self.username)}>\r\n"
        subRequest += f'Call-ID: {response.headers["Call-ID"]}\r\n'
        subRequest += f"CSeq: {self.subscribeCounter.next()} SUBSCRIBE\r\n"
        subRequest += self._contact_header(include_instance=True)
        subRequest += "Max-Forwards: 70\r\n"
        subRequest += f"User-Agent: pyVoIP {pyVoIP.__version__}\r\n"
        subRequest += f"Expires: {self.default_expires * 2}\r\n"
        subRequest += "Event: message-summary\r\n"
        subRequest += "Accept: application/simple-message-summary\r\n"
        subRequest += "Content-Length: 0"
        subRequest += "\r\n\r\n"

        return subRequest

    def genRegister(self, request: SIPMessage, deregister=False) -> str:
        warnings.warn(
            "genRegister is deprecated due to PEP8 compliance. "
            + "Use gen_register instead.",
            DeprecationWarning,
            stacklevel=2,
        )
        return self.gen_register(request, deregister)

    def gen_register(self, request: SIPMessage, deregister=False) -> str:
        header_name = "Authorization"
        if (
            request.status == SIPStatus.PROXY_AUTHENTICATION_REQUIRED
            or request.authentication_header == "Proxy-Authenticate"
        ):
            header_name = "Proxy-Authorization"

        auth_line = self._build_digest_auth_header(
            request,
            header_name=header_name,
            method="REGISTER",
            uri=self._registrar_uri(),
        )

        regRequest = f"REGISTER {self._registrar_uri()} SIP/2.0\r\n"

        regRequest += self._via_header(rport=True)
        regRequest += (
            f'From: "{self.username}" '
            + f"<{self._registrar_uri(user=self.username)}>;tag="
            + f'{self.tagLibrary["register"]}\r\n'
        )
        regRequest += (
            f'To: "{self.username}" '
            + f"<{self._registrar_uri(user=self.username)}>\r\n"
        )
        call_id = request.headers.get("Call-ID", self.gen_call_id())
        regRequest += f"Call-ID: {call_id}\r\n"
        regRequest += f"CSeq: {self.registerCounter.next()} REGISTER\r\n"
        regRequest += self._contact_header(include_instance=True)
        regRequest += f'Allow: {(", ".join(pyVoIP.SIPCompatibleMethods))}\r\n'
        regRequest += "Max-Forwards: 70\r\n"
        regRequest += "Allow-Events: org.3gpp.nwinitdereg\r\n"
        regRequest += f"User-Agent: pyVoIP {pyVoIP.__version__}\r\n"
        regRequest += (
            "Expires: "
            + f"{self.default_expires if not deregister else 0}\r\n"
        )

        regRequest += auth_line
        regRequest += "Content-Length: 0"
        regRequest += "\r\n\r\n"

        return regRequest

    def genBusy(self, request: SIPMessage) -> str:
        warnings.warn(
            "genBusy is deprecated due to PEP8 compliance. "
            + "Use gen_busy instead.",
            DeprecationWarning,
            stacklevel=2,
        )
        return self.gen_busy(request)

    def gen_busy(self, request: SIPMessage) -> str:
        response = "SIP/2.0 486 Busy Here\r\n"
        response += self._gen_response_via_header(request)
        response += (
            f"From: {request.headers['From']['raw']};tag="
            + f"{request.headers['From']['tag']}\r\n"
        )
        response += f"To: {self._to_header_with_local_tag(request)}\r\n"
        response += f"Call-ID: {request.headers['Call-ID']}\r\n"
        response += (
            f"CSeq: {request.headers['CSeq']['check']} "
            + f"{request.headers['CSeq']['method']}\r\n"
        )
        response += self._contact_header()
        response += f"User-Agent: pyVoIP {pyVoIP.__version__}\r\n"
        response += self._gen_supported_header()
        response += 'Warning: 399 GS "Unable to accept call"\r\n'
        response += f"Allow: {(', '.join(pyVoIP.SIPCompatibleMethods))}\r\n"
        response += "Content-Length: 0\r\n\r\n"

        return response

    def genOk(self, request: SIPMessage) -> str:
        warnings.warn(
            "genOk is deprecated due to PEP8 compliance. "
            + "Use gen_ok instead.",
            DeprecationWarning,
            stacklevel=2,
        )
        return self.gen_ok(request)

    def gen_ok(self, request: SIPMessage) -> str:
        okResponse = "SIP/2.0 200 OK\r\n"
        okResponse += self._gen_response_via_header(request)
        okResponse += (
            f"From: {request.headers['From']['raw']};tag="
            + f"{request.headers['From']['tag']}\r\n"
        )
        to_line = request.headers["To"]["raw"]
        if request.headers["To"]["tag"]:
            to_line += f";tag={request.headers['To']['tag']}"
        else:
            to_line += f";tag={self.gen_tag()}"
        okResponse += f"To: {to_line}\r\n"
        okResponse += f"Call-ID: {request.headers['Call-ID']}\r\n"
        okResponse += (
            f"CSeq: {request.headers['CSeq']['check']} "
            + f"{request.headers['CSeq']['method']}\r\n"
        )
        okResponse += f"User-Agent: pyVoIP {pyVoIP.__version__}\r\n"
        okResponse += f"Allow: {(', '.join(pyVoIP.SIPCompatibleMethods))}\r\n"
        okResponse += "Content-Length: 0\r\n\r\n"

        return okResponse

    def genRinging(self, request: SIPMessage) -> str:
        warnings.warn(
            "genRinging is deprecated due to PEP8 compliance. "
            + "Use gen_ringing instead.",
            DeprecationWarning,
            stacklevel=2,
        )
        return self.gen_ringing(request)

    def gen_ringing(self, request: SIPMessage) -> str:
        tag = self.gen_tag()
        regRequest = "SIP/2.0 180 Ringing\r\n"
        regRequest += self._gen_response_via_header(request)
        regRequest += (
            f"From: {request.headers['From']['raw']};tag="
            + f"{request.headers['From']['tag']}\r\n"
        )
        regRequest += f"To: {request.headers['To']['raw']};tag={tag}\r\n"
        regRequest += f"Call-ID: {request.headers['Call-ID']}\r\n"
        regRequest += (
            f"CSeq: {request.headers['CSeq']['check']} "
            + f"{request.headers['CSeq']['method']}\r\n"
        )
        regRequest += self._contact_header()
        regRequest += f"User-Agent: pyVoIP {pyVoIP.__version__}\r\n"
        regRequest += self._gen_supported_header()
        regRequest += f"Allow: {(', '.join(pyVoIP.SIPCompatibleMethods))}\r\n"
        regRequest += "Content-Length: 0\r\n\r\n"

        self.tagLibrary[request.headers["Call-ID"]] = tag

        return regRequest

    def genAnswer(
        self,
        request: SIPMessage,
        sess_id: str,
        ms: Dict[int, Dict[int, "RTP.PayloadType"]],
        sendtype: "RTP.TransmitType",
    ) -> str:
        warnings.warn(
            "genAnswer is deprecated due to PEP8 compliance. "
            + "Use gen_answer instead.",
            DeprecationWarning,
            stacklevel=2,
        )
        return self.gen_answer(request, sess_id, ms, sendtype)

    def gen_answer(
        self,
        request: SIPMessage,
        sess_id: str,
        ms: Dict[int, Dict[int, "RTP.PayloadType"]],
        sendtype: "RTP.TransmitType",
    ) -> str:
        # Generate body first for content length
        addr_type = self._sdp_address_type(self.myIP)
        body = "v=0\r\n"
        body += (
            f"o=pyVoIP {sess_id} {int(sess_id)+2} "
            + f"IN {addr_type} {self.myIP}\r\n"
        )

        body += f"s=pyVoIP {pyVoIP.__version__}\r\n"
        body += f"c=IN {addr_type} {self.myIP}\r\n"
        body += "t=0 0\r\n"
        for x in ms:
            body += f"m=audio {x} RTP/AVP"
            for m in ms[x]:
                body += f" {m}"
            body += "\r\n"
            for m in ms[x]:
                codec = ms[x][m]
                body += (
                    "a=rtpmap:"
                    + pyVoIP.RTP.rtpmap_for_payload_type(m, codec)
                    + "\r\n"
                )
                for fmtp in pyVoIP.RTP.fmtp_for_payload_type(m, codec):
                    body += f"a=fmtp:{m} {fmtp}\r\n"
            body += "a=ptime:20\r\n"
            body += "a=maxptime:150\r\n"
            body += f"a={sendtype}\r\n"

        body_bytes = body.encode("utf8")
        tag = self.tagLibrary[request.headers["Call-ID"]]

        regRequest = "SIP/2.0 200 OK\r\n"
        regRequest += self._gen_response_via_header(request)
        regRequest += (
            f"From: {request.headers['From']['raw']};tag="
            + f"{request.headers['From']['tag']}\r\n"
        )
        regRequest += f"To: {request.headers['To']['raw']};tag={tag}\r\n"
        regRequest += f"Call-ID: {request.headers['Call-ID']}\r\n"
        regRequest += (
            f"CSeq: {request.headers['CSeq']['check']} "
            + f"{request.headers['CSeq']['method']}\r\n"
        )
        regRequest += self._contact_header()
        regRequest += f"User-Agent: pyVoIP {pyVoIP.__version__}\r\n"
        regRequest += self._gen_supported_header()
        regRequest += f"Allow: {(', '.join(pyVoIP.SIPCompatibleMethods))}\r\n"
        regRequest += "Content-Type: application/sdp\r\n"
        regRequest += f"Content-Length: {len(body_bytes)}\r\n\r\n"
        regRequest += body

        return regRequest

    def genInvite(
        self,
        number: str,
        sess_id: str,
        ms: Dict[int, Dict[str, "RTP.PayloadType"]],
        sendtype: "RTP.TransmitType",
        branch: str,
        call_id: str,
    ) -> str:
        warnings.warn(
            "genInvite is deprecated due to PEP8 compliance. "
            + "Use gen_invite instead.",
            DeprecationWarning,
            stacklevel=2,
        )
        return self.gen_invite(number, sess_id, ms, sendtype, branch, call_id)

    def gen_invite(
        self,
        number: str,
        sess_id: str,
        ms: Dict[int, Dict[str, "RTP.PayloadType"]],
        sendtype: "RTP.TransmitType",
        branch: str,
        call_id: str,
    ) -> str:
        # Generate body first for content length
        addr_type = self._sdp_address_type(self.myIP)
        body = "v=0\r\n"
        body += (
            f"o=pyVoIP {sess_id} {int(sess_id)+2} "
            + f"IN {addr_type} {self.myIP}\r\n"
        )

        body += f"s=pyVoIP {pyVoIP.__version__}\r\n"
        body += f"c=IN {addr_type} {self.myIP}\r\n"
        body += "t=0 0\r\n"
        for x in ms:
            body += f"m=audio {x} RTP/AVP"
            for m in ms[x]:
                body += f" {m}"
            body += "\r\n"
            for m in ms[x]:
                codec = ms[x][m]
                body += (
                    "a=rtpmap:"
                    + pyVoIP.RTP.rtpmap_for_payload_type(m, codec)
                    + "\r\n"
                )
                for fmtp in pyVoIP.RTP.fmtp_for_payload_type(m, codec):
                    body += f"a=fmtp:{m} {fmtp}\r\n"
            body += "a=ptime:20\r\n"
            body += "a=maxptime:150\r\n"
            body += f"a={sendtype}\r\n"

        body_bytes = body.encode("utf8")
        tag = self.tagLibrary.get(call_id)
        if tag is None:
            tag = self.gen_tag()
            self.tagLibrary[call_id] = tag

        remote_uri = self._normalize_request_target(number)
        invRequest = f"INVITE {remote_uri} SIP/2.0\r\n"
        invRequest += self._via_header(branch=branch, rport=True)
        invRequest += "Max-Forwards: 70\r\n"
        invRequest += self._contact_header()
        invRequest += f"To: <{remote_uri}>\r\n"
        invRequest += f"From: <{self._registrar_uri(user=self.username)}>;tag={tag}\r\n"
        invRequest += f"Call-ID: {call_id}\r\n"
        invRequest += f"CSeq: {self.inviteCounter.next()} INVITE\r\n"
        invRequest += f"Allow: {(', '.join(pyVoIP.SIPCompatibleMethods))}\r\n"
        invRequest += "Content-Type: application/sdp\r\n"
        invRequest += f"User-Agent: pyVoIP {pyVoIP.__version__}\r\n"
        invRequest += f"Content-Length: {len(body_bytes)}\r\n\r\n"
        invRequest += body

        return invRequest

    def genCancel(self, request: SIPMessage) -> str:
        warnings.warn(
            "genCancel is deprecated due to PEP8 compliance. "
            + "Use gen_cancel instead.",
            DeprecationWarning,
            stacklevel=2,
        )
        return self.gen_cancel(request)

    def gen_cancel(self, request: SIPMessage) -> str:
        cancel_request = f"CANCEL {request.uri} SIP/2.0\r\n"
        cancel_request += self._gen_response_via_header(request)
        cancel_request += "Max-Forwards: 70\r\n"
        cancel_request += f"To: {request.headers['To']['raw']}\r\n"
        cancel_request += (
            f"From: {request.headers['From']['raw']};tag="
            + f"{request.headers['From']['tag']}\r\n"
        )
        cancel_request += f"Call-ID: {request.headers['Call-ID']}\r\n"
        cancel_request += (
            f"CSeq: {request.headers['CSeq']['check']} CANCEL\r\n"
        )
        cancel_request += f"User-Agent: pyVoIP {pyVoIP.__version__}\r\n"
        cancel_request += "Content-Length: 0\r\n\r\n"
        return cancel_request

    def cancel(self, request: SIPMessage) -> None:
        message = self.gen_cancel(request)
        self.send_raw(message.encode("utf8"), self.signal_target())

    def genBye(self, request: SIPMessage) -> str:
        warnings.warn(
            "genBye is deprecated due to PEP8 compliance. "
            + "Use gen_bye instead.",
            DeprecationWarning,
            stacklevel=2,
        )
        return self.gen_bye(request)

    def gen_bye(self, request: SIPMessage) -> str:
        tag = self.tagLibrary[request.headers["Call-ID"]]
        c = self._dialog_remote_uri(request)
        byeRequest = f"BYE {c} SIP/2.0\r\n"
        byeRequest += self._via_header(rport=True)
        fromH = request.headers["From"]["raw"]
        toH = request.headers["To"]["raw"]
        if request.headers["From"]["tag"] == tag:
            byeRequest += f"From: {fromH};tag={tag}\r\n"
            if request.headers["To"]["tag"] != "":
                to = toH + ";tag=" + request.headers["To"]["tag"]
            else:
                to = toH
            byeRequest += f"To: {to}\r\n"
        else:
            byeRequest += (
                f"To: {fromH};tag=" + f"{request.headers['From']['tag']}\r\n"
            )
            byeRequest += f"From: {toH};tag={tag}\r\n"
        byeRequest += f"Call-ID: {request.headers['Call-ID']}\r\n"
        cseq = int(request.headers["CSeq"]["check"]) + 1
        byeRequest += f"CSeq: {cseq} BYE\r\n"
        byeRequest += "Max-Forwards: 70\r\n"
        byeRequest += self._contact_header()
        byeRequest += f"User-Agent: pyVoIP {pyVoIP.__version__}\r\n"
        byeRequest += f"Allow: {(', '.join(pyVoIP.SIPCompatibleMethods))}\r\n"
        byeRequest += "Content-Length: 0\r\n\r\n"

        return byeRequest

    def genAck(self, request: SIPMessage) -> str:
        warnings.warn(
            "genAck is deprecated due to PEP8 compliance. "
            + "Use gen_ack instead.",
            DeprecationWarning,
            stacklevel=2,
        )
        return self.gen_ack(request)

    def gen_ack(self, request: SIPMessage) -> str:
        call_id = request.headers["Call-ID"]
        tag = self.tagLibrary.get(call_id)
        if tag is None:
            tag = request.headers["From"].get("tag", "")

        is_2xx = (
            request.type == SIPMessageType.RESPONSE
            and 200 <= int(request.status) < 300
        )

        if is_2xx and request.headers.get("Contact"):
            ack_uri = self._extract_uri(str(request.headers["Contact"]))
            via = self._via_header(rport=True)
        else:
            ack_uri = request.headers["To"]["raw"].lstrip("<").rstrip(">")
            via = self._gen_response_via_header(request)

        ackMessage = f"ACK {ack_uri} SIP/2.0\r\n"
        ackMessage += via
        ackMessage += "Max-Forwards: 70\r\n"

        to_line = request.headers["To"]["raw"]
        if request.headers["To"]["tag"]:
            to_line += f";tag={request.headers['To']['tag']}"
        ackMessage += f"To: {to_line}\r\n"

        ackMessage += f"From: {request.headers['From']['raw']}"
        if tag:
            ackMessage += f";tag={tag}"
        ackMessage += "\r\n"
        ackMessage += f"Call-ID: {request.headers['Call-ID']}\r\n"
        ackMessage += f"CSeq: {request.headers['CSeq']['check']} ACK\r\n"
        ackMessage += f"User-Agent: pyVoIP {pyVoIP.__version__}\r\n"
        ackMessage += "Content-Length: 0\r\n\r\n"

        return ackMessage

    def _gen_response_via_header(self, request: SIPMessage) -> str:
        via = ""
        for h_via in request.headers["Via"]:
            v_line = (
                "Via: "
                + f'{h_via.get("type", "SIP/2.0/UDP")} '
                + self._format_hostport(
                    h_via["address"][0],
                    int(h_via["address"][1]),
                    always_include_port=True,
                )
            )
            rendered_params = {"type", "transport", "address"}
            if "branch" in h_via.keys():
                v_line += f';branch={h_via["branch"]}'
                rendered_params.add("branch")
            if "rport" in h_via.keys():
                if h_via["rport"] is not None:
                    v_line += f';rport={h_via["rport"]}'
                else:
                    v_line += ";rport"
                rendered_params.add("rport")
            if "received" in h_via.keys():
                v_line += f';received={h_via["received"]}'
                rendered_params.add("received")
            for key, value in h_via.items():
                if key in rendered_params:
                    continue
                if value is None:
                    v_line += f";{key}"
                else:
                    v_line += f";{key}={value}"
            v_line += "\r\n"
            via += v_line
        return via

    def invite(
        self,
        number: str,
        ms: Dict[int, Dict[str, "RTP.PayloadType"]],
        sendtype: "RTP.TransmitType",
    ) -> Tuple[SIPMessage, str, int]:
        branch = "z9hG4bK" + self.gen_call_id()[0:25]
        call_id = self.gen_call_id()
        sess_id = self.sessID.next()
        media_summary = self._summarize_media(ms)
        signal_host, signal_port = self.signal_target()
        self._set_last_invite_debug(
            event="invite-created",
            status="dialing",
            number=number,
            call_id=call_id,
            sess_id=sess_id,
            branch=branch,
            local_addr=f"{self.myIP}:{self.myPort}",
            target=f"{signal_host}:{signal_port}",
            proxy=(None if self.proxy is None else f"{self.proxy}:{self.proxy_port}"),
            registrar=f"{self.server_host}:{self.server_port}",
            media=media_summary,
            sendtype=str(sendtype),
            status_code=None,
            phrase=None,
            description=None,
            response_heading=None,
            retries=0,
            error=None,
        )
        debug(
            f"INVITE created for {number}",
            "INVITE created "
            + f"call_id={call_id} number={number} "
            + f"branch={branch} sess_id={sess_id} "
            + f"target={signal_host}:{signal_port} media={media_summary}",
        )

        invite = self.gen_invite(
            number, str(sess_id), ms, sendtype, branch, call_id
        )
        with self.recvLock:
            self.send_raw(invite.encode("utf8"), self.signal_target())
            self._set_last_invite_debug(event="invite-sent")
            debug("Invited")
            deadline = time.monotonic() + self.invite_timeout
            retries = 0
            last_response: Optional[SIPMessage] = None

            while self.NSD and time.monotonic() < deadline:
                response = self._recv_message_before(deadline)
                if response is None:
                    continue

                last_response = response

                if response.type == SIPMessageType.MESSAGE:
                    self.parse_message(response)
                    continue

                cseq = response.headers.get("CSeq", {})
                cseq_method = cseq.get("method") if isinstance(cseq, dict) else None
                if (
                    response.headers.get("Call-ID") != call_id
                    or cseq_method != "INVITE"
                ):
                    # Not our transaction, process normally.
                    self.parse_message(response)
                    continue

                status_code = int(response.status)
                self._set_last_invite_debug(
                    event="invite-response",
                    status_code=status_code,
                    phrase=response.status.phrase,
                    description=response.status.description,
                    response_heading=str(
                        response.heading, "utf8", errors="replace"
                    ),
                )
                debug(
                    response.summary(),
                    "INVITE response "
                    + f"call_id={call_id} "
                    + f"status={status_code} {response.status.phrase}",
                )

                # 100 Trying is transaction progress only. Keep waiting so a
                # later 401/407 challenge can still be handled in this method.
                if 100 <= status_code < 180:
                    self._set_last_invite_debug(
                        event="invite-provisional",
                        status="dialing",
                    )
                    continue

                # 18x/199 responses are visible call progress. Return control
                # so VoIPPhone.call() can create the call object; later final
                # responses are then handled by the receive loop.
                if 180 <= status_code < 200:
                    self._set_last_invite_debug(
                        event="invite-provisional",
                        status=(
                            "ringing"
                            if response.status
                            in (SIPStatus.RINGING, SIPStatus.SESSION_PROGRESS)
                            else "dialing"
                        ),
                    )
                    # SIPClient.invite() consumes this response before
                    # VoIPPhone.call() has inserted the call into self.calls.
                    # Queue it so the phone can apply the provisional state
                    # immediately after creating the VoIPCall.
                    self.pending_invite_responses[call_id] = response

                    return SIPMessage(invite.encode("utf8")), call_id, sess_id

                # Digest challenges (401 / 407)
                if response.status in (
                    SIPStatus.UNAUTHORIZED,
                    SIPStatus.PROXY_AUTHENTICATION_REQUIRED,
                ) and retries < self.invite_max_retries:
                    ack = self.gen_ack(response)
                    self.send_raw(ack.encode("utf8"), self.ack_target(response))

                    header_name = "Authorization"
                    if (
                        response.status == SIPStatus.PROXY_AUTHENTICATION_REQUIRED
                        or response.authentication_header == "Proxy-Authenticate"
                    ):
                        header_name = "Proxy-Authorization"

                    digest_uri = self._normalize_request_target(number)
                    auth_line = self._build_digest_auth_header(
                        response,
                        header_name=header_name,
                        method="INVITE",
                        uri=digest_uri,
                    )

                    branch = self._bump_branch(branch)
                    invite = self.gen_invite(
                        number, str(sess_id), ms, sendtype, branch, call_id
                    )
                    invite = invite.replace(
                        "\r\nContent-Length",
                        f"\r\n{auth_line}Content-Length",
                    )
                    self.send_raw(invite.encode("utf8"), self.signal_target())
                    retries += 1
                    self._set_last_invite_debug(
                        event="invite-auth-sent",
                        status="auth",
                        retries=retries,
                    )
                    debug(
                        invite,
                        "INVITE authentication retry "
                        + f"call_id={call_id} retries={retries} "
                        + f"status={status_code} {response.status.phrase}",
                    )

                    # Return now; receive loop handles subsequent messages.
                    return SIPMessage(invite.encode("utf8")), call_id, sess_id

                # Final response (>=200) â€” don't hang waiting for only 100/180.
                self.pending_invite_responses[call_id] = response
                self._set_last_invite_debug(
                    event="invite-final-queued",
                    status=("failed" if status_code >= 300 else "final"),
                    status_code=status_code,
                    phrase=response.status.phrase,
                    description=response.status.description,
                    response_heading=str(
                        response.heading, "utf8", errors="replace"
                    ),
                )

                debug(
                    response.summary(),
                    f"INVITE got final response {status_code} {response.status.phrase} "
                    f"(Call-ID={call_id})",
                )
                return SIPMessage(invite.encode("utf8")), call_id, sess_id

            extra = ""
            if last_response is not None:
                extra = " Last response: " + str(
                    last_response.heading, "utf8", errors="replace"
                )
            self._set_last_invite_debug(
                event="invite-timeout",
                status="failed",
                error=(
                    f"INVITE timed out after {self.invite_timeout}s"
                    + (extra if extra else "")
                ),
            )

            raise TimeoutError(
                f"INVITE timed out after {self.invite_timeout}s (Call-ID={call_id}).{extra}"
            )

    @staticmethod
    def _bump_branch(branch: str) -> str:
        hexindex = -5
        try:
            hexdigit = (int(branch[hexindex], 16) + 1) & 0xF
            return branch[:hexindex] + f"{hexdigit:x}" + branch[hexindex + 1 :]
        except Exception:
            return branch + "1"

    def _auth_user_for_header(self, header_name: Optional[str]) -> str:
        if header_name in ("Proxy-Authenticate", "Proxy-Authorization"):
            return self.auth_username
        return self.username

    @staticmethod
    def _authorization_challenge_header(
        header_name: Optional[str],
    ) -> str:
        if header_name in ("Proxy-Authenticate", "Proxy-Authorization"):
            return "Proxy-Authenticate"
        return "WWW-Authenticate"

    @staticmethod
    def _select_auth_challenge(
        message: SIPMessage,
        challenge_header: str,
    ) -> Dict[str, str]:
        challenges = getattr(message, "authentication_challenges", {})
        if isinstance(challenges, dict):
            auth = challenges.get(challenge_header)
            if isinstance(auth, list):
                for challenge in auth:
                    if (
                        isinstance(challenge, dict)
                        and challenge.get("realm")
                        and challenge.get("nonce")
                        and str(challenge.get("algorithm", "MD5")).upper()
                        == "MD5"
                    ):
                        return challenge
                for challenge in auth:
                    if isinstance(challenge, dict) and challenge:
                        return challenge
            elif isinstance(auth, dict) and auth:
                return auth

        auth = message.headers.get(challenge_header)
        if isinstance(auth, list):
            for challenge in auth:
                if isinstance(challenge, dict) and challenge:
                    return challenge
        if isinstance(auth, dict) and auth:
            return auth

        if getattr(message, "authentication_header", None) == challenge_header:
            return message.authentication

        # Preserve existing fallback behavior for older SIPMessage-like
        # objects and unusual responses with only one parsed auth header.
        return message.authentication

    def _build_digest_auth_header(
        self,
        challenge: SIPMessage,
        *,
        header_name: str,
        method: str,
        uri: str,
    ) -> str:
        challenge_header = self._authorization_challenge_header(header_name)
        auth = self._select_auth_challenge(challenge, challenge_header)
        realm = auth.get("realm")
        nonce = auth.get("nonce")
        if not realm or not nonce:
            raise SIPParseError(f"Digest challenge missing realm/nonce: {auth}")

        algorithm = str(auth.get("algorithm", "MD5")).strip().strip('"') or "MD5"
        if algorithm.upper() != "MD5":
            raise SIPParseError(
                f"Unsupported SIP digest algorithm {algorithm!r}."
            )

        opaque = auth.get("opaque")
        qop_raw = auth.get("qop")

        def md5_hex(s: str) -> str:
            return hashlib.md5(s.encode("utf8")).hexdigest()

        auth_user = self._auth_user_for_header(
            header_name or challenge.authentication_header
        )

        ha1 = md5_hex(f"{auth_user}:{realm}:{self.password}")
        ha2 = md5_hex(f"{method}:{uri}")

        qop_token: Optional[str] = None
        if qop_raw:
            tokens = [t.strip() for t in qop_raw.split(",") if t.strip()]
            lowered = [t.lower() for t in tokens]
            if "auth" not in lowered:
                raise SIPParseError(
                    f"Unsupported SIP digest qop {qop_raw!r}."
                )
            qop_token = "auth"

        if qop_token:
            nc = "00000001"
            cnonce = uuid.uuid4().hex
            response = md5_hex(f"{ha1}:{nonce}:{nc}:{cnonce}:{qop_token}:{ha2}")
        else:
            nc = None
            cnonce = None
            response = md5_hex(f"{ha1}:{nonce}:{ha2}")

        header = (
            f'{header_name}: Digest username="{auth_user}",'
            f'realm="{realm}",nonce="{nonce}",uri="{uri}",response="{response}"'
        )
        if opaque:
            header += f',opaque="{opaque}"'
        if algorithm:
            header += f",algorithm={algorithm}"
        if qop_token and nc and cnonce:
            header += f',qop={qop_token},nc={nc},cnonce="{cnonce}"'
        header += "\r\n"
        return header

    def bye(self, request: SIPMessage) -> None:
        message = self.gen_bye(request)
        self.send_raw(message.encode("utf8"), self.dialog_target(request))

    def deregister(self) -> bool:
        attempts = 0
        max_attempts = max(1, pyVoIP.REGISTER_FAILURE_THRESHOLD)

        while attempts < max_attempts:
            try:
                with self.recvLock:
                    deregistered = self.__deregister()
                if not deregistered:
                    debug("DEREGISTERATION FAILED")
                    return False
                else:
                    self.phone._status = PhoneStatus.INACTIVE

                return deregistered
            except Exception as e:
                debug(f"DEREGISTERATION ERROR: {e}")
                if isinstance(e, RetryRequiredError):
                    attempts += 1
                    if attempts < max_attempts:
                        time.sleep(5)
                        continue
                if type(e) is OSError:
                    raise
                return False

        return False

    def __deregister(self) -> bool:
        self.phone._status = PhoneStatus.DEREGISTERING
        firstRequest = self.gen_first_response(deregister=True)
        response = self._send_register_request(
            firstRequest, action="Deregistering"
        )

        if response.status == SIPStatus.BAD_REQUEST:
            self._handle_bad_request("DEREGISTER", response)

        if response.status in (
            SIPStatus.UNAUTHORIZED,
            SIPStatus.PROXY_AUTHENTICATION_REQUIRED,
        ):
            # 401 Unauthorized or 407 Proxy Authentication Required.
            regRequest = self.gen_register(response, deregister=True)
            response = self._send_register_request(
                regRequest, action="Deregistering"
            )
            if response.status in (
                SIPStatus.UNAUTHORIZED,
                SIPStatus.PROXY_AUTHENTICATION_REQUIRED,
            ):
                # At this point, it's reasonable to assume that
                # this is caused by invalid credentials.
                debug("Unauthorized")
                raise InvalidAccountInfoError(
                    "Invalid authentication credentials for SIP server "
                    + f"{self.server}:{self.myPort}"
                )
            elif response.status == SIPStatus.BAD_REQUEST:
                self._handle_bad_request("DEREGISTER", response)

        if response.status == SIPStatus(500):
            # We raise so the calling function can sleep and try again
            raise RetryRequiredError("Response SIP status of 500")

        if response.status == SIPStatus.OK:
            return True
        return False

    def register(self) -> bool:
        try:
            with self.recvLock:
                registered = self.__register()
            if not registered:
                debug("REGISTERATION FAILED")
                self.registerFailures += 1
            else:
                self.phone._status = PhoneStatus.REGISTERED
                self.registerFailures = 0

            if self.registerFailures >= pyVoIP.REGISTER_FAILURE_THRESHOLD:
                debug("Too many registration failures, stopping.")
                self.stop()
                if self.fatalCallback is not None:
                    self.fatalCallback()
                return False
            self.__start_register_timer()

            return registered
        except Exception as e:
            debug(f"REGISTERATION ERROR: {e}")
            self.registerFailures += 1
            if self.registerFailures >= pyVoIP.REGISTER_FAILURE_THRESHOLD:
                self.stop()
                if self.fatalCallback is not None:
                    self.fatalCallback()
                return False
            if isinstance(e, RetryRequiredError):
                time.sleep(5)
                return self.register()
            self.__start_register_timer(delay=0)
            return False

    def __start_register_timer(self, delay: Optional[int] = None):
        if delay is None:
            delay = self.default_expires - 5
        if self.NSD:
            debug("New register thread")
            # self.subscribe(response)
            self.registerThread = Timer(delay, self.register)
            self.registerThread.name = (
                "SIP Register CSeq: " + f"{self.registerCounter.x}"
            )
            self.registerThread.daemon = True
            self.registerThread.start()

    def __register(self) -> bool:
        self.phone._status = PhoneStatus.REGISTERING
        firstRequest = self.gen_first_response()
        response = self._send_register_request(
            firstRequest, action="Registering"
        )
        first_response = response

        if response.status == SIPStatus.BAD_REQUEST:
            self._handle_bad_request("REGISTER", response)

        if response.status in (
            SIPStatus.UNAUTHORIZED,
            SIPStatus.PROXY_AUTHENTICATION_REQUIRED,
        ):
            # 401 Unauthorized or 407 Proxy Authentication Required.
            regRequest = self.gen_register(response)
            response = self._send_register_request(
                regRequest, action="Registering"
            )
            if response.status in (
                SIPStatus.UNAUTHORIZED,
                SIPStatus.PROXY_AUTHENTICATION_REQUIRED,
            ):
                # At this point, it's reasonable to assume that
                # this is caused by invalid credentials.
                debug("=" * 50)
                debug("Unauthorized, SIP Message Log:\n")
                debug("SENT")
                debug(firstRequest)
                debug("\nRECEIVED")
                debug(first_response.summary())
                debug("\nSENT (DO NOT SHARE THIS PACKET)")
                debug(regRequest)
                debug("\nRECEIVED")
                debug(response.summary())
                debug("=" * 50)
                raise InvalidAccountInfoError(
                    "Invalid authentication credentials for SIP server "
                    + f"{self.server}:{self.myPort}"
                )
            elif response.status == SIPStatus.BAD_REQUEST:
                self._handle_bad_request("REGISTER", response)

        if response.status not in [
            SIPStatus(400),
            SIPStatus(401),
            SIPStatus(407),
        ]:
            # Unauthorized
            if response.status == SIPStatus(500):
                # We raise so the calling function can sleep and try again
                raise RetryRequiredError("Response SIP status of 500")
            else:
                # TODO: determine if needed here
                self.parse_message(response)

        debug(response.summary())
        debug(response.raw)

        if response.status == SIPStatus.OK:
            return True
        else:
            raise InvalidAccountInfoError(
                "Invalid authentication credentials for SIP server "
                + f"{self.server}:{self.myPort}"
            )

    def _handle_bad_request(
        self,
        action: str,
        response: SIPMessage,
    ) -> None:
        message = f"SIP {action} failed with 400 Bad Request"
        details = []
        call_id = response.headers.get("Call-ID")
        cseq = response.headers.get("CSeq")
        if isinstance(cseq, dict):
            cseq_value = (
                f"{cseq.get('check', '')} {cseq.get('method', '')}"
            ).strip()
        else:
            cseq_value = str(cseq or "")

        if call_id:
            details.append(f"Call-ID={call_id}")
        if cseq_value:
            details.append(f"CSeq={cseq_value}")
        if details:
            message += " (" + ", ".join(details) + ")"

        debug(message)
        raise SIPRequestError(message)

    def subscribe(self, lastresponse: SIPMessage) -> None:
        warnings.warn(
            "subscribe(SIPMessage) is deprecated. "
            + "Use subscribe_to(...) instead.",
            DeprecationWarning,
            stacklevel=2,
        )
        try:
            self.subscribe_to(
                self.username,
                event="message-summary",
                expires=self.default_expires * 2,
                accept=["application/simple-message-summary"],
            )
        except Exception as ex:
            debug(f"Legacy subscribe failed: {ex}")

    def trying_timeout_check(self, response: SIPMessage) -> SIPMessage:
        """
        Some servers need time to process the response.
        When this happens, the first response you get from the server is
        SIPStatus.TRYING. This while loop tries checks every second for an
        updated response. It times out after 30 seconds.
        """
        start_time = time.monotonic()
        while response.status == SIPStatus.TRYING:
            remaining = self.register_timeout - (
                time.monotonic() - start_time
            )
            if remaining <= 0:
                raise TimeoutError(
                    f"Waited {self.register_timeout} seconds but server is "
                    + "still TRYING"
                )

            response = self._recv_message_before(time.monotonic() + remaining)
            if response is None:
                raise TimeoutError(
                    f"Waited {self.register_timeout} seconds but server is "
                    + "still TRYING"
                )

        return response
