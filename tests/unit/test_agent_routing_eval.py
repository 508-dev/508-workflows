"""Guard Discord mention dispatch and the optional decision-model replay."""

from unittest.mock import Mock

import pytest
import requests

from scripts import agent_routing_eval as routing


@pytest.mark.parametrize(
    "case",
    [case for case in routing.load_cases() if not case.challenge],
    ids=lambda case: case.id,
)
def test_mention_routing_regressions(case: routing.RoutingCase) -> None:
    assert routing.AgentCog._mention_workflow_route(case.message) == case.route


def test_live_routing_eval_scores_acceptance_and_falls_back(monkeypatch) -> None:
    cases = routing.load_cases()
    cases_by_id = {case.id: case for case in cases}
    selected = [
        cases_by_id[case_id]
        for case_id in (
            "job-search-implicit",
            "workflow-job-task",
            "knowledge-job-policy",
            "knowledge-gig-guidelines",
        )
    ]
    weak = dict(job_search=0.4, knowledge_question=0.3, workflow=0.2, abstain=0.1)
    strong = dict(job_search=0.97, knowledge_question=0.01, workflow=0.01, abstain=0.01)

    def response(probabilities: dict) -> Mock:
        return Mock(
            **{
                "json.return_value": {
                    "answers": {
                        "route": {
                            "type": "choice",
                            "choice": "job_search",
                            "probabilities": probabilities,
                        }
                    },
                    "usage": {"cost": 0.000001},
                }
            }
        )

    post = Mock(
        side_effect=[
            response(strong),
            response(weak),
            response({"job_search": 1}),
            requests.Timeout(),
        ]
    )
    monkeypatch.setattr(routing.requests, "post", post)
    model = "typesafe/jev-1.14"
    report = routing.run_comparison(selected, [model], "fake-key", 0.85)

    assert report["rules_correct"] == 3
    metrics = report["models"][model]
    assert metrics["correct"] == 4
    assert metrics["improvements"] == 1
    assert metrics["regressions"] == metrics["workflow_sent_elsewhere"] == 0
    assert metrics["accepted"] == metrics["deferred"] == 1
    assert metrics["error"] == 2
    assert metrics["calls_with_reported_cost"] == 3
    assert metrics["reported_cost_usd"] == pytest.approx(0.000003)
    assert post.call_count == len(selected)
    assert post.call_args_list[0].args == (routing.DECISIONS_URL,)
    assert post.call_args_list[0].kwargs["json"]["state"] == {
        "discord_message": selected[0].message
    }


def test_chat_baseline_matches_route_contract_and_handles_abstention(
    monkeypatch,
) -> None:
    cases = {case.id: case for case in routing.load_cases()}
    selected = [
        cases["job-search-implicit"],
        cases["knowledge-job-policy"],
        cases["workflow-job-task"],
    ]
    payloads = [
        {"choices": [{"message": {"content": '{"choice":"job_search"}'}}]},
        {"choices": [{"message": {"content": '{"choice":"abstain"}'}}]},
        {"choices": [{"message": {"content": '{"choice":"unknown"}'}}]},
    ]
    post = Mock(
        side_effect=[
            Mock(json=Mock(return_value={**payload, "usage": {"cost": 0.001}}))
            for payload in payloads
        ]
    )
    monkeypatch.setattr(routing.requests, "post", post)
    model = "openai/gpt-4.1-mini"

    report = routing.run_comparison(
        selected, [], "fake-key", 0.85, chat_models=(model,)
    )

    metrics = report["models"][model]
    assert metrics["endpoint"] == "chat"
    assert metrics["correct"] == 3
    assert metrics["improvements"] == 1
    assert metrics["accepted"] == metrics["deferred"] == metrics["error"] == 1
    assert metrics["calls_with_reported_cost"] == 3
    assert metrics["reported_cost_usd"] == pytest.approx(0.003)
    assert post.call_args_list[0].args == (routing.CHAT_URL,)
    request = post.call_args_list[0].kwargs["json"]
    assert request["model"] == model
    schema = request["response_format"]["json_schema"]["schema"]
    assert set(schema["properties"]["choice"]["enum"]) == set(routing.ROUTES)
    assert selected[0].message in request["messages"][1]["content"]
