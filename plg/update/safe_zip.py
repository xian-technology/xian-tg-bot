import shutil
from pathlib import Path
from stat import S_IFLNK, S_IFMT
from zipfile import ZipFile


def safe_extract_plugin_zip(zip_file: ZipFile, destination: Path) -> None:
    destination = destination.resolve()

    for member in zip_file.infolist():
        filename = member.filename
        member_path = Path(filename)
        if (
            filename.startswith(("/", "\\"))
            or "\\" in filename
            or member_path.is_absolute()
            or member_path.drive
            or any(part in ("", ".", "..") for part in member_path.parts)
        ):
            raise ValueError(f"unsafe ZIP member path: {filename!r}")

        mode = member.external_attr >> 16
        if S_IFMT(mode) == S_IFLNK:
            raise ValueError(f"unsafe ZIP symlink member: {filename!r}")

        target = (destination / member_path).resolve()
        if destination != target and destination not in target.parents:
            raise ValueError(f"ZIP member escapes plugin directory: {filename!r}")

        if member.is_dir():
            target.mkdir(parents=True, exist_ok=True)
            continue

        target.parent.mkdir(parents=True, exist_ok=True)
        with zip_file.open(member, "r") as source, target.open("wb") as output:
            shutil.copyfileobj(source, output)
