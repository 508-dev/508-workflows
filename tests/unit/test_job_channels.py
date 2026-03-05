"""Tests for job-post channel persistence helpers."""

from __future__ import annotations

from contextlib import contextmanager

import five08.job_channels as job_channels


class _CursorStub:
    def __init__(self, *, row: dict | None = None, rows: list[dict] | None = None):
        self._row = row
        self._rows = rows or []
        self.executed = []

    def execute(self, query: str, params: tuple) -> None:
        self.executed.append((query, params))

    def fetchone(self) -> dict | None:
        return self._row

    def fetchall(self) -> list[dict]:
        return list(self._rows)


class _ConnectionStub:
    def __init__(self, cursor: _CursorStub):
        self._cursor = cursor

    def cursor(self, row_factory=None) -> _CursorStub:  # noqa: ARG002
        return self._cursor

    def __enter__(self) -> "_ConnectionStub":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:  # noqa: ANN001
        return None


def _install_connection_stub(monkeypatch, cursor: _CursorStub) -> None:
    @contextmanager
    def _conn():
        yield _ConnectionStub(cursor)

    monkeypatch.setattr(job_channels, "get_postgres_connection", lambda _: _conn())


def test_list_registered_job_post_channels(monkeypatch) -> None:
    cursor = _CursorStub(rows=[{"channel_id": "123"}, {"channel_id": "456"}])
    _install_connection_stub(monkeypatch, cursor)

    result = job_channels.list_registered_job_post_channels(
        job_channels.SharedSettings(), guild_id="guild-1"
    )

    assert result == ["123", "456"]
    assert cursor.executed
    assert cursor.executed[0][1] == ("guild-1",)


def test_register_job_post_channel_returns_true_on_insert(monkeypatch) -> None:
    cursor = _CursorStub(row={"channel_id": "123"})
    _install_connection_stub(monkeypatch, cursor)

    created = job_channels.register_job_post_channel(
        job_channels.SharedSettings(), guild_id="guild-1", channel_id="123"
    )

    assert created is True
    assert cursor.executed[0][1] == ("guild-1", "123")


def test_register_job_post_channel_returns_false_on_noop(monkeypatch) -> None:
    cursor = _CursorStub(row=None)
    _install_connection_stub(monkeypatch, cursor)

    created = job_channels.register_job_post_channel(
        job_channels.SharedSettings(), guild_id="guild-1", channel_id="123"
    )

    assert created is False


def test_unregister_job_post_channel_returns_true_on_delete(monkeypatch) -> None:
    cursor = _CursorStub(row={"channel_id": "123"})
    _install_connection_stub(monkeypatch, cursor)

    removed = job_channels.unregister_job_post_channel(
        job_channels.SharedSettings(), guild_id="guild-1", channel_id="123"
    )

    assert removed is True
    assert cursor.executed[0][1] == ("guild-1", "123")


def test_unregister_job_post_channel_returns_false_on_noop(monkeypatch) -> None:
    cursor = _CursorStub(row=None)
    _install_connection_stub(monkeypatch, cursor)

    removed = job_channels.unregister_job_post_channel(
        job_channels.SharedSettings(), guild_id="guild-1", channel_id="123"
    )

    assert removed is False
