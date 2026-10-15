"""Build an audited GitHub source archive; never copy runtime data."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import sys
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.release_rules import MANIFEST_NAME, PROJECT_NAME, check_sources, source_files


def build_archive(output: Path, root: Path = ROOT) -> Path:
    root = root.resolve()
    content = {path.relative_to(root).as_posix(): path.read_bytes() for path in source_files(root)}
    errors = check_sources(content)
    if errors:
        raise ValueError("Source checks failed:\n" + "\n".join(errors))
    version_match = re.search(r'__version__\s*=\s*["\']([^"\']+)', content["axis/__init__.py"].decode("utf-8"))
    manifest = {
        "project": PROJECT_NAME,
        "version": version_match[1] if version_match else "unknown",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "format": "source-only",
        "files": {name: {"size": len(raw), "sha256": hashlib.sha256(raw).hexdigest()} for name, raw in content.items()},
    }
    output = Path(output).resolve()
    output.mkdir(parents=True, exist_ok=True)
    destination = output / f"{PROJECT_NAME}-source.zip"
    temporary = destination.with_suffix(".zip.tmp")
    try:
        with zipfile.ZipFile(temporary, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
            for relative, raw in content.items():
                archive.writestr(f"{PROJECT_NAME}/{relative}", raw)
            archive.writestr(f"{PROJECT_NAME}/{MANIFEST_NAME}", json.dumps(manifest, ensure_ascii=False, indent=2).encode("utf-8"))
        temporary.replace(destination)
    finally:
        temporary.unlink(missing_ok=True)
    checksum = hashlib.sha256(destination.read_bytes()).hexdigest()
    destination.with_suffix(".zip.sha256").write_text(f"{checksum}  {destination.name}\n", encoding="ascii")
    return destination


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / "dist")
    args = parser.parse_args()
    destination = build_archive(args.output)
    print(f"Source archive: {destination}")
    print(f"SHA256: {destination.with_suffix('.zip.sha256')}")


if __name__ == "__main__":
    main()
