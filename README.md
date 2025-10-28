# Xian Telegram Bot Framework

Plugin-first framework for building Telegram bots with Python 3.11, asynchronous handlers, and pluggable FastAPI endpoints. It wraps [`python-telegram-bot`](https://github.com/python-telegram-bot/python-telegram-bot) with conventions that make it simple to compose features, ship HTTP routes, and persist bot state.

## Features
- **Asynchronous plugins** – isolated modules under `plg/` with optional configs and web resources.
- **FastAPI web layer** – extend the bot with HTTP endpoints per plugin via `WebAppWrapper`.
- **Persistent storage** – SQLite helpers powered by `aiosqlite` and plugin-scoped data directories.
- **Configuration system** – merge global defaults from `cfg/global.json` with plugin overrides.
- **Operational tooling** – structured logging through `loguru`, graceful shutdown, PM2-ready scripts.

## Requirements
- Python 3.11 and Poetry ≥ 1.6 (`curl -sSL https://install.python-poetry.org | python3 -`).
- Telegram bot token stored in `.env`; additional secrets should live in the same file.
- Optional PM2 (`npm install -g pm2`) for production process management.

## Quick Start
1. Clone the repo:
   ```bash
   git clone https://github.com/xian-network/tg-bot.git
   cd tg-bot
   ```
2. Install dependencies inside the Poetry virtualenv:
   ```bash
   poetry install
   ```
3. Create a `.env` file:
   ```env
   TG_TOKEN=your_bot_token
   LOG_LEVEL=INFO        # DEBUG, INFO, WARNING, ERROR
   LOG_INTO_FILE=true
   ```
4. Run the bot locally:
   ```bash
   poetry run python main.py
   ```

## Development Workflow
- `poetry run pytest` – execute the async test suite (with `pytest-asyncio`).
- `poetry run ruff check .` – lint for style and common mistakes.
- `poetry run mypy .` – static type analysis using the project stubs.
- `poetry run python main.py` – launch the bot with current configuration.
- `./start.sh` – replicate the PM2 entrypoint locally.

Run linting and tests before pushing changes; note any skipped checks in your pull request.

## Project Layout
```
main.py            # TelegramBot runtime and lifecycle
plugin.py          # TGBFPlugin base class, manifest utilities, decorators
plg/<feature>/     # Feature plugins (handlers, web endpoints, resources)
cfg/global.json    # Global configuration defaults
res/               # Shared static assets
web.py             # FastAPI wrapper exposed by the bot
tests/             # Async pytest suite (create alongside modules you touch)
```

## Configuration
Global settings live in `cfg/global.json`:
```json
{
  "admin_tg_id": 123456789,
  "webserver_port": 5000,
  "xian": {
    "node": "http://127.0.0.1:26657",
    "explorer": "https://explorer.xian.org",
    "chain_id": "your-chain-id"
  }
}
```
Plugins may provide `cfg/<plugin>.json` with keys such as `handle`, `requires`, `description`, `category`, `aliases`, and access control lists (`blacklist`, `whitelist`). Keep secrets in environment variables and reference them through your plugin code.

## Running in Production
`pm2.config.json` and `start.sh` mirror the production profile:
```bash
chmod +x start.sh
pm2 start pm2.config.json
pm2 logs tg-bot      # Stream bot logs
pm2 restart tg-bot   # Redeploy after updates
```
Enable startup persistence with `pm2 save` and `pm2 startup`.

## Updating & Maintenance
```bash
pm2 stop tg-bot
git pull origin main
poetry install        # Apply dependency updates
pm2 restart tg-bot
```
Review release notes for configuration or schema changes, and run `poetry run pytest` before restarting.

## Plugin Development
- Use `poetry run python main.py` once to ensure the plugin scaffold is discovered.
- Place handlers in `plg/<feature>/<feature>.py` with a class that inherits `TGBFPlugin`.
- Define an optional `MANIFEST` attribute or rely on `PluginManifest.materialize`.
- Register jobs, handlers, and web routes through the helper methods in `TGBFPlugin`.
- Add tests under `tests/` mirroring your plugin module and stub external services.

See `plg/README.md` for detailed patterns and review `AGENTS.md` for contributor expectations.
