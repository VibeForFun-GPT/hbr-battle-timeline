"""Check the structure, source policy and checksums of a source archive."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath
import sys
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.release_rules import MANIFEST_NAME, PROJECT_NAME, check_sources


def check_archive(path: Path) -> list[str]:
    errors = []
    files = {}
    manifest = None
    with zipfile.ZipFile(path) as archive:
        seen = set()
        for item in archive.infolist():
            member = PurePosixPath(item.filename)
            if item.filename in seen:
                errors.append(f"Duplicate archive member: {item.filename}")
                continue
            seen.add(item.filename)
            if member.is_absolute() or not member.parts or member.parts[0] != PROJECT_NAME or ".." in member.parts or "\\" in item.filename:
                errors.append(f"Invalid archive member: {item.filename}")
                continue
            if item.is_dir():
                continue
            if item.file_size > 2 * 1024 * 1024:
                errors.append(f"Unexpectedly large source file: {item.filename}")
                continue
            relative = PurePosixPath(*member.parts[1:]).as_posix()
            raw = archive.read(item)
            if relative == MANIFEST_NAME:
                try:
                    manifest = json.loads(raw)
                except (ValueError, UnicodeDecodeError):
                    errors.append("Unreadable source manifest")
            else:
                files[relative] = raw
    errors.extend(check_sources(files))
    if not isinstance(manifest, dict) or not isinstance(manifest.get("files"), dict):
        errors.append("Missing or invalid source manifest")
    else:
        if set(manifest["files"]) != set(files):
            errors.append("Manifest file list does not match archive")
        for relative, raw in files.items():
            record = manifest["files"].get(relative, {})
            if not isinstance(record, dict) or record.get("sha256") != hashlib.sha256(raw).hexdigest() or record.get("size") != len(raw):
                errors.append(f"Manifest checksum mismatch: {relative}")
    sidecar = Path(path).with_suffix(".zip.sha256")
    if sidecar.is_file():
        fields = sidecar.read_text(encoding="ascii").split()
        if not fields or fields[0] != hashlib.sha256(Path(path).read_bytes()).hexdigest():
            errors.append("Archive SHA256 mismatch")
    return errors


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive", type=Path, required=True)
    args = parser.parse_args()
    errors = check_archive(args.archive)
    if errors:
        print("Release checks failed:\n" + "\n".join(errors), file=sys.stderr)
        raise SystemExit(1)
    print("Release checks passed: source structure, exclusions, syntax and checksums.")


if __name__ == "__main__":
    main()
