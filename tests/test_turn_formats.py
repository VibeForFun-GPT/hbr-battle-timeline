"""TURN parsing accepts explicit UI labels, never unrelated scene numbers."""
from pathlib import Path
import sys
import unittest

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from axis.vision import DEFAULT_LAYOUT, Reader, parse_counter, parse_turn


class TurnFormatTests(unittest.TestCase):
    def test_labeled_single_turns(self):
        for text, expected in [("TURN1", 1), ("TURN 12", 12), ("turn 99", 99),
                               (" TURN\n111 ", 111), ("TURN999", 999)]:
            with self.subTest(text=text):
                self.assertEqual(parse_turn(text), expected)

    def test_existing_explicit_turn_counters(self):
        for text, expected in [("TURN1/9", 1), ("TURN 12 / 99", 12),
                               ("TURN3\\12", 3), ("TURN3.12", 3),
                               ("TURN1/1O", 1), ("TURN999/999", 999)]:
            with self.subTest(text=text):
                self.assertEqual(parse_turn(text), expected)

    def test_previous_unique_slash_ocr_repair_is_retained(self):
        self.assertEqual(parse_turn("TURN7130"), 7)
        self.assertEqual(parse_turn("TURN111"), 111)

    def test_unlabeled_numbers_and_wrong_labels_are_rejected(self):
        for text in ["1", "12/99", "BONUS1/3", "RETURN1", "TURNER1",
                     "not TURN1", "TURN1 extra", "TURN", "", None]:
            with self.subTest(text=text):
                self.assertIsNone(parse_turn(text))

    def test_invalid_ranges_and_malformed_counters_are_rejected(self):
        for text in ["TURN0", "TURN000", "TURN1000", "TURN9999", "TURN-1",
                     "TURN1/0", "TURN2/1", "TURN1/1000", "TURN1000/1001",
                     "TURN1/", "TURN/2", "TURN1/2/3"]:
            with self.subTest(text=text):
                self.assertIsNone(parse_turn(text))

    def test_reader_status_uses_single_turn_and_keeps_other_readings(self):
        reader = object.__new__(Reader)
        reader.layout = DEFAULT_LAYOUT
        reader.lines = lambda patches: [("TURN1", .95), ("BONUS1/3", .94), ("68.4%", .96)]
        status = reader.status(np.zeros((1080, 1920, 3), dtype=np.uint8))
        self.assertEqual(status["turn"], 1)
        self.assertEqual(status["bonus"], [1, 3])
        self.assertEqual(status["od"], 68.4)
        self.assertEqual(status["raw"]["turn"], "TURN1")

    def test_reader_still_rejects_low_confidence_and_bonus_parser_is_unchanged(self):
        reader = object.__new__(Reader)
        reader.layout = DEFAULT_LAYOUT
        reader.lines = lambda patches: [("TURN1", .59), ("BONUS1/3", .94), ("0%", .96)]
        self.assertIsNone(reader.status(np.zeros((1080, 1920, 3), dtype=np.uint8))["turn"])
        self.assertIsNone(parse_counter("BONUS1", "BONUS"))
        self.assertEqual(parse_counter("BONUS 2/3", "BONUS"), [2, 3])


if __name__ == "__main__":
    unittest.main()
