"""
cogs/welcome.py
─────────────────────────────────────────────────────────────────────────────
Welcome / goodbye cards.

• When someone joins, posts an "Aurora" card in WELCOME_CHANNEL_ID: their
  avatar blurred into the background, the avatar itself in a ring on the left,
  and their name + join date beside it. The new member is pinged.
• When someone leaves, posts the same card in black & white with how long
  they were here. No ping.
• Bots are skipped, and only joins/leaves of the channel's own server count.
• Fonts: Inter (SIL OFL), bundled in welcome_asset/fonts.
"""

import asyncio
import io
import logging
import os
from datetime import datetime, timezone
from pathlib import Path

import discord
from discord.ext import commands
from PIL import Image, ImageDraw, ImageEnhance, ImageFilter, ImageFont, ImageOps

log = logging.getLogger(__name__)

FONT_DIR = Path(__file__).parent.parent / "welcome_asset" / "fonts"
W, H = 1200, 400
SS = 2  # draw at 2x and downscale for smooth edges


def _font(weight: str, size: int) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(str(FONT_DIR / f"Inter-{weight}.ttf"), size * SS)


def _renderable(text: str) -> bool:
    """True if Inter has a glyph for every character (no tofu boxes)."""
    f = _font("Bold", 20)

    def glyph(c: str) -> bytes:
        img = Image.new("L", (100, 100))
        ImageDraw.Draw(img).text((10, 10), c, font=f, fill=255)
        return img.tobytes()

    tofu = glyph("￿")
    return all(c.isspace() or glyph(c) != tofu for c in text)


def _fit_font(draw: ImageDraw.ImageDraw, text: str, weight: str, size: int, max_w: int):
    while size > 20:
        f = _font(weight, size)
        if draw.textlength(text, font=f) <= max_w * SS:
            return f
        size -= 2
    return _font(weight, size)


def _tracked(draw: ImageDraw.ImageDraw, xy, text: str, f, fill, tracking: int) -> None:
    """Draw text with letter-spacing (tracking in px at 1x)."""
    x, y = xy
    for ch in text:
        draw.text((x, y), ch, font=f, fill=fill)
        x += draw.textlength(ch, font=f) + tracking * SS


def _circle(img: Image.Image, d: int) -> Image.Image:
    img = ImageOps.fit(img, (d, d), Image.LANCZOS)
    mask = Image.new("L", (d, d), 0)
    ImageDraw.Draw(mask).ellipse((0, 0, d - 1, d - 1), fill=255)
    out = Image.new("RGBA", (d, d))
    out.paste(img, (0, 0), mask)
    return out


def render_card(avatar: bytes, name: str, label: str, line1: str, line2: str, leaving: bool) -> io.BytesIO:
    """Render the Aurora card; goodbye cards are greyscale."""
    av = Image.open(io.BytesIO(avatar)).convert("RGBA")
    cw, ch = W * SS, H * SS

    bg = ImageOps.fit(av.convert("RGB"), (cw, ch), Image.LANCZOS, centering=(0.5, 0.35))
    bg = bg.filter(ImageFilter.GaussianBlur(60 * SS))
    bg = ImageEnhance.Color(bg).enhance(1.8)
    bg = ImageEnhance.Brightness(bg).enhance(0.5)
    canvas = bg.convert("RGBA")
    # soft left-to-right darkening so the text stays readable
    shade = Image.linear_gradient("L").rotate(90).resize((cw, ch))
    canvas = Image.composite(Image.new("RGBA", (cw, ch), (0, 0, 0, 255)), canvas, shade.point(lambda v: v * 0.35))
    d = ImageDraw.Draw(canvas)

    ad = 220 * SS
    ax, ay = 90 * SS, (ch - ad) // 2
    ring = 6 * SS
    d.ellipse((ax - ring, ay - ring, ax + ad + ring, ay + ad + ring), fill=(255, 255, 255, 230))
    canvas.alpha_composite(_circle(av, ad), (ax, ay))

    tx = ax + ad + 70 * SS
    max_w = W - tx // SS - 70
    _tracked(d, (tx, 92 * SS), label, _font("SemiBold", 22), (255, 255, 255, 170), 8)
    d.text((tx, 130 * SS), name, font=_fit_font(d, name, "Bold", 76, max_w), fill=(255, 255, 255))
    d.text((tx, 238 * SS), line1, font=_fit_font(d, line1, "Regular", 28, max_w), fill=(255, 255, 255, 215))
    d.text((tx, 282 * SS), line2, font=_font("Light", 24), fill=(255, 255, 255, 150))

    if leaving:
        canvas = ImageOps.grayscale(canvas.convert("RGB")).convert("RGBA")

    card = canvas.resize((W, H), Image.LANCZOS)
    mask = Image.new("L", (W, H), 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, W, H), 28, fill=255)
    card.putalpha(mask)

    buf = io.BytesIO()
    card.save(buf, "PNG")
    buf.seek(0)
    return buf


def _stayed(joined: datetime | None) -> str:
    """'Was here for 4 months' style duration."""
    if joined is None:
        return ""
    secs = (datetime.now(timezone.utc) - joined).total_seconds()
    for unit, size in (("year", 365 * 86400), ("month", 30 * 86400), ("day", 86400),
                       ("hour", 3600), ("minute", 60)):
        n = int(secs // size)
        if n >= 1:
            return f"Was here for {n} {unit}{'s' if n != 1 else ''}"
    return "Was here for less than a minute"


class Welcome(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        val = os.getenv("WELCOME_CHANNEL_ID", "")
        self.channel_id = int(val) if val.isdigit() else None

    def _channel(self, guild: discord.Guild) -> discord.TextChannel | None:
        if self.channel_id is None:
            return None
        channel = self.bot.get_channel(self.channel_id)
        return channel if channel and channel.guild.id == guild.id else None

    def _display(self, member: discord.Member) -> str:
        name = member.display_name
        return name if _renderable(name) else member.name

    async def _post(self, member, channel, label, line1, line2, leaving, content):
        try:
            avatar = await member.display_avatar.replace(size=512, format="png").read()
            img = await asyncio.to_thread(
                render_card, avatar, self._display(member), label, line1, line2, leaving)
            await channel.send(
                content,
                file=discord.File(img, filename="goodbye.png" if leaving else "welcome.png"),
                allowed_mentions=discord.AllowedMentions(users=True),
            )
        except Exception:
            log.exception("welcome card failed for %s", member)

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member):
        channel = self._channel(member.guild)
        if member.bot or channel is None:
            return
        joined = member.joined_at or datetime.now(timezone.utc)
        await self._post(member, channel, "WELCOME", f"Welcome to {member.guild.name}",
                         f"Joined {joined:%b} {joined.day}, {joined.year}", False, member.mention)

    @commands.Cog.listener()
    async def on_member_remove(self, member: discord.Member):
        channel = self._channel(member.guild)
        if member.bot or channel is None:
            return
        await self._post(member, channel, "GOODBYE", f"Left {member.guild.name}",
                         _stayed(member.joined_at), True, None)


async def setup(bot):
    await bot.add_cog(Welcome(bot))
