"""Contracts for the OpenRouter-only OMP egress proxy."""

from __future__ import annotations

import socket
import threading

import pytest

from five08.wiki_editing.omp_egress_proxy import (
    EgressProxyError,
    EgressProxySettings,
    _relay_tunnel,
    parse_connect_target,
    resolve_public_addresses,
)


def test_connect_proxy_accepts_only_openrouter_https() -> None:
    assert parse_connect_target("openrouter.ai:443") == ("openrouter.ai", 443)

    for target in (
        "api.openrouter.ai:443",
        "openrouter.ai:80",
        "127.0.0.1:443",
        "openrouter.ai:443/path",
        "openrouter.ai",
    ):
        with pytest.raises(EgressProxyError):
            parse_connect_target(target)


def test_connect_proxy_drops_private_or_link_local_dns_answers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda *_args, **_kwargs: [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", 443)),
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("169.254.169.254", 443)),
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("10.0.0.5", 443)),
        ],
    )

    with pytest.raises(EgressProxyError, match="public address space"):
        resolve_public_addresses("openrouter.ai", 443)


def test_connect_proxy_keeps_only_global_dns_answers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda *_args, **_kwargs: [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("10.0.0.5", 443)),
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("104.18.3.1", 443)),
        ],
    )

    addresses = resolve_public_addresses("openrouter.ai", 443)

    assert addresses == ((socket.AF_INET, socket.SOCK_STREAM, 6, ("104.18.3.1", 443)),)


def test_proxy_settings_reject_an_external_listener() -> None:
    with pytest.raises(RuntimeError, match="LISTEN_ADDR"):
        EgressProxySettings.from_environment(
            {"WIKI_OMP_EGRESS_PROXY_LISTEN_ADDR": "proxy.example:3128"}
        )


def test_proxy_tunnel_outlives_the_configured_sandbox_run() -> None:
    settings = EgressProxySettings.from_environment(
        {"WIKI_OMP_SANDBOX_RUN_TIMEOUT_SECONDS": "600"}
    )

    assert settings.tunnel_timeout_seconds == 660.0


def test_tunnel_drains_buffered_bytes_before_propagating_half_closes() -> None:
    client, downstream = socket.socketpair()
    upstream, provider = socket.socketpair()
    for tunnel_socket in (downstream, upstream):
        tunnel_socket.setblocking(False)
    for peer_socket in (client, provider):
        peer_socket.settimeout(2)

    relay = threading.Thread(
        target=_relay_tunnel,
        args=(downstream, upstream),
    )
    relay.start()
    try:
        client.sendall(b"request")
        client.shutdown(socket.SHUT_WR)

        assert provider.recv(1024) == b"request"
        assert provider.recv(1024) == b""

        provider.sendall(b"response")
        provider.shutdown(socket.SHUT_WR)

        assert client.recv(1024) == b"response"
        assert client.recv(1024) == b""
        relay.join(timeout=2)
        assert not relay.is_alive()
    finally:
        for tunnel_socket in (client, downstream, upstream, provider):
            tunnel_socket.close()
