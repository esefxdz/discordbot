import discord
from discord.ext import commands
from ..shared.constants import C_RADIO


class _CategoryView(discord.ui.View):
    """Step 1: buttons for each radio category."""

    def __init__(self, bot: commands.Bot, stations: dict, timeout: float = 120.0):
        super().__init__(timeout=timeout)
        self.bot      = bot
        self.stations = stations
        self.message: discord.Message | None = None

        for i, category in enumerate(stations.keys()):
            label = category[:80]  # Discord button label limit
            btn = discord.ui.Button(
                label=label,
                style=discord.ButtonStyle.primary,
                row=i // 5,  # 5 buttons per row max
            )
            btn.callback = self._make_category_callback(category)
            self.add_item(btn)

    def _make_category_callback(self, category: str):
        async def callback(interaction: discord.Interaction):
            station_list = self.stations[category]
            view = _StationSelect(self.bot, category, station_list, interaction, timeout=self.timeout)
            embed = discord.Embed(
                title=f"📻 {category}",
                description="Pick a station from the dropdown below:",
                color=C_RADIO,
            )
            await interaction.response.edit_message(embed=embed, view=view)
            view.message = interaction.message
        return callback

    async def on_timeout(self):
        if self.message:
            try:
                for child in self.children:
                    child.disabled = True
                await self.message.edit(view=self)
            except Exception:
                pass


class _StationSelect(discord.ui.View):
    """Step 2: dropdown to pick a specific station."""

    def __init__(
        self,
        bot: commands.Bot,
        category: str,
        stations: list[dict],
        interaction: discord.Interaction,
        timeout: float = 120.0,
    ):
        super().__init__(timeout=timeout)
        self.bot          = bot
        self.category     = category
        self.stations     = stations
        self._interaction = interaction
        self.message: discord.Message | None = None
        self._picked: dict | None = None

        options = []
        for s in stations:
            label = s["name"][:100]  # Discord select option limit
            desc  = s["desc"][:100] if s["desc"] else "\u200b"  # zero-width space if empty
            options.append(discord.SelectOption(label=label, description=desc, value=s["name"]))

        select = discord.ui.Select(placeholder="Choose a station…", options=options)
        select.callback = self._on_select
        self.add_item(select)

    async def _on_select(self, interaction: discord.Interaction):
        station_name = interaction.data["values"][0]  # type: ignore
        for s in self.stations:
            if s["name"] == station_name:
                self._picked = s
                break

        # Disable the dropdown immediately
        for child in self.children:
            child.disabled = True
        await interaction.response.edit_message(view=self)

        # Delegate playback to the cog
        cog = self.bot.get_cog("Music")
        if cog and self._picked:
            await cog._play_radio_stream(interaction, self.category, self._picked)
        self.stop()

    async def on_timeout(self):
        if self.message:
            try:
                for child in self.children:
                    child.disabled = True
                await self.message.edit(view=self)
            except Exception:
                pass
