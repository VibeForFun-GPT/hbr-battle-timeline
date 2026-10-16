"""Verify geometric Layout candidates using independent synthetic rings."""
import copy
import os
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

import cv2
import numpy as np

HERE=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(HERE))
from axis.layout_detection import detect_layout
from axis.vision import DEFAULT_LAYOUT,Video


def selection(*,wide=False,count=6,button=True):
    frame=np.full((1080,1920,3),(35,50,30),np.uint8)
    centers=([310,550,790,1000,1210,1420] if wide else [200,480,760,1030,1280,1530])
    rng=np.random.default_rng(127)
    points=[]
    for index,x in enumerate(centers[:count]):
        y=912 if index<3 else 921
        rx,ry=((80,96) if index<3 else (67,80)) if wide else ((94,94) if index<3 else (80,80))
        pixels=rng.integers(40,180,(ry*2,rx*2,3),dtype=np.uint8)
        frame[y-ry:y+ry,x-rx:x+rx]=pixels
        cv2.ellipse(frame,(x,y),(rx,ry),0,0,360,(160,35,190),12)
        cv2.putText(frame,'BUF',(x-rx,y-ry//2),cv2.FONT_HERSHEY_SIMPLEX,.6,(255,255,255),2)
        points.append((x,y))
    if button:
        center=(1660,906) if wide else (1760,912)
        axes=(102,122) if wide else (120,120)
        cv2.ellipse(frame,center,axes,0,0,360,(160,35,190),-1)
        cv2.ellipse(frame,center,axes,0,0,360,(225,145,240),5)
        cv2.putText(frame,'ACTION',(center[0]-65,center[1]+8),cv2.FONT_HERSHEY_SIMPLEX,.65,(255,255,255),2)
    return frame,points


class LayoutDetectionTests(unittest.TestCase):
    def candidate(self,x,y=300,rx=25,ry=30):
        return dict(x=x,y=y,rx=rx,ry=ry,support=.9,contrast=.7,filled=.9)

    def test_six_rings_and_button_produce_ordered_layout_anchors(self):
        frame,points=selection()
        candidate=detect_layout(frame)
        self.assertIsNotNone(candidate)
        layout=candidate['layout']
        self.assertEqual(len(layout['portrait_centers']),6)
        for actual,expected in zip(layout['portrait_centers'],points):
            self.assertLess(np.linalg.norm(np.array(actual)-expected),45)
        self.assertEqual(len(layout['skill_boxes']),3)
        self.assertEqual(len(layout['role_boxes']),6)
        self.assertEqual(len(layout['portrait_radii']),6)
        self.assertTrue(all(rx>0 and ry>0 for rx,ry in layout['portrait_radii']))
        self.assertEqual(len(candidate['evidence']['avatars']),6)
        for key in ('portrait_boxes','role_boxes','skill_boxes'):
            for x1,y1,x2,y2 in layout[key]:self.assertTrue(0<=x1<x2<=1920 and 0<=y1<y2<=1080)

    def test_wide_elliptical_avatars_and_shifted_row_preserve_counter_calibration(self):
        frame,points=selection(wide=True)
        base=copy.deepcopy(DEFAULT_LAYOUT)
        base.update(crop=[.05,0,.95,1],turn_box=[70,620,370,740],od_box=[1500,90,1770,170])
        before=copy.deepcopy(base)
        candidate=detect_layout(frame,base)
        self.assertIsNotNone(candidate)
        self.assertEqual(base,before)
        for key in ('crop','od_box'):
            self.assertEqual(candidate['layout'][key],base[key])
        for key in ('turn_box','bonus_box'):
            original=base[key];actual=candidate['layout'][key]
            self.assertLessEqual(actual[0],original[0])
            self.assertGreaterEqual(actual[2],original[2])
            self.assertEqual(actual[1],original[1]-30)
            self.assertEqual(actual[3],original[3]+30)
        for actual,expected in zip(candidate['layout']['portrait_centers'],points):
            self.assertLess(np.linalg.norm(np.array(actual)-expected),45)

    def test_missing_ring_or_confirm_button_never_yields_a_layout(self):
        self.assertIsNone(detect_layout(selection(count=5)[0]))
        self.assertIsNone(detect_layout(selection(button=False)[0]))

    def test_nested_confirm_boundary_cannot_replace_missing_first_avatar(self):
        frame=selection()[0]
        # Five actual Avatars plus the large outer Confirm boundary. A smaller
        # partial circle in the button must not make that boundary a sixth slot.
        rings=[self.candidate(x) for x in (167,263,337,407,478)]
        rings.append(self.candidate(558,rx=32,ry=38))
        buttons=[self.candidate(576,rx=21,ry=29)]
        with patch('axis.layout_detection._rings',return_value=rings),patch('axis.layout_detection._buttons',return_value=buttons):
            self.assertIsNone(detect_layout(frame))

    def test_later_six_slot_group_cannot_skip_a_separate_left_avatar(self):
        frame=selection()[0]
        rings=[self.candidate(x,rx=17,ry=21) for x in (55,125,195,265,335,405,475)]
        buttons=[self.candidate(575,rx=30,ry=35)]
        with patch('axis.layout_detection._rings',return_value=rings),patch('axis.layout_detection._buttons',return_value=buttons):
            candidate=detect_layout(frame)
        self.assertIsNotNone(candidate)
        self.assertEqual(candidate['layout']['portrait_centers'][0][0],165)

    def test_shifted_group_without_left_avatar_is_rejected(self):
        frame=selection()[0]
        rings=[self.candidate(x,rx=17,ry=21) for x in (166,238,310,382,454,526)]
        buttons=[self.candidate(602,rx=30,ry=35)]
        with patch('axis.layout_detection._rings',return_value=rings),patch('axis.layout_detection._buttons',return_value=buttons):
            self.assertIsNone(detect_layout(frame))

    def test_weak_arc_between_last_avatar_and_button_is_not_a_slot(self):
        frame=selection()[0]
        rings=[self.candidate(x) for x in (80,164,252,336,408,507)]
        rings[-1]['contrast']=.22
        buttons=[self.candidate(575,rx=21,ry=30)]
        with patch('axis.layout_detection._rings',return_value=rings),patch('axis.layout_detection._buttons',return_value=buttons):
            self.assertIsNone(detect_layout(frame))

    def test_magenta_effects_and_invalid_images_are_rejected(self):
        effect=np.full((1080,1920,3),(160,35,190),np.uint8)
        for frame in (effect,np.zeros_like(effect),None,np.zeros((1080,1920),np.uint8)):
            self.assertIsNone(detect_layout(frame))


@unittest.skipUnless(os.environ.get('HBR_LAYOUT_TEST_MEDIA'),'Optional Layout integration requires local validation media')
class RealMediaLayoutTests(unittest.TestCase):
    def frame(self,relative,when):
        video=Video(Path(os.environ['HBR_LAYOUT_TEST_MEDIA'])/relative)
        try:return video.frame(when)
        finally:video.close()

    def assert_centers(self,candidate,expected):
        self.assertIsNotNone(candidate)
        for actual,point in zip(candidate['layout']['portrait_centers'],expected):
            self.assertLess(np.linalg.norm(np.array(actual)-point),45)

    def test_low_resolution_selection_preserves_all_six_slots(self):
        self.assert_centers(detect_layout(self.frame('video-a/video-a.mp4',31.037)),
                            [(315,900),(550,900),(780,900),(1008,900),(1205,900),(1395,900)])

    def test_action_animation_has_no_selection_layout(self):
        self.assertIsNone(detect_layout(self.frame('video-a/video-a.mp4',101.622)))

    def test_nested_button_does_not_shift_high_resolution_team(self):
        self.assert_centers(detect_layout(self.frame('video-c/video.mp4',37)),
                            [(240,905),(495,905),(765,905),(1005,905),(1225,905),(1440,905)])

    def test_ambiguous_last_ring_requires_correct_center_or_rejection(self):
        candidate=detect_layout(self.frame('video-c/video.mp4',39.5))
        if candidate is not None:
            self.assert_centers(candidate,[(240,905),(495,905),(765,905),(1005,905),(1225,905),(1440,905)])


if __name__=='__main__':
    unittest.main(verbosity=2)
