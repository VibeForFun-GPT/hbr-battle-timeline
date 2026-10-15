"""Verify multi-signal Bootstrap detection and bounded asynchronous search."""
from http.client import HTTPConnection
from http.server import ThreadingHTTPServer
import json
from pathlib import Path
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import Mock, patch

import numpy as np

HERE=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(HERE))
from app import Store,make_handler
from axis.start_detection import StartSearchCancelled,bootstrap_evidence,find_start
from axis.vision import DEFAULT_LAYOUT


def selection_frame(*,button=True,detail=True):
    frame=np.zeros((1080,1920,3),np.uint8)
    for x1,y1,x2,y2 in DEFAULT_LAYOUT['portrait_boxes']:
        frame[y1:y2,x1:x2]=(180,30,160)
    if detail:
        rng=np.random.default_rng(321)
        for x,y in DEFAULT_LAYOUT['portrait_centers']:
            frame[y-15:y+57,x-39:x+39]=rng.integers(0,255,(72,78,3),dtype=np.uint8)
    if button:
        x1,y1,x2,y2=DEFAULT_LAYOUT['button_box']
        frame[y1:y2,x1:x2]=(180,30,160)
    return frame


def reader(turn=1):
    result=Mock()
    result.status.return_value={'turn':turn,'raw':{'turn':'TURN 1' if turn else ''},'confidence':{'turn':.95}}
    return result


class FakeVideo:
    fps=2.
    frames=40
    duration=20.

    def __init__(self,scenes):
        self.scenes=scenes
        self.closed=False

    def metadata(self):
        return {'fps':self.fps,'frames':self.frames,'duration':self.duration,'width':1920,'height':1080}

    def sample(self,start,end,rate):
        for when,frame in self.scenes:
            if start<=when<end:
                yield when,frame

    def close(self):
        self.closed=True


class StartDetectionTests(unittest.TestCase):
    def test_multiple_signals_and_a_labeled_turn_are_required(self):
        frame=selection_frame()
        valid=reader()
        evidence=bootstrap_evidence(frame,DEFAULT_LAYOUT,valid)
        self.assertTrue(evidence['accepted'])
        self.assertEqual(evidence['colored_avatars'],6)
        self.assertEqual(evidence['detailed_avatars'],6)
        self.assertEqual(evidence['turn_raw'],'TURN 1')
        invalid=reader(None)
        self.assertFalse(bootstrap_evidence(frame,DEFAULT_LAYOUT,invalid)['accepted'])
        self.assertFalse(bootstrap_evidence(selection_frame(button=False),DEFAULT_LAYOUT,valid)['accepted'])
        self.assertFalse(bootstrap_evidence(selection_frame(detail=False),DEFAULT_LAYOUT,valid)['accepted'])
        frame[:]=0
        self.assertFalse(bootstrap_evidence(frame,DEFAULT_LAYOUT,valid)['accepted'])

    def test_search_skips_intro_and_configuration_instead_of_guessing_a_time(self):
        selection=selection_frame()
        video=FakeVideo([(0,np.zeros_like(selection)),(7.5,selection),(13.5,selection)])
        ocr=reader()
        ocr.status.side_effect=[{'turn':None},{'turn':3,'raw':{'turn':'TURN 3'},'confidence':{'turn':.98}}]
        updates=[]
        layout={**DEFAULT_LAYOUT,'crop':[.1,.1,.9,.9]}
        factory=Mock(return_value=video)
        result=find_start('fixture.avi',layout=layout,video_factory=factory,reader_factory=lambda _:ocr,progress=updates.append)
        self.assertTrue(result['found'])
        self.assertEqual(result['time'],13.5)
        self.assertEqual(result['checked_frames'],3)
        self.assertEqual(result['crop'],layout['crop'])
        self.assertEqual(result['evidence']['turn'],3)
        self.assertTrue(result['image'].startswith('data:image/jpeg;base64,'))
        self.assertGreater(result['confidence'],.8)
        factory.assert_called_once_with('fixture.avi',layout)
        self.assertTrue(video.closed)
        self.assertTrue(updates)

    def test_failed_search_never_returns_a_usable_timestamp(self):
        video=FakeVideo([(0,selection_frame()),(.5,selection_frame())])
        result=find_start('fixture.avi',video_factory=lambda *_:video,reader_factory=lambda _:reader(None))
        self.assertFalse(result['found'])
        self.assertTrue(result['complete'])
        self.assertNotIn('time',result)
        self.assertIsNone(result['resume_from'])
        self.assertTrue(video.closed)

    def test_bounded_scan_has_a_resume_point_and_cancellation_closes_the_video(self):
        blank=np.zeros((1080,1920,3),np.uint8)
        video=FakeVideo([(0,blank),(.5,blank),(1.,blank)])
        result=find_start('fixture.avi',max_samples=1,video_factory=lambda *_:video,reader_factory=lambda _:reader())
        self.assertFalse(result['found'])
        self.assertFalse(result['complete'])
        self.assertEqual(result['checked_frames'],1)
        self.assertGreater(result['resume_from'],result['scanned_until'])
        self.assertNotIn('time',result)
        self.assertTrue(video.closed)
        cancelled=threading.Event();cancelled.set()
        other=FakeVideo([(0,blank)])
        with self.assertRaises(StartSearchCancelled):
            find_start('fixture.avi',cancel=cancelled,video_factory=lambda *_:other,reader_factory=lambda _:reader())
        self.assertTrue(other.closed)

    def test_out_of_range_search_and_reader_failure_release_the_decoder(self):
        video=FakeVideo([])
        with self.assertRaises(ValueError):
            find_start('fixture.avi',search_from=20,video_factory=lambda *_:video)
        self.assertTrue(video.closed)
        video=FakeVideo([])
        with self.assertRaisesRegex(RuntimeError,'OCR unavailable'):
            find_start('fixture.avi',video_factory=lambda *_:video,reader_factory=Mock(side_effect=RuntimeError('OCR unavailable')))
        self.assertTrue(video.closed)

    def test_wall_time_budget_stops_without_returning_an_unverified_frame(self):
        video=FakeVideo([(0,selection_frame())])
        with patch('axis.start_detection.time.monotonic',side_effect=[0.,2.]):
            result=find_start('fixture.avi',budget_seconds=1,video_factory=lambda *_:video,reader_factory=lambda _:reader())
        self.assertFalse(result['found'])
        self.assertFalse(result['complete'])
        self.assertEqual(result['checked_frames'],0)
        self.assertNotIn('time',result)
        self.assertTrue(video.closed)


class StartSearchApiTests(unittest.TestCase):
    def setUp(self):
        self.temporary=tempfile.TemporaryDirectory()
        self.root=Path(self.temporary.name)
        self.path=self.root/'fixture.avi';self.path.write_bytes(b'synthetic path validation fixture')
        self.store=Store(self.root/'data')
        self.server=ThreadingHTTPServer(('127.0.0.1',0),make_handler(self.store,'start-test'))
        self.worker=threading.Thread(target=self.server.serve_forever,daemon=True);self.worker.start()
        self.connection=HTTPConnection('127.0.0.1',self.server.server_port,timeout=5)
        self.release=threading.Event()
        self.entered=threading.Event()

    def tearDown(self):
        self.release.set()
        self.store.close()
        for record in self.store.start_searches.values():record['thread'].join(timeout=2)
        self.connection.close();self.server.shutdown();self.server.server_close();self.worker.join()
        self.temporary.cleanup()

    def request(self,route,body=None):
        method='POST' if body is not None else 'GET'
        self.connection.request(method,'/start-test/'+route,None if body is None else json.dumps(body),{'Content-Type':'application/json'})
        response=self.connection.getresponse()
        return response.status,json.loads(response.read())

    def search(self,path,*,cancel,progress,**kwargs):
        self.entered.set()
        progress({'scanned_until':12.,'fraction':.2,'message':'Synthetic scan'})
        while not self.release.wait(.01):
            if cancel.is_set():raise StartSearchCancelled('Cancelled fixture')
        return {'found':True,'time':17.5,'crop':[0,0,1,1],'evidence':{'accepted':True}}

    def terminal(self,search_id):
        deadline=time.monotonic()+2
        while time.monotonic()<deadline:
            status,result=self.request('api/find-start/'+search_id)
            self.assertEqual(status,200)
            if result['status']!='running':return result
            time.sleep(.01)
        self.fail('Asynchronous search did not reach a terminal state')

    def test_start_returns_promptly_and_polling_exposes_only_verified_result(self):
        with patch('app.find_start',side_effect=self.search):
            begun=time.monotonic()
            status,state=self.request('api/find-start',{'path':str(self.path),'search_from':'00:05'})
            self.assertEqual(status,200,state)
            self.assertLess(time.monotonic()-begun,1.)
            self.assertTrue(self.entered.wait(1))
            self.assertEqual(state['status'],'running')
            self.assertIsNone(state['result'])
            status,duplicate=self.request('api/find-start',{'path':str(self.path)})
            self.assertEqual(status,400,duplicate)
            self.release.set()
            done=self.terminal(state['id'])
        self.assertEqual(done['status'],'done')
        self.assertTrue(done['found'])
        self.assertEqual(done['result']['time'],17.5)
        self.assertTrue(done['result']['evidence']['accepted'])

    def test_cancellation_and_bad_requests(self):
        with patch('app.find_start',side_effect=self.search):
            status,state=self.request('api/find-start',{'path':str(self.path)})
            self.assertEqual(status,200,state)
            status,cancelled=self.request('api/find-start/'+state['id']+'/cancel',{})
            self.assertEqual(status,200,cancelled)
            done=self.terminal(state['id'])
        self.assertEqual(done['status'],'cancelled')
        self.assertFalse(done['found'])
        self.assertIsNone(done['result'])
        for body in ({},{'path':None},{'path':str(self.path),'search_from':-1},
                     {'path':str(self.path),'profile':{'layout':{'crop':[1,0,0,1]}}}):
            status,result=self.request('api/find-start',body)
            self.assertEqual(status,400,result)
        status,result=self.request('api/find-start/unknown')
        self.assertEqual(status,400,result)

    def test_cancel_then_immediate_replacement_does_not_wait_for_the_old_worker(self):
        old_entered,old_release=threading.Event(),threading.Event()
        new_entered,new_release=threading.Event(),threading.Event()

        def initializing(path,*,search_from,progress,**kwargs):
            entered,release=(old_entered,old_release) if search_from==0 else (new_entered,new_release)
            entered.set()
            if not release.wait(10):raise RuntimeError('Fixture worker was not released')
            progress({'message':'Old worker result' if search_from==0 else 'Replacement result'})
            return {'found':True,'time':search_from+1,'evidence':{'accepted':True}}

        try:
            with patch('app.find_start',side_effect=initializing):
                status,old=self.request('api/find-start',{'path':str(self.path)})
                self.assertEqual(status,200,old)
                self.assertTrue(old_entered.wait(1))
                status,_=self.request('api/find-start/'+old['id']+'/cancel',{})
                self.assertEqual(status,200)
                # No delay or polling: the old worker remains blocked in initialization.
                status,new=self.request('api/find-start',{'path':str(self.path),'search_from':5})
                self.assertEqual(status,200,new)
                self.assertNotEqual(new['id'],old['id'])
                self.assertTrue(new_entered.wait(1))
                old_release.set()
                self.store.start_searches[old['id']]['thread'].join(timeout=1)
                old_state=self.store.find_start_state(old['id'])
                new_state=self.store.find_start_state(new['id'])
                self.assertEqual(old_state['status'],'cancelled')
                self.assertIsNone(old_state['result'])
                self.assertEqual(new_state['status'],'running')
                self.assertIsNone(new_state['result'])
                self.assertNotEqual(new_state['message'],'Old worker result')
                status,duplicate=self.request('api/find-start',{'path':str(self.path)})
                self.assertEqual(status,400,duplicate)
                new_release.set()
                self.store.start_searches[new['id']]['thread'].join(timeout=1)
                result=self.store.find_start_state(new['id'])
                self.assertEqual(result['status'],'done')
                self.assertEqual(result['result']['time'],6)
        finally:
            old_release.set();new_release.set()

    def test_repeated_cancelled_initializations_have_a_resource_limit(self):
        def initializing(path,**kwargs):
            if not self.release.wait(10):raise RuntimeError('Fixture worker was not released')
            return {'found':True,'time':1,'evidence':{'accepted':True}}

        with patch('app.find_start',side_effect=initializing):
            for _ in range(4):
                status,state=self.request('api/find-start',{'path':str(self.path)})
                self.assertEqual(status,200,state)
                self.request('api/find-start/'+state['id']+'/cancel',{})
            status,result=self.request('api/find-start',{'path':str(self.path)})
            self.assertEqual(status,400,result)
            self.assertIn('释放资源',result['error'])
            self.store.close()
            self.assertTrue(all(record['cancel'].is_set() for record in self.store.start_searches.values()))
            self.release.set()
            for record in self.store.start_searches.values():record['thread'].join(timeout=1)
            self.assertTrue(all(record['state']['status']=='cancelled' for record in self.store.start_searches.values()))


if __name__=='__main__':
    unittest.main(verbosity=2)
