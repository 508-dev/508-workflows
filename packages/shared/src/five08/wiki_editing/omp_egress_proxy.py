"""A narrow OpenRouter-only CONNECT proxy for the isolated OMP sidecar.

The authoring sandbox lives only on Docker-internal networks. Its OMP child
must use this proxy to reach OpenRouter, while this proxy is the only service
that has an external Docker route. The proxy accepts HTTPS CONNECT requests
for the exact approved host and rejects all other HTTP traffic, private
addresses, non-443 ports, and DNS answers outside public address space.
"""

from __future__ import annotations

import ipaddress
import logging
import os
import select
import socket
import threading
import time
from dataclasses import dataclass
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import cast


logger = logging.getLogger(__name__)

_ALLOWED_HOSTS = frozenset({"openrouter.ai"})
_CONNECT_PORT = 443
_MAX_CONCURRENCY = 4
_CONNECT_TIMEOUT_SECONDS = 10.0
_MAX_TUNNEL_SECONDS = 330.0
_MAX_BUFFER_BYTES = 1_000_000


class EgressProxyError(ValueError):
    """A requested proxy destination is outside the fixed policy."""


@dataclass(frozen=True, slots=True)
class EgressProxySettings:
    """Non-secret settings for the sidecar's OpenRouter egress proxy."""

    listen_host: str = "0.0.0.0"
    listen_port: int = 3128
    max_concurrency: int = 1

    @classmethod
    def from_environment(
        cls,
        environ: dict[str, str] | None = None,
    ) -> "EgressProxySettings":
        values = os.environ if environ is None else environ
        host, port = _parse_listen_address(
            values.get("WIKI_OMP_EGRESS_PROXY_LISTEN_ADDR", "0.0.0.0:3128")
        )
        max_concurrency = _parse_bounded_int(
            values.get("WIKI_OMP_EGRESS_PROXY_MAX_CONCURRENCY", "1"),
            name="WIKI_OMP_EGRESS_PROXY_MAX_CONCURRENCY",
            minimum=1,
            maximum=_MAX_CONCURRENCY,
        )
        return cls(
            listen_host=host,
            listen_port=port,
            max_concurrency=max_concurrency,
        )


def _parse_listen_address(value: object) -> tuple[str, int]:
    """Accept only a local TCP listener address suitable for this container."""
    candidate = str(value).strip()
    host, separator, port_text = candidate.rpartition(":")
    if not separator or host not in {"0.0.0.0", "127.0.0.1"}:
        raise RuntimeError("WIKI_OMP_EGRESS_PROXY_LISTEN_ADDR is invalid")
    try:
        port = int(port_text)
    except ValueError as exc:
        raise RuntimeError("WIKI_OMP_EGRESS_PROXY_LISTEN_ADDR is invalid") from exc
    if not 1 <= port <= 65535:
        raise RuntimeError("WIKI_OMP_EGRESS_PROXY_LISTEN_ADDR is invalid")
    return host, port


def _parse_bounded_int(
    value: object,
    *,
    name: str,
    minimum: int,
    maximum: int,
) -> int:
    try:
        parsed = int(str(value))
    except (TypeError, ValueError) as exc:
        raise RuntimeError(f"{name} must be an integer") from exc
    if not minimum <= parsed <= maximum:
        raise RuntimeError(f"{name} is outside its safe range")
    return parsed


def parse_connect_target(value: str) -> tuple[str, int]:
    """Parse an HTTP CONNECT authority and enforce the fixed provider host."""
    target = value.strip()
    host, separator, port_text = target.rpartition(":")
    if not separator or not host or ":" in host or not port_text.isdecimal():
        raise EgressProxyError("invalid CONNECT target")
    normalized_host = host.casefold().rstrip(".")
    if normalized_host not in _ALLOWED_HOSTS:
        raise EgressProxyError("CONNECT host is not allowed")
    if int(port_text) != _CONNECT_PORT:
        raise EgressProxyError("CONNECT port is not allowed")
    return normalized_host, _CONNECT_PORT


def resolve_public_addresses(host: str, port: int) -> tuple[tuple[object, ...], ...]:
    """Resolve the fixed host and drop loopback/private/rebinding candidates."""
    try:
        addresses = socket.getaddrinfo(
            host,
            port,
            type=socket.SOCK_STREAM,
        )
    except OSError as exc:
        raise EgressProxyError("provider host could not be resolved") from exc

    approved: list[tuple[object, ...]] = []
    for family, socket_type, protocol, _canonical_name, address in addresses:
        if not isinstance(address, tuple) or not address:
            continue
        try:
            parsed = ipaddress.ip_address(str(address[0]))
        except ValueError:
            continue
        if not parsed.is_global:
            continue
        approved.append((family, socket_type, protocol, address))
    if not approved:
        raise EgressProxyError("provider host resolved outside public address space")
    return tuple(approved)


def connect_openrouter(host: str, port: int) -> socket.socket:
    """Open a bounded TCP connection to one DNS-validated provider address."""
    last_error: OSError | None = None
    for family, socket_type, protocol, address in resolve_public_addresses(host, port):
        connection = socket.socket(
            cast(socket.AddressFamily, family),
            cast(socket.SocketKind, socket_type),
            cast(int, protocol),
        )
        try:
            connection.settimeout(_CONNECT_TIMEOUT_SECONDS)
            connection.connect(cast(tuple[str, int], address))
            connection.setblocking(False)
            return connection
        except OSError as exc:
            last_error = exc
            connection.close()
    raise EgressProxyError("provider connection failed") from last_error


class OpenRouterEgressProxy(ThreadingHTTPServer):
    """Threaded HTTP CONNECT server with a fixed bounded tunnel budget."""

    daemon_threads = True
    allow_reuse_address = True

    def __init__(
        self,
        server_address: tuple[str, int],
        *,
        max_concurrency: int,
    ) -> None:
        self.tunnel_slots = threading.BoundedSemaphore(max_concurrency)
        super().__init__(server_address, OpenRouterEgressProxyHandler)


class OpenRouterEgressProxyHandler(BaseHTTPRequestHandler):
    """Reject every request except an OpenRouter HTTPS CONNECT tunnel."""

    protocol_version = "HTTP/1.1"

    def do_CONNECT(self) -> None:  # noqa: N802 - HTTP method hook
        server = cast(OpenRouterEgressProxy, self.server)
        if not server.tunnel_slots.acquire(blocking=False):
            self.send_error(HTTPStatus.SERVICE_UNAVAILABLE, "proxy is busy")
            return

        upstream: socket.socket | None = None
        try:
            host, port = parse_connect_target(self.path)
            upstream = connect_openrouter(host, port)
            self.send_response(HTTPStatus.OK, "Connection Established")
            self.send_header("Connection", "close")
            self.end_headers()
            self.wfile.flush()
            self.connection.setblocking(False)
            self._relay(upstream)
        except EgressProxyError as exc:
            logger.warning("Rejected OMP egress request: %s", exc)
            self.send_error(HTTPStatus.FORBIDDEN, "provider destination is not allowed")
        except OSError:
            logger.warning("OMP provider egress connection failed", exc_info=True)
            self.send_error(HTTPStatus.BAD_GATEWAY, "provider connection failed")
        finally:
            if upstream is not None:
                upstream.close()
            server.tunnel_slots.release()
            self.close_connection = True

    def _relay(self, upstream: socket.socket) -> None:
        """Bidirectionally relay bytes without buffering an unbounded stream."""
        _relay_tunnel(self.connection, upstream)

    def do_GET(self) -> None:  # noqa: N802 - HTTP method hook
        self.send_error(HTTPStatus.METHOD_NOT_ALLOWED)

    do_HEAD = do_GET
    do_POST = do_GET
    do_PUT = do_GET
    do_DELETE = do_GET
    do_PATCH = do_GET
    do_OPTIONS = do_GET

    def log_message(self, format: str, *args: object) -> None:
        """Keep requests free of headers and credentials in application logs."""
        logger.info("OMP egress proxy: " + format, *args)


def _relay_tunnel(downstream: socket.socket, upstream: socket.socket) -> None:
    """Relay a tunnel while preserving buffered data across TCP half-closes."""
    peers = {downstream: upstream, upstream: downstream}
    pending = {downstream: bytearray(), upstream: bytearray()}
    read_open = {downstream: True, upstream: True}
    write_open = {downstream: True, upstream: True}
    deadline = time.monotonic() + _MAX_TUNNEL_SECONDS

    def shutdown_drained_destinations() -> bool:
        """Propagate EOF after the matching direction's buffered data is sent."""
        for source, destination in peers.items():
            if read_open[source] or pending[destination] or not write_open[destination]:
                continue
            try:
                destination.shutdown(socket.SHUT_WR)
            except OSError:
                return False
            write_open[destination] = False
        return True

    while True:
        if not shutdown_drained_destinations():
            return
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return
        ready_for_read = [
            source
            for source, destination in peers.items()
            if read_open[source]
            and write_open[destination]
            and len(pending[destination]) < _MAX_BUFFER_BYTES
        ]
        ready_for_write = [
            destination
            for destination, data in pending.items()
            if data and write_open[destination]
        ]
        if not ready_for_read and not ready_for_write:
            return
        try:
            ready_read, ready_write, _ = select.select(
                ready_for_read,
                ready_for_write,
                [],
                min(1.0, remaining),
            )
        except OSError:
            return
        for source in ready_read:
            destination = peers[source]
            try:
                data = source.recv(
                    min(64 * 1024, _MAX_BUFFER_BYTES - len(pending[destination]))
                )
            except (BlockingIOError, InterruptedError):
                continue
            except OSError:
                return
            if not data:
                read_open[source] = False
                continue
            pending[destination].extend(data)
        for destination in ready_write:
            data = pending[destination]
            try:
                sent = destination.send(data)
            except (BlockingIOError, InterruptedError):
                continue
            except OSError:
                return
            if sent <= 0:
                return
            del data[:sent]


def serve(settings: EgressProxySettings) -> None:
    """Run the fixed provider proxy until the container stops."""
    server = OpenRouterEgressProxy(
        (settings.listen_host, settings.listen_port),
        max_concurrency=settings.max_concurrency,
    )
    logger.info(
        "Starting OpenRouter-only OMP egress proxy on %s:%s",
        settings.listen_host,
        settings.listen_port,
    )
    try:
        server.serve_forever()
    finally:
        server.server_close()


def main() -> None:
    logging.basicConfig(
        level=os.getenv("LOG_LEVEL", "INFO").upper(),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    serve(EgressProxySettings.from_environment())


if __name__ == "__main__":  # pragma: no cover - container entrypoint
    main()
