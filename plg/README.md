# Plugin Development Guide

This guide explains how to build plugins for the Xian Telegram Bot Framework. Every feature ships as an asynchronous plugin that inherits `TGBFPlugin` and lives under `plg/<feature>/`.

## Directory Layout
```
plg/
└── your_plugin/
    ├── your_plugin.py      # Plugin implementation (class name matches directory)
    ├── cfg/
    │   └── your_plugin.json  # Optional plugin-local configuration
    ├── dat/                # Optional SQLite/kv storage (created on demand)
    └── res/                # Optional resources (templates, SQL, etc.)
```

## Minimal Plugin Skeleton
```python
from telegram import Update
from telegram.ext import CallbackContext, CommandHandler

from plugin import PluginManifest, TGBFPlugin


class YourPlugin(TGBFPlugin):
    MANIFEST = PluginManifest(
        description="Describe what the plugin does",
        category="Utilities",
        requires=("other_plugin",),
    )

    async def init(self):
        await self.add_handler(
            CommandHandler(self.handle, self.command_callback, block=False)
        )

    @TGBFPlugin.logging()
    @TGBFPlugin.send_typing()
    async def command_callback(self, update: Update, context: CallbackContext):
        if not update.message:
            return
        await update.message.reply_text("Hello from your plugin!")
```
`init()` runs during plugin enable; use it to register handlers, jobs, or web endpoints. Override `cleanup()` to dispose of resources when the plugin is disabled.

## Configuration & Metadata
- Create `cfg/your_plugin.json` when you need custom settings:
  ```json
  {
    "handle": "yourcommand",
    "category": "Utilities",
    "description": "What your plugin does",
    "dependency": ["other_plugin"],
    "admins": [123456789],
    "blacklist": [],
    "whitelist": []
  }
  ```
- Access values with `self.cfg.get("key")`. Use `self.cfg_global` for `cfg/global.json`.
- `MANIFEST` overrides are optional; without it, the framework infers metadata from config and class attributes. Classic config-driven dependencies still use the `dependency` key that powers `@TGBFPlugin.dependency()`.

## Decorators & Helpers
Use the provided decorators to guard handlers:
- `@TGBFPlugin.logging()` – log incoming `Update` objects.
- `@TGBFPlugin.send_typing()` – show typing indicators.
- `@TGBFPlugin.private()` / `public()` / `owner()` – scope commands.
- `@TGBFPlugin.blacklist()` / `whitelist()` / `dependency()` – enforce access and dependencies.

Register additional behaviour with helper methods:
- `await self.add_handler(handler, group=None)` – attach Telegram handlers.
- `self.run_once(func, when, data=None)` / `self.run_repeating(...)` – schedule jobs.
- `await self.notify("message")` – alert the admin defined in `cfg/global.json`.

## Data & Resources
### SQLite (async)
```python
if not await self.table_exists("sample"):
    await self.exec_sql("CREATE TABLE sample (id INTEGER PRIMARY KEY)")

result = await self.exec_sql("SELECT * FROM sample WHERE id = ?", some_id)
rows = result["data"] if result["success"] else []
```
The helpers wrap `aiosqlite` connections and commit automatically. Use `table_exists_global()` and `exec_sql_global()` to work with shared tables.

### Key-Value Store
```python
self.kv_set("key", {"value": 1})
value = self.kv_get("key")
self.kv_del("key")
```

### Resources
Place templates or SQL under `res/`:
```python
html = await self.get_resource("usage.html")
sql = await self.get_resource_global("create_wallets.sql")
```

## HTTP Endpoints
```python
from starlette.responses import JSONResponse

async def init(self):
    await self.add_endpoint("/your-endpoint", self.endpoint_handler)

async def endpoint_handler(self, param: str | None = None):
    return JSONResponse({"status": "ok", "param": param})
```
Endpoints register with the shared FastAPI app and must handle asynchronous execution.

## Cleanup & Best Practices
```python
async def cleanup(self):
    await self.notify("Plugin shutting down")
    if self.jobs:
        for job in self.jobs:
            job.schedule_removal()
```
- Always guard against `update.message` being `None` (edited channel posts, callbacks).
- Prefer HTML strings using constants from `constants.py` for emojis/icons.
- Keep handlers short; offload blocking work via async helpers or `asyncio.to_thread`.
- Add tests under `tests/` that stub Telegram/network calls, and run `poetry run pytest`.
