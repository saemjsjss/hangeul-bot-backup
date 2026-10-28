"""Revision 2.5 tests (Agent B, documents): the owner's 28 Oct rules and the by-eye check of the four owner cases.

  1. an NID whose BACK side (its machine-readable zone) belongs to another person: FAIL when confirmed;
  2. the bank opening-balance rule is dropped: one NOTE at most, never FLAG / FAIL;
  3. a solvency certificate signed by hand without a stamp: the handwritten signature is found, PASS saying so;
  4. the present-only ID card / CV is never cut; every page number of a cut part's bot message is mapped;
  5. a sideways page read as junk is read turned; an e-Apostille starts the next level's unit; the TIN's printed
     father with item-number noise; the printed TIN against the QR gets a closer look before any FLAG;
  6. "Alim" in a madrasah's name on a Dakhil certificate is no HSC; a tie between two financial page types is settled
     by the page's title or its neighbours; the QR ladder before "no QR code" on a licence;
  7. the income tax's "notarised" seal: evidence of what the seal looked like (the verdict is the bot's);
  +  a weakly read page next to the document a file's name names joins it (the EAP folder's ECA file); the date of
     birth gets a closer look before its FLAG.

Made-up data only: placeholder names ("SAMPLE", "DEMO", "EXAMPLE", "TESTER"), numbers built at run time, synthetic
images. No personal data, no card, no network. The closer look (closer.py, Agent A) is stubbed through sys.modules.

    <runner venv python> -I -B -m unittest discover -s <worktree>\\src\\verify\\runner\\tests -t <worktree> -p "test_fixes25*.py"
"""
from __future__ import annotations

import datetime as dt
import shutil
import sys
import unittest
from dataclasses import dataclass, field
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import Dict, List, Optional

import numpy as np

from src.verify.runner import bank as BK
from src.verify.runner import botimport as B
from src.verify.runner import doccheck as DC
from src.verify.runner import pagetype as PT
from src.verify.runner import split as SP
from src.verify.runner import tin as TN
from src.verify.runner.model import (FAIL, FLAG, NOTE, PASS, DocUnit, Finding, Identity, NameRef, PageRead,
                                     set_page_numbering)
from src.verify.runner.tests.test_fixes23 import folder
from src.verify.runner.tests.test_pagetype import TEXTS as T

PKG = "src.verify.runner"
TODAY = dt.date(2026, 10, 28)
WORK = Path(r"C:\Hangeul\DOCCHECK\lab\private-runner\tmp\test_fixes25_work")


def setUpModule():
    B.load(B._loaded.cache_root if B._loaded is not None else B.DEFAULT_CACHE_ROOT)


def tearDownModule():
    set_page_numbering(None)
    shutil.rmtree(WORK, ignore_errors=True)


def pg(i: int, text: str, file: str = "bundle.pdf", file_page: int = -1, kind: str = "ocr") -> PageRead:
    return PageRead(index=i, file=file, file_page=i if file_page < 0 else file_page, text=text, kind=kind,
                    chars=len(text), sideways=False, seconds=0.0, image_only=False)


def unit(key: str, pages: List[int], uid: str = "D01", **kw) -> DocUnit:
    return DocUnit(uid=uid, key=key, fine=kw.pop("fine", key), title=kw.pop("title", key.upper()), pages=pages, **kw)


# --- the closer look, stubbed (closer.py's contract) ------------------------------------------------------------
@dataclass
class Look:
    decision: str
    best: str = ""
    scores: Dict[str, float] = field(default_factory=dict)
    margin: float = 1.0
    variants: int = 3
    agree: int = 3
    dot: Optional[bool] = None
    note: str = "stub"


class StubCloser:
    """sys.modules["src.verify.runner.closer"] for one test: look() answers `decision` and records its calls."""

    def __init__(self, decision: str):
        self.decision, self.calls = decision, []
        self.mod = ModuleType(f"{PKG}.closer")
        self.mod.look = self.look
        self.mod.page_image = lambda engine, page, dpi=400: np.full((40, 40, 3), 255, np.uint8)
        self.mod.MARGIN = 0.5

    def look(self, image, box, candidates, kind="name"):
        self.calls.append((list(candidates), kind))
        best = candidates[0] if self.decision == "same" else candidates[-1]
        return Look(self.decision, best)

    def __enter__(self):
        self.saved = sys.modules.get(f"{PKG}.closer")
        sys.modules[f"{PKG}.closer"] = self.mod
        import src.verify.runner as pkg
        self.saved_attr = getattr(pkg, "closer", None)
        pkg.closer = self.mod
        return self

    def __exit__(self, *exc):
        import src.verify.runner as pkg
        if self.saved is None:
            sys.modules.pop(f"{PKG}.closer", None)
        else:
            sys.modules[f"{PKG}.closer"] = self.saved
        if self.saved_attr is None:
            if hasattr(pkg, "closer"):
                delattr(pkg, "closer")
        else:
            pkg.closer = self.saved_attr


# ============================================================================================ 1. NID back side
def mrz_number(n: str) -> str:
    """Line 1 of a TD1 MRZ for a made-up 10-digit number (9 characters, '<', the 10th digit and the check digit)."""
    return f"I<BGD{n[:9]}<{n[9]}{DC.mrz_check_digit(n)}<<<<<<<<<<<<<<<"


def mrz_line2(dob: str, sex: str, exp: str = "351231") -> str:
    return f"{dob}{DC.mrz_check_digit(dob)}{sex}{exp}{DC.mrz_check_digit(exp)}BGD<<<<<<<<<<<0"


FRONT_NO = "52" + "34186709"            # made-up NID numbers (no leading 01: not a mobile number)
OTHER_NO = "97" + "65012384"


def nid_pages(front_no: str, front_dob: str, back_no: str, back_dob: str, back_sex: str) -> List[PageRead]:
    trans = (f"Translated from Bengali to English NATIONAL ID CARD Name DEMO SAMPLE Father TESTER SAMPLE Date of Birth: "
             f"{front_dob} NID No {front_no[:3]} {front_no[3:6]} {front_no[6:]} Address Village Sample "
             f"{mrz_number(back_no)} {mrz_line2(back_dob, back_sex)} SAMPLE<<TESTER<<<<<<<")
    scan = (f"Government of the People's Republic of Bangladesh National ID Card DEMO SAMPLE {front_no[:3]} "
            f"{front_no[3:6]} {front_no[6:]} {mrz_number(back_no)} {mrz_line2(back_dob, back_sex)} SAMPLE<<TESTER<<<")
    return [pg(0, trans, "MOTHER NID.pdf", 0), pg(1, scan, "MOTHER NID.pdf", 1)]


class TestNidBackSide(unittest.TestCase):
    def test_mrz_is_read_with_its_check_digits(self):
        r = DC.td1_reads(f"x {mrz_number(FRONT_NO)} {mrz_line2('810715', 'F')} SAMPLE<<DEMO", TODAY)
        self.assertEqual(len(r), 1)
        self.assertEqual((r[0]["number"], r[0]["number_ok"], r[0]["sex"]), (FRONT_NO, True, "F"))
        self.assertEqual(r[0]["dob"], dt.date(1981, 7, 15))
        bad = mrz_number(FRONT_NO)[:-16] + str((DC.mrz_check_digit(FRONT_NO) + 1) % 10) + "<" * 15
        self.assertEqual(DC.td1_reads(bad, TODAY), [])                    # a wrong check digit is no read
        # OCR's spellings of the line-1 head and lower case
        ocr = f"K<8GD {FRONT_NO[:9]}<{FRONT_NO[9]}{DC.mrz_check_digit(FRONT_NO)}<<<< {mrz_line2('810715', 'f')}"
        self.assertEqual(DC.td1_reads(ocr, TODAY)[0]["number"], FRONT_NO)

    def test_the_fathers_back_on_the_mothers_card_fails(self):
        pages = nid_pages(FRONT_NO, "15 July 1981", OTHER_NO, "590203", "M")
        fs = DC.nid_back_rows([p.text for p in pages], unit("mother_nid", [0, 1]), pages, None, TODAY)
        self.assertEqual([(f.verdict, f.rule) for f in fs], [(FAIL, "nid.back_side")])
        self.assertIn("belongs to another person", fs[0].message)
        self.assertIn("sex M on the mother's card", fs[0].message)
        self.assertIn("in the scan and the translation", fs[0].message)
        self.assertEqual(sorted(fs[0].evidence["signals"]), ["dob", "number", "sex"])

    def test_one_signal_is_a_flag(self):
        pages = nid_pages(FRONT_NO, "15 July 1981", FRONT_NO, "810715", "M")    # only the sex differs
        fs = DC.nid_back_rows([p.text for p in pages], unit("mother_nid", [0, 1]), pages, None, TODAY)
        self.assertEqual([f.verdict for f in fs], [FLAG])
        self.assertIn("check by eye", fs[0].message)

    def test_the_own_back_passes(self):
        pages = nid_pages(FRONT_NO, "15 July 1981", FRONT_NO, "810715", "F")
        fs = DC.nid_back_rows([p.text for p in pages], unit("mother_nid", [0, 1]), pages, None, TODAY)
        self.assertEqual([f.verdict for f in fs], [PASS])
        self.assertEqual(fs[0].evidence["agree"], ["number", "date of birth", "sex"])

    def test_same_number_and_sex_with_a_misread_front_date_passes(self):
        pages = nid_pages(FRONT_NO, "16 July 1981", FRONT_NO, "810715", "F")    # the front's date misread by OCR
        fs = DC.nid_back_rows([p.text for p in pages], unit("mother_nid", [0, 1]), pages, None, TODAY)
        self.assertEqual([f.verdict for f in fs], [PASS])
        self.assertIn("dob_note", fs[0].evidence)

    def test_an_ocr_slip_in_the_front_number_is_no_signal(self):
        slip = FRONT_NO[:-1] + str((int(FRONT_NO[-1]) + 1) % 10)            # one digit apart: an OCR slip
        pages = nid_pages(slip, "15 July 1981", FRONT_NO, "810715", "F")
        fs = DC.nid_back_rows([p.text for p in pages], unit("mother_nid", [0, 1]), pages, None, TODAY)
        self.assertNotIn(fs[0].verdict, (FAIL, FLAG))

    def test_an_old_card_without_mrz_is_a_note(self):
        pages = [pg(0, "NATIONAL ID CARD Name DEMO SAMPLE NID No 523 418 6709 Date of Birth 15 July 1981")]
        fs = DC.nid_back_rows([pages[0].text], unit("father_nid", [0]), pages, None, TODAY)
        self.assertEqual([f.verdict for f in fs], [NOTE])

    def test_the_students_card_uses_the_passport_sex(self):
        pages = nid_pages(FRONT_NO, "11 November 2003", FRONT_NO, "031111", "F")
        ident = Identity(student=NameRef(), father=NameRef(), mother=NameRef(), sex="M")
        fs = DC.nid_back_rows([p.text for p in pages], unit("student_nid", [0, 1]), pages, ident, TODAY)
        self.assertEqual([f.verdict for f in fs], [FLAG])
        self.assertIn("the student's card", fs[0].message)


# ============================================================================================ 2. opening balance
class TestOpeningBalanceDropped(unittest.TestCase):
    def test_every_opening_balance_row_becomes_one_note(self):
        rows = [(PASS, "balance 2,500,000 BDT meets the 1,800,000 BDT minimum"),
                (FLAG, "the statement opens at 900,000 BDT — below the 1,800,000 BDT opening balance expected for KLP"),
                (NOTE, "no line is labelled as the opening balance — check by eye that the statement opens at 1,800,000 "
                       "BDT or more"),
                (PASS, "opening balance 2,000,000 BDT is within the expected range")]
        out = DC.opening_balance_dropped(rows)
        self.assertEqual(out, [rows[0], (NOTE, DC.OPENING_BALANCE_NOTE)])
        self.assertEqual(DC.opening_balance_dropped(rows[:1]), rows[:1])           # nothing to say: no NOTE

    def test_the_patterns_match_the_bots_own_messages(self):
        import inspect
        import re
        src = inspect.getsource(B._loaded.dv)
        for p in DC.OPENING_BALANCE_ROWS:
            self.assertTrue(re.search(p.lstrip("^"), src), p)


# ============================================================================================ 3. handwritten signature
def letter_page(signature: bool, seed: int = 3) -> np.ndarray:
    """A made-up A4 letter at 150 dpi: lines of printed words, and (optionally) a looping handwritten signature."""
    import cv2
    rng = np.random.default_rng(seed)
    img = np.full((1754, 1240, 3), 255, np.uint8)
    y = 300
    for _ in range(26):
        x = 120
        while x < 1050:
            w = int(rng.integers(3, 9))
            cv2.putText(img, "Sample"[:w] if w <= 6 else "Example", (x, y), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 0, 0), 1)
            x += 18 * w + 14
        y += 34
    if signature:
        t = np.linspace(0, 6 * np.pi, 400)
        xs = 820 + 120 * t / (6 * np.pi) + 22 * np.cos(t)
        ys = 1450 + 32 * np.sin(1.5 * t)
        pts = np.stack([xs, ys], 1).astype(np.int32).reshape(-1, 1, 2)
        cv2.polylines(img, [pts], False, (20, 20, 120), 2)
    return img


class TestHandwrittenSignature(unittest.TestCase):
    def setUp(self):
        set_page_numbering(None)

    def test_a_signature_is_found_and_printed_words_are_not(self):
        self.assertEqual(len(BK.ink_signatures(letter_page(True))), 1)
        self.assertEqual(BK.ink_signatures(letter_page(False)), [])

    def test_two_signatures_count_two_and_a_ring_or_a_name_stamp_none(self):
        import cv2
        img = letter_page(True)
        t = np.linspace(0, 5 * np.pi, 300)                                  # a second, smaller signature (left)
        pts = np.stack([170 + 90 * t / (5 * np.pi) + 15 * np.cos(t), 1460 + 24 * np.sin(1.3 * t)], 1)
        cv2.polylines(img, [pts.astype(np.int32).reshape(-1, 1, 2)], False, (30, 30, 30), 2)
        cv2.circle(img, (600, 1610), 70, (0, 0, 0), 3)                      # a black round stamp's ring
        cv2.putText(img, "DEMO OFFICER", (120, 1640), cv2.FONT_HERSHEY_DUPLEX, 1.1, (0, 0, 0), 3)   # a name stamp
        sigs = BK.ink_signatures(img)
        self.assertEqual(len(sigs), 2, sigs)
        self.assertTrue(all(s["box"][1] > 0.8 and s["box"][3] < 0.86 for s in sigs), sigs)
        whys = {r["why"] for r in BK._sig_scan(img)[1]}
        self.assertIn("a stamp's ring", whys)

    def test_a_signature_cut_by_its_line_is_one(self):
        import cv2
        img = letter_page(True)
        cv2.line(img, (780, 1452), (1000, 1452), (0, 0, 0), 2)              # the signature line through it
        self.assertEqual(len(BK.ink_signatures(img)), 1)

    def test_signature_without_a_stamp_passes_saying_so(self):
        f = BK.seal_row([0], ["To whom it may concern This is to certify"], lambda k: k, lambda i: None,
                        lambda i: letter_page(True))
        self.assertEqual(f.verdict, PASS)
        self.assertEqual(f.message, "handwritten signature found, no stamp, on the solvency certificate (page 1)")
        self.assertEqual(f.evidence["handwritten_signatures"], 1)
        self.assertFalse(f.evidence["stamp"])

    def test_a_black_round_stamp_is_a_stamp(self):
        import cv2
        img = letter_page(False)
        cv2.circle(img, (600, 1500), 80, (0, 0, 0), 3)
        cv2.circle(img, (600, 1500), 62, (0, 0, 0), 2)
        f = BK.seal_row([0], ["To whom it may concern"], lambda k: k, lambda i: None, lambda i: img)
        self.assertEqual(f.verdict, PASS)
        self.assertEqual(f.message, "round stamp (its ring's shape) found on the solvency certificate (page 1); no "
                                    "handwritten signature seen")

    def test_nothing_at_all_is_still_a_flag(self):
        f = BK.seal_row([0], ["To whom it may concern This is to certify"], lambda k: k, lambda i: None,
                        lambda i: letter_page(False))
        self.assertEqual(f.verdict, FLAG)
        self.assertTrue(f.message.startswith("no seal or signature found on the solvency certificate (page 1)"))


# ============================================================================================ 4. the checker's regressions
class TestPresentOnlyNeverCut(unittest.TestCase):
    def test_a_cv_holding_several_documents_stays_one_present_only_unit(self):
        cv = [T["passport"], T["hsc_cert"], T["hsc_tr"], T["trade"]]
        for name, key in (("10 STUDENT CV.pdf", "id_card_cv"), ("11 ID CARD.pdf", None)):
            with self.subTest(name):
                res, *_ = folder([(name, key, cv)])
                self.assertEqual([(u.key, u.part) for u in res.units], [("id_card_cv", "")])

    def test_other_files_are_still_cut(self):
        res, *_ = folder([("9. FINANCIAL DOCUMENTS.pdf", None, [T["solvency"], T["statement"], T["trade"], T["tin"]])])
        self.assertGreater(len(res.units), 1)


class TestPartPageRefs(unittest.TestCase):
    def setUp(self):
        self.pages = [pg(i, "x", "F.pdf", i) for i in range(10)]
        self.u = unit("academic", [5, 6, 7], part="p6-8")

    def test_every_number_is_mapped(self):
        f = lambda m: DC.part_page_refs(m, self.u, self.pages)               # noqa: E731
        self.assertEqual(f("followed by HSC certificate/transcript (page 2, 3)"),
                         "followed by HSC certificate/transcript (page 7, 8)")
        self.assertEqual(f("pages 2 and 3 could not be read"), "pages 7 and 8 could not be read")
        self.assertEqual(f("page(s) 2 are scanned sideways"), "page(s) 7 are scanned sideways")
        self.assertEqual(f("page(s) 1, 3 are scanned sideways"), "page(s) 6, 8 are scanned sideways")
        self.assertEqual(f("pages 2-3 differ"), "pages 7-8 differ")
        self.assertEqual(f("(page 2: SSC, page 3: HSC)"), "(page 7: SSC, page 8: HSC)")
        self.assertEqual(f("page 9 is out of the cut"), "page 9 is out of the cut")      # not a page of the cut
        self.assertEqual(f("on page 1, 1,800,000 BDT"), "on page 6, 1,800,000 BDT")     # an amount is no page

    def test_a_whole_file_unit_is_unchanged(self):
        self.assertEqual(DC.part_page_refs("page 2, 3", unit("academic", [5, 6, 7]), self.pages), "page 2, 3")


# ============================================================================================ 5a. sideways pages
SIDEWAYS_JUNK = ("ojnseje JO uollejolie Aue Inoylim penssi Si Biej4ik8 5I4L DION suollelilex3 Jaiiojiuo Ezoz "
                 "pakhj Xuvlq oubjr snoalf qwrta lkjhg")


class TestSidewaysPages(unittest.TestCase):
    def test_a_junk_page_is_read_turned_and_kept_turned(self):
        pages = [pg(0, T["apostille"]), pg(1, SIDEWAYS_JUNK), pg(2, T["hsc_tr"])]
        asked = []

        def read_turned(p, angle):
            asked.append((p.index, angle))
            return T["hsc_cert"] if angle == 270 else SIDEWAYS_JUNK[::-1]

        recs = PT.turn_junk_pages(pages, read_turned)
        self.assertEqual(asked, [(1, 90), (1, 270)])                     # a good turned read ends the search
        self.assertEqual((pages[1].text, pages[1].sideways, pages[1].turned), (T["hsc_cert"], True, 270))
        self.assertEqual(PT.page_features(pages[1].text).best, "academic_certificate")
        self.assertEqual([r["angle"] for r in recs], [270])
        self.assertEqual(PT.turned_note(recs, "bundle"),
                         "scanned sideways or upside down, turned to be read: page 2 — the student should upload it "
                         "the right way up")

    def test_the_lines_direction_narrows_the_turns(self):
        for ratio, want in ((0.02, [90, 270]), (8.5, [180]), (1.0, [90, 270, 180]), (None, [90, 270, 180])):
            with self.subTest(ratio):
                asked = []
                pages = [pg(0, SIDEWAYS_JUNK)]
                PT.turn_junk_pages(pages, lambda p, a: asked.append(a) or SIDEWAYS_JUNK,
                                   None if ratio is None else (lambda p, _r=ratio: _r))
                self.assertEqual(asked, want)

    def test_text_line_ratio_tells_sideways_from_upright(self):
        from src.verify.runner import engine as EN
        page = letter_page(False)                                     # rows of printed words
        self.assertGreater(EN.text_line_ratio(page), PT.UPRIGHT_RATIO)
        self.assertLess(EN.text_line_ratio(np.ascontiguousarray(np.rot90(page))), PT.SIDEWAYS_RATIO)
        self.assertEqual(EN.text_line_ratio(np.full((300, 200, 3), 255, np.uint8)), 1.0)

    def test_no_better_turn_leaves_the_page(self):
        pages = [pg(0, SIDEWAYS_JUNK)]
        recs = PT.turn_junk_pages(pages, lambda p, a: SIDEWAYS_JUNK)
        self.assertEqual((pages[0].text, pages[0].turned), (SIDEWAYS_JUNK, 0))
        self.assertEqual([r["angle"] for r in recs], [0])
        self.assertEqual(PT.turned_note(recs, "folder"), "")

    def test_readable_and_layer_pages_are_never_turned(self):
        called = []
        readable = "Please note that the following information is given below for the year"
        pages = [pg(0, readable), pg(1, SIDEWAYS_JUNK, kind="layer"), pg(2, T["family"]), pg(3, "")]
        PT.turn_junk_pages(pages, lambda p, a: called.append(p.index) or "")
        self.assertEqual(called, [])

    def test_the_note_names_every_page_of_every_file(self):
        recs = [{"page": 3, "file": "ACADEMIC.pdf", "file_page": 1, "angle": 90},
                {"page": 6, "file": "ACADEMIC.pdf", "file_page": 4, "angle": 270},
                {"page": 9, "file": "OTHER.pdf", "file_page": 0, "angle": 0}]
        self.assertEqual(PT.turned_note(recs, "folder"),
                         "scanned sideways or upside down, turned to be read: ACADEMIC.pdf p2, 5 — the student should "
                         "upload them the right way up")


# ============================================================================================ 5b. academic levels
class TestApostilleStartsTheNextLevel(unittest.TestCase):
    def test_a_certificate_read_without_its_level(self):
        no_level = "This is to certify that the candidate duly passed the examination held in the year"
        texts = [T["apostille"], T["hsc_cert"], T["hsc_tr"], T["apostille"], no_level, T["ssc_tr"]]
        types = PT.type_pages([pg(i, t) for i, t in enumerate(texts)])
        self.assertIsNone(types[4].level)
        self.assertEqual(SP.split_academic(types, 0, 5), [(0, 2, "HSC"), (3, 5, "SSC")])

    def test_an_apostille_inside_one_level_stays_in_it(self):
        texts = [T["apostille"], T["hsc_cert"], T["apostille"], T["hsc_tr"]]
        types = PT.type_pages([pg(i, t) for i, t in enumerate(texts)])
        self.assertEqual(SP.split_academic(types, 0, 3), [(0, 3, "HSC")])


# ============================================================================================ 5c / 5d. the TIN
TIN_TEXT = ("Taxpayer's Identification Number (TIN) Certificate TIN : {tin} This is to Certify that EXAMPLE SAMPLE is a "
            "Registered Taxpayer of National Board of Revenue Taxpayer's Particulars 1) Name EXAMPLE SAMPLE 2) Father's "
            "Name TESTER SAMPLE {noise} Mother's Name DEMO SAMPLE 4.a) Current Address Village Sample")


class TestTinPrint(unittest.TestCase):
    def test_item_number_noise_before_the_mothers_label(self):
        for noise in ("3)", "3 3)", "3 3", "@ 3 3)"):
            with self.subTest(noise):
                f = TN.printed_tin_fields(TIN_TEXT.format(tin="1" * 12, noise=noise))
                self.assertEqual((f.get("name"), f.get("father"), f.get("mother")),
                                 ("EXAMPLE SAMPLE", "TESTER SAMPLE", "DEMO SAMPLE"))

    def test_digits_closer_look(self):
        qr, printed = "5" + "4" * 11, "5" + "4" * 10 + "1"
        pdf = WORK / "tin p01.pdf"
        WORK.mkdir(parents=True, exist_ok=True)
        import pymupdf
        doc = pymupdf.open()
        doc.new_page().insert_text((72, 72), f"TIN : {printed}")
        doc.save(str(pdf))
        doc.close()
        # "differs" stays a FLAG: the closer look and the page read can share a look-alike misread (owner folder 3)
        for decision, kind, verdict in (("same", "CLOSER_EQUAL", PASS), ("differs", "CLOSER_DIFFERENT", FLAG),
                                        ("unclear", "OCR_DIGITS", FLAG)):
            with self.subTest(decision), StubCloser(decision) as st:
                got, info = TN.tin_closer_look(pdf, 0, qr, printed)
                self.assertEqual(got, kind)
                self.assertEqual(st.calls, [([qr, printed], "digits")])
                rows = TN.verdicts({"tin": qr, "name": "EXAMPLE SAMPLE"}, {"tin": got, "name": "EQUAL"}, True)
                self.assertEqual([v for v, _, r in rows if r == "tin.qr_tin"], [verdict])

    def test_a_scan_is_looked_at_at_its_own_resolution(self):
        for native, want in ((150.4, 150), (96, 150), (600, 400), (None, TN.CLOSER_DPI)):
            fake = SimpleNamespace(native_dpi=lambda path, page, _n=native: _n)
            self.assertEqual(TN.closer_dpi(fake, "x.pdf", 0), want)
        self.assertEqual(TN.closer_dpi(SimpleNamespace(), "x.pdf", 0), TN.CLOSER_DPI)

    def test_no_closer_module_keeps_the_flag(self):
        import src.verify.runner as pkg
        saved, saved_attr = sys.modules.get(f"{PKG}.closer"), pkg.__dict__.pop("closer", None)
        sys.modules[f"{PKG}.closer"] = None                      # import fails
        try:
            got, info = TN.tin_closer_look(WORK / "absent.pdf", 0, "1" * 12, "1" * 11 + "7")
        finally:
            if saved is None:
                sys.modules.pop(f"{PKG}.closer", None)
            else:
                sys.modules[f"{PKG}.closer"] = saved
            if saved_attr is not None:
                pkg.closer = saved_attr
        self.assertEqual(got, "OCR_DIGITS")


# ============================================================================================ 6a. Dakhil / Alim
DAKHIL_CERT = ("BANGLADESH MADRASAH EDUCATION BOARD Dakhil Examination 2020 This is to certify that EXAMPLE SAMPLE of "
               "Sample Alim Madrasah duly passed")
DAKHIL_TR = ("BANGLADESH MADRASAH EDUCATION BOARD Dakhil Examination 2020 Academic Transcript Sample Alim Madrasah "
             "Letter Grade Grade Point A+ 5.00 A 4.00")


class TestDakhilIsSsc(unittest.TestCase):
    def test_the_bots_mixed_level_flag_is_judged_by_the_examination(self):
        pages = [pg(0, T["apostille"], "AC.pdf", 3), pg(1, DAKHIL_CERT, "AC.pdf", 4), pg(2, DAKHIL_TR, "AC.pdf", 5)]
        u = unit("academic", [0, 1, 2], level="SSC", part="p4-6")
        msg = ("the pages after the e-Apostille on page 1 are not all for one qualification (page 2: SSC, page 3: HSC) — "
               "each apostille covers one certificate and one transcript")
        fs = DC._academic_rows([(FLAG, msg), (PASS, "notarised (wording or seal found)")], u, pages, "folder")
        alt = [f for f in fs if f.rule == "academic.level"]
        self.assertEqual([f.verdict for f in alt], [PASS])
        self.assertIn("page 4 is followed by SSC certificate/transcript (page 5, 6)", alt[0].message)
        self.assertNotIn(FLAG, [f.verdict for f in fs])

    def test_really_mixed_levels_keep_the_flag(self):
        pages = [pg(0, T["apostille"]), pg(1, T["ssc_cert"]), pg(2, T["hsc_tr"])]
        msg = ("the pages after the e-Apostille on page 1 are not all for one qualification (page 2: SSC, page 3: HSC) — "
               "each apostille covers one certificate and one transcript")
        fs = DC._academic_rows([(FLAG, msg)], unit("academic", [0, 1, 2], level="SSC"), pages, "bundle")
        self.assertEqual([(f.verdict, f.rule) for f in fs], [(FLAG, "bot.check_academic")])


# ============================================================================================ 6b. page-type ties
ITAX_CERT = ("GOVERNMENT OF THE PEOPLE'S REPUBLIC OF BANGLADESH NATIONAL BOARD OF REVENUE INCOME TAX CERTIFICATE "
             "Name EXAMPLE SAMPLE Tax Identification Number (TIN) " + "4" * 12 + " assessment year Taxes Circle")


class TestTies(unittest.TestCase):
    def tie(self, a: str, b: str, v: float = 7.0):
        pt = PT.page_features("")
        pt.scores = {k: 0.0 for k in pt.scores}
        pt.scores.update({a: v, b: v})
        pt.best, pt.score = a, v
        return pt

    def test_the_title_settles_a_tie(self):
        types = [PT.page_features(T["tin"]), self.tie("tin_certificate", "income_tax"), PT.page_features("")]
        PT.break_ties(types, [T["tin"], ITAX_CERT, ""], ["F.pdf"] * 3)
        self.assertEqual(types[1].best, "income_tax")
        self.assertGreater(types[1].scores["income_tax"], types[1].scores["tin_certificate"])
        self.assertIn("(title)", types[1].tie)

    def test_neighbours_settle_a_tie_without_a_title(self):
        body = "Name EXAMPLE SAMPLE Tax Identification Number 4444"
        types = [PT.page_features(T["income_tax"]), self.tie("tin_certificate", "income_tax"),
                 PT.page_features(T["income_tax"])]
        PT.break_ties(types, [T["income_tax"], body, T["income_tax"]], ["F.pdf"] * 3)
        self.assertEqual(types[1].best, "income_tax")
        letter = "GOVERNMENT OF THE PEOPLE'S REPUBLIC OF BANGLADESH " + body
        types = [PT.page_features(T["tin"]), self.tie("tin_certificate", "income_tax"),
                 PT.page_features(T["income_tax"])]
        PT.break_ties(types, [T["tin"], letter, T["income_tax"]], ["F.pdf"] * 3)
        self.assertEqual(types[1].best, "income_tax")                    # a letterhead starts the next document
        types = [PT.page_features(T["tin"]), self.tie("tin_certificate", "income_tax"),
                 PT.page_features(T["income_tax"])]
        PT.break_ties(types, [T["tin"], body, T["income_tax"]], ["F.pdf"] * 3)
        self.assertEqual(types[1].best, "tin_certificate")               # no letterhead: it continues the TIN

    def test_other_ties_are_left_alone(self):
        pt = PT.page_features(T["academic_in_bank"])
        before = (pt.best, dict(pt.scores))
        PT.break_ties([pt], [T["academic_in_bank"]], ["F.pdf"])
        self.assertEqual((pt.best, pt.scores, pt.tie), (before[0], before[1], ""))

    def test_the_income_tax_certificate_after_the_tin_goes_with_the_income_tax(self):
        fin = "9. FINANCIAL DOCUMENTS.pdf"
        texts = [T["solvency"], T["statement"], T["trade"], T["tin"], ITAX_CERT, "", T["income_tax"], T["income_tax"]]
        res, seg, pages = folder([(fin, None, texts)])
        got = [(u.fine, u.part) for u in res.units]
        self.assertIn(("tin_certificate", "p4"), got)
        self.assertIn(("income_tax", "p5-8"), got)


# ============================================================================================ 6c. the QR ladder
class FakeTin(ModuleType):
    def __init__(self, codes, square=True, blob=False):
        super().__init__(f"{PKG}.tin")
        self.codes, self.read, self.square, self.blob = codes, [], square, blob

    def render_page(self, path, k, dpi):
        return np.zeros((10, 10, 3), np.uint8)

    def _finder_boxes(self, img):
        return [(0, 0, 5, 5)] if self.square else []

    def _blob_boxes(self, img):
        return [(0, 0, 5, 5)] if self.blob else []

    def read_page(self, path, k, img, deep=True):
        self.read.append(k)
        self.deep = deep
        return {"codes": list(self.codes), "stage": "B/test"}

    def meaningful(self, codes):
        return any(len(c) >= 8 for c in codes)


class TestQrLadder(unittest.TestCase):
    def rows(self):
        return [Finding(PASS, "page.colour", "colour scan", "bot", (5,)),
                Finding(PASS, "page.qr", "no QR code on this document — the rule covers documents that carry one",
                        "bot:page_checks.qr_check", (5,))]

    def run_with(self, codes, key="trade_license", fine="trade_license", square=True, blob=False):
        fake = FakeTin(codes, square, blob)
        saved = sys.modules.get(f"{PKG}.tin")
        sys.modules[f"{PKG}.tin"] = fake
        try:
            pages = [pg(0, "x", "F.pdf", 3), pg(1, "y", "F.pdf", 4)]
            u = unit(key, [0, 1], fine=fine, path=Path(r"C:\made-up\TRADE LICENSE p04-05.pdf"), part="p4-5")
            return DC.qr_ladder_rows(self.rows(), u, pages), fake
        finally:
            sys.modules[f"{PKG}.tin"] = saved

    def test_a_degraded_qr_read_by_the_ladder_passes(self):
        out, fake = self.run_with(["https://sample.example.gov.bd/verify/123"])
        qr = [f for f in out if f.rule == "page.qr"]
        self.assertEqual(len(qr), 1)
        self.assertEqual(qr[0].verdict, PASS)
        self.assertTrue(qr[0].message.startswith("QR code read on page 4 with a closer look"))
        self.assertTrue(fake.deep)                                        # finder patterns seen: the deep stages

    def test_nothing_decoded_keeps_the_bots_row_and_says_so(self):
        out, _ = self.run_with([])
        qr = [f for f in out if f.rule == "page.qr"][0]
        self.assertEqual(qr.verdict, PASS)
        self.assertTrue(qr.message.endswith(DC.QR_LADDER_NONE.format(where="pages 4, 5")), qr.message)
        self.assertEqual(qr.evidence["qr_ladder_pages"], [4, 5])

    def test_a_textured_block_alone_gets_the_quick_ladder(self):
        out, fake = self.run_with(["https://sample.example.gov.bd/verify/9"], square=False, blob=True)
        self.assertEqual((fake.read, fake.deep), ([0], False))
        self.assertEqual([f.verdict for f in out if f.rule == "page.qr"], [PASS])

    def test_no_qr_like_block_leaves_the_bots_words(self):
        out, fake = self.run_with([], square=False)
        self.assertEqual(fake.read, [])                                   # nothing worth the slow ladder
        self.assertEqual([f.message for f in out], [f.message for f in self.rows()])
        self.assertEqual(out[1].evidence["qr_ladder"], "no QR-like block on any page")

    def test_other_documents_are_not_looked_at(self):
        out, fake = self.run_with(["https://sample.example/verify"], key="bank", fine="bank")
        self.assertEqual(fake.read, [])
        self.assertEqual([f.message for f in out], [f.message for f in self.rows()])


# ============================================================================================ 7. the seal's evidence
class TestSealEvidence(unittest.TestCase):
    def emblem_page(self) -> np.ndarray:
        import cv2
        img = np.full((1000, 800, 3), 255, np.uint8)                       # BGR
        cv2.circle(img, (400, 120), 60, (0, 0, 220), 14)                   # a red ring at the top centre
        cv2.circle(img, (400, 120), 30, (0, 160, 0), -1)                   # green inside it
        return img

    def test_a_red_ring_with_green_at_the_top_centre_is_emblem_like(self):
        blobs = DC.seal_components(self.emblem_page())
        self.assertTrue(blobs)
        self.assertTrue(all(b["emblem_like"] for b in blobs))

    def test_a_violet_stamp_low_on_the_page_is_not(self):
        import cv2
        img = np.full((1000, 800, 3), 255, np.uint8)
        cv2.circle(img, (600, 800), 70, (200, 40, 140), 12)                # violet (BGR)
        blobs = DC.seal_components(img)
        self.assertTrue(blobs)
        self.assertFalse(any(b["emblem_like"] for b in blobs))

    def test_the_verdict_is_the_bots(self):
        import cv2
        WORK.mkdir(parents=True, exist_ok=True)
        png = WORK / "itax p01.png"
        cv2.imwrite(str(png), self.emblem_page())
        f = Finding(PASS, "bot.check_income_tax", "notarised (wording or seal found)", "bot:check_income_tax", (1,))
        out = DC.with_seal_evidence([f], "Income tax certificate Commissioner of Taxes", png, B._loaded.dv,
                                    B._loaded.R)
        self.assertEqual((out[0].verdict, out[0].message), (PASS, f.message))
        ev = out[0].evidence["notarised"]
        self.assertEqual((ev["wording"], ev["how"], ev["emblem_like_only"]), (False, "seal colour", True))
        worded = DC.with_seal_evidence([f], "Attested by the notary public advocate", png, B._loaded.dv, B._loaded.R)
        self.assertEqual(worded[0].evidence["notarised"]["how"], "wording")


# ============================================================================================ + ECA file, date of birth
ECA = "Extra Curricular Activities Certificate of Participation debate club volunteer workshop competition award"


class TestWeakPageJoinsTheNamedDocument(unittest.TestCase):
    def test_an_eca_files_weak_second_page_is_no_employment_certificate(self):
        weak_employment = "Designation volunteer joined the program in the year"
        self.assertLessEqual(PT.page_features(weak_employment).score, SP.HINT_MIN)
        self.assertEqual(PT.page_features(weak_employment).best, "employment_certificate")
        res, *_ = folder([("EXTRA CURRICULAR ACTIVITIES (ECA).pdf", "eca", [ECA, weak_employment])])
        self.assertEqual([(u.key, u.part) for u in res.units], [("eca", "")])

    def test_a_clear_second_document_is_still_cut(self):
        res, *_ = folder([("TRADE LICENSE.pdf", "trade_license", [T["trade"], T["tin"]])])
        self.assertEqual(sorted(u.fine for u in res.units), ["tin_certificate", "trade_license"])


class TestDobCloserLook(unittest.TestCase):
    def ident(self):
        return Identity(student=NameRef(), father=NameRef(), mother=NameRef(), dob=dt.date(2005, 8, 1),
                        dob_trusted=True, dob_source="MRZ")

    def test_a_misread_digit_is_looked_at_again(self):
        pages = [pg(0, "Secondary School Certificate Examination Date of Birth 04/08/2005 (First August Two Thousand Five)")]
        u = unit("academic", [0], level="SSC", title="ACADEMIC SSC")
        with StubCloser("same") as st:
            fs = DC.dob_rows(pages[0].text, self.ident(), u, pages, eng=object())
        self.assertEqual([f.verdict for f in fs], [PASS])
        self.assertEqual(st.calls, [(["01/08/2005", "04/08/2005"], "digits")])
        with StubCloser("unclear"):
            fs = DC.dob_rows(pages[0].text, self.ident(), u, pages, eng=object())
        self.assertEqual([f.verdict for f in fs], [FLAG])

    def test_no_engine_keeps_the_flag(self):
        pages = [pg(0, "Date of Birth 04/08/2005")]
        fs = DC.dob_rows(pages[0].text, self.ident(), unit("student_nid", [0]), pages)
        self.assertEqual([f.verdict for f in fs], [FLAG])

    def test_as_printed(self):
        d, ref = dt.date(2005, 8, 4), dt.date(2005, 8, 1)
        self.assertEqual(DC.as_printed("04/08/2005", d, ref), "01/08/2005")
        self.assertEqual(DC.as_printed("2005-08-04", d, ref), "2005-08-01")
        self.assertEqual(DC.as_printed("4 August 2005", d, ref), "1 August 2005")


if __name__ == "__main__":
    unittest.main()
