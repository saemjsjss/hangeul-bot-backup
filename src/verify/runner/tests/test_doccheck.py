"""doccheck.py (RUNNER_SPEC 5.8 and Task 5) on made-up text, synthetic PDFs and synthetic page images.

The bot's checkers are the real ones from this worktree (botimport). The other tasks' modules (tin, birth,
names, identity) are faked through sys.modules. Every name is a placeholder ("SAMPLE ONE"); every number is
built at run time. Scratch files go under C:\\Hangeul\\DOCCHECK\\lab\\private-runner\\tmp and are removed.
"""
from __future__ import annotations

import ast
import datetime as dt
import inspect
import re
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import numpy as np

from src.verify.runner import botimport as B
from src.verify.runner import doccheck as D
from src.verify.runner.model import (ERROR, FAIL, FLAG, NOTE, ORDER, PASS, PRESENT, AffidavitInfo, BirthFacts,
                                     DerivedRecord, DocUnit, Finding, Identity, MachineRead, NameRef, PageRead)

TODAY = dt.date(2026, 10, 27)
NS = B.load(B._loaded.cache_root if B._loaded is not None else B.DEFAULT_CACHE_ROOT)
dv, PC, R = NS.dv, NS.PC, NS.R
PKG = "src.verify.runner"
NUM10 = "52341" + "86709"                  # a made-up 10-digit ID number (not a mobile: no leading 01)
PPNO = "A" + "0" + "7654321"               # a made-up 9-character passport number
OTHER_PPNO = "A" + "0" + "7654329"

# ~110 characters, under 25 words: the text layer is read (no OCR) and the page is not "computer-made",
# so the colour rule really measures it
SCANLIKE = ("Certificate issued by the Union Parishad office for the family members listed below with "
            "their registration")


def blank_identity(**kw) -> Identity:
    ident = Identity(student=NameRef(), father=NameRef(), mother=NameRef())
    for k, v in kw.items():
        setattr(ident, k, v)
    return ident


def page(i: int, text: str, file: str = "bundle.pdf", file_page: int = None) -> PageRead:
    return PageRead(index=i, file=file, file_page=i if file_page is None else file_page, text=text, kind="ocr",
                    chars=len(text), sideways=False, seconds=0.0, image_only=False)


def unit(uid: str, key: str, pages, path=None, fine: str = "", title: str = "", level: str = "", sub=()):
    return DocUnit(uid=uid, key=key, fine=fine or key, title=title or key.upper(), pages=list(pages),
                   level=level, sub=list(sub), path=Path(path) if path else None)


class Tmp:
    root: Path = None

    @classmethod
    def make(cls) -> Path:
        base = B.PRIVATE / "tmp"
        base.mkdir(parents=True, exist_ok=True)
        cls.root = Path(tempfile.mkdtemp(prefix="test_doccheck_", dir=str(base)))
        return cls.root

    @classmethod
    def clean(cls) -> None:
        if cls.root and cls.root.is_relative_to(B.PRIVATE) and cls.root.exists():
            shutil.rmtree(cls.root, ignore_errors=True)


def make_pdf(path: Path, texts, colour=False, qr=None) -> Path:
    """One page per text (each text > 80 characters, so the bot reads its text layer). colour: a red block
    (a coloured scan); qr: {page index: payload} drawn as a QR code image."""
    import cv2
    import pymupdf
    doc = pymupdf.open()
    for i, t in enumerate(texts):
        assert len(t) > 80, "a test page needs a text layer of more than 80 characters"
        pg = doc.new_page(width=595, height=842)
        rc = pg.insert_textbox(pymupdf.Rect(36, 36, 559, 600), t, fontsize=8)
        assert rc >= 0, "text did not fit"
        if colour:
            pg.draw_rect(pymupdf.Rect(420, 700, 520, 790), color=(0.85, 0.1, 0.1), fill=(0.85, 0.1, 0.1))
        if qr and i in qr:
            code = cv2.QRCodeEncoder.create().encode(qr[i])
            code = cv2.resize(code, (code.shape[1] * 8, code.shape[0] * 8), interpolation=cv2.INTER_NEAREST)
            code = cv2.copyMakeBorder(code, 32, 32, 32, 32, cv2.BORDER_CONSTANT, value=255)
            ok, png = cv2.imencode(".png", code)
            pg.insert_image(pymupdf.Rect(380, 620, 560, 800), stream=png.tobytes())
    path.parent.mkdir(parents=True, exist_ok=True)
    doc.save(str(path))
    doc.close()
    return path


def fake_eng(bot=NS, images=None, boxes=None):
    def page_image_of(p, dpi=300):
        if images is None or p.index not in images:
            raise RuntimeError("no image in this test")
        return images[p.index]

    def read_boxes(img):
        if boxes is None:
            raise RuntimeError("no boxes in this test")
        return boxes
    return SimpleNamespace(bot=bot, page_image_of=page_image_of, read_boxes=read_boxes)


def run(units, pages, program="KLP", mode="bundle", ident=None, eng=None, births=None, aff=None, **kw):
    rec = DerivedRecord(program, TODAY, ident or blank_identity())
    return D.check_documents(units, pages, rec, eng or fake_eng(), mode, aff or AffidavitInfo(), births or {},
                             lambda i, v: "unclear", **kw)


def msgs(fs, rule=None):
    return [f.message for f in fs if rule is None or f.rule == rule]


class Proxy:
    """A bot module with a few functions replaced."""

    def __init__(self, real, **over):
        self._real = real
        self.__dict__.update(over)

    def __getattr__(self, name):
        return getattr(self._real, name)


def fake_tin(codes_by_page):
    return SimpleNamespace(read_qr=lambda path, deep=True: [{"page": k, "codes": c} for k, c in codes_by_page.items()],
                           meaningful=lambda codes: any(len(c) >= 8 for c in codes))


# =============================================================================================================
class TestPinnedMessages(unittest.TestCase):
    """Every DROP / KEEP_ONLY / REWRITE pattern matches a string literal of the bot's code as it stands (an
    f-string as its template, and with each field shown as 0), so a drift in the bot breaks this test."""

    @staticmethod
    def literals(src: str):
        out = set()
        for node in ast.walk(ast.parse(src)):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                out.add(node.value)
            elif isinstance(node, ast.JoinedStr):
                a, b = [], []
                for v in node.values:
                    if isinstance(v, ast.Constant):
                        a.append(str(v.value))
                        b.append(str(v.value))
                    else:
                        a.append("{" + ast.unparse(v.value) + "}")
                        b.append("0")
                out.add("".join(a))
                out.add("".join(b))
        return out

    def test_pinned_messages(self):
        lits = self.literals(inspect.getsource(dv)) | self.literals(inspect.getsource(PC))
        patterns = [p for ps in D.DROP.values() for p in ps] + D.DROP_BOARD_QR
        patterns += [p for ps in D.KEEP_ONLY.values() for p in ps]
        patterns += [p for t in D.REWRITE.values() for p in t]
        patterns += [D.APOSTILLE_PAGE1] + D.HOLDER_ROWS
        missing = [p for p in patterns if not any(re.search(p, s) for s in lits)]
        self.assertEqual(missing, [])

    def test_drop_tables_cover_the_spec(self):
        self.assertEqual(set(D.DROP), {"father_nid", "mother_nid", "student_nid", "bank", "trade_license"})
        self.assertEqual(len(D.DROP["bank"]), 10)
        self.assertEqual(set(D.KEEP_ONLY), {"photo"})
        self.assertEqual(set(D.REWRITE), {"student_nid"})


class TestEmptyPortal(unittest.TestCase):
    """With DerivedRecord.portal_view() the bot's loose name, GPA, holder and sponsor rows cannot fire."""

    TEXT = ("Father SAMPLE ONE Mother SAMPLE TWO student SAMPLE THREE notarised by advocate GPA 5.00 SSC GPA 4.50 "
            "This is to certify that SAMPLE ONE is a Registered Taxpayer TIN certificate Name of Proprietor: SAMPLE "
            "ONE Name of business trade licence Account Balance Tk 25,00,000.00 solvency to whom it may concern")

    def setUp(self):
        self.view = DerivedRecord("KLP", TODAY, blank_identity()).portal_view()

    def assert_clean(self, rows, extra=()):
        for _, m in rows:
            self.assertFalse(re.search(r"'s name|name '|GPA", m), m)
            self.assertFalse(any(re.search(p, m) for p in list(D.HOLDER_ROWS) + list(extra)), m)

    def test_view(self):
        self.assertEqual({k: v for k, v in self.view.items() if v}, {"Program": "KLP"})

    def test_bot_checkers(self):
        self.assert_clean(dv.check_nid(self.TEXT, self.view, [], "father", None, {"passport": self.TEXT}))
        self.assert_clean(dv.check_family_cert(self.TEXT, self.view, [], None))
        self.assert_clean(dv.check_academic(self.TEXT, self.view, [], None, {}))
        self.assert_clean(dv.check_bank(self.TEXT, self.view, [], "KLP", None, {}), [r"sponsor"])
        self.assert_clean(dv.check_financial(self.TEXT, self.view, [], None, {}), [r"sponsor"])

    def test_holder_row_would_stop_the_run(self):
        leaky = Proxy(dv, check_bank=lambda *a, **k: [(PASS, "account appears to be in the name of X")])
        tmp = Tmp.make()
        try:
            pdf = make_pdf(tmp / "bank.pdf", [self.TEXT])
            with self.assertRaises(D.PortalLeak):
                run([unit("D01", "bank", [0], pdf)], [page(0, self.TEXT)],
                    eng=fake_eng(bot=SimpleNamespace(dv=leaky, PC=PC, R=R, C=NS.C)))
        finally:
            Tmp.clean()


class TestUnits(unittest.TestCase):
    """check_documents on synthetic bundles: DROP, REWRITE, ORDER, income tax, death certificates, page rows."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = Tmp.make()

    @classmethod
    def tearDownClass(cls):
        Tmp.clean()

    def pdf(self, name, texts, **kw):
        return make_pdf(self.tmp / name, texts, **kw)

    def test_bank_drops(self):
        sol = ("TO WHOM IT MAY CONCERN This is to certify that the account holder maintains a savings account with "
               "us. Account opening date 01/01/2020. Balance Tk 25,00,000.00 equivalent to USD 20,500. Date: "
               "01/10/2026. Manager signature and seal.")
        stm = ("Statement of Account Generation Date 01/10/2026 Opening Balance 19,00,000.00 deposit withdrawal "
               "closing balance 25,00,000.00 transaction details for the period shown")
        p = self.pdf("bank.pdf", [sol, stm])
        raw = dv.check_bank(sol + "\n" + stm, {}, [], "KLP", p, {})
        dropped = [m for _, m in raw if any(re.search(x, m) for x in D.DROP["bank"])]
        self.assertTrue(any(m.startswith("account opened") for m in dropped), raw)
        self.assertTrue(any(re.search(r"USD|seal|dated", m) for m in dropped), raw)
        u = unit("D01", "bank", [0, 1], p)
        fs = run([u], [page(0, sol), page(1, stm)]).per_unit["D01"]
        for m in msgs(fs):
            self.assertFalse(any(re.search(x, m) for x in D.DROP["bank"]), m)
        self.assertTrue(any(f.source == "bot:check_bank" for f in fs))

    def test_trade_licence_drops_the_loose_tin_row(self):
        t = ("TIN Certificate This is to Certify that SAMPLE ONE is a Registered Taxpayer of National Board of "
             "Revenue. Name of Proprietor: SAMPLE ONE Name of business SAMPLE STORE trade licence fiscal year "
             "2026-2027 renewal")
        raw = dv.check_financial(t, {}, [], None, {})
        self.assertTrue(any(m.startswith("TIN and trade licence are both in the name of") for _, m in raw), raw)
        fs = run([unit("D01", "trade_license", [0], self.pdf("trade.pdf", [t]))], [page(0, t)]).per_unit["D01"]
        self.assertFalse(any(re.search(x, m) for m in msgs(fs) for x in D.DROP["trade_license"]), msgs(fs))
        self.assertTrue(any(f.source == "bot:check_financial" for f in fs))

    def test_nid_rows_dropped_and_rewritten(self):
        t1 = f"Translation of the National ID card. Name SAMPLE ONE. NID No: {NUM10}. Date of Birth 06/05/1970 card"
        t2 = f"Government of the People's Republic. National ID card original. ID NO {NUM10} 77 holder signature"
        p = self.pdf("snid.pdf", [t1, t2])
        fs = run([unit("D01", "student_nid", [0, 1], p)], [page(0, t1), page(1, t2)]).per_unit["D01"]
        bot = [f for f in fs if f.source == "bot:check_nid"]
        for f in bot:
            self.assertFalse(any(re.search(x, f.message) for x in D.DROP["student_nid"]), f.message)
        self.assertIn("no notarisation found — the student's NID must be notarised", [f.message for f in bot])
        self.assertFalse(any("parents' NID" in f.message for f in fs))
        num = [f for f in fs if f.rule == "nid.number"]
        self.assertEqual([f.verdict for f in num], [PASS], [f.message for f in num])

    def test_nid_number_rows_glued_digits(self):
        t1, t2 = f"NID No: {NUM10} translation page", f"ID NO {NUM10} 77 original card"
        u = unit("D01", "mother_nid", [3, 4])
        # the bot's own reading of these pages: the glued page has no number -> the false "not compared" NOTE
        per = [set(dv.nid_numbers(t1)), set(dv.nid_numbers(t2))]
        self.assertEqual(per, [{NUM10}, set()])
        self.assertEqual([v for v, _ in dv.nid_number_agreement(per, [NUM10])], [NOTE])
        fs = D.nid_number_rows([t1, t2], u)
        self.assertEqual([f.verdict for f in fs], [PASS])
        self.assertFalse(any("not on two pages" in f.message for f in fs))
        self.assertEqual(fs[0].pages, (4, 5))
        self.assertEqual([f.verdict for f in D.nid_number_rows(["no number here", "nor here"], u)], [FLAG])

    def test_bundle_academic_apostille_order_and_folder_fail(self):
        cert = ("Board of Intermediate and Secondary Education. Higher Secondary Certificate examination. This is to "
                "certify that the candidate passed. notarised")
        apo = ("e-Apostille issued by the Ministry of Foreign Affairs under the convention of 5 October 1961 for the "
               "document attached")
        link = "https://apostille.mygov.bd/verify/" + "123456789"
        p = self.pdf("hsc.pdf", [cert, apo], qr={1: link})
        pages = [page(5, cert), page(6, apo)]
        pages = [page(0, "x" * 90)] * 5 + pages
        with mock.patch.dict(sys.modules, {PKG + ".tin": fake_tin({})}):
            fs = run([unit("D01", "academic", [5, 6], p, level="HSC", title="ACADEMIC HSC")], pages).per_unit["D01"]
            order = [f for f in fs if f.verdict == ORDER]
            self.assertEqual([(f.rule, f.pages) for f in order], [("academic.apostille_order", (7,))])
            self.assertIn("the e-Apostille for HSC is on page 7", order[0].message)
            self.assertFalse(any(m.startswith("page 1 is not an e-Apostille") for m in msgs(fs)))
            folder = run([unit("D01", "academic", [5, 6], p, level="HSC")], pages, mode="folder").per_unit["D01"]
            self.assertTrue(any(f.verdict == FAIL and f.message.startswith("page 1 is not an e-Apostille")
                                for f in folder))
            # "no e-Apostille" stays FAIL
            q = self.pdf("ssc.pdf", [cert.replace("Higher Secondary", "Secondary School")])
            none = run([unit("D02", "academic", [5], q, level="SSC")], pages).per_unit["D02"]
            self.assertTrue(any(f.verdict == FAIL and "no e-Apostille" in f.message for f in none), msgs(none))

    def test_board_qr(self):
        cert = ("Board of Intermediate and Secondary Education. Secondary School Certificate examination. This is to "
                "certify that the candidate passed. notarised")
        p = self.pdf("ssc_qr.pdf", [cert, cert])
        pages = [page(i, "y" * 90) for i in range(7)] + [page(7, cert), page(8, cert)]
        payload = "123456/Md.Sample One/5.00"
        with mock.patch.dict(sys.modules, {PKG + ".tin": fake_tin({1: [payload]})}):
            u = unit("D01", "academic", [7, 8], p, level="SSC")
            found, machine = D.board_qr(u, pages)
            self.assertEqual([(f.verdict, f.rule, f.pages) for f in found], [(PASS, "academic.board_qr", (9,))])
            self.assertEqual(machine, [MachineRead(unit="D01", person="student", name="Md.Sample One",
                                                   source="board QR", page=8)])
            dc = run([u], pages)
            fs = dc.per_unit["D01"]
            self.assertEqual(dc.machine, machine)
            self.assertFalse([f for f in fs if f.rule == "page.qr"])          # qr_check skipped
            self.assertFalse(any(re.search(x, m) for m in msgs(fs) for x in D.DROP_BOARD_QR))
        with mock.patch.dict(sys.modules, {PKG + ".tin": fake_tin({0: ["https://apostille.mygov.bd/x/1"]})}):
            self.assertEqual(D.board_qr(u, pages), ([], []))                  # an apostille code is no board QR
            self.assertTrue([f for f in run([u], pages).per_unit["D01"] if f.rule == "page.qr"])

    def test_board_qr_names(self):
        self.assertEqual(D.board_qr_name("123456/Md.Sample One/5.00"), "Md.Sample One")
        self.assertEqual(D.board_qr_name("Roll: 123456\nName: SAMPLE ONE\nFather's Name: SAMPLE TWO\nGPA: 5.00"),
                         "SAMPLE ONE")
        self.assertEqual(D.board_qr_name("Father's Name: SAMPLE TWO, Name: SAMPLE ONE, GPA: 5.00"), "SAMPLE ONE")
        self.assertEqual(D.board_qr_name("https://example.invalid/result?roll=123456"), "")

    def test_income_tax_klp_info_only_master_unchanged(self):
        t = ("Income Tax Return acknowledgement for the assessment year 2026-2027. Tax paid Tk 5,000 by the "
             "assessee as shown in the return")
        p = self.pdf("tax.pdf", [t])
        klp = run([unit("D01", "income_tax", [0], p)], [page(0, t)], program="KLP").per_unit["D01"]
        self.assertFalse([f for f in klp if f.verdict in (FLAG, FAIL)], [(f.verdict, f.message) for f in klp])
        notes = [f for f in klp if f.verdict == NOTE and f.message.endswith(D.INFO_SUFFIX)]
        self.assertTrue(any("tax paid" in f.message for f in notes))
        self.assertTrue(any(f.evidence.get("was") == FLAG for f in notes))
        self.assertEqual([(f.verdict, f.message) for f in klp if f.rule == "doc.present_info"],
                         [(PRESENT, "present — information only for KLP")])
        master = run([unit("D01", "income_tax", [0], p)], [page(0, t)], program="MASTER").per_unit["D01"]
        self.assertTrue(any(f.verdict == FLAG and "tax paid" in f.message for f in master))
        self.assertFalse(any(f.rule == "doc.present_info" for f in master))
        self.assertFalse(any(D.INFO_SUFFIX in f.message for f in master))

    def test_death_certificates(self):
        t = "Death certificate issued by the Union Parishad for the deceased person named below with the date of death"
        p = self.pdf("death.pdf", [t])
        n = self.pdf("fnid.pdf", [f"National ID card of the holder. NID No {NUM10}. Notarised by advocate, notary "
                                  "public seal and signature"])
        both = run([unit("D01", "father_nid", [0], n), unit("D02", "father_death_cert", [1], p)],
                   [page(0, "z" * 90), page(1, t)]).per_unit["D02"]
        self.assertEqual([(f.verdict, f.rule) for f in both if f.rule.startswith("death.")],
                         [(NOTE, "death.stands_in")])
        alone = run([unit("D02", "father_death_cert", [1], p)], [page(0, "z" * 90), page(1, t)]).per_unit["D02"]
        self.assertEqual([(f.verdict, f.message) for f in alone if f.rule.startswith("death.")],
                         [(PASS, "accepted in place of the father's NID")])
        unknown = run([unit("D03", "death_cert", [1], p)], [page(0, "z" * 90), page(1, t)]).per_unit["D03"]
        self.assertEqual([(f.verdict, f.rule) for f in unknown if f.rule.startswith("death.")], [(FLAG, "death.role")])

    def test_bundle_colour(self):
        fam = self.pdf("fam_grey.pdf", [SCANLIKE])
        aff = self.pdf("aff_grey.pdf", [SCANLIKE + " affidavit sworn"])
        rec = self.pdf("rec.pdf", [SCANLIKE + " recommendation"])
        pages = [page(0, SCANLIKE), page(1, SCANLIKE), page(2, SCANLIKE)]
        units = [unit("D01", "family_cert", [0], fam), unit("D02", "affidavit", [1], aff),
                 unit("D03", "recommendation", [2], rec)]
        dc = run(units, pages)
        self.assertEqual([(f.verdict, f.rule) for f in dc.bundle], [(FLAG, "page.colour_bundle")])
        self.assertEqual(dc.bundle[0].pages, (1, 2))
        for uid in ("D01", "D02"):
            self.assertFalse([f for f in dc.per_unit[uid] if f.rule == "page.colour"], uid)
        self.assertTrue([f for f in dc.per_unit["D03"] if f.rule == "page.colour" and f.verdict == PASS])
        # one grey unit inside a colour scan keeps its own row
        aff_c = self.pdf("aff_colour.pdf", [SCANLIKE + " affidavit sworn"], colour=True)
        dc = run([unit("D01", "family_cert", [0], fam), unit("D02", "affidavit", [1], aff_c)], pages)
        self.assertEqual(dc.bundle, [])
        self.assertEqual([f.verdict for f in dc.per_unit["D01"] if f.rule == "page.colour"], [FAIL])
        # folder mode never aggregates
        self.assertEqual(run(units, pages, mode="folder").bundle, [])

    def test_qr_check_defused(self):
        t = SCANLIKE + " for the marketing and meeting office"
        self.assertEqual(PC.qr_check({"qr": [], "qr_seen": False}, text=t), [])        # the bot skips it
        fs = run([unit("D01", "family_cert", [0], self.pdf("fam_mkt.pdf", [t]))], [page(0, t)]).per_unit["D01"]
        self.assertTrue([f for f in fs if f.rule == "page.qr"])

    def test_tin_unit_skips_qr_check(self):
        t = SCANLIKE + " taxpayer identification number certificate"
        u = unit("D01", "trade_license", [0], self.pdf("tin.pdf", [t]), fine="tin_certificate")
        self.assertFalse([f for f in run([u], [page(0, t)]).per_unit["D01"] if f.rule == "page.qr"])

    def test_id_card_cv_never_read(self):
        reads = []
        spy = Proxy(dv, read_document=lambda p, *a: reads.append(p) or ("", []))
        bot = SimpleNamespace(dv=spy, PC=PC, R=R, C=NS.C)
        fs = run([unit("D01", "id_card_cv", [0], self.tmp / "missing.pdf")], [page(0, "q" * 90)],
                 eng=fake_eng(bot=bot)).per_unit["D01"]
        self.assertEqual([(f.verdict, f.rule) for f in fs], [(PRESENT, "doc.present_only")])
        self.assertEqual(reads, [])

    def test_unreadable_and_code_faults(self):
        u = unit("D01", "family_cert", [0], self.tmp / "nothing-here.pdf")
        fs = run([u], [page(0, "q" * 90)]).per_unit["D01"]
        self.assertEqual([(f.verdict, f.rule) for f in fs], [(ERROR, "doc.unreadable")])
        self.assertNotIn(str(self.tmp), fs[0].message)
        broken = Proxy(dv, check_family_cert=lambda *a: (_ for _ in ()).throw(TypeError("a bug")))
        p = self.pdf("fam_ok.pdf", [SCANLIKE])
        with self.assertRaises(TypeError):
            run([unit("D01", "family_cert", [0], p)], [page(0, SCANLIKE)],
                eng=fake_eng(bot=SimpleNamespace(dv=broken, PC=PC, R=R, C=NS.C)))

    def test_present_documents(self):
        p = self.pdf("ps.pdf", [SCANLIKE + " personal statement"])
        fs = run([unit("D01", "personal_statement", [0], p)], [page(0, SCANLIKE)]).per_unit["D01"]
        self.assertIn((PASS, "doc.present"), [(f.verdict, f.rule) for f in fs])
        self.assertTrue([f for f in fs if f.rule == "page.colour"])            # Q10: page checks kept

    def test_dob_and_passport_number_consistency(self):
        ident = blank_identity(dob=dt.date(2004, 5, 6), dob_trusted=True, dob_source="MRZ", passport_no=PPNO,
                               passport_no_ok=True)
        snid = ("National ID card of the student. Date of Birth 06/05/2004. NID No " + NUM10 + " notarised by "
                "advocate")
        aff = f"Affidavit sworn before the notary public. Passport No: {OTHER_PPNO} issued to the deponent, sworn"
        pages = [page(0, snid), page(1, aff)]
        dc = run([unit("D01", "student_nid", [0], self.pdf("snid2.pdf", [snid])),
                  unit("D02", "affidavit", [1], self.pdf("aff2.pdf", [aff]))], pages, ident=ident)
        self.assertEqual([f.verdict for f in dc.per_unit["D01"] if f.rule == "doc.dob_consistency"], [PASS])
        self.assertEqual([f.verdict for f in dc.per_unit["D02"] if f.rule == "doc.passport_no_consistency"], [FLAG])
        other = blank_identity(dob=dt.date(2004, 5, 7), dob_trusted=True, dob_source="MRZ")
        dc = run([unit("D01", "student_nid", [0], self.pdf("snid3.pdf", [snid]))], pages, ident=other)
        self.assertEqual([f.verdict for f in dc.per_unit["D01"] if f.rule == "doc.dob_consistency"], [FLAG])
        untrusted = blank_identity(dob=dt.date(2004, 5, 7), dob_trusted=False)
        dc = run([unit("D01", "student_nid", [0], self.pdf("snid4.pdf", [snid]))], pages, ident=untrusted)
        self.assertFalse([f for f in dc.per_unit["D01"] if f.rule == "doc.dob_consistency"])


class TestPassportRows(unittest.TestCase):
    MRZ_OK = {"passport_no": PPNO, "passport_no_ok": True, "line2_ok": True, "checks": 5}

    def verdicts(self, rows, rule):
        return [f.verdict for f in rows if f.rule == rule]

    def test_validity(self):
        expired = blank_identity(expiry=dt.date(2026, 1, 1), expiry_source="MRZ", expiry_trusted=True)
        self.assertEqual(self.verdicts(D.passport_rows("", expired, TODAY), "passport.validity"), [FAIL])
        six = blank_identity(expiry=dt.date(2027, 4, 30), expiry_source="MRZ re-read", expiry_trusted=True)
        rows = D.passport_rows("", six, TODAY)
        self.assertEqual(self.verdicts(rows, "passport.validity"), [FAIL])
        self.assertIn("6 month(s)", rows[0].message)
        ok = blank_identity(expiry=dt.date(2030, 4, 30), expiry_source="passport label", expiry_trusted=True)
        self.assertEqual(self.verdicts(D.passport_rows("", ok, TODAY), "passport.validity"), [PASS])
        scan6 = blank_identity(expiry=dt.date(2027, 4, 30), expiry_source="scan date", expiry_trusted=False)
        rows = D.passport_rows("", scan6, TODAY)
        self.assertEqual(self.verdicts(rows, "passport.validity"), [FLAG])
        self.assertIn("not the MRZ", rows[0].message)
        scan20 = blank_identity(expiry=dt.date(2028, 8, 30), expiry_source="scan date", expiry_trusted=False)
        self.assertEqual(self.verdicts(D.passport_rows("", scan20, TODAY), "passport.validity"), [PASS])
        none = blank_identity()
        self.assertEqual(self.verdicts(D.passport_rows("", none, TODAY), "passport.validity"), [FLAG])

    def test_number_and_mrz(self):
        same = blank_identity(mrz=dict(self.MRZ_OK), passport_no=PPNO, passport_no_ok=True, printed_passport_no=PPNO,
                              dob_source="MRZ")
        rows = D.passport_rows("", same, TODAY)
        self.assertEqual(self.verdicts(rows, "passport.number"), [PASS])
        self.assertEqual(self.verdicts(rows, "passport.mrz"), [PASS])
        differ = blank_identity(mrz=dict(self.MRZ_OK), passport_no=PPNO, passport_no_ok=True,
                                printed_passport_no=OTHER_PPNO)
        self.assertEqual(self.verdicts(D.passport_rows("", differ, TODAY), "passport.number"), [FLAG])
        bad = blank_identity(mrz=dict(self.MRZ_OK, passport_no_ok=False, line2_ok=False), passport_no=PPNO,
                             passport_no_ok=False)
        rows = D.passport_rows("", bad, TODAY)
        self.assertEqual(self.verdicts(rows, "passport.number"), [FLAG])
        self.assertEqual(self.verdicts(rows, "passport.mrz"), [])
        unread = D.passport_rows("", blank_identity(), TODAY)
        self.assertEqual([f.message for f in unread if f.rule == "passport.number"],
                         ["the MRZ could not be read — check the passport by eye"])

    def test_identity_notes_on_the_passport_unit(self):
        tmp = Tmp.make()
        try:
            t = "Passport of the People's Republic of Bangladesh, data page with the holder's details and the MRZ"
            note = Finding(FLAG, "identity.mrz_truncated", "the MRZ name may be cut", "runner:identity")
            ident = blank_identity(notes=[note])
            dc = run([unit("D01", "passport", [0], make_pdf(tmp / "pp.pdf", [t], colour=True))], [page(0, t)],
                     ident=ident)
            self.assertIn(note, dc.per_unit["D01"])
            self.assertTrue([f for f in dc.per_unit["D01"] if f.rule == "page.colour"])
        finally:
            Tmp.clean()


class TestPhoto(unittest.TestCase):
    """Critique 1: a scanned bundle page is ONE image covering the whole page; the photo is found by its edge."""
    H, W = 1754, 1240          # A4 at 150 dpi
    RECT = (480, 300, 420, 540)   # x, y, w, h (h/w 1.29)

    @classmethod
    def setUpClass(cls):
        cls.tmp = Tmp.make()

    @classmethod
    def tearDownClass(cls):
        Tmp.clean()

    def scan(self, rectangle=True, background=236, edge=True):
        import cv2
        img = np.full((self.H, self.W, 3), 250, np.uint8)
        for k in range(6):                                     # a few printed lines
            cv2.rectangle(img, (120, 120 + 30 * k), (1100, 128 + 30 * k), (40, 40, 40), -1)
        x, y, w, h = self.RECT
        if rectangle:
            cv2.rectangle(img, (x, y), (x + w, y + h), (background,) * 3, -1)
            if edge:
                cv2.rectangle(img, (x, y), (x + w, y + h), (125, 125, 125), 2)
        cv2.ellipse(img, (x + w // 2, y + int(0.6 * h)), (int(0.17 * w), int(0.2 * h)), 0, 0, 360, (45, 40, 40), -1)
        return img

    def test_crop_within_5_percent_and_background_passes(self):
        out = self.tmp / "PASSPORT SIZE PHOTO p02.jpg"
        got = D.crop_photo(None, self.scan(), out, embedded=(0.0, 0.0, 1.0, 1.0))
        self.assertEqual(got, out)
        from PIL import Image
        with Image.open(out) as im:
            w, h = im.size
        _, _, rw, rh = self.RECT
        self.assertLess(abs(w - rw) / rw, 0.05)
        self.assertLess(abs(h - rh) / rh, 0.05)
        u = unit("D01", "photo", [1], out)
        rows = D.photo_rows(u, "bundle", fake_eng(), [page(0, ""), page(1, "")])
        self.assertTrue(rows)
        self.assertFalse([f for f in rows if f.verdict == FAIL], [f.message for f in rows])
        self.assertTrue(all(re.search(r"^\d+x\d+px, ratio|^background is", f.message) for f in rows),
                        [f.message for f in rows])
        self.assertIn(PASS, [f.verdict for f in rows if "ratio" in f.message])

    def test_no_rectangle_flags_edges_and_judges_nothing(self):
        out = self.tmp / "PASSPORT SIZE PHOTO p03.jpg"
        self.assertIsNone(D.crop_photo(None, self.scan(rectangle=False), out, embedded=(0.0, 0.0, 1.0, 1.0)))
        self.assertFalse(out.exists())
        rows = D.photo_rows(unit("D01", "photo", [2], out), "bundle", fake_eng(), [page(i, "") for i in range(3)])
        self.assertEqual([(f.verdict, f.rule) for f in rows], [(FLAG, "photo.edges")])
        self.assertIn("page 3", rows[0].message)

    def test_dark_background_inside_the_edge_fails(self):
        out = self.tmp / "PASSPORT SIZE PHOTO p04.jpg"
        self.assertIsNotNone(D.crop_photo(None, self.scan(background=70, edge=False), out))
        rows = D.photo_rows(unit("D01", "photo", [0], out), "bundle", fake_eng(), [page(0, "")])
        self.assertEqual([f.verdict for f in rows if f.message.startswith("background")], [FAIL])

    def test_whole_image_shortcut_only_when_not_page_sized(self):
        img = np.full((self.H, self.W, 3), 240, np.uint8)            # no edge anywhere
        out = self.tmp / "PASSPORT SIZE PHOTO p05.jpg"
        self.assertIsNone(D.crop_photo(None, img, out, embedded=(0.0, 0.0, 1.0, 1.0)))
        box = (0.1, 0.05, 0.9, 0.95)                                  # 72% of the page, h/w 1.59
        self.assertEqual(D.crop_photo(None, img, out, embedded=box), out)
        from PIL import Image
        with Image.open(out) as im:
            self.assertEqual(im.size, (round(0.9 * self.W) - round(0.1 * self.W),
                                       round(0.95 * self.H) - round(0.05 * self.H)))

    def test_bundle_photo_through_check_documents(self):
        cut = self.tmp / "docs" / "PASSPORT SIZE PHOTO p02.jpg"
        cut.parent.mkdir(parents=True, exist_ok=True)
        from PIL import Image
        Image.fromarray(np.full((40, 30, 3), 128, np.uint8)).save(str(cut), format="JPEG")
        u = unit("D02", "photo", [1], cut)
        dc = run([u], [page(0, "a" * 90), page(1, "")], eng=fake_eng(images={1: self.scan()}))
        self.assertTrue(D.crop_succeeded(u.path))
        rows = dc.per_unit["D02"]
        self.assertTrue(rows and all(f.rule == "bot.check_photo" for f in rows), [f.message for f in rows])
        u2 = unit("D03", "photo", [1], self.tmp / "docs" / "PASSPORT SIZE PHOTO p09.jpg")
        dc = run([u2], [page(0, "a" * 90), page(1, "")], eng=fake_eng(images={1: self.scan(rectangle=False)}))
        self.assertEqual([f.rule for f in dc.per_unit["D03"]], ["photo.edges"])

    def test_folder_photo_keeps_every_row(self):
        p = self.tmp / "PASSPORT SIZE PHOTO.png"
        from PIL import Image
        Image.fromarray(np.full((450, 350, 3), 245, np.uint8)).save(str(p))
        rows = D.photo_rows(unit("D01", "photo", [0], p), "folder", fake_eng(), [page(0, "", file=p.name)])
        self.assertTrue(any("JPG" in f.message and f.verdict == FAIL for f in rows), [f.message for f in rows])


class TestAdapters(unittest.TestCase):
    """Birth certificate and language adapters, with the other tasks' modules faked."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = Tmp.make()

    @classmethod
    def tearDownClass(cls):
        Tmp.clean()

    def test_birth_certificate(self):
        calls = {}
        facts = BirthFacts(pages=(2,), format="bilingual_qr", english_pages=[2], qr_status="decoded",
                           qr_name="SAMPLE ONE")
        birth = SimpleNamespace(
            read_birth_certificate=lambda *a: calls.setdefault("read", a),
            check_birth_certificate=lambda f, u, ident, name, ok, confirm: calls.setdefault(
                "check", (f, u.uid, name, ok)) and [Finding(PASS, "birth.format", "new bilingual print", "runner:birth")])
        names = SimpleNamespace(printed_student_name=lambda u, pages, f: ("SAMPLE ONE", True))
        t = SCANLIKE + " birth registration certificate in bangla and english"
        p = make_pdf(self.tmp / "birth.pdf", [t], colour=True)
        with mock.patch.dict(sys.modules, {PKG + ".birth": birth, PKG + ".names": names}):
            dc = run([unit("D01", "birth_cert", [2], p)], [page(0, "b" * 90), page(1, "b" * 90), page(2, t)],
                     births={(2,): facts})
        self.assertNotIn("read", calls)
        self.assertEqual(calls["check"], (facts, "D01", "SAMPLE ONE", True))
        fs = dc.per_unit["D01"]
        self.assertIn("birth.format", [f.rule for f in fs])
        self.assertFalse([f for f in fs if f.rule in ("page.qr", "page.bangla")])
        self.assertTrue([f for f in fs if f.rule == "page.colour"])
        self.assertEqual(dc.machine, [MachineRead(unit="D01", person="student", name="SAMPLE ONE", source="birth QR",
                                                  page=2)])

    def test_language(self):
        seen = []
        names = SimpleNamespace(covered=lambda aff, a, b: seen.append((a, b)) or False)
        identity = SimpleNamespace(passport_for_language=lambda ident: {
            "surname": "SAMPLE", "given_names": "ONE", "passport_no": "", "dob": None, "name_verified": True,
            "name_trusted": True})
        trf = ("IELTS Test Report Form ACADEMIC Centre Number BD001 Date 12/SEP/2025 Candidate Number 123456 "
               "Family Name SAMPLE First Name(s) TWO Candidate ID X Date of Birth 06/05/2004 Test Results")
        boxes = [([[0, 0], [600, 0], [600, 10], [0, 10]], trf, 0.9)]
        p = make_pdf(self.tmp / "ielts.pdf", [trf])
        ident = blank_identity(student=NameRef(parts=("SAMPLE", "ONE"), written="SAMPLE ONE"))
        with mock.patch.dict(sys.modules, {PKG + ".names": names, PKG + ".identity": identity}):
            fs = run([unit("D01", "language_cert", [0], p)], [page(0, trf)], ident=ident,
                     eng=fake_eng(images={0: np.zeros((4, 4, 3), np.uint8)}, boxes=boxes)).per_unit["D01"]
            self.assertIn(("SAMPLE ONE", "SAMPLE TWO"), seen)                  # critique 14
            self.assertEqual([f.verdict for f in fs if f.rule == "language.name"], [FLAG])
            self.assertTrue(all(f.message.startswith("IELTS: ") for f in fs if f.rule.startswith("language.")))
            duo = "Duolingo English Test Overall 120 Test Date: Sep 12, 2025 certs.duolingo.com/" + "01234" + "56789abcdef"
            q = make_pdf(self.tmp / "duo.pdf", [duo])
            fs = run([unit("D02", "language_cert", [0], q)], [page(0, duo)], ident=ident).per_unit["D02"]
            self.assertEqual([(f.verdict, f.rule) for f in fs if not f.rule.startswith("page.")],
                             [(PRESENT, "doc.present_only")])


class TestOrder(unittest.TestCase):
    """The bot's order: the passport text exists before the NIDs; income tax has a place for every program."""

    def test_order(self):
        for program in ("KLP", "EAP", "BACHELOR", "MASTER"):
            order, seen_passport = [], []

            def read(p, *a):
                order.append(Path(p).stem)
                return (Path(p).stem + " text " + "w" * 90, [(595, 842)])

            def nid(text, view, sizes, whose, path, others):
                seen_passport.append("passport" in others)
                return []
            spy = Proxy(dv, read_document=read, check_nid=nid, check_income_tax=lambda *a: [],
                        check_family_cert=lambda *a: [], check_bank=lambda *a: [])
            pc = Proxy(PC, inspect=lambda p: {"saturation": 5.0, "qr": [], "qr_seen": False, "pages": 1,
                                              "digital": False})
            bot = SimpleNamespace(dv=spy, PC=pc, R=R, C=NS.C)
            units = [unit("D01", "income_tax", [0], "income_tax.pdf"), unit("D02", "mother_nid", [1], "mother.pdf"),
                     unit("D03", "father_nid", [2], "father.pdf"), unit("D04", "bank", [3], "bank.pdf"),
                     unit("D05", "passport", [4], "passport.pdf"), unit("D06", "unrecognised", [5], "x.pdf"),
                     unit("D07", "family_cert", [6], "family.pdf")]
            pages = [page(i, "p" * 90) for i in range(7)]
            dc = run(units, pages, program=program, eng=fake_eng(bot=bot))
            self.assertEqual(order[0], "passport", program)
            self.assertLess(order.index("passport"), order.index("father"))
            self.assertLess(order.index("father"), order.index("mother"))
            self.assertIn("income_tax", order, program)
            self.assertEqual(order[-1] if program != "MASTER" else order[order.index("bank") + 1], "income_tax",
                             program)
            self.assertNotIn("x", order)
            self.assertEqual(seen_passport, [True, True])
            self.assertEqual(dc.per_unit["D06"], [])
            self.assertEqual(set(dc.per_unit), {u.uid for u in units})


if __name__ == "__main__":
    unittest.main()
