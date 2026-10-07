"""Help cog — one place that explains everything the bot can do.

!help            →  overview + a dropdown to open any category
!help <category> →  jump straight to a category, e.g. !help music

The text lives in CATEGORIES below. GIF commands are generated from the
gifs/ folder, so that list is read from the Gifs cog at runtime.
"""
######################################################################
import discord
from discord.ext import commands

COLOR = 0x5BA0D0

INTRO = (
    "Hi, I'm Yuuka! I play music and radio, convert videos, convert money, "
    "run a Blue Archive gacha, chat with AI, and a bunch of small fun stuff.\n\n"
    "Most commands start with `!`. The media tools and `/currency` are "
    "slash commands: type `/` and pick them from the list.\n\n"
    "Pick a category below, or type `!help <category>` (e.g. `!help music`). `!commands` works too."
)

# key: (emoji, title, one-line summary, full page text)
CATEGORIES: dict[str, tuple[str, str, str, str]] = {
    "music": ("🎵", "Music & Radio", "Play songs from YouTube or live radio in voice", (
        "Join a voice channel first.\n\n"
        "**Anyone can use**\n"
        "`!play <song or link>` (`!p`) — search YouTube or paste a video/playlist link. "
        "Example: `!play blue archive ost`\n"
        "`!radio` — pick a radio station from a menu (category, then station)\n"
        "`!nowplaying` (`!np`) — what's playing right now\n"
        "`!queue` (`!q`) — what's coming up next\n"
        "`!history` (`!h`) — recently played songs\n"
        "`!music` — short list of the music commands\n\n"
        "**Needs the DJ role**\n"
        "`!pause` · `!resume` · `!skip`\n"
        "`!stop` (`!leave`, `!disconnect`) — stop music or radio, clear the queue and leave\n"
        "`!seek <time>` — jump to a time, e.g. `!seek 1:30` or `!seek 90`\n"
        "`!looptrack` — repeat the current song\n"
        "`!loopqueue` — repeat the whole queue\n"
        "`!autoplay` — keep playing similar songs when the queue runs out\n"
        "`!remove <pos>` — remove a song from the queue\n"
        "`!move <from> <to>` — move a song in the queue\n"
        "`!shuffle` — shuffle the queue\n"
        "`!clear` — empty the queue"
    )),
    "media": ("🎬", "Media tools", "Convert, reverse, speed up or caption videos", (
        "Slash commands: type `/`, pick one, and upload your file in the `file` box.\n\n"
        "`/togif` — turn a video into a GIF\n"
        "`/tomp4` — convert any video/audio to MP4\n"
        "`/tomp3` — pull the audio out as MP3\n"
        "`/toopus` — pull the audio out as Opus\n"
        "`/reverse` — play a clip backwards\n"
        "`/speed` — speed up or slow down (`factor` 0.5 to 2.0)\n"
        "`/caption` — put text on top of a video\n\n"
        "Limits: files up to 50 MB, output up to 10 minutes."
    )),
    "utilities": ("🧰", "Utilities", "Currency, weather, timestamps, calendar, bucketlist", (
        "`/currency` — convert money, e.g. from `usd` to `try`, amount `100`. "
        "Understands names like `dollar`, `euro`, `lira`\n"
        "`!weather [city]` — weather forecast (defaults to Kayseri)\n"
        "`!time` — opens a form (day, time, country) and gives you a Discord timestamp "
        "that shows up in everyone's own time zone\n"
        "`!strinova` — how many people are playing Strinova on Steam right now\n\n"
        "**Calendar** (members only) — events show on https://esefos.netlify.app/calendar\n"
        "`!book` — book an event with a form\n"
        "`!unbook` — remove an upcoming event\n\n"
        "**Bucketlist** (approved users only) — shows on https://esefos.netlify.app/bucketlist\n"
        "`!bucket` — add an item with a form\n"
        "`!unbucket` — remove an item\n"
        "`!bucketlist` — list the bucketlist commands"
    )),
    "fun": ("🎲", "Fun & games", "Dice, coin, 8ball, slots, ship and more", (
        "`!coin` — flip a coin\n"
        "`!dice` — roll a 6-sided die\n"
        "`!d4` · `!d8` · `!d10` · `!d12` · `!d20` · `!d100` — roll that die\n"
        "`!roll <dice>` — custom roll: `!roll 2d6`, `!roll d20` or `!roll 20`\n"
        "`!rps <rock|paper|scissors>` — rock paper scissors\n"
        "`!8ball <question>` — ask the magic 8 ball\n"
        "`!slots` — spin the slot machine\n"
        "`!roulette` — russian roulette (1 in 6)\n"
        "`!ship <@user> [@user]` — love meter\n"
        "`!pp` · `!rank` · `!aura` — very scientific measurements\n"
        "`!yuuka` — kanpeki!\n"
        "`!koharu` — shikei!\n"
        "`!fun` — short list of these commands"
    )),
    "memes": ("📜", "Copypastas & GIFs", "Copypastas and reaction GIFs", (
        "**Copypastas**\n"
        "`!yuukapasta` — the Yuuka devotion pasta\n"
        "`!yuyuko` — Yuyuko ASCII art\n"
        "`!anlaki` — bro anlaki\n"
        "`!copypasta` — list all copypastas\n\n"
        "**Reaction GIFs**\n"
        "{gifs}\n"
        "`!gifs` — list all GIF commands"
    )),
    "bluearchive": ("🌸", "Blue Archive gacha", "Pull students, spark, show off your collection", (
        "**Banners**\n"
        "`!gacha` (`!banners`, `!g`) — current banners\n"
        "`!gacha pick <n>` — choose banner #n (`!gacha pick regular` for the permanent pool)\n"
        "`!gacha info` — your active banner, spark points and collection summary\n"
        "`!gacha help` — gacha command list\n"
        "`!gacha health` — check that everything a pull needs is working\n\n"
        "**Pulling**\n"
        "`!pull` — 10-pull (2★ or better guaranteed on the 10th)\n"
        "`!pull single` — 1 pull\n"
        "`!spark <name>` — claim a rate-up student with 200 recruitment points\n\n"
        "**Collection**\n"
        "`!inv` (`!collection`, `!i`) — browse your collection with buttons\n"
        "`!inv @user` — look at someone else's\n"
        "`!eligma` — your Eligma balance"
    )),
    "ai": ("🤖", "AI chat", "Talk to Yuuka (approved users only)", (
        "**Ping me** (`@Yuuka ...`) to talk. I remember the last 15 messages in the channel.\n\n"
        "`!activechat` — talk without pinging in this channel\n"
        "`!stopchat` — turn that off again\n"
        "`!setpersona <name>` — switch personality (wipes memory)\n"
        "`!personas` — list personalities\n"
        "`!clearchat` — wipe my memory of this channel\n"
        "`!summary` — see my long-term summary of this channel\n"
        "`!clearsummary` — wipe that summary\n"
        "`!think` — thinking mode menu (off / high / max)\n"
        "`!thinkstatus` — is thinking on here?\n"
        "`!turbo` — switch between the fast and the smart model\n"
        "`!current` — which model I'm using\n"
        "`!temperature <0-2>` — lower = stricter, higher = more chaotic\n"
        "`!aicost` — tokens used and cost so far\n"
        "`!ai` — list the AI commands"
    )),
    "stats": ("🖥️", "PC stats", "Live stats of the machine I run on", (
        "`!stats` — CPU, RAM and disk at a glance\n"
        "`!fetch` — full system info (fastfetch)\n"
        "`!cpu` · `!mem` · `!load` — CPU usage, memory, load average\n"
        "`!temps` — temperature sensors\n"
        "`!gpu` — current graphics mode\n"
        "`!top` — top 5 processes by CPU\n"
        "`!processes` — how many processes are running\n"
        "`!net` — network traffic since boot\n"
        "`!uptime` — how long the PC has been on\n"
        "`!battery` — battery level\n"
        "`!sysinfo` — list these commands"
    )),
    "automatic": ("⚙️", "Automatic stuff", "Things I do without a command", (
        "**Telegram bridge** — messages in linked channels are mirrored to Telegram groups "
        "and back, including replies, edits, stickers and media. "
        "`!bridge` shows its status.\n\n"
        "**\"my time\"** — when certain friends write *my time* next to a time, I reply "
        "with a timestamp that shows in everyone's own time zone.\n\n"
        "**Strinova player count** — a channel name that shows how many people are "
        "playing Strinova, updated every 10 minutes.\n\n"
        "**Member count** — a channel name that shows how many members the server has, "
        "updated every 10 minutes.\n\n"
        "**Welcome & goodbye cards** — I post a card when someone joins the server, "
        "and a black & white one when someone leaves.\n\n"
        "**Custom roles** — in the roles channel, press *Get my role* and I'll DM you "
        "to make your own colored role.\n\n"
        "**Website** — the PC's live stats are shown on https://esefos.netlify.app"
    )),
    "admin": ("🔒", "Admin", "Owner-only commands", (
        "These only work for the bot owner (`!ferox` only for one specific member).\n\n"
        "`!purge <n>` — delete the last n messages\n"
        "`!ban @users/@roles [reason]` — ban everyone mentioned (asks to confirm)\n"
        "`!ferox <10m|2h|1d|1w|off>` — time out ferox\n"
        "`!rolepanel` — re-post the custom role panel\n"
        "`!gitpull` — pull the latest code\n"
        "`!update` — pull the latest code and restart if anything changed\n"
        "`!restart` — restart the bot\n"
        "`!status` — bot service status\n"
        "`!logs [n]` — last n log lines (default 40)\n"
        "`!gitlog [n]` — last n commits (default 5)"
    )),
}

ALIASES = {
    "radio": "music", "ffmpeg": "media", "video": "media", "currency": "utilities",
    "util": "utilities", "games": "fun", "gifs": "memes", "copypasta": "memes",
    "gacha": "bluearchive", "ba": "bluearchive", "sysinfo": "stats", "owner": "admin",
}


class HelpSelect(discord.ui.Select):
    def __init__(self, cog: "Help", author_id: int) -> None:
        self._cog = cog
        self._author_id = author_id
        super().__init__(
            placeholder="Pick a category…",
            options=[
                discord.SelectOption(label=title, value=key, emoji=emoji, description=summary[:100])
                for key, (emoji, title, summary, _) in CATEGORIES.items()
            ],
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        embed = self._cog.page(self.values[0])
        # someone else clicking gets their own private copy instead of flipping ours
        if interaction.user.id != self._author_id:
            return await interaction.response.send_message(embed=embed, ephemeral=True)
        await interaction.response.edit_message(embed=embed)


class HelpView(discord.ui.View):
    def __init__(self, cog: "Help", author_id: int) -> None:
        super().__init__(timeout=300)
        self.add_item(HelpSelect(cog, author_id))


class Help(commands.Cog):
    """The !help command."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    def overview(self) -> discord.Embed:
        embed = discord.Embed(title="What can Yuuka do?", description=INTRO, color=COLOR)
        for emoji, title, summary, _ in CATEGORIES.values():
            embed.add_field(name=f"{emoji} {title}", value=summary, inline=True)
        return embed

    def page(self, key: str) -> discord.Embed:
        emoji, title, _, text = CATEGORIES[key]
        if key == "memes":
            gifs_cog = self.bot.get_cog("Gifs")
            names = sorted(getattr(gifs_cog, "_gif_names", []))
            text = text.replace("{gifs}", " · ".join(f"`!{n}`" for n in names) or "none loaded")
        embed = discord.Embed(title=f"{emoji} {title}", description=text, color=COLOR)
        embed.set_footer(text="!help — back to the overview")
        return embed

    @commands.command(name="help", aliases=["commands"])
    async def help_cmd(self, ctx: commands.Context, category: str | None = None) -> None:
        """Show everything the bot can do."""
        if category:
            key = category.lower().replace(" ", "")
            key = ALIASES.get(key, key)
            if key in CATEGORIES:
                return await ctx.reply(embed=self.page(key), view=HelpView(self, ctx.author.id))
            await ctx.reply(f"No category called `{category}`, here's everything:")
        await ctx.reply(embed=self.overview(), view=HelpView(self, ctx.author.id))


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Help(bot))
