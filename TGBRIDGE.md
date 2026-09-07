# Telegram bridge — rebuild notes

Everything below is in `tgbridge/`. Routes, behaviour and env-var names stay
backwards compatible: **your existing `credentials.env` works unchanged.**

---

## 1. What you need to do

One optional step, for the avatars you asked for:

1. Make a channel in your server (e.g. `#tg-avatar-cache`) and deny
   `@everyone` → *View Channel*.
2. Right-click it → **Copy Channel ID**.
3. Paste it into the `AVATAR_CACHE_CHANNEL_ID=` line already added at the
   bottom of `credentials.env`.

Leave it empty and everything else still works — forwarded messages just keep
the default webhook avatar.

Optional: `pip install lottie` to make animated `.tgs` stickers actually
animate. Without it they fall back to a static thumbnail, which is still
better than the broken attachment they produce today.

`main.py` now loads the bridge as one extension:

```python
await bot.load_extension('tgbridge')
```

**Behavioural change worth knowing:** the Telegram poller now starts when the
extension loads, not from `on_ready`. That's what fixes the duplicate-poller
bug (A3), but it means startup happens before the Discord gateway connects. A
Telegram failure at that moment is caught and retried in the background (5
attempts, backing off 30 s → 10 min) — it will **not** stop the rest of the bot
from booting. Verified with a deliberately invalid token: the bot loaded fine,
both cogs registered, and `!bridge status` showed the error.

---

## 2. Bugs fixed

These were all live defects, not hypotheticals.

| # | Bug | Effect before |
|---|---|---|
| A1 | `parse_mode='Markdown'` with an unescaped body | **Every** Discord message containing a stray `*`, `_`, `` ` `` or `[` was rejected by Telegram with a 400 and silently dropped. Now HTML parse mode with proper escaping, plus a plain-text retry if Telegram still complains. |
| A2 | No `allowed_mentions` on webhook posts | Anyone in the Telegram group could type `@everyone` and ping your whole server. Now `{"parse": []}` on every post. |
| A3 | `forwarder.start()` called from `on_ready` | `on_ready` fires again after every gateway reconnect, starting a second poller on the same token → Telegram 409 Conflict and duplicated messages. Now starts once at extension load, with a `_running` guard. |
| A4 | `document.mime_type.startswith(...)` | `mime_type` is nullable; any document without one raised into the bare `except` and the message was dropped. |
| A5 | `temp_in_path` used in `finally`, assigned in `try` | If `NamedTemporaryFile` raised, cleanup threw `UnboundLocalError` and masked the real error. |
| A6 | ffmpeg cleanup after `wait_for`, no kill on timeout | A conversion timeout leaked the temp file **and** left an orphaned ffmpeg process forever. Now killed and reaped in every path. |

---

## 3. Stickers — measured, not guessed

Your old code ran all three Telegram sticker formats through one GIF
conversion. I tested each:

| Format | Before | Now |
|---|---|---|
| **Static `.webp`** | Re-encoded to GIF. Measured: the antialiased edge went from **228 alpha levels to 2**, i.e. a visible jagged halo on every sticker — and the file got **4× bigger** (942 B → 3771 B) and was downscaled 512→320. | Passed through untouched. Discord renders `.webp` natively with full alpha. Zero CPU. |
| **Animated `.tgs`** | ffmpeg exits **183 "Invalid data found"** (it's gzipped Lottie JSON, ffmpeg cannot read it). The `returncode == 0` check failed, so the raw `.tgs` was uploaded as an **unopenable junk attachment**. | Rendered via the optional `lottie` library; falls back to Telegram's own static thumbnail + emoji. |
| **Video `.webm`** | Converted to GIF, same alpha crushing. | Converted to **animated WebP** — loops in Discord, keeps 8-bit alpha, and measured **7.4× smaller** than the GIF (16 KB vs 122 KB). |

**One subtle bug worth calling out.** ffmpeg's *default* VP9 decoder silently
discards the alpha channel of VP9-in-WebM — every transparent pixel decodes to
opaque black. Only `libvpx-vp9`, selected **before** `-i`, reads it. Telegram
video stickers are exactly this format. Verified: without the flag, 1 alpha
level; with it, 217. This is now handled in `media.py:_decoder_args`.

Converted stickers are cached in sqlite by `file_unique_id`, so each one is
only ever converted once.

---

## 4. Profile-photo avatars

Telegram's only file URL embeds your bot token
(`api.telegram.org/file/bot<TOKEN>/...`), so it can never go in a webhook
payload. Instead the photo is re-uploaded once to your private cache channel
and that link is used as `avatar_url`.

I verified against the live API that **Discord re-hosts the image at post
time**: `author.avatar` comes back as a fresh hash distinct from the webhook's
own. So the source link only needs to be alive at the moment of the POST, and
already-forwarded messages keep their avatar forever.

Caching behaviour, all verified by test:

- repeat lookups cost **zero** API calls
- users with no photo (or privacy-hidden) are cached negatively, so it doesn't
  re-ask on every message
- when the hosted link goes stale it re-uploads **from the stored blob**,
  without re-downloading from Telegram
- when someone changes their photo it's detected and refreshed
- channel posts use the chat photo instead of a user photo

---

## 5. Features added

**Reply threading (both directions).** A Telegram reply renders as a Discord
quote block with a clickable jump link to the original forwarded message; a
Discord reply becomes a native Telegram reply. Backed by a message-ID map in
`data/tgbridge.db`.

**Edit sync (both directions).** Telegram `edited_message` updates patch the
Discord message; Discord edits patch the Telegram one. The Discord side uses
`on_raw_message_edit`, **not** `on_message_edit` — the cached variant never
fires for messages sent before the last restart, so edit sync would have
silently worked in testing and failed in production.

**Delete sync (Discord → Telegram only).** Including bulk deletes. The reverse
isn't possible: Telegram's Bot API gives bots no delete events at all.

**Albums.** Telegram sends an album as N separate updates, so a 5-photo post
used to become 5 separate Discord messages. Now debounced (1.5 s) into one
message with up to 10 attachments. All N Telegram IDs map to the single
Discord message, so a reply to *any* photo in the album still resolves.

**Message types that used to say `*(unsupported message type)*`:** voice notes
(with duration), video notes, audio (with artist/title), polls (question +
options), locations and venues (with a maps link), contacts, dice rolls,
join/leave notices, pins, and "forwarded from X" attribution.

**Rich text.** Telegram entities → Discord markdown and back: bold, italic,
underline, strikethrough, spoilers, inline code, code blocks with language,
links, and blockquotes.

> Telegram entity offsets are counted in **UTF-16 code units**, not Python
> characters, so any message containing an emoji shifts every following offset
> and naive slicing corrupts the formatting. The converter works on a UTF-16
> buffer and decodes once at the end. Verified with emoji before, inside and
> after formatted spans.

**Discord references resolved.** `<@123>`, `<@&123>`, `<#123>` and
`<:emoji:123>` used to forward to Telegram as raw snowflakes; they now become
readable names.

**Long messages split** instead of being truncated at 2000 chars (`content[:2000]`),
on the nicest available boundary. Same on the Telegram side at 4096.

**Rate limits and retries.** One shared `aiohttp` session instead of a new one
per message; 429s now wait the `retry_after` Discord asks for; 5xx retries with
exponential backoff.

**Oversized uploads** are detected and posted as a notice naming the file,
instead of vanishing.

**Markdown injection blocked.** Telegram text is escaped before it reaches
Discord, and Discord text is HTML-escaped before it reaches Telegram, so
neither side can inject formatting or HTML into the other.

**Route auto-discovery.** Any `TELEGRAM_GROUP_ID_<SUFFIX>` +
`DISCORD_WEBHOOK_URL_<SUFFIX>` + `DISCORD_CHANNEL_ID_<SUFFIX>` trio becomes a
route automatically — a third bridged channel is now an `.env` edit, not a code
change. `data/bridge_routes.json` can add or override routes. Your existing
`main` and `shitpost` routes were confirmed to load unchanged.

**`!bridge status`** — routes, uptime, forwarded counts, webhook success/failure,
cached avatars and stickers, whether Lottie is installed, and the last error.

**Explicit echo guard.** The bridge now checks the sender against its own bot
ID. Previously loops were avoided only incidentally, which would have bitten as
soon as edit sync or a second token was added.

---

## 6. Files

| File | Purpose |
|---|---|
| `__init__.py` | extension entry point; wires and starts everything |
| `config.py` | route discovery from env + optional JSON |
| `store.py` | sqlite: message map, avatar cache, sticker cache |
| `entities.py` | Telegram entities ↔ Discord markdown ↔ Telegram HTML |
| `media.py` | ffmpeg / Lottie conversion, with process kill + cleanup |
| `avatars.py` | profile photos → Discord CDN |
| `webhook.py` | Discord webhook client: session, retries, splitting |
| `forwarder.py` | Telegram → Discord |
| `discord_to_telegram.py` | Discord → Telegram |
| `status_cog.py` | `!bridge status` |

New database at `data/tgbridge.db` (created automatically, `data/` is already
gitignored). Message mappings are pruned after 30 days.

---

## 7. What I could not test

I have no access to your Telegram group or a live Discord channel, so the
end-to-end path is **unverified against the real APIs**. What I did verify:

- every module imports, and `main.py` parses
- both directions exercised with mocked Telegram/Discord objects, covering
  ~25 message shapes (text, entities, photos, albums, replies, edits, echo
  guard, null MIME types, polls, locations, dice, voice, splitting)
- ffmpeg conversions run on real generated media, with alpha fidelity measured
- avatar caching logic, including staleness and photo-change paths
- route discovery against your actual `credentials.env`
- the Discord avatar re-hosting question, against the live API (one test
  message, deleted immediately afterwards)

Worth watching on first run: whether Telegram accepts the generated HTML for
unusual messages (there's a plain-text fallback if it doesn't), and whether the
bot has permission to delete messages in the Telegram group for delete sync.
