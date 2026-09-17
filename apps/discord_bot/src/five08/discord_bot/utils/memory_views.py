"""Owner-only memory controls that use the existing confirmation gateway."""

from __future__ import annotations

from typing import Any

import discord


class MemoryEditModal(discord.ui.Modal, title="Edit private memory"):
    value = discord.ui.TextInput(
        label="Replacement fact", style=discord.TextStyle.paragraph, max_length=2000
    )

    def __init__(self, view: "MemoryFactsView", fact: dict[str, Any]) -> None:
        super().__init__()
        self.memory_view = view
        self.fact = fact
        self.value.default = str((fact.get("value_json") or {}).get("text") or "")[
            :2000
        ]

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await self.memory_view.propose(
            interaction, f"Update memory fact {self.fact['id']} to {self.value.value}"
        )


class MemoryFactsView(discord.ui.View):
    def __init__(
        self,
        *,
        cog: Any,
        requester_id: int,
        context: dict[str, Any],
        facts: list[dict[str, Any]],
    ) -> None:
        super().__init__(timeout=600)
        self.cog = cog
        self.requester_id = requester_id
        self.context = context
        self.facts = facts
        self.page = 0
        self.selected_id: str | None = None
        self._refresh_options()

    def _refresh_options(self) -> None:
        self.selected_id = None
        self.choose.options = [
            discord.SelectOption(
                label=str(fact.get("key") or "Memory")[:100],
                value=str(fact["id"]),
                description=str((fact.get("value_json") or {}).get("text") or "")[:100]
                or None,
            )
            for fact in self.facts[self.page * 5 : self.page * 5 + 5]
        ]
        self.previous.disabled = self.page == 0
        self.next_page.disabled = (self.page + 1) * 5 >= len(self.facts)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id == self.requester_id:
            return True
        await interaction.response.send_message(
            "These memories belong to their requester.", ephemeral=True
        )
        return False

    @discord.ui.select(placeholder="Choose a private memory")
    async def choose(
        self, interaction: discord.Interaction, select: discord.ui.Select
    ) -> None:
        self.selected_id = select.values[0]
        await interaction.response.defer()

    @discord.ui.button(label="Edit", style=discord.ButtonStyle.primary, row=1)
    async def edit(
        self, interaction: discord.Interaction, _button: discord.ui.Button
    ) -> None:
        fact = next(
            (fact for fact in self.facts if fact["id"] == self.selected_id), None
        )
        if fact is None:
            await interaction.response.send_message(
                "Choose a memory first.", ephemeral=True
            )
            return
        await interaction.response.send_modal(MemoryEditModal(self, fact))

    @discord.ui.button(label="Forget", style=discord.ButtonStyle.danger, row=1)
    async def forget(
        self, interaction: discord.Interaction, _button: discord.ui.Button
    ) -> None:
        if self.selected_id is None:
            await interaction.response.send_message(
                "Choose a memory first.", ephemeral=True
            )
            return
        await self.propose(interaction, f"Forget memory fact {self.selected_id}")

    @discord.ui.button(label="Previous", row=2)
    async def previous(
        self, interaction: discord.Interaction, _button: discord.ui.Button
    ) -> None:
        self.page = max(0, self.page - 1)
        await self._render_page(interaction)

    @discord.ui.button(label="Next", row=2)
    async def next_page(
        self, interaction: discord.Interaction, _button: discord.ui.Button
    ) -> None:
        self.page = min((len(self.facts) - 1) // 5, self.page + 1)
        await self._render_page(interaction)

    async def _render_page(self, interaction: discord.Interaction) -> None:
        self._refresh_options()
        lines = self.cog._format_memory_fact_result_lines(
            "Your private memory",
            {"facts": self.facts[self.page * 5 : self.page * 5 + 5]},
        )
        await interaction.response.edit_message(
            content="\n".join(lines)[:1900], view=self
        )

    async def propose(self, interaction: discord.Interaction, request: str) -> None:
        from five08.discord_bot.cogs.agent import AgentConfirmationView

        if interaction.user.id != self.requester_id:
            await interaction.response.send_message(
                "These memories belong to their requester.", ephemeral=True
            )
            return
        await interaction.response.defer(ephemeral=True)
        context = dict(self.context)
        try:
            fresh_roles = await self.cog._guild_role_names(
                guild_id=str(context.get("guild_id") or ""),
                user_id=self.requester_id,
            )
            if fresh_roles is None:
                raise RuntimeError("Discord role refresh unavailable")
            context["roles"] = fresh_roles
            response = await self.cog._post_agent_request(
                message=request, context=context
            )
        except Exception:
            await interaction.followup.send(
                "I couldn't prepare the memory change. Try again.", ephemeral=True
            )
            return
        plan = response.get("plan") or {}
        view = None
        if response.get("status") == "requires_confirmation" and plan.get("plan_id"):
            view = AgentConfirmationView(
                cog=self.cog,
                requester_id=self.requester_id,
                plan_id=plan["plan_id"],
                context=context,
            )
        if view is None:
            await interaction.followup.send(
                self.cog._format_agent_response(response), ephemeral=True
            )
        else:
            await interaction.followup.send(
                self.cog._format_agent_response(response), view=view, ephemeral=True
            )
