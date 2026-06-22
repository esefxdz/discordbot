# yuuka

Personal Discord bot running on my Arch Linux laptop. Named after Hayase Yuuka.

## Features

- Music playback via Lavalink — YouTube search, URLs, playlists, queue management
- Live system monitoring — CPU, RAM, temps, disk, top processes, fastfetch
- Fun commands — dice, coinflip, aura meter, rank, gifs, copypastas

## Stack

- Python + discord.py
- Wavelink + Lavalink for audio
- psutil + lm-sensors for sysinfo
- Hosted on an Acer Nitro AN515-51 running Arch Linux
- Runs as a systemd service, updated via git pull over SSH

## Commands

- `!music` — full music command list
- `!sysinfo` — full sysinfo command list
- `!gifs` — full gif list
- `!help` — everything else