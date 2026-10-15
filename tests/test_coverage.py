"""Verify conservative numbered gaps and read-only legacy Job enrichment."""
import copy
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest

HERE=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(HERE))
from app import Store
from axis.model import annotate_coverage,turn_coverage


def rows(turns):
    return [dict(id=f'row-{index}',turn=turn,kind='normal',time=index*10.,confirm_time=index*10.+.5,
                 phase_start=index*10.-2,needs_review=False,reviewed=False,notes=[],
                 actions=[{'character_id':slot,'skill_raw':f'Original Skill {slot}','skill':f'Skill {slot}'} for slot in range(1,4)])
            for index,turn in enumerate(turns,1)]


class TurnCoverageTests(unittest.TestCase):
    def test_real_reported_number_pattern_has_three_gaps_and_twenty_missing_numbers(self):
        sequence=rows([1,2,3,4,4,5,6,11,11,11,22,23,23,23,23,23,23,30,30,30,30,30,30,31,32,32])
        before=copy.deepcopy(sequence)
        coverage=turn_coverage(sequence)
        self.assertEqual(coverage['status'],'gaps_detected')
        self.assertEqual(coverage['missing_turn_count'],20)
        self.assertEqual([(gap['after_turn'],gap['before_turn']) for gap in coverage['gaps']],[(6,11),(11,22),(23,30)])
        self.assertEqual(coverage['gaps'][1]['missing_turns'],list(range(12,22)))
        gap=coverage['gaps'][1]
        self.assertEqual((gap['after_row_id'],gap['before_row_id']),('row-10','row-11'))
        self.assertEqual(gap['start_time'],sequence[9]['confirm_time'])
        self.assertEqual(gap['end_time'],sequence[10]['phase_start'])
        self.assertEqual(sequence,before)
        self.assertNotIn('AUTO',gap['message'])
        self.assertIn('选招/确认',gap['message'])

    def test_repeated_turns_and_od_confirmations_do_not_add_gaps(self):
        sequence=rows([7,7,7,8,8,9])
        for row,kind in zip(sequence,['pre_od','od','normal','post_od','extra','normal']):row['kind']=kind
        coverage=turn_coverage(sequence)
        self.assertEqual(coverage,{'status':'no_numbered_gaps_detected','missing_turn_count':0,'gaps':[]})

    def test_first_turn_unknown_values_and_backward_jumps_do_not_invent_numbers(self):
        for turns in ([7],[11,7,8],[1,None,4],[1,'3',5],[1,True,4],[1,0,4],[1,1000,4],[1,3.0,5],[]):
            with self.subTest(turns=turns):
                self.assertEqual(turn_coverage(rows(turns))['gaps'],[])

    def test_missing_ids_and_unreadable_times_have_explicit_safe_values(self):
        sequence=rows([1,3])
        for row in sequence:row.pop('id')
        sequence[0]['confirm_time']=float('nan')
        sequence[1]['phase_start']=None
        gap=turn_coverage(sequence)['gaps'][0]
        self.assertEqual((gap['after_row_id'],gap['before_row_id']),('row-1','row-2'))
        self.assertIsNone(gap['start_time'])
        self.assertIsNone(gap['end_time'])

    def test_annotation_is_idempotent_and_preserves_original_actions_and_manual_notes(self):
        document={'rows':rows([6,11]),'warnings':['Manual warning']}
        document['rows'][1]['notes']=['Manual note']
        before=copy.deepcopy(document)
        annotate_coverage(document)
        gap=document['coverage']['gaps'][0]
        self.assertTrue(document['rows'][1]['needs_review'])
        self.assertEqual(document['rows'][0]['notes'],before['rows'][0]['notes'])
        self.assertEqual(document['rows'][1]['notes'],['Manual note',gap['message']])
        for after,original in zip(document['rows'],before['rows']):self.assertEqual(after['actions'],original['actions'])
        self.assertEqual(len(document['rows']),2)
        first=copy.deepcopy(document)
        annotate_coverage(document)
        self.assertEqual(document,first)

    def test_user_filling_or_correcting_turns_removes_only_generated_stale_notes_and_warnings(self):
        for fill_rows in (False,True):
            with self.subTest(fill_rows=fill_rows):
                document={'rows':rows([1,3]),'warnings':['Keep user warning']}
                document['rows'][1]['notes']=['Keep manual note']
                annotate_coverage(document)
                old_message=document['coverage']['gaps'][0]['message']
                if fill_rows:
                    inserted=rows([2])[0]
                    inserted.update(id='inserted-row',time=15,confirm_time=15.5,phase_start=14)
                    document['rows'].insert(1,inserted)
                else:
                    document['rows'][1]['turn']=2
                annotate_coverage(document)
                self.assertEqual(document['coverage']['status'],'no_numbered_gaps_detected')
                self.assertEqual(document['warnings'],['Keep user warning'])
                self.assertEqual(document['rows'][-1]['notes'],['Keep manual note'])
                self.assertTrue(all(old_message not in row['notes'] for row in document['rows']))

    def test_legacy_document_read_enriches_in_memory_and_never_rewrites_saved_evidence(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);folder=root/'jobs'/'legacy';folder.mkdir(parents=True)
            document={'title':'Legacy Fixture','created_at':'2026-10-15','rows':rows([11,22]),'warnings':['Existing warning']}
            raw=json.dumps(document,ensure_ascii=False).encode('utf8')
            for filename in ('axis.json','axis.automatic.json'):(folder/filename).write_bytes(raw)
            before=hashlib.sha256(raw).hexdigest()
            store=Store(root)
            try:
                enriched=store.document('legacy')
                self.assertEqual(enriched['coverage']['missing_turn_count'],10)
                self.assertTrue(enriched['rows'][1]['needs_review'])
                self.assertEqual(len(enriched['rows']),2)
                self.assertEqual(enriched,store.document('legacy'))
                for filename in ('axis.json','axis.automatic.json'):
                    self.assertEqual(hashlib.sha256((folder/filename).read_bytes()).hexdigest(),before)
                self.assertNotIn('coverage',json.loads((folder/'axis.json').read_bytes()))
            finally:store.close()


if __name__=='__main__':
    unittest.main(verbosity=2)
