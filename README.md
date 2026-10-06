# yuuka

personal discord bot running on my debian laptop. named after hayase yuuka. 
it started as a simple music bot and became a massive mess of features i sometimes use.

## features

- **ai** — uses deepseek v4 (flash/pro). text-only (no image/vision input), continuous memory summarization (so it doesn't forget context), per-channel personas (yuuka/rem), and native deepseek-reasoner thinking toggles. doesn't break character.
- **bridges** — two-way telegram <-> discord message forwarding. twitter/x leaks: new @CalabiyauLeaks tweets go to two servers as fxtwitter links (`twitterbridge/`), from a self-hosted RSSHub feed on yuuka (esefrss). `!leaks` shows the latest 5. if the feed breaks for 90 min the bot DMs the owner.
- **media & music** — lavalink + wavelink music playback (youtube search + http/icecast radio streams). also has ffmpeg media conversion slash commands (/togif, /caption, /reverse, /speed, /tomp4, /tomp3, /toopus).
- **sysinfo** — live monitoring of my laptop's cpu, ram, temps (lm-sensors), top processes, and fastfetch.
- **utilities** — live currency conversion (/currency), steam stat tracking (strinova player counts).
- **calendar** — firestore-backed event booking via modal. autoconverts local times to UTC using a country list, feeds a web calendar frontend.
- **timestamp** — `/timestamp` modal that generates Discord `<t:unix:FORMAT>` tags. accepts day numbers or day names ("Sunday"), handles month rollover, outputs all 7 Discord time formats.
- **timestamp (friends)** — passive message listener: when a whitelisted user says "my time" in chat, the bot parses the surrounding text for time expressions, auto-converts to their timezone, and replies with timestamp tags. no commands needed.
- **junk** — dice, coinflip, aura meter, rank, gifs, copypastas.

## stack

- python + `discord.py`
- deepseek api for the ai stuff
- wavelink + lavalink (java) for music
- psutil + lm-sensors for hardware stats
- runs as systemd services: `discordbot`, `lavalink`, and a weekly `discordbot-refresh.timer` that auto-updates the Blue Archive roster

## commands

run these to see the sub-commands:
- `!ai` — lists all ai-related commands (setting personas, activechat, toggling thinking/temperature)
- `!music` — music playback controls
- `!sysinfo` — hardware monitoring
- `!gifs` — the gif list 
- `!fun` — the junk commands
- `!gacha help` — blue archive recruitment simulator (pick banners, pull with live rates)
- `!book` / `!unbook` — calendar event booking (whitelisted users only)
- `!time` — Discord timestamp generator modal
