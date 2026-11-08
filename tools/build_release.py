#!/usr/bin/env python3
"""Package only installable integration files, never local captures or caches."""

import json
import os
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile


def validate_release_tag(version, tag):
    if tag is not None and tag != version:
        raise ValueError(f"Release tag {tag!r} does not match manifest version {version!r}")


def main():
    root = Path(__file__).resolve().parents[1]
    source = root / "custom_components" / "telenot"
    version = json.loads((source / "manifest.json").read_text())["version"]
    tag = os.environ.get("GITHUB_REF_NAME") if os.environ.get("GITHUB_REF_TYPE") == "tag" else None
    validate_release_tag(version, tag)
    destination = root / "dist" / "telenot.zip"
    destination.parent.mkdir(exist_ok=True)
    with ZipFile(destination, "w", ZIP_DEFLATED) as archive:
        for path in sorted(source.rglob("*")):
            if path.is_file() and path.suffix in {".py", ".json", ".png"} and "__pycache__" not in path.parts:
                archive.write(path, path.relative_to(source.parent))
        archive.write(root / "LICENSE", "telenot/LICENSE")
    print(destination)


if __name__ == "__main__":
    main()
