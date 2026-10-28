"""Revision 2.5, integration (28 Oct): the closer look's page search as the runner calls it (box=None), the dot rule
with one confirmed dot pattern, and the combination of several places. MADE-UP names and synthetic images only."""
from __future__ import annotations

import unittest

import numpy as np

from src.verify.runner import closer as C
from src.verify.runner import strict as S
from src.verify.runner.tests.test_closer import _have_models, page


def _look(decision, best="A", dot=None, note="n"):
    return C.Look(decision, best, {"A": -0.1, "B": -1.0}, 0.9, 28, 28, dot, note, {"box": [1, 2, 3, 4]})


class TestDotRuleOneConfirmedPattern(unittest.TestCase):
    """CHECK_0001: the passport prints "M.", the employment certificate's no-dot is confirmed, the NID and the TIN
    read "M." but their closer looks were unclear. The confirmed different document is never PASS."""

    def test_confirmed_difference_with_an_unconfirmed_follower_is_flag(self):
        out = S.dot_rule("M. SAMPLE TOKAR", [("NID", ["M. SAMPLE TOKAR"], False),
                                             ("TIN", ["M. SAMPLE TOKAR"], False),
                                             ("EMP", ["M SAMPLE TOKAR"], True)])
        self.assertEqual(out, {"NID": "PASS", "TIN": "PASS", "EMP": "FLAG"})

    def test_confirmed_mix_still_fails(self):
        out = S.dot_rule("M. SAMPLE TOKAR", [("NID", ["M. SAMPLE TOKAR"], True),
                                             ("EMP", ["M SAMPLE TOKAR"], True)])
        self.assertEqual(out, {"NID": "PASS", "EMP": "FAIL"})

    def test_every_other_document_dotted_against_a_plain_passport_is_assumed_ok(self):
        out = S.dot_rule("MD SAMPLE", [("A", ["MD. SAMPLE"], True), ("B", ["MD. SAMPLE"], False)])
        self.assertEqual(out, {"A": "PASS", "B": "PASS"})

    def test_confirmed_follower_and_unconfirmed_other_unchanged(self):
        out = S.dot_rule("MD. X", [("A", ["MD. X"], True), ("B", ["MD X"], False)])
        self.assertEqual(out, {"A": "PASS", "B": "FLAG"})


class TestCombinePlaces(unittest.TestCase):
    def test_the_same_decision_everywhere(self):
        res = C._combine([_look("same"), _look("same")], "name")
        self.assertEqual(res.decision, "same")
        self.assertEqual(res.detail["places"], 2)

    def test_reference_in_one_place_and_the_variant_in_another_is_unclear(self):
        res = C._combine([_look("same"), _look("differs", "B")], "name")
        self.assertEqual(res.decision, "unclear")
        self.assertIn("more than one place", res.note)
        self.assertIsNone(res.dot)

    def test_an_unsettled_place_is_unclear(self):
        self.assertEqual(C._combine([_look("same"), _look("unclear")], "name").decision, "unclear")
        self.assertEqual(C._combine([_look("differs", "B"), _look("unclear")], "name").decision, "unclear")

    def test_dots_must_agree_too(self):
        res = C._combine([_look("differs", "B", dot=True), _look("differs", "B", dot=False)], "dots")
        self.assertEqual(res.decision, "unclear")
        res = C._combine([_look("same", dot=True), _look("same", dot=True)], "dots")
        self.assertEqual((res.decision, res.dot), ("same", True))


class TestReferencePlaces(unittest.TestCase):
    """The reference's own page printing the name twice (a passport's Father field and its emergency contact): the
    settled places decide the reference's dots when they agree and none says otherwise."""

    def test_one_settled_place_and_one_unsettled(self):
        from src.verify.runner import names as N
        res = C._combine([_look("same", "M. SAMPLE TOKAR", dot=True), _look("unclear")], "dots")
        self.assertEqual(res.decision, "unclear")
        self.assertEqual(N._reference_places(res), "M. SAMPLE TOKAR")

    def test_places_that_disagree_settle_nothing(self):
        from src.verify.runner import names as N
        res = C._combine([_look("same", "M. SAMPLE TOKAR", dot=True), _look("differs", "M SAMPLE TOKAR", dot=False)],
                         "dots")
        self.assertIsNone(N._reference_places(res))
        self.assertIsNone(N._reference_places(C._combine([_look("unclear"), _look("unclear")], "dots")))
        self.assertIsNone(N._reference_places(_look("unclear")))
        self.assertIsNone(N._reference_places(None))


def _line_image(words_x, h=30, W=900, H=120, rule_x=None):
    """Black blocks standing for words on one line (x0, x1 pairs), an optional vertical rule (a table border)."""
    img = np.full((H, W, 3), 250, np.uint8)
    y0 = 45
    for x0, x1 in words_x:
        for x in range(x0, x1, 9):                    # letters: 6 px strokes with 3 px gaps
            img[y0 + 4:y0 + h - 4, x:min(x + 6, x1)] = 20
    if rule_x is not None:
        img[5:H - 5, rule_x:rule_x + 2] = 20
    return img, (float(words_x[0][0]), float(y0), float(words_x[0][1]), float(y0 + h))


class TestExtendInk(unittest.TestCase):
    def test_a_missed_word_after_the_box_is_taken_in(self):
        img, box = _line_image([(100, 220), (245, 330)])
        x0, y0, x1, y1 = C._extend_ink(img, box)
        self.assertGreaterEqual(x1, 325)
        self.assertLessEqual(x0, 101)

    def test_a_far_field_is_not_taken_in(self):
        img, box = _line_image([(100, 220), (400, 480)])          # 180 px = 6 line heights away
        self.assertLess(C._extend_ink(img, box)[2], 260)

    def test_a_table_border_stops_it(self):
        img, box = _line_image([(100, 220), (245, 330)], rule_x=232)
        self.assertLess(C._extend_ink(img, box)[2], 232)

    def test_right_only_keeps_the_left_edge(self):
        img, box = _line_image([(20, 80), (100, 220)])
        box = (100.0, box[1], 220.0, box[3])
        self.assertEqual(C._extend_ink(img, box, right_only=True)[0], 100.0)
        self.assertLessEqual(C._extend_ink(img, box)[0], 21)


@unittest.skipUnless(_have_models(), "the ONNX models and Windows fonts are needed")
class TestPageSearchPlaces(unittest.TestCase):
    def test_reference_in_a_footer_and_a_short_form_in_a_row_is_never_same(self):
        img = page([("Name of Holder :", "SAMPLE TOKAR"), ("Date :", "01.02.2026"),
                    ("Father :", "MD SAMPLE TOKAR RAFIN")])
        res = C.look(img, None, ["MD SAMPLE TOKAR RAFIN", "SAMPLE TOKAR"], "name")
        self.assertNotEqual(res.decision, "same")

    def test_a_dropped_last_word_read_is_a_misread(self):
        img = page([("Name :", "Fanila Kesum"), ("Roll :", "123456")])
        res = C.look(img, None, ["Fanila Kesum", "Fanila"], "name")
        self.assertNotEqual(res.decision, "differs")


if __name__ == "__main__":
    unittest.main()
