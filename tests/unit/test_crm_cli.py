from __future__ import annotations

from typing import Any

import pytest

from five08 import crm_cli
from five08.crm_contacts import BatchUpdateResult, ContactUpdatePreview, FROM_LOCATION


class FakeContact:
    def __init__(self, payload: dict[str, Any]) -> None:
        self._payload = payload

    def to_dict(self) -> dict[str, Any]:
        return dict(self._payload)


class FakeRepository:
    def __init__(self) -> None:
        self.search_calls: list[dict[str, Any]] = []
        self.batch_update_calls: list[dict[str, Any]] = []
        self.search_result = [FakeContact({"id": "contact-1", "name": "Alice"})]
        self.batch_result = BatchUpdateResult(
            previews=[
                ContactUpdatePreview(
                    contact_id="contact-1",
                    name="Alice",
                    updates={"cTimezone": "UTC-05:00"},
                )
            ],
            applied=False,
        )

    def search(self, **kwargs: Any) -> list[FakeContact]:
        self.search_calls.append(kwargs)
        return self.search_result

    def batch_update(self, **kwargs: Any) -> BatchUpdateResult:
        self.batch_update_calls.append(kwargs)
        return self.batch_result


def test_crmctl_search_passes_expected_criteria(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    repo = FakeRepository()
    monkeypatch.setattr(crm_cli, "_load_repository", lambda: repo)

    exit_code = crm_cli.run(
        [
            "search",
            "--where",
            "timezone__is_null=true",
            "--where",
            "location__is_not_null=true",
            "--where",
            "member_type__in=Member,Prospect",
            "--where",
            "phone__not_like=+1%",
            "--limit",
            "5",
        ]
    )

    assert exit_code == 0
    assert repo.search_calls == [
        {
            "limit": 5,
            "timezone__is_null": True,
            "location__is_not_null": True,
            "member_type__in": "Member,Prospect",
            "phone__not_like": "+1%",
        }
    ]
    assert '"count": 1' in capsys.readouterr().out


def test_crmctl_batch_update_parses_assignments(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    repo = FakeRepository()
    monkeypatch.setattr(crm_cli, "_load_repository", lambda: repo)

    exit_code = crm_cli.run(
        [
            "batch-update",
            "--limit",
            "0",
            "--where",
            "timezone__is_null=true",
            "--where",
            "location__is_not_null=true",
            "--update",
            "timezone=@location",
            "--update",
            'roles=["developer","designer"]',
            "--update",
            "member_type=Member",
            "--update",
            "seniority=null",
        ]
    )

    assert exit_code == 0
    assert repo.batch_update_calls == [
        {
            "where": {
                "timezone__is_null": True,
                "location__is_not_null": True,
            },
            "update": {
                "timezone": FROM_LOCATION,
                "roles": ["developer", "designer"],
                "member_type": "Member",
                "seniority": None,
            },
            "limit": None,
            "apply": False,
        }
    ]
    assert '"applied": false' in capsys.readouterr().out


def test_crmctl_parses_scalar_literals_in_where_and_update(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = FakeRepository()
    monkeypatch.setattr(crm_cli, "_load_repository", lambda: repo)

    exit_code = crm_cli.run(
        [
            "batch-update",
            "--where",
            "age__gte=65",
            "--update",
            "score=10",
        ]
    )

    assert exit_code == 0
    assert repo.batch_update_calls == [
        {
            "where": {"age__gte": 65},
            "update": {"score": 10},
            "limit": 100,
            "apply": False,
        }
    ]


def test_crmctl_search_accepts_value_style_filters(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = FakeRepository()
    monkeypatch.setattr(crm_cli, "_load_repository", lambda: repo)

    exit_code = crm_cli.run(
        [
            "search",
            "--where",
            "timezone__equals=UTC-05:00",
            "--where",
            "roles__is_null=true",
        ]
    )

    assert exit_code == 0
    assert repo.search_calls == [
        {
            "limit": 100,
            "timezone__equals": "UTC-05:00",
            "roles__is_null": True,
        }
    ]


def test_crmctl_search_requires_phone_country_code_for_prefix_filters() -> None:
    with pytest.raises(SystemExit) as exc_info:
        crm_cli.run(["search", "--phone-country-code-match", "missing"])

    assert exc_info.value.code == 2


def test_crmctl_batch_update_rejects_invalid_assignment() -> None:
    with pytest.raises(SystemExit) as exc_info:
        crm_cli.run(["batch-update", "--update", "timezone"])

    assert exc_info.value.code == 2


def test_crmctl_rejects_duplicate_where_assignment() -> None:
    with pytest.raises(SystemExit) as exc_info:
        crm_cli.run(
            [
                "search",
                "--where",
                "timezone__is_null=true",
                "--where",
                "timezone__is_null=false",
            ]
        )

    assert exc_info.value.code == 2


def test_crmctl_rejects_duplicate_update_assignment() -> None:
    with pytest.raises(SystemExit) as exc_info:
        crm_cli.run(
            [
                "batch-update",
                "--update",
                "timezone=UTC+01:00",
                "--update",
                "timezone=UTC+02:00",
            ]
        )

    assert exc_info.value.code == 2


def test_crmctl_batch_update_reports_invalid_timezone_update(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    repo = FakeRepository()
    monkeypatch.setattr(crm_cli, "_load_repository", lambda: repo)

    def _raise_batch_update(**kwargs: Any) -> BatchUpdateResult:
        raise ValueError("Invalid timezone value: 'EST'")

    repo.batch_update = _raise_batch_update  # type: ignore[assignment]

    exit_code = crm_cli.run(
        [
            "batch-update",
            "--where",
            "timezone__is_null=true",
            "--update",
            "timezone=EST",
        ]
    )

    assert exit_code == 1
    assert "Invalid timezone value" in capsys.readouterr().err


def test_crmctl_batch_update_reports_invalid_from_location_target(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    repo = FakeRepository()
    monkeypatch.setattr(crm_cli, "_load_repository", lambda: repo)

    def _raise_batch_update(**kwargs: Any) -> BatchUpdateResult:
        raise ValueError("FROM_LOCATION is only supported for timezone updates")

    repo.batch_update = _raise_batch_update  # type: ignore[assignment]

    exit_code = crm_cli.run(
        [
            "batch-update",
            "--where",
            "timezone__is_null=true",
            "--update",
            "city=@location",
        ]
    )

    assert exit_code == 1
    assert (
        "FROM_LOCATION is only supported for timezone updates"
        in capsys.readouterr().err
    )


def test_crmctl_reports_runtime_error(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    repo = FakeRepository()

    def _raise_search(**kwargs: Any) -> list[FakeContact]:
        raise RuntimeError("boom")

    repo.search = _raise_search  # type: ignore[assignment]
    monkeypatch.setattr(crm_cli, "_load_repository", lambda: repo)

    exit_code = crm_cli.run(["search"])

    assert exit_code == 1
    assert "Error: boom" in capsys.readouterr().err
