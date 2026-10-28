"""Task 3 tests: names.py (RUNNER_SPEC 5.9.1, section 7; owner decisions A1, A2, A6 b and e).

Made-up names only ("SAMPLE", "EXAMPLE", "TESTER", "DEMO"); "MD" / "MST" are the abbreviations the owner's
rules talk about. The long honorific is built from character codes so that no name-like literal sits in
the source. Fake confirmers and a fake engine stand in for the OCR re-read.

    <runner venv python> -I -B -m unittest discover -s <worktree>\\src\\verify\\runner\\tests -t <worktree> -p "test_names*.py"
"""
from __future__ import annotations

import shutil
import unittest
from pathlib import Path

from src.verify.runner import botimport as B
from src.verify.runner import names as N
from src.verify.runner import strict as S
from src.verify.runner.model import (FAIL, FLAG, NOTE, PASS, AffidavitInfo, BirthFacts, DocUnit, Identity,
                                     MachineRead, NameRef, PageRead)

LONG_MD = "".join(chr(c) for c in (77, 79, 72, 65, 77, 77, 65, 68))   # the long form of MD
WORK = B.PRIVATE / "tmp" / "test_names"


def setUpModule():
    B.load(B._loaded.cache_root if B._loaded is not None else B.DEFAULT_CACHE_ROOT)


def page(i: int, text: str, file: str = "BUNDLE.pdf", file_page: int = -1) -> PageRead:
    return PageRead(i, file, i if file_page < 0 else file_page, text, "ocr", len(text), False, 0.0, False, "cpu")


class Case:
    """A bundle of made-up pages and units; unit texts are given per page."""

    def __init__(self):
        self.pages = []
        self.units = []

    def add(self, key: str, texts, title: str = "", role: str = "", role_how: str = "") -> DocUnit:
        idx = []
        for t in texts:
            self.pages.append(page(len(self.pages), t))
            idx.append(len(self.pages) - 1)
        u = DocUnit(uid=f"D{len(self.units) + 1:02d}", key=key, fine=key, title=title or key.upper(), pages=idx,
                    role=role, role_how=role_how)
        self.units.append(u)
        return u


def ident_of(student_written="MD. EXAMPLE SAMPLE", student_parts=("SAMPLE", "MD", "EXAMPLE"),
             father="TESTER SAMPLE", mother="DEMO SAMPLE", unit="D01", truncated=False,
             student_confirmed=True) -> Identity:
    return Identity(
        student=NameRef(parts=tuple(student_parts), written=student_written, source="passport MRZ", unit=unit,
                        pages=(0,), confirmed=student_confirmed, truncated=truncated),
        father=NameRef(parts=S.words(father), written=father, source="birth certificate", unit="DBC",
                       pages=(), confirmed=True),
        mother=NameRef(parts=S.words(mother), written=mother, source="birth certificate", unit="DBC",
                       pages=(), confirmed=True))


def academic(student="MD. EXAMPLE SAMPLE", father="TESTER SAMPLE", mother="DEMO SAMPLE") -> str:
    return (f"BOARD OF EXAMINATIONS Transcript Name of Student {student} Father's Name {father} "
            f"Mother's Name {mother} Roll 123456 Result 5.00")


class FakeConfirm:
    """confirm(page, variant) -> a fixed answer; records the calls."""

    def __init__(self, answer="unclear", printed=""):
        self.answer, self._printed, self.calls, self.log = answer, printed, [], []

    def __call__(self, page_index, variant):
        self.calls.append((page_index, variant))
        return self.answer(page_index, variant) if callable(self.answer) else self.answer

    def printed(self, page_index, variant):
        return self._printed


def run(case: Case, ident: Identity, confirm=None, aff=None, machine=(), extra=(), births=None):
    return N.evaluate(ident, case.units, case.pages, aff or AffidavitInfo(), list(machine), births or {},
                      confirm or FakeConfirm(), extra)


def rules(res, uid):
    return sorted((f.rule, f.verdict) for f in res.per_unit.get(uid, []))


def base_case() -> Case:
    c = Case()
    c.add("passport", ["PASSPORT Surname SAMPLE Given Names MD. EXAMPLE"], "PASSPORT")
    return c


class TestExtraction(unittest.TestCase):
    def test_dot_marks(self):
        self.assertEqual(N._tokens("Name: MD:EXAMPLE SAMPLE"), ["Name:", "MD.EXAMPLE", "SAMPLE"])
        self.assertEqual(N._tokens("Name A_ EXAMPLE SAMPLE"), ["Name", "A.", "EXAMPLE", "SAMPLE"])
        self.assertEqual(N._tokens("Name; MD, EXAMPLE SAMPLE"), ["Name;", "MD.", "EXAMPLE", "SAMPLE"])
        self.assertEqual(N._tokens("Sex:M Name: SAMPLE, Date"), ["Sex:M", "Name:", "SAMPLE,", "Date"])
        self.assertEqual(N._tokens("No: 12"), ["No:", "12"])

    def test_read_value_keeps_the_dot(self):
        tk = N._tokens("Father's Name: A: TESTER SAMPLE Mother's Name DEMO SAMPLE")
        vals, _ = N.read_value(tk, 1)
        self.assertEqual(vals, ["A.", "TESTER", "SAMPLE"])

    def test_collect_reads_units_and_pages(self):
        c = base_case()
        u = c.add("academic", ["Certificate page", academic()], "ACADEMIC HSC")
        c.add("bank", ["Account Name MD. EXAMPLE SAMPLE"], "BANK")
        c.add("trade_license", ["certify that TESTER SAMPLE is employed here"], "EMPLOYMENT")
        reads, mrz = N.collect_reads(c.units, c.pages, {})
        self.assertEqual({r.unit for r in reads}, {"D01", u.uid})       # no bank / trade_license reads
        st = [r for r in reads if r.unit == u.uid and r.person == "student"]
        self.assertEqual([r.page for r in st], [2])
        self.assertEqual(st[0].written, "MD. EXAMPLE SAMPLE")
        money, _ = N.collect_reads(c.units, c.pages, {}, money=True)
        self.assertIn("D04", {r.unit for r in money})

    def test_birth_cert_read_only_on_english_pages(self):
        c = base_case()
        u = c.add("birth_cert", ["Name NOISEWORD JUNKWORD Father's Name BADONE BADTWO",
                                 "Name EXAMPLE SAMPLE Father's Name TESTER SAMPLE"], "BIRTH CERTIFICATE")
        facts = BirthFacts(pages=tuple(u.pages), english_pages=[u.pages[1]])
        reads, _ = N.collect_reads(c.units, c.pages, {tuple(u.pages): facts})
        bc = [r for r in reads if r.unit == u.uid]
        self.assertTrue(bc)
        self.assertTrue(all(r.page == u.pages[1] for r in bc))
        self.assertFalse(any("NOISEWORD" in r.written or "BADONE" in r.written for r in bc))


class TestLettersRule(unittest.TestCase):
    def test_match_gives_no_unit_finding_and_summary_pass(self):
        c = base_case()
        u = c.add("academic", [academic(), academic()], "ACADEMIC HSC")
        res = run(c, ident_of())
        self.assertEqual(res.per_unit.get(u.uid, []), [])
        self.assertEqual({f.rule: f.verdict for f in res.summary},
                         {"names.student": PASS, "names.father": PASS, "names.mother": PASS})
        self.assertEqual(res.per_person, {"student": PASS, "father": PASS, "mother": PASS})

    def test_one_letter_on_two_pages_is_fail(self):            # the test-1 mother case
        c = base_case()
        u = c.add("academic", [academic(mother="DEMOO SAMPLE"), academic(mother="DEMOO SAMPLE")], "ACADEMIC HSC")
        res = run(c, ident_of())
        self.assertEqual(rules(res, u.uid), [("names.mother", FAIL)])
        f = res.per_unit[u.uid][0]
        self.assertEqual(f.evidence["kind"], "LETTERS")
        self.assertIn("no affidavit covers it", f.message)
        self.assertEqual(f.pages, (2, 3))
        self.assertEqual(res.per_person["mother"], FAIL)

    def test_one_page_and_the_reread(self):
        for answer, verdict in (("confirmed", FAIL), ("refuted", FLAG), ("unclear", FLAG)):
            with self.subTest(answer=answer):
                c = base_case()
                u = c.add("academic", ["Certificate page", academic(mother="DEMOO SAMPLE")], "ACADEMIC SSC")
                conf = FakeConfirm(answer)
                res = run(c, ident_of(), conf)
                self.assertEqual(rules(res, u.uid), [("names.mother", verdict)])
                self.assertIn((2, "DEMOO SAMPLE"), conf.calls)
                if answer == "refuted":
                    self.assertIn("probably OCR", res.per_unit[u.uid][0].message)
                if verdict == FLAG:
                    self.assertIn("check by eye", res.per_unit[u.uid][0].message)

    def test_a_difference_is_never_pass(self):
        variants = ["DEMOO SAMPLE", "DEM0 SAMPLE", "SAMPLE DEMO", "MST DEMO SAMPLE", "DEMO", "OTHERNAME SAMPLE"]
        for v in variants:
            for answer in ("confirmed", "refuted", "unclear"):
                c = base_case()
                u = c.add("academic", [academic(mother=v)], "ACADEMIC")
                res = run(c, ident_of(), FakeConfirm(answer))
                got = rules(res, u.uid)
                with self.subTest(v=v, answer=answer):
                    self.assertTrue(got, "a difference must give a finding")
                    self.assertNotIn(PASS, [x for _, x in got])
                    self.assertNotEqual(res.per_person["mother"], PASS)

    def test_dropped_md_and_long_form_are_fail(self):
        for doc_name, kind in (("EXAMPLE SAMPLE", "SUBSET"), (f"{LONG_MD} EXAMPLE SAMPLE", "DIFFERENT")):
            with self.subTest(doc_name=doc_name):
                c = base_case()
                u = c.add("academic", [academic(student=doc_name), academic(student=doc_name)], "ACADEMIC")
                res = run(c, ident_of(student_written="MD EXAMPLE SAMPLE"))
                self.assertEqual(rules(res, u.uid), [("names.student", FAIL)])
                self.assertEqual(res.per_unit[u.uid][0].evidence["kind"], kind)

    def test_added_md_is_a_difference(self):
        c = base_case()
        u = c.add("academic", [academic(student="MD EXAMPLE SAMPLE")] * 2, "ACADEMIC")
        res = run(c, ident_of(student_written="EXAMPLE SAMPLE", student_parts=("SAMPLE", "EXAMPLE")))
        self.assertEqual(rules(res, u.uid), [("names.student", FAIL)])

    def test_contradicted_on_the_same_document_is_flag(self):
        c = base_case()
        u = c.add("academic", [academic(mother="DEMOO SAMPLE"), academic(mother="DEMO SAMPLE")], "ACADEMIC")
        res = run(c, ident_of(), FakeConfirm("confirmed"))
        self.assertEqual(rules(res, u.uid), [("names.mother", FLAG)])

    def test_unconfirmed_reference_never_fails(self):
        c = base_case()
        u = c.add("academic", [academic(mother="DEMOO SAMPLE")] * 2, "ACADEMIC")
        ident = ident_of()
        ident.mother.confirmed = False
        res = run(c, ident, FakeConfirm("confirmed"))
        self.assertEqual(rules(res, u.uid), [("names.mother", FLAG)])

    def test_affidavit(self):
        c = base_case()
        u = c.add("academic", [academic(mother="DEMOO SAMPLE")] * 2, "ACADEMIC")
        cover = AffidavitInfo(present=True, units=["DX"], groups=[("mother", ["DEMO SAMPLE", "DEMOO SAMPLE"])])
        res = run(c, ident_of(), aff=cover)
        self.assertEqual(rules(res, u.uid), [("names.mother", NOTE)])
        other = AffidavitInfo(present=True, units=["DX"], groups=[("father", ["TESTER SAMPLE", "TESTOR SAMPLE"])])
        res = run(c, ident_of(), aff=other)
        self.assertEqual(rules(res, u.uid), [("names.mother", FAIL)])
        noisy = AffidavitInfo(present=True, units=["DX"], groups=[("mother", ["DEM0 SAMPLE", "DEMOO SAMPLE"])])
        res = run(c, ident_of(), aff=noisy)
        self.assertEqual(rules(res, u.uid), [("names.mother", FLAG)])        # covered only up to OCR noise

    def test_glued_initial_with_its_dot_is_no_finding(self):     # A6 e
        c = base_case()
        u = c.add("academic", [academic(father="A.TESTER SAMPLE")] * 2, "ACADEMIC")
        res = run(c, ident_of(father="A. TESTER SAMPLE"))
        self.assertEqual(res.per_unit.get(u.uid, []), [])

    def test_truncated_mrz_extension_is_flag(self):
        c = base_case()
        u = c.add("academic", [academic(student="EXAMPLELONG SAMPLE")] * 2, "ACADEMIC")
        ident = ident_of(student_written="", student_parts=("SAMPLE", "EXAMPLELO"), truncated=True)
        res = run(c, ident, FakeConfirm("confirmed"))
        self.assertEqual(rules(res, u.uid), [("names.student", FLAG)])
        self.assertIn("may be cut", res.per_unit[u.uid][0].message)

    def test_mrz_only_reference_any_order(self):
        c = base_case()
        u = c.add("academic", [academic(student="MD EXAMPLE SAMPLE")] * 2, "ACADEMIC")
        res = run(c, ident_of(student_written=""))
        self.assertEqual(res.per_unit.get(u.uid, []), [])

    def test_machine_reads(self):
        # a board QR with the reference letters confirms the reference: the page variant is FLAG, not FAIL
        c = base_case()
        u = c.add("academic", ["Certificate page", academic(student="MD EXAMPLE SAMPLA")], "ACADEMIC")
        qr = MachineRead(unit=u.uid, person="student", name="MD EXAMPLE SAMPLE", source="board QR", page=1)
        res = run(c, ident_of(student_written="MD EXAMPLE SAMPLE"), FakeConfirm("confirmed"), machine=[qr])
        self.assertEqual(rules(res, u.uid), [("names.student", FLAG)])
        # a QR with the variant's letters confirms the variant: FAIL
        qr2 = MachineRead(unit=u.uid, person="student", name="SAMPLA MD EXAMPLE", source="board QR", page=1)
        res = run(c, ident_of(student_written="MD EXAMPLE SAMPLE"), FakeConfirm("unclear"), machine=[qr2])
        self.assertEqual(rules(res, u.uid), [("names.student", FAIL)])
        # every read and the QR agree: nothing
        c2 = base_case()
        u2 = c2.add("academic", [academic(student="MD EXAMPLE SAMPLE")], "ACADEMIC")
        qr3 = MachineRead(unit=u2.uid, person="student", name="SAMPLE MD EXAMPLE", source="board QR", page=1)
        res = run(c2, ident_of(student_written="MD EXAMPLE SAMPLE"), machine=[qr3])
        self.assertEqual(res.per_unit.get(u2.uid, []), [])

    def test_bank_and_trade_license_give_no_names_finding(self):
        c = base_case()
        b = c.add("bank", ["This is to certify that DEMOO SAMPLE maintains an account"], "BANK")
        t = c.add("trade_license", ["Name of Proprietor DEMOO SAMPLE"], "TRADE LICENSE")
        res = run(c, ident_of(), FakeConfirm("confirmed"))
        self.assertNotIn(b.uid, res.per_unit)
        self.assertNotIn(t.uid, res.per_unit)

    def test_no_reference_is_flag_on_the_summary(self):
        c = base_case()
        c.add("academic", [academic()], "ACADEMIC")
        ident = ident_of()
        ident.father = NameRef()
        res = run(c, ident)
        self.assertEqual({f.rule: f.verdict for f in res.summary}["names.father"], FLAG)

    def test_nid_mrz_line_is_surname_first(self):
        c = base_case()
        u = c.add("father_nid", ["NATIONAL ID CARD Name TESTER SAMPLE Date of Birth",
                                 "I<BGD0000000000<<<<<<<< SAMPLE<<TESTER<<<<<<<<<<<<"], "FATHER NID")
        res = run(c, ident_of())
        self.assertEqual(res.per_unit.get(u.uid, []), [])

    def test_card_mrz_line_only_supports_a_matching_printed_name(self):
        c = base_case()
        u = c.add("father_nid", ["NATIONAL ID CARD Name TESTER SAMPLE Date of Birth",
                                 "I<BGD0000000000<<<<<<<< SAMPLE<<TESTEP<<<<<<<<<<<<"], "FATHER NID")
        res = run(c, ident_of(), FakeConfirm("confirmed"))
        self.assertEqual(res.per_unit.get(u.uid, []), [])
        c2 = base_case()
        u2 = c2.add("father_nid", ["NATIONAL ID CARD unreadable front",
                                   "I<BGD0000000000<<<<<<<< SAMPLE<<TESTEP<<<<<<<<<<<<"], "FATHER NID")
        res = run(c2, ident_of(), FakeConfirm("unclear"))
        self.assertEqual(rules(res, u2.uid), [("names.father", FLAG)])   # no printed read: the MRZ line counts

    def test_a_word_dropped_after_the_label(self):
        c = base_case()
        u = c.add("academic", [academic(mother="DEMO")] * 2, "ACADEMIC")
        res = run(c, ident_of())
        self.assertEqual(rules(res, u.uid), [("names.mother", FAIL)])
        self.assertEqual(res.per_unit[u.uid][0].evidence["kind"], "SUBSET")


class TestDotRule(unittest.TestCase):
    """A2 through evaluate(): the passport's printed name is the reference; two pages confirm a spelling."""

    def dots(self, ref, docs, confirm=None):
        c = base_case()
        us = [c.add("academic", [academic(student=d)] * n, f"ACADEMIC {k}") for k, (d, n) in enumerate(docs)]
        res = run(c, ident_of(student_written=ref), confirm)
        return [{r: v for r, v in rules(res, u.uid)}.get("names.student_dots", PASS) for u in us], res

    def test_owner_case_1(self):        # passport MD. + all others MD -> OK
        got, _ = self.dots("MD. EXAMPLE SAMPLE", [("MD EXAMPLE SAMPLE", 2), ("MD EXAMPLE SAMPLE", 2)])
        self.assertEqual(got, [PASS, PASS])

    def test_owner_case_2(self):        # passport MD. + others mixed -> FAIL
        got, res = self.dots("MD. EXAMPLE SAMPLE", [("MD. EXAMPLE SAMPLE", 2), ("MD EXAMPLE SAMPLE", 2)])
        self.assertEqual(got, [PASS, FAIL])
        self.assertEqual(res.per_person["student"], FAIL)

    def test_owner_case_3(self):        # passport MD + others mixed -> FAIL
        got, _ = self.dots("MD EXAMPLE SAMPLE", [("MD. EXAMPLE SAMPLE", 2), ("MD EXAMPLE SAMPLE", 2)])
        self.assertEqual(got, [FAIL, PASS])

    def test_owner_case_4(self):        # passport MD + others MD -> OK
        got, _ = self.dots("MD EXAMPLE SAMPLE", [("MD EXAMPLE SAMPLE", 2), ("MD EXAMPLE SAMPLE", 2)])
        self.assertEqual(got, [PASS, PASS])

    def test_symmetric_case_q13(self):  # passport MD + all others MD. -> OK (assumed)
        got, _ = self.dots("MD EXAMPLE SAMPLE", [("MD. EXAMPLE SAMPLE", 2), ("MD. EXAMPLE SAMPLE", 2)])
        self.assertEqual(got, [PASS, PASS])

    def test_test1_case(self):          # A6 b: SSC "MD" once, the re-read shows "MD." -> FLAG, never FAIL
        conf = FakeConfirm(lambda p, v: "refuted" if "." not in v else "confirmed", printed="MD.EXAMPLESAMPLE")
        got, res = self.dots("MD. EXAMPLE SAMPLE", [("MD. EXAMPLE SAMPLE", 2), ("MD EXAMPLE SAMPLE", 1)], conf)
        self.assertEqual(got, [PASS, FLAG])
        self.assertNotIn(FAIL, [f.verdict for fs in res.per_unit.values() for f in fs])

    def test_single_reads_mixed_reread_confirms_then_fail(self):
        got, _ = self.dots("MD. EXAMPLE SAMPLE", [("MD. EXAMPLE SAMPLE", 1), ("MD EXAMPLE SAMPLE", 1)],
                           FakeConfirm("confirmed"))
        self.assertEqual(got, [PASS, FAIL])
        got, _ = self.dots("MD. EXAMPLE SAMPLE", [("MD. EXAMPLE SAMPLE", 1), ("MD EXAMPLE SAMPLE", 1)],
                           FakeConfirm("unclear"))
        self.assertEqual(got, [PASS, FLAG])

    def test_glued_dot_loss_helper(self):          # integration (test 1): "M.Sample" read "MSample"
        self.assertTrue(N.glued_dot_loss("MDEXAMPLE SAMPLE", "MD. EXAMPLE SAMPLE"))
        self.assertTrue(N.glued_dot_loss("Mr: AExample Sample", "A. EXAMPLE SAMPLE"))
        self.assertFalse(N.glued_dot_loss("MD EXAMPLE SAMPLE", "MD. EXAMPLE SAMPLE"))     # a space: a real read
        self.assertFalse(N.glued_dot_loss("MD.EXAMPLE SAMPLE", "MD. EXAMPLE SAMPLE"))     # the dot is there
        self.assertFalse(N.glued_dot_loss("MDEXAMPLE SAMPLE", "MD EXAMPLE SAMPLE"))       # no dot to lose
        self.assertFalse(N.glued_dot_loss("MDEXAMPLE SAMPLE", None))

    def test_glued_read_never_confirms_a_missing_dot(self):
        # two pages and a "confirming" re-read of a glued read are the same OCR artefact: FLAG, never FAIL
        got, res = self.dots("MD. EXAMPLE SAMPLE", [("MD. EXAMPLE SAMPLE", 1), ("MDEXAMPLE SAMPLE", 2)],
                             FakeConfirm("confirmed"))
        self.assertEqual(got, [PASS, FLAG])
        msg = [f.message for fs in res.per_unit.values() for f in fs if f.rule == "names.student_dots"][0]
        self.assertIn("run together", msg)

    def test_reference_following_doc_not_flagged_for_a_glued_read(self):
        got, _ = self.dots("MD. EXAMPLE SAMPLE", [("MD. EXAMPLE SAMPLE", 1), ("MDEXAMPLE SAMPLE", 1)],
                           FakeConfirm(lambda p, v: "confirmed" if "." not in v else "unclear"))
        self.assertEqual(got, [PASS, FLAG])

    def test_dot_fail_covered_is_note(self):
        c = base_case()
        a = c.add("academic", [academic(student="MD. EXAMPLE SAMPLE")] * 2, "A")
        b = c.add("academic", [academic(student="MD EXAMPLE SAMPLE")] * 2, "B")
        cover = AffidavitInfo(present=True, groups=[("student", ["MD. EXAMPLE SAMPLE", "MD EXAMPLE SAMPLE"])])
        res = run(c, ident_of(), aff=cover)
        self.assertEqual(rules(res, b.uid), [("names.student_dots", NOTE)])
        self.assertEqual(rules(res, a.uid), [])

    def test_extra_reads_enter_the_dot_rule_only(self):
        c = base_case()
        a = c.add("academic", [academic(father="A. TESTER SAMPLE")] * 2, "A")
        bank = c.add("bank", ["solvency page"], "BANK")
        res = run(c, ident_of(father="A. TESTER SAMPLE"), extra=[("father", bank.uid, "A TESTER SAMPLE", bank.pages[0]),
                                                                ("father", bank.uid, "A TESTER SAMPLE", bank.pages[0])])
        self.assertEqual(rules(res, a.uid), [])
        self.assertEqual(rules(res, bank.uid), [("names.father_dots", FLAG)])   # one page: not confirmed
        res = run(c, ident_of(father="A. TESTER SAMPLE"), extra=[("father", bank.uid, "A TESTER SAMPLA", 5)])
        self.assertNotIn(bank.uid, res.per_unit)                                  # other letters: ignored


class TestAffidavit(unittest.TestCase):
    def test_groups_keep_spellings_as_written(self):
        text = ("AFFIDAVIT I solemnly declare that 4. So, MD. EXAMPLE SAMPLE and MD EXAMPLE SAMPLE are the same "
                "person. 5. That my mother's name DEMO SAMPLE and DEMOO SAMPLE is the same person.")
        gs = N.affidavit_groups(text)
        flat = [(g.whose, g.variants) for g in gs]
        self.assertTrue(any("MD. EXAMPLE SAMPLE" in v and "MD EXAMPLE SAMPLE" in v for _, v in flat), flat)
        self.assertTrue(any(w == "mother" and "DEMOO SAMPLE" in v for w, v in flat), flat)

    def test_covered_is_letters_and_dots_in_one_group(self):
        aff = AffidavitInfo(present=True, groups=[("", ["MD. EXAMPLE SAMPLE", "MD EXAMPLE SAMPLE"]),
                                                  ("mother", ["DEMO SAMPLE", "DEMOO SAMPLE"])])
        self.assertTrue(N.covered(aff, "MD. EXAMPLE SAMPLE", "md example sample"))
        self.assertTrue(N.covered(aff, "DEMO SAMPLE", "DEMOO  SAMPLE"))
        self.assertFalse(N.covered(aff, "MD. EXAMPLE SAMPLE", "DEMO SAMPLE"))       # two groups
        self.assertFalse(N.covered(aff, "MD.EXAMPLE SAMPLE", "MD EXAMPLE.SAMPLE"))   # a dot elsewhere
        self.assertTrue(N.covered(aff, ("SAMPLE", "MD", "EXAMPLE"), "MD EXAMPLE SAMPLE"))  # MRZ parts
        self.assertFalse(N.covered(AffidavitInfo(), "DEMO SAMPLE", "DEMOO SAMPLE"))

    def test_affidavit_info(self):
        c = base_case()
        c.add("affidavit", ["That my mother's name DEMO SAMPLE and DEMOO SAMPLE is the same person. "
                            "Her NID and birth certificate"], "AFFIDAVIT")
        info = N.affidavit_info(c.units, c.pages)
        self.assertTrue(info.present)
        self.assertEqual(info.units, ["D02"])
        self.assertIn("nid", info.documents)
        self.assertTrue(N.covered(info, "DEMO SAMPLE", "DEMOO SAMPLE"))


class TestCanonical(unittest.TestCase):
    def reads(self, *texts_by_key):
        c = Case()
        for key, texts in texts_by_key:
            c.add(key, texts)
        return N.collect_reads(c.units, c.pages, {})[0]

    def test_birth_certificate_cut_by_vocabulary(self):
        rs = self.reads(("birth_cert", ["Name EXAMPLE SAMPLE Father's Name TESTER SAMPLE Sd Illegible OTHER "
                                        "Mother's Name DEMO SAMPLE"]),
                        ("academic", [academic()]))
        self.assertEqual(N.canonical("father", rs, None), (("TESTER", "SAMPLE"), "birth certificate"))
        d = N.canonical_detail("mother", rs, None)
        self.assertEqual((d["parts"], d["written"], d["how"]), (("DEMO", "SAMPLE"), "DEMO SAMPLE", "birth certificate"))

    def test_vocabulary_from_other_units(self):         # critique 4: the passport has no parents' page
        rs = self.reads(("birth_cert", ["Father's Name TESTER SAMPLE JUNKWORD MOREJUNK Mother's Name DEMO SAMPLE"]))
        voc = self.reads(("nid_unassigned", ["NATIONAL ID Name TESTER SAMPLE Date of Birth"]))
        parts, how = N.canonical("father", rs, None, voc)
        self.assertEqual((parts, how), (("TESTER", "SAMPLE"), "birth certificate"))

    def test_passport_field_fallback(self):
        rs = self.reads(("passport", ["Father's Name TESTER SAMPLE Mother's Name DEMO SAMPLE"]))
        self.assertEqual(N.canonical("mother", rs, None), (("DEMO", "SAMPLE"), "passport field"))
        self.assertEqual(N.canonical("father", [], None), ((), "no canonical read"))


class TestSmallRows(unittest.TestCase):
    def test_family_first_row(self):
        ident = ident_of()
        ok = N.family_first_row("FAMILY CERTIFICATE 1. MD. EXAMPLE SAMPLE self 2. TESTER SAMPLE father", ident)
        self.assertEqual((ok.verdict, ok.rule), (PASS, "names.family_first_row"))
        bad = N.family_first_row("FAMILY CERTIFICATE 1. EXAMPLE SAMPLE self", ident)
        self.assertEqual(bad.verdict, FLAG)
        far = N.family_first_row("x " * 700 + "MD EXAMPLE SAMPLE", ident)
        self.assertEqual(far.verdict, FLAG)

    def test_evaluate_files_the_family_first_row(self):
        c = base_case()
        u = c.add("family_cert", ["FAMILY CERTIFICATE 1. MD. EXAMPLE SAMPLE self 2. TESTER SAMPLE father"], "FAMILY")
        res = run(c, ident_of())
        self.assertIn(("names.family_first_row", PASS), rules(res, u.uid))
        self.assertEqual([f.pages for f in res.per_unit[u.uid] if f.rule == "names.family_first_row"], [(2,)])

    def test_printed_student_name(self):
        c = Case()
        u = c.add("birth_cert", ["Name NOISEWORD JUNKWORD Date", "Name: MD. EXAMPLE SAMPLE Sex Male",
                                 "Name MD. EXAMPLE SAMPLE Father's Name TESTER SAMPLE"])
        facts = BirthFacts(pages=tuple(u.pages), english_pages=[1, 2])
        self.assertEqual(N.printed_student_name(u, c.pages, facts), ("MD. EXAMPLE SAMPLE", True))
        facts1 = BirthFacts(pages=tuple(u.pages), english_pages=[2])
        self.assertEqual(N.printed_student_name(u, c.pages, facts1), ("MD. EXAMPLE SAMPLE", False))
        none = BirthFacts(pages=tuple(u.pages), english_pages=[])
        self.assertEqual(N.printed_student_name(u, c.pages, none), ("", False))


class FakeEngine:
    """page_image_of -> a blank page; read_boxes -> the page's boxes, or the crop's re-read boxes."""

    def __init__(self, page_boxes, crop_boxes):
        import numpy as np
        self.np = np
        self.page_boxes, self.crop_boxes = page_boxes, crop_boxes
        self.reads = 0

    def page_image_of(self, page, dpi=300):
        return self.np.full((300, 900, 3), 255, dtype=self.np.uint8)

    def read_boxes(self, img):
        self.reads += 1
        return self.page_boxes if img.shape[:2] == (300, 900) else self.crop_boxes


def box(x0, y0, x1, y1):
    return [[x0, y0], [x1, y0], [x1, y1], [x0, y1]]


class TestConfirmer(unittest.TestCase):
    def setUp(self):
        shutil.rmtree(WORK, ignore_errors=True)
        self.pages = [page(0, "Name MD EXAMPLE SAMPLE")]
        self.page_boxes = [(box(10, 100, 80, 130), "Name:", 0.99), (box(100, 100, 160, 130), "MD", 0.97),
                           (box(170, 100, 300, 130), "EXAMPLE SAMPLE", 0.95)]

    def tearDown(self):
        shutil.rmtree(WORK, ignore_errors=True)

    def test_results(self):
        crop = {"confirmed": [(box(0, 0, 50, 40), "MD", 0.95), (box(60, 0, 300, 40), "EXAMPLE SAMPLE", 0.9)],
                "refuted": [(box(0, 0, 50, 40), "MD.", 0.95), (box(60, 0, 300, 40), "EXAMPLE SAMPLE", 0.9)],
                "low": [(box(0, 0, 50, 40), "MD", 0.5), (box(60, 0, 300, 40), "EXAMPLE SAMPLE", 0.9)],
                "other": [(box(0, 0, 300, 40), "MD EXAMPLE SAMPLA", 0.95)],
                "none": [(box(0, 0, 300, 40), "UNRELATEDWORDS HERE", 0.95)]}
        want = {"confirmed": "confirmed", "refuted": "refuted", "low": "unclear", "other": "refuted",
                "none": "unclear"}
        for k, boxes in crop.items():
            with self.subTest(k=k):
                conf = N.make_confirmer(FakeEngine(self.page_boxes, boxes), self.pages, WORK)
                self.assertEqual(conf(0, "MD EXAMPLE SAMPLE"), want[k])
                self.assertEqual(len(conf.log), 1)
                if k == "refuted":
                    self.assertEqual(conf.printed(0, "MD EXAMPLE SAMPLE"), "MD.EXAMPLESAMPLE")
                if k != "none":
                    self.assertTrue(Path(conf.log[0]["crop"]).is_file())
                    self.assertTrue(Path(conf.log[0]["crop"]).resolve().is_relative_to(B.PRIVATE))

    def test_reference_around_the_variant_refutes_it(self):
        # the page read dropped "MD"; the zoomed crop shows the whole reference: not a confirmation
        crop = [(box(0, 0, 50, 40), "MD", 0.95), (box(60, 0, 300, 40), "EXAMPLE SAMPLE", 0.93)]
        conf = N.make_confirmer(FakeEngine(self.page_boxes, crop), self.pages, WORK)
        self.assertEqual(conf(0, "EXAMPLE SAMPLE", reference="MD EXAMPLE SAMPLE"), "refuted")
        self.assertEqual(conf.log[-1]["why"], "the re-read shows the reference spelling")
        conf2 = N.make_confirmer(FakeEngine(self.page_boxes, crop[1:]), self.pages, WORK)
        self.assertEqual(conf2(0, "EXAMPLE SAMPLE", reference="MD EXAMPLE SAMPLE"), "confirmed")

    def test_no_engine_memo_and_budget(self):
        self.assertEqual(N.make_confirmer(None, self.pages, WORK)(0, "MD EXAMPLE SAMPLE"), "unclear")
        eng = FakeEngine(self.page_boxes, [(box(0, 0, 300, 40), "MD EXAMPLE SAMPLE", 0.99)])
        conf = N.make_confirmer(eng, self.pages, WORK)
        self.assertEqual(conf(0, "MD EXAMPLE SAMPLE"), "confirmed")
        n = eng.reads
        self.assertEqual(conf(0, "md example  sample"), "confirmed")     # memoised (same form)
        self.assertEqual(eng.reads, n)
        conf.used = N.REREAD_MAX
        self.assertEqual(conf(0, "MD EXAMPLE SAMPLA"), "unclear")         # budget spent
        self.assertEqual(N.REREAD_MAX, 24)
        self.assertTrue(N.REREAD_CAN_FAIL)
        self.assertEqual(N.REREAD_MIN_CONF, 0.80)

    def test_name_not_on_the_page(self):
        conf = N.make_confirmer(FakeEngine([(box(0, 0, 90, 30), "NOTHING", 0.9)], []), self.pages, WORK)
        self.assertEqual(conf(0, "MD EXAMPLE SAMPLE"), "unclear")

    def test_evaluate_lists_the_rereads(self):
        c = base_case()
        u = c.add("academic", ["Certificate page", academic(mother="DEMOO SAMPLE")], "ACADEMIC")
        boxes = [(box(10, 100, 120, 130), "Mother's Name", 0.99), (box(130, 100, 300, 130), "DEMOO SAMPLE", 0.96)]
        eng = FakeEngine(boxes, [(box(0, 0, 300, 40), "DEMOO SAMPLE", 0.96)])
        conf = N.make_confirmer(eng, c.pages, WORK)
        res = run(c, ident_of(), conf)
        self.assertEqual(rules(res, u.uid), [("names.mother", FAIL)])
        self.assertEqual(len(res.rereads), 1)
        self.assertEqual(res.rereads[0]["result"], "confirmed")
        self.assertEqual(res.rereads[0]["effect"], "names.mother FAIL")


# ============================================================================================ owner 28 Oct
class Look:
    """closer.Look, made by hand (decision, best, the rest neutral)."""

    def __new__(cls, decision, best, cands):
        from src.verify.runner import closer
        return closer.Look(decision, best, {c: 0.0 for c in cands}, 0.5, 10, 10, None, "test")


class CloserFake:
    """A closer-mode confirmer: `truth` maps a run page to the spelling (letters and dots) the page prints; the
    look picks the candidate equal to it (dots too for kind "dots"), else "unclear"."""
    closer = True

    def __init__(self, truth):
        self.truth, self.log, self.calls = truth, [], []

    def look(self, page, candidates, kind="name"):
        self.calls.append((page, tuple(candidates), kind))
        t = self.truth.get(page)
        if t is None:
            return Look("unclear", candidates[0], candidates)
        key = S.form if kind == "dots" else S.letters
        hit = next((c for c in candidates if key(c) == key(t)), None)
        if hit is None:
            return Look("unclear", candidates[0], candidates)
        return Look("same" if hit == candidates[0] else "differs", hit, candidates)

    def pair(self, page, reference, variant, kind="name"):
        res = self.look(page, [reference, variant], kind)
        return res.decision

    def __call__(self, page_index, variant, reference=""):
        return "unclear"


class TestBoardPapers(unittest.TestCase):
    def test_which_papers_are_board_papers(self):
        def u(title, level="", text="Transcript"):
            c = Case()
            unit = c.add("academic", [text], title)
            unit.level = level
            return unit, c.pages
        self.assertTrue(N.board_paper(*u("ACADEMIC HSC")))
        self.assertTrue(N.board_paper(*u("ACADEMIC (HSC, SSC)")))
        self.assertTrue(N.board_paper(*u("ACADEMIC", level="SSC")))
        self.assertFalse(N.board_paper(*u("ACADEMIC DEGREE")))
        self.assertFalse(N.board_paper(*u("ACADEMIC", level="degree")))
        self.assertTrue(N.board_paper(*u("ACADEMIC", text="Board of Intermediate and Secondary Education Dakhil")))
        self.assertTrue(N.board_paper(*u("ACADEMIC", text="Madrasah Education Board Alim Examination")))
        self.assertFalse(N.board_paper(*u("ACADEMIC", text="Sample University Bachelor of Science CGPA")))
        c = Case()
        self.assertFalse(N.board_paper(c.add("family_cert", ["x"]), c.pages))

    def test_board_papers_dots_are_not_judged(self):
        """Owner 28 Oct: "board papers md. is fine, no fail for that" - passport MD. + a board paper MD + another
        document MD. is no 'mixed' FAIL; the board paper gets a NOTE; its letters stay strict."""
        c = base_case()
        hsc = c.add("academic", [academic(student="MD EXAMPLE SAMPLE")] * 2, "ACADEMIC HSC")
        deg = c.add("academic", [academic(student="MD. EXAMPLE SAMPLE")] * 2, "ACADEMIC DEGREE")
        res = run(c, ident_of(), FakeConfirm("confirmed"))
        self.assertEqual(rules(res, hsc.uid), [("names.student_dots", NOTE)])
        self.assertIn(N.BOARD_DOTS_NOTE, res.per_unit[hsc.uid][0].message)
        self.assertEqual(rules(res, deg.uid), [])
        line = next(f for f in res.summary if f.rule == "names.student")
        self.assertEqual(line.verdict, PASS)
        c2 = base_case()
        hsc2 = c2.add("academic", [academic(student="MD. EXAMPLE SAMPLX")] * 2, "ACADEMIC HSC")
        res2 = run(c2, ident_of(), FakeConfirm("confirmed"))
        self.assertEqual(rules(res2, hsc2.uid), [("names.student", FAIL)])      # letters stay strict


class TestCloserMode(unittest.TestCase):
    """names.evaluate with the closer look (a fake looker standing in for closer.look on the page images)."""

    def test_dots_come_from_the_closer_look(self):
        # passport MD. ; the NID's page read lost the dot but the look shows it; the employment letter has none
        c = base_case()
        nid = c.add("student_nid", ["NID Name MD EXAMPLE SAMPLE Date of Birth"], "STUDENT NID")
        emp = c.add("trade_license", ["To whom it may concern employment letter"], "EMPLOYMENT CERTIFICATE")
        truth = {0: "MD. EXAMPLE SAMPLE", nid.pages[0]: "MD. EXAMPLE SAMPLE", emp.pages[0]: "MD EXAMPLE SAMPLE"}
        res = run(c, ident_of(), CloserFake(truth), extra=[("student", emp.uid, "MD. EXAMPLE SAMPLE", emp.pages[0])])
        self.assertEqual(rules(res, nid.uid), [])
        self.assertEqual(rules(res, emp.uid), [("names.student_dots", FAIL)])

    def test_an_unclear_dot_look_is_flag_never_fail(self):
        c = base_case()
        nid = c.add("student_nid", ["NID Name MD. EXAMPLE SAMPLE Date of Birth"], "STUDENT NID")
        emp = c.add("trade_license", ["To whom it may concern employment letter"], "EMPLOYMENT CERTIFICATE")
        truth = {0: "MD. EXAMPLE SAMPLE", nid.pages[0]: "MD. EXAMPLE SAMPLE"}       # the letter's page: unclear
        res = run(c, ident_of(), CloserFake(truth), extra=[("student", emp.uid, "MD EXAMPLE SAMPLE", emp.pages[0])])
        self.assertEqual(rules(res, emp.uid), [("names.student_dots", FLAG)])

    def test_a_misread_letter_is_corrected_on_its_page(self):
        c = base_case()
        u = c.add("academic", [academic(mother="DEMO SAMPIE")], "ACADEMIC DEGREE")
        res = run(c, ident_of(), CloserFake({u.pages[0]: "DEMO SAMPLE"}))
        self.assertEqual(rules(res, u.uid), [])
        self.assertEqual(next(f for f in res.summary if f.rule == "names.mother").verdict, PASS)

    def test_a_real_one_letter_difference_fails(self):
        c = base_case()
        u = c.add("academic", [academic(mother="DEMO SAMPLA")], "ACADEMIC DEGREE")
        ident = ident_of()
        ident.mother.pages = (0,)
        res = run(c, ident, CloserFake({u.pages[0]: "DEMO SAMPLA", 0: "DEMO SAMPLE"}))
        self.assertEqual(rules(res, u.uid), [("names.mother", FAIL)])
        f = res.per_unit[u.uid][0]
        self.assertEqual((f.evidence["closer_variant"], f.evidence["closer_reference"]), ("differs", "same"))

    def test_every_page_of_the_document_is_looked_at(self):
        """Two pages read the same variant: both looks "same" -> a misread, no finding; one "same" and one unclear ->
        FLAG (the unclear page may print the variant), never FAIL; one page "differs" -> the document prints it."""
        ident = ident_of()
        ident.mother.pages = (0,)
        for truth_of, want in (
                (lambda u: {u.pages[0]: "DEMO SAMPLE", u.pages[1]: "DEMO SAMPLE", 0: "DEMO SAMPLE"}, []),
                (lambda u: {u.pages[0]: "DEMO SAMPLE", 0: "DEMO SAMPLE"}, [("names.mother", FLAG)]),
                (lambda u: {u.pages[0]: "DEMO SAMPLE", u.pages[1]: "DEMO SAMPLA", 0: "DEMO SAMPLE"},
                 [("names.mother", FAIL)])):
            c = base_case()
            u = c.add("academic", [academic(mother="DEMO SAMPLA")] * 2, "ACADEMIC DEGREE")
            with self.subTest(want=want):
                conf = CloserFake(truth_of(u))
                res = run(c, ident, conf)
                self.assertEqual(rules(res, u.uid), want)
                self.assertEqual({p for p, _c, k in conf.calls if k == "name" and p != 0}, set(u.pages))

    def test_the_reference_page_showing_the_variant_is_flag(self):
        """The passport's own page shows the variant's spelling: the reference read may be the misread, so the
        difference is FLAG, never FAIL (and never an outvote by other documents)."""
        c = base_case()
        u = c.add("academic", [academic(student="MD. EXAMPLE SAMPLX")], "ACADEMIC DEGREE")
        res = run(c, ident_of(), CloserFake({u.pages[0]: "MD. EXAMPLE SAMPLX", 0: "MD. EXAMPLE SAMPLX"}))
        self.assertEqual(rules(res, u.uid), [("names.student", FLAG)])
        f = res.per_unit[u.uid][0]
        self.assertIn("may be the misread", f.message)
        self.assertEqual((f.evidence["closer_variant"], f.evidence["closer_reference"]), ("differs", "differs"))

    def test_other_documents_never_outvote_a_confirmed_difference(self):
        """Owner 28 Oct: three documents print the same variant (each confirmed on its page) and the passport's page
        prints the reference: every one of them is FAIL, never downgraded by their number."""
        c = base_case()
        us = [c.add("academic", [academic(mother="DEMO SAMPLA")], f"ACADEMIC {k}") for k in ("DEGREE", "DIPLOMA")]
        us.append(c.add("student_nid", ["NID Name MD. EXAMPLE SAMPLE Mother DEMO SAMPLA Date"], "STUDENT NID"))
        ident = ident_of()
        ident.mother.pages = (0,)
        truth = {u.pages[0]: "DEMO SAMPLA" for u in us}
        truth[0] = "DEMO SAMPLE"
        res = run(c, ident, CloserFake(truth))
        for u in us:
            self.assertIn(("names.mother", FAIL), rules(res, u.uid))
        self.assertEqual(next(f for f in res.summary if f.rule == "names.mother").verdict, FAIL)

    def test_a_misparsed_label_is_searched_for_the_name_itself(self):
        """A single read too unlike the name ("not compared", FLAG) gets a closer look for the reference on its page:
        printed there clearly -> no finding; not there -> the FLAG stays (never PASS)."""
        c = base_case()
        u = c.add("academic", [academic(mother="OTHERWORD ANOTHERWORD")], "ACADEMIC DEGREE")
        res = run(c, ident_of(), CloserFake({u.pages[0]: "DEMO SAMPLE"}))
        self.assertEqual(rules(res, u.uid), [])
        self.assertEqual(next(f for f in res.summary if f.rule == "names.mother").verdict, PASS)
        res = run(c, ident_of(), CloserFake({u.pages[0]: "OTHERWORD ANOTHERWORD"}))
        self.assertEqual(rules(res, u.uid), [("names.mother_unread", FLAG)])

    def test_an_address_word_under_a_one_word_name_is_no_difference(self):
        fam = ("Family Certificate SL NAME RELATION ID NO BIRTH DATE ADDRESS 01 GAMMA SAMPLE SELF 1234567890 "
               "01-06-2003 Vill: Somewhere 02 TESTER FATHER 2345678901 01-02- Vill: Somewhere TOWNNAME 1967 "
               "DISTRICTX 03 DEMO SAMPLE MOTHER 3456789012 28-03-1976 Vill: Somewhere")
        c = base_case()
        u = c.add("family_cert", [fam], "FAMILY CERTIFICATE")
        res = run(c, ident_of(father="TESTER"), CloserFake({}))
        self.assertEqual([r for r in rules(res, u.uid) if r[0] == "names.father"], [])


if __name__ == "__main__":
    unittest.main()
