# Plugin Development Guide

This guide explains how to create plugins for the Xian Telegram Bot Framework.

## Plugin Structure

Each plugin should have its own directory in the `plg` folder with the following structure:

```
plg/
└── your_plugin/
    ├── your_plugin.py        # Main plugin file
    ├── cfg/                  # Configuration files
    │   └── your_plugin.json
    └── res/                  # Resource files
        ├── your_plugin.html  # Usage info
        └── other_resources
```

## Creating a Plugin

1. Create a new directory in the `plg` folder with your plugin name
2. Create a Python file with the same name as the directory
3. Create a class with the same name as the directory
4. The class needs to inherit from `TGBFPlugin`
5. Implement the required `init()` method

Basic example:

```python
from plugin import TGBFPlugin
from telegram import Update
from telegram.ext import CallbackContext, CommandHandler

class YourPlugin(TGBFPlugin):
    async def init(self):
        # Register command handler
        await self.add_handler(
            CommandHandler(self.handle, self.command_callback, block=False)
        )

    @TGBFPlugin.logging()              # Enables logging
    @TGBFPlugin.send_typing()          # Shows typing indicator
    async def command_callback(self, update: Update, context: CallbackContext):
        if not update.message:
            return
            
        await update.message.reply_text("Hello from your plugin!")
```

## Configuration

Create a JSON configuration file in the plugin's `cfg` directory:

```json
{
    "handle": "yourcommand",                 # Command trigger (optional)
    "category": "Your Category",             # For help command grouping
    "description": "What your plugin does",  # Plugin description
    "dependency": ["other_plugin"],          # Required plugins
    "admins": [123456789],                   # Admin user IDs
    "blacklist": [123456789],                # Blocked user IDs
    "whitelist": [123456789]                 # Allowed user IDs
}
```

Access configuration in your code:
```python
# Get plugin's own config
value = self.cfg.get("config_key")

# Get global config
value = self.cfg_global.get("config_key")
```

## Plugin Decorators

The framework provides several decorators to handle common tasks:

```python
@TGBFPlugin.logging()          # Enable logging
@TGBFPlugin.send_typing()      # Show typing indicator
@TGBFPlugin.private()          # Only allow in private chats
@TGBFPlugin.public()           # Only allow in public chats
@TGBFPlugin.owner()            # Only allow for bot owner
@TGBFPlugin.blacklist()        # Check blacklist
@TGBFPlugin.whitelist()        # Check whitelist
@TGBFPlugin.dependency()       # Check required plugins
```

## Database Access

The framework provides both SQLite and key-value storage:

```python
# SQLite example
async def init(self):
    # Create table if not exists
    if not await self.table_exists("your_table"):
        sql = "CREATE TABLE your_table (id INTEGER PRIMARY KEY)"
        await self.exec_sql(sql)

    # Execute query
    sql = "SELECT * FROM your_table WHERE id = ?"
    result = await self.exec_sql(sql, some_id)

# Key-value storage example
def save_data(self):
    # Set value
    self.kv_set("key", "value")
    
    # Get value
    value = self.kv_get("key")
    
    # Delete value
    self.kv_del("key")
    
    # Get all values
    all_data = self.kv_all()
```

## Web Endpoints

Add HTTP endpoints to your plugin:

```python
from starlette.responses import JSONResponse

async def init(self):
    await self.add_endpoint('/your-endpoint', self.endpoint_handler)

async def endpoint_handler(self, param: str = None):
    return JSONResponse({
        'status': 'success',
        'data': 'Response data',
        'param': param
    })
```

## Resource Files

1. Create a usage info file (`res/your_plugin.html`):
```html
<b>How to use the {{handle}} plugin</b>

◾️ Example usage:
<code>/{{handle}} parameter</code>
```

2. Access resource in your code:
```python
# Get usage info
info = await self.get_resource("your_plugin.html")

# Get other resource
data = await self.get_resource("other_file.txt")
```

## Helper Methods

The framework provides several helper methods:

```python
# Get plugin instance
plugin = self.get_plugin("plugin_name")

# Check if plugin is enabled
is_enabled = self.is_enabled("plugin_name")

# Check if in private chat
is_private = self.is_private(message)

# Remove message after delay
await self.remove_msg_after(message, after_secs=10)

# Notify admin
await self.notify("Something happened")
```

## Error Handling

Always use try-except blocks and log errors:

```python
try:
    # Your code
    pass
except Exception as e:
    # Log error
    self.log.error(f"Error in plugin: {e}")
    # Notify admin
    await self.notify(e)
    # Inform user
    await update.message.reply_text(f"❌ An error occurred: {str(e)}")
```

## Best Practices

1. Always handle edited messages:
```python
if not update.message:
    return
```

2. Use HTML formatting for messages:
```python
await update.message.reply_text(
    f"<b>Bold text</b>\n"
    f"<code>Monospace text</code>",
    parse_mode=ParseMode.HTML
)
```

3. Use constants for emojis:
```python
import constants as con

await update.message.reply_text(f"{con.INFO} Information message")
```

4. Clean up resources:
```python
async def cleanup(self):
    # Called when plugin is disabled
    await self
