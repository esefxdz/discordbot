# Repository Guidelines

## Project Structure & Module Organization

```
yuuka/
├── main.py               # Bot entry point, cog loader, bridge initialization
├── cogs/                  # Discord command modules (general, sysinfo, gifs, etc.)
├── ai/                    # AI roleplay integration (DeepSeek API, per-channel personas)
│   └── personalities/     # Character prompt files
├── music/                 # Music playback subsystem (wavelink + yt-dlp + ffmpeg)
│   ├── player/            # Playback commands, events, queue management
│   ├── radio/             # Internet radio streaming
│   └── shared/            # Constants, shared state, utilities
├── tgbridge/              # Telegram ↔ Discord two-way message forwarding
├── twitterbridge/         # Twitter/X RSS feed → Discord webhook relay
├── rio/                   # SSH connection utilities
├── data/                  # Runtime persistence files (gitignored)
├── gifs/                  # GIF assets (gitignored)
└── credentials_test.env   # Environment variable template
```

Each cog is a self-contained `commands.Cog` subclass loaded in `main.py`. Bridge components run as background `asyncio` tasks.

## Build, Test, and Development Commands

This is a personal bot with no formal build system. Run it directly:

```bash
python main.py
```

- Copy `credentials_test.env` → `credentials.env` and fill in your tokens.
- There is no test suite. Test by running the bot locally and using `!`-prefixed commands in a Discord server.

## Coding Style & Naming Conventions

- **Language**: Python 3.12+
- **Indentation**: 4 spaces
- **Naming**: `snake_case` for files, variables, and functions; `PascalCase` for classes; `UPPER_CASE` for constants
- **Cogs**: One class per file in `cogs/`, subclassing `commands.Cog`, loaded via `await bot.load_extension('cogs.<name>')`
- **Music subpackage**: Uses mixin-based composition — `Music` inherits from several command/event mixins
- **Async**: All I/O uses `async`/`await`; bridge tasks run with `asyncio.create_task()`
- No linter or formatter is currently configured

## Testing Guidelines

No formal test framework is in place. When adding or changing features, validate manually:

1. Start the bot locally with a test Discord token.
2. Exercise each affected command in a private server.
3. For music/radio, test with different stream types (YouTube, direct audio URLs, radio streams).
4. For bridges, confirm messages flow correctly in both directions.
5. Check the console for unhandled exceptions — the `logging` level is `INFO`.

## Commit & Pull Request Guidelines

This is a personal repository with informal conventions:

- **Commit messages**: Short, lowercase, imperative mood (e.g., `fix radio race conditions`, `add radio mode`). No strict format is enforced.
- **Branches**: Work directly on `master` or use short-lived feature branches.
- **PRs**: Not used — the project is single-contributor.
- **Before committing**: Ensure the bot starts without import errors and any changed cogs load successfully.

## Environment & Secrets

- Store secrets in `credentials.env` (gitignored). The template is `credentials_test.env`.
- Required: `DISCORD_TOKEN`, `OWNER_ID`. Optional: `TELEGRAM_BOT_TOKEN`, `TWITTER_RSS_URL`, `DEEPSEEK_API`, and webhook URLs.
- The bot is managed as a `systemd` service on Arch Linux and updated via `git pull` over SSH (the `!gitpull` cog handles remote updates).
