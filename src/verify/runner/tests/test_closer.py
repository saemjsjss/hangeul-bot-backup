"""The closer look (closer.py; owner 28 Oct 2026) on MADE-UP synthetic crops: text rendered with the Windows fonts,
with and without dots, one-letter real differences, added words, a tail, degraded prints. No personal data: every
name here is a made-up placeholder."""
from __future__ import annotations

import io
import random
import unittest
from pathlib import Path

import numpy as np

from src.verify.runner import closer as C

FONTS = Path(r"C:\Windows\Fonts")
MODELS = Path(r"C:\Hangeul\DOCCHECK\lab\onnx\models")


def _have_models() -> bool:
    return (MODELS / "english_g2.onnx").exists() and (MODELS / "craft_mlt_25k.onnx").exists() and \
        (FONTS / "arial.ttf").exists()


def render(text: str, font: str = "arial.ttf", size: int = 28, fg=(30, 30, 40), bg=(250, 250, 248),
           before: str = "", after: str = "", gap: float = 0.6):
    """One printed line (an optional label before and a field after); returns (RGB, the box of the whole line)."""
    from PIL import Image, ImageDraw, ImageFont
    f = ImageFont.truetype(str(FONTS / font), size)
    full = (before + " " if before else "") + text + ("      " + after if after else "")
    l, t, r, b = f.getbbox(full)
    W, H = int(r - l + 2 * gap * size), int(b - t + 2 * gap * size)
    im = Image.new("RGB", (W, H), bg)
    ImageDraw.Draw(im).text((gap * size - l, gap * size - t), full, font=f, fill=fg)
    return np.array(im), (0.5 * gap * size, 0.5 * gap * size, W - 0.5 * gap * size, H - 0.5 * gap * size)


def degrade(rgb, seed: int = 1, blur: float = 0.8, jpeg: int = 50, noise: float = 6.0, tint=True):
    from PIL import Image, ImageFilter
    rng = random.Random(seed)
    a = rgb.astype(np.float32)
    if tint:
        paper = a.mean(axis=2, keepdims=True) > 200
        a = np.where(paper, a * np.array([1.0, 0.93, 0.85], np.float32), a)
    im = Image.fromarray(np.clip(a, 0, 255).astype(np.uint8)).filter(ImageFilter.GaussianBlur(blur))
    a = np.array(im).astype(np.float32) + np.random.default_rng(rng.randint(0, 99)).normal(0, noise, a.shape)
    im = Image.fromarray(np.clip(a, 0, 255).astype(np.uint8))
    buf = io.BytesIO()
    im.save(buf, "JPEG", quality=jpeg)
    return np.array(Image.open(io.BytesIO(buf.getvalue())).convert("RGB"))


def page(lines, font: str = "arial.ttf", size: int = 26):
    """A small made-up form: "label   value" lines."""
    from PIL import Image, ImageDraw, ImageFont
    f = ImageFont.truetype(str(FONTS / font), size)
    W = 60 + max(int(f.getlength(a + "     " + b)) for a, b in lines) + 80
    H = 60 + len(lines) * int(size * 2.4)
    im = Image.new("RGB", (W, H), (250, 250, 248))
    d = ImageDraw.Draw(im)
    y = 40
    for a, b in lines:
        d.text((40, y), a, font=f, fill=(30, 30, 40))
        d.text((40 + int(f.getlength(a + "     ")), y), b, font=f, fill=(30, 30, 40))
        y += int(size * 2.4)
    return np.array(im)


# ============================================================================================ the CTC arithmetic
class TestCTC(unittest.TestCase):
    def brute(self, lp, label):
        """log P(label) by enumerating every frame path (tiny cases only)."""
        import itertools
        T, K = lp.shape
        total = -np.inf
        for path in itertools.product(range(K), repeat=T):
            out, prev = [], -1
            for c in path:
                if c != prev and c != 0:
                    out.append(c)
                prev = c
            if out == list(label):
                total = np.logaddexp(total, sum(lp[t, c] for t, c in enumerate(path)))
        return float(total)

    def test_forward_equals_the_sum_over_alignments(self):
        rng = np.random.default_rng(3)
        for label in ([1], [1, 2], [1, 1], [2, 1, 2]):
            p = rng.random((5, 3)) + 0.05
            lp = np.log(p / p.sum(axis=1, keepdims=True))
            self.assertAlmostEqual(C.ctc_logl(lp, label), self.brute(lp, label), places=6)

    def test_impossible_label_scores_minus_infinity(self):
        lp = np.log(np.full((2, 3), 1 / 3.0))
        self.assertLessEqual(C.ctc_logl(lp, [1, 1, 1]), -1e8)           # needs 5 frames

    def test_keyword_span_and_alignment(self):
        lp = np.log(np.full((12, 4), 0.01))
        for t, c in ((0, 3), (1, 3), (4, 1), (6, 2), (7, 2), (10, 3)):
            lp[t, c] = np.log(0.97)
        for t in (2, 3, 5, 8, 9, 11):
            lp[t, 0] = np.log(0.97)
        sc, a, b = C.kws_span(lp, [1, 2])
        self.assertEqual((a, b), (4, 6))
        self.assertEqual(C.viterbi_frames(lp[4:8], [1, 2]), [0, 2])

    def test_norm(self):
        self.assertEqual(C.norm("Md. Sample-Name", "name"), "MDSAMPLENAME")
        self.assertEqual(C.norm("Md.. Sample.", "dots"), "MD.SAMPLE")
        self.assertEqual(C.norm("AB<<CD 12", "digits"), "AB<<CD12")

    def test_segments_are_the_places_that_differ(self):
        self.assertEqual(C.segments([1, 2, 3], [1, 2, 3]), [])
        self.assertEqual(C.segments([1, 2, 3, 4], [1, 5, 3, 4]), [(1, 2, 1, 2)])          # one letter
        self.assertEqual(C.segments([1, 2, 3], [9, 9, 1, 2, 3]), [(0, 0, 0, 2)])          # an added word
        self.assertEqual(C.segments([1, 2, 3, 4], [1, 2]), [(2, 4, 2, 2)])                # a dropped word
        self.assertEqual(C.segments([1, 2, 3, 4, 5], [1, 7, 3, 4, 8]), [(1, 2, 1, 2), (4, 5, 4, 5)])
        a, b = [1, 2, 3, 4, 5], [1, 7, 3, 4, 8]
        self.assertEqual(C._hybrid(a, b, (4, 5, 4, 5)), [1, 2, 3, 4, 8])                  # ONE place taken


# ============================================================================================ the recogniser
@unittest.skipUnless(_have_models(), "the ONNX models or the Windows fonts are not here")
class TestLook(unittest.TestCase):
    def check(self, printed, other, want_same_first=True, font="arial.ttf", size=28, kind="name", deg=None, **kw):
        rgb, box = render(printed, font, size, **kw)
        if deg is not None:
            rgb = degrade(rgb, **deg)
        a = C.look(rgb, box, [printed, other], kind)
        b = C.look(rgb, box, [other, printed], kind)
        return a, b

    def test_a_one_letter_difference(self):
        a, b = self.check("Vonima Pekter", "Vonima Pektar")
        self.assertEqual((a.decision, b.decision), ("same", "differs"))
        self.assertEqual(a.best, "Vonima Pekter")
        self.assertGreaterEqual(a.margin, C.MARGIN)

    def test_a_dropped_letter_and_capitals(self):
        a, b = self.check("QELWOR TOSSAIN", "QELWOR TOSSEN", font="times.ttf")
        self.assertEqual((a.decision, b.decision), ("same", "differs"))

    def test_an_added_honorific_is_seen(self):
        rgb, box = render("MST Zelira Phatun", before="Name:")
        res = C.look(rgb, box, ["Zelira Phatun", "MST Zelira Phatun"])
        self.assertEqual(res.decision, "differs")
        res = C.look(rgb, box, ["MST Zelira Phatun", "Zelira Phatun"])
        self.assertEqual(res.decision, "same")

    def test_a_tail_that_is_not_printed(self):
        rgb, box = render("Zelvo Quddin", before="Father :", after="Nationality")
        res = C.look(rgb, box, ["Zelvo Quddin", "Zelvo Quddin Nor"])
        self.assertEqual(res.decision, "same")

    def test_the_label_before_and_the_field_after_do_not_count(self):
        rgb, box = render("Rovexa Phanam", before="Mother's Name :", after="Date of Birth")
        self.assertEqual(C.look(rgb, box, ["Rovexa Phanam", "Rovexa Phanom"]).decision, "same")

    def test_the_page_is_searched_when_the_box_is_unknown(self):
        img = page([("Name", "TOLVIR WASEN"), ("Father's Name", "ABDOL KERIM"), ("Mother's Name", "SEVINA PORVIN"),
                    ("Address", "VILLAGE SAMPLE")])
        res = C.look(img, None, ["SEVINA PORVIN", "SEVINA PORVEEN"])
        self.assertEqual(res.decision, "same")
        self.assertTrue(res.detail.get("box"))
        res = C.look(img, None, ["ABDOL KERlM", "ABDOL KERIM"])          # the reference read with an l: corrected
        self.assertEqual(res.decision, "differs")

    def test_dots(self):
        a, b = self.check("MD. Tolvir Wasen", "MD Tolvir Wasen", kind="dots", size=34)
        self.assertEqual((a.decision, b.decision), ("same", "differs"))
        self.assertIs(a.dot, True)
        a, b = self.check("MD Tolvir Wasen", "MD. Tolvir Wasen", kind="dots", size=34)
        self.assertEqual((a.decision, b.decision), ("same", "differs"))
        self.assertIs(a.dot, False)

    def test_one_value_verified_against_the_recognisers_own_reading(self):
        rgb, box = render("Rafiqel Oslom", "calibri.ttf", 30)
        self.assertEqual(C.look(rgb, box, ["Rafiqel Oslom"]).decision, "same")

    def test_the_letter_shape_expert(self):
        rgb, box = render("Jomila Pekter", "arial.ttf", 30)
        big, _f = C._enlarge(C._vcrop(rgb, box[1], box[3], box[0], box[2], 0.25 * (box[3] - box[1]),
                                      0.35 * (box[3] - box[1])), box[3] - box[1])
        self.assertEqual(C.shape_check(big, "Jomila Pekler", "Jomila Pekter")[0], 1)
        self.assertEqual(C.shape_check(big, "Jomila Pekter", "Jomila Pekler")[0], 0)
        self.assertIsNone(C.shape_check(big, "Jomila Pekter", "Jomila Pektar")[0])     # e / a: one shape class

    def test_a_real_difference_is_never_same(self):
        """Safety first (owner 28 Oct): a print that differs from the reference by one letter or one word must come
        out "differs" or "unclear" on degraded crops too, never "same"."""
        pairs = [("Vonima Pektar", "Vonima Pekter"), ("Monar Tossen", "Monar Tossain"), ("Rovexa Begun", "Rovexa Begum"),
                 ("Halibor Rehaman", "Halibor Rehman"), ("Jovanara", "Jovanara Begum"),
                 ("MST Zalmo Phatun", "Zalmo Phatun")]
        fonts = ["arial.ttf", "times.ttf", "verdana.ttf", "georgia.ttf"]
        for k, (printed, ref) in enumerate(pairs):
            for j, font in enumerate(fonts[:2] if k % 2 else fonts[2:]):
                rgb, box = render(printed, font, 20 + 4 * j)
                rgb = degrade(rgb, seed=k * 7 + j, blur=0.6 + 0.2 * j, jpeg=45)
                with self.subTest(printed=printed, font=font):
                    self.assertNotEqual(C.look(rgb, box, [ref, printed]).decision, "same")

    def test_a_third_spelling_is_never_same(self):
        """The print differs from the reference in one letter while the OCR variant adds an unprinted word: the
        reference beats the variant on the sum, but its differing letter reads the other way, so never "same"."""
        for font in ("arial.ttf", "times.ttf"):
            rgb, box = render("Vonima Pektar", font, 28)
            with self.subTest(font=font):
                res = C.look(rgb, box, ["Vonima Pekter", "Vonima Pektar Begun"])
                self.assertNotEqual(res.decision, "same")

    def test_a_wrapped_cell_is_never_decided_from_one_line(self):
        """A table cell printed on two lines ("<A>" over "<B>"): the first line alone reads "<A>", so a candidate
        that drops "<B>" must not come out confirmed, and the shorter reference must not come out "same"."""
        from PIL import Image, ImageDraw, ImageFont
        f = ImageFont.truetype(str(FONTS / "arial.ttf"), 28)
        im = Image.new("RGB", (360, 130), (250, 250, 248))
        d = ImageDraw.Draw(im)
        d.text((30, 20), "TOLVIR", font=f, fill=(30, 30, 40))
        d.text((30, 56), "WASEN", font=f, fill=(30, 30, 40))
        rgb = np.array(im)
        box = (25, 18, 160, 52)
        self.assertNotEqual(C.look(rgb, box, ["TOLVIR WASEN", "TOLVIR"]).decision, "differs")
        self.assertNotEqual(C.look(rgb, box, ["TOLVIR", "TOLVIR WASEN"]).decision, "same")
        self.assertIsNotNone(C.line_under(rgb, C.tighten(rgb, box)))

    def test_artwork_after_the_name_is_no_added_word(self):
        """Curly artwork (a made-up flourish standing in for a seal edge or a line of another script) printed after
        the name: a candidate that reads a word into it ("... Nor") is never confirmed."""
        from PIL import Image, ImageDraw, ImageFont
        f = ImageFont.truetype(str(FONTS / "arial.ttf"), 28)
        im = Image.new("RGB", (520, 80), (250, 250, 248))
        d = ImageDraw.Draw(im)
        d.text((20, 22), "Zelvo Quddin", font=f, fill=(30, 30, 40))
        rng = random.Random(4)
        x = 200
        for _ in range(9):
            w = rng.randint(14, 26)
            d.arc((x, 28 + rng.randint(-4, 4), x + w, 52), rng.randint(0, 180), rng.randint(200, 360),
                  fill=(60, 50, 90), width=2)
            d.line((x + w // 2, 26, x + w // 2 + rng.randint(-6, 6), 54), fill=(60, 50, 90), width=2)
            x += w + rng.randint(2, 6)
        rgb = np.array(im)
        res = C.look(rgb, (14, 14, 510, 62), ["Zelvo Quddin", "Zelvo Quddin Nor"])
        self.assertNotEqual(res.decision, "differs")

    def test_a_descender_is_kept_in_the_crop(self):
        rgb, box = render("Rafiqel Oslom", "arial.ttf", 30)
        x0, y0, x1, y1 = C.tighten(rgb, box)
        from PIL import ImageFont
        f = ImageFont.truetype(str(FONTS / "arial.ttf"), 30)
        _l, t, _r, b = f.getbbox("Rafiqel Oslom")
        self.assertGreaterEqual(y1 - y0, 0.9 * (b - t))                 # the q's tail is inside the line

    def test_a_glued_dot_is_seen_by_its_shape(self):
        """'MD.Tolvir' printed without a space after the dot: the recogniser tends to drop such a dot; the dot's own
        shape (a solid blob on the baseline between the letters) must keep it from reading as 'MDTolvir'."""
        rgb, box = render("MD.Tolvir Wasen", "arial.ttf", 34)
        res = C.look(rgb, box, ["MD. Tolvir Wasen", "MDTolvir Wasen"], "dots")
        self.assertNotEqual(res.decision, "differs")
        self.assertIn(res.dot, (True, None))

    def test_garbage_is_unclear(self):
        rgb = (np.random.default_rng(5).random((60, 400, 3)) * 255).astype(np.uint8)
        self.assertEqual(C.look(rgb, (0, 0, 400, 60), ["Vonima Pekter", "Vonima Pektar"]).decision, "unclear")


if __name__ == "__main__":
    unittest.main()
