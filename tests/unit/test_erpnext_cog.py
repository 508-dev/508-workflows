"""Unit tests for ERPNext Discord cog."""

from unittest.mock import AsyncMock, Mock, patch

import pytest

from five08.discord_bot.cogs.erpnext import ErpNextCog
from five08.clients.erpnext import ERPNextAPIError


VALID_INVOICE = {
    "name": "TEST-SINV-0001",
    "docstatus": 0,
    "owner": "test-user@example.com",
    "project": "TEST-PROJ-001",
    "cost_center": "Projects - TEST",
    "posting_date": "2026-01-01",
    "due_date": "2026-02-01",
    "items": [{"idx": 1, "project": "TEST-PROJ-001", "cost_center": "Projects - TEST"}],
}


@pytest.fixture
def mock_interaction() -> AsyncMock:
    interaction = AsyncMock()
    interaction.response = AsyncMock()
    interaction.response.defer = AsyncMock()
    interaction.followup = AsyncMock()
    interaction.followup.send = AsyncMock()
    interaction.namespace = Mock()
    interaction.namespace.doctype = "Sales Invoice"
    return interaction


@pytest.fixture
def mock_doctype() -> Mock:
    choice = Mock()
    choice.value = "Sales Invoice"
    return choice


@pytest.fixture
def cog() -> ErpNextCog:
    with patch("five08.discord_bot.cogs.erpnext.ERPNextClient"):
        return ErpNextCog(Mock())


# ---------------------------------------------------------------------------
# validate_invoice_command tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_validate_invoice_passes(cog, mock_interaction, mock_doctype):
    cog.client.get_invoice = Mock(return_value=VALID_INVOICE)
    await cog.validate_invoice_command.callback(
        cog, mock_interaction, mock_doctype, "TEST-SINV-0001"
    )
    embed = mock_interaction.followup.send.call_args.kwargs["embed"]
    assert "No issues found" in embed.title


@pytest.mark.asyncio
async def test_validate_invoice_shows_invoice_info_field(
    cog, mock_interaction, mock_doctype
):
    cog.client.get_invoice = Mock(return_value=VALID_INVOICE)
    await cog.validate_invoice_command.callback(
        cog, mock_interaction, mock_doctype, "TEST-SINV-0001"
    )
    embed = mock_interaction.followup.send.call_args.kwargs["embed"]
    field_names = [f.name for f in embed.fields]
    assert "Invoice Info" in field_names


@pytest.mark.asyncio
async def test_validate_invoice_not_found(cog, mock_interaction, mock_doctype):
    cog.client.get_invoice = Mock(return_value=None)
    await cog.validate_invoice_command.callback(
        cog, mock_interaction, mock_doctype, "DOES-NOT-EXIST"
    )
    sent = mock_interaction.followup.send.call_args.args[0]
    assert "not found" in sent


@pytest.mark.asyncio
async def test_validate_invoice_api_error(cog, mock_interaction, mock_doctype):
    cog.client.get_invoice = Mock(side_effect=ERPNextAPIError("connection failed"))
    await cog.validate_invoice_command.callback(
        cog, mock_interaction, mock_doctype, "TEST-SINV-0001"
    )
    sent = mock_interaction.followup.send.call_args.args[0]
    assert "Failed to fetch" in sent


@pytest.mark.asyncio
async def test_validate_invoice_issues_truncated_under_discord_limit(
    cog, mock_interaction, mock_doctype
):
    cog.client.get_invoice = Mock(return_value=VALID_INVOICE)
    with patch("five08.discord_bot.cogs.erpnext.validate_invoice") as mock_validate:
        result = Mock()
        result.passed = False
        result.issues = [Mock(message="x" * 2000)]
        mock_validate.return_value = result
        await cog.validate_invoice_command.callback(
            cog, mock_interaction, mock_doctype, "TEST-SINV-0001"
        )
    embed = mock_interaction.followup.send.call_args.kwargs["embed"]
    issues_field = next(f for f in embed.fields if f.name == "Issues")
    assert len(issues_field.value) <= 1024


# ---------------------------------------------------------------------------
# invoice_name_autocomplete tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_autocomplete_returns_choices(cog, mock_interaction):
    cog.client.search_invoices = Mock(
        return_value=[
            {
                "name": "TEST-SINV-0001",
                "docstatus": 0,
                "owner": "test-user@example.com",
                "posting_date": "2026-01-01",
            },
        ]
    )
    choices = await cog.invoice_name_autocomplete(mock_interaction, "TEST-SINV")
    assert len(choices) == 1
    assert choices[0].value == "TEST-SINV-0001"


@pytest.mark.asyncio
async def test_autocomplete_returns_empty_on_api_error(cog, mock_interaction):
    cog.client.search_invoices = Mock(side_effect=ERPNextAPIError("timeout"))
    choices = await cog.invoice_name_autocomplete(mock_interaction, "TEST-SINV")
    assert choices == []


@pytest.mark.asyncio
async def test_autocomplete_returns_empty_when_no_doctype(cog, mock_interaction):
    mock_interaction.namespace.doctype = None
    choices = await cog.invoice_name_autocomplete(mock_interaction, "")
    assert choices == []
