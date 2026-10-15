"""Sync public Character names and Style Avatar assets from SeraphDB."""

from __future__ import annotations

import argparse
import concurrent.futures
import io
import json
import re
import sys
import time
import urllib.error
import urllib.request
import uuid
from datetime import datetime, timezone
from pathlib import Path

from PIL import Image


ROOT = Path(__file__).resolve().parents[1]
SOURCE = "https://hbr.quest/"
ENDPOINTS = {
    "characters": "https://master.hbr.quest/v1/characters.json",
    "styles": "https://master.hbr.quest/v1/styles.json",
    "localization": "https://master.hbr.quest/v1/zhTW.json",
}
CDN = "https://cdn.hbr.quest/webp/jp/card/"
HEADERS = {"User-Agent": "Mozilla/5.0", "Referer": SOURCE}


def fetch(url: str) -> bytes:
    last_error: Exception | None = None
    for attempt in range(3):
        try:
            request = urllib.request.Request(url, headers=HEADERS)
            with urllib.request.urlopen(request, timeout=35) as response:
                return response.read()
        except (OSError, urllib.error.URLError) as exc:
            last_error = exc
            if isinstance(exc, urllib.error.HTTPError) and exc.code in (401, 403, 404):
                break
            if attempt < 2:
                time.sleep(0.6 * (attempt + 1))
    raise RuntimeError(f"{url}: {last_error}")


def valid_image(data: bytes) -> None:
    # Decode RGB pixels as well as checking the image container.
    with Image.open(io.BytesIO(data)) as image:
        if image.width < 32 or image.height < 32:
            raise ValueError("Avatar image is too small")
        image.convert("RGB").load()


def atomic_write(path: Path, data: bytes) -> None:
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        temporary.write_bytes(data)
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def name_for(character: dict, translation: dict) -> tuple[str, str, str]:
    label = character["label"]
    parts = [translation.get(field, {}).get(label, "") for field in
             ("lastname", "middlename", "firstname")]
    localized = "".join(str(part).strip() for part in parts)
    if localized:
        alias = translation.get("firstname", {}).get(label) or localized
        return localized, alias, "zh-TW"
    japanese = character["name"].split(" — ", 1)[0].replace(" ", "").strip()
    return japanese, japanese, "ja-JP"


def sync_catalog(output=None, progress=None, *, workers: int = 5) -> dict:
    """Return the new manifest; preserve the previous manifest on any failed asset."""
    output = Path(output or ROOT / "data" / "character-catalog").resolve()
    if not 1 <= workers <= 6:
        raise ValueError("workers must be between 1 and 6")
    output.mkdir(parents=True, exist_ok=True)
    image_dir = output / "images"
    image_dir.mkdir(exist_ok=True)
    # Read the complete metadata before altering any existing manifest.
    with concurrent.futures.ThreadPoolExecutor(max_workers=3) as pool:
        futures = {key: pool.submit(fetch, url) for key, url in ENDPOINTS.items()}
        metadata = {key: json.loads(future.result()) for key, future in futures.items()}
    raw_characters = metadata["characters"]
    raw_styles = metadata["styles"]
    localization = metadata["localization"]
    if not isinstance(raw_characters, list) or not isinstance(raw_styles, list):
        raise ValueError("Unexpected Character / Style data format")
    if not raw_characters or not raw_styles:
        raise ValueError("Empty Character / Style data")
    # Another form has its own documented Character ID and Avatar.
    additional_forms = []
    for style in raw_styles:
        form = style.get("another")
        if form and form.get("image") and form.get("chara_label"):
            additional_forms.append({"label": form["label"], "name": form.get("name", style["name"]),
                                     "image": form["image"], "chara_label": form["chara_label"],
                                     "role": form.get("role", style.get("role", ""))})
    raw_styles = raw_styles + additional_forms
    translation = localization.get("character", {})
    style_names = localization.get("card", {}).get("name", {})
    characters = []
    for raw in raw_characters:
        name, alias, language = name_for(raw, translation)
        characters.append({"id": raw["label"], "name": name, "alias": alias,
                           "name_language": language})
    by_id = {character["id"]: character for character in characters}
    styles = []
    for raw in raw_styles:
        filename = raw["image"]
        if not re.fullmatch(r"[A-Za-z0-9_.-]+\.webp", filename):
            raise ValueError(f"Unexpected asset filename: {filename}")
        character = by_id[raw["chara_label"]]
        styles.append({
            "character_id": character["id"], "name": character["name"],
            "name_alias": character["alias"], "name_language": character["name_language"],
            "style_id": raw["label"], "style_name": style_names.get(raw["label"]) or raw["name"],
            "image": "images/" + filename, "source_url": CDN + filename,
            "role": raw.get("role", ""),
        })

    def download(style: dict) -> tuple[str, bool]:
        path = output / style["image"]
        if path.is_file():
            try:
                valid_image(path.read_bytes())
                return style["style_id"], False
            except (OSError, ValueError):
                pass
        data = fetch(style["source_url"])
        valid_image(data)
        atomic_write(path, data)
        return style["style_id"], True

    failures = []
    downloaded = 0
    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(download, style): style for style in styles}
        for completed, future in enumerate(concurrent.futures.as_completed(futures), 1):
            style = futures[future]
            try:
                _, fresh = future.result()
                downloaded += int(fresh)
            except Exception as exc:
                failures.append({"style_id": style["style_id"], "source_url": style["source_url"],
                                 "error": str(exc)})
            if progress is not None:
                progress(completed, len(styles), f"已准备 {completed}/{len(styles)} 张 Avatar")
            elif completed % 25 == 0 or completed == len(styles):
                print(f"Avatar cache: {completed}/{len(styles)}; downloaded {downloaded}; "
                      f"missing {len(failures)}", flush=True)

    updated_at = datetime.now(timezone.utc).isoformat()
    report = {"updated_at": updated_at, "characters": len(characters), "styles": len(styles),
              "downloaded": downloaded, "reused": len(styles) - downloaded - len(failures),
              "missing": failures, "manifest_updated": not failures}
    atomic_write(output / "sync-report.json", json.dumps(report, ensure_ascii=False, indent=2).encode("utf-8"))
    if failures:
        # Preserve the last usable manifest and all successful cache files.
        raise RuntimeError(f"Sync incomplete: {len(failures)} missing assets; "
                           f"existing catalog.json retained. See {output / 'sync-report.json'}")
    catalog = {"schema_version": 1, "source": SOURCE, "updated_at": updated_at,
               "sources": list(ENDPOINTS.values()), "characters": characters, "styles": styles}
    atomic_write(output / "catalog.json", json.dumps(catalog, ensure_ascii=False, indent=2).encode("utf-8"))
    return catalog


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / "data" / "character-catalog")
    parser.add_argument("--workers", type=int, default=5)
    args = parser.parse_args()
    if not 1 <= args.workers <= 6:
        parser.error("--workers must be between 1 and 6")
    try:
        catalog = sync_catalog(args.output, workers=args.workers)
        print(f"Catalog ready: {len(catalog['characters'])} Characters, "
              f"{len(catalog['styles'])} Styles → {args.output.resolve() / 'catalog.json'}")
        return 0
    except Exception as exc:
        print(f"Sync failed; existing cache retained: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
