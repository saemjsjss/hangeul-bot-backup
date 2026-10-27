"""Task 7: the birth certificate (RUNNER_SPEC 5.12, owner decision A3). Made-up texts and synthetic images only.

Every name, number and date here is a placeholder ("SAMPLE ONE", a BRN built from "2005" + thirteen 1s, a
QR key built from the bytes 0..47). The engine, the notarisation test and the targeted re-read are fakes;
the network is blocked for the whole module (S4).

    <runner venv python> -I -B -m unittest discover -s <worktree>\\src\\verify\\runner\\tests -t <worktree> -p "test_birth*.py"
"""
from __future__ import annotations

import ast
import base64
import datetime as dt
import shutil
import socket
import tempfile
import unittest
import urllib.request
from pathlib import Path
from unittest import mock

import cv2
import numpy as np

from src.verify.runner import birth as B
from src.verify.runner import model as M
from src.verify.runner.botimport import PRIVATE

D = dt.date(2005, 5, 5)                      # the made-up date of birth used throughout
BRN = "2005" + "1" * 13                      # a made-up 17-digit BRN whose year is D's
OTHER_BRN = "2005" + "2" * 13
KEY64 = base64.b64encode(bytes(range(48))).decode()          # 64 base64 characters = 48 bytes
GOOD_URL = "https://bdris.gov.bd/certificate/verify?key=" + KEY64
SEAL_NAME = "SEALED"

# --- made-up page texts ---------------------------------------------------------------------------------
BILINGUAL = ("Government of the People's Republic of Bangladesh Office of the Registrar, Birth and Death "
             "Registration Birth Registration Certificate Date of Registration Birth Registration Number Date of "
             f"Issuance 12/03/2010 {BRN} 15/06/2022 Date of Birth 05/05/2005 Sex Male In Word Fifth May Two "
             "Thousand Five Name SAMPLE ONE Mother SAMPLE MOTHER Father SAMPLE FATHER Place of Birth Sample Town "
             "This certificate is generated from bdris.gov.bd, and to verify this certificate, please scan the "
             "above QR Code & Bar Code.")


def old_print(dob_field: str = "05 ■ 05 ■ 2005", words: str = "5th May, 2005", extra: str = "") -> str:
    return ("Government of the People's Republic of Bangladesh (Rule 9, 10) BDR Form-3 Birth Registration "
            "Certificate Register No 12 Date of Registration 10/01/2006 Date of Issue 11/01/2006 Birth "
            f"Registration Number {BRN} Name SAMPLE ONE Date of Birth {dob_field} Sex Male In Word {words} "
            "Place of Birth Sample Village Father's Name SAMPLE FATHER Mother's Name SAMPLE MOTHER "
            f"Signature and Seal of the Registrar {extra}")


EVERIFY = ("Birth Registration Record Verification Registered Person Name SAMPLE ONE Date of Birth Birth "
           f"Registration Number Sex 05/05/2005 {BRN} Male Father's Name SAMPLE FATHER Mother's Name SAMPLE "
           "MOTHER Place of Birth Sample Town")
MANUAL = ("(UP BDR Form-3A) Birth Certificate Union Parishad Office Name SAMPLE ONE Father SAMPLE FATHER Mother "
          "SAMPLE MOTHER Date of Birth 05/05/2005 Place of Birth Sample Village Permanent Address Sample Post "
          "Upazila Sample District Sample")


def translation(notary: bool = True, dob: str = "05/05/2005", words: str = "Fifth May Two Thousand Five") -> str:
    return ("Translated True Copy Government of the People's Republic of Bangladesh Office of the Registrar Birth "
            f"Registration Certificate Birth Registration Number {BRN} Name: SAMPLE ONE Date of Birth: {dob} "
            f"In Word: {words} Sex: Male Father's Name: SAMPLE FATHER Mother's Name: SAMPLE MOTHER Place of Birth: "
            "Sample Village Date of Issue: 01/02/2020" + (" Notary Public, Advocate" if notary else ""))


# The English engine reads a Bangla page as noise; a misleading date sits in it (fields must not come from it).
BANGLA = ("Gjol trol yior pqst Tolz Jrol Yiehiel mrtk Grhol jpor 01/01/1999 Trerg zxqv Terr bdfl Giohol wnrt "
          "Jolz Yiol Tpol fgh Trehral qwrt mnbv")
EMPTY = "  .  "


# --- fakes ----------------------------------------------------------------------------------------------
def pages_of(*texts: str, file_pages=None):
    out = []
    for i, t in enumerate(texts):
        fp = file_pages[i] if file_pages else i
        out.append(M.PageRead(index=i, file=f"FILE{i}", file_page=fp, text=t, kind="ocr", chars=len(t),
                              sideways=False, seconds=0.0, image_only=False))
    return out


def unit_of(pages, idx=None) -> M.DocUnit:
    idx = list(range(len(pages))) if idx is None else list(idx)
    return M.DocUnit(uid="D07", key="birth_cert", fine="birth_cert", title="BIRTH CERTIFICATE", pages=idx)


def notarised(text: str, path):
    """The bot's test, faked: the wording, or a seal on the page file (a file named SEALED here)."""
    return "notary" in (text or "").lower() or (path is not None and SEAL_NAME in Path(path).name)


class FakeReader:
    """bot.dv._reader with the recogniser the boxed-date re-read uses: scripted answers in call order."""

    def __init__(self, answers):
        self.answers = list(answers)
        self.calls = 0

    def recognize(self, grey, horizontal_list, free_list):
        self.calls += 1
        text = self.answers.pop(0) if self.answers else ""
        return [([[0, 0], [1, 0], [1, 1], [0, 1]], text, 0.99)]


class FakeEngine:
    def __init__(self, images=None, boxes=(), reader=None, cut=None):
        self.images = images or {}
        self.boxes = list(boxes)
        self.cut = cut
        self.bot = type("Bot", (), {"dv": type("DV", (), {"_reader": reader})()})()
        self.read_boxes_calls = 0
        if cut is not None:
            self.cut_page = self._cut_page

    def page_image_of(self, page, dpi=300):
        return self.images.get(page.index)

    def read_boxes(self, img):
        self.read_boxes_calls += 1
        return list(self.boxes)

    def _cut_page(self, page, out_dir):
        return Path(out_dir) / self.cut.get(page.index, f"PAGE p{page.index + 1:02d}.pdf")


def ident(dob=D, trusted=True, source="MRZ") -> M.Identity:
    return M.Identity(student=M.NameRef(parts=("SAMPLE", "ONE"), written="SAMPLE ONE"), father=M.NameRef(),
                      mother=M.NameRef(), dob=dob, dob_source=source if dob else "", dob_trusted=trusted)


def rows(findings, rule):
    return [f for f in findings if f.rule == rule]


def verdicts(findings, rule):
    return [f.verdict for f in rows(findings, rule)]


# --- synthetic images -----------------------------------------------------------------------------------
def page_image(w: int = 1200, h: int = 1600):
    return np.full((h, w, 3), 255, np.uint8)


def put_qr(img, payload: str, x: int = 40, y: int = 40, scale: int = 6):
    q = cv2.QRCodeEncoder.create().encode(payload)
    q = cv2.resize(q, None, fx=scale, fy=scale, interpolation=cv2.INTER_NEAREST)
    img[y:y + q.shape[0], x:x + q.shape[1]] = q[:, :, None]
    return img


def code128_values(digits: str):
    """Code set C pairs, then Code B for an odd last digit; start, data and the mod-103 check symbol."""
    vals = [105] + [int(digits[i:i + 2]) for i in range(0, len(digits) - len(digits) % 2, 2)]
    if len(digits) % 2:
        vals += [100, ord(digits[-1]) - 32]
    vals.append((vals[0] + sum(i * v for i, v in enumerate(vals[1:], 1))) % 103)
    return vals


def put_barcode(img, digits: str, x: int, y: int, module: int = 3, height: int = 80):
    widths = "".join(B._C128[v] for v in code128_values(digits)) + B.C128_STOP
    cx, dark = x + 10 * module, True
    for wch in widths:
        w = int(wch) * module
        if dark:
            img[y:y + height, cx:cx + w] = 0
        cx += w
        dark = not dark
    return img


def boxed_row_image():
    """Two solid black blocks on one row (the bdris date row) with some ink in the three digit groups."""
    img = page_image(1000, 1400)
    a, c = (400, 500, 30, 40), (490, 500, 30, 40)
    for x, y, w, h in (a, c):
        img[y:y + h, x:x + w] = 0
    for x0 in (340, 368, 435, 460, 525, 553, 581, 609):
        cv2.putText(img, "8", (x0, 535), cv2.FONT_HERSHEY_SIMPLEX, 1.0, (0, 0, 0), 2)
    return img


# --- network guard (S4) ---------------------------------------------------------------------------------
_PATCHES = []


def _no_network(*a, **k):
    raise AssertionError("network use in the birth-certificate tests")


def setUpModule():
    for target in ("socket.socket.connect", "socket.create_connection", "socket.getaddrinfo",
                   "urllib.request.urlopen"):
        p = mock.patch(target, _no_network)
        p.start()
        _PATCHES.append(p)


def tearDownModule():
    while _PATCHES:
        _PATCHES.pop().stop()


class WorkDir(unittest.TestCase):
    def setUp(self):
        base = PRIVATE / "tmp"
        base.mkdir(parents=True, exist_ok=True)
        self.work = Path(tempfile.mkdtemp(prefix="test_birth_", dir=str(base)))

    def tearDown(self):
        if self.work.is_relative_to(PRIVATE):
            shutil.rmtree(self.work, ignore_errors=True)

    def read(self, *texts, eng=None, file_pages=None, idx=None):
        pages = pages_of(*texts, file_pages=file_pages)
        u = unit_of(pages, idx)
        return B.read_birth_certificate(u, pages, eng, self.work, notarised), u, pages


# ========================================================================================================
class TestNoNetwork(unittest.TestCase):
    def test_guard_is_on(self):
        with self.assertRaises(AssertionError):
            socket.create_connection(("127.0.0.1", 9))
        with self.assertRaises(AssertionError):
            urllib.request.urlopen("https://bdris.gov.bd/")

    def test_module_has_no_network_import(self):
        tree = ast.parse(Path(B.__file__).read_text(encoding="utf-8"))
        mods = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                mods |= {a.name for a in node.names}
            elif isinstance(node, ast.ImportFrom):
                mods.add(node.module or "")
        for bad in ("socket", "urllib.request", "http", "http.client", "requests", "urllib3"):
            self.assertNotIn(bad, mods)


class TestPageKindAndFormat(WorkDir):
    def test_page_kinds(self):
        self.assertEqual(B.page_kind(BILINGUAL), "bilingual_qr")
        self.assertEqual(B.page_kind(old_print()), "english_bdris")
        self.assertEqual(B.page_kind(EVERIFY), "everify")
        self.assertEqual(B.page_kind(translation()), "translation")
        self.assertEqual(B.page_kind(MANUAL), "manual_english")
        self.assertEqual(B.page_kind(BANGLA), "bangla")
        self.assertEqual(B.page_kind(EMPTY), "other")
        self.assertEqual(B.page_kind("বা" * 30), "bangla")      # a Bangla text layer

    def test_formats(self):
        cases = [((BILINGUAL,), "bilingual_qr", "", "PASS"),
                 ((old_print(),), "english_bdris", "", "PASS"),
                 ((EVERIFY,), "english_bdris", "everify", "PASS"),
                 ((BANGLA,), "bangla_original", "", "FAIL"),
                 ((BANGLA, translation()), "bangla_original", "", "PASS"),
                 ((translation(),), "english_translation", "", "FLAG"),
                 ((MANUAL,), "unknown", "manual_english", "FLAG"),
                 ((EMPTY,), "unknown", "", "FLAG")]
        for texts, fmt, var, verdict in cases:
            with self.subTest(fmt=fmt, var=var, n=len(texts)):
                f, u, _ = self.read(*texts)
                self.assertEqual((f.format, f.variant), (fmt, var))
                out = B.check_birth_certificate(f, u, ident())
                self.assertEqual(verdicts(out, "birth.format"), [verdict])
                self.assertEqual(f.pages, tuple(u.pages))
                self.assertEqual(len(f.page_kinds), len(texts))

    def test_bangla_original_messages(self):
        f, u, _ = self.read(BANGLA)
        out = B.check_birth_certificate(f, u, ident())
        self.assertIn("no English translation", rows(out, "birth.format")[0].message)
        f, u, _ = self.read(BANGLA, translation())
        self.assertTrue(f.has_translation)
        self.assertEqual(f.page_kinds, ["bangla", "translation"])

    def test_translation_laid_out_like_the_old_print_beside_a_bangla_original(self):
        # the owner's example: the translator copied the old English print's layout (no translation heading)
        f, _, _ = self.read(BANGLA, old_print(extra="Notary Public"))
        self.assertEqual(f.format, "bangla_original")
        self.assertTrue(f.has_translation)
        self.assertEqual(f.english_pages, [1])


class TestNotary(WorkDir):
    def test_bangla_without_translation_fails_even_with_the_registrar_seal(self):
        img = page_image()
        eng = FakeEngine(images={0: img}, cut={0: SEAL_NAME + " p01.pdf"})
        f, u, _ = self.read(BANGLA, eng=eng)
        out = B.check_birth_certificate(f, u, ident())
        self.assertEqual(verdicts(out, "birth.format"), ["FAIL"])
        self.assertEqual(verdicts(out, "birth.notary"), ["FAIL"])
        self.assertFalse(f.notarised)
        self.assertEqual(f.seal_only_pages, [])

    def test_unnotarised_translation_fails(self):
        f, u, _ = self.read(BANGLA, translation(notary=False))
        out = B.check_birth_certificate(f, u, ident())
        self.assertEqual(verdicts(out, "birth.format"), ["PASS"])
        self.assertEqual(verdicts(out, "birth.notary"), ["FAIL"])

    def test_notarised_translation_passes_and_fields_come_from_it_only(self):
        f, u, _ = self.read(BANGLA, translation(notary=True))
        out = B.check_birth_certificate(f, u, ident())
        self.assertEqual(verdicts(out, "birth.format"), ["PASS"])
        self.assertEqual(verdicts(out, "birth.notary"), ["PASS"])
        self.assertEqual(f.notary_pages, [1])
        self.assertEqual(f.english_pages, [1])
        self.assertEqual(f.dob, D)                                     # never the Bangla page's 01/01/1999
        self.assertTrue(all(page == 1 for _, _, page in f.dob_readings))
        self.assertEqual(f.brn, BRN)

    def test_notary_wording_only_on_the_original_is_flag(self):
        f, u, _ = self.read(BANGLA + " Notary Public", translation(notary=False))
        out = B.check_birth_certificate(f, u, ident())
        self.assertEqual(verdicts(out, "birth.notary"), ["FLAG"])

    def test_seal_only_on_the_certificate_print_is_flag(self):
        eng = FakeEngine(images={0: page_image()}, cut={0: SEAL_NAME + " p01.pdf"})
        f, u, _ = self.read(old_print(), eng=eng)
        self.assertEqual(f.seal_only_pages, [0])
        out = B.check_birth_certificate(f, u, ident())
        self.assertEqual(verdicts(out, "birth.notary"), ["FLAG"])
        self.assertIn("seal but no notary wording", rows(out, "birth.notary")[0].message)

    def test_seal_on_a_separate_attestation_page_counts(self):
        eng = FakeEngine(images={0: page_image(), 1: page_image()}, cut={1: SEAL_NAME + " p02.pdf"})
        f, u, _ = self.read(old_print(), EMPTY, eng=eng)
        self.assertTrue(f.notarised)
        self.assertEqual(f.notary_pages, [1])

    def test_seal_colour_on_the_image_when_no_page_file(self):
        img = page_image()
        cv2.circle(img, (600, 1200), 120, (150, 40, 200), -1)          # a violet stamp (RGB)
        self.assertTrue(B.seal_on_image(img[:, :, ::-1].copy()))
        self.assertFalse(B.seal_on_image(page_image()))
        eng = FakeEngine(images={0: page_image(), 1: img})           # no cut_page: the image fallback
        f, _, _ = self.read(old_print(), EMPTY, eng=eng)
        self.assertEqual(f.notary_pages, [1])

    def test_notary_wording_on_the_print(self):
        f, u, _ = self.read(old_print(extra="ATTESTED Notary Public"))
        self.assertTrue(f.notarised)
        self.assertEqual(verdicts(B.check_birth_certificate(f, u, ident()), "birth.notary"), ["PASS"])


class TestBoxedDob(unittest.TestCase):
    def test_blocks_read_as_anything_or_dropped(self):
        for block in ("1", "l", "I", "|", "i", "■", "", " ", "!"):
            with self.subTest(block=repr(block)):
                text = old_print(dob_field=f"05{block}05{block}2005")
                self.assertEqual(B.extract_dob_boxed(text), [D])

    def test_spaced_digits_and_blocks(self):
        self.assertEqual(B.extract_dob_boxed(old_print(dob_field="0 5 l 0 5 l 2 0 0 5")), [D])

    def test_o_for_zero_inside_the_date(self):
        self.assertEqual(B.extract_dob_boxed(old_print(dob_field="O5 ■ O5 ■ 2OO5")), [D])
        self.assertEqual(B.labelled_dob(translation(dob="O5/O5/2OO5")), D)

    def test_ambiguous_boxes_give_candidates(self):
        got = B.extract_dob_boxed(old_print(dob_field="1l1l2005"))  # 1 [l] 1 [l] 2005, or 11 11 2005
        self.assertEqual(sorted(got), [dt.date(2005, 1, 1), dt.date(2005, 11, 11)])

    def test_read_dob_sources(self):
        self.assertEqual(B.read_dob(old_print()), [("boxes", D), ("words", D)])
        self.assertEqual(B.read_dob(translation()), [("label", D), ("words", D)])
        self.assertEqual(B.read_dob(BILINGUAL), [("label", D), ("words", D)])
        self.assertEqual(B.read_dob(EVERIFY), [("label", D)])
        self.assertEqual(B.read_dob(old_print(words="")), [("boxes", D)])

    def test_words_forms(self):
        self.assertEqual(B.parse_dob_in_words("In Word: 5th May, 2005"), D)
        self.assertEqual(B.parse_dob_in_words("In Word Fifth May Two Thousand Five"), D)
        self.assertEqual(B.parse_dob_in_words("In Words: Twenty Ninth of February Two Thousand"),
                         dt.date(2000, 2, 29))
        self.assertEqual(B.parse_dob_in_words("In Word Seventh of December Nineteen Ninety Nine"),
                         dt.date(1999, 12, 7))
        self.assertEqual(B.parse_dob_in_words("In Word Fifth Mayy Two Thousnd Five"), D)   # OCR misspellings
        self.assertEqual(B.parse_dob_in_words("Date of Birth: Fifth May Two Thousand Five"), D)   # no label


class TestNeverIssueOrRegistration(WorkDir):
    def test_issue_and_registration_right_after_the_dob_label(self):
        text = old_print(dob_field="Date of Issue 01/02/2020 Date of Registration 03/04/2019", words="")
        self.assertEqual(B.extract_dob_boxed(text), [])
        self.assertIsNone(B.labelled_dob(text))
        self.assertEqual(B.read_dob(text), [])
        f, u, _ = self.read(text)
        self.assertIsNone(f.dob)
        out = B.check_birth_certificate(f, u, ident())
        self.assertEqual(verdicts(out, "birth.dob"), ["FLAG"])
        self.assertIn("could not be read", rows(out, "birth.dob")[0].message)

    def test_label_followed_by_issue_on_a_translation(self):
        text = ("Translated True Copy Birth Registration Certificate Office of the Registrar Name SAMPLE ONE "
                "Father SAMPLE FATHER Mother SAMPLE MOTHER Date of Birth: Date of Issue: 01/02/2020 "
                "Date of Registration: 03/04/2019 Sex Male Place of Birth Sample")
        self.assertIsNone(B.labelled_dob(text))
        f, _, _ = self.read(BANGLA, text)
        self.assertIsNone(f.dob)
        self.assertEqual(f.issue_date, dt.date(2020, 2, 1))
        self.assertEqual(f.registration_date, dt.date(2019, 4, 3))

    def test_words_line_stops_at_an_issue_label(self):
        self.assertIsNone(B.parse_dob_in_words("In Word: Date of Issue 12th May, 2020"))
        self.assertIsNone(B.parse_dob_in_words("Date of Issue: Fifth May Two Thousand Twenty"))

    def test_dates_kept_apart(self):
        f, _, _ = self.read(old_print())
        self.assertEqual(f.dob, D)
        self.assertEqual(f.registration_date, dt.date(2006, 1, 10))
        self.assertEqual(f.issue_date, dt.date(2006, 1, 11))
        f, _, _ = self.read(BILINGUAL)
        self.assertEqual((f.registration_date, f.issue_date), (dt.date(2010, 3, 12), dt.date(2022, 6, 15)))
        self.assertEqual(f.dob, D)


class TestDobTrust(WorkDir):
    def test_boxes_and_words_agree_trusted(self):
        f, _, _ = self.read(old_print())
        self.assertEqual((f.dob, f.dob_trusted, f.dob_source), (D, True, "boxes+words"))

    def test_label_and_words_agree_trusted(self):
        f, _, _ = self.read(BANGLA, translation())
        self.assertEqual((f.dob, f.dob_trusted, f.dob_source), (D, True, "label+words"))

    def test_disagree_none_and_flag(self):
        f, u, _ = self.read(old_print(words="6th May, 2005"))
        self.assertIsNone(f.dob)
        self.assertFalse(f.dob_trusted)
        out = B.check_birth_certificate(f, u, ident())
        self.assertEqual(verdicts(out, "birth.dob"), ["FLAG"])
        self.assertIn("DOB unclear", rows(out, "birth.dob")[0].message)
        self.assertIn("page 1", rows(out, "birth.dob")[0].message)

    def test_one_source_untrusted(self):
        for text, src in ((old_print(words=""), "boxes"), (translation(words=""), "label"),
                          (old_print(dob_field="", words="5th May, 2005"), "words")):
            with self.subTest(src=src):
                f, _, _ = self.read(BANGLA, text) if src == "label" else self.read(text)
                self.assertEqual((f.dob, f.dob_trusted, f.dob_source), (D, False, src))

    def test_ambiguous_boxes_confirmed_by_words(self):
        f, _, _ = self.read(old_print(dob_field="1l1l2005", words="11th Nov, 2005"))
        self.assertEqual((f.dob, f.dob_trusted, f.dob_source), (dt.date(2005, 11, 11), True, "boxes+words"))

    def test_ambiguous_boxes_against_other_words_is_unclear(self):
        f, _, _ = self.read(old_print(dob_field="1l1l2005", words="5th May, 2005"))
        self.assertIsNone(f.dob)

    def test_ambiguous_boxes_alone_is_unclear(self):
        f, u, _ = self.read(old_print(dob_field="1l1l2005", words=""))
        self.assertIsNone(f.dob)
        msg = rows(B.check_birth_certificate(f, u, ident()), "birth.dob")[0].message
        self.assertIn("DOB unclear", msg)
        self.assertIn("2005-11-11", msg)

    def test_against_the_passport(self):
        other = dt.date(2004, 4, 4)
        f, u, _ = self.read(old_print())                                       # trusted (boxes + words)
        self.assertEqual(verdicts(B.check_birth_certificate(f, u, ident(D)), "birth.dob"), ["PASS"])
        self.assertEqual(verdicts(B.check_birth_certificate(f, u, ident(other)), "birth.dob"), ["FAIL"])
        self.assertEqual(verdicts(B.check_birth_certificate(f, u, ident(other, trusted=False,
                                                                           source="passport label")),
                                  "birth.dob"), ["FLAG"])
        f1, u1, _ = self.read(old_print(words=""))                              # untrusted (boxes only)
        self.assertEqual(verdicts(B.check_birth_certificate(f1, u1, ident(other)), "birth.dob"), ["FLAG"])
        self.assertEqual(verdicts(B.check_birth_certificate(f1, u1, ident(D)), "birth.dob"), ["PASS"])
        fail = rows(B.check_birth_certificate(f, u, ident(other)), "birth.dob")[0].message
        self.assertIn("boxes and words agree", fail)
        self.assertIn("(MRZ)", fail)

    def test_no_passport_dob_or_one_taken_from_this_certificate(self):
        f, u, _ = self.read(old_print())
        self.assertEqual(verdicts(B.check_birth_certificate(f, u, ident(None, trusted=False)), "birth.dob"),
                         ["FLAG"])
        self.assertEqual(verdicts(B.check_birth_certificate(f, u, ident(D, trusted=False,
                                                                           source="birth certificate")),
                                  "birth.dob"), ["FLAG"])

    def test_brn_year(self):
        f, u, _ = self.read(old_print(dob_field="05 ■ 05 ■ 2004", words="5th May, 2004"))
        self.assertEqual(f.dob, dt.date(2004, 5, 5))
        out = B.check_birth_certificate(f, u, ident(dt.date(2004, 5, 5)))
        self.assertEqual(verdicts(out, "birth.brn_year"), ["FLAG"])
        f, u, _ = self.read(old_print())
        self.assertEqual(verdicts(B.check_birth_certificate(f, u, ident()), "birth.brn_year"), [])


class TestImageReread(WorkDir):
    def test_find_dob_row_geometry(self):
        img = boxed_row_image()[:, :, ::-1].copy()
        row = B.find_dob_row(img)
        self.assertIsNotNone(row)
        self.assertEqual(len(row["blocks"]), 2)
        dd, mm, yy = row["groups"]["dd"], row["groups"]["mm"], row["groups"]["yyyy"]
        self.assertTrue(330 <= dd[0] < 345 and dd[1] <= 402, dd)        # left of block 1 (x 400)
        self.assertTrue(428 <= mm[0] and mm[1] <= 492, mm)             # between the blocks (x 430-490)
        self.assertTrue(518 <= yy[0] and 630 <= yy[1] <= 650, yy)      # right of block 2 (x 520), 4 cells
        self.assertIsNone(B.find_dob_row(page_image()[:, :, ::-1].copy()))

    def test_read_dob_groups_plumbing(self):
        img = boxed_row_image()[:, :, ::-1].copy()
        row = B.find_dob_row(img)
        answers = iter(["05", "05", "2005"])
        self.assertEqual(B.read_dob_groups(img, row, lambda g: next(answers)), ("05", "05", "2005"))

    def test_lost_boxes_read_from_the_image_and_confirmed_by_words(self):
        reader = FakeReader(["O5", "05", "2005"])                      # an O for a zero is folded
        eng = FakeEngine(images={0: boxed_row_image()}, boxes=[], reader=reader)
        text = old_print(dob_field="", words="5th May, 2005")
        self.assertEqual(B.extract_dob_boxed(text), [])
        f, u, _ = self.read(text, eng=eng)
        self.assertEqual((f.dob, f.dob_trusted, f.dob_source), (D, True, "boxes+words"))
        self.assertGreaterEqual(reader.calls, 3)
        self.assertTrue(any("re-read" in n for n in f.notes))

    def test_image_reread_alone_is_untrusted(self):
        eng = FakeEngine(images={0: boxed_row_image()}, boxes=[], reader=FakeReader(["05", "05", "2005"]))
        f, _, _ = self.read(old_print(dob_field="", words=""), eng=eng)
        self.assertEqual((f.dob, f.dob_trusted), (D, False))

    def test_unreadable_image_digits_give_no_dob(self):
        eng = FakeEngine(images={0: boxed_row_image()}, boxes=[], reader=FakeReader([]))
        f, _, _ = self.read(old_print(dob_field="", words=""), eng=eng)
        self.assertIsNone(f.dob)


class TestQr(WorkDir):
    def setUp(self):
        super().setUp()
        self._budget = mock.patch.object(B, "QR_BUDGET_S", 3.0)
        self._budget.start()

    def tearDown(self):
        self._budget.stop()
        super().tearDown()

    def bilingual(self, payload=None, barcode=None, text=BILINGUAL, printed_name="SAMPLE ONE", confirmed=True,
                  confirm=None):
        img = page_image()
        if payload:
            put_qr(img, payload)
        if barcode:
            put_barcode(img, barcode, x=650, y=60)
        f, u, _ = self.read(text, eng=FakeEngine(images={0: img}))
        return f, B.check_birth_certificate(f, u, ident(), printed_name, confirmed, confirm)

    def test_genuine_link_pass_and_online_note(self):
        f, out = self.bilingual(GOOD_URL)
        self.assertEqual(f.qr_status, "decoded")
        self.assertEqual((f.qr["host"], f.qr["key_len"], f.qr["key_bytes"]), ("bdris.gov.bd", 64, 48))
        self.assertEqual(verdicts(out, "birth.qr"), ["PASS", "NOTE"])
        self.assertIn("checked online", rows(out, "birth.qr")[1].message)
        self.assertNotIn("FAIL", [x.verdict for x in out if x.rule.startswith("birth.qr")])
        self.assertTrue((self.work / "birth_qr_p01.json").exists())

    def test_look_alike_host_fails(self):
        f, out = self.bilingual("https://bdris-gov.sample.workers.dev/certificate/verify?key=" + KEY64)
        self.assertEqual(f.qr_status, "decoded")
        self.assertEqual(verdicts(out, "birth.qr"), ["FAIL"])
        self.assertIn("sample.workers.dev", rows(out, "birth.qr")[0].message)

    def test_http_not_https_fails(self):
        _, out = self.bilingual("http://bdris.gov.bd/certificate/verify?key=" + KEY64)
        self.assertEqual(verdicts(out, "birth.qr"), ["FAIL"])
        self.assertIn("https", rows(out, "birth.qr")[0].message)

    def test_short_key_flag(self):
        f, out = self.bilingual("https://bdris.gov.bd/certificate/verify?key=" + KEY64[:52])
        self.assertEqual(f.qr["key_len"], 52)
        self.assertEqual(verdicts(out, "birth.qr"), ["FLAG"])

    def test_key_with_a_space_flag(self):
        self.assertFalse(B.key_well_formed(B.parse_payload(GOOD_URL.replace(KEY64[:8], KEY64[:7] + " "))))
        self.assertTrue(B.key_well_formed(B.parse_payload(GOOD_URL)))

    def test_no_qr_on_a_bilingual_print_flag(self):
        f, out = self.bilingual(None)
        self.assertIn(f.qr_status, ("absent", "undecoded"))
        self.assertEqual(verdicts(out, "birth.qr"), ["FLAG"])
        self.assertIn("could not be decoded", rows(out, "birth.qr")[0].message)

    def test_no_engine_means_no_qr_flag(self):
        f, u, _ = self.read(BILINGUAL)
        out = B.check_birth_certificate(f, u, ident())
        self.assertEqual(f.qr_status, "undecoded")
        self.assertEqual(verdicts(out, "birth.qr"), ["FLAG"])

    def test_payload_name_rules(self):
        f, out = self.bilingual("Name: SAMPLE TWO; issued by the sample office")
        self.assertEqual(f.qr_name, "SAMPLE TWO")
        self.assertEqual(verdicts(out, "birth.qr"), ["FAIL"])                  # a text, not a bdris link
        self.assertEqual(verdicts(out, "birth.qr_name"), ["FAIL"])
        unit = M.DocUnit(uid="D07", key="birth_cert", fine="birth_cert", title="BIRTH CERTIFICATE", pages=[0])
        mr = B.qr_machine_read(f, unit)
        self.assertEqual((mr.person, mr.name, mr.source, mr.page), ("student", "SAMPLE TWO", "birth QR", 0))
        for printed, conf, want in (("SAMPLE ONE", False, "FLAG"), ("SAMPLE TWO", False, "PASS"),
                                    ("", True, "FLAG"), ("MD SAMPLE TWO", True, "FAIL")):
            with self.subTest(printed=printed, conf=conf):
                out = B.check_birth_certificate(f, unit, ident(), printed, conf)
                self.assertEqual(verdicts(out, "birth.qr_name"), [want])

    def test_genuine_qr_has_no_machine_read(self):
        f, _ = self.bilingual(GOOD_URL)
        self.assertIsNone(B.qr_machine_read(f, unit_of(pages_of(BILINGUAL))))

    def test_barcode_decoded_and_equal(self):
        f, out = self.bilingual(GOOD_URL, barcode=BRN)
        self.assertEqual(f.qr["barcode"]["status"], "decoded")
        self.assertEqual(f.qr_brn, BRN)
        self.assertEqual(verdicts(out, "birth.qr_brn"), ["PASS"])

    def test_barcode_different_flag_unless_the_print_is_confirmed(self):
        f, out = self.bilingual(GOOD_URL, barcode=OTHER_BRN)
        self.assertEqual(f.qr_brn, OTHER_BRN)
        self.assertFalse(f.brn_confirmed)
        self.assertEqual(verdicts(out, "birth.qr_brn"), ["FLAG"])
        # the zoomed re-read confirms the printed BRN -> FAIL
        asked = []

        def confirm(page, variant):
            asked.append((page, variant))
            return "confirmed"
        u = unit_of(pages_of(BILINGUAL))
        out = B.check_birth_certificate(f, u, ident(), "SAMPLE ONE", True, confirm)
        self.assertEqual(verdicts(out, "birth.qr_brn"), ["FAIL"])
        self.assertEqual(asked, [(0, BRN)])
        self.assertTrue(f.brn_confirmed)

    def test_barcode_rules_on_facts(self):
        u = unit_of(pages_of(BILINGUAL))
        base = dict(pages=(0,), format="bilingual_qr", page_kinds=["bilingual_qr"], english_pages=[0],
                    qr_status="decoded", brn=BRN, qr_brn=OTHER_BRN, dob=D, dob_trusted=True,
                    dob_source="label+words", dob_readings=[("label", D.isoformat(), 0)])
        for status, confirmed, want in (("decoded", True, "FAIL"), ("decoded", False, "FLAG"),
                                        ("weak", True, "FLAG")):
            with self.subTest(status=status, confirmed=confirmed):
                q = B.parse_payload(GOOD_URL)
                q.update(status="decoded", payload=GOOD_URL, page=0, barcode={"status": status, "payload": OTHER_BRN})
                f = M.BirthFacts(qr=q, brn_confirmed=confirmed, **base)
                out = B.check_birth_certificate(f, u, ident())
                self.assertEqual(verdicts(out, "birth.qr_brn"), [want])
                self.assertEqual(verdicts(out, "birth.qr"), ["PASS", "NOTE"])

    def test_payload_dob(self):
        u = unit_of(pages_of(BILINGUAL))
        for qdob, trusted, want in ((D, True, "PASS"), (dt.date(2004, 4, 4), True, "FAIL"),
                                    (dt.date(2004, 4, 4), False, "FLAG")):
            with self.subTest(qdob=qdob, trusted=trusted):
                f = M.BirthFacts(pages=(0,), format="bilingual_qr", page_kinds=["bilingual_qr"], qr_status="decoded",
                                 qr={"status": "decoded", "payload": "Name: X DOB 05/05/2005", "kind": "text", "page": 0},
                                 dob=D, dob_trusted=trusted, qr_dob=qdob)
                self.assertEqual(verdicts(B.check_birth_certificate(f, u, ident()), "birth.qr_dob"), [want])

    def test_everify_link_and_parse_payload(self):
        p = B.parse_payload("https://everify.bdris.gov.bd/BirthQR/Index/?qc=" + KEY64 + KEY64[:44])
        self.assertEqual(p["kind"], "bdris_everify_url")
        self.assertEqual(B.parse_payload(GOOD_URL)["kind"], "bdris_verify_url")
        self.assertEqual(B.parse_payload("https://sample.example/verify?key=" + KEY64)["kind"], "url")
        t = B.parse_payload(f"BRN {BRN} DOB 05/05/2005 Name: SAMPLE ONE")
        self.assertEqual(t["text_fields"], {"brn": BRN, "dob": "05/05/2005", "name": "SAMPLE ONE"})

    def test_offline_match(self):
        q = B.parse_payload(GOOD_URL)
        q.update(status="decoded", payload=GOOD_URL, barcode={"status": "decoded", "payload": BRN})
        self.assertEqual(B.offline_match(q, BRN, D), ("match", []))
        self.assertEqual(B.offline_match(q, OTHER_BRN, D)[0], "mismatch")
        self.assertEqual(B.offline_match({"status": "absent"}), ("absent", []))

    def test_pyzbar_and_opencv_alone_both_decode(self):
        img = put_qr(page_image(), GOOD_URL)[:, :, ::-1].copy()
        for use_zbar in (True, False):
            with self.subTest(use_zbar=use_zbar):
                r = B.decode_birth_qr(img, budget_s=3.0, barcode=False, use_zbar=use_zbar)
                self.assertEqual((r["status"], r["payload"]), ("decoded", GOOD_URL))


class TestPagesAndContract(WorkDir):
    def test_folder_pages_are_file_pages(self):
        # the unit sits at run pages 3-4, which are pages 1-2 of its folder file
        texts = ("x", "x", "x", BANGLA, translation(notary=True))
        pages = pages_of(*texts, file_pages=[0, 1, 2, 0, 1])
        u = unit_of(pages, [3, 4])
        f = B.read_birth_certificate(u, pages, None, self.work, notarised)
        self.assertEqual(f.pages, (3, 4))
        self.assertEqual(f.english_pages, [4])
        out = B.check_birth_certificate(f, u, ident())
        self.assertEqual(rows(out, "birth.notary")[0].pages, (2,))
        self.assertEqual(rows(out, "birth.format")[0].pages, (1, 2))
        self.assertIn("page 2", rows(out, "birth.notary")[0].message)

    def test_findings_contract(self):
        f, u, _ = self.read(old_print(extra="Notary Public"))
        out = B.check_birth_certificate(f, u, ident())
        self.assertEqual({x.rule for x in out}, {"birth.format", "birth.notary", "birth.dob"})
        for x in out:
            self.assertIsInstance(x, M.Finding)
            self.assertEqual(x.source, "runner:birth")
            self.assertIn(x.verdict, (M.PASS, M.NOTE, M.FLAG, M.FAIL))
            self.assertTrue(x.pages and all(p >= 1 for p in x.pages))
        self.assertEqual([x.verdict for x in out], ["PASS", "PASS", "PASS"])

    def test_facts_serialise_without_images(self):
        import dataclasses
        import json
        f, _, _ = self.read(BILINGUAL)
        d = dataclasses.asdict(f)
        self.assertEqual(set(d), {fl.name for fl in dataclasses.fields(M.BirthFacts)})
        json.dumps(d, default=str)

    def test_facts_built_elsewhere_still_check(self):
        f = M.BirthFacts(pages=(5,), format="english_bdris", page_kinds=["english_bdris"], english_pages=[5],
                         notarised=True, notary_pages=[5], dob=D, dob_trusted=True, dob_source="boxes+words",
                         dob_readings=[("boxes", D.isoformat(), 5), ("words", D.isoformat(), 5)])
        u = M.DocUnit(uid="D02", key="birth_cert", fine="birth_cert", title="BIRTH CERTIFICATE", pages=[5])
        out = B.check_birth_certificate(f, u, ident())
        self.assertEqual([x.verdict for x in out], ["PASS", "PASS", "PASS"])
        self.assertEqual(rows(out, "birth.notary")[0].pages, (6,))

    def test_work_dir_outside_private_writes_nothing(self):
        outside = PRIVATE.parent / "not-private-runner-birth-test"
        pages = pages_of(BILINGUAL)
        eng = FakeEngine(images={0: put_qr(page_image(), GOOD_URL)}, cut={0: "X.pdf"})
        with mock.patch.object(B, "QR_BUDGET_S", 3.0):
            f = B.read_birth_certificate(unit_of(pages), pages, eng, outside, notarised)
        self.assertEqual(f.qr_status, "decoded")
        self.assertFalse(outside.exists())


if __name__ == "__main__":
    unittest.main()
