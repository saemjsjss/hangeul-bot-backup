"""Revision 2.6, the fixer after the independent check (28 Oct): the single-candidate look reads the words beside the
value, an added word the closer look cannot settle is FLAG (never the same name), and the dot probe. MADE-UP names
and synthetic images only.
"""
from __future__ import annotations

import unittest

from src.verify.runner import closer as C
from src.verify.runner import names as N
from src.verify.runner import strict as S
from src.verify.runner.model import FAIL, FLAG
from src.verify.runner.tests.test_closer import _have_models, render
from src.verify.runner.tests.test_names import (CloserFake, academic, base_case, ident_of, rules, run,
                                                setUpModule)  # noqa: F401  (setUpModule loads the bot)


def toks(spec):
    """Tokens from "text@frame" pieces: each character one frame apart from the given start."""
    out = []
    for piece in spec:
        text, at = piece.rsplit("@", 1)
        t = int(at)
        for ch in text:
            out.append((ch, t, t))
            t += 1
    return out


class TestBesideWords(unittest.TestCase):
    """closer.beside_words on hand-made token runs: the value's region is frames 50-80, a line height is 10 frames."""

    def beside(self, spec, kind="name"):
        return C.beside_words(toks(spec), 50, 80, 10.0, kind)

    def test_an_added_word_before_or_after_is_found(self):
        self.assertEqual(self.beside(["MST@44"]), [("before", "MST")])
        self.assertEqual(self.beside(["TOVIRA@84"]), [("after", "TOVIRA")])
        self.assertEqual(self.beside(["MD.@45"]), [("before", "MD")])

    def test_a_label_a_title_a_number_or_a_far_word_ends_the_value(self):
        self.assertEqual(self.beside(["Name:@40"]), [])
        self.assertEqual(self.beside(["Name of Student@30"]), [])          # label words, no colon
        self.assertEqual(self.beside(["son of@84"]), [])
        self.assertEqual(self.beside(["sonof@84"]), [])                    # read without the space
        self.assertEqual(self.beside(["Mr.@45"]), [])                      # Q16: a leading title is dropped
        self.assertEqual(self.beside(["12345@84"]), [])                    # another field
        self.assertEqual(self.beside(["TOVIRA@120"]), [])                  # beyond RUN_GAP line heights
        self.assertEqual(self.beside(["Roll:@84"]), [])                    # the next field's label

    def test_unknown_words_are_never_labels(self):
        self.assertEqual(self.beside(["asis@84"]), [("after", "ASIS")])    # two-letter pieces alone: no label
        self.assertEqual(self.beside(["Mr@84"]), [("after", "MR")])        # a title AFTER a name is no title
        self.assertEqual(self.beside([", TOVIRA@83"]), [("after", "TOVIRA")])    # punctuation is passed over

    def test_the_next_fields_label_ends_the_value(self):
        self.assertEqual(self.beside(["Date of Birth@84"]), [])            # no colon: a label all the same
        self.assertEqual(self.beside(["DATEOFBIRTH@84"]), [])
        self.assertEqual(self.beside(["Roll No@84"]), [])
        self.assertEqual(self.beside(["Begum@84"]), [("after", "BEGUM")])  # a name word is never a label

    def test_an_mrz_header_before_the_surname_is_no_added_word(self):
        self.assertEqual(self.beside(["P<XYZ@45"]), [])                    # document code + issuing state, glued
        self.assertEqual(self.beside(["MD<@46"]), [("before", "MD")])      # anything else stays a further word
        self.assertEqual(self.beside(["PXYZQ@45"]), [("before", "PXYZQ")])  # without a '<' it is no MRZ header

    def test_a_number_beside_a_number_belongs_to_it(self):
        self.assertEqual(self.beside(["12345@84"], kind="digits"), [("after", "12345")])

    def test_line_frames(self):
        import numpy as np
        g = np.zeros((100, 400), np.uint8)              # p0.25: the line is 100 / 1.5 px of 100; 4 x 64 model px
        self.assertAlmostEqual(C.line_frames("p0.25/gray", g, 64), (64 / 1.5) * 64 / 256.0)


@unittest.skipUnless(_have_models(), "the ONNX models or the fonts are not on this PC")
class TestSingleCandidateLook(unittest.TestCase):
    """names.prints asks the closer look for ONE spelling: a word printed beside it is no longer unseen."""

    def test_an_added_word_beside_the_value_is_never_same(self):
        for text in ("Mst Zelira Phatun", "Zelira Phatun Tovira", "MD ZELIRA PHATUN"):
            rgb, box = render(text, "calibri.ttf", 30, before="Name :")
            ref = "ZELIRA PHATUN" if text.isupper() else "Zelira Phatun"
            for b in (box, None):
                with self.subTest(text=text, box=b is not None):
                    res = C.look(rgb, b, [ref])
                    self.assertNotEqual(res.decision, "same")

    def test_an_mrz_name_line(self):
        """identity._mrz_by_closer_look: the printed name in MRZ order, alone, on a made-up code line."""
        import numpy as np
        from PIL import Image, ImageDraw, ImageFont
        from src.verify.runner.tests.test_closer import FONTS
        for line, want_same in (("P<XYZSAMPOL<<DEMIRA<TOKAN<<<<<<<<<<<<<<<<<<<", True),
                                ("P<XYZSAMPOL<<MD<DEMIRA<TOKAN<<<<<<<<<<<<<<<", False)):
            f = ImageFont.truetype(str(FONTS / "consola.ttf"), 30)
            l, t, r, b = f.getbbox(line)
            im = Image.new("RGB", (r - l + 40, b - t + 40), (248, 248, 246))
            ImageDraw.Draw(im).text((20 - l, 20 - t), line, font=f, fill=(20, 20, 25))
            arr = np.array(im)
            with self.subTest(want_same=want_same):
                res = C.look(arr, (5, 5, arr.shape[1] - 5, arr.shape[0] - 5), ["SAMPOL DEMIRA TOKAN"])
                if want_same:
                    self.assertEqual(res.decision, "same")
                else:
                    self.assertNotEqual(res.decision, "same")

    def test_the_value_itself_after_a_label_is_same(self):
        rgb, box = render("Zelira Phatun", "calibri.ttf", 30, before="Mother's Name :", after="Date of Birth")
        res = C.look(rgb, box, ["Zelira Phatun"])
        self.assertEqual(res.decision, "same")


def stamp_line(name, extra, gap_lh, extra_fg, font="arial.ttf", size=30, label="Name :"):
    """A made-up line: label and name in black ink, then `extra` `gap_lh` line heights after the name in `extra_fg`
    (a notary stamp's ink, or the name's own). Returns (RGB, the box of the whole line)."""
    import numpy as np
    from PIL import Image, ImageDraw, ImageFont
    from src.verify.runner.tests.test_closer import FONTS
    f = ImageFont.truetype(str(FONTS / font), size)
    x_name = 20 + f.getbbox(label + " ")[2]
    x_extra = x_name + f.getbbox(name)[2] + gap_lh * size
    W, H = int(x_extra + f.getbbox(extra)[2] + 30), int(size * 2.4)
    im = Image.new("RGB", (W, H), (250, 250, 247))
    d = ImageDraw.Draw(im)
    d.text((20, size * 0.6), label, font=f, fill=(25, 25, 30))
    d.text((x_name, size * 0.6), name, font=f, fill=(25, 25, 30))
    d.text((x_extra, size * 0.6), extra, font=f, fill=extra_fg)
    return np.array(im), (10.0, size * 0.3, W - 10.0, H - size * 0.3)


@unittest.skipUnless(_have_models(), "the ONNX models or the fonts are not on this PC")
class TestFarTail(unittest.TestCase):
    """The EAP notary stamp (rev 2.6): a short word the OCR ran onto the name from a stamp printed FAR after it, in
    another ink, is no part of the name; a word in the name's own ink, or a near word, is never cut away."""

    REF = "Delira Sompat"
    STAMP = (120, 70, 170)

    def test_a_far_word_in_a_stamps_ink_is_cut(self):
        for font in ("arial.ttf", "times.ttf"):
            rgb, box = stamp_line(self.REF, "MD", 5.0, self.STAMP, font=font)
            with self.subTest(font=font):
                res = C.look(rgb, box, [self.REF, self.REF + " MD"])
                self.assertEqual(res.decision, "same")
                self.assertIn("cut", res.detail)

    def test_a_far_word_in_the_names_own_ink_stays(self):
        rgb, box = stamp_line(self.REF, "MD", 5.0, (25, 25, 30))
        res = C.look(rgb, box, [self.REF, self.REF + " MD"])
        self.assertNotIn("cut", res.detail)
        self.assertNotEqual(res.decision, "same")

    def test_a_near_word_is_never_cut(self):
        for fg in (self.STAMP, (25, 25, 30)):
            for font in ("calibri.ttf", "times.ttf"):
                rgb, box = stamp_line(self.REF, "Begum", 0.35, fg, font=font)
                with self.subTest(fg=fg, font=font):
                    res = C.look(rgb, box, [self.REF, self.REF + " BEGUM"])
                    self.assertNotIn("cut", res.detail)
                    if fg != self.STAMP:
                        self.assertNotEqual(res.decision, "same")


class TestAddedWordUnclear(unittest.TestCase):
    """Q18 (checker): an added word read on one page is the same name only when the closer look shows the reference
    there; unclear is FLAG, a confirmed added word with the reference confirmed is FAIL."""

    def case(self, truth_of):
        c = base_case()
        u = c.add("academic", [academic(mother="DEMO SAMPLE TOVIRAX")], "ACADEMIC DEGREE")
        ident = ident_of()
        ident.mother.pages = (0,)
        return rules(run(c, ident, CloserFake(truth_of(u))), u.uid)

    def test_unclear_is_flag(self):
        self.assertEqual(self.case(lambda u: {0: "DEMO SAMPLE"}), [("names.mother", FLAG)])

    def test_the_reference_shown_is_the_same_name(self):
        self.assertEqual(self.case(lambda u: {0: "DEMO SAMPLE", u.pages[0]: "DEMO SAMPLE"}), [])

    def test_the_added_word_shown_is_fail(self):
        self.assertEqual(self.case(lambda u: {0: "DEMO SAMPLE", u.pages[0]: "DEMO SAMPLE TOVIRAX"}),
                         [("names.mother", FAIL)])

    def test_a_misparse_beside_an_unsettled_added_word_is_no_fail(self):
        """Takeover (corpus S174): the added-word read stays FLAG; a label value wholly unlike the name read on two
        other pages of the same document is a misparse (the document names the person in the added-word read),
        never a FAIL even when the look finds that text printed."""
        c = base_case()
        u = c.add("academic", [academic(mother="DEMO SAMPLE TOVIRAX"), academic(mother="QUENTOR VALBIS"),
                               academic(mother="QUENTOR VALBIS")], "ACADEMIC DEGREE")
        ident = ident_of()
        ident.mother.pages = (0,)
        truth = {0: "DEMO SAMPLE", u.pages[1]: "QUENTOR VALBIS", u.pages[2]: "QUENTOR VALBIS"}
        self.assertEqual(rules(run(c, ident, CloserFake(truth)), u.uid), [("names.mother", FLAG)])


class TestNidBackSexWithOneDateCheck(unittest.TestCase):
    """Checker's rules probe (rev 2.6): another person's back whose date-of-birth check digit is misread still shows
    its checked number and its sex letter (the expiry's check digit agrees): two signals, FAIL."""

    def test_number_and_sex_are_two_signals(self):
        import datetime as dt
        from src.verify.runner import doccheck as DC
        from src.verify.runner.tests.test_fixes25 import FRONT_NO, OTHER_NO, nid_pages, pg, unit
        today = dt.date(2026, 10, 28)
        pages = nid_pages(FRONT_NO, "15 July 1981", OTHER_NO, "590203", "M")
        good = f"590203{DC.mrz_check_digit('590203')}M"
        bad = f"590203{(DC.mrz_check_digit('590203') + 1) % 10}M"
        pages = [pg(p.index, p.text.replace(good, bad), "MOTHER NID.pdf", p.index) for p in pages]
        self.assertTrue(all(bad in p.text for p in pages))
        r = DC.td1_reads(pages[0].text, today)[0]
        self.assertEqual((r["number"], r["dob"], r["sex"], r["line2_ok"]), (OTHER_NO, None, "M", False))
        fs = DC.nid_back_rows([p.text for p in pages], unit("mother_nid", [0, 1]), pages, None, today)
        self.assertEqual([(f.verdict, sorted(f.evidence["signals"])) for f in fs], [(FAIL, ["number", "sex"])])
        # her own back with the same misread date check: number and sex agree, no signal
        own = nid_pages(FRONT_NO, "15 July 1981", FRONT_NO, "810715", "F")
        g2, b2 = f"810715{DC.mrz_check_digit('810715')}F", f"810715{(DC.mrz_check_digit('810715') + 1) % 10}F"
        own = [pg(p.index, p.text.replace(g2, b2), "MOTHER NID.pdf", p.index) for p in own]
        fs = DC.nid_back_rows([p.text for p in own], unit("mother_nid", [0, 1]), own, None, today)
        self.assertNotIn(fs[0].verdict, (FAIL, FLAG))


class ProbeFake(CloserFake):
    """CloserFake whose dots looks carry the dot's shape and how the look was decided (`by`)."""

    def __init__(self, truth, by="the dot's shape + the recogniser"):
        super().__init__(truth)
        self.by = by

    def look(self, page, candidates, kind="name"):
        res = super().look(page, candidates, kind)
        if kind != "dots" or res.decision == "unclear":
            return res
        diff = set()
        for c in candidates[1:]:
            diff |= set(S.dot_pattern(c)) ^ set(S.dot_pattern(candidates[0]))
        first = min(diff) if diff else None
        dot = None if first is None else first in S.dot_pattern(res.best)
        return C.Look(res.decision, res.best, res.scores, res.margin, 28, 28, dot, res.note,
                      {"pre": "unclear", "by": self.by if res.decision == "differs" else ""})


class TestDotProbe(unittest.TestCase):
    """W16 (checker): every read shows "MD" but one document prints "MD."; the dot probe sees it. The result is FLAG
    (no OCR read shows the dot), never PASS and never FAIL."""

    def case(self, degree_prints, by="the dot's shape + the recogniser", title="ACADEMIC DEGREE"):
        c = base_case()
        nid = c.add("student_nid", ["NID Name MD EXAMPLE SAMPLE Date of Birth"], "STUDENT NID")
        deg = c.add("academic", [academic(student="MD EXAMPLE SAMPLE")], title)
        truth = {0: "MD EXAMPLE SAMPLE", nid.pages[0]: "MD EXAMPLE SAMPLE", deg.pages[0]: degree_prints}
        conf = ProbeFake(truth, by)
        res = run(c, ident_of(student_written="MD EXAMPLE SAMPLE"), conf)
        return res, nid, deg, conf

    def test_a_dot_the_ocr_dropped_is_flag(self):
        res, nid, deg, _ = self.case("MD. EXAMPLE SAMPLE")
        self.assertEqual(rules(res, deg.uid), [("names.student_dots", FLAG)])
        self.assertIn("dot probe", res.per_unit[deg.uid][0].message)
        self.assertEqual(rules(res, nid.uid), [])

    def test_no_dot_printed_no_finding(self):
        res, nid, deg, conf = self.case("MD EXAMPLE SAMPLE")
        self.assertEqual((rules(res, deg.uid), rules(res, nid.uid)), ([], []))
        self.assertTrue(any(k == "dots" for _p, _c, k in conf.calls))          # the probe looked

    def test_the_shape_overriding_the_recogniser_is_not_enough(self):
        res, _nid, deg, _ = self.case("MD. EXAMPLE SAMPLE", by="the dot's shape (the recogniser's lead was small)")
        self.assertEqual(rules(res, deg.uid), [])

    def test_board_papers_are_not_probed(self):
        res, _nid, deg, conf = self.case("MD. EXAMPLE SAMPLE", title="ACADEMIC HSC")
        self.assertEqual([r for r in rules(res, deg.uid) if r[1] in (FAIL, FLAG)], [])
        self.assertNotIn(deg.pages[0], {p for p, _c, k in conf.calls if k == "dots"})

    def test_dot_places(self):
        self.assertEqual(N.dot_places("MD EXAMPLE SAMPLE"), (2,))
        self.assertEqual(N.dot_places("A K M SAMPLE"), (1, 2, 3))
        self.assertEqual(N.dot_places("MST. DEMO SAMPLE"), (3,))
        self.assertEqual(N.dot_places("EXAMPLE SAMPLE"), ())
        self.assertEqual(N.dot_places("DEMO SAMPLE MD"), ())                  # never after the last word


if __name__ == "__main__":
    unittest.main()
