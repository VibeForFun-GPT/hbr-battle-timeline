"""Avatar Naming Tests using independent catalog artwork and explicit fallback."""
import json
from pathlib import Path
import sys
import tempfile
import unittest

import cv2
import numpy as np


HERE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HERE))
from axis.avatar_names import AvatarNameMatcher, DEFAULT_CATALOG
from axis.vision import Avatars, read_image
from _support import private_avatars_ready, private_frames_ready, site_catalog_ready


def artwork(seed):
    rng = np.random.default_rng(seed)
    blocks = rng.integers(0, 255, (18, 18, 3), dtype=np.uint8)
    return cv2.GaussianBlur(cv2.resize(blocks, (144, 144), interpolation=cv2.INTER_CUBIC), (3, 3), 0)


def catalog(path, styles):
    entries = []
    for number, (ident, name, pixels) in enumerate(styles):
        filename = f"images/style-{number}.png"
        target = path.parent / filename
        target.parent.mkdir(parents=True, exist_ok=True)
        cv2.imencode('.png', pixels)[1].tofile(str(target))
        entries.append(dict(character_id=ident, name=name, name_alias=name[-1], style_id=f'{ident}-{number}', image=filename,
                            source_url=f'https://cdn.hbr.quest/test/{number}.webp', name_language='zh-TW'))
    path.write_text(json.dumps(dict(schema_version=1, source='https://hbr.quest/', updated_at='2026-10-05', characters=[], styles=entries)), encoding='utf8')


class AvatarNameTests(unittest.TestCase):
    def test_missing_and_corrupt_cache_are_safe(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / 'catalog.json'
            matcher = AvatarNameMatcher.load(path)
            self.assertFalse(matcher.ready)
            self.assertFalse(matcher.match(artwork(1))['accepted'])
            path.write_text('{invalid', encoding='utf8')
            self.assertFalse(AvatarNameMatcher.load(path).ready)
            path.write_text(json.dumps({'schema_version': 99, 'styles': []}), encoding='utf8')
            self.assertFalse(AvatarNameMatcher.load(path).ready)

    def test_partial_cache_skips_missing_corrupt_and_external_files(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / 'catalog.json'
            catalog(path, [('A', '人物甲', artwork(1))])
            data = json.loads(path.read_text(encoding='utf8'))
            for image in ('images/missing.webp', 'images/corrupt.webp', '../outside.png'):
                data['styles'].append({**data['styles'][0], 'image': image})
            (path.parent / 'images' / 'corrupt.webp').write_bytes(b'invalid')
            path.write_text(json.dumps(data), encoding='utf8')
            matcher = AvatarNameMatcher.load(path)
            self.assertTrue(matcher.ready)
            self.assertEqual(matcher.style_count, 1)
            self.assertEqual(matcher.missing_images, 3)

    def test_same_character_styles_do_not_create_false_ambiguity(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / 'catalog.json'
            source = artwork(15)
            catalog(path, [('A', '人物甲', source), ('A', '人物甲', source.copy()), ('B', '人物乙', artwork(35))])
            result = AvatarNameMatcher.load(path).match(source[29:101, 35:113].copy())
            self.assertTrue(result['accepted'], result)
            self.assertEqual(result['database_character_id'], 'A')
            self.assertEqual(result['name_alias'], '甲')
            self.assertEqual(len(result['candidates']), 2)
            self.assertGreater(result['margin'], .1)

    def test_distinct_characters_with_equal_artwork_are_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / 'catalog.json'
            source = artwork(15)
            catalog(path, [('A', '人物甲', source), ('B', '人物乙', source.copy())])
            result = AvatarNameMatcher.load(path).match(source[29:101, 35:113].copy())
            self.assertFalse(result['accepted'])
            self.assertEqual(result['reason'], 'ambiguous')
            self.assertIsNone(result['name'])
            self.assertEqual(result['margin'], 0)

    def test_unknown_and_invalid_avatars_are_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / 'catalog.json'
            catalog(path, [('A', '人物甲', artwork(15)), ('B', '人物乙', artwork(35))])
            matcher = AvatarNameMatcher.load(path)
            images = [None, np.zeros((72, 78, 3), np.uint8), np.zeros((72, 78), np.uint8), artwork(905)[:72, :78]]
            self.assertTrue(all(not result['accepted'] for result in matcher.match_many(images)))

    def test_scaled_local_crop_and_jpeg_keep_source_identity(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / 'catalog.json'
            source = artwork(81)
            catalog(path, [('A', '人物甲', source), ('B', '人物乙', artwork(82))])
            matcher = AvatarNameMatcher.load(path)
            face = source[29:101, 35:113].copy()
            resized = cv2.resize(face, None, fx=.82, fy=.82, interpolation=cv2.INTER_AREA)
            jpeg = cv2.imdecode(cv2.imencode('.jpg', face, [cv2.IMWRITE_JPEG_QUALITY, 75])[1], cv2.IMREAD_COLOR)
            for result in matcher.match_many([face, resized, jpeg]):
                self.assertTrue(result['accepted'], result)
                self.assertEqual(result['database_character_id'], 'A')

    @unittest.skipUnless(site_catalog_ready(), 'Optional full-site integration requires a complete local Character Cache')
    def test_full_site_catalog_rejects_unknown_texture_and_smooth_gradients(self):
        matcher = AvatarNameMatcher.load()
        rng = np.random.default_rng(994)
        values = np.tile(np.linspace(20, 235, 78, dtype=np.uint8), (72, 1))
        gradient = np.repeat(values[:, :, None], 3, axis=2)
        images = [rng.integers(0, 255, (72, 78, 3), dtype=np.uint8), artwork(905)[:72, :78], artwork(999)[:72, :78],
                  gradient, np.fliplr(gradient).copy()]
        results = matcher.match_many(images)
        self.assertTrue(all(not result['accepted'] for result in results))
        # Smooth fields can yield high NCC; the distinct-Character margin must
        # still reject them, rather than interpreting similarity as probability.
        self.assertGreater(results[-1]['raw_score'], matcher.score_threshold)
        self.assertEqual(results[-1]['reason'], 'ambiguous')
        self.assertTrue(all(len({candidate['database_character_id'] for candidate in result['candidates']}) == len(result['candidates']) <= 3 for result in results))

    @unittest.skipUnless(site_catalog_ready() and private_avatars_ready() and private_frames_ready(), 'Optional real-media Avatar integration requires all 6 local sample Avatars, all 7 Frames and complete Character Cache')
    def test_independent_site_thumbnails_name_real_video_faces(self):
        matcher = AvatarNameMatcher.load()
        self.assertGreaterEqual(matcher.style_count, 100)
        expected = ['KAsakura', 'YIzumi', 'RKayamori', 'MNikaido', 'YShirakawa', 'SHiguchi']
        direct = [read_image(HERE / 'data' / 'sample-final' / 'avatars' / f'{i}.jpg') for i in range(1, 7)]
        for result, identity in zip(matcher.match_many(direct), expected):
            self.assertTrue(result['accepted'], result)
            self.assertEqual(result['database_character_id'], identity)
        frame = cv2.resize(read_image(HERE / 'tests' / 'fixtures' / 'bootstrap.jpg'), (1920, 1080))
        for result, identity in zip(matcher.match_many(Avatars(frame).images), expected):
            self.assertTrue(result['accepted'], result)
            self.assertEqual(result['database_character_id'], identity)


if __name__ == '__main__':
    unittest.main(verbosity=2)
