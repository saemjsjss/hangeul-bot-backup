"""Task 3 tests: identity.py (RUNNER_SPEC 5.5; owner decisions A1, A3, A6 a, A7; critique 4 and 12).

Synthetic MRZ lines whose ICAO check digits are computed here from made-up data. The passport number is
assembled at run time so that no number-shaped literal sits in the source. Made-up names only.

    <runner venv python> -I -B -m unittest discover -s <worktree>\\src\\verify\\runner\\tests -t <worktree> -p "test_identity*.py"
"""
from __future__ import annotations

import datetime as dt
import shutil
import unittest
from pathlib import Path

from src.verify.runner import botimport as B
from src.verify.runner import identity as I
from src.verify.runner import strict as S
from src.verify.runner.model import (FLAG, NOTE, BirthFacts, DerivedRecord, DocUnit, Identity, NameRef,
                                     PageRead)

WORK = B.PRIVATE / "tmp" / "test_identity"
PNO = "ZX" + "".join(str(d) for d in range(1, 8))            # 2 letters + 7 digits, made up
DOB, EXP = "000101", "300101"                                # 2000-01-01, 2030-01-01


def setUpModule():
    B.load(B._loaded.cache_root if B._loaded is not None else B.DEFAULT_CACHE_ROOT)


def cd(s: str) -> str:
    """ICAO 9303 check digit (weights 7, 3, 1; '<' = 0, A = 10 ...)."""
    total = 0
    for i, ch in enumerate(s):
        v = int(ch) if ch.isdigit() else (ord(ch) - 55 if ch.isalpha() else 0)
        total += v * (7, 3, 1)[i % 3]
    return str(total % 10)


def line1(surname: str, given: str) -> str:
    return ("P<BGD" + surname + "<<" + "<".join(given.split())).ljust(44, "<")[:44]


def line2(number: str = PNO, dob: str = DOB, sex: str = "M", expiry: str = EXP, bad: str = "") -> str:
    num = number.ljust(9, "<")
    pers = "<" * 14
    c_num, c_dob, c_exp, c_pers = cd(num), cd(dob), cd(expiry), "0"
    if bad == "dob":
        c_dob = str((int(c_dob) + 1) % 10)
    if bad == "expiry":
        c_exp = str((int(c_exp) + 1) % 10)
    comp = cd(num + c_num + dob + c_dob + expiry + c_exp + pers + c_pers)
    return num + c_num + "BGD" + dob + c_dob + sex + expiry + c_exp + pers + c_pers + comp


L1 = line1("SAMPLE", "MD EXAMPLE")


def passport_text(mrz: str = "", given: str = "MD. EXAMPLE", parents: bool = True, extra: str = "") -> str:
    parts = ["PEOPLE'S REPUBLIC PASSPORT Type P Country Code BGD Passport No. " + PNO,
             f"Surname SAMPLE Given Name(s) {given} Nationality BANGLADESHI",
             "Date of Birth 01 JAN 2000 Sex M Place of Birth PLACEHOLDER Date of Issue 02 JAN 2020", extra]
    if parents:
        parts.append("Father's Name TESTER SAMPLE Mother's Name DEMO SAMPLE Spouse Name")
    parts.append(mrz)
    return "\n".join(p for p in parts if p)


def page(i: int, text: str, sideways: bool = False) -> PageRead:
    return PageRead(i, "BUNDLE.pdf", i, text, "ocr", len(text), sideways, 0.0, False, "cpu")


class Case:
    def __init__(self):
        self.pages, self.units = [], []

    def add(self, key, texts, role="", role_how=""):
        idx = []
        for t in texts:
            self.pages.append(page(len(self.pages), t))
            idx.append(len(self.pages) - 1)
        u = DocUnit(uid=f"D{len(self.units) + 1:02d}", key=key, fine=key, title=key.upper(), pages=idx, role=role,
                    role_how=role_how)
        self.units.append(u)
        return u


def build(case: Case, births=None, engine=None, confirm=None) -> Identity:
    return I.build_identity(case.units, case.pages, births or {}, engine, WORK, confirm)


def rules(ident: Identity):
    return sorted((n.rule, n.verdict) for n in ident.notes)


class TestMrzFromWords(unittest.TestCase):
    def test_line2_from_its_own_word(self):
        m = I.mrz_from_words("noise " + L1 + " " + line2() + " more")
        self.assertTrue(m["line2_ok"])
        self.assertEqual((m["dob"], m["expiry"], m["passport_no"]), ("2000-01-01", "2030-01-01", PNO))
        self.assertTrue(m["line1_ok"])
        for k in ("passport_no", "passport_no_ok", "dob", "dob_ok", "expiry", "expiry_ok", "sex", "line2_ok",
                  "checks", "name"):
            self.assertIn(k, m)

    def test_both_lines_joined_into_one_word(self):          # A6 a
        m = I.mrz_from_words("PASSPORT " + L1 + line2() + " end")
        self.assertTrue(m["line2_ok"])
        self.assertTrue(m["line1_ok"])
        self.assertEqual(m["checks"], 4)

    def test_line2_split_in_two_words(self):
        l2 = line2()
        m = I.mrz_from_words(L1 + " " + l2[:20] + " " + l2[20:])
        self.assertTrue(m["line2_ok"])

    def test_guillemets_and_noise(self):
        l2 = line2().replace("<<<<", "«<<", 1)
        self.assertTrue(I.mrz_from_words(l2.lower().replace("bgd", "BGD"))["line2_ok"])

    def test_nothing(self):
        self.assertEqual(I.mrz_from_words("no machine readable zone here"), {})


class TestBuildIdentity(unittest.TestCase):
    def tearDown(self):
        shutil.rmtree(WORK, ignore_errors=True)

    def test_trusted_dob_and_expiry(self):
        c = Case()
        c.add("passport", [passport_text(L1 + "\n" + line2())])
        ident = build(c)
        self.assertEqual((ident.dob, ident.dob_source, ident.dob_trusted), (dt.date(2000, 1, 1), "MRZ", True))
        self.assertEqual((ident.expiry, ident.expiry_source, ident.expiry_trusted), (dt.date(2030, 1, 1), "MRZ", True))
        self.assertEqual((ident.passport_no, ident.passport_no_ok, ident.printed_passport_no), (PNO, True, PNO))
        self.assertTrue(ident.mrz["line2_ok"])
        self.assertEqual(ident.sex, "MALE")
        self.assertNotIn("identity.mrz_unread", [r for r, _ in rules(ident)])
        self.assertNotIn("identity.mrz_reread", [r for r, _ in rules(ident)])

    def test_joined_lines_trusted(self):                      # A6 a
        for mrz in (L1 + line2(), L1 + " " + line2()[:25] + " " + line2()[25:]):
            with self.subTest(mrz=len(mrz)):
                c = Case()
                c.add("passport", [passport_text(mrz)])
                ident = build(c)
                self.assertTrue(ident.dob_trusted and ident.expiry_trusted)
                self.assertEqual(ident.dob_source, "MRZ")
                self.assertTrue(ident.name_trusted)

    def test_bad_dob_check_digit(self):
        c = Case()
        c.add("passport", [passport_text(L1 + "\n" + line2(bad="dob"))])
        ident = build(c)
        self.assertFalse(ident.dob_trusted)
        self.assertEqual((ident.dob, ident.dob_source), (dt.date(2000, 1, 1), "passport label"))
        self.assertTrue(ident.expiry_trusted)                 # 2+ other check digits agree

    def test_bad_expiry_check_digit(self):
        c = Case()
        c.add("passport", [passport_text(L1 + "\n" + line2(bad="expiry"), extra="Date of Expiry 01 JAN 2030")])
        ident = build(c)
        self.assertEqual((ident.expiry_source, ident.expiry_trusted), ("passport label", True))
        c = Case()
        c.add("passport", [passport_text(L1 + "\n" + line2(bad="expiry"), extra="valid 01 JAN 2030")])
        ident = build(c)
        self.assertEqual((ident.expiry, ident.expiry_source, ident.expiry_trusted),
                         (dt.date(2030, 1, 1), "scan date", False))

    def test_no_mrz(self):
        c = Case()
        c.add("passport", [passport_text("")])
        ident = build(c)
        self.assertIn(("identity.mrz_unread", FLAG), rules(ident))
        self.assertIn(("identity.mrz_name_unread", FLAG), rules(ident))
        self.assertEqual(ident.student.source, "passport printed")
        self.assertEqual(ident.student.written, "MD. EXAMPLE SAMPLE")
        self.assertFalse(ident.student.confirmed)

    def test_truncated_name_field(self):
        long1 = line1("SAMPLEFAMILYNAMEPART", "EXAMPLEGIVENNAMEPARTS")
        self.assertNotIn("<<<", long1[5:])
        c = Case()
        c.add("passport", [passport_text(long1 + "\n" + line2(), given="EXAMPLEGIVENNAMEPARTSX")])
        ident = build(c)
        self.assertTrue(ident.student.truncated)
        self.assertIn(("identity.mrz_truncated", FLAG), rules(ident))

    def test_printed_name_keeps_the_dot(self):
        c = Case()
        u = c.add("passport", [passport_text(L1 + "\n" + line2())])
        ident = build(c)
        st = ident.student
        self.assertEqual(st.parts, ("SAMPLE", "MD", "EXAMPLE"))
        self.assertEqual(st.written, "MD. EXAMPLE SAMPLE")
        self.assertEqual(S.dot_pattern(st.written), (2,))
        self.assertTrue(st.confirmed)
        self.assertEqual((st.source, st.unit), ("passport MRZ", u.uid))
        self.assertEqual((ident.surname, ident.given_names), ("SAMPLE", "MD EXAMPLE"))
        self.assertTrue(ident.name_trusted and ident.name_verified)

    def test_printed_dot_read_as_another_mark(self):
        c = Case()
        c.add("passport", [passport_text(L1 + "\n" + line2(), given="MD:EXAMPLE")])
        self.assertEqual(build(c).student.written, "MD.EXAMPLE SAMPLE")

    def test_printed_name_differs_from_mrz(self):
        for answer, written, flagged in (("refuted", "", True), ("confirmed", "MD. EXAMPLA SAMPLE", True)):
            with self.subTest(answer=answer):
                c = Case()
                c.add("passport", [passport_text(L1 + "\n" + line2(), given="MD. EXAMPLA")])
                calls = []
                ident = build(c, confirm=lambda p, v: calls.append((p, v)) or answer)
                self.assertEqual(calls[0], (0, "MD. EXAMPLA SAMPLE"))
                self.assertEqual([v for _, v in calls].count("MD. EXAMPLA SAMPLE"), 1)   # asked once
                self.assertEqual(ident.student.written, written)
                self.assertEqual(("identity.printed_name", FLAG) in rules(ident), flagged)

    def test_parents_from_birth_certificate(self):
        c = Case()
        c.add("passport", [passport_text(L1 + "\n" + line2())])
        bc = c.add("birth_cert", ["NOISEWORD BANGLA PAGE Name JUNKA JUNKB",
                                  "BIRTH REGISTRATION CERTIFICATE Name MD. EXAMPLE SAMPLE Father's Name TESTER SAMPLE "
                                  "Father's Nationality Bangladeshi Mother's Name DEMO SAMPLE Mother's Nationality "
                                  "Bangladeshi"])
        births = {tuple(bc.pages): BirthFacts(pages=tuple(bc.pages), english_pages=[bc.pages[1]])}
        ident = build(c, births)
        for person, name in (("father", "TESTER SAMPLE"), ("mother", "DEMO SAMPLE")):
            ref = getattr(ident, person)
            self.assertEqual((ref.written, ref.source, ref.unit), (name, "birth certificate", bc.uid))
            self.assertEqual(ref.parts, S.words(name))
            self.assertTrue(ref.confirmed)                    # the passport field prints it too
        self.assertNotIn("identity.parents_from_passport", [r for r, _ in rules(ident)])

    def test_no_birth_certificate_uses_passport_fields(self):
        c = Case()
        c.add("passport", [passport_text(L1 + "\n" + line2())])
        ident = build(c)
        self.assertEqual((ident.father.written, ident.father.source), ("TESTER SAMPLE", "passport field"))
        self.assertEqual((ident.mother.written, ident.mother.source), ("DEMO SAMPLE", "passport field"))
        self.assertEqual(rules(ident).count(("identity.parents_from_passport", NOTE)), 2)

    def test_no_parents_anywhere(self):
        c = Case()
        c.add("passport", [passport_text(L1 + "\n" + line2(), parents=False)])
        ident = build(c)
        self.assertEqual(rules(ident).count(("identity.parents_unknown", FLAG)), 2)
        self.assertEqual(ident.father, NameRef())

    def test_passport_without_parents_page_plus_birth_certificate(self):     # critique 4
        c = Case()
        c.add("passport", [passport_text(L1 + "\n" + line2(), parents=False)])
        bc = c.add("birth_cert", ["Name MD. EXAMPLE SAMPLE Father's Name TESTER SAMPLE NOISEA NOISEB "
                                  "Mother's Name DEMO SAMPLE NOISEC NOISED"])
        c.add("nid_unassigned", ["NATIONAL ID CARD Name TESTER SAMPLE Date of Birth 01 Jan 1970"])
        c.add("nid_unassigned", ["NATIONAL ID CARD Name DEMO SAMPLE Date of Birth 01 Jan 1975"])
        births = {tuple(bc.pages): BirthFacts(pages=tuple(bc.pages), english_pages=list(bc.pages))}
        ident = build(c, births)
        self.assertEqual((ident.father.written, ident.father.source), ("TESTER SAMPLE", "birth certificate"))
        self.assertEqual((ident.mother.written, ident.mother.source), ("DEMO SAMPLE", "birth certificate"))
        self.assertTrue(ident.father.confirmed and ident.mother.confirmed)
        self.assertNotIn("identity.parents_unknown", [r for r, _ in rules(ident)])

    def test_birth_certificate_dob_is_an_untrusted_fallback(self):
        c = Case()
        c.add("passport", ["PASSPORT page without dates"])
        bc = c.add("birth_cert", ["Name SAMPLE"])
        births = {tuple(bc.pages): BirthFacts(pages=tuple(bc.pages), dob=dt.date(2000, 1, 1), dob_trusted=True)}
        ident = build(c, births)
        self.assertEqual((ident.dob, ident.dob_source, ident.dob_trusted),
                         (dt.date(2000, 1, 1), "birth certificate", False))

    def test_refs(self):
        c = Case()
        c.add("passport", [passport_text(L1 + "\n" + line2())])
        r = I.refs(build(c))
        self.assertEqual(list(r.father), ["TESTER", "SAMPLE"])
        self.assertEqual(list(r.mother), ["DEMO", "SAMPLE"])
        self.assertEqual(list(r.student), ["MD", "EXAMPLE", "SAMPLE"])

    def test_portal_view_and_language(self):
        c = Case()
        c.add("passport", [passport_text(L1 + "\n" + line2(bad="dob"))])
        ident = build(c)
        view = DerivedRecord("KLP", dt.date(2026, 1, 1), ident).portal_view()
        self.assertEqual({k: v for k, v in view.items() if v}, {"Program": "KLP"})
        lang = I.passport_for_language(ident)
        self.assertIsNone(lang["dob"])                        # untrusted (critique 12)
        self.assertTrue(lang["name_trusted"])
        self.assertEqual(set(lang), {"surname", "given_names", "passport_no", "dob", "name_verified", "name_trusted",
                                     "printed_name", "printed_confirmed", "closer"})     # owner 28 Oct
        self.assertIsNone(lang["closer"])                    # no closer look without the real recogniser
        c = Case()
        c.add("passport", [passport_text(L1 + "\n" + line2())])
        self.assertEqual(I.passport_for_language(build(c))["dob"], dt.date(2000, 1, 1))


class FakeEngine:
    """A page image and an OCR text for the zoomed MRZ crop."""

    def __init__(self, text):
        import numpy as np
        self.np, self.text, self.calls = np, text, []

    def page_image_of(self, page, dpi=300):
        return self.np.full((400, 300, 3), 255, dtype=self.np.uint8)

    def read_text(self, img):
        self.calls.append(img.shape)
        return self.text

    def read_boxes(self, img):
        return []


class TestReread(unittest.TestCase):
    def tearDown(self):
        shutil.rmtree(WORK, ignore_errors=True)

    def test_reread_decides(self):
        c = Case()
        c.add("passport", [passport_text("P<BGDSAMPLE<<MD<EXAMPLE noisy unreadable zone")])
        eng = FakeEngine(L1 + "\n" + line2())
        ident = build(c, engine=eng)
        self.assertEqual((ident.dob_source, ident.dob_trusted), ("MRZ re-read", True))
        self.assertEqual((ident.expiry_source, ident.expiry_trusted), ("MRZ re-read", True))
        self.assertIn(("identity.mrz_reread", NOTE), rules(ident))
        self.assertEqual(eng.calls[0], (280, 600, 3))         # bottom 35% of 400 x 300, scaled x2
        self.assertTrue((WORK / "mrz_p01.png").is_file())
        self.assertTrue(ident.mrz["crop"].endswith("mrz_p01.png"))

    def test_no_reread_when_trusted(self):
        c = Case()
        c.add("passport", [passport_text(L1 + "\n" + line2())])
        eng = FakeEngine("")
        ident = build(c, engine=eng)
        self.assertEqual(eng.calls, [])
        self.assertNotIn("identity.mrz_reread", [r for r, _ in rules(ident)])

    def test_reread_mrz_direct(self):
        pages = [page(0, "x", sideways=True)]
        m = I.reread_mrz(FakeEngine(L1 + "\n" + line2()), pages, 0, WORK)
        self.assertTrue(m["line2_ok"])
        self.assertIn("_text", m)


class TestFinishIdentity(unittest.TestCase):
    def case(self, father_how="name", mother_how="position", parents=True):
        c = Case()
        c.add("passport", [passport_text(L1 + "\n" + line2(), parents=parents)])
        c.add("father_nid", ["NATIONAL ID CARD Name TESTER SAMPLE Date of Birth 01 Jan 1970",
                             "Translation Name TESTER SAMPLE Father LATE ELDER SAMPLE"], "father", father_how)
        c.add("mother_nid", ["NATIONAL ID CARD Name DEMO SAMPLE Date of Birth 01 Jan 1975"], "mother", mother_how)
        return c

    def test_role_confirmed_nid_names(self):
        c = self.case()
        ident = I.finish_identity(build(c), c.units, c.pages, {})
        self.assertEqual((ident.father_nid.written, ident.father_nid.source), ("TESTER SAMPLE", "father NID"))
        self.assertTrue(ident.father_nid.confirmed)            # two pages print it
        self.assertEqual(ident.mother_nid, NameRef())          # position-only role: never a reference
        self.assertEqual(ident.father.source, "passport field")

    def test_unknown_parents_take_the_nid_name(self):
        c = self.case(father_how="file-name", mother_how="position", parents=False)
        ident = build(c)
        self.assertEqual(rules(ident).count(("identity.parents_unknown", FLAG)), 2)
        ident = I.finish_identity(ident, c.units, c.pages, {})
        self.assertEqual((ident.father.written, ident.father.source), ("TESTER SAMPLE", "father NID"))
        self.assertEqual(ident.mother, NameRef())
        self.assertIn(("identity.parents_from_nid", NOTE), rules(ident))
        self.assertEqual(rules(ident).count(("identity.parents_unknown", FLAG)), 1)


# ============================================================================================ owner 28 Oct
class CloserFake:
    """A closer-mode confirmer: `truth` maps a run page to the spellings it prints (letters compared); a look picks
    the candidate printed there, else "unclear"."""
    closer = True

    def __init__(self, truth):
        self.truth, self.calls, self.log = truth, [], []

    def look(self, page, candidates, kind="name"):
        from src.verify.runner import closer
        self.calls.append((page, tuple(candidates)))
        printed = [S.letters(t) for t in self.truth.get(page, ())]
        hit = next((c for c in candidates if S.letters(c) in printed), None)
        if hit is None:
            return closer.Look("unclear", candidates[0], {}, 0.0, 10, 0, None, "test")
        return closer.Look("same" if hit == candidates[0] else "differs", hit, {}, 0.5, 10, 10, None, "test")

    def pair(self, page, reference, variant, kind="name"):
        return self.look(page, [reference, variant], kind).decision

    def __call__(self, page_index, variant, reference=""):
        return "unclear"


BC_TEXT = ("BIRTH REGISTRATION CERTIFICATE Name MD. EXAMPLE SAMPLE Father's Name TESTER SAMPLE NOR Father's "
           "Nationality Bangladeshi Mother's Name DEMO SAMPLX Mother's Nationality Bangladeshi")


class TestCloserLook(unittest.TestCase):
    def setUp(self):
        shutil.rmtree(WORK, ignore_errors=True)

    def case(self):
        c = Case()
        c.add("passport", [passport_text(L1 + "\n" + line2(), parents=False)])
        bc = c.add("birth_cert", [BC_TEXT])
        nid = c.add("mother_nid", ["NATIONAL ID CARD Name DEMO SAMPLE Father TESTER OLD Date of Birth"],
                    role="mother", role_how="name")
        fam = c.add("family_cert", ["FAMILY CERTIFICATE Father's Name TESTER SAMPLE Mother's Name DEMO SAMPLE "
                                    "Date of Issue"])
        births = {tuple(bc.pages): BirthFacts(pages=tuple(bc.pages), english_pages=list(bc.pages))}
        return c, bc, births

    def test_a_misread_reference_is_corrected_by_the_look_on_its_own_page(self):
        c, bc, births = self.case()
        conf = CloserFake({bc.pages[0]: ["DEMO SAMPLE", "TESTER SAMPLE"]})
        ident = I.finish_identity(build(c, births, confirm=conf), c.units, c.pages, births, conf)
        self.assertEqual(ident.mother.written, "DEMO SAMPLE")
        self.assertIn(("identity.reference_corrected", NOTE), rules(ident))

    def test_an_unclear_look_never_corrects_by_counting_documents(self):
        c, bc, births = self.case()
        conf = CloserFake({})
        ident = I.finish_identity(build(c, births, confirm=conf), c.units, c.pages, births, conf)
        self.assertEqual(ident.mother.written, "DEMO SAMPLX")                # two documents agree: no outvote
        self.assertNotIn(("identity.reference_corrected", NOTE), rules(ident))

    def test_a_tail_the_look_does_not_see_is_dropped(self):
        c, bc, births = self.case()
        conf = CloserFake({bc.pages[0]: ["TESTER SAMPLE"]})
        ident = build(c, births, confirm=conf)
        self.assertEqual((ident.father.written, ident.father.tail), ("TESTER SAMPLE", ()))
        self.assertIn(("identity.tail_not_printed", NOTE), rules(ident))
        conf2 = CloserFake({bc.pages[0]: ["TESTER SAMPLE NOR"]})
        self.assertEqual(build(c, births, confirm=conf2).father.written, "TESTER SAMPLE NOR")
        self.assertEqual(build(c, births, confirm=CloserFake({})).father.tail, ("NOR",))   # unclear: kept, FLAG later

    def test_a_noisy_mrz_name_line_is_verified_by_the_look(self):
        c = Case()
        noisy = ("P<BGDSAMPLE<<MD<EXAMPLE<<<<<<<<<KKKCCX<<<<<<<<<<<")[:44]
        c.add("passport", [passport_text("P<BGDSAMQLE<<MD<EXAMPLE<KK<<C<<\n" + line2())])
        plain = build(c)
        conf = CloserFake({0: ["SAMPLE MD EXAMPLE"]})
        ident = build(c, confirm=conf)
        self.assertTrue(ident.name_trusted)
        self.assertEqual(S.letters(ident.student.parts), S.letters("SAMPLE MD EXAMPLE"))
        self.assertEqual(ident.student.written, "MD. EXAMPLE SAMPLE")            # the printed name: dot reference
        self.assertEqual(ident.surname, "SAMPLE")
        self.assertTrue(noisy and plain is not None)

    def test_language_gets_the_printed_name_and_the_look(self):
        c, bc, births = self.case()
        conf = CloserFake({})
        ident = I.finish_identity(build(c, births, confirm=conf), c.units, c.pages, births, conf)
        lang = I.passport_for_language(ident)
        self.assertEqual(lang["printed_name"], "MD. EXAMPLE SAMPLE")
        self.assertTrue(callable(lang["closer"]))

    def test_the_language_look_is_taken_on_the_certificate_page(self):
        """The certificate's word is looked at on the language certificate's own page (its read may come from a
        separate row read, so the page is not searched by text alone), the passport's word on the passport page."""
        c, bc, births = self.case()
        lc = c.add("language_cert", ["IELTS Test Report Form Family Name SAMPLX First Name MD EXAMPLE"])
        conf = CloserFake({lc.pages[0]: ["SAMPLE"], 0: ["SAMPLE"]})
        ident = I.finish_identity(build(c, births, confirm=conf), c.units, c.pages, births, conf)
        fn = I.passport_for_language(ident)["closer"]
        self.assertEqual(fn("SAMPLX", "SAMPLE"), ("same", "same"))           # a misread on the certificate
        self.assertIn((lc.pages[0], ("SAMPLE", "SAMPLX")), conf.calls)
        conf.truth[lc.pages[0]] = ["SAMPLX"]
        self.assertEqual(fn("SAMPLX", "SAMPLE"), ("differs", "same"))        # printed so: a confirmed difference


if __name__ == "__main__":
    unittest.main()
