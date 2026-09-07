#telegram entities <-> discord markdown <-> telegram html##
"""Rich-text conversion between Telegram and Discord.

Telegram offsets count UTF-16 code units, not Python chars — one emoji shifts
every later offset, so we slice a utf-16-le buffer and decode once at the end.
"""
######################################################################
import html
import logging
import re

log = logging.getLogger(__name__)

# characters that would otherwise be interpreted as Discord markdown
_DISCORD_SPECIAL = set("\\*_~`|")

# Telegram entity type -> (opening marker, closing marker)
_SIMPLE_MARKERS = {
    "bold":          ("**", "**"),
    "italic":        ("*", "*"),
    "underline":     ("__", "__"),
    "strikethrough": ("~~", "~~"),
    "spoiler":       ("||", "||"),
    "code":          ("`", "`"),
}


def _utf16_units(text: str) -> list[bytes]:
    """Split text into UTF-16 code units — the unit Telegram offsets count."""
    raw = text.encode("utf-16-le")
    return [raw[i:i + 2] for i in range(0, len(raw), 2)]


def _ascii_char(unit: bytes):
    """Return the ASCII character for a code unit, or None if it isn't ASCII."""
    if unit[1] == 0 and unit[0] < 128:
        return chr(unit[0])
    return None


def to_discord_markdown(text: str, entities) -> str:
    """Render Telegram text + entities as Discord markdown."""
    if not text:
        return ""
    try:
        return _convert(text, entities or [])
    except Exception as exc:                       # pragma: no cover - safety net
        log.warning("entity conversion failed, sending plain text: %s", exc)
        return escape_discord(text)


def escape_discord(text: str) -> str:
    """Neutralise Discord markdown in an untrusted string."""
    out = []
    at_line_start = True
    for ch in text:
        if ch in _DISCORD_SPECIAL:
            out.append("\\" + ch)
        elif ch == ">" and at_line_start:
            out.append("\\>")
        else:
            out.append(ch)
        at_line_start = ch == "\n"
    return "".join(out)


def _markers_for(entity):
    """-> (open, close, is_literal, is_quote) for one Telegram entity."""
    etype = entity.type
    if etype in _SIMPLE_MARKERS:
        opener, closer = _SIMPLE_MARKERS[etype]
        return opener, closer, etype == "code", False
    if etype == "pre":
        lang = getattr(entity, "language", None) or ""
        return f"```{lang}\n", "\n```", True, False
    if etype == "text_link":
        return "[", f"]({entity.url})", False, False
    if etype == "text_mention":
        # a mention of a Telegram user with no public @username
        return "**", "**", False, False
    if etype in ("blockquote", "expandable_blockquote"):
        return "> ", "", False, True
    # url/mention/hashtag/email/custom_emoji need no markup of their own
    return None, None, False, False


def _convert(text: str, entities) -> str:
    units = _utf16_units(text)
    total = len(units)

    # events[i] = markers to emit immediately before code unit i
    opens: dict[int, list] = {}
    closes: dict[int, list] = {}

    for index, ent in enumerate(entities):
        opener, closer, literal, quote = _markers_for(ent)
        if opener is None:
            continue
        start = ent.offset
        end = ent.offset + ent.length
        if start < 0 or end > total or start >= end:
            continue
        # longer spans open first; the index makes closing LIFO so two
        # entities on the same span don't emit crossed tags
        opens.setdefault(start, []).append((-ent.length, index, opener, literal, quote))
        closes.setdefault(end, []).append((ent.length, -index, closer, literal, quote))

    out = bytearray()
    literal_depth = 0
    quote_depth = 0
    at_line_start = True

    def emit(s: str):
        out.extend(s.encode("utf-16-le"))

    for i in range(total + 1):
        for _, _, closer, literal, quote in sorted(closes.get(i, [])):
            if closer:
                emit(closer)
            if literal:
                literal_depth -= 1
            if quote:
                quote_depth -= 1
        for _, _, opener, literal, quote in sorted(opens.get(i, [])):
            if literal:
                literal_depth += 1
            if quote:
                quote_depth += 1
            if opener:
                emit(opener)
                at_line_start = False

        if i == total:
            break

        unit = units[i]
        ch = _ascii_char(unit)
        if ch == "\n" and quote_depth:
            # keep every line of a quote inside the quote
            emit("\n" + "> " * quote_depth)
            at_line_start = False
        elif literal_depth or ch is None:
            out.extend(unit)
            at_line_start = False
        elif ch in _DISCORD_SPECIAL:
            emit("\\" + ch)
            at_line_start = False
        elif ch == ">" and at_line_start:
            emit("\\>")
            at_line_start = False
        else:
            out.extend(unit)
            at_line_start = ch == "\n"

    return bytes(out).decode("utf-16-le")


# ── discord → telegram ─────────────────────────────────────────────────────
# telegram's Markdown mode 400s on any unpaired * _ ` or [ — html is the only
# parse mode where arbitrary user text can be escaped safely

_CODE_BLOCK = re.compile(r"```(?:([a-zA-Z0-9+#-]*)\n)?(.*?)```", re.DOTALL)
_INLINE_CODE = re.compile(r"`([^`\n]+)`")
_MD_LINK = re.compile(r"\[([^\]]+)\]\((https?://[^\s)]+)\)")
_BOLD = re.compile(r"\*\*(.+?)\*\*", re.DOTALL)
_UNDERLINE = re.compile(r"__(.+?)__", re.DOTALL)
_STRIKE = re.compile(r"~~(.+?)~~", re.DOTALL)
_SPOILER = re.compile(r"\|\|(.+?)\|\|", re.DOTALL)
# the leading \S keeps "* * *" from being read as an empty italic span
_ITALIC = re.compile(r"(?<![\w*])\*(\S[^*\n]*?)\*(?![\w*])")
_ITALIC_US = re.compile(r"(?<![\w_])_(\S[^_\n]*?)_(?![\w_])")

_CUSTOM_EMOJI = re.compile(r"<a?:([a-zA-Z0-9_]+):\d+>")
_USER_MENTION = re.compile(r"<@!?(\d+)>")
_ROLE_MENTION = re.compile(r"<@&(\d+)>")
_CHANNEL_MENTION = re.compile(r"<#(\d+)>")
_TIMESTAMP = re.compile(r"<t:(\d+)(?::[tTdDfFR])?>")


def resolve_discord_references(message, content: str) -> str:
    """Replace raw <@id> / <#id> / <:emoji:id> markup with readable names."""
    if not content:
        return ""
    guild = getattr(message, "guild", None)

    def user_repl(match):
        uid = int(match.group(1))
        member = guild.get_member(uid) if guild else None
        if member is None:
            for user in getattr(message, "mentions", []):
                if user.id == uid:
                    member = user
                    break
        return f"@{member.display_name}" if member else "@unknown"

    def role_repl(match):
        role = guild.get_role(int(match.group(1))) if guild else None
        return f"@{role.name}" if role else "@role"

    def channel_repl(match):
        chan = guild.get_channel(int(match.group(1))) if guild else None
        return f"#{chan.name}" if chan else "#channel"

    content = _USER_MENTION.sub(user_repl, content)
    content = _ROLE_MENTION.sub(role_repl, content)
    content = _CHANNEL_MENTION.sub(channel_repl, content)
    content = _CUSTOM_EMOJI.sub(lambda m: f":{m.group(1)}:", content)
    content = _TIMESTAMP.sub(lambda m: f"<t:{m.group(1)}>", content)
    return content


def to_telegram_html(content: str) -> str:
    """Convert Discord markdown to the small HTML subset Telegram accepts."""
    if not content:
        return ""
    try:
        return _to_html(content)
    except Exception as exc:                       # pragma: no cover - safety net
        log.warning("markdown conversion failed, sending escaped text: %s", exc)
        return html.escape(content)


def _to_html(content: str) -> str:
    # park code behind placeholders so its contents aren't read as markdown
    stash: list[str] = []

    def park(rendered: str) -> str:
        stash.append(rendered)
        return f"\x00{len(stash) - 1}\x00"

    def code_block(match):
        lang, body = match.group(1), match.group(2)
        body = html.escape(body.strip("\n"))
        if lang:
            return park(f'<pre><code class="language-{lang}">{body}</code></pre>')
        return park(f"<pre>{body}</pre>")

    content = _CODE_BLOCK.sub(code_block, content)
    content = _INLINE_CODE.sub(
        lambda m: park(f"<code>{html.escape(m.group(1))}</code>"), content)

    # a link's url must not be escaped as text
    content = _MD_LINK.sub(
        lambda m: park(f'<a href="{html.escape(m.group(2), quote=True)}">'
                       f"{html.escape(m.group(1))}</a>"), content)

    content = html.escape(content)

    content = _BOLD.sub(r"<b>\1</b>", content)
    content = _UNDERLINE.sub(r"<u>\1</u>", content)
    content = _STRIKE.sub(r"<s>\1</s>", content)
    content = _SPOILER.sub(r'<span class="tg-spoiler">\1</span>', content)
    content = _ITALIC.sub(r"<i>\1</i>", content)
    content = _ITALIC_US.sub(r"<i>\1</i>", content)

    # telegram has no inline quote tag, so keep a visual marker
    content = re.sub(r"(?m)^&gt;\s?", "| ", content)

    for index, rendered in enumerate(stash):
        content = content.replace(f"\x00{index}\x00", rendered)
    return content
