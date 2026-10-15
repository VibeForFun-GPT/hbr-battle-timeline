"""Allowlisted source packaging and checks, with no third-party dependencies."""
from __future__ import annotations

import ast
import os
from pathlib import Path, PurePosixPath
import re

PROJECT_NAME = "hbr-battle-timeline"
MANIFEST_NAME = "RELEASE_MANIFEST.json"
ROOT_FILES = {
    ".gitignore", ".gitattributes", ".editorconfig", ".python-version",
    "README.md", "LICENSE", "THIRD_PARTY_NOTICES.md", "CONTRIBUTING.md",
    "SECURITY.md", "CHANGELOG.md", "requirements.txt", "app.py",
    "install.ps1", "start.cmd", "启动行动轴.cmd",
}
SOURCE_FOLDERS = {"axis", "web", "tools", "scripts", "tests", "docs", "examples", ".github"}
SOURCE_SUFFIXES = {".py", ".html", ".css", ".js", ".json", ".md", ".txt", ".yml", ".yaml", ".toml", ".ps1", ".cmd"}
EXCLUDED_PARTS = {".git", ".venv", "venv", ".analysis", "__pycache__", "node_modules", ".pytest_cache", ".ruff_cache"}
DATA_FILES = {"data/.gitkeep", "data/README.md"}
REQUIRED_FILES = {
    "app.py", "README.md", "LICENSE", "THIRD_PARTY_NOTICES.md",
    "requirements.txt", "install.ps1", "start.cmd", ".gitignore",
    "axis/__init__.py", "axis/pipeline.py", "axis/vision.py", "axis/avatar_names.py",
    "axis/live.py", "axis/export.py", "axis/model.py",
    "web/index.html", "web/app.js", "web/style.css",
    "tools/sync_character_catalog.py", "tools/make_source_archive.py", "tools/check_release.py",
    "scripts/release_rules.py", ".github/workflows/ci.yml",
}


def allowed(relative: str) -> bool:
    path = PurePosixPath(relative)
    if path.is_absolute() or any(part in ("..", "") for part in path.parts) or "\\" in relative or ":" in relative:
        return False
    if any(part in EXCLUDED_PARTS for part in path.parts):
        return False
    if relative in ROOT_FILES or relative in DATA_FILES:
        return True
    if not path.parts or path.parts[0] not in SOURCE_FOLDERS:
        return False
    if path.parts[:2] == ("tests", "fixtures"):
        return relative == "tests/fixtures/README.md"
    if any(part.startswith(".env") for part in path.parts):
        return False
    return path.suffix.lower() in SOURCE_SUFFIXES


def source_files(root: Path) -> list[Path]:
    root = root.resolve()
    files = []
    for current, directories, names in os.walk(root, followlinks=False):
        folder = Path(current)
        relative_folder = folder.relative_to(root)
        if folder == root:
            directories[:] = [name for name in directories if name in SOURCE_FOLDERS | {"data"} and not (folder / name).is_symlink()]
        elif relative_folder.parts[0] == "data" or relative_folder.parts[:2] == ("tests", "fixtures"):
            directories[:] = []
        else:
            directories[:] = [name for name in directories if name not in EXCLUDED_PARTS and not (folder / name).is_symlink()]
        for name in names:
            path = folder / name
            relative = path.relative_to(root).as_posix()
            if path.is_symlink():
                if allowed(relative):
                    raise ValueError(f"Source symlinks are not packaged: {relative}")
                continue
            if path.is_file() and allowed(relative):
                files.append(path)
    return sorted(files, key=lambda p: p.relative_to(root).as_posix())


def check_sources(files: dict[str, bytes]) -> list[str]:
    errors = []
    for required in sorted(REQUIRED_FILES - files.keys()):
        errors.append(f"Missing required source file: {required}")
    personal_path = re.compile(r"(?:[A-Za-z]:[\\/]+Users[\\/]+[^\\/\s'\"]+|/(?:Users|home)/[^/\s'\"]+)")
    credential = re.compile(r'''(?im)^\s*(?:api[_-]?key|access[_-]?token|auth[_-]?token|password)\s*[:=]\s*["'][^"'\r\n]{8,}["']''')
    for relative, raw in files.items():
        if not allowed(relative):
            errors.append(f"Excluded source path: {relative}")
            continue
        if b"\0" in raw:
            errors.append(f"Unexpected binary source file: {relative}")
            continue
        try:
            text = raw.decode("utf-8-sig")
        except UnicodeDecodeError:
            errors.append(f"Source is not UTF-8: {relative}")
            continue
        if personal_path.search(text):
            errors.append(f"Personal directory path found in: {relative}")
        if credential.search(text):
            errors.append(f"Possible hardcoded credential found in: {relative}")
        if relative.endswith(".py"):
            try:
                ast.parse(text, filename=relative)
            except SyntaxError as error:
                errors.append(f"Python syntax error in {relative}: {error.msg}")
        if relative.endswith(".cmd") and b"\n" in raw.replace(b"\r\n", b""):
            errors.append(f"Windows launcher needs CRLF line endings: {relative}")
    return errors
