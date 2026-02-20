"""
Example Cog Template

This is a template for creating new bot functionality. Copy this file and modify
it to create new cogs for the 508.dev Discord bot.
"""

from discord.ext import commands


class ExampleCog(commands.Cog):
    """Example cog showing basic structure and common patterns."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    # @app_commands.command(name="hello", description="Say hello and get a warm welcome!")
    # async def hello_command(self, interaction: discord.Interaction) -> None:
    #     """Simple hello command example."""
    #     await interaction.response.send_message(
    #         f"Hello {interaction.user.mention}! Welcome to 508.dev!"
    #     )


async def setup(bot: commands.Bot) -> None:
    """Required function to load the cog."""
    cog = ExampleCog(bot)
    await bot.add_cog(cog)
