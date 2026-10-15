"""Verify Timeline Preview Metadata, frame timestamps and endpoint compatibility."""
import base64
from http.client import HTTPConnection
from http.server import ThreadingHTTPServer
import json
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch

import cv2
import numpy as np


HERE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HERE))
from app import Store, make_handler, preview_video


class PreviewTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        cls.root = Path(cls.temporary.name)
        cls.path = cls.root / 'timeline.avi'
        writer = cv2.VideoWriter(str(cls.path), cv2.VideoWriter_fourcc(*'MJPG'), 10, (160, 90))
        if not writer.isOpened():
            cls.temporary.cleanup()
            raise RuntimeError('The Preview integration fixture needs the MJPG encoder')
        try:
            for color in ((0, 0, 220), (0, 220, 0), (220, 0, 0),
                          (220, 220, 0), (220, 0, 220), (0, 220, 220)):
                writer.write(np.full((90, 160, 3), color, np.uint8))
        finally:
            writer.release()
        cls.single = cls.root / 'one-frame.avi'
        writer = cv2.VideoWriter(str(cls.single), cv2.VideoWriter_fourcc(*'MJPG'), 30, (160, 90))
        if not writer.isOpened():
            cls.temporary.cleanup()
            raise RuntimeError('The one-Frame Preview fixture needs the MJPG encoder')
        try:
            writer.write(np.full((90, 160, 3), (80, 160, 220), np.uint8))
        finally:
            writer.release()

    @classmethod
    def tearDownClass(cls):
        cls.temporary.cleanup()

    def setUp(self):
        self.store = Store(self.root / self._testMethodName)
        self.server = ThreadingHTTPServer(('127.0.0.1', 0), make_handler(self.store, 'preview-test'))
        self.worker = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.worker.start()
        self.connection = HTTPConnection('127.0.0.1', self.server.server_port, timeout=10)

    def tearDown(self):
        self.connection.close()
        self.store.close()
        self.server.shutdown()
        self.server.server_close()
        self.worker.join()

    def post(self, route, body):
        self.connection.request('POST', '/preview-test/' + route, json.dumps(body),
                                {'Content-Type': 'application/json'})
        response = self.connection.getresponse()
        result = json.loads(response.read())
        return response.status, result

    def image(self, result):
        self.assertTrue(result['image'].startswith('data:image/jpeg;base64,'))
        raw = base64.b64decode(result['image'].split(',', 1)[1])
        image = cv2.imdecode(np.frombuffer(raw, np.uint8), cv2.IMREAD_COLOR)
        self.assertEqual(image.shape, (1080, 1920, 3))
        return image

    def test_video_info_returns_slider_bounds_without_decoding_a_frame(self):
        status, result = self.post('api/video-info', {'path': str(self.path)})
        self.assertEqual(status, 200, result)
        self.assertAlmostEqual(result['duration'], .6)
        self.assertAlmostEqual(result['fps'], 10)
        self.assertAlmostEqual(result['max_time'], .5)
        self.assertEqual(result['metadata']['frames'], 6)
        self.assertEqual((result['metadata']['width'], result['metadata']['height']), (160, 90))
        self.assertEqual(Path(result['metadata']['path']), self.path.resolve())
        self.assertNotIn('image', result)
        video = Mock(fps=10., frames=6, duration=.6)
        video.metadata.return_value = result['metadata']
        with patch('app.Video', return_value=video):
            preview_video(str(self.path), include_image=False)
        video.cap.read.assert_not_called()
        video.cap.set.assert_not_called()
        video.close.assert_called_once()

    def test_legacy_preview_and_timestamp_format_return_the_displayed_frame(self):
        status, first = self.post('api/preview', {'path': str(self.path)})
        self.assertEqual(status, 200, first)
        self.assertEqual(first['time'], 0)
        self.assertEqual(first['frame_index'], 0)
        self.assertGreater(float(self.image(first)[:, :, 2].mean()), 200)
        status, later = self.post('api/preview', {'path': str(self.path), 'time': '00:00.25'})
        self.assertEqual(status, 200, later)
        self.assertEqual(later['frame_index'], 2)
        self.assertAlmostEqual(later['time'], .2)
        image = self.image(later)
        self.assertGreater(float(image[:, :, 0].mean()), 200)
        self.assertLess(float(image[:, :, 2].mean()), 20)
        self.assertEqual(later['metadata'], first['metadata'])

    def test_end_and_past_end_clamp_to_the_last_existing_frame(self):
        for timestamp in (.6, 999, '99:00'):
            status, result = self.post('api/preview', {'path': str(self.path), 'time': timestamp})
            self.assertEqual(status, 200, result)
            self.assertEqual(result['frame_index'], 5)
            self.assertAlmostEqual(result['time'], .5)
            image = self.image(result)
            self.assertGreater(float(image[:, :, 1].mean()), 200)
            self.assertGreater(float(image[:, :, 2].mean()), 200)
            self.assertLess(float(image[:, :, 0].mean()), 20)

    def test_one_frame_video_has_a_valid_zero_slider_bound(self):
        status, info = self.post('api/video-info', {'path': str(self.single)})
        self.assertEqual(status, 200, info)
        self.assertAlmostEqual(info['duration'], 1 / 30)
        self.assertEqual(info['max_time'], 0)
        status, result = self.post('api/preview', {'path': str(self.single), 'time': 9})
        self.assertEqual(status, 200, result)
        self.assertEqual(result['time'], 0)
        self.assertEqual(result['frame_index'], 0)
        self.image(result)

    def test_invalid_timestamps_and_missing_paths_are_client_errors(self):
        for timestamp in (-1, 'NaN', 'Infinity', 'bad', '1:2:3:4'):
            status, result = self.post('api/preview', {'path': str(self.path), 'time': timestamp})
            self.assertEqual(status, 400, result)
            self.assertIn('error', result)
        for route in ('api/preview', 'api/video-info'):
            for body in ({}, {'path': None}, {'path': ''}, {'path': str(self.root / 'missing.avi')}):
                status, result = self.post(route, body)
                self.assertEqual(status, 400, result)

    def test_decoder_is_released_when_frame_read_fails(self):
        video = Mock(fps=10., frames=6, duration=.6)
        video.metadata.return_value = {'fps': 10., 'duration': .6}
        video.cap.read.return_value = (False, None)
        with patch('app.Video', return_value=video):
            status, result = self.post('api/preview', {'path': str(self.path), 'time': .2})
        self.assertEqual(status, 400, result)
        video.close.assert_called_once()

    def test_active_live_preview_remains_compatible(self):
        capture = Mock()
        capture.snapshot.return_value = {'active': True}
        capture.frame.return_value = np.full((90, 160, 3), (0, 160, 230), np.uint8)
        self.store.capture = capture
        status, result = self.post('api/live/preview', {})
        self.assertEqual(status, 200, result)
        self.assertEqual(set(result), {'image'})
        self.assertTrue(result['image'].startswith('data:image/jpeg;base64,'))
        capture.frame.assert_called_once()


if __name__ == '__main__':
    unittest.main(verbosity=2)
