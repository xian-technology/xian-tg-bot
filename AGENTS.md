# Repository Guidelines

## Project Structure & Module Organization
Core runtime lives in `main.py`; `plugin.py` defines `TGBFPlugin`. Shared helpers sit in `utils.py`, `constants.py`, and `config.py`. Features ship as directories under `plg/<feature>/` with modules like `plg/help/help.py` and optional plugin-local `cfg/` or `res/`. Shared assets live in `res/`, defaults in `cfg/global.json`. Entry scripts (`start.sh`, `pm2.config.json`) and `web.py` live at the root, and tests belong in `tests/`.

## Build, Test, and Development Commands
- `poetry install` — install dependencies inside the Poetry virtualenv.
- `poetry run python main.py` — start the bot with your environment config.
- `./start.sh` — mirror the PM2 entry point locally.
- `poetry run pytest` — execute the async test suite.
- `poetry run ruff check .` & `poetry run mypy .` — lint and type-check changes.
- `pm2 start pm2.config.json` — launch the production profile when needed.

## Coding Style & Naming Conventions
Write Python 3.11 with four-space indentation and keep lines below 100 characters (Ruff default). Classes stay PascalCase, plugin packages and modules use lowercase_snake_case, and coroutine names follow snake_case (`run_trend_scan`). Use `loguru.logger` and `@TGBFPlugin` decorators for logging, typing cues, and guards. Configuration keys remain lowercase_snake_case to align with JSON config.

## Testing Guidelines
Use `pytest` plus `pytest-asyncio`; name files `test_*.py` and keep them near the module under test. Prefer async fixtures for plugin handlers and stub Telegram, HTTP, and DB calls to stay deterministic. Run `poetry run pytest` before every PR and call out gaps when touching configuration, plugin loading, or persistence routines.

## Commit & Pull Request Guidelines
Commits should remain short and imperative, mirroring history such as “Fix /simulate command” or “Update dependencies.” Group related changes together and reference issue IDs when available. Pull requests need a concise summary, verification notes (commands plus outcomes), relevant ticket links, and screenshots or chat transcripts for user-facing updates. Explicitly call out configuration or schema changes and attach snippets for new files in `cfg/` or `res/` to ease review.

## Security & Configuration Tips
Load secrets through environment variables or `.env` consumed by `python-dotenv`; never commit credentials. When editing `cfg/global.json` or plugin overrides, check in sanitized defaults and document sensitive values outside the repo. FastAPI routes added via `WebAppWrapper` must include the expected auth or rate controls before deployment, and rotate tokens if they leak.
