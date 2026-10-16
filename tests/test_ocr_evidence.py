"""Regression coverage for UI Counter variants and conflicting Skill Evidence."""
from pathlib import Path
import sys
import unittest

import numpy as np

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from axis.counter_detection import grouped_counter_text, reconcile_turn_counters
from axis.vision import DEFAULT_LAYOUT, Reader
from axis.model import annotate_rows


def detection(text,x,y=25,width=40,score=.96):
    return ([[x,y],[x+width,y],[x+width,y+20],[x,y+20]],text,score)


class CounterEvidenceTests(unittest.TestCase):
    def test_unreadable_bonus_keeps_session_context_without_filling_counter(self):
        rows=annotate_rows([{'turn':3,'bonus':[1,2]},
                            {'turn':3,'bonus':None,'bonus_unreadable':True},
                            {'turn':3,'bonus':[2,2]}])
        self.assertEqual([r['od_session'] for r in rows],[1,1,1])
        self.assertIsNone(rows[1]['bonus'])
        self.assertEqual(rows[2]['kind'],'od')

    def test_first_visible_second_bonus_does_not_claim_an_od_start(self):
        row=annotate_rows([{'turn':3,'bonus':[2,2]}])[0]
        self.assertEqual(row['od_timing'],'unknown')
        self.assertEqual(row['kind'],'od')
        self.assertTrue(row['needs_review'])

    def test_shared_explicit_total_repairs_dropped_slash_and_keeps_evidence(self):
        rows=[{'turn':3,'ocr_status':{'raw':{'turn':'TURN3/30'}}},
              {'turn':330,'ocr_status':{'raw':{'turn':'TURN330'}}},
              {'turn':430,'ocr_status':{'raw':{'turn':'TURN430'}}},
              {'turn':999,'ocr_status':{'raw':{'turn':'TURN999'}}},
              {'turn':None,'ocr_status':{'raw':{'turn':''}}}]
        reconcile_turn_counters(rows)
        self.assertEqual([row['turn'] for row in rows],[3,3,4,None,None])
        self.assertEqual(rows[1]['turn_original'],330)
        self.assertTrue(rows[1]['needs_review'])

    def test_unfractioned_or_conflicting_totals_do_not_rewrite_turns(self):
        for rows in ([{'turn':330,'ocr_status':{'raw':{'turn':'TURN330'}}}],
                     [{'turn':1,'ocr_status':{'raw':{'turn':'TURN1/30'}}},
                      {'turn':1,'ocr_status':{'raw':{'turn':'TURN1/50'}}},
                      {'turn':330,'ocr_status':{'raw':{'turn':'TURN330'}}}]):
            reconcile_turn_counters(rows)
            self.assertEqual(rows[-1]['turn'],330)

    def test_split_counter_uses_only_the_same_horizontal_line(self):
        records=[detection('TURN',0,100,70),detection('2',80,94,20),detection('/30',105,100),
                 detection('BONUS',0,20,90),detection('1/2',100,20),detection('999',0,160)]
        self.assertEqual(grouped_counter_text(records,'TURN'),('TURN2/30',.96))
        self.assertEqual(grouped_counter_text(records,'BONUS'),('BONUS1/2',.96))

    def test_unlabeled_digits_and_conflicting_counters_are_rejected(self):
        self.assertIsNone(grouped_counter_text([detection('1/3',0)],'BONUS'))
        self.assertIsNone(grouped_counter_text([detection('TURN1',0,10),detection('TURN2',0,100)],'TURN'))

    def test_large_numerator_and_small_denominator_are_not_merged_into_turn230(self):
        large=([[80,10],[100,10],[100,40],[80,40]],'2',.99)
        records=[detection('TURN',0,20,70),large,detection('30',120,20,40)]
        self.assertEqual(grouped_counter_text(records,'TURN'),('TURN2/30',.8))

    def test_bonus_can_replace_turn_in_the_same_ui_area(self):
        reader=object.__new__(Reader)
        reader.layout=DEFAULT_LAYOUT
        reader.lines=lambda patches:[('BONUS1/2',.95),('',.1),('',.1)]
        result=reader.status(np.zeros((1080,1920,3),np.uint8))
        self.assertIsNone(result['turn'])
        self.assertEqual(result['bonus'],[1,2])

    def test_detection_recovers_split_counter_and_retains_raw_evidence(self):
        reader=object.__new__(Reader)
        reader.layout=DEFAULT_LAYOUT
        reader.lines=lambda patches:[('TURN230',.88),('',.1),('25%',.99)]
        reader.ocr=lambda *a,**k:([detection('TURN',0,80,70),detection('2',80,80,20),detection('/30',105,80),
                                  detection('BONUS',0,10,90),detection('1/2',100,10)],None)
        result=reader.status(np.zeros((1080,1920,3),np.uint8))
        self.assertEqual(result['turn'],2)
        self.assertEqual(result['bonus'],[1,2])
        self.assertEqual(result['sources']['turn'],'detection')
        self.assertEqual(result['raw']['turn'],'TURN2/30')


class SkillEvidenceTests(unittest.TestCase):
    def test_longer_recognition_with_ascii_debris_does_not_replace_detection(self):
        reader=object.__new__(Reader)
        reader.layout=DEFAULT_LAYOUT
        reader.aliases={}
        reader.ocr=lambda *a,**k:([detection('石塔的手筋+',5,10,200),detection('振',320,10,30)],None)
        reader.lines=lambda patches:[('石塔的手筋+#',.99),('振',.99),('',0.)]
        actions=reader.skills(np.zeros((1080,1920,3),np.uint8))
        self.assertEqual(actions[0]['skill_raw'],'石塔的手筋+')
        self.assertTrue(actions[0]['skill_ocr_disagreement'])
        self.assertEqual(actions[0]['skill_ocr_alternatives']['recognition'],'石塔的手筋+#')
        self.assertTrue(actions[1]['skill_suspicious'])


if __name__=='__main__':
    unittest.main()
