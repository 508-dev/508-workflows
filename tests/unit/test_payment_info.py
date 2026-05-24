from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, Mock, patch

import pytest

from five08.clients.erpnext import ERPNextAPIError
from five08.discord_bot.cogs import payment_info as payment_info_cog
from five08.discord_bot.cogs.payment_info import PaymentInfoCog
from five08.payment_info import (
    PaymentInfoError,
    PaymentInfoInput,
    get_supplier_payment_details,
    mask_payment_details_for_display,
    payment_info_summary,
    resolve_payment_identity,
    update_supplier_payment_details,
)


class FakeERPNextClient:
    def __init__(self) -> None:
        self.records: dict[str, dict[str, dict[str, Any]]] = {
            "User": {
                "jane@508.dev": {
                    "name": "jane@508.dev",
                    "email": "jane@508.dev",
                }
            },
            "Employee": {
                "HR-EMP-0001": {
                    "name": "HR-EMP-0001",
                    "user_id": "jane@508.dev",
                    "supplier": "SUP-0001",
                }
            },
            "Supplier": {
                "SUP-0001": {
                    "name": "SUP-0001",
                    "supplier_name": "Jane Engineer",
                    "email_id": "",
                    "portal_users": [],
                    "supplier_details": "",
                }
            },
        }
        self.closed = False

    def close(self) -> None:
        self.closed = True

    def get_record(self, doctype: str, record_id: str) -> dict[str, Any]:
        try:
            return dict(self.records[doctype][record_id])
        except KeyError as exc:
            raise ERPNextAPIError("not found", status_code=404) from exc

    def list_records(
        self,
        doctype: str,
        *,
        fields: list[str],
        filters: list[Any] | None = None,
        or_filters: list[Any] | None = None,
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        rows = list(self.records.get(doctype, {}).values())
        for raw_filter in filters or []:
            _doctype, field, operator, value = raw_filter
            if operator == "=":
                rows = [row for row in rows if row.get(field) == value]
        return [dict(row) for row in rows[:limit]]

    def search_suppliers(self, query: str, *, limit: int = 10) -> list[dict[str, Any]]:
        normalized = query.strip().casefold()
        rows = []
        for supplier in self.records["Supplier"].values():
            if (
                supplier.get("name", "").casefold() == normalized
                or supplier.get("email_id", "").casefold() == normalized
            ):
                rows.append(dict(supplier))
        return rows[:limit]

    def update_record(
        self,
        doctype: str,
        record_id: str,
        fields: dict[str, Any],
    ) -> dict[str, Any]:
        self.records[doctype][record_id].update(fields)
        return dict(self.records[doctype][record_id])


def test_resolve_payment_identity_uses_discord_linked_508_email_only() -> None:
    client = FakeERPNextClient()

    identity = resolve_payment_identity(client, " Jane@508.dev ")

    assert identity.email == "jane@508.dev"
    assert identity.user_id == "jane@508.dev"
    assert identity.employee_id == "HR-EMP-0001"
    assert identity.supplier_id == "SUP-0001"


def test_resolve_payment_identity_rejects_non_508_email() -> None:
    client = FakeERPNextClient()

    with pytest.raises(PaymentInfoError, match="@508.dev"):
        resolve_payment_identity(client, "jane@example.com")


def test_get_supplier_payment_details_reads_supplier_details_field() -> None:
    client = FakeERPNextClient()
    identity = resolve_payment_identity(client, "jane@508.dev")
    client.records["Supplier"]["SUP-0001"]["supplier_details"] = "Bank: Test Bank"

    supplier = get_supplier_payment_details(client, identity)

    assert supplier["name"] == "SUP-0001"
    assert supplier["supplier_details"] == "Bank: Test Bank"


def test_update_supplier_payment_details_requires_supplier_details() -> None:
    client = FakeERPNextClient()
    identity = resolve_payment_identity(client, "jane@508.dev")

    with pytest.raises(PaymentInfoError, match="Supplier Details"):
        update_supplier_payment_details(
            client,
            identity,
            PaymentInfoInput(),
        )


def test_update_supplier_payment_details_replaces_supplier_details() -> None:
    client = FakeERPNextClient()
    identity = resolve_payment_identity(client, "jane@508.dev")
    client.records["Supplier"]["SUP-0001"]["supplier_details"] = "Tax ID: 12-3456789"

    supplier, changed_fields = update_supplier_payment_details(
        client,
        identity,
        PaymentInfoInput(
            supplier_details="\n".join(
                [
                    "Bank: Test Bank",
                    "Routing / SWIFT / branch: 011000015",
                    "Account number: 123456789",
                ]
            )
        ),
    )

    assert supplier["supplier_details"] == (
        "Bank: Test Bank\n"
        "Routing / SWIFT / branch: 011000015\n"
        "Account number: 123456789"
    )
    assert changed_fields == ["supplier_details"]


def test_payment_info_summary_masks_account_numbers_but_not_bank_or_routing() -> None:
    client = FakeERPNextClient()
    identity = resolve_payment_identity(client, "jane@508.dev")
    supplier = {
        "supplier_details": "\n".join(
            [
                "Account holder: Jane Engineer",
                "Bank: Test Bank",
                "Routing / SWIFT / branch: 011000015",
                "Account number: 1234567890",
                "IBAN: GB82WEST12345698765432",
            ]
        )
    }

    lines = payment_info_summary(identity, supplier)

    summary = "\n".join(lines)
    assert "Bank: Test Bank" in summary
    assert "Routing / SWIFT / branch: 011000015" in summary
    assert "1234567890" not in summary
    assert "GB82WEST12345698765432" not in summary
    assert "12****90" in summary
    assert "GB****32" in summary


def test_payment_info_summary_sanitizes_nested_code_fences() -> None:
    client = FakeERPNextClient()
    identity = resolve_payment_identity(client, "jane@508.dev")
    supplier = {
        "supplier_details": "\n".join(
            [
                "Bank: Test Bank",
                "```",
                "@everyone",
                "Account number: 1234567890",
            ]
        )
    }

    summary = "\n".join(payment_info_summary(identity, supplier))

    assert summary.count("```") == 2
    assert "'''" in summary
    assert "1234567890" not in summary
    assert "12****90" in summary


def test_mask_payment_details_for_display_leaves_routing_numbers_readable() -> None:
    details = "\n".join(
        [
            "Bank: Test Bank",
            "Routing / SWIFT / branch: 011000015",
            "Account number: 1234567890",
        ]
    )

    masked = mask_payment_details_for_display(details)

    assert "Routing / SWIFT / branch: 011000015" in masked
    assert "Account number: 12****90" in masked


def test_mask_payment_details_handles_account_lines_without_colons() -> None:
    details = "\n".join(
        [
            "Bank Test Bank",
            "ACH Account 1234567890",
            "Account # 9876543210",
        ]
    )

    masked = mask_payment_details_for_display(details)

    assert "Bank Test Bank" in masked
    assert "1234567890" not in masked
    assert "9876543210" not in masked
    assert "12****90" in masked
    assert "98****10" in masked


def test_mask_payment_details_does_not_mask_account_holder() -> None:
    details = "\n".join(
        [
            "Account holder: Jane Engineer",
            "Account number: 1234567890",
        ]
    )

    masked = mask_payment_details_for_display(details)

    assert "Account holder: Jane Engineer" in masked
    assert "Account number: 12****90" in masked


def _make_interaction(user_id: int = 123456789) -> AsyncMock:
    interaction = AsyncMock()
    interaction.response = AsyncMock()
    interaction.response.defer = AsyncMock()
    interaction.followup = AsyncMock()
    interaction.followup.send = AsyncMock()
    interaction.user = Mock()
    interaction.user.id = user_id
    return interaction


@pytest.fixture
def fake_erp_client() -> FakeERPNextClient:
    return FakeERPNextClient()


@pytest.fixture
def fake_crm_client() -> Mock:
    crm = Mock()
    crm.list_contacts.return_value = {
        "list": [
            {
                "id": "contact-1",
                "name": "Jane Engineer",
                "c508Email": "jane@508.dev",
                "cDiscordUserID": "123456789",
            }
        ]
    }
    return crm


@pytest.fixture
def payment_cog(
    fake_crm_client: Mock,
    fake_erp_client: FakeERPNextClient,
) -> PaymentInfoCog:
    with (
        patch(
            "five08.discord_bot.cogs.payment_info.EspoClient",
            return_value=fake_crm_client,
        ),
        patch.object(PaymentInfoCog, "_init_audit_logger", return_value=None),
    ):
        cog = PaymentInfoCog(Mock())
    cog.audit_logger = Mock()
    cog._erpnext_client = Mock(return_value=fake_erp_client)  # type: ignore[method-assign]
    return cog


@pytest.mark.asyncio
async def test_payment_info_command_is_ephemeral_and_self_scoped(
    payment_cog: PaymentInfoCog,
    fake_crm_client: Mock,
) -> None:
    interaction = _make_interaction()

    await payment_cog.payment_info_command.callback(payment_cog, interaction)

    fake_crm_client.list_contacts.assert_called_once()
    params = fake_crm_client.list_contacts.call_args.args[0]
    assert params["where"][0]["value"] == "123456789"
    assert params["select"] == "id,name,emailAddress,c508Email,cDiscordUserID"
    assert interaction.response.defer.call_args.kwargs["ephemeral"] is True
    assert interaction.followup.send.call_args.kwargs["ephemeral"] is True
    assert "allowed_mentions" in interaction.followup.send.call_args.kwargs
    assert "jane@508.dev" in interaction.followup.send.call_args.args[0]


@pytest.mark.asyncio
async def test_payment_info_command_denies_when_crm_email_is_not_508(
    payment_cog: PaymentInfoCog,
    fake_crm_client: Mock,
) -> None:
    fake_crm_client.list_contacts.return_value["list"][0]["c508Email"] = (
        "jane@example.com"
    )
    interaction = _make_interaction()

    await payment_cog.payment_info_command.callback(payment_cog, interaction)

    sent = interaction.followup.send.call_args.args[0]
    assert "@508.dev" in sent
    assert interaction.followup.send.call_args.kwargs["ephemeral"] is True


@pytest.mark.asyncio
async def test_payment_info_update_is_ephemeral_and_does_not_echo_full_account(
    payment_cog: PaymentInfoCog,
) -> None:
    interaction = _make_interaction()

    await payment_cog.handle_payment_info_update(
        interaction,
        PaymentInfoInput(
            supplier_details="\n".join(
                [
                    "Bank: Test Bank",
                    "Routing / SWIFT / branch: 011000015",
                    "ACH Account 123456789",
                ]
            ),
        ),
    )

    sent = interaction.followup.send.call_args.args[0]
    assert "Payment info updated" in sent
    assert "123456789" not in sent
    assert "Routing / SWIFT / branch: 011000015" in sent
    assert "ACH Account 12****89" in sent
    assert "allowed_mentions" in interaction.followup.send.call_args.kwargs
    assert interaction.followup.send.call_args.kwargs["ephemeral"] is True


@pytest.mark.asyncio
async def test_setup_rejects_whitespace_only_config() -> None:
    bot = Mock()
    bot.add_cog = AsyncMock()
    fake_settings = Mock(
        espo_base_url="https://crm.test",
        espo_api_key="   ",
        erpnext_base_url="https://erp.test",
        erpnext_api_key="key:secret",
    )

    with patch.object(payment_info_cog, "settings", fake_settings):
        await payment_info_cog.setup(bot)

    bot.add_cog.assert_not_called()
