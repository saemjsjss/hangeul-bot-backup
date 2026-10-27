"""The second-pass fixes (27 Oct, after the checkers re-verified the 33 review findings): one test per item that was
still open or newly broken. Made-up names, numbers and dates only. The safety items (S1, S2, S4, S5, S6, S8) are
tested in test_safety.ReviewTwoGuard (armed guards run in child processes)."""
from __future__ import annotations

import datetime as dt
import shutil
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from src.verify.runner import botimport as B
from src.verify.runner import model as M
from src.verify.runner.model import (FAIL, FLAG, NOT_JUDGED, NOTE, PASS, AffidavitInfo, BirthFacts, DocResult, DocUnit,
                                     Finding, Identity, NameRef, PageRead, SplitResult)
from src.verify.runner.tests.test_names import (Case, FakeConfirm, FakeEngine, academic, base_case, box, ident_of,
                                                 rules, run)

WORK = B.PRIVATE / "tmp" / "test_review_fixes2"
STUDENT = dict(student_written="EXAMPLE SAMPLE", student_parts=("SAMPLE", "EXAMPLE"))


def setUpModule():
    B.load(B._loaded.cache_root if B._loaded is not None else B.DEFAULT_CACHE_ROOT)


def N():
    from src.verify.runner import names
    return names


def P(i: int, file: str, file_page: int, text: str = "") -> PageRead:
    return PageRead(i, file, file_page, text, "ocr", len(text), False, 0.0, False, "cpu")


# ---------------------------------------------------------------------------------- new defect 1 (names)
class TestPrintedWordsAfterTheName(unittest.TestCase):
    """A correct document whose label value runs on into printed non-name words: never a names FAIL, even when the
    zoomed re-read or a second page confirms the printed words."""

    TEXTS = [
        "UNIVERSITY OF SAMPLE This is to certify that EXAMPLE SAMPLE successfully completed the Bachelor programme",
        "UNIVERSITY OF SAMPLE This is to certify that EXAMPLE SAMPLE completed all requirements for the degree",
        "BOARD OF SAMPLE This is to certify that EXAMPLE SAMPLE appeared at the examination held in",
        "UNIVERSITY OF SAMPLE Academic Transcript Name of Student: EXAMPLE SAMPLE Program: BSC Roll 1",
        "UNIVERSITY OF SAMPLE Name of Student EXAMPLE SAMPLE Programme Bachelor of Science Roll 1",
        "UNIVERSITY OF SAMPLE Name of Student: EXAMPLE SAMPLE Department: PHYSICS CGPA: 3.50",
        "UNIVERSITY OF SAMPLE Name of Student EXAMPLE SAMPLE Course Semester Batch Major Degree",
    ]

    def test_no_fail_with_a_confirming_reread_or_two_pages(self):
        for t in self.TEXTS:
            for n, ans in ((1, "confirmed"), (2, "unclear"), (2, "confirmed")):
                with self.subTest(text=t[:60], pages=n, reread=ans):
                    c = Case()
                    c.add("passport", ["PASSPORT Surname SAMPLE"], "PASSPORT")
                    u = c.add("academic", [t] * n, "ACADEMIC")
                    res = run(c, ident_of(**STUDENT), FakeConfirm(ans))
                    self.assertEqual(rules(res, u.uid), [])
                    self.assertEqual(res.per_person["student"], PASS)

    def test_the_real_confirmer_on_a_certify_sentence(self):
        words = ["This", "is", "to", "certify", "that", "EXAMPLE", "SAMPLE", "successfully", "completed", "the"]
        page_boxes, x = [], 10
        for w in words:
            page_boxes.append((box(x, 100, x + 12 * len(w), 130), w, 0.96))
            x += 12 * len(w) + 10
        crop_boxes = [(box(10, 10, 100, 40), "EXAMPLE", 0.95), (box(110, 10, 200, 40), "SAMPLE", 0.95),
                      (box(210, 10, 360, 40), "successfully", 0.93), (box(370, 10, 480, 40), "completed", 0.94)]
        c = Case()
        c.add("passport", ["PASSPORT Surname SAMPLE"], "PASSPORT")
        u = c.add("academic", ["UNIVERSITY OF SAMPLE " + " ".join(words) + " Bachelor programme"], "ACADEMIC")
        conf = N().make_confirmer(FakeEngine(page_boxes, crop_boxes), c.pages, WORK)
        res = N().evaluate(ident_of(**STUDENT), c.units, c.pages, AffidavitInfo(), [], {}, conf)
        self.assertEqual(rules(res, u.uid), [])
        self.assertEqual(res.per_person["student"], PASS)

    def test_a_title_case_field_word_after_a_capitalised_card_name_ends_it(self):
        tk = N()._tokens("Name EXAMPLE SAMPLE Zebrafy Date of Birth")
        self.assertEqual(N().read_value(tk, 1)[0], ["EXAMPLE", "SAMPLE"])
        tk = N()._tokens("Name: Example Sample successfully")
        self.assertEqual(N().read_value(tk, 1)[0], ["Example", "Sample"])
        tk = N()._tokens("Name: MD. EXAMPLE SAMPLE BEGUMX Roll")        # a capitalised word stays in the value
        self.assertEqual(N().read_value(tk, 1)[0], ["MD.", "EXAMPLE", "SAMPLE", "BEGUMX"])
        tk = N()._tokens("Name: Md. EXAMPLE SAMPLE")                    # an honorific carries no case style
        self.assertEqual(N().read_value(tk, 1)[0], ["Md.", "EXAMPLE", "SAMPLE"])

    def test_a_real_added_word_is_still_a_difference(self):
        c = base_case()
        u = c.add("academic", [academic(mother="DEMO SAMPLE BEGUMX")] * 2, "ACADEMIC")
        self.assertEqual(rules(run(c, ident_of(), FakeConfirm("confirmed")), u.uid), [("names.mother", FAIL)])

    def test_family_table_with_occupations(self):
        t = ("FAMILY CERTIFICATE Office of the Ward Councillor This is to certify that the following persons are "
             "members of one family. SL Name Occupation Relation 1 TESTER SAMPLE Farmer Father 2 DEMO SAMPLE "
             "Housewife Mother 3 MD. EXAMPLE SAMPLE Unemployed Self Ward Councillor")
        for n, ans in ((1, "unclear"), (1, "confirmed"), (2, "unclear")):
            with self.subTest(pages=n, reread=ans):
                c = base_case()
                u = c.add("family_cert", [t] * n, "FAMILY CERTIFICATE")
                res = run(c, ident_of(), FakeConfirm(ans))
                self.assertEqual(rules(res, u.uid), [("names.family_first_row", PASS)])
                self.assertEqual(res.per_person, {"student": PASS, "father": PASS, "mother": PASS})


# ---------------------------------------------------------------------------------- new defect 2 (bank holder)
class TestBankHolderDescriptor(unittest.TestCase):
    H = "TO WHOM IT MAY CONCERN Date: 15-09-2026 This is to certify that "
    TAIL = (" has been maintaining a Savings Account with our branch. Balance BDT 25,00,000.00 equivalent to "
            "USD 20,000.00. Authorized Signature")

    def ident(self):
        return Identity(student=NameRef(parts=("HOTEL", "GOLF"), written="GOLF HOTEL", source="passport MRZ",
                                        confirmed=True),
                        father=NameRef(parts=("ALPHA", "BRAVO"), written="ALPHA BRAVO", source="birth certificate",
                                       confirmed=True),
                        mother=NameRef(parts=("ECHO", "FOXTROT"), written="ECHO FOXTROT", source="birth certificate",
                                       confirmed=True))

    def test_a_descriptor_after_the_holder_never_fails(self):
        from src.verify.runner import bank as Bk
        u = DocUnit("D09", "bank", "bank_solvency", "BANK", [10])
        for holder in ("Alpha Bravo Freedom Fighter", "Alpha Bravo Retired Govt. employee", "ALPHA BRAVO Freedom Fighter"):
            with self.subTest(holder=holder):
                o = Bk.check_bank_unit(u, [self.H + holder + self.TAIL], [10], self.ident(), lambda a, b: False,
                                       lambda i, v: "confirmed", lambda i: None)
                hp = [f for f in o.findings if f.rule == "bank.holder_parent"]
                self.assertEqual([f.verdict for f in hp], [PASS])

    def test_a_non_name_word_extending_the_reference_is_flag(self):
        from src.verify.runner import bank as Bk
        idn = self.ident()
        for n in ("ALPHA BRAVO Zebrafy", "ALPHA BRAVO FIGHTER", "Alpha Bravo Program:"):
            with self.subTest(n=n):
                v = Bk._judge_name(n, 10, idn, Bk._parent_refs(idn), lambda a, b: False, lambda i, x: "confirmed", [],
                                   False, False)
                self.assertEqual(v.verdict, FLAG)
        v = Bk._judge_name("ALPHA BRAVO KILOX", 10, idn, Bk._parent_refs(idn), lambda a, b: False,
                           lambda i, x: "confirmed", [], False, False)
        self.assertEqual(v.verdict, FAIL)                        # a capitalised extra word: still a difference


# ---------------------------------------------------------------------------------- O3 (the birth certificate tail)
class TestBirthCertificateTail(unittest.TestCase):
    BC = ("Birth Registration Certificate Name EXAMPLE SAMPLE Father's Name TESTER SAMPLE KHANO Nationality "
          "Bangladeshi Mother's Name DEMO SAMPLE Nationality Bangladeshi")

    def _case(self, bc_pages):
        c = Case()
        c.add("passport", ["PASSPORT Surname SAMPLE Given Names EXAMPLE"], "PASSPORT")
        bc = c.add("birth_cert", [self.BC] * bc_pages, "BIRTH CERTIFICATE")
        ac = c.add("academic", [academic(student="EXAMPLE SAMPLE", father="TESTER SAMPLE", mother="DEMO SAMPLE")] * 2,
                   "ACADEMIC HSC")
        births = {tuple(bc.pages): BirthFacts(pages=tuple(bc.pages), english_pages=list(bc.pages))}
        return c, bc, ac, births

    def test_two_birth_certificate_pages_confirm_the_tail(self):
        from src.verify.runner import identity as I
        c, bc, ac, births = self._case(2)
        ident = I.build_identity(c.units, c.pages, births, None, WORK, FakeConfirm("unclear"))
        self.assertEqual((ident.father.written, ident.father.tail), ("TESTER SAMPLE KHANO", ()))
        res = N().evaluate(ident, c.units, c.pages, AffidavitInfo(), [], births, FakeConfirm("unclear"))
        self.assertEqual(rules(res, ac.uid), [("names.father", FAIL)])     # two pages each side: confirmed

    def test_an_unconfirmed_tail_is_check_by_eye_never_a_silent_pass(self):
        from src.verify.runner import identity as I
        c, bc, ac, births = self._case(1)
        for ans in ("unclear", "refuted"):
            with self.subTest(reread=ans):
                ident = I.build_identity(c.units, c.pages, births, None, WORK, FakeConfirm(ans))
                self.assertEqual((ident.father.written, ident.father.tail), ("TESTER SAMPLE", ("KHANO",)))
                res = N().evaluate(ident, c.units, c.pages, AffidavitInfo(), [], births, FakeConfirm(ans))
                self.assertEqual(rules(res, ac.uid), [])
                self.assertEqual(rules(res, bc.uid), [("names.father_tail", FLAG)])
                self.assertEqual(res.per_person["father"], FLAG)
                line = next(f for f in res.summary if f.rule == "names.father")
                self.assertEqual(line.verdict, FLAG)
                self.assertIn("KHANO", line.message)
                self.assertEqual(res.per_person["mother"], PASS)

    def test_another_word_where_the_unconfirmed_tail_stands_is_check_by_eye(self):
        """The certificate reads "TESTER SAMPLE KHANO" once (KHANO unconfirmed); a document printing "TESTER SAMPLE
        ZULUX" on two pages differs from it only through that unconfirmed read: FLAG, never FAIL. More added words
        than the tail holds stay a difference (FAIL)."""
        from src.verify.runner import identity as I
        for father, want in (("TESTER SAMPLE ZULUX", FLAG), ("TESTER SAMPLE ZULUX YANKEX", FAIL),
                             ("MD TESTER SAMPLE", FAIL)):
            with self.subTest(father=father):
                c = Case()
                c.add("passport", ["PASSPORT Surname SAMPLE Given Names EXAMPLE"], "PASSPORT")
                bc = c.add("birth_cert", [self.BC], "BIRTH CERTIFICATE")
                c.add("academic", [academic(student="EXAMPLE SAMPLE", father="TESTER SAMPLE", mother="DEMO SAMPLE")]
                      * 2, "ACADEMIC SSC")
                ac = c.add("academic", [academic(student="EXAMPLE SAMPLE", father=father, mother="DEMO SAMPLE")] * 2,
                           "ACADEMIC HSC")
                births = {tuple(bc.pages): BirthFacts(pages=tuple(bc.pages), english_pages=list(bc.pages))}
                ident = I.build_identity(c.units, c.pages, births, None, WORK, FakeConfirm("unclear"))
                self.assertEqual(ident.father.tail, ("KHANO",))
                res = N().evaluate(ident, c.units, c.pages, AffidavitInfo(), [], births, FakeConfirm("unclear"))
                self.assertEqual(rules(res, ac.uid), [("names.father", want)])
                if father.endswith("ZULUX"):
                    f = next(f for f in res.per_unit[ac.uid] if f.rule == "names.father")
                    self.assertIn("adds ZULUX where the birth certificate's read goes on with KHANO", f.message)

    def test_a_holder_adding_another_word_at_the_tail_is_flag(self):
        from src.verify.runner import bank as Bk
        idn = Identity(student=NameRef(parts=("HOTEL", "GOLF"), written="GOLF HOTEL", source="passport MRZ",
                                       confirmed=True),
                       father=NameRef(parts=("ALPHA", "BRAVO"), written="ALPHA BRAVO", source="birth certificate",
                                      confirmed=True, tail=("KILO",)),
                       mother=NameRef(parts=("ECHO", "FOXTROT"), written="ECHO FOXTROT", source="birth certificate",
                                      confirmed=True))
        for n, want in (("ALPHA BRAVO ZULU", FLAG), ("ALPHA BRAVO KILO", FLAG), ("ALPHA BRAVO ZULU YANKEE", FAIL)):
            with self.subTest(n=n):
                v = Bk._judge_name(n, 10, idn, Bk._parent_refs(idn), lambda a, b: False, lambda i, x: "confirmed", [],
                                   False, False)
                self.assertEqual(v.verdict, want)
        v = Bk._judge_name("ALPHA BRAVO ZULU", 10, idn, Bk._parent_refs(idn), lambda a, b: False,
                           lambda i, x: "confirmed", [], False, False)
        self.assertIn("adds ZULU where the birth certificate's read goes on with KILO", v.message)


# ---------------------------------------------------------------------------------- C7 (headline counts)
class TestHeadlineOnNotJudgedDocuments(unittest.TestCase):
    def test_a_chain_fail_on_a_not_judged_unit_is_counted(self):
        from src.verify.runner import verdict as V
        res = SplitResult(units=[], notices=[], missing=[], stop=False, maybe={})
        tin = DocUnit("D05", "trade_license", "tin_certificate", "TIN CERTIFICATE", [4])
        f = Finding(FAIL, "financial.taxpayer_holder", "the taxpayer is not the holder", "runner:financial", (5,))
        d = DocResult(unit=tin, findings=[f], verdict=NOT_JUDGED, reasons=["fix page 5, then check again"])
        result, head = V.overall([d], [], res, [f], [])
        self.assertEqual(result, FAIL)
        self.assertTrue(head.startswith("FAIL — 1 FAIL, 0 FLAG, 0 MISSING"), head)
        d2 = DocResult(unit=tin, findings=[f], verdict=FAIL, reasons=[f.message])     # judged: counted once
        self.assertTrue(V.overall([d2], [], res, [f], [])[1].startswith("FAIL — 1 FAIL"))


# ---------------------------------------------------------------------------------- C2 (passport expiry label)
class TestPassportExpiryRow(unittest.TestCase):
    def expiry(self, text):
        from src.verify.runner import identity as I
        return I.expiry_from_label(text, B._loaded.dv.find_dates)

    def test_label_rows(self):
        e34 = dt.date(2034, 2, 11)
        cases = {
            "Date of Birth Date of Issue Date of Expiry / 01 JAN 2000 12 FEB 2024 11 FEB 2034": (e34, True),
            "Date of Issue Issuing Authority Date of Expiry 12 FEB 2024 DIP/SAMPLE 11 FEB 2034": (e34, True),
            "Date of Expiry Date of Issue 11 FEB 2034 12 FEB 2024": (e34, True),
            "Date of Birth 01 JAN 2000 Date of Expiry 11 FEB 2034 Date of Issue 12 FEB 2024": (e34, True),
            "Date of Expiry 11 FEB 2034 Issuing Authority DIP SAMPLE Sample Office Sample Road Scan 05/03/2025": (e34, True),
            "Date of Issue Date of" + chr(10) + "Expiry 12 FEB 2024 11 FEB 2034": (e34, True),
            "Date of Expiry Date of Issue 11 FEB" + chr(10) + "2034 12 FEB 2024": (e34, True),   # a wrapped date
        }
        for text, (want, trusted) in cases.items():
            with self.subTest(text=text):
                got = self.expiry(text)
                self.assertEqual((got[0], got[2]), (want, trusted))
        got = self.expiry("PASSPORT Date of Issue Date of Expiry 12 FEB 2024")       # one date for two labels
        self.assertFalse(got[2])
        self.assertIsNone(self.expiry("PASSPORT no expiry label 11 FEB 2034"))

    def test_identity_uses_it(self):
        from src.verify.runner.tests import test_identity as TI
        c = TI.Case()
        c.add("passport", ["PEOPLE'S REPUBLIC PASSPORT Surname SAMPLE Given Name(s) EXAMPLE Date of Birth Date of "
                           "Issue Date of Expiry 01 JAN 2000 12 FEB 2024 11 FEB 2034"])
        ident = TI.build(c)
        self.assertEqual((ident.expiry, ident.expiry_trusted), (dt.date(2034, 2, 11), True))


# ---------------------------------------------------------------------------------- C1 (pages of two files)
class TestCrossFilePages(unittest.TestCase):
    def setUp(self):
        self.pages = [P(0, "BANK SOLVENCY.pdf", 0), P(1, "BANK SOLVENCY.pdf", 1), P(2, "TIN CERTIFICATE.pdf", 0)]
        M.set_page_numbering(self.pages)

    def tearDown(self):
        M.set_page_numbering(None)

    def test_the_chain_names_the_other_files_page(self):
        from src.verify.runner import financial as F
        from src.verify.runner import report as RP
        tin = DocUnit("D02", "trade_license", "tin_certificate", "TIN CERTIFICATE", [2],
                      source_file="TIN CERTIFICATE.pdf")
        bank = DocUnit("D01", "bank", "bank_solvency", "BANK SOLVENCY", [0, 1], source_file="BANK SOLVENCY.pdf")
        holder = SimpleNamespace(name="OTHERX PERSONX", page=1, ocr_suspect=False)
        tp = {"name": "TESTER SAMPLE", "source": "PRINTED", "printed": "TESTER SAMPLE", "printed_page": 2}
        out = F.chain_findings(tp, tin, [], {}, holder, bank, ident_of(), lambda a, b: False, FakeConfirm("unclear"))
        f = next(f for f in out["D02"] if f.rule == "financial.taxpayer_holder")
        self.assertEqual(f.verdict, FLAG)
        self.assertIn("check by eye, page 2 of BANK SOLVENCY.pdf", f.message)
        self.assertEqual(f.evidence["run_pages"], [1, 2])
        r = SimpleNamespace(mode="folder", pages=self.pages)
        self.assertEqual(RP._pages_of_finding(f, tin, r), "BANK SOLVENCY.pdf p2; TIN CERTIFICATE.pdf p1")
        self.assertEqual(RP._finding_run_pages(f, tin, r), [1, 2])
        self.assertEqual(M.page_in(2, [2]), "page 1")
        self.assertEqual(RP.file_pages_label([2], r), "TIN CERTIFICATE.pdf p1")
        self.assertEqual(RP.file_pages_label([0, 2], SimpleNamespace(mode="bundle", pages=self.pages)), "1,3")
        # the FINANCIAL CHAIN cross rows: with run pages, or the file of the document line that carries the finding
        po = next(f2 for f2 in out["D02"] if f2.rule == "financial.taxpayer_print_only")
        r.docs = [DocResult(unit=tin, findings=list(out["D02"]), verdict=FLAG, reasons=[])]
        self.assertEqual(RP.cross_pages(f, r), "BANK SOLVENCY.pdf p2; TIN CERTIFICATE.pdf p1")
        self.assertEqual(RP.cross_pages(po, r), "TIN CERTIFICATE.pdf p1")

    def test_the_financial_chain_line_names_the_file_of_a_cited_page(self):
        """Folder mode: the FINANCIAL CHAIN summary line belongs to no file, so a message citing a page of its
        document line ("check by eye, page 2") gets that file's name; a message naming a file already, or citing no
        page, is kept; bundle mode is unchanged."""
        from src.verify.runner import report as RP
        bank = DocUnit("D01", "bank", "bank_solvency", "BANK SOLVENCY", [0, 1], source_file="BANK SOLVENCY.pdf")
        hp = Finding(FLAG, "bank.holder_parent", "account holder OTHERX PERSONX could not be compared — check by eye, "
                     "page 2", "runner:bank", (2,))
        named = Finding(FLAG, "financial.taxpayer_holder", "not written the same — check by eye, page 2 of BANK "
                        "SOLVENCY.pdf", "runner:financial", (2,))
        nopage = Finding(FLAG, "bank.usd", "no USD equivalent found", "runner:bank", ())
        r = SimpleNamespace(mode="folder", pages=self.pages,
                            docs=[DocResult(unit=bank, findings=[hp, named, nopage], verdict=FLAG, reasons=[])])
        self.assertEqual(RP._naming_its_file(hp, r).message, hp.message + " (BANK SOLVENCY.pdf)")
        self.assertIs(RP._naming_its_file(named, r), named)
        self.assertIs(RP._naming_its_file(nopage, r), nopage)
        lines = [t for _, t in RP.summary_lines(SimpleNamespace(
            overall=FLAG, case_id="F-0", program="KLP", mode="folder", pages=self.pages, headline="FLAG — 1 FLAG",
            reading={}, notices=[], docs=r.docs, missing_lines=[], cross=[hp, named], types=[]))]
        chain = next(t for t in lines if t.startswith("FINANCIAL CHAIN:"))
        self.assertIn("page 2 (BANK SOLVENCY.pdf)", chain)
        self.assertNotIn("SOLVENCY.pdf (BANK", chain)


# ---------------------------------------------------------------------------------- separate bank files (folder)
class TestSplitBankFiles(unittest.TestCase):
    SOLV = ("TO WHOM IT MAY CONCERN Date: 15-09-2026 This is to certify that Alpha Bravo, S/O Kilo Lima, has been "
            "maintaining a Savings Account with our branch. Balance BDT 25,00,000.00 equivalent to USD 20,000.00.")
    STMT = ("STATEMENT OF ACCOUNT Account Name ALPHA BRAVO Account No 1234 Account Type Saving Print Date 15-09-2026 "
            "Date Narration Debit Credit Balance 01-09-2026 Opening Balance 2,500,000.00")
    ONE = "the bank file has only one page, so it cannot hold both a solvency certificate and a statement"

    def test_one_page_rows_and_the_date_match_across_files(self):
        from src.verify.runner import bank as Bk
        from src.verify.runner import pipeline as PL
        pages = [P(0, "BANK SOLVENCY.pdf", 0, self.SOLV), P(1, "BANK STATEMENT.pdf", 0, self.STMT)]
        units = [DocUnit("D01", "bank", "bank", "BANK SOLVENCY", [0], source_file="BANK SOLVENCY.pdf"),
                 DocUnit("D02", "bank", "bank", "BANK STATEMENT", [1], source_file="BANK STATEMENT.pdf")]
        bank_out = {"D01": SimpleNamespace(solvency_pages=[0]), "D02": SimpleNamespace(solvency_pages=[])}
        bank_map = {"D01": Bk.dates_rows([self.SOLV], [0]), "D02": []}
        self.assertEqual([f.verdict for f in bank_map["D01"]], [NOTE])          # alone: no statement to compare
        per_unit = {u.uid: [Finding(FLAG, "bot.check_bank", self.ONE, "bot:check_bank", (1,))] for u in units}
        PL._split_bank_files(SimpleNamespace(bank=Bk), units, pages, bank_out, bank_map, per_unit)
        for u in units:
            self.assertEqual([f.verdict for f in per_unit[u.uid]], [NOTE])
            self.assertIn("separate bank files (BANK SOLVENCY.pdf, BANK STATEMENT.pdf)", per_unit[u.uid][0].message)
        dates = [f for f in bank_map["D01"] if f.rule == "bank.dates"]
        self.assertEqual([f.verdict for f in dates], [PASS])
        self.assertEqual(dates[0].evidence["run_pages"], [0, 1])
        self.assertEqual(bank_map["D02"], [])
        two_certs = {u.uid: [Finding(FLAG, "bot.check_bank", self.ONE, "bot:check_bank", (1,))] for u in units}
        pages2 = [P(0, "A.pdf", 0, self.SOLV), P(1, "B.pdf", 0, self.SOLV)]   # no statement anywhere: kept
        PL._split_bank_files(SimpleNamespace(bank=Bk), units, pages2, bank_out, {"D01": [], "D02": []}, two_certs)
        self.assertEqual([f.verdict for u in units for f in two_certs[u.uid]], [FLAG, FLAG])


# ---------------------------------------------------------------------------------- S3 (no file name on stderr)
class TestErrorLinesNameNoFile(unittest.TestCase):
    def setUp(self):
        WORK.mkdir(parents=True, exist_ok=True)
        self.d = Path(tempfile.mkdtemp(prefix="s3_", dir=WORK))

    def tearDown(self):
        shutil.rmtree(self.d, ignore_errors=True)

    def test_input_errors(self):
        from src.verify.runner import ingest
        made_up = "ZZMADEUP PERSONNAME"
        folder = self.d / "infolder"
        folder.mkdir()
        (folder / f"{made_up} passport.pdf").write_bytes(b"not a pdf, made-up bytes")
        bundle = self.d / f"{made_up} bundle.pdf"
        bundle.write_bytes(b"not a pdf, made-up bytes")
        for inp, where in ((folder, "file 1 of 1 in the folder"), (bundle, "the bundle")):
            with self.subTest(where=where):
                with self.assertRaises(ingest.InputError) as cm:
                    ingest.open_input(inp)
                self.assertNotIn("PERSONNAME", str(cm.exception))
                self.assertIn(where, str(cm.exception))
        with self.assertRaises(ingest.InputError) as cm:
            ingest.open_input(Path(made_up + ".pdf"))                          # relative: never echoed
        self.assertNotIn("PERSONNAME", str(cm.exception))

    def test_safety_messages_never_echo_a_relative_path(self):
        """redact() removes drive and UNC paths only, so a relative path must never be put into a message."""
        from src.verify.runner import safety
        rel = Path("ZZMADEUP PERSONNAME bundle.pdf")
        out = B.PRIVATE / "runs" / "CASE_X"
        for call in (lambda: safety.check_out_dir(out, rel), lambda: safety.check_out_dir(rel, self.d / "x.pdf"),
                     lambda: safety.check_input(rel), lambda: safety.assert_writable(rel)):
            with self.assertRaises(safety.SafetyError) as cm:
                call()
            self.assertNotIn("PERSONNAME", safety.redact(cm.exception))


if __name__ == "__main__":
    unittest.main()
