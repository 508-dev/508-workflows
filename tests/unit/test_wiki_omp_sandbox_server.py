"""Contracts for the isolated OMP wiki authoring sidecar."""

from __future__ import annotations

import json
import threading
from typing import Any
from unittest.mock import Mock, patch

import pytest

from five08.wiki_editing import omp_sandbox_server
from five08.wiki_editing.omp_sandbox_server import (
    OmpRpcSession,
    OmpRunError,
    OmpRunTimeout,
    SandboxMaterial,
    SandboxRequestError,
    SandboxSettings,
    parse_draft_submission,
    parse_sandbox_run,
)


def _request_payload() -> dict[str, object]:
    return {
        "protocol_version": "v1",
        "run": {
            "run_id": "run-123",
            "attempt": 1,
            "model": "openrouter/test-model",
            "thinking": "medium",
        },
        "proposal": {
            "action": "create",
            "target_document_id": None,
            "revision_instruction": None,
        },
        "materials": [
            {
                "id": "request:1",
                "source": {
                    "title": "Explicit wiki update request",
                },
                "text": "Document the approved release checklist.",
            }
        ],
        "draft_contract": {
            "one_draft_only": True,
            "title_max_characters": 512,
            "text_max_characters": 16_000,
            "summary_max_characters": 8_000,
            "source_ids_must_come_from_materials": True,
            "no_publish": True,
        },
        "instructions": "Create one reviewable draft from the supplied material.",
    }


def _draft_submission(
    *, source_ids: list[str], text: str = "Use the approved release checklist."
) -> str:
    return json.dumps(
        {
            "action": "create",
            "target_document_id": None,
            "title": "Release checklist",
            "text": text,
            "summary": "Records the release checklist.",
            "source_ids": source_ids,
        }
    )


def test_http_body_limit_accepts_worst_case_valid_json_encoding() -> None:
    """Valid non-BMP input must fit even when requests emits surrogate escapes."""
    non_bmp = "\U0001f680"
    payload = _request_payload()
    payload["run"] = {
        "run_id": non_bmp * 512,
        "attempt": 100,
        "model": "openrouter/" + ("m" * 240),
        "thinking": "xhigh",
    }
    payload["proposal"] = {
        "action": "update",
        "target_document_id": non_bmp * 256,
        "revision_instruction": non_bmp * 4_000,
    }
    payload["materials"] = [
        {
            "id": f"{index:02d}" + (non_bmp * 254),
            "source": {"title": non_bmp * 512},
            "text": non_bmp * 1_500,
        }
        for index in range(32)
    ]
    payload["instructions"] = non_bmp * 2_000

    parse_sandbox_run(payload)
    encoded = json.dumps(payload, allow_nan=False).encode("utf-8")

    assert len(encoded) > 600_000
    assert len(encoded) <= omp_sandbox_server.MAX_HTTP_BODY_BYTES


@pytest.mark.parametrize(
    "mutation",
    [
        lambda payload: payload.update(unexpected="prompt injection"),
        lambda payload: payload.update(
            materials=[payload["materials"][0], payload["materials"][0]]
        ),
    ],
)
def test_parse_sandbox_run_rejects_noncanonical_request_or_materials(
    mutation: Any,
) -> None:
    payload = _request_payload()

    mutation(payload)

    with pytest.raises(SandboxRequestError):
        parse_sandbox_run(payload)


def test_parse_draft_submission_allows_only_approved_unique_source_ids() -> None:
    run = parse_sandbox_run(_request_payload())

    with pytest.raises(OmpRunError, match="unapproved material"):
        parse_draft_submission(
            _draft_submission(source_ids=["request:1", "private-memory:99"]),
            run,
        )
    with pytest.raises(OmpRunError, match="unapproved material"):
        parse_draft_submission(
            _draft_submission(source_ids=["request:1", "request:1"]), run
        )


def test_sandbox_contract_enforces_the_configured_document_limit() -> None:
    payload = _request_payload()
    draft_contract = payload["draft_contract"]
    assert isinstance(draft_contract, dict)
    draft_contract["text_max_characters"] = 1_000

    run = parse_sandbox_run(payload)

    assert run.max_document_characters == 1_000
    with pytest.raises(OmpRunError, match="invalid draft text"):
        parse_draft_submission(
            _draft_submission(source_ids=["request:1"], text="x" * 1_001),
            run,
        )


@pytest.mark.parametrize("limit", [999, 16_001, True])
def test_sandbox_contract_rejects_out_of_policy_document_limit(limit: object) -> None:
    payload = _request_payload()
    draft_contract = payload["draft_contract"]
    assert isinstance(draft_contract, dict)
    draft_contract["text_max_characters"] = limit

    with pytest.raises(SandboxRequestError, match="unsupported draft contract"):
        parse_sandbox_run(payload)


def test_parse_sandbox_run_exposes_only_prompt_safe_material_fields() -> None:
    run = parse_sandbox_run(_request_payload())

    assert run.materials == (
        SandboxMaterial(
            source_id="request:1",
            title="Explicit wiki update request",
            text="Document the approved release checklist.",
        ),
    )


def test_sandbox_settings_require_the_internal_egress_proxy(tmp_path: Any) -> None:
    provider_key = tmp_path / "openrouter-key"
    provider_key.write_text("openrouter-key", encoding="utf-8")

    with pytest.raises(RuntimeError, match="WIKI_OMP_EGRESS_PROXY_URL"):
        SandboxSettings.from_environment(
            {
                "WIKI_OMP_SANDBOX_TOKEN": "sandbox-token",
                "OPENROUTER_API_KEY_FILE": str(provider_key),
            }
        )


def _rpc_process(*, frames: list[bytes]) -> tuple[Mock, Mock]:
    process = Mock()
    process.stdin = Mock()
    process.stdin.write.side_effect = lambda frame: len(frame)
    process.stdout = Mock()
    process.stdout.fileno.return_value = 42
    process.poll.return_value = 0
    return process, Mock(side_effect=frames)


def _rpc_response(request_id: str, command: str, data: dict[str, object]) -> bytes:
    return (
        json.dumps(
            {
                "type": "response",
                "id": request_id,
                "command": command,
                "success": True,
                "data": data,
            }
        ).encode("utf-8")
        + b"\n"
    )


def test_omp_child_uses_minimal_environment_and_disables_builtin_tools(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("WIKI_OMP_SANDBOX_TOKEN", "sandbox-token-must-not-reach-omp")
    monkeypatch.setenv("OPENROUTER_API_KEY_FILE", "/run/secrets/openrouter-key")
    process, read_chunk = _rpc_process(
        frames=[
            b'{"type":"ready"}\n',
            _rpc_response("wiki_1", "get_state", {"dumpTools": []}),
            _rpc_response("wiki_2", "set_auto_retry", {}),
            _rpc_response("wiki_3", "set_auto_compaction", {}),
        ]
    )
    process_factory = Mock(return_value=process)
    monkeypatch.setattr(
        omp_sandbox_server.select,
        "select",
        lambda readable, _writable, _exceptional, _timeout: (readable, [], []),
    )
    session = OmpRpcSession(
        settings=SandboxSettings(
            token="sandbox-token",
            openrouter_api_key="openrouter-key",
            egress_proxy_url="http://wiki_omp_egress_proxy:3128",
            omp_executable="/usr/local/bin/omp",
        ),
        model="openrouter/test-model",
        thinking="medium",
        process_factory=process_factory,
        read_chunk=read_chunk,
    )

    with session:
        pass

    command = process_factory.call_args.args[0]
    environment = process_factory.call_args.kwargs["env"]
    assert "WIKI_OMP_SANDBOX_TOKEN" not in environment
    assert "OPENROUTER_API_KEY_FILE" not in environment
    assert environment["OPENROUTER_API_KEY"] == "openrouter-key"
    assert environment["HTTPS_PROXY"] == "http://wiki_omp_egress_proxy:3128"
    assert environment["HTTP_PROXY"] == "http://wiki_omp_egress_proxy:3128"
    assert environment["ALL_PROXY"] == "http://wiki_omp_egress_proxy:3128"
    assert environment["https_proxy"] == "http://wiki_omp_egress_proxy:3128"
    for flag in {
        "--no-tools",
        "--no-session",
        "--no-skills",
        "--no-rules",
        "--no-extensions",
        "--no-lsp",
        "--no-pty",
        "--no-title",
    }:
        assert flag in command

    sent_commands = [
        json.loads(call.args[0].decode("utf-8"))["type"]
        for call in process.stdin.write.call_args_list
    ]
    assert sent_commands == ["get_state", "set_auto_retry", "set_auto_compaction"]


def test_omp_session_fails_closed_if_builtin_tools_are_still_exposed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    process, read_chunk = _rpc_process(
        frames=[
            b'{"type":"ready"}\n',
            _rpc_response("wiki_1", "get_state", {"dumpTools": [{"name": "read"}]}),
        ]
    )
    monkeypatch.setattr(
        omp_sandbox_server.select,
        "select",
        lambda readable, _writable, _exceptional, _timeout: (readable, [], []),
    )
    session = OmpRpcSession(
        settings=SandboxSettings(
            token="sandbox-token",
            openrouter_api_key="openrouter-key",
            egress_proxy_url="http://wiki_omp_egress_proxy:3128",
        ),
        model="openrouter/test-model",
        thinking="medium",
        process_factory=Mock(return_value=process),
        read_chunk=read_chunk,
    )

    with pytest.raises(OmpRunError, match="tool isolation"):
        session.__enter__()


def test_partial_rpc_frame_honors_the_session_deadline(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    process = Mock()
    process.stdout = Mock()
    process.stdout.fileno.return_value = 42
    partial_frame = b'{"type":"ready"'
    read_chunk = Mock(return_value=partial_frame)
    select_results = iter(
        [
            ([process.stdout], [], []),
            ([], [], []),
        ]
    )
    monkeypatch.setattr(
        omp_sandbox_server.select,
        "select",
        lambda *_args: next(select_results),
    )
    session = OmpRpcSession(
        settings=SandboxSettings(
            token="sandbox-token",
            openrouter_api_key="openrouter-key",
            egress_proxy_url="http://wiki_omp_egress_proxy:3128",
        ),
        model="openrouter/test-model",
        thinking="medium",
        read_chunk=read_chunk,
    )
    session.process = process
    session._deadline = omp_sandbox_server.time.monotonic() + 1.0

    with pytest.raises(OmpRunTimeout, match="timed out"):
        session._read_frame()

    read_chunk.assert_called_once_with(42, omp_sandbox_server.MAX_RPC_FRAME_BYTES + 1)
    process.stdout.readline.assert_not_called()


def test_rpc_write_honors_the_session_deadline() -> None:
    release_writer = threading.Event()
    process = Mock()

    def _blocked_write(frame: bytes) -> int:
        release_writer.wait(timeout=1.0)
        return len(frame)

    process.stdin.write.side_effect = _blocked_write
    session = OmpRpcSession(
        settings=SandboxSettings(
            token="sandbox-token",
            openrouter_api_key="openrouter-key",
            egress_proxy_url="http://wiki_omp_egress_proxy:3128",
        ),
        model="openrouter/test-model",
        thinking="medium",
    )
    session.process = process
    session._deadline = omp_sandbox_server.time.monotonic() + 0.01

    try:
        with (
            patch.object(session, "close") as mock_close,
            pytest.raises(OmpRunTimeout, match="timed out"),
        ):
            session._send({"type": "prompt", "message": "x" * 100_000})
    finally:
        release_writer.set()

    mock_close.assert_called_once_with()


def test_rpc_write_completes_a_short_unbuffered_write() -> None:
    process = Mock()
    writes = 0
    accepted = bytearray()

    def _short_write(frame: bytes) -> int:
        nonlocal writes
        writes += 1
        count = min(7, len(frame))
        accepted.extend(frame[:count])
        return count

    process.stdin.write.side_effect = _short_write
    session = OmpRpcSession(
        settings=SandboxSettings(
            token="sandbox-token",
            openrouter_api_key="openrouter-key",
            egress_proxy_url="http://wiki_omp_egress_proxy:3128",
        ),
        model="openrouter/test-model",
        thinking="medium",
    )
    session.process = process
    session._deadline = omp_sandbox_server.time.monotonic() + 1.0

    request_id = session._send({"type": "prompt", "message": "x" * 100})

    assert request_id == "wiki_1"
    assert writes > 1
    assert json.loads(accepted) == {
        "id": "wiki_1",
        "type": "prompt",
        "message": "x" * 100,
    }
    process.stdin.flush.assert_called_once_with()


def test_maximum_size_unterminated_rpc_frame_is_rejected_without_waiting(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A full frame without its newline cannot become valid after more waiting."""
    process = Mock()
    process.stdout = Mock()
    process.stdout.fileno.return_value = 42
    read_chunk = Mock(return_value=b"x" * omp_sandbox_server.MAX_RPC_FRAME_BYTES)
    monkeypatch.setattr(
        omp_sandbox_server.select,
        "select",
        Mock(return_value=([process.stdout], [], [])),
    )
    session = omp_sandbox_server.OmpRpcSession(
        settings=omp_sandbox_server.SandboxSettings(
            token="sandbox-token",
            openrouter_api_key="openrouter-key",
            egress_proxy_url="http://wiki_omp_egress_proxy:3128",
        ),
        model="openrouter/test-model",
        thinking="medium",
        read_chunk=read_chunk,
    )
    session.process = process
    session._deadline = omp_sandbox_server.time.monotonic() + 1.0

    with pytest.raises(OmpRunError, match="safe boundary"):
        session._read_frame()

    read_chunk.assert_called_once_with(42, omp_sandbox_server.MAX_RPC_FRAME_BYTES + 1)
    process.stdout.readline.assert_not_called()
