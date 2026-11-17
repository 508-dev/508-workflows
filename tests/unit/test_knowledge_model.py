"""Tests for grounded knowledge model output validation."""

from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from five08.knowledge import model as knowledge_model
from five08.knowledge.model import OpenAICompatibleKnowledgeModel
from five08.knowledge.models import KnowledgeEvidence


@pytest.mark.parametrize(
    "payload",
    [
        {
            "status": "insufficient",
            "answer": "Leftover model text",
            "evidence_ids": [],
            "confidence": 0.4,
        },
        {
            "status": "insufficient",
            "answer": "",
            "evidence_ids": ["memory:1"],
            "confidence": 0.4,
        },
    ],
)
def test_malformed_model_abstention_remains_insufficient(
    monkeypatch: pytest.MonkeyPatch,
    payload: dict[str, object],
) -> None:
    monkeypatch.setattr(
        OpenAICompatibleKnowledgeModel,
        "_chat_json",
        lambda *_args, **_kwargs: payload,
    )
    model = OpenAICompatibleKnowledgeModel(config=Mock())

    draft = model.answer(
        question="Does it deploy?",
        evidence=[
            KnowledgeEvidence(
                evidence_id="memory:1",
                source_type="memory",
                source_ref="fact-1",
                title="Deployment",
                excerpt="It deploys.",
                visibility="org",
            )
        ],
    )

    assert draft is not None
    assert draft.status == "insufficient"
    assert draft.answer == ""
    assert draft.evidence_ids == []


def test_response_format_retry_shares_one_model_deadline(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = Mock()
    config.resolve.return_value = SimpleNamespace(
        model="test-model",
        source_tier="default",
        base_url="https://model.example",
    )
    config.openai_api_key = "secret"
    config.openai_base_url = "https://model.example"
    unsupported = Mock(
        status_code=400,
        text="response_format is an unsupported parameter",
    )
    succeeded = Mock(status_code=200, text="")
    succeeded.json.return_value = {
        "choices": [{"message": {"content": '{"status":"insufficient"}'}}]
    }
    post = Mock(side_effect=[unsupported, succeeded])
    monkeypatch.setattr(knowledge_model.requests, "post", post)
    monkeypatch.setattr(
        knowledge_model.time,
        "monotonic",
        Mock(side_effect=[10.0, 10.25, 11.0]),
    )
    model = OpenAICompatibleKnowledgeModel(config=config, timeout_seconds=3.0)

    result = model._chat_json(
        system_prompt="system",
        user_payload={"question": "question"},
        max_tokens=10,
    )

    assert result == {"status": "insufficient"}
    assert post.call_count == 2
    assert post.call_args_list[0].kwargs["timeout"] == pytest.approx(2.75)
    assert post.call_args_list[1].kwargs["timeout"] == pytest.approx(2.0)
