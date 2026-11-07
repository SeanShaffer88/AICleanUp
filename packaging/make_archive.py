#!/usr/bin/env python3
"""Zip a staged folder into a portable archive.

`python packaging/make_archive.py <staged-folder> <output.zip>`

Timestamps are fixed and executable bits are preserved, so re-running the build
on the same inputs yields a byte-identical archive and the app stays runnable
after unzipping on macOS and Linux.
"""
from __future__ import annotations

import os
import sys
import zipfile
from pathlib import Path

FIXED_TIMESTAMP = (2024, 1, 1, 0, 0, 0)


def build_archive(source: Path, destination: Path) -> list[str]:
    if not source.is_dir():
        raise SystemExit(f"Nothing to archive: {source} is not a folder")
    files = sorted(path for path in source.rglob("*") if path.is_file())
    if not files:
        raise SystemExit(f"Nothing to archive: {source} is empty")
    destination.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(destination, "w", zipfile.ZIP_DEFLATED) as archive:
        for path in files:
            relative = path.relative_to(source).as_posix()
            info = zipfile.ZipInfo(relative, date_time=FIXED_TIMESTAMP)
            mode = 0o755 if os.access(path, os.X_OK) else 0o644
            info.external_attr = (0o100000 | mode) << 16
            info.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(info, path.read_bytes())
    return [path.relative_to(source).as_posix() for path in files]


def main(argv: list[str]) -> int:
    if len(argv) != 3:
        print(__doc__.strip(), file=sys.stderr)
        return 2
    source, destination = Path(argv[1]), Path(argv[2])
    members = build_archive(source, destination)
    size = destination.stat().st_size
    print(f"{destination} ({size:,} bytes, {len(members)} files)")
    for member in members:
        print(f"  {member}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
