"""Regression Tests for unknown Counters, Layout Validation and signed OD."""
import copy
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

import numpy as np

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from app import validate_profile
from axis import pipeline
from axis.model import compact_rows
from axis.vision import DEFAULT_LAYOUT,Reader


def frame_for(code):
    """Use a tiny tagged Frame; these Tests isolate Pipeline decisions from OCR."""
    frame=np.zeros((32,32,3),np.uint8)
    frame[0,0]=code
    return frame


class CounterFixtureVideo:
    def __init__(self,path,layout=None,*,states):
        self.path=Path(path)
        self.layout=copy.deepcopy({**DEFAULT_LAYOUT,**(layout or {})})
        self.states=states
        self.duration=float(len(states)+1)
        self.fps=4.
        self.frames=round(self.duration*self.fps)

    def sample(self,start=0,end=None,rate=4):
        for index in range(len(self.states)):
            for when,code in ((index,index+1),(index+.25,index+1),(index+.5,0)):
                if when>=start and (end is None or when<end):
                    yield float(when),frame_for(code)

    def frame(self,when):
        return frame_for(0)

    def metadata(self):
        return dict(path=str(self.path),name=self.path.name,duration=self.duration,
                    width=1920,height=1080,fps=self.fps,frames=self.frames)

    def close(self):
        pass


class CounterFixtureReader:
    def __init__(self,layout,aliases=None,*,states):
        self.layout=layout
        self.states=states

    def roles(self,frame):
        return ['']*6

    def od_menu(self,frame):
        return False

    def status(self,frame):
        code=int(frame[0,0,0])
        turn,bonus=self.states[code-1] if code else (None,None)
        return dict(turn=turn,bonus=copy.deepcopy(bonus),od=25.,
                    raw=dict(turn=f'TURN{turn}' if turn is not None else '',
                             bonus=f'BONUS{bonus[0]}/{bonus[1]}' if bonus else '',od='25%'),
                    confidence=dict(turn=.99 if turn is not None else .1,
                                    bonus=.99 if bonus else .1,od=.99),
                    sources=dict(turn='recognition',bonus='recognition'))

    def skills(self,frame):
        code=int(frame[0,0,0])
        return [dict(skill_raw=f'Visible Skill {code}.{slot}',
                     skill_full=f'Visible Skill {code}.{slot}',
                     skill=f'Visible Skill {code}.{slot}',skill_confidence=1.)
                for slot in range(1,4)]


class CounterFixtureAvatars:
    def __init__(self,*args):
        self.images=[np.zeros((72,78,3),np.uint8) for _ in range(6)]

    def button_state(self,frame):
        pink=bool(frame[0,0,0])
        return pink,not pink,1.

    def od_menu(self,frame):
        return False

    def match(self,frame):
        return True,np.full((6,6),.95),1.

    def assign(self,*args):
        return list(range(6)),[.95]*6

    def availability(self,frame):
        return [1.]*3


class PipelineCounterRegressionTests(unittest.TestCase):
    def analyze_states(self,states):
        with tempfile.TemporaryDirectory() as temporary:
            folder=Path(temporary)
            video_factory=lambda path,layout=None:CounterFixtureVideo(path,layout,states=states)
            reader_factory=lambda layout,aliases=None:CounterFixtureReader(layout,aliases,states=states)
            with patch.object(pipeline,'Video',side_effect=video_factory), \
                 patch.object(pipeline,'Reader',side_effect=reader_factory), \
                 patch.object(pipeline,'Avatars',CounterFixtureAvatars), \
                 patch.object(pipeline,'bootstrap_evidence',return_value={'accepted':True}), \
                 patch.object(pipeline,'save_image'):
                return pipeline.analyze(folder/'fixture.mp4',folder/'output',
                                        profile={'name_mode':'placeholder'})

    def test_unreadable_turn_without_bonus_does_not_inherit_or_compact(self):
        document=self.analyze_states([(1,None),(None,None),(2,None)])
        rows=document['rows']
        self.assertEqual([row['turn'] for row in rows],[1,None,2])
        self.assertEqual(rows[1]['kind'],'normal')
        self.assertTrue(rows[1]['needs_review'])
        self.assertEqual(rows[1]['actions'][0]['skill_raw'],'Visible Skill 2.1')
        compact=compact_rows(rows)
        self.assertEqual(len(compact),3)
        self.assertEqual([row['confirmations'] for row in compact],[[.5],[1.5],[2.5]])

    def test_bonus_without_readable_turn_keeps_unknown_timing_and_confirmations(self):
        document=self.analyze_states([(1,None),(None,[1,2]),(None,[1,2]),(None,[2,2])])
        rows=document['rows']
        self.assertEqual([row['turn'] for row in rows],[1,None,None,None])
        self.assertEqual([row['bonus'] for row in rows],[None,[1,2],[1,2],[2,2]])
        for row in rows[1:]:
            self.assertNotIn(row['kind'],('pre_od','post_od','extra'))
            self.assertEqual(row.get('od_timing'),'unknown')
            self.assertTrue(row['needs_review'])
        self.assertEqual(len(compact_rows(rows)),4)

    def test_conflicting_bonus_totals_preserve_each_observed_fraction(self):
        fractions=[[1,2],[1,2],[2,3],[3,3]]
        document=self.analyze_states([(1,bonus) for bonus in fractions])
        rows=document['rows']
        self.assertEqual([row['bonus'] for row in rows],fractions)
        self.assertTrue(all(1<=row['bonus'][0]<=row['bonus'][1] for row in rows))
        self.assertEqual([row['ocr_status']['raw']['bonus'] for row in rows],
                         ['BONUS1/2','BONUS1/2','BONUS2/3','BONUS3/3'])


class PortraitRadiiValidationTests(unittest.TestCase):
    def test_six_finite_positive_radius_pairs_are_accepted(self):
        radii=[[80,90],[81.5,90.5],[80,90],[70,80],[70,80],[70,80]]
        profile=validate_profile({'layout':{'portrait_radii':radii}})
        self.assertEqual(profile['layout']['portrait_radii'],radii)
        self.assertEqual(validate_profile({})['layout']['portrait_centers'],
                         DEFAULT_LAYOUT['portrait_centers'])

    def test_invalid_radius_shapes_and_numbers_are_rejected(self):
        invalid=[[],[[80,90]],[[80,90]]*5,[[80,90]]*7,'invalid',True,None,
                 [[80]],[[80,90,100]]*6,[[0,90]]*6,[[-1,90]]*6,
                 [[80,0]]*6,[[80,-1]]*6,[[float('nan'),90]]*6,
                 [[80,float('inf')]]*6,[['80',90]]*6,[[True,90]]*6,
                 [[80,False]]*6]
        for radii in invalid:
            with self.subTest(radii=radii):
                with self.assertRaises(ValueError):
                    validate_profile({'layout':{'portrait_radii':radii}})


class SignedODRegressionTests(unittest.TestCase):
    def status_for(self,text):
        reader=object.__new__(Reader)
        reader.layout=DEFAULT_LAYOUT
        reader.lines=lambda patches:[('TURN1',.99),('',.1),(text,.99)]
        return reader.status(np.zeros((1080,1920,3),np.uint8))

    def test_signed_od_keeps_the_visible_sign_and_raw_text(self):
        for text,expected in [('-0.07%',-.07),('+0.02%',.02),('0%',0.),
                              ('25.6%',25.6),('300%',300.)]:
            with self.subTest(text=text):
                status=self.status_for(text)
                self.assertEqual(status['od'],expected)
                self.assertEqual(status['raw']['od'],text)

    def test_unlabeled_or_conflicting_numbers_do_not_become_od(self):
        for text in ('25','OD2 50%','1.2.3%',''):
            with self.subTest(text=text):
                self.assertIsNone(self.status_for(text)['od'])


if __name__=='__main__':
    unittest.main()
