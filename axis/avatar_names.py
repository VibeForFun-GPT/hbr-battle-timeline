"""Offline Avatar Naming from independent HBR Quest Style Thumbnails."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path

import cv2
import numpy as np


DEFAULT_CATALOG = Path(__file__).resolve().parents[1] / "data" / "character-catalog" / "catalog.json"


class AvatarNameMatcher:
    """Multi-Scale local NCC with distinct-Character ambiguity rejection.

    Raw NCC scores are similarity measurements, not probabilities. A database
    Character ID remains separate from the battle's six local Avatar IDs.
    """

    # Checked against independently downloaded artwork and 720p Video Crops.
    # Both gates must pass; new or heavily damaged art remains a Placeholder.
    score_threshold = .83
    margin_threshold = .06
    scales = tuple(float(v) for v in np.arange(.80, 1.651, .075))

    def __init__(self, entries=(), *, path=None, source="https://hbr.quest/", updated_at=None, error=None, missing_images=0):
        self.entries = tuple(entries)
        self.path = Path(path) if path else DEFAULT_CATALOG
        self.source = source
        self.updated_at = updated_at
        self.error = error
        self.missing_images = missing_images
        self.ready = bool(self.entries)
        self.style_count = len(self.entries)
        self.character_count = len({entry["character_id"] for entry in self.entries})

    @classmethod
    def load(cls, path=None):
        """Read a local catalog; unavailable or corrupt caches remain usable."""
        path = Path(path) if path is not None else DEFAULT_CATALOG
        try:
            data = json.loads(path.read_text(encoding="utf-8-sig"))
            if not isinstance(data, dict) or data.get("schema_version") != 1 or not isinstance(data.get("styles"), list):
                raise ValueError("Unsupported Avatar Catalog schema")
            root = path.parent.resolve()
            entries, missing = [], 0
            for style in data["styles"]:
                try:
                    if not isinstance(style, dict) or not all(isinstance(style.get(k), str) and style[k].strip() for k in ("character_id", "name", "style_id", "image")):
                        raise ValueError("Invalid Style metadata")
                    image_path = (root / style["image"]).resolve()
                    if not image_path.is_relative_to(root):
                        raise ValueError("Style image is outside the catalog")
                    pixels = cv2.imdecode(np.fromfile(str(image_path), dtype=np.uint8), cv2.IMREAD_UNCHANGED)
                    if pixels is None or pixels.ndim != 3 or pixels.shape[2] not in (3, 4) or min(pixels.shape[:2]) < 32:
                        raise ValueError("Unreadable Style Thumbnail")
                    if max(pixels.shape[:2]) > 1024:
                        raise ValueError("Oversized Style Thumbnail")
                    gray = cv2.cvtColor(pixels[:, :, :3], cv2.COLOR_BGR2GRAY)
                    ratio = min(1., 96 / max(gray.shape))
                    small = cv2.resize(gray, (round(gray.shape[1] * ratio), round(gray.shape[0] * ratio)), interpolation=cv2.INTER_AREA)
                    entries.append({**style, "bgr": pixels[:, :, :3], "gray": gray, "small": small, "ratio": ratio})
                except (OSError, ValueError, TypeError, cv2.error):
                    missing += 1
            return cls(entries, path=path, source=data.get("source") or "https://hbr.quest/", updated_at=data.get("updated_at") or data.get("fetched_at"), missing_images=missing,
                       error=None if entries else "No readable Style Thumbnails")
        except (OSError, UnicodeError, ValueError, TypeError, cv2.error):
            return cls(path=path, error="Avatar Catalog is missing or corrupt")

    def status(self):
        return {"ready": self.ready, "source": self.source, "updated_at": self.updated_at,
                "style_count": self.style_count, "character_count": self.character_count,
                "missing_images": self.missing_images, "error": self.error}

    def _empty(self, reason):
        return {"accepted": False, "name": None, "name_alias": None, "database_character_id": None,
                "matched_style_id": None, "raw_score": 0., "margin": 0., "candidates": [],
                "name_language": None, "source_url": self.source, "reason": reason,
                "catalog_updated_at": self.updated_at}

    @staticmethod
    def _search(canvas, query, scales, ratio=1.):
        best_score, best_scale = -1., 1.
        for scale in scales:
            width = round(query.shape[1] * scale * ratio)
            height = round(query.shape[0] * scale * ratio)
            if min(width, height) < 18 or width > canvas.shape[1] or height > canvas.shape[0]:
                continue
            patch = cv2.resize(query, (width, height), interpolation=cv2.INTER_AREA if scale * ratio < 1 else cv2.INTER_LINEAR)
            if patch.std() < 8:
                continue
            scores = cv2.matchTemplate(canvas, patch, cv2.TM_CCOEFF_NORMED)
            score = float(cv2.minMaxLoc(scores)[1])
            if np.isfinite(score) and score > best_score:
                best_score, best_scale = score, float(scale)
        return best_score, best_scale

    def match(self, image):
        """Name a BGR face Crop, or explicitly reject unknown/ambiguous input."""
        if not self.ready:
            return self._empty("catalog_unavailable")
        if not isinstance(image, np.ndarray) or image.ndim != 3 or image.shape[2] != 3 or min(image.shape[:2]) < 24 or max(image.shape[:2]) > 1024:
            return self._empty("invalid_avatar")
        if image.dtype != np.uint8:
            return self._empty("invalid_avatar")
        gray_query = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
        if gray_query.std() < 8:
            return self._empty("insufficient_detail")
        # Keep the eyes/hair and omit most of the bottom-left combat badges.
        height, width = gray_query.shape
        region = (slice(max(1, round(height * .035)), round(height * .82)), slice(max(1, round(width * .04)), round(width * .97)))
        query = gray_query[region]
        color_query = image[region]
        coarse = []
        for entry in self.entries:
            score, scale = self._search(entry["small"], query, self.scales, entry["ratio"])
            coarse.append((score, scale, entry))
        coarse.sort(key=lambda item: item[0], reverse=True)
        # Coarse grayscale search tolerates lighting; Color NCC adds face cues.
        # Refinement includes multiple Styles of each distinct top Character.
        shortlist, selected_counts = [], {}
        for item in coarse:
            ident = item[2]["character_id"]
            if ident not in selected_counts and len(selected_counts) >= 12:
                continue
            if selected_counts.get(ident, 0) < 2:
                shortlist.append(item)
                selected_counts[ident] = selected_counts.get(ident, 0) + 1
        grouped = {}
        for _, scale, entry in shortlist:
            refine = sorted({max(.8, scale + offset) for offset in (-.06, -.03, 0, .03, .06)})
            score, matched_scale = self._search(entry["bgr"], color_query, refine)
            candidate = {"name": entry["name"], "name_alias": entry.get("name_alias") or entry["name"], "database_character_id": entry["character_id"],
                         "matched_style_id": entry["style_id"], "raw_score": round(max(0., score), 4),
                         "name_language": entry.get("name_language"), "source_url": entry.get("source_url") or self.source,
                         "style_name": entry.get("style_name"), "scale": round(matched_scale, 4)}
            current = grouped.get(entry["character_id"])
            if current is None or candidate["raw_score"] > current["raw_score"]:
                grouped[entry["character_id"]] = candidate
        candidates = sorted(grouped.values(), key=lambda candidate: candidate["raw_score"], reverse=True)[:3]
        if not candidates:
            return self._empty("no_match")
        best = candidates[0]
        second = candidates[1]["raw_score"] if len(candidates) > 1 else 0.
        margin = round(best["raw_score"] - second, 4)
        accepted = best["raw_score"] >= self.score_threshold and margin >= self.margin_threshold
        return {**best, "accepted": accepted, "name": best["name"] if accepted else None,
                "margin": margin, "candidates": candidates,
                "reason": "accepted" if accepted else "low_score" if best["raw_score"] < self.score_threshold else "ambiguous",
                "catalog_updated_at": self.updated_at}

    def match_many(self, images):
        """Match independent Avatar Crops without changing their local order."""
        images = list(images)
        if not self.ready or len(images) < 2:
            return [self.match(image) for image in images]
        with ThreadPoolExecutor(max_workers=min(4, len(images))) as pool:
            return list(pool.map(self.match, images))
