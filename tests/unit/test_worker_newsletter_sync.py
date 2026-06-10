"""Unit tests for worker newsletter sync job result handling."""

import pytest

from five08.worker import jobs


def test_sync_508_members_newsletters_job_masks_failure_emails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeNewsletterSyncProcessor:
        def __init__(self, settings: object) -> None:
            self.settings = settings

        def sync_508_members(self) -> dict[str, object]:
            return {
                "crm_lookup_failures": [
                    {"mailbox": "jane@508.dev", "error": "CRM unavailable"}
                ],
                "providers": {
                    "brevo": {
                        "failures": [
                            {
                                "email": "jane@example.com",
                                "error": "provider unavailable",
                            }
                        ]
                    },
                    "keila": {"failures": "not-a-list"},
                },
            }

    monkeypatch.setattr(
        jobs,
        "NewsletterSyncProcessor",
        FakeNewsletterSyncProcessor,
    )

    result = jobs.sync_508_members_newsletters_job()

    assert result == {
        "crm_lookup_failures": [
            {"mailbox": "j***@5****...", "error": "CRM unavailable"}
        ],
        "providers": {
            "brevo": {
                "failures": [
                    {
                        "email": "j***@e****...",
                        "error": "provider unavailable",
                    }
                ]
            },
            "keila": {"failures": "not-a-list"},
        },
    }
