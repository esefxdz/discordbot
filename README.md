# yuuka

personal discord bot running on my arch linux laptop. named after hayase yuuka. 
it started as a simple music bot and became a massive mess of features i actually use.

## features

- **ai** — uses deepseek v4 (flash/pro). full vision support (well... vision is a little broken rn), continuous memory summarization (so it doesn't forget context), per-channel personas (yuuka/rem), and native deepseek-reasoner thinking toggles. doesn't break character.
- **bridges** — two-way telegram <-> discord message forwarding. also pulls twitter/x leaks via rss and dumps them into a channel.d
- **media & music** — yt-dlp + ffmpeg based music playback. also has commands for random ffmpeg media conversions.
- **sysinfo** — live monitoring of my laptop's cpu, ram, temps (lm-sensors), top processes, and fastfetch.
- **utilities** — live currency conversion, steam stat tracking (strinova player counts).
- **calendar** — firestore-backed event booking via modal. autoconverts local times to UTC using a country list, feeds a web calendar frontend.
- **timestamp** — `/timestamp` modal that generates Discord `<t:unix:FORMAT>` tags. accepts day numbers or day names ("Sunday"), handles month rollover, outputs all 7 Discord time formats.
- **timestamp (friends)** — passive message listener: when a whitelisted user says "my time" in chat, the bot parses the surrounding text for time expressions, auto-converts to their timezone, and replies with timestamp tags. no commands needed.
- **junk** — dice, coinflip, aura meter, rank, gifs, copypastas.

## stack

- python + `discord.py`
- deepseek api for the ai stuff
- yt-dlp + ffmpeg for audio
- psutil + lm-sensors for hardware stats
- hosted on an acer nitro AN515-51 running arch linux
- runs as a systemd service, updated via git pull over ssh

## commands

run these to see the sub-commands:
- `!ai` — lists all ai-related commands (setting personas, activechat, toggling thinking/temperature)
- `!music` — music playback controls
- `!sysinfo` — hardware monitoring
- `!gifs` — the gif list 
- `!fun` — the junk commands
- `!gacha help` — blue archive recruitment simulator (pick banners, pull with live rates)
- `!book` / `!unbook` — calendar event booking (whitelisted users only)
- `/timestamp` — Discord timestamp generator modal
