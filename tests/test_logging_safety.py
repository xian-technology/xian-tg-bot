import json
import os
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

from loguru import logger

from plg.error.error import Error
from plugin import TGBFPlugin


async def test_sql_logging_never_contains_parameters_or_literal_query(tmp_path: Path) -> None:
    records: list[str] = []
    handler = logger.add(records.append, level="DEBUG", format="{message} {extra}")
    plugin = TGBFPlugin.__new__(TGBFPlugin)
    plugin.notify = AsyncMock()
    try:
        result = await plugin._exec_on_db(tmp_path / "test.db", "SELECT ?", "dummy-wallet-secret")
        assert result["success"] is True
        await plugin._exec_on_db(tmp_path / "test.db", "SELECT 'dummy-literal-secret'")
        output = "".join(records)
        assert "dummy-wallet-secret" not in output
        assert "dummy-literal-secret" not in output
        assert "SELECT" in output
    finally:
        logger.remove(handler)


async def test_error_report_contains_incident_context_without_payloads() -> None:
    records: list[str] = []
    handler = logger.add(records.append)
    plugin = Error.__new__(Error)
    plugin._cfg_global = {"admin_tg_id": 123}
    try:
        try:
            raise ValueError("dummy-error-secret")
        except ValueError as error:
            context = SimpleNamespace(error=error, chat_data={"data": "dummy-chat-secret"},
                                      user_data={"privkey": "dummy-wallet-secret"},
                                      bot=SimpleNamespace(send_message=AsyncMock()))
            await plugin.init_callback(SimpleNamespace(update_id=45, payload="dummy-update-secret"), context)
        sent = context.bot.send_message.call_args.kwargs["text"]
        output = "".join(records) + sent
        for secret in ("dummy-error-secret", "dummy-chat-secret", "dummy-wallet-secret", "dummy-update-secret"):
            assert secret not in output
        assert "ValueError" in output
        assert "incident_id=" in sent
        assert "test_logging_safety.py" in output
    finally:
        logger.remove(handler)


def test_bounded_file_retention_rotation_and_idle_expiry(tmp_path: Path) -> None:
    from logging_setup import BoundedLogFile

    old = tmp_path / "2026-01-01_00-00-00_000001.log"
    old.write_text("legacy")
    os.utime(old, (time.time() - 100, time.time() - 100))
    sink = BoundedLogFile(tmp_path, max_file_bytes=64, max_total_bytes=160,
                          retention_seconds=0.1, cleanup_interval=0.02)
    try:
        assert not old.exists()
        for _ in range(20):
            sink.write("record " + "x" * 30 + "\n")
            logs = list(tmp_path.glob("*.log"))
            assert all(path.stat().st_size <= 64 for path in logs)
            assert sum(path.stat().st_size for path in logs) <= 160
        deadline = time.monotonic() + 2
        while list(tmp_path.glob("*.log")) and time.monotonic() < deadline:
            time.sleep(0.02)
        assert list(tmp_path.glob("*.log")) == []
    finally:
        sink.stop()
    assert not sink.worker.is_alive()


def test_log_format_is_utc_single_line_and_exception_has_no_locals(tmp_path: Path) -> None:
    from logging_setup import configure_logging

    configure_logging("DEBUG", True, directory=tmp_path)
    try:
        logger.bind(update_id=17).info("first\nforged log")
        try:
            raise ValueError("dummy-error-secret")
        except ValueError:
            logger.exception("operation failed")
        output = (tmp_path / "bot.log").read_text()
        lines = output.splitlines()
        assert len(lines) == 2
        assert "Z | INFO | tg-bot/" in lines[0]
        assert "update_id" in lines[0] and "17" in lines[0]
        assert "dummy-error-secret" not in output
        assert "ValueError" in output
    finally:
        logger.remove()


def test_pm2_uses_only_bounded_application_log() -> None:
    app = json.loads(Path("pm2.config.json").read_text())["apps"][0]
    assert app["out_file"] == "/dev/null"
    assert app["error_file"] == "/dev/null"
    assert app["env"]["LOG_INTO_FILE"] == "true"


async def test_update_decorator_preserves_result_without_serializing_update() -> None:
    records: list[str] = []
    handler = logger.add(records.append, format="{message} {extra}")

    @TGBFPlugin.logging()
    async def handle(self: object, update: object, context: object) -> int:
        return 42

    try:
        result = await handle(None, SimpleNamespace(update_id=123, text="dummy-update-secret"), None)
        assert result == 42
        output = "".join(records)
        assert "dummy-update-secret" not in output
        assert "123" in output and "handle" in output
    finally:
        logger.remove(handler)


def test_bootstrap_failure_is_persisted_without_exception_payload(tmp_path: Path) -> None:
    import subprocess
    import sys

    root = Path(__file__).resolve().parents[1]
    script = f"""
import sys
sys.path.insert(0, {str(root)!r})
class BrokenDependency:
    def find_spec(self, fullname, path, target=None):
        if fullname == 'main':
            raise ImportError('dummy-import-secret')
sys.meta_path.insert(0, BrokenDependency())
from run_bot import run
raise SystemExit(run())
"""
    result = subprocess.run([sys.executable, "-c", script], cwd=tmp_path, capture_output=True, text=True)
    assert result.returncode == 1
    output = (tmp_path / "log" / "bot.log").read_text()
    assert "ImportError" in output and "incident_id=" in output
    assert "dummy-import-secret" not in output + result.stderr


def test_third_party_http_log_never_records_token_urls(tmp_path: Path) -> None:
    import logging

    from logging_setup import OperationalHandler, configure_logging

    configure_logging("INFO", True, directory=tmp_path)
    try:
        record = logging.LogRecord("httpx", logging.INFO, __file__, 1,
                                   "HTTP Request: GET https://api.telegram.org/bot%s/getMe", ("dummy-token",), None)
        OperationalHandler().emit(record)
        output = (tmp_path / "bot.log").read_text()
        assert "dummy-token" not in output and "api.telegram.org" not in output
        assert "httpx" in output
    finally:
        logger.remove()


def test_single_record_unicode_cap_and_restart_retention(tmp_path: Path) -> None:
    from logging_setup import BoundedLogFile

    sink = BoundedLogFile(tmp_path, max_file_bytes=64, max_total_bytes=128)
    try:
        sink.write("🌞" * 100)
        output = (tmp_path / "bot.log").read_bytes()
        assert len(output) <= 64
        assert output.decode().endswith(" [truncated]\n")
    finally:
        sink.stop()
    oversized = tmp_path / "2026-01-01_00-00-00_000001.log"
    oversized.write_bytes(b"x" * 129)
    restarted = BoundedLogFile(tmp_path, max_file_bytes=64, max_total_bytes=128)
    try:
        assert not oversized.exists()
        assert (tmp_path / "bot.log").read_bytes() == output
    finally:
        restarted.stop()


def test_retention_boundary_preserves_current_and_newest_archive(tmp_path: Path) -> None:
    from logging_setup import BoundedLogFile

    sink = BoundedLogFile(tmp_path, max_file_bytes=32, max_total_bytes=64)
    try:
        sink.write("a" * 32)
        assert len(list(tmp_path.glob("*.log"))) == 1
        sink.write("b" * 32)
        assert sorted(path.read_text() for path in tmp_path.glob("*.log")) == ["a" * 32, "b" * 32]
        sink.write("c" * 32)
        assert sorted(path.read_text() for path in tmp_path.glob("*.log")) == ["b" * 32, "c" * 32]
        assert (tmp_path / "bot.log").read_text() == "c" * 32
    finally:
        sink.stop()


def test_http_context_retains_only_valid_method_and_status() -> None:
    import logging

    from logging_setup import _http_context

    for component, args in (("httpx", ("POST", "dummy-url", "HTTP/1.1", 200, "OK")),
                            ("uvicorn.access", ("dummy-client", "GET", "dummy-url", "1.1", 404))):
        record = logging.LogRecord(component, 20, "", 0, "", args, None)
        expected_method = "POST" if component == "httpx" else "GET"
        expected_status = 200 if component == "httpx" else 404
        assert _http_context(record) == {"method": expected_method, "http_status": expected_status}
    invalid_cases: list[tuple[object, ...]] = [(), (None, None, None, None, None), ([], None, None, 999, None)]
    for invalid_args in invalid_cases:
        record = logging.LogRecord("httpx", 20, "", 0, "", invalid_args, None)
        assert _http_context(record) == {}


def test_bootstrap_normal_and_failure_paths_are_safe(tmp_path: Path) -> None:
    import logging
    import sys

    import pytest

    from run_bot import run

    # No bot, network, or Telegram initialization: the whole imported application is a stub.
    with pytest.MonkeyPatch.context() as patch:
        patch.chdir(tmp_path)
        handlers = logging.getLogger().handlers[:]
        level = logging.getLogger().level
        try:
            patch.setitem(sys.modules, "main", SimpleNamespace(run=lambda: 7))
            assert run() == 7

            def broken() -> None:
                raise RuntimeError("dummy-runtime-secret")

            patch.setitem(sys.modules, "main", SimpleNamespace(run=broken))
            assert run() == 1
            output = (tmp_path / "log" / "bot.log").read_text()
            assert "RuntimeError" in output and "dummy-runtime-secret" not in output

            def exits() -> None:
                raise SystemExit(3)

            patch.setitem(sys.modules, "main", SimpleNamespace(run=exits))
            with pytest.raises(SystemExit) as exc:
                run()
            assert exc.value.code == 3
            assert "SystemExit" in (tmp_path / "log" / "bot.log").read_text()
        finally:
            logging.getLogger().handlers = handlers
            logging.getLogger().setLevel(level)


def test_invalid_log_limits_and_symlinks_are_rejected_or_ignored(tmp_path: Path) -> None:
    import pytest

    from logging_setup import BoundedLogFile

    for limits in ({"max_file_bytes": 0}, {"max_file_bytes": 3, "max_total_bytes": 2},
                   {"retention_seconds": 0}, {"cleanup_interval": 0}):
        with pytest.raises(ValueError, match="retention"):
            BoundedLogFile(tmp_path, **limits)
    external = tmp_path / "keep.txt"
    external.write_text("keep")
    (tmp_path / "external.log").symlink_to(external)
    sink = BoundedLogFile(tmp_path, max_file_bytes=3, max_total_bytes=3)
    try:
        sink.write("abc")
        assert external.read_text() == "keep"
        assert (tmp_path / "external.log").is_symlink()
    finally:
        sink.stop()


def test_frame_locations_are_bounded_without_source_or_exception_text() -> None:
    from logging_setup import frame_locations

    def recurse(depth: int) -> None:
        if depth:
            recurse(depth - 1)
        else:
            raise ValueError("dummy-secret")

    try:
        recurse(20)
    except ValueError as error:
        frames = frame_locations(error.__traceback__)
    assert len(frames.split(";")) == 8
    assert "dummy-secret" not in frames


def test_retention_prefers_archives_and_expires_exact_cutoff(tmp_path: Path) -> None:
    import pytest

    from logging_setup import BoundedLogFile

    directory = tmp_path / "nested" / "logs"
    sink = BoundedLogFile(directory, max_file_bytes=32, max_total_bytes=64, retention_seconds=100)
    try:
        active = directory / "bot.log"
        active.write_bytes(b"a" * 32)
        archive = directory / "bot.101.log"
        archive.write_bytes(b"b" * 32)
        os.utime(active, (950, 950))
        os.utime(archive, (990, 990))
        with pytest.MonkeyPatch.context() as patch:
            patch.setattr(time, "time", lambda: 1000)
            sink.cleanup()
            assert active.exists() and archive.exists()
            other = directory / "bot.102.log"
            other.write_bytes(b"c" * 32)
            os.utime(other, (980, 980))
            sink.cleanup()
            assert active.exists() and archive.exists() and not other.exists()
            os.utime(archive, (900, 900))
            sink.cleanup()
            assert not archive.exists()
    finally:
        sink.stop()


def test_exact_file_size_append_and_tiny_record_cap(tmp_path: Path) -> None:
    from logging_setup import BoundedLogFile

    sink = BoundedLogFile(tmp_path, max_file_bytes=32, max_total_bytes=64)
    try:
        sink.write("a" * 16)
        sink.write("b" * 16)
        assert [p.read_text() for p in tmp_path.glob("*.log")] == ["a" * 16 + "b" * 16]
    finally:
        sink.stop()
    tiny = BoundedLogFile(tmp_path / "tiny", max_file_bytes=1, max_total_bytes=1)
    try:
        tiny.write("long message")
        assert (tmp_path / "tiny" / "bot.log").stat().st_size == 1
    finally:
        tiny.stop()


def test_standard_logging_routes_existing_handlers_and_exception_safely(tmp_path: Path) -> None:
    import logging

    from logging_setup import configure_logging, configure_standard_logging

    names = ("", "uvicorn", "uvicorn.error", "uvicorn.access")
    previous = {name: (logging.getLogger(name).handlers[:], logging.getLogger(name).level,
                       logging.getLogger(name).propagate) for name in names}
    configure_logging("INFO", True, directory=tmp_path)
    try:
        logging.getLogger().handlers = [logging.NullHandler()]
        logging.getLogger("uvicorn.error").propagate = False
        configure_standard_logging()
        try:
            raise ValueError("dummy-exception-secret")
        except ValueError:
            logging.getLogger("uvicorn.error").exception("dummy-log-secret")
        logging.getLogger("uvicorn.access").info("%s %s %s %s %s", "client", "POST", "dummy-url", "1.1", 599)
        output = (tmp_path / "bot.log").read_text()
        assert "ValueError" in output and "uvicorn.error" in output
        assert '"http_status": "599"' in output and '"method": "POST"' in output
        assert "dummy-" not in output
    finally:
        logger.remove()
        for name, (handlers, level, propagate) in previous.items():
            target = logging.getLogger(name)
            target.handlers = handlers
            target.setLevel(level)
            target.propagate = propagate


def test_bootstrap_file_optout_and_successful_systemexit(tmp_path: Path) -> None:
    import logging
    import sys

    import pytest

    from run_bot import run

    handlers = logging.getLogger().handlers[:]
    level = logging.getLogger().level
    try:
        with pytest.MonkeyPatch.context() as patch:
            patch.chdir(tmp_path)
            patch.setenv("LOG_INTO_FILE", "false")
            patch.setitem(sys.modules, "main", SimpleNamespace(run=lambda: 0))
            assert run() == 0
            assert not (tmp_path / "log").exists()

            def exits() -> None:
                raise SystemExit(0)

            patch.setitem(sys.modules, "main", SimpleNamespace(run=exits))
            with pytest.raises(SystemExit) as exc:
                run()
            assert exc.value.code == 0
            assert not (tmp_path / "log").exists()
    finally:
        logging.getLogger().handlers = handlers
        logging.getLogger().setLevel(level)


def test_http_informational_status_is_preserved() -> None:
    import logging

    from logging_setup import _http_context

    record = logging.LogRecord("httpx", 20, "", 0, "", ("GET", "url", "1.1", 100, "Continue"), None)
    assert _http_context(record) == {"method": "GET", "http_status": 100}


def test_third_party_os_error_keeps_errno_without_message(tmp_path: Path) -> None:
    import errno
    import logging

    from logging_setup import OperationalHandler, configure_logging

    configure_logging("INFO", True, directory=tmp_path)
    try:
        error = OSError(errno.EADDRINUSE, "dummy-bind-secret")
        record = logging.LogRecord("uvicorn.error", logging.ERROR, __file__, 1,
                                   "startup failed: %s", (error,), None)
        OperationalHandler().emit(record)
        output = (tmp_path / "bot.log").read_text()
        assert "dummy-bind-secret" not in output
        assert "OSError" in output and f'"error_code": "{errno.EADDRINUSE}"' in output
        assert "EADDRINUSE" in output and " | ERROR | " in output
    finally:
        logger.remove()


async def test_generic_exception_notification_omits_payload_and_logs_delivery_failure() -> None:
    plugin = TGBFPlugin.__new__(TGBFPlugin)
    plugin._cfg_global = {"admin_tg_id": 123}
    send = AsyncMock()
    plugin._tgb = SimpleNamespace(bot=SimpleNamespace(updater=SimpleNamespace(bot=SimpleNamespace(send_message=send))))
    records: list[str] = []
    handler = logger.add(records.append, format="{message} {extra}")
    try:
        assert await plugin.notify(ValueError("dummy-notification-secret")) is True
        message = send.call_args.args[1]
        assert "dummy-notification-secret" not in message
        assert "ValueError" in message and "incident_id=" in message
        send.side_effect = RuntimeError("dummy-delivery-secret")
        assert await plugin.notify("Safe status") is False
        assert "dummy-delivery-secret" not in "".join(records)
        assert "RuntimeError" in "".join(records)
    finally:
        logger.remove(handler)


def test_retention_tolerates_operator_removing_snapshot_files(tmp_path: Path) -> None:
    import pytest

    from logging_setup import BoundedLogFile

    sink = BoundedLogFile(tmp_path, max_file_bytes=32, max_total_bytes=64)
    try:
        snapshot = sink._files
        for expired in (False, True):
            victim = tmp_path / "bot.100.log"
            victim.write_bytes(b"x" * 32)
            if expired:
                os.utime(victim, (0, 0))
            else:
                (tmp_path / "bot.101.log").write_bytes(b"y" * 32)
                (tmp_path / "bot.102.log").write_bytes(b"z" * 32)

            def scan_then_remove() -> list[tuple[Path, os.stat_result]]:
                files = snapshot()
                victim.unlink()
                return files

            with pytest.MonkeyPatch.context() as patch:
                patch.setattr(sink, "_files", scan_then_remove)
                sink.cleanup()
            assert not victim.exists()
    finally:
        sink.stop()


def test_default_retention_policy_and_safe_context_allowlist(tmp_path: Path) -> None:
    from logging_setup import BoundedLogFile, _prepare_record

    sink = BoundedLogFile(tmp_path)
    try:
        assert sink.max_file_bytes == 50 * 1024 * 1024
        assert sink.max_total_bytes == 250 * 1024 * 1024
        assert sink.retention_seconds == 7 * 24 * 60 * 60
        assert sink.worker.daemon is True
    finally:
        sink.stop()
    record: dict[str, Any] = {"extra": {"user_data": "dummy-secret", "update_id": 123, "handler": object()},
              "exception": None, "message": "safe"}
    assert _prepare_record(record) is True
    assert json.loads(record["extra"]["context"]) == {"update_id": "123"}


async def test_generic_exception_notification_escapes_html_frame_names() -> None:
    plugin = TGBFPlugin.__new__(TGBFPlugin)
    plugin._cfg_global = {"admin_tg_id": 123}
    send = AsyncMock()
    plugin._tgb = SimpleNamespace(bot=SimpleNamespace(updater=SimpleNamespace(bot=SimpleNamespace(send_message=send))))
    try:
        exec(compile("raise ValueError('dummy-secret')", "safe_probe.py", "exec"))
    except ValueError as error:
        assert await plugin.notify(error) is True
    message = send.call_args.args[1]
    assert "&lt;module&gt;" in message
    assert "<module>" not in message and "dummy-secret" not in message


async def test_sql_error_preserves_result_but_not_sensitive_diagnostics(tmp_path: Path) -> None:
    records: list[str] = []
    handler = logger.add(records.append, format="{message} {extra}")
    plugin = TGBFPlugin.__new__(TGBFPlugin)
    plugin.notify = AsyncMock()
    try:
        result = await plugin._exec_on_db(tmp_path / "test.db", "dummy-query-secret")
        assert result["success"] is False
        assert "dummy" in result["data"]
        assert "dummy-query-secret" not in "".join(records)
        notification = plugin.notify.call_args.args[0]
        assert "dummy-query-secret" not in notification and "incident_id=" in notification
        assert "OperationalError" in "".join(records)
    finally:
        logger.remove(handler)


def test_low_volume_continuous_writes_rotate_hourly_and_expire(tmp_path: Path) -> None:
    import pytest

    from logging_setup import BoundedLogFile

    now = [1_800_000_000.0]
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(time, "time", lambda: now[0])
        sink = BoundedLogFile(tmp_path)
        try:
            for hour in range(24 * 9):
                now[0] = 1_800_000_000.0 + hour * 3600
                sink.write(f"hour={hour}\n")
            output = "".join(path.read_text() for path in tmp_path.glob("bot*.log"))
            assert "hour=0\n" not in output
            assert "hour=215\n" in output
            assert len(list(tmp_path.glob("bot*.log"))) > 1
        finally:
            sink.stop()


def test_log_cleanup_preserves_unrelated_files(tmp_path: Path) -> None:
    from logging_setup import BoundedLogFile

    unrelated = [tmp_path / name for name in ("other.log", "bot.notes.log", "backup.log")]
    for path in unrelated:
        path.write_text("keep")
        os.utime(path, (0, 0))
    sink = BoundedLogFile(tmp_path, max_file_bytes=3, max_total_bytes=3)
    try:
        sink.write("abc")
        sink.cleanup()
        assert all(path.read_text() == "keep" for path in unrelated)
    finally:
        sink.stop()


def test_normal_log_write_does_not_scan_directory_or_stat_active(tmp_path: Path) -> None:
    import pytest

    from logging_setup import BoundedLogFile

    sink = BoundedLogFile(tmp_path)
    try:
        sink.write("first\n")
        with pytest.MonkeyPatch.context() as patch:
            patch.setattr(sink, "_files", lambda: (_ for _ in ()).throw(AssertionError("directory scan")))
            patch.setattr(Path, "stat", lambda *a, **k: (_ for _ in ()).throw(AssertionError("stat")))
            sink.write("second\n")
        assert (tmp_path / "bot.log").read_text() == "first\nsecond\n"
    finally:
        sink.stop()


def test_control_escaping_and_huge_integer_context_are_bounded() -> None:
    from logging_setup import _prepare_record, _single_line

    assert _single_line("a\u2028b\u2029c\x85d\x01e\u202e\u2066\ud800\U000e0001", 100) == r"a\u2028b\u2029c\x85d\x01e\u202e\u2066\ud800\U000e0001"
    record: dict[str, Any] = {"extra": {"update_id": 1 << 1_000_000}, "exception": None, "message": "safe"}
    assert _prepare_record(record) is True
    assert json.loads(record["extra"]["context"])["update_id"] == "[integer exceeds 256 bits]"


def test_bootstrap_timestamp_has_same_millisecond_utc_shape(tmp_path: Path) -> None:
    import re

    from run_bot import record_fatal

    record_fatal(ValueError("dummy"), tmp_path)
    assert re.match(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z \| CRITICAL \|", (tmp_path / "bot.log").read_text())


def test_restart_uses_oldest_record_timestamp_and_reserves_active_budget(tmp_path: Path) -> None:
    from datetime import UTC, datetime

    import pytest

    from logging_setup import BoundedLogFile

    start = 1_800_000_000.0
    first_line = datetime.fromtimestamp(start, UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z") + " | INFO | old\n"
    (tmp_path / "bot.log").write_text(first_line)
    # A newer mtime must not make old records young after a process restart.
    os.utime(tmp_path / "bot.log", (start + 8 * 86400, start + 8 * 86400))
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(time, "time", lambda: start + 8 * 86400)
        sink = BoundedLogFile(tmp_path, max_file_bytes=64, max_total_bytes=128)
        try:
            assert not (tmp_path / "bot.log").exists()
            sink.write("new")
            assert (tmp_path / "bot.log").read_text() == "new"
        finally:
            sink.stop()


async def test_error_notification_bounds_oversized_update_id() -> None:
    plugin = Error.__new__(Error)
    plugin._cfg_global = {"admin_tg_id": 123}
    context = SimpleNamespace(error=ValueError("dummy-secret"), bot=SimpleNamespace(send_message=AsyncMock()))
    await plugin.init_callback(SimpleNamespace(update_id=1 << 1_000_000), context)
    message = context.bot.send_message.call_args.kwargs["text"]
    assert "[integer exceeds 256 bits]" in message
    assert "dummy-secret" not in message


async def test_main_plugin_import_error_is_logged_without_exception_payload() -> None:
    from main import TelegramBot

    bot = TelegramBot()
    bot._load_plugin_module = AsyncMock(side_effect=RuntimeError("dummy-main-token-url"))
    records: list[str] = []
    handler = logger.add(records.append, format="{message} {extra}")
    try:
        success, reason = await bot.enable_plugin("dummy")
        assert success is False and reason == "dummy-main-token-url"
        output = "".join(records)
        assert "dummy-main-token-url" not in output
        assert "RuntimeError" in output
    finally:
        logger.remove(handler)


async def test_graphql_exception_logging_does_not_echo_token_url() -> None:
    import pytest

    import plugin as plugin_module

    class NoNetworkSession:
        async def __aenter__(self) -> "NoNetworkSession":
            return self

        async def __aexit__(self, *args: object) -> None:
            return None

        def post(self, *args: object, **kwargs: object) -> None:
            raise RuntimeError("dummy-graphql-token-url")

    bot_plugin = TGBFPlugin.__new__(TGBFPlugin)
    records: list[str] = []
    handler = logger.add(records.append, format="{message} {extra}")
    try:
        with pytest.MonkeyPatch.context() as patch:
            patch.setattr(plugin_module.aiohttp, "ClientSession", NoNetworkSession)
            with pytest.raises(RuntimeError, match="dummy-graphql-token-url"):
                await bot_plugin.fetch_graphql("query {}", endpoint="http://unused.invalid")
        output = "".join(records)
        assert "dummy-graphql-token-url" not in output
        assert "RuntimeError" in output
    finally:
        logger.remove(handler)


async def test_websocket_logs_keep_status_without_error_reason_or_event_payload() -> None:
    from plg.event.event import Event

    event = Event.__new__(Event)
    event.event = "Tx"
    records: list[str] = []
    handler = logger.add(records.append, level="DEBUG", format="{message} {extra}")
    try:
        await event.on_error(ValueError("dummy-websocket-token-url"))
        await event.on_close(None, 1000, "dummy-close-payload")
        await event.on_message(None, '{"result": null, "data": "dummy-event-payload"}')
        output = "".join(records)
        assert "dummy-" not in output
        assert "ValueError" in output and "1000" in output
        assert event.is_connected is False
    finally:
        logger.remove(handler)


async def test_send_logs_transaction_identity_without_sdk_message_payload() -> None:
    import inspect

    from xian_py.models import TransactionSubmission
    from xian_py.wallet import Wallet

    from plg.send.send import Send

    submission = TransactionSubmission.from_dict({"submitted": True, "accepted": True,
        "tx_hash": "ABC123", "mode": "checktx", "message": "dummy-receipt-payload"})
    sender = Send.__new__(Send)
    sender.get_wallet = AsyncMock(return_value=Wallet("1" * 64))
    client = SimpleNamespace(send=AsyncMock(return_value=submission))
    sender.get_xian = AsyncMock(return_value=client)
    sender.confirm_tx = AsyncMock()
    message = SimpleNamespace(edit_text=AsyncMock())
    update = SimpleNamespace(message=SimpleNamespace(from_user=SimpleNamespace(id=1),
                                                    reply_text=AsyncMock(return_value=message)))
    receiver = Wallet("2" * 64).public_key
    records: list[str] = []
    handler = logger.add(records.append, level="DEBUG", format="{message} {extra}")
    try:
        await inspect.unwrap(Send.send_callback)(sender, update, SimpleNamespace(args=["1", receiver]))
        output = "".join(records)
        assert "dummy-receipt-payload" not in output
        assert "ABC123" in output
        client.send.assert_awaited_once()
        sender.confirm_tx.assert_awaited_once()
        assert sender.confirm_tx.call_args.args[1] is submission
    finally:
        logger.remove(handler)


def test_transaction_context_projects_only_valid_public_metadata() -> None:
    from logging_setup import transaction_context

    assert transaction_context({"tx_hash": "ABC123", "status": 0, "result": "dummy-secret"}) == {"tx_hash": "ABC123", "status": 0}
    assert transaction_context({"tx_hash": "dummy-secret", "status": "dummy-secret"}) == {}
    assert transaction_context(SimpleNamespace(tx_hash="def456", accepted=False, message="dummy-secret")) == {"tx_hash": "def456", "status": False}


def test_hourly_rotation_survives_idle_maintenance_and_collision(tmp_path: Path) -> None:
    import pytest

    from logging_setup import BoundedLogFile

    now = [1_800_000_000.0]
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(time, "time", lambda: now[0])
        sink = BoundedLogFile(tmp_path, max_file_bytes=64, max_total_bytes=256)
        try:
            sink.write("first\n")
            now[0] += 3599
            sink.cleanup()
            sink.write("before hour\n")
            assert len(list(tmp_path.glob("bot*.log"))) == 1
            (tmp_path / "bot.42.log").write_text("existing archive")
            os.utime(tmp_path / "bot.42.log", (now[0], now[0]))
            patch.setattr(time, "time_ns", lambda: 42)
            now[0] += 1
            sink.cleanup()
            sink.write("exact hour\n")
            assert (tmp_path / "bot.42.log").read_text() == "existing archive"
            assert (tmp_path / "bot.43.log").read_text() == "first\nbefore hour\n"
            assert (tmp_path / "bot.log").read_text() == "exact hour\n"
        finally:
            sink.stop()


def test_unicode_escape_spelling_integer_boundary_and_oversized_ascii_marker(tmp_path: Path) -> None:
    from logging_setup import BoundedLogFile, _context_text, _single_line

    assert _single_line("a\nb\rc\uffff", 100) == r"a\nb\rc\uffff"
    assert _context_text(1 << 255) == str(1 << 255)
    assert _context_text(1 << 256) == "[integer exceeds 256 bits]"
    sink = BoundedLogFile(tmp_path, max_file_bytes=32, max_total_bytes=64)
    try:
        sink.write("x" * 33)
        assert (tmp_path / "bot.log").read_text().endswith(" [truncated]\n")
    finally:
        sink.stop()


def test_bootstrap_frames_cannot_inject_control_characters(tmp_path: Path) -> None:
    from run_bot import record_fatal

    try:
        exec(compile("raise ValueError('dummy-secret')", "unsafe\n\u202eframe.py", "exec"))
    except ValueError as error:
        record_fatal(error, tmp_path)
    output = (tmp_path / "bot.log").read_text()
    assert len(output.splitlines()) == 1
    assert "\u202e" not in output and r"\u202e" in output
    assert "dummy-secret" not in output


def test_retention_continues_after_closed_stderr_with_safe_failure_metadata() -> None:
    import errno
    import sys
    from unittest.mock import Mock

    import pytest

    from logging_setup import BoundedLogFile

    diagnostics: list[str] = []

    class ClosedStream:
        def write(self, message: str) -> None:
            diagnostics.append(message)
            raise ValueError("I/O operation on closed file")

    sink = BoundedLogFile.__new__(BoundedLogFile)
    sink._stop = SimpleNamespace(wait=Mock(side_effect=[False, False, True]))
    sink.cleanup = Mock(side_effect=[OSError(errno.ENOSPC, "dummy-filesystem-secret"), None])
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(sys, "stderr", ClosedStream())
        sink._maintain(0.01)
    assert sink.cleanup.call_count == 2
    assert len(diagnostics) == 1
    assert "exception_type=OSError" in diagnostics[0]
    assert f"errno={errno.ENOSPC}" in diagnostics[0]
    assert "dummy-filesystem-secret" not in diagnostics[0]
