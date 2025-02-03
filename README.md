# Xian Telegram Bot Framework

A powerful and flexible framework for building Telegram bots using Python. Built on top of [`python-telegram-bot`](https://github.com/python-telegram-bot/python-telegram-bot), this framework provides a plugin-based architecture that makes it easy to develop, maintain, and extend Telegram bots.

## Features

- **Plugin System**: Modular architecture where each feature is a plugin that can be enabled/disabled at runtime
- **FastAPI Integration**: Each plugin can create its own HTTP endpoints
- **SQLite Integration**: Built-in database support for persistent storage
- **Configuration Management**: JSON-based configuration system for both global and per-plugin settings
- **Advanced Logging**: File-based logging with rotation support
- **Decorator System**: Handy decorators for common bot tasks like:
  - Typing notifications
  - Access control (private/public/owner commands)
  - Command filtering (blacklists/whitelists)
- **Built-in Plugins**:
  - Error handling
  - Backup/restore functionality
  - Bot control (restart/shutdown)
  - Plugin management
  - User message tracking

## Installation

1. Clone the repository:
```bash
git clone https://github.com/xian-network/tg-bot.git
cd tg-bot
```

2. Install Poetry (Python dependency management):
```bash
curl -sSL https://install.python-poetry.org | python3 -
```

3. Install dependencies:
```bash
poetry install
```

4. Create `.env` file in the root directory:
```env
TG_TOKEN=your_telegram_bot_token
LOG_LEVEL=INFO  # DEBUG, INFO, WARNING, or ERROR
LOG_INTO_FILE=true
```

## Configuration

### Global Configuration

The global configuration file (`cfg/global.json`) contains settings that affect the entire bot:

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

### Plugin Configuration

Each plugin can have its own configuration file in the `cfg` folder. Common settings include:
- `handle`: Command trigger
- `dependency`: List of required plugins
- `admins`: List of admin user IDs
- `description`: Plugin description
- `category`: Plugin category
- `blacklist`/`whitelist`: Access control lists
- `blacklist_msg`/`whitelist_msg`: Custom messages for access denied

## Running the Bot

### Using PM2 (Recommended for Production)

The repository includes both a startup script (`start.sh`) and PM2 configuration file (`pm2.config.json`). The startup script automatically detects the Poetry virtual environment, and the PM2 configuration is pre-configured with recommended settings for production use.

First, make the startup script executable:
```bash
chmod +x start.sh
```

4. PM2 Commands:
```bash
# Start the bot
pm2 start pm2.config.json

# View status
pm2 status

# Monitor logs
pm2 logs tg-bot

# Monitor resources
pm2 monit

# Stop the bot
pm2 stop tg-bot

# Restart the bot
pm2 restart tg-bot

# Remove from PM2
pm2 delete tg-bot

# Set up auto-start on system boot
pm2 startup
pm2 save
```

### Using Screen (Alternative)

```bash
screen -S tgbf2
poetry run python main.py
# Press Ctrl+A, D to detach
# Use 'screen -r tgbf2' to reattach
```

## Updating the Bot

1. Stop the bot:
```bash
pm2 stop tg-bot
```

2. Pull latest changes:
```bash
git reset --hard origin/main
git pull origin main
```

3. Update dependencies:
```bash
poetry install
```

4. Restart the bot:
```bash
pm2 restart tg-bot
```

## Plugin Development

Plugins are Python modules that extend the bot's functionality. For detailed information about creating and working with plugins, see the [Plugin Development Guide](plg/README.md).

## Web Server Integration

To enable the FastAPI web server:

1. In `cfg/global.json`, set:
```json
{
    "webserver_port": 5000
}
```
