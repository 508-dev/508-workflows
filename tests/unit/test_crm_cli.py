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
            "--timezone-empty",
            "--location-present",
            "--member-type",
            "Member",
            "--phone-country-code",
            "+1",
            "--phone-missing-country-code",
            "--limit",
            "5",
        ]
    )

    assert exit_code == 0
    assert repo.search_calls == [
        {
            "limit": 5,
            "timezone_empty": True,
            "location_present": True,
            "member_type": ["Member"],
            "phone_country_code": "+1",
            "phone_missing_country_code": True,
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
            "--phone-country-code",
            "+1",
            "--phone-has-country-code",
            "--set",
            "timezone=@location",
            "--set",
            'roles=["developer","designer"]',
            "--set",
            "member_type=Member",
            "--set",
            "seniority=null",
        ]
    )

    assert exit_code == 0
    assert repo.batch_update_calls == [
        {
            "search": {
                "phone_country_code": "+1",
                "phone_missing_country_code": False,
            },
            "updates": {
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


def test_crmctl_search_rejects_conflicting_timezone_flags() -> None:
    with pytest.raises(SystemExit) as exc_info:
        crm_cli.run(["search", "--timezone", "UTC-05:00", "--timezone-empty"])

    assert exc_info.value.code == 2


def test_crmctl_search_requires_phone_country_code_for_prefix_filters() -> None:
    with pytest.raises(SystemExit) as exc_info:
        crm_cli.run(["search", "--phone-missing-country-code"])

    assert exc_info.value.code == 2


def test_crmctl_batch_update_rejects_invalid_assignment() -> None:
    with pytest.raises(SystemExit) as exc_info:
        crm_cli.run(["batch-update", "--set", "timezone"])

    assert exc_info.value.code == 2


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
