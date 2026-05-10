from dataclasses import dataclass, field
from enum import Enum
from threading import Lock
from typing import Callable, Dict, Iterable, Optional, Tuple
import ipaddress
import random
import select
import socket
import ssl
import time

try:  # pragma: no cover - exercised when dnspython is installed.
    import dns.resolver as dns_resolver
except Exception:  # pragma: no cover - keeps source-tree imports usable.
    dns_resolver = None


class SIPTransport(Enum):
    UDP = "UDP"
    TCP = "TCP"
    TLS = "TLS"

    @classmethod
    def from_uri(cls, value: Optional[str], *, scheme: str = "sip") -> "SIPTransport":
        raw = str(value or "").strip().lower()
        scheme = str(scheme or "sip").strip().lower()

        # RFC 3261 treats SIPS as secure.  transport=tcp on a sips: URI is a
        # reliable transport selector; the first hop is still TLS-over-TCP.
        if scheme == "sips":
            if raw == "udp":
                raise ValueError("SIPS URIs cannot use UDP transport.")
            return cls.TLS

        if raw in ("", "udp"):
            return cls.UDP
        if raw == "tcp":
            return cls.TCP
        if raw in ("tls", "tls-over-tcp"):
            return cls.TLS
        raise ValueError(f"Unsupported SIP transport {value!r}.")

    @property
    def via_token(self) -> str:
        return self.value

    @property
    def uri_token(self) -> str:
        return self.value


@dataclass(frozen=True)
class SIPURIInfo:
    scheme: str
    host: str
    port: Optional[int] = None
    user: Optional[str] = None
    params: Dict[str, str] = field(default_factory=dict)
    has_scheme: bool = False
    explicit_port: bool = False


@dataclass(frozen=True)
class ResolvedSIPTarget:
    host: str
    port: int
    transport: SIPTransport
    scheme: str = "sip"
    source: str = "uri"
    service: Optional[str] = None
    uri_host: Optional[str] = None
    explicit_port: bool = False
    explicit_transport: bool = False


def _strip_quotes(value: object) -> str:
    if isinstance(value, bytes):
        text = value.decode("ascii", errors="ignore")
    elif hasattr(value, "to_text"):
        text = value.to_text()
    else:
        text = str(value)
    return text.strip().strip('"')


def split_hostport(value: str, default_port: Optional[int] = None) -> Tuple[str, Optional[int], bool]:
    target = str(value or "").strip()
    if target.startswith("["):
        host, separator, rest = target[1:].partition("]")
        if separator and rest.startswith(":"):
            try:
                return host, int(rest[1:]), True
            except ValueError:
                return host, default_port, False
        return host, default_port, False

    # Unbracketed IPv6 literals contain multiple colons and no URI port.
    if target.count(":") == 1:
        host, raw_port = target.rsplit(":", 1)
        try:
            return host, int(raw_port), True
        except ValueError:
            pass

    return target, default_port, False


def format_hostport(
    host: str,
    port: Optional[int] = None,
    *,
    always_include_port: bool = False,
) -> str:
    formatted_host = str(host).strip()
    if ":" in formatted_host and not (
        formatted_host.startswith("[") and formatted_host.endswith("]")
    ):
        formatted_host = f"[{formatted_host}]"

    if port is not None and (always_include_port or int(port) != 5060):
        return f"{formatted_host}:{int(port)}"
    return formatted_host


class SIPResolver:
    _NAPTR_SERVICE_TRANSPORT = {
        "SIP+D2U": SIPTransport.UDP,
        "SIP+D2T": SIPTransport.TCP,
        "SIPS+D2T": SIPTransport.TLS,
    }

    def __init__(
        self,
        *,
        transport_preference: Optional[Iterable[SIPTransport]] = None,
    ):
        self.transport_preference = list(
            transport_preference
            if transport_preference is not None
            else (SIPTransport.TLS, SIPTransport.TCP, SIPTransport.UDP)
        )

    @staticmethod
    def is_sip_uri(value: str) -> bool:
        lower = str(value or "").strip().lower()
        return lower.startswith("sip:") or lower.startswith("sips:")

    @staticmethod
    def default_port(transport: SIPTransport) -> int:
        return 5061 if transport == SIPTransport.TLS else 5060

    @staticmethod
    def is_numeric_host(host: str) -> bool:
        try:
            ipaddress.ip_address(str(host).strip("[]"))
            return True
        except ValueError:
            return False

    @classmethod
    def parse_uri(cls, value: str) -> SIPURIInfo:
        raw = str(value or "").strip()
        if "<" in raw and ">" in raw:
            raw = raw.split("<", 1)[1].split(">", 1)[0].strip()

        lower = raw.lower()
        scheme = "sip"
        has_scheme = False
        if lower.startswith("sips:"):
            scheme = "sips"
            raw = raw[5:]
            has_scheme = True
        elif lower.startswith("sip:"):
            scheme = "sip"
            raw = raw[4:]
            has_scheme = True

        raw = raw.split("?", 1)[0]
        host_part, *param_parts = raw.split(";")
        params: Dict[str, str] = {}
        for param in param_parts:
            if not param:
                continue
            if "=" in param:
                key, val = param.split("=", 1)
            else:
                key, val = param, ""
            params[key.strip().lower()] = val.strip()

        user = None
        if "@" in host_part:
            user, host_part = host_part.rsplit("@", 1)
            user = user or None

        # RFC 3261: maddr overrides the host for routing purposes.
        host_part = params.get("maddr") or host_part
        host, port, explicit_port = split_hostport(host_part)
        if not host:
            raise ValueError(f"SIP URI has no host: {value!r}")

        return SIPURIInfo(
            scheme=scheme,
            host=host,
            port=port,
            user=user,
            params=params,
            has_scheme=has_scheme,
            explicit_port=explicit_port,
        )

    def resolve(
        self,
        value: str,
        *,
        default_port: Optional[int] = 5060,
        default_transport: Optional[SIPTransport] = None,
    ) -> ResolvedSIPTarget:
        uri = self.parse_uri(value)

        # The legacy pyVoIP API passes server/proxy as a host and port as a
        # separate argument.  Treat that separate port as explicit only when
        # the caller did not provide a SIP URI.  SIP URIs without a port are
        # allowed to run the RFC 3263 DNS procedures.
        argument_port_is_explicit = (
            not uri.has_scheme and not uri.explicit_port and default_port is not None
        )
        explicit_port = uri.explicit_port or argument_port_is_explicit
        port = uri.port if uri.explicit_port else (
            int(default_port) if argument_port_is_explicit and default_port is not None else None
        )

        explicit_transport = "transport" in uri.params
        if explicit_transport:
            transport = SIPTransport.from_uri(uri.params.get("transport"), scheme=uri.scheme)
            srv = self._srv_if_usable(uri, transport, explicit_port)
            if srv is not None:
                return srv
        elif uri.scheme == "sips":
            transport = SIPTransport.TLS
            srv = self._srv_if_usable(uri, transport, explicit_port)
            if srv is not None:
                return srv
        elif default_transport is not None:
            transport = default_transport
            srv = self._srv_if_usable(uri, transport, explicit_port)
            if srv is not None:
                return srv
        elif self.is_numeric_host(uri.host) or explicit_port:
            transport = SIPTransport.UDP
        else:
            resolved = self._resolve_dns(uri)
            if resolved is not None:
                return resolved
            transport = SIPTransport.UDP

        return ResolvedSIPTarget(
            host=uri.host,
            port=port if port is not None else self.default_port(transport),
            transport=transport,
            scheme=uri.scheme,
            source="uri",
            uri_host=uri.host,
            explicit_port=explicit_port,
            explicit_transport=explicit_transport,
        )

    def _srv_if_usable(
        self,
        uri: SIPURIInfo,
        transport: SIPTransport,
        explicit_port: bool,
    ) -> Optional[ResolvedSIPTarget]:
        if explicit_port or self.is_numeric_host(uri.host):
            return None
        return self._resolve_srv_for_transport(
            uri.host,
            transport,
            uri,
            source="srv-explicit-transport",
        )

    def _resolve_dns(self, uri: SIPURIInfo) -> Optional[ResolvedSIPTarget]:
        if dns_resolver is None:
            return None

        naptr = self._resolve_naptr(uri)
        if naptr is not None:
            return naptr

        transports = (
            [SIPTransport.TLS]
            if uri.scheme == "sips"
            else list(self.transport_preference)
        )
        for transport in transports:
            srv = self._resolve_srv_for_transport(
                uri.host, transport, uri, source="srv-fallback"
            )
            if srv is not None:
                return srv
        return None

    def _resolve_naptr(self, uri: SIPURIInfo) -> Optional[ResolvedSIPTarget]:
        try:
            answers = dns_resolver.resolve(uri.host, "NAPTR")
        except Exception:
            return None

        records = []
        for record in answers:
            service = _strip_quotes(getattr(record, "service", "")).upper()
            flags = _strip_quotes(getattr(record, "flags", "")).lower()
            replacement = _strip_quotes(getattr(record, "replacement", "")).rstrip(".")
            transport = self._NAPTR_SERVICE_TRANSPORT.get(service)
            if transport is None or flags != "s" or not replacement:
                continue
            if uri.scheme == "sips":
                if not service.startswith("SIPS+"):
                    continue
            elif service.startswith("SIPS+"):
                continue
            elif transport not in self.transport_preference:
                continue
            records.append(
                (
                    int(getattr(record, "order", 0)),
                    int(getattr(record, "preference", 0)),
                    service,
                    replacement,
                    transport,
                )
            )

        records.sort(key=lambda item: (item[0], item[1]))
        for _order, _preference, service, replacement, transport in records:
            srv = self._resolve_srv_record(
                replacement,
                transport,
                uri,
                source="naptr",
                service=service,
            )
            if srv is not None:
                return srv
        return None

    def _srv_name(self, host: str, transport: SIPTransport) -> str:
        if transport == SIPTransport.TLS:
            return f"_sips._tcp.{host}"
        if transport == SIPTransport.TCP:
            return f"_sip._tcp.{host}"
        return f"_sip._udp.{host}"

    def _resolve_srv_for_transport(
        self,
        host: str,
        transport: SIPTransport,
        uri: SIPURIInfo,
        *,
        source: str,
    ) -> Optional[ResolvedSIPTarget]:
        return self._resolve_srv_record(
            self._srv_name(host, transport), transport, uri, source=source
        )

    def _resolve_srv_record(
        self,
        srv_name: str,
        transport: SIPTransport,
        uri: SIPURIInfo,
        *,
        source: str,
        service: Optional[str] = None,
    ) -> Optional[ResolvedSIPTarget]:
        if dns_resolver is None:
            return None
        try:
            answers = list(dns_resolver.resolve(srv_name, "SRV"))
        except Exception:
            return None
        if not answers:
            return None

        record = self._pick_srv_record(answers)
        target = _strip_quotes(getattr(record, "target", "")).rstrip(".")
        if not target:
            return None
        return ResolvedSIPTarget(
            host=target,
            port=int(getattr(record, "port")),
            transport=transport,
            scheme=uri.scheme,
            source=source,
            service=service or srv_name,
            uri_host=uri.host,
            explicit_port=False,
            explicit_transport="transport" in uri.params,
        )

    @staticmethod
    def _pick_srv_record(records):
        records = sorted(records, key=lambda item: int(getattr(item, "priority", 0)))
        priority = int(getattr(records[0], "priority", 0))
        candidates = [r for r in records if int(getattr(r, "priority", 0)) == priority]
        total_weight = sum(max(0, int(getattr(r, "weight", 0))) for r in candidates)
        if total_weight <= 0:
            return random.choice(candidates)

        ticket = random.randint(1, total_weight)
        running = 0
        for record in candidates:
            running += max(0, int(getattr(record, "weight", 0)))
            if ticket <= running:
                return record
        return candidates[-1]


class SIPConnection:
    def __init__(
        self,
        local_host: str,
        local_port: int,
        target: ResolvedSIPTarget,
        *,
        tls_context: Optional[ssl.SSLContext] = None,
        tls_server_name: Optional[str] = None,
        send_timeout: float = 30.0,
    ):
        self.local_host = local_host
        self.local_port = int(local_port)
        self.target = target
        self.tls_context = tls_context
        self.tls_server_name = tls_server_name
        self.send_timeout = send_timeout
        self.socket: Optional[socket.socket] = None
        self._stream_buffer = b""
        self._send_lock = Lock()

    @property
    def connection_oriented(self) -> bool:
        return self.target.transport in (SIPTransport.TCP, SIPTransport.TLS)

    def open(self) -> None:
        if self.target.transport == SIPTransport.UDP:
            self.socket = self._open_udp_socket()
        else:
            self.socket = self._open_stream_socket()
        self.socket.setblocking(True)

    def close(self) -> None:
        if self.socket is None:
            return
        try:
            self.socket.close()
        finally:
            self.socket = None

    @staticmethod
    def _ip_version(address: str) -> Optional[int]:
        try:
            return ipaddress.ip_address(str(address).strip("[]")).version
        except ValueError:
            return None

    @classmethod
    def _family_for_addresses(cls, *addresses: str):
        versions = [cls._ip_version(address) for address in addresses]
        versions = [version for version in versions if version is not None]
        if len(set(versions)) > 1:
            raise OSError(f"SIP addresses use mixed IP versions: {addresses!r}")
        return socket.AF_INET6 if versions and versions[0] == 6 else socket.AF_INET

    @staticmethod
    def _socket_address(host: str, port: int, family):
        if family == socket.AF_INET6:
            return (host, int(port), 0, 0)
        return (host, int(port))

    @staticmethod
    def _wildcard_for_family(family):
        return "::" if family == socket.AF_INET6 else "0.0.0.0"

    def _local_bind_host(self, family) -> str:
        if self.local_host in ("", "0.0.0.0", "::"):
            return self._wildcard_for_family(family)
        return self.local_host

    def _open_udp_socket(self) -> socket.socket:
        family = self._family_for_addresses(self.local_host, self.target.host)
        sock = socket.socket(family, socket.SOCK_DGRAM)
        sock.bind(
            self._socket_address(
                self._local_bind_host(family), self.local_port, family
            )
        )
        return sock

    def _open_stream_socket(self) -> socket.socket:
        last_error: Optional[BaseException] = None
        infos = socket.getaddrinfo(
            self.target.host,
            self.target.port,
            0,
            socket.SOCK_STREAM,
        )
        for family, socktype, proto, _canon, sockaddr in infos:
            sock = socket.socket(family, socktype, proto)
            try:
                sock.settimeout(self.send_timeout)
                sock.bind(
                    self._socket_address(
                        self._local_bind_host(family), self.local_port, family
                    )
                )
                sock.connect(sockaddr)
                if self.target.transport == SIPTransport.TLS:
                    context = self.tls_context or ssl.create_default_context()
                    server_name = self.tls_server_name or self.target.host
                    sock = context.wrap_socket(sock, server_hostname=server_name)
                return sock
            except BaseException as ex:
                last_error = ex
                try:
                    sock.close()
                except Exception:
                    pass
        if last_error is not None:
            raise last_error
        raise OSError(f"Unable to resolve SIP target {self.target.host!r}")

    def send(self, data: bytes, target: Optional[Tuple[str, int]] = None) -> None:
        if self.socket is None:
            raise RuntimeError("SIP connection is not open.")
        with self._send_lock:
            if self.target.transport == SIPTransport.UDP:
                self.socket.sendto(data, target or (self.target.host, self.target.port))
            else:
                self._send_stream(data)

    def _send_stream(self, data: bytes) -> None:
        assert self.socket is not None
        view = memoryview(data)
        deadline = time.monotonic() + self.send_timeout
        while view:
            try:
                sent = self.socket.send(view)
            except (BlockingIOError, ssl.SSLWantWriteError):
                remaining = max(0.0, deadline - time.monotonic())
                if remaining == 0:
                    raise TimeoutError("Timed out writing SIP stream data")
                select.select([], [self.socket], [], remaining)
                continue
            except ssl.SSLWantReadError:
                remaining = max(0.0, deadline - time.monotonic())
                if remaining == 0:
                    raise TimeoutError("Timed out during SIP TLS write")
                select.select([self.socket], [], [], remaining)
                continue

            if sent == 0:
                raise OSError("SIP stream socket closed while sending")
            view = view[sent:]

    def recv_raw_message(self) -> bytes:
        if self.socket is None:
            raise RuntimeError("SIP connection is not open.")
        if self.target.transport == SIPTransport.UDP:
            return self.socket.recv(8192)
        return self._recv_stream_message()

    def recv_raw_message_before(
        self,
        deadline: float,
        *,
        running: Optional[Callable[[], bool]] = None,
    ) -> Optional[bytes]:
        if self.socket is None:
            raise RuntimeError("SIP connection is not open.")

        old_timeout = self.socket.gettimeout()
        self.socket.setblocking(False)
        try:
            while time.monotonic() < deadline and (running is None or running()):
                if self.connection_oriented and self._stream_has_message():
                    return self.recv_raw_message()

                remaining = max(0.0, deadline - time.monotonic())
                if remaining == 0:
                    return None
                ready = select.select([self.socket], [], [], remaining)
                if not ready[0]:
                    return None
                try:
                    return self.recv_raw_message()
                except (BlockingIOError, ssl.SSLWantReadError, ssl.SSLWantWriteError):
                    continue
        finally:
            self.socket.settimeout(old_timeout)
        return None

    def _stream_has_message(self) -> bool:
        needed = self._stream_message_length()
        return needed is not None and len(self._stream_buffer) >= needed

    def _stream_message_length(self) -> Optional[int]:
        header_end = self._stream_buffer.find(b"\r\n\r\n")
        if header_end < 0:
            return None
        headers = self._stream_buffer[:header_end].decode(
            "utf8", errors="replace"
        ).split("\r\n")
        content_length = 0
        for line in headers[1:]:
            lower = line.lower()
            if lower.startswith("content-length:") or lower.startswith("l:"):
                try:
                    content_length = int(line.split(":", 1)[1].strip())
                except ValueError:
                    content_length = 0
                break
        return header_end + 4 + content_length

    def _recv_stream_message(self) -> bytes:
        assert self.socket is not None
        message = self._pop_stream_message()
        if message is not None:
            return message

        while True:
            chunk = self.socket.recv(8192)
            if not chunk:
                raise OSError("SIP stream socket closed")
            self._stream_buffer += chunk
            message = self._pop_stream_message()
            if message is not None:
                return message

    def _pop_stream_message(self) -> Optional[bytes]:
        needed = self._stream_message_length()
        if needed is None or len(self._stream_buffer) < needed:
            return None
        message = self._stream_buffer[:needed]
        self._stream_buffer = self._stream_buffer[needed:]
        return message
