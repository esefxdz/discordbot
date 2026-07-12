import asyncio
import wavelink
import discord
import logging
from discord.ext import commands
from ..shared.constants import (
    C_RADIO, C_ERR, C_WARN,
    LOOP_OFF, MODE_MUSIC, MODE_RADIO,
)
from .views import _CategoryView
from .icy import IcyMetadataPoller

log = logging.getLogger(__name__)


class RadioCommands:
    """Mixin: radio browsing command, stream playback, and drop notifications."""

    # ── !radio ────────────────────────────────────────────────────────────────
    @commands.command()
    async def radio(self, ctx: commands.Context):
        """Browse and play radio stations with a two-step interactive picker."""
        if not ctx.author.voice:
            return await ctx.reply(embed=discord.Embed(
                description="❌ Join a voice channel first.", color=C_ERR))

        st = self.state(ctx.guild.id)

        # Mode check: refuse if music is active
        if st.mode == MODE_MUSIC and (st.current or st.queue):
            return await ctx.reply(embed=discord.Embed(
                description="🎵 Music is currently playing. Use `!stop` first to switch to radio.",
                color=C_WARN))

        # Step 1: show category buttons
        view  = _CategoryView(self.bot, self._radio_stations)
        embed = discord.Embed(
            title="📻  Radio — Choose a Category",
            description="Select a genre to browse stations:",
            color=C_RADIO,
        )
        await ctx.reply(embed=embed, view=view)

    # ── radio playback helper (called from _StationSelect callback) ──────────
    async def _play_radio_stream(
        self,
        interaction: discord.Interaction,
        category: str,
        station: dict,
    ):
        """Connect, play a radio stream, and set mode. Called from the station picker UI."""
        guild = interaction.guild
        if guild is None:
            return

        member = guild.get_member(interaction.user.id)
        if member is None or member.voice is None:
            await interaction.followup.send(
                embed=discord.Embed(description="❌ You must be in a voice channel.", color=C_ERR),
                ephemeral=True,
            )
            return

        st = self.state(guild.id)

        async with self._lock(guild.id):
            # Re-check mode — music may have started between !radio and now
            if st.mode == MODE_MUSIC:
                await interaction.followup.send(
                    embed=discord.Embed(
                        description="🎵 Music is currently playing. Use `!stop` first to switch to radio.",
                        color=C_WARN,
                    ),
                    ephemeral=True,
                )
                return

            # Connect or reuse existing player
            vc = guild.voice_client
            if not isinstance(vc, wavelink.Player):
                try:
                    vc = await member.voice.channel.connect(cls=wavelink.Player)
                except Exception as e:
                    await interaction.followup.send(
                        embed=discord.Embed(description=f"❌ Failed to connect: {e}", color=C_ERR),
                        ephemeral=True,
                    )
                    return
                vc.autoplay        = wavelink.AutoPlayMode.disabled
                vc.inactive_timeout = None
                self._voice_channels[guild.id] = member.voice.channel.id

            # Clear mode before skipping so the old track_end handler
            # doesn't interfere with the new station's state
            if vc.playing or vc.paused:
                st.mode    = None
                st.current = None
                await vc.skip()

            # Resolve and play the stream URL via Lavalink
            try:
                results = await wavelink.Playable.search(station["url"])
                if not results:
                    await interaction.followup.send(
                        embed=discord.Embed(
                            description=(
                                f"❌ Could not resolve stream: `{station['url']}`\n"
                                "Lavalink may need the **LavaSrc** plugin for direct HTTP/Icecast URLs."
                            ),
                            color=C_ERR,
                        ),
                        ephemeral=True,
                    )
                    return
                track = results[0]
            except Exception as e:
                await interaction.followup.send(
                    embed=discord.Embed(description=f"❌ Failed to load stream: {e}", color=C_ERR),
                    ephemeral=True,
                )
                return

            # Set radio state *before* play so on_wavelink_track_start
            # sees MODE_RADIO and doesn't overwrite our status text
            st.queue.clear()
            st.current  = track
            st.loop     = LOOP_OFF
            st.autoplay = False
            st.mode     = MODE_RADIO
            self._radio_channels[guild.id] = interaction.channel_id

            try:
                await vc.play(track)
            except Exception as e:
                st.mode = None
                self._radio_channels.pop(guild.id, None)
                await interaction.followup.send(
                    embed=discord.Embed(description=f"❌ Failed to play stream: {e}", color=C_ERR),
                    ephemeral=True,
                )
                return

            # Set channel status with the JSON station name
            await self._set_channel_status(vc, title=station["name"])

            # Send now-playing embed
            embed = discord.Embed(
                title="📻  Now Playing",
                description=f"### {station['name']}",
                color=C_RADIO,
            )
            if station["desc"]:
                embed.add_field(name="Description", value=station["desc"], inline=False)
            embed.add_field(name="Category", value=category,      inline=True)
            embed.add_field(name="Source",   value=station["url"], inline=True)
            embed.set_footer(text="yuuka radio · use !stop to disconnect")
            await interaction.followup.send(embed=embed)

            # Spawn ICY metadata poller if this station supports it
            if station["url"] in self._icy_urls:
                log.info("Starting ICY poller for %s", station["name"])
                self._cancel_icy_poller(guild.id)
                poller = IcyMetadataPoller(
                    station["url"],
                    on_title=lambda title: self._send_icy_now_playing(
                        guild.id, station["name"], title
                    ),
                )
                self._icy_tasks[guild.id] = asyncio.create_task(poller.run())
            else:
                log.info("No ICY support for %s (%s not in set of %d URLs)",
                         station["name"], station["url"], len(self._icy_urls))

    # ── ICY now-playing embed ─────────────────────────────────────────────────
    async def _send_icy_now_playing(self, guild_id: int, station_name: str, title: str) -> None:
        """Post a compact 'Now Playing' embed when an ICY station updates its track."""
        channel_id = self._radio_channels.get(guild_id)
        if channel_id is None:
            return
        guild = self.bot.get_guild(guild_id)
        if guild is None:
            return
        channel = guild.get_channel_or_thread(channel_id)
        if channel is None:
            return
        try:
            embed = discord.Embed(
                description=f"🎵 **{title}**\n-# now on {station_name}",
                color=C_RADIO,
            )
            await channel.send(embed=embed)
        except Exception:
            log.debug(f"Failed to send ICY now-playing in guild {guild_id}", exc_info=True)

    # ── radio drop notification ───────────────────────────────────────────────
    async def _notify_radio_drop(self, guild_id: int):
        """Send a notification to the channel where radio was started."""
        self._cancel_icy_poller(guild_id)
        channel_id = self._radio_channels.pop(guild_id, None)
        if channel_id is None:
            return
        guild = self.bot.get_guild(guild_id)
        if guild is None:
            return
        channel = guild.get_channel_or_thread(channel_id)
        if channel is None:
            return
        try:
            await channel.send(embed=discord.Embed(
                description="📻 Radio stream ended unexpectedly. Use `!radio` to restart.",
                color=C_WARN,
            ))
        except Exception:
            log.warning(f"Failed to send radio-drop notification in guild {guild_id}", exc_info=True)
