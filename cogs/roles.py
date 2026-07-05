"""
cogs/roles.py
─────────────────────────────────────────────────────────────────────────────
Custom decorative role cog.

Channel behaviour
─────────────────
• The bot posts (or re-uses) a single persistent panel message in
  ROLE_CHANNEL_ID containing a short explanation and a "Get my role" button.
• Any user message in that channel is deleted immediately so it stays clean.
• Clicking the button opens a DM wizard — fully concurrent across members.

Wizard
──────
• Human tone, plain text, no embeds, no emojis.
• 120-second timeout per question.
• If the member already has a managed role, acknowledges it and asks if they
  want to change it.
• Asks for: name → colour → hoist (display separately in member list).
• Role is zero-permissions, purely decorative.
"""

import asyncio
import os
import re

import discord
from discord.ext import commands

# ── constants ────────────────────────────────────────────────────────────────

# Invisible Unicode sentinel appended to every managed role name.
# Users never see it; we use it to identify roles this bot created.
ROLE_TAG = "\u2060"  # WORD JOINER — zero-width, invisible in Discord
TIMEOUT  = 120  # seconds per DM question

COLOR_PRESETS: dict[str, int] = {
    "red":    0xFF5555,
    "orange": 0xFFB86C,
    "yellow": 0xF1FA8C,
    "green":  0x50FA7B,
    "cyan":   0x8BE9FD,
    "blue":   0x6272A4,
    "pink":   0xFF79C6,
    "purple": 0xBD93F9,
    "white":  0xF8F8F2,
    "black":  0x21222C,
}

PANEL_TEXT = (
    "**custom roles**\n\n"
    "you can give yourself a role with your own name and color. "
    "click the button below and the bot will message you to set it up."
)

# ── helpers ──────────────────────────────────────────────────────────────────

def _parse_color(raw: str) -> int | None:
    raw = raw.strip().lower()
    if raw in COLOR_PRESETS:
        return COLOR_PRESETS[raw]
    raw = raw.lstrip("#")
    if re.fullmatch(r"[0-9a-f]{6}", raw):
        return int(raw, 16)
    return None


def _find_managed_role(member: discord.Member) -> discord.Role | None:
    for role in member.roles:
        if ROLE_TAG in role.name:
            return role
    return None


def _color_presets_text() -> str:
    return ", ".join(COLOR_PRESETS.keys())


# ── persistent button view ────────────────────────────────────────────────────

class RoleWizardView(discord.ui.View):
    """Persistent view that lives in the role channel panel message."""

    def __init__(self, cog: "Roles"):
        super().__init__(timeout=None)
        self.cog = cog

    @discord.ui.button(
        label="Get my role",
        style=discord.ButtonStyle.secondary,
        custom_id="roles:get_my_role",
    )
    async def get_role_button(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ):
        member: discord.Member = interaction.user  # type: ignore
        guild  = interaction.guild

        # Acknowledge the interaction immediately so Discord doesn't show an error
        await interaction.response.defer(ephemeral=True)

        if guild is None:
            await interaction.followup.send("this only works inside a server.", ephemeral=True)
            return

        if member.id in self.cog._active:
            await interaction.followup.send(
                "you already have a wizard open, check your DMs.", ephemeral=True
            )
            return

        # Start wizard as a background task
        asyncio.create_task(self.cog._wizard(member, guild))


# ── cog ──────────────────────────────────────────────────────────────────────

class Roles(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot   = bot
        self._active: set[int] = set()
        self._view = RoleWizardView(self)

        raw = os.getenv("ROLE_CHANNEL_ID", "")
        self.role_channel_id: int | None = int(raw) if raw.isdigit() else None

    async def cog_load(self):
        # Register the persistent view so button clicks survive restarts.
        # This must happen here (before on_ready) so Discord can route
        # button interactions that arrive before on_ready fires.
        self.bot.add_view(self._view)

    # ── panel management ──────────────────────────────────────────────────────

    async def _ensure_panel(self):
        """Post the explanation panel if it doesn't exist yet; otherwise leave it.
        Must be called after the bot is ready so the channel cache is warm.
        """
        if self.role_channel_id is None:
            return
        channel = self.bot.get_channel(self.role_channel_id)
        if channel is None:
            return

        # Look for an existing panel message from this bot
        async for msg in channel.history(limit=50):
            if msg.author == self.bot.user and msg.components:
                return  # panel already exists, leave it alone

        # Not found — post a fresh one
        await channel.send(content=PANEL_TEXT, view=self._view)

    @commands.Cog.listener()
    async def on_ready(self):
        # Channel cache is warm now — safe to look up the panel channel
        await self._ensure_panel()

    # ── keep role channel clean ───────────────────────────────────────────────

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.author.bot or not message.guild:
            return
        if self.role_channel_id is None or message.channel.id != self.role_channel_id:
            return
        try:
            await message.delete()
        except discord.HTTPException:
            pass

    # ── wizard orchestration ──────────────────────────────────────────────────

    async def _wizard(self, member: discord.Member, guild: discord.Guild):
        self._active.add(member.id)
        try:
            await self._run_wizard(member, guild)
        finally:
            self._active.discard(member.id)

    async def _run_wizard(self, member: discord.Member, guild: discord.Guild):
        # Open DM channel
        try:
            dm = await member.create_dm()
        except discord.Forbidden:
            return

        def dm_check(m: discord.Message) -> bool:
            return m.author.id == member.id and m.channel.id == dm.id

        async def ask(prompt: str) -> str | None:
            """Send a DM prompt and return the stripped reply, or None on timeout/cancel."""
            await dm.send(prompt)
            try:
                reply = await self.bot.wait_for("message", check=dm_check, timeout=TIMEOUT)
            except asyncio.TimeoutError:
                await dm.send("took too long, wizard cancelled.")
                return None
            text = reply.content.strip()
            if text.lower() == "cancel":
                await dm.send("cancelled.")
                return None
            return text

        # ── greet + handle existing role ──────────────────────────────────────
        existing = _find_managed_role(member)

        if existing:
            raw_existing_name = existing.name.replace(ROLE_TAG, "").strip()
            reply = await ask(
                f"hey, you already have a role called \"{raw_existing_name}\". "
                f"do you want to change it? say yes or no."
            )
            if reply is None:
                return
            if reply.lower() not in ("yes", "y"):
                await dm.send("alright, keeping your role as is.")
                return

        else:
            try:
                await dm.send(
                    "hey, let's set up your custom role. "
                    "it won't give you any permissions, it's just for looks. "
                    "type \"cancel\" at any point to stop."
                )
            except discord.Forbidden:
                return

        # ── step 1: name ──────────────────────────────────────────────────────
        name_raw = await ask("what do you want your role to be called?")
        if name_raw is None:
            return
        if not (2 <= len(name_raw) <= 32):
            await dm.send("that name needs to be between 2 and 32 characters. wizard cancelled.")
            return

        # ── step 2: colour ────────────────────────────────────────────────────
        colour_raw = await ask(
            f"what color? you can give me a hex code like #bd93f9, "
            f"or just say one of these: {_color_presets_text()}."
        )
        if colour_raw is None:
            return
        colour_int = _parse_color(colour_raw)
        if colour_int is None:
            await dm.send(
                "couldn't figure out that color. use a hex code or one of the named options. "
                "wizard cancelled."
            )
            return

        # ── step 3: hoist (display separately) ───────────────────────────────
        hoist_reply = await ask(
            "do you want to be listed separately from regular members in the member list? "
            "say yes or no."
        )
        if hoist_reply is None:
            return
        hoisted = hoist_reply.lower() in ("yes", "y")

        # ── apply ─────────────────────────────────────────────────────────────
        full_name  = f"{name_raw}{ROLE_TAG}"  # sentinel is appended, invisible to users
        colour_obj = discord.Colour(colour_int)

        try:
            if existing:
                await existing.edit(
                    name=full_name,
                    colour=colour_obj,
                    hoist=hoisted,
                    reason="custom role update",
                )
                role = existing
            else:
                role = await guild.create_role(
                    name=full_name,
                    colour=colour_obj,
                    hoist=hoisted,
                    permissions=discord.Permissions.none(),
                    reason=f"custom decorative role for {member}",
                )
                # Place just above @everyone
                await guild.edit_role_positions([(role, 1)])
                await member.add_roles(role, reason="custom decorative role")

        except discord.Forbidden:
            await dm.send(
                "i don't have permission to manage roles. "
                "ask an admin to give me the Manage Roles permission."
            )
            return
        except discord.HTTPException as e:
            await dm.send(f"something went wrong: {e}")
            return

        sep_note = " you'll show up separately in the member list." if hoisted else ""
        await dm.send(
            f"done. your role \"{name_raw}\" has been "
            f"{'updated' if existing else 'set up'}."
            f"{sep_note}"
        )

    # ── owner command to manually refresh the panel ───────────────────────────

    @commands.command(name="rolepanel")
    async def rolepanel(self, ctx: commands.Context):
        """(Owner only) Re-post the role channel panel."""
        if ctx.author.id != int(os.getenv("OWNER_ID", "0")):
            return
        if self.role_channel_id is None:
            return await ctx.reply("ROLE_CHANNEL_ID is not set.")
        channel = self.bot.get_channel(self.role_channel_id)
        if channel is None:
            return await ctx.reply("can't find that channel.")
        await channel.send(content=PANEL_TEXT, view=self._view)
        await ctx.reply("panel posted.")


async def setup(bot: commands.Bot):
    await bot.add_cog(Roles(bot))
