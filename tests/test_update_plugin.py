from pathlib import Path
from zipfile import ZipFile, ZipInfo

import pytest

from plg.update.safe_zip import safe_extract_plugin_zip


def _write_zip(path: Path, entries: dict[str, str | bytes], *, symlink: str | None = None) -> None:
    with ZipFile(path, "w") as archive:
        for name, content in entries.items():
            archive.writestr(name, content)
        if symlink is not None:
            info = ZipInfo(symlink)
            info.external_attr = 0o120777 << 16
            archive.writestr(info, "target.py")


def test_safe_extract_plugin_zip_extracts_regular_members(tmp_path: Path) -> None:
    zip_path = tmp_path / "plugin.zip"
    destination = tmp_path / "plugin"
    _write_zip(zip_path, {"main.py": "print('ok')\n", "res/config.json": "{}"})

    with ZipFile(zip_path) as archive:
        safe_extract_plugin_zip(archive, destination)

    assert (destination / "main.py").read_text() == "print('ok')\n"
    assert (destination / "res" / "config.json").read_text() == "{}"


@pytest.mark.parametrize(
    "member_name",
    [
        "../escape.py",
        "/absolute.py",
        r"..\escape.py",
        "nested/../../escape.py",
    ],
)
def test_safe_extract_plugin_zip_rejects_path_traversal(
    tmp_path: Path, member_name: str
) -> None:
    zip_path = tmp_path / "plugin.zip"
    destination = tmp_path / "plugin"
    _write_zip(zip_path, {member_name: "bad"})

    with ZipFile(zip_path) as archive, pytest.raises(ValueError):
        safe_extract_plugin_zip(archive, destination)

    assert not (tmp_path / "escape.py").exists()


def test_safe_extract_plugin_zip_rejects_symlinks(tmp_path: Path) -> None:
    zip_path = tmp_path / "plugin.zip"
    destination = tmp_path / "plugin"
    _write_zip(zip_path, {"main.py": "ok"}, symlink="link.py")

    with ZipFile(zip_path) as archive, pytest.raises(ValueError):
        safe_extract_plugin_zip(archive, destination)
