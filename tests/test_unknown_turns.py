"""Unreadable TURN counters never imply repeated Turns or known OD timing."""
import copy
from pathlib import Path
import sys
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from axis.model import annotate_rows,compact_rows


def row(turn,bonus=None,*,when=1.,skill='Original Skill'):
    actions=[dict(character_id=index,character=f'Character {index}',character_full=f'Full Character {index}',
                  skill=skill,skill_full=f'Full {skill}',skill_raw=skill) for index in range(1,4)]
    return dict(turn=turn,bonus=bonus,time=when,confirm_time=when+.25,actions=actions,
                needs_review=False,reviewed=False,notes=['Keep manual note'],od_after=10.)


class UnknownTurnTests(unittest.TestCase):
    def test_unreadable_normal_confirmations_stay_separate_and_keep_actions(self):
        sequence=[row(None,when=1.,skill='First'),row(None,when=3.,skill='Second'),row(None,when=5.,skill='Third')]
        original=copy.deepcopy(sequence)
        annotate_rows(sequence)
        self.assertEqual([item['kind'] for item in sequence],['normal']*3)
        self.assertEqual([item['label'] for item in sequence],['待核对']*3)
        self.assertTrue(all(item['needs_review'] for item in sequence))
        self.assertTrue(all(any('TURN Counter' in note for note in item['notes']) for item in sequence))
        self.assertEqual([item['actions'] for item in sequence],[item['actions'] for item in original])
        compact=compact_rows(sequence)
        self.assertEqual(len(compact),3)
        self.assertEqual([item['confirmations'] for item in compact],[[1.25],[3.25],[5.25]])
        self.assertEqual([item['actions'][0]['skill'] for item in compact],['First','Second','Third'])

    def test_legacy_unknown_extra_rows_are_not_merged_during_compaction(self):
        first=row(None,when=1.,skill='First');first.update(kind='normal',label='待核对',od_session=None)
        second=row(None,when=2.,skill='Second');second.update(kind='extra',label='追加',od_session=None)
        compact=compact_rows([first,second])
        self.assertEqual(len(compact),2)
        self.assertEqual([item['actions'][0]['skill'] for item in compact],['First','Second'])

    def test_unknown_bonus_confirmations_do_not_claim_pre_or_post_od(self):
        sequence=[row(None),row(None,[1,2],when=3.),row(None,[1,2],when=5.),row(None,[2,2],when=7.)]
        original_actions=copy.deepcopy([item['actions'] for item in sequence])
        annotate_rows(sequence)
        for item in sequence[1:]:
            self.assertEqual(item['kind'],'od')
            self.assertEqual(item['label'],'OD（时机待核对）')
            self.assertEqual(item['od_timing'],'unknown')
            self.assertTrue(item['needs_review'])
            self.assertTrue(any('无法判定前置或后置 OD' in note for note in item['notes']))
        self.assertEqual(sequence[1]['od_action_count'],3)
        self.assertEqual(len(compact_rows(sequence)),4)
        self.assertEqual([item['actions'] for item in sequence],original_actions)

    def test_known_turn_state_remains_valid_around_unknown_confirmations(self):
        sequence=annotate_rows([row(1),row(None,when=2.),row(None,when=3.),row(2,when=4.),row(2,[1,2],when=5.)])
        self.assertEqual([item['label'] for item in sequence[:4]],['1','待核对','待核对','2'])
        self.assertEqual(sequence[-1]['kind'],'post_od')
        self.assertEqual(sequence[-1]['od_timing'],'post')

    def test_reannotation_is_idempotent_and_preserves_existing_review_notes(self):
        sequence=annotate_rows([row(None),row(None,[1,1],when=2.)])
        expected=copy.deepcopy(sequence)
        annotate_rows(sequence)
        self.assertEqual(sequence,expected)
        self.assertTrue(all(item['notes'][0]=='Keep manual note' for item in sequence))


if __name__=='__main__':
    unittest.main()
