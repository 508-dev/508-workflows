"""Worker startup and durable wiki retry recovery contracts."""

from unittest.mock import patch

from five08.worker import consumer


def test_recovery_redispatches_due_wiki_retries() -> None:
    with (
        patch(
            "five08.worker.consumer.list_due_retry_job_ids",
            return_value=["job-1", "job-2"],
        ) as scan,
        patch("five08.worker.actors.execute_job.send") as send,
    ):
        consumer._recover_due_wiki_retries()

    scan.assert_called_once_with(
        consumer.settings, job_type="author_wiki_edit_proposal_job"
    )
    assert [call.args for call in send.call_args_list] == [
        ("job-1",),
        ("job-2",),
    ]
