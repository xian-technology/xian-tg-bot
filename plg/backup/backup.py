import os
import time
import zipfile
from pathlib import Path

from telegram import Update
from telegram.ext import CallbackContext, CommandHandler

import constants as con
from plugin import TGBFPlugin


class Backup(TGBFPlugin):

    async def init(self):
        await self.add_handler(CommandHandler(self.handle, self.backup_callback, block=False))

    def should_exclude(self, path: str, name: str) -> bool:
        """Helper to determine if a path should be excluded from backup"""
        # Check if path contains these directory names anywhere in the path
        path_exclusions = {
            'site-packages',
            'virtualenvs',
            '.git',
            '.idea',
            '.vscode',
            '__pycache__',
            'venv',
            'node_modules',
            'bck',  # Exclude backup directory itself
            'log',  # Exclude log directory
            'tmp'  # Exclude temp directory
        }

        # Check filename/dirname exclusions
        name_exclusions = {
            '.env',
            '.pyc',
            '.pyo',
            '.pyd',
            '.so',
            '.dll',
            '.dylib',
            '.DS_Store',
            '.zip'  # Exclude ZIP files to prevent recursive backups
        }

        # Check if the path contains any excluded directory
        path_parts = Path(path).parts
        if any(excl in path_parts for excl in path_exclusions):
            return True

        # Check name exclusions
        if name.startswith('.') or name.startswith('~'):
            return True

        if any(name.endswith(excl) for excl in name_exclusions):
            return True

        return False

    @TGBFPlugin.owner(hidden=True)
    @TGBFPlugin.private(hidden=True)
    @TGBFPlugin.logging()
    @TGBFPlugin.send_typing()
    async def backup_callback(self, update: Update, context: CallbackContext):
        if not update.message:
            return

        # Validate command argument
        command = ""
        if len(context.args) == 1:
            command = context.args[0].lower().strip()
            if not self.is_enabled(command):
                await update.message.reply_text(f"{con.ERROR} Plugin '{command}' not available")
                return

        # Send initial status
        status_message = await update.message.reply_text(f"{con.WAIT} Creating backup...")

        try:
            # Prepare backup path
            Path.mkdir(con.DIR_BCK, exist_ok=True)
            filename = f"{time.strftime('%Y%m%d%H%M%S')}{command}.zip"
            filepath = Path(con.DIR_BCK) / filename

            # Determine base directory
            base_dir = Path(con.DIR_PLG) / command if command else Path.cwd()
            self.log.debug(f"Starting backup of {base_dir} to {filepath}")

            # Create backup archive
            file_count = 0
            try:
                with zipfile.ZipFile(filepath, "w", compression=zipfile.ZIP_DEFLATED) as zf:
                    # Walk through directory tree
                    for root, dirs, files in os.walk(base_dir, topdown=True):
                        root_path = Path(root)

                        # Filter directories in-place
                        dirs[:] = [d for d in dirs if not self.should_exclude(str(root_path / d), d)]

                        # Add files that shouldn't be excluded
                        for file in files:
                            file_path = root_path / file
                            if not self.should_exclude(str(file_path), file):
                                arcname = file_path.relative_to(base_dir)
                                try:
                                    zf.write(file_path, arcname)
                                    file_count += 1
                                    if file_count % 20 == 0:  # Update status every 20 files
                                        await status_message.edit_text(
                                            f"{con.WAIT} Creating backup... ({file_count} files)"
                                        )
                                    self.log.debug(f"Added to backup: {arcname}")
                                except Exception as e:
                                    self.log.error(f"Failed to add file {file_path}: {e}")

            except Exception as e:
                self.log.error(f"ZIP creation failed: {e}")
                await status_message.edit_text(f"{con.ERROR} Failed to create ZIP: {str(e)}")
                if filepath.exists():
                    filepath.unlink()
                return

            self.log.debug(f"ZIP creation completed with {file_count} files")

            # Update status before sending
            await status_message.edit_text(f"{con.WAIT} Sending backup file ({file_count} files)...")

            # Send file
            self.log.debug("Starting file upload to Telegram...")
            try:
                with open(filepath, 'rb') as f:
                    await context.bot.send_document(
                        chat_id=update.effective_user.id,
                        caption=f"{con.DONE} Backup created with {file_count} files",
                        document=f,
                        filename=filename,
                        read_timeout=30,  # Lower timeouts to fail faster if stuck
                        write_timeout=30,
                        connect_timeout=30,
                        pool_timeout=30
                    )
                self.log.debug("File successfully sent to Telegram")

            except Exception as e:
                self.log.error(f"Failed to send file: {e}")
                await status_message.edit_text(
                    f"{con.ERROR} Could not send backup file: {str(e)}\n"
                    f"The backup was created at: {filepath}"
                )
                return
            finally:
                # Cleanup backup file
                if filepath.exists():
                    filepath.unlink()
                    self.log.debug("Backup file cleaned up")

            # Success - remove status message
            await status_message.delete()

        except Exception as e:
            self.log.error(f"Backup process failed: {e}")
            await status_message.edit_text(f"{con.ERROR} Backup failed: {str(e)}")
            # Final cleanup attempt
            if filepath.exists():
                filepath.unlink()