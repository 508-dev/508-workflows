"""Contracts for the isolated OMP wiki authoring sidecar."""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import Mock

import pytest

from five08.wiki_editing import omp_sandbox_server
from five08.wiki_editing.omp_sandbox_server import (
    OmpRpcSession,
    OmpRunError,
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
            "text_max_characters": 500_000,
            "summary_max_characters": 8_000,
            "source_ids_must_come_from_materials": True,
            "no_publish": True,
        },
        "instructions": "Create one reviewable draft from the supplied material.",
    }


def _draft_submission(*, source_ids: list[str]) -> str:
    return json.dumps(
        {
            "action": "create",
            "target_document_id": None,
            "title": "Release checklist",
            "text": "Use the approved release checklist.",
            "summary": "Records the release checklist.",
            "source_ids": source_ids,
        }
    )


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


def _rpc_process(*, frames: list[bytes]) -> Mock:
    process = Mock()
    process.stdin = Mock()
    process.stdout = Mock()
    process.stdout.readline.side_effect = frames
    process.poll.return_value = 0
    return process


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
    process = _rpc_process(
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
    process = _rpc_process(
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
    )

    with pytest.raises(OmpRunError, match="tool isolation"):
        session.__enter__()
