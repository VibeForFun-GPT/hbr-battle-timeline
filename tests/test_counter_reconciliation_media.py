"""Optional regression checks against locally retained public-video OCR evidence."""
import copy
import hashlib
import json
import os
from pathlib import Path
import sys
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from axis.counter_detection import reconcile_turn_counters
from axis.model import annotate_coverage,annotate_rows


@unittest.skipUnless(os.environ.get('HBR_COUNTER_TEST_MEDIA'),'Optional Counter integration requires local validation OCR evidence')
class RealMediaCounterReconciliationTests(unittest.TestCase):
    def evidence(self):
        path=Path(os.environ['HBR_COUNTER_TEST_MEDIA'])/'video-a/final/axis.json'
        return path,json.loads(path.read_text(encoding='utf-8'))

    def test_impossible_turns_are_repaired_without_changing_actions_or_source(self):
        path,document=self.evidence()
        original=hashlib.sha256(path.read_bytes()).hexdigest()
        rows=copy.deepcopy(document['rows'])
        before=copy.deepcopy(rows)
        reconcile_turn_counters(rows)
        changes=[(index+1,row['turn_original'],row['turn']) for index,row in enumerate(rows) if 'turn_original' in row]
        self.assertEqual(changes,[(5,330,3),(6,330,3),(8,330,3),(9,330,3),(11,430,4)])
        expected=[1,2,None,3,3,3,3,3,3,4,4,4,4,4,4,4,4,None,4,4,4,4,4]
        self.assertEqual([row['turn'] for row in rows],expected)
        for old,new in zip(before,rows):
            for key in ('actions','screenshot','time','confirm_time','ocr_status'):
                self.assertEqual(old[key],new[key])
            if 'turn_original' in new:
                self.assertTrue(new['needs_review'])
                self.assertEqual(new['turn_repair_source'],'explicit_video_counter_total')
        self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(),original)

    def test_repaired_real_evidence_removes_false_gaps_but_keeps_unknowns(self):
        _,document=self.evidence()
        self.assertGreater(document['coverage']['missing_turn_count'],0)
        reconcile_turn_counters(document['rows'])
        annotate_rows(document['rows'])
        annotate_coverage(document)
        self.assertEqual(document['coverage']['missing_turn_count'],0)
        self.assertIsNone(document['rows'][2]['turn'])
        self.assertIsNone(document['rows'][17]['turn'])
        self.assertEqual(document['rows'][2]['label'],'待核对')
        self.assertEqual(document['rows'][17]['label'],'待核对')
        repaired=copy.deepcopy(document['rows'])
        reconcile_turn_counters(document['rows'])
        self.assertEqual(document['rows'],repaired)


if __name__=='__main__':
    unittest.main()
