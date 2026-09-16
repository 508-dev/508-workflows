"""Tests for grounded knowledge model output validation."""

from unittest.mock import Mock

import pytest

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
