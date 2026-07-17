"""Execute project-payment actions and their Discord notification outbox."""

from __future__ import annotations

import logging
from typing import Any

from five08.automation_store import (
    automation_action_subject_id,
    claim_automation_action,
    mark_automation_action_failed,
)
from five08.clients.discord_bot import DiscordBotAPIError, DiscordBotClient
from five08.clients.erpnext import ERPNextAPIError, ERPNextClient
from five08.project_payments import (
    PROJECT_PAYMENT_ROUTE_ACTION,
    ProjectPaymentActionApplicationStatus,
    ProjectPaymentAllocationStatus,
    apply_project_payment_automation_action,
    block_project_payment_notification_if_ineligible,
    claim_project_payment_notification,
    erpnext_bank_transaction_to_input,
    get_bank_transaction,
    get_project_payment_notification_source_context,
    mark_project_payment_notification_failed,
    mark_project_payment_notification_sent,
    upsert_bank_transaction,
)
from five08.projects import (
    erpnext_project_external_id,
    erpnext_project_to_input,
    upsert_project,
)
from five08.settings import SharedSettings
from five08.worker.config import settings


logger = logging.getLogger(__name__)


class ProjectPaymentActionPreflightBlocked(Exception):
    """A canonical source recheck proves a claimed action cannot proceed."""


class ProjectPaymentActionExecutor:
    """Executor for the registered v1 project-payment action type.

    The allocation itself is deliberately not assembled here.  It lives in
    ``apply_project_payment_automation_action`` so its action lease, rule
    version, ERP source revision, project state, allocation, and notification
    outbox commit as one database transaction.
    """

    def __init__(self, app_settings: SharedSettings = settings) -> None:
        self.settings = app_settings

    def execute_action(self, action_id: str) -> dict[str, Any]:
        """Claim and apply one durable payment-routing action."""
        if not bool(
            getattr(self.settings, "project_payment_automation_enabled", False)
        ):
            return {"status": "disabled", "action_id": action_id}

        action = claim_automation_action(self.settings, action_id=action_id)
        if action is None:
            return {"status": "not_claimed", "action_id": action_id}
        if action.lease_token is None:
            logger.error("Claimed payment action has no lease token id=%s", action.id)
            return {"status": "not_claimed", "action_id": action.id}
        if action.action_type != PROJECT_PAYMENT_ROUTE_ACTION:
            mark_automation_action_failed(
                self.settings,
                action_id=action.id,
                error=f"unregistered payment action type: {action.action_type}",
                dead=True,
                lease_token=action.lease_token,
            )
            return {
                "status": "ignored",
                "action_id": action.id,
                "reason": "unregistered_action_type",
            }

        try:
            self._refresh_bank_transaction_from_erpnext(action.id)
            if action.rule_project_id:
                self._refresh_project_from_erpnext(action.rule_project_id)
            applied = apply_project_payment_automation_action(
                self.settings,
                action_id=action.id,
                lease_token=action.lease_token,
            )
        except ProjectPaymentActionPreflightBlocked as exc:
            mark_automation_action_failed(
                self.settings,
                action_id=action.id,
                error=str(exc),
                dead=True,
                lease_token=action.lease_token,
            )
            return {
                "status": "blocked",
                "action_id": action.id,
                "reason": str(exc),
            }
        except Exception as exc:
            mark_automation_action_failed(
                self.settings,
                action_id=action.id,
                error=f"{type(exc).__name__}: {exc}",
                lease_token=action.lease_token,
            )
            raise

        if applied.status is ProjectPaymentActionApplicationStatus.NOT_OWNER:
            return {"status": "not_claimed", "action_id": action.id}
        if applied.status is ProjectPaymentActionApplicationStatus.BLOCKED:
            return {
                "status": "blocked",
                "action_id": action.id,
                "reason": applied.reason,
            }

        delivered_notification_ids: list[str] = []
        if bool(getattr(self.settings, "project_payment_notifications_enabled", False)):
            for notification_id in applied.notification_ids:
                delivery = self.deliver_notification(notification_id)
                if delivery["status"] == "sent":
                    delivered_notification_ids.append(notification_id)

        return {
            "status": "succeeded",
            "action_id": action.id,
            "allocation_id": applied.allocation_id,
            "allocation_created": applied.allocation_created,
            "notification_ids": list(applied.notification_ids),
            "delivered_notification_ids": delivered_notification_ids,
        }

    def _erpnext_client(self) -> ERPNextClient:
        """Create an authenticated ERP client for an action-time recheck."""
        base_url = str(getattr(self.settings, "erpnext_base_url", "") or "").strip()
        api_key = str(getattr(self.settings, "erpnext_api_key", "") or "").strip()
        if not base_url or not api_key:
            raise ValueError("payment_routing_requires_erpnext_credentials")
        return ERPNextClient(
            base_url,
            api_key,
            timeout_seconds=float(
                getattr(self.settings, "erpnext_api_timeout_seconds", 20.0)
            ),
        )

    def _refresh_bank_transaction_from_erpnext(self, action_id: str) -> None:
        """Reproject the canonical Bank Transaction before a money action.

        The submit webhook is only an ingestion hint. A delayed or retried
        action must not rely on a source row that has since been canceled or
        corrected in ERPNext. A newer document revision is written locally and
        then rejected by the immutable snapshot fence in the atomic apply.
        """
        subject_id = automation_action_subject_id(self.settings, action_id=action_id)
        if subject_id is None:
            raise ProjectPaymentActionPreflightBlocked(
                "payment_event_has_no_transaction_subject"
            )
        stored_transaction = get_bank_transaction(
            self.settings,
            transaction_id=subject_id,
        )
        if stored_transaction is None:
            raise ProjectPaymentActionPreflightBlocked("payment_transaction_not_found")
        if stored_transaction.source != "erpnext":
            raise ProjectPaymentActionPreflightBlocked(
                "payment_transaction_source_is_not_erpnext"
            )

        client = self._erpnext_client()
        try:
            try:
                document = client.get_bank_transaction(stored_transaction.external_id)
            except ERPNextAPIError as exc:
                if exc.status_code == 404:
                    raise ProjectPaymentActionPreflightBlocked(
                        "bank_transaction_not_found_in_erpnext"
                    ) from exc
                raise
        finally:
            client.close()
        transaction = erpnext_bank_transaction_to_input(document)
        if transaction is None:
            raise ProjectPaymentActionPreflightBlocked(
                "bank_transaction_is_no_longer_submitted"
            )
        if transaction.external_id != stored_transaction.external_id:
            raise ProjectPaymentActionPreflightBlocked(
                "bank_transaction_identity_changed"
            )
        # ERPNext's ``modified`` value is our revision ordering fence. A
        # nonempty but unparsable string cannot safely establish which source
        # document is newer, so automatic routing must fail closed rather than
        # treat it as an opaque revision token.
        if (
            transaction.source_revision is None
            or transaction.source_modified_at is None
        ):
            raise ProjectPaymentActionPreflightBlocked(
                "bank_transaction_has_no_canonical_revision"
            )
        persisted_transaction = upsert_bank_transaction(self.settings, transaction)
        if (
            not persisted_transaction.accepted
            or persisted_transaction.id != stored_transaction.id
        ):
            # A stale/unversioned fetch must never leave the old local revision
            # looking eligible for the atomic apply below. A fresh canonical
            # event/review is required rather than routing money from it.
            raise ProjectPaymentActionPreflightBlocked(
                "bank_transaction_revision_refresh_was_not_accepted"
            )

    def _refresh_project_from_erpnext(self, project_id: str | None) -> None:
        """Refresh the project from ERPNext immediately before payment routing.

        The local cache is still the transaction/locking boundary, but neither
        automatic routing nor an approved suggestion can rely on a manually
        refreshed status. A current ERP read updates the cache first; if
        ERPNext is unavailable, the action is retried instead of allocating
        against stale status.
        """
        if not project_id:
            # The transactional action boundary produces the durable policy
            # failure for an unscoped action. Do not make an unrelated ERP call.
            return
        external_id = erpnext_project_external_id(
            self.settings,
            project_id=project_id,
        )
        if external_id is None:
            raise ValueError("payment_project_has_no_erpnext_identity")
        client = self._erpnext_client()
        try:
            document = client.get_project(external_id)
        finally:
            client.close()
        payload = erpnext_project_to_input(document)
        if payload is None or payload.external_id != external_id:
            raise ValueError("payment_project_refresh_was_invalid")
        if str(payload.source_status or "").strip().casefold() != "open":
            raise ProjectPaymentActionPreflightBlocked(
                "payment_project_is_not_open_in_erpnext"
            )
        if payload.source_modified_at is None:
            raise ProjectPaymentActionPreflightBlocked(
                "payment_project_has_no_canonical_revision"
            )
        refreshed_project_id = upsert_project(self.settings, payload)
        if refreshed_project_id != project_id:
            raise ValueError("payment_project_identity_changed")

    def _notification_source_block_reason(
        self,
        *,
        notification_id: str,
        lease_token: str,
    ) -> str | None:
        """Return a deterministic reason to suppress a stale notification.

        Allocation-time validation is necessary but not sufficient: a durable
        outbox row may wait while Discord is unavailable or notifications are
        disabled.  Re-read the authoritative ERPNext document immediately
        before the external side effect and require its revision to match the
        immutable allocation evidence.

        Transient ERP/network errors intentionally propagate to the caller so
        the owned outbox lease is released for retry.  A missing, canceled, or
        revised Bank Transaction is a terminal safety failure instead.
        """
        source_context = get_project_payment_notification_source_context(
            self.settings,
            notification_id=notification_id,
            lease_token=lease_token,
        )
        if source_context is None:
            return "payment_notification_source_context_lost"
        if (
            source_context.allocation_status
            is not ProjectPaymentAllocationStatus.CONFIRMED
        ):
            return "payment_allocation_is_not_confirmed"
        if source_context.source != "erpnext":
            return "payment_transaction_source_is_not_erpnext"
        if source_context.allocation_source_revision is None:
            return "payment_allocation_has_no_canonical_source_revision"
        if (
            source_context.current_source_revision
            != source_context.allocation_source_revision
        ):
            return "payment_source_revision_superseded"

        client = self._erpnext_client()
        try:
            try:
                document = client.get_bank_transaction(source_context.external_id)
            except ERPNextAPIError as exc:
                if exc.status_code == 404:
                    return "bank_transaction_not_found_in_erpnext"
                raise
        finally:
            client.close()
        transaction = erpnext_bank_transaction_to_input(document)
        if transaction is None:
            return "bank_transaction_is_no_longer_submitted"
        if transaction.external_id != source_context.external_id:
            return "bank_transaction_identity_changed"

        # Save the latest canonical projection before deciding whether this
        # allocation's immutable evidence is still current. If another sync
        # has already observed a newer revision, the re-read below still wins
        # and suppresses delivery.
        upsert_bank_transaction(self.settings, transaction)
        refreshed_transaction = get_bank_transaction(
            self.settings,
            transaction_id=source_context.bank_transaction_id,
        )
        if refreshed_transaction is None:
            return "payment_transaction_not_found"
        if (
            refreshed_transaction.source != source_context.source
            or refreshed_transaction.external_id != source_context.external_id
            or refreshed_transaction.source_revision
            != source_context.allocation_source_revision
        ):
            return "payment_source_revision_superseded"
        if transaction.source_revision != source_context.allocation_source_revision:
            return "payment_source_revision_superseded"
        return None

    def deliver_notification(self, notification_id: str) -> dict[str, Any]:
        """Request bot delivery of one claimed, current and eligible outbox row."""
        if not bool(
            getattr(self.settings, "project_payment_notifications_enabled", False)
        ):
            return {"status": "disabled", "notification_id": notification_id}
        if block_project_payment_notification_if_ineligible(
            self.settings,
            notification_id=notification_id,
        ):
            return {
                "status": "blocked",
                "notification_id": notification_id,
                "reason": "project_channel_or_allocation_ineligible",
            }

        notification = claim_project_payment_notification(
            self.settings,
            notification_id=notification_id,
        )
        if notification is None:
            if block_project_payment_notification_if_ineligible(
                self.settings,
                notification_id=notification_id,
            ):
                return {
                    "status": "blocked",
                    "notification_id": notification_id,
                    "reason": "project_channel_or_allocation_ineligible",
                }
            return {"status": "not_claimed", "notification_id": notification_id}
        if notification.lease_token is None:
            logger.error(
                "Claimed payment notification has no lease token id=%s",
                notification.id,
            )
            return {"status": "not_claimed", "notification_id": notification.id}

        base_url = str(
            getattr(self.settings, "discord_bot_internal_base_url", "") or ""
        ).strip()
        api_secret = str(getattr(self.settings, "api_shared_secret", "") or "").strip()
        if not base_url or not api_secret:
            mark_project_payment_notification_failed(
                self.settings,
                notification_id=notification.id,
                error="Discord bot internal endpoint is not configured",
                lease_token=notification.lease_token,
            )
            return {
                # Deployment configuration can be repaired after the payment
                # allocation commits. Leave the durable outbox retryable
                # rather than permanently suppressing a valid receipt.
                "status": "failed",
                "notification_id": notification.id,
                "reason": "discord_bot_not_configured",
            }

        try:
            self._refresh_project_from_erpnext(notification.project_id)
        except ProjectPaymentActionPreflightBlocked as exc:
            blocked = mark_project_payment_notification_failed(
                self.settings,
                notification_id=notification.id,
                error=str(exc),
                blocked=True,
                lease_token=notification.lease_token,
            )
            if not blocked:
                return {"status": "not_claimed", "notification_id": notification.id}
            return {
                "status": "blocked",
                "notification_id": notification.id,
                "reason": str(exc),
            }
        except Exception as exc:
            mark_project_payment_notification_failed(
                self.settings,
                notification_id=notification.id,
                error=(
                    "Canonical ERP project notification recheck failed: "
                    f"{type(exc).__name__}: {exc}"
                ),
                lease_token=notification.lease_token,
            )
            raise

        try:
            source_block_reason = self._notification_source_block_reason(
                notification_id=notification.id,
                lease_token=notification.lease_token,
            )
        except Exception as exc:
            mark_project_payment_notification_failed(
                self.settings,
                notification_id=notification.id,
                error=(
                    "Canonical Bank Transaction notification recheck failed: "
                    f"{type(exc).__name__}: {exc}"
                ),
                lease_token=notification.lease_token,
            )
            raise
        if source_block_reason is not None:
            blocked = mark_project_payment_notification_failed(
                self.settings,
                notification_id=notification.id,
                error=source_block_reason,
                blocked=True,
                lease_token=notification.lease_token,
            )
            if not blocked:
                return {"status": "not_claimed", "notification_id": notification.id}
            return {
                "status": "blocked",
                "notification_id": notification.id,
                "reason": source_block_reason,
            }

        client = DiscordBotClient(base_url, api_secret)
        try:
            response = client.post_project_payment_notification(
                notification_id=notification.id,
                lease_token=notification.lease_token,
            )
        except DiscordBotAPIError as exc:
            status_code = client.status_code
            blocked = status_code in {400, 403, 404, 409}
            mark_project_payment_notification_failed(
                self.settings,
                notification_id=notification.id,
                error=str(exc),
                blocked=blocked,
                lease_token=notification.lease_token,
            )
            if blocked:
                return {
                    "status": "blocked",
                    "notification_id": notification.id,
                    "reason": "bot_rejected_delivery",
                }
            raise

        message_id = str(response.get("message_id") or "").strip()
        if not message_id:
            message = "Discord bot response did not include message_id"
            mark_project_payment_notification_failed(
                self.settings,
                notification_id=notification.id,
                error=message,
                lease_token=notification.lease_token,
            )
            raise RuntimeError(message)
        if not mark_project_payment_notification_sent(
            self.settings,
            notification_id=notification.id,
            discord_message_id=message_id,
            lease_token=notification.lease_token,
        ):
            # The bot has a durable receipt. A later outbox retry will ask it
            # for the same notification and converge without a second send.
            return {"status": "not_claimed", "notification_id": notification.id}
        logger.info(
            "Delivered project payment notification id=%s message_id=%s",
            notification.id,
            message_id,
        )
        return {
            "status": "sent",
            "notification_id": notification.id,
            "message_id": message_id,
        }
