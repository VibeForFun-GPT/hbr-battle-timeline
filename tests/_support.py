"""Optional local integration assets and generated public Test artwork."""
import json
from pathlib import Path

import cv2
import numpy as np
from PIL import ImageFont

from axis.avatar_names import AvatarNameMatcher, DEFAULT_CATALOG
from axis.export import font_path


HERE = Path(__file__).resolve().parents[1]
FRAME_NAMES = ('bootstrap.jpg', 'select-1.jpg', 'confirm-1.jpg', 'select-2.jpg', 'confirm-2.jpg', 'select-3.jpg', 'confirm-3.jpg')


def private_frames_ready():
    return all((HERE / 'tests' / 'fixtures' / name).is_file() for name in FRAME_NAMES)


def sample_profile_ready():
    return (HERE / 'examples' / 'sample-profile.json').is_file()


def cjk_font_ready():
    try:
        ImageFont.truetype(font_path(), 18)
        return True
    except (OSError, ValueError):
        return False


def site_catalog_ready():
    if not DEFAULT_CATALOG.is_file():
        return False
    matcher = AvatarNameMatcher.load()
    return matcher.style_count >= 100 and matcher.character_count >= 20


def private_avatars_ready():
    return all((HERE / 'data' / 'sample-final' / 'avatars' / f'{index}.jpg').is_file() for index in range(1, 7))


def generated_artwork(seed):
    """Generate independent colored artwork; no game assets are involved."""
    rng = np.random.default_rng(seed)
    blocks = rng.integers(0, 255, (18, 18, 3), dtype=np.uint8)
    return cv2.GaussianBlur(cv2.resize(blocks, (144, 144), interpolation=cv2.INTER_CUBIC), (3, 3), 0)


def write_synthetic_catalog(folder):
    """Create a six-Character local catalog and matching independent Crops."""
    folder = Path(folder)
    (folder / 'images').mkdir(parents=True)
    styles, crops = [], []
    for index in range(1, 7):
        source = generated_artwork(700 + index)
        relative = f'images/synthetic-{index}.png'
        cv2.imencode('.png', source)[1].tofile(str(folder / relative))
        styles.append(dict(character_id=f'Synthetic{index}', name=f'合成队员{index}', name_alias=f'队员{index}',
                           name_language='zh-CN', style_id=f'Synthetic{index}-Style', image=relative,
                           source_url=f'https://example.invalid/synthetic/{index}.png'))
        crops.append(source[29:101, 35:113].copy())
    path = folder / 'catalog.json'
    path.write_text(json.dumps(dict(schema_version=1, source='synthetic-test', updated_at='2026-10-15', characters=[], styles=styles),
                               ensure_ascii=False), encoding='utf8')
    return path, crops
