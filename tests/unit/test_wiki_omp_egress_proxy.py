"""Contracts for the OpenRouter-only OMP egress proxy."""

from __future__ import annotations

import socket

import pytest

from five08.wiki_editing.omp_egress_proxy import (
    EgressProxyError,
    EgressProxySettings,
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
