"""Verify window isolation, capture recovery and Capture-to-axis integration."""
from http.client import HTTPConnection
from http.server import ThreadingHTTPServer
import json
from pathlib import Path
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import Mock,patch

import cv2
import numpy as np
from PIL import Image

HERE=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(HERE))
from app import Store,make_handler
from axis.live import CapturePaused,WindowSource
from axis.vision import read_image,Video
from _support import cjk_font_ready, private_frames_ready, sample_profile_ready, site_catalog_ready


INFO=dict(hwnd=55,pid=101,title='Integration Fixture',left=100,top=100,width=1280,height=720,minimized=False)


class WindowTests(unittest.TestCase):
    def source(self):
        api=Mock()
        api.GetAncestor.side_effect=lambda hwnd,_:hwnd
        api.GetForegroundWindow.return_value=55
        api.GetSystemMetrics.side_effect=lambda n:{76:0,77:0,78:1920,79:1080}[n]
        with patch('axis.live.windows_api',return_value=api),patch('axis.live.window_info',return_value=INFO.copy()):
            return WindowSource(55),api

    def test_only_selected_client_area_is_captured(self):
        source,api=self.source()
        with patch('axis.live.window_info',return_value=INFO.copy()),patch('axis.live.ImageGrab.grab',return_value=Image.new('RGB',(1280,720),'red')) as grab:
            frame=source.frame()
        grab.assert_called_once_with(bbox=(100,100,1380,820),all_screens=True)
        self.assertEqual(frame.shape,(720,1280,3))
        self.assertEqual(frame[0,0].tolist(),[0,0,255])

    def test_background_or_minimized_window_is_not_captured(self):
        source,api=self.source();api.GetForegroundWindow.return_value=77
        with patch('axis.live.window_info',return_value=INFO.copy()),patch('axis.live.ImageGrab.grab') as grab:
            with self.assertRaises(CapturePaused):source.frame()
            grab.assert_not_called()
        with patch('axis.live.window_info',return_value={**INFO,'minimized':True}),patch('axis.live.ImageGrab.grab') as grab:
            with self.assertRaises(CapturePaused):source.frame()
            grab.assert_not_called()

    def test_alt_tab_during_grab_discards_the_frame(self):
        source,api=self.source();api.GetForegroundWindow.side_effect=[55,77]
        with patch('axis.live.window_info',return_value=INFO.copy()),patch('axis.live.ImageGrab.grab',return_value=Image.new('RGB',(1280,720),'red')):
            with self.assertRaises(CapturePaused):source.frame()

    def test_stop_shortcut_requires_all_three_keys(self):
        source,api=self.source()
        api.GetAsyncKeyState.side_effect=lambda key:0 if key==0x45 else 0x8000
        self.assertFalse(source.stop_requested())
        api.GetAsyncKeyState.return_value=0x8000;api.GetAsyncKeyState.side_effect=None
        self.assertTrue(source.stop_requested())


class LiveIntegrationTests(unittest.TestCase):
    def test_end_before_first_frame_releases_the_session(self):
        class WaitingSource:
            info=INFO.copy()
            def frame(self):raise CapturePaused('等待游戏')
            def stop_requested(self):return False
        with tempfile.TemporaryDirectory() as temporary:
            store=Store(temporary,source_factory=lambda _:WaitingSource())
            try:
                job=store.start_live({'hwnd':55})
                store.stop_live();store.capture.thread.join(timeout=3)
                self.assertFalse(store.capture.thread.is_alive())
                metadata=store.metadata(job['id'])
                self.assertEqual(metadata['status'],'error')
                self.assertIn('没有捕获',metadata['message'])
                self.assertFalse(store.running)
                self.assertFalse((store.folder(job['id'])/'axis.json').exists())
            finally:store.close()

    @unittest.skipUnless(private_frames_ready() and sample_profile_ready() and cjk_font_ready() and site_catalog_ready(), 'Optional live Replay requires all 7 local Frames, sample Profile, CJK Font and complete Character Cache')
    def test_start_pause_end_and_automatic_axis(self):
        fixtures=HERE/'tests'/'fixtures'
        black=np.zeros((720,1280,3),np.uint8)
        scenes=[('bootstrap.jpg',.8),('select-1.jpg',.5),(None,.5),('select-1.jpg',.6),('confirm-1.jpg',.4),('black',.5),
                ('select-2.jpg',.9),('confirm-2.jpg',.4),('black',.5),('select-3.jpg',.9),('confirm-3.jpg',.4),('black',.6)]
        images={name:read_image(fixtures/name) for name,_ in scenes if name not in (None,'black')}
        class ReplaySource:
            info=INFO.copy()
            def __init__(self):self.begun=None
            def frame(self):
                if self.begun is None:self.begun=time.monotonic()
                remaining=time.monotonic()-self.begun
                for name,duration in scenes:
                    if remaining<duration:
                        if name is None:raise CapturePaused('模拟切换到其他窗口')
                        return black.copy() if name=='black' else images[name].copy()
                    remaining-=duration
                return black.copy()
            def stop_requested(self):return False
        with tempfile.TemporaryDirectory() as temporary:
            store=Store(temporary,source_factory=lambda _:ReplaySource())
            server=ThreadingHTTPServer(('127.0.0.1',0),make_handler(store,'live-test'))
            worker=threading.Thread(target=server.serve_forever,daemon=True);worker.start()
            connection=HTTPConnection('127.0.0.1',server.server_port,timeout=10)
            try:
                connection.request('POST','/live-test/api/live/start',json.dumps({'hwnd':55,'fps':8,'sample_rate':4,'profile':{}}),{'Content-Type':'application/json'})
                response=connection.getresponse();self.assertEqual(response.status,200);job=json.loads(response.read())
                self.assertEqual(job['status'],'capturing')
                connection.request('POST','/live-test/api/live/profile',json.dumps({'profile':{'aliases':{'重點保養':'任意简写'}}}),{'Content-Type':'application/json'})
                response=connection.getresponse();self.assertEqual(response.status,200);response.read()
                with self.assertRaises(ValueError):store.start_live({'hwnd':55})
                deadline=time.monotonic()+20
                while store.capture.snapshot()['duration']<sum(d for _,d in scenes):
                    if not store.capture.snapshot()['active'] or time.monotonic()>deadline:break
                    time.sleep(.05)
                connection.request('POST','/live-test/api/live/stop','{}',{'Content-Type':'application/json'})
                response=connection.getresponse();self.assertEqual(response.status,200);response.read()
                store.capture.thread.join(timeout=25)
                self.assertFalse(store.capture.thread.is_alive())
                metadata=store.metadata(job['id']);self.assertEqual(metadata['status'],'done',metadata['message'])
                document=store.document(job['id'])
                self.assertEqual([r['label'] for r in document['rows']],['1','2','追加'])
                self.assertIsNone(document['rows'][-1]['od_after'])
                self.assertTrue(document['rows'][-1]['needs_review'])
                self.assertTrue(any('行动后 OD' in note for note in document['rows'][-1]['notes']))
                self.assertEqual([a['character_id'] for a in document['rows'][0]['actions']],[1,4,3])
                self.assertEqual(document['rows'][0]['actions'][0]['skill'],'任意简写')
                self.assertEqual(document['settings']['aliases'],{'重點保養':'任意简写'})
                self.assertEqual(document['settings']['name_mode'],'auto')
                self.assertEqual([p['name'] for p in document['team']],
                                 ['朝倉可憐','和泉由希','茅森月歌','二階堂三鄉','白河結奈','樋口聖華'])
                self.assertTrue(all(p['name_source']=='hbr_quest_avatar' for p in document['team']))
                self.assertGreater(document['capture']['paused_seconds'],.3)
                self.assertFalse(document['capture']['audio'])
                self.assertTrue(any('暂停' in warning for warning in document['warnings']))
                folder=store.folder(job['id'])
                self.assertTrue((folder/'capture.webm').is_file())
                self.assertTrue((folder/'capture.json').is_file())
                self.assertTrue((folder/'exports'/'detailed'/'行动轴.xlsx').is_file())
                video=Video(folder/'capture.webm');self.assertAlmostEqual(video.fps,8.,places=1);video.close()
                connection.request('GET','/live-test/video/'+job['id'],headers={'Range':'bytes=0-3'})
                response=connection.getresponse();self.assertEqual(response.status,206);self.assertEqual(response.read(),bytes.fromhex('1a45dfa3'))
            finally:
                connection.close();store.close();server.shutdown();server.server_close();worker.join()


if __name__=='__main__':unittest.main(verbosity=2)
