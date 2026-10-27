"""Task 2 tests: per-page features (RUNNER_SPEC section 5.3).

Every text here is made-up cue wording; every name a placeholder ("ALPHA SAMPLE"); every number is built
at run time from a seed (no real number, and no digit run in the source). No personal data.

    <runner venv python> -I -B -m unittest discover -s <worktree>\\src\\verify\\runner\\tests -t <worktree> -p "test_pagetype*.py"
"""
from __future__ import annotations

import unittest

from src.verify.runner import botimport as B
from src.verify.runner import pagetype as PT
from src.verify.runner.model import PageRead, PageType

FA, MO, ST = "ALPHA SAMPLE", "BETA SAMPLE", "GAMMA SAMPLE"          # father, mother, student (made-up)
FA2 = "DELTA SAMPLE"                                                # a second made-up person


def num(seed: int, n: int = 10) -> str:
    """A made-up n-digit number (built at run time)."""
    return "".join(str((seed * 7 + i * 3) % 10) for i in range(n))


def nid_tr(name: str = FA, seed: int = 1, dob: str = "01 Jan 1960") -> str:
    return (f"Translated True Copy National ID Card Government of the People's Republic of Bangladesh "
            f"Name: {name} Date of Birth: {dob} NID No: {num(seed)} Election Commission Blood Group")


def nid_card(name: str = FA, seed: int = 1, dob: str = "01 Jan 1960") -> str:
    return (f"National ID Card Government of the People's Republic Name: {name} Date of Birth {dob} "
            f"ID NO {num(seed)} Blood Group Election Commission")


def nid_noname_tr(seed: int = 9, dob: str = "05 May 1955") -> str:
    """An NID translation whose holder name cannot be read (no Name label)."""
    return (f"Translated True Copy National ID Card Government of the People's Republic Date of Birth: {dob} "
            f"NID No: {num(seed)} Election Commission Blood Group")


def nid_noname_card(seed: int = 9, dob: str = "05 May 1955") -> str:
    return f"National ID Card Government of the People's Republic Date of Birth {dob} ID NO {num(seed)} Blood Group"


def death(name: str = FA) -> str:
    return f"Death Registration Certificate Name: {name} Date of Death 01 Jan 2020 Place of Death Cause of Death"


TEXTS = {
    "passport": (f"BANGLADESH PASSPORT Personal Data Passport No Date of Expiry Surname SAMPLE Given Name GAMMA "
                 f"Father's Name: {FA} Mother's Name: {MO} P<BGDSAMPLE<<GAMMA<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<"),
    "photo": "",
    "birth": (f"Birth Registration Certificate Birth Registration Number Date of Registration Registrar Date of "
              f"Issuance Sex Male Place of Birth In Word Name: {ST} Father: {FA} Mother: {MO}"),
    "family": ("Family Relationship Certificate This is to certify that the family consists of the following family "
               "members related to each other relation Self Brother Sister Union Parishad well known"),
    "hsc_cert": ("Board of Intermediate and Secondary Education Higher Secondary Certificate Examination This is to "
                 "certify that the candidate duly passed the examination GPA 5.00"),
    "hsc_tr": ("Higher Secondary Certificate Examination Academic Transcript Mark Sheet Letter Grade Grade Point "
               "Subject Bangla A+ 5.00 English A 4.00 Math A- 3.50 Physics B 3.00"),
    "ssc_cert": ("Board of Intermediate and Secondary Education Secondary School Certificate Examination This is to "
                 "certify that the candidate duly passed the examination GPA 5.00"),
    "ssc_tr": ("Secondary School Certificate Examination Academic Transcript Mark Sheet Letter Grade Grade Point "
               "Subject Bangla A+ 5.00 English A 4.00 Math A- 3.50 Physics B 3.00"),
    "apostille": "Apostille Convention de la Haye This public document has been signed and bears the seal of the office",
    "apostille_hsc": ("Apostille Convention de la Haye This public document Higher Secondary Certificate bears the "
                      "seal of the office"),
    "apostille_ssc": ("Apostille Convention de la Haye This public document Secondary School Certificate bears the "
                      "seal of the office"),
    "solvency": ("Solvency Certificate To whom it may concern This is to certify that the account holder has been "
                 "maintaining an account with our bank and is financially sound USD equivalent"),
    "statement": ("Statement of Account Opening Balance Closing Balance Debit Credit Balance Transaction Withdraw "
                  "Deposit Bank Account"),
    "trade": "Trade License License No Nature of Business Fiscal Year City Corporation Business",
    "tin": ("TIN Certificate Taxpayer Identification Number National Board of Revenue Taxes Circle Registered "
            "Taxpayer Previous TIN"),
    "employment": ("Employment Certificate This is to certify that the employee is working as an officer Designation "
                   "Salary Joined"),
    "income_tax": "Income Tax Return of Income Assessment Year Acknowledgement Total Income Tax Paid Net Wealth",
    "affidavit": ("Affidavit The deponent solemnly declares that both spellings belong to the same person to the "
                  "best of knowledge and belief"),
    "unrecognised": " ".join(["lorem ipsum dolor sit amet consectetur adipiscing elit sed do eiusmod tempor"] * 3),
    # one weak cue (an ECA word, 1.5): under the bar, and leaning to no section (an all-zero page leans to
    # "passport", the lab's stable-sort tie, and joins a passport before it)
    "unrecognised_weak": " ".join(["lorem ipsum dolor sit amet consectetur adipiscing elit sed do eiusmod"] * 3)
                         + " workshop",
    # reads best as an HSC certificate, but as much like a bank statement: aligned inside the bank block
    "academic_in_bank": "Higher Secondary Certificate Examination statement of account opening balance",
}


def page(i: int, text: str, file: str = "bundle.pdf", file_page: int = -1, image_only: bool = False) -> PageRead:
    return PageRead(index=i, file=file, file_page=i if file_page < 0 else file_page, text=text, kind="ocr",
                    chars=len(text), sideways=False, seconds=0.0, image_only=image_only)


class TestFeatures(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.ns = B.load(B._loaded.cache_root if B._loaded is not None else B.DEFAULT_CACHE_ROOT)

    def test_types(self):
        want = {"passport": "passport", "photo": "photo", "birth": "birth_cert", "family": "family_cert",
                "hsc_cert": "academic_certificate", "hsc_tr": "academic_transcript",
                "ssc_cert": "academic_certificate", "ssc_tr": "academic_transcript",
                "apostille": "academic_apostille", "apostille_hsc": "academic_apostille",
                "solvency": "bank_solvency", "statement": "bank_statement", "trade": "trade_license",
                "tin": "tin_certificate", "employment": "employment_certificate", "income_tax": "income_tax",
                "affidavit": "affidavit", "unrecognised": "other"}
        for k, fine in want.items():
            with self.subTest(k):
                self.assertEqual(PT.page_features(TEXTS[k]).best, fine)
        self.assertEqual(PT.page_features(nid_card()).best, "nid")
        self.assertEqual(PT.page_features(death()).best, "death_cert")

    def test_result_is_a_pagetype(self):
        pt = PT.page_features(TEXTS["hsc_cert"])
        self.assertIsInstance(pt, PageType)
        self.assertEqual(pt.index, 0)
        self.assertEqual(pt.score, max(v for k, v in pt.scores.items()))
        self.assertIsInstance(pt.nid_numbers, list)
        self.assertIsInstance(pt.birth_dates, list)

    def test_other_below_the_bar_and_raw_best(self):
        P = self.ns.P
        pt = PT.page_features(TEXTS["unrecognised"])
        self.assertEqual(pt.best, "other")
        self.assertLess(pt.score, P.OTHER_BELOW)
        self.assertEqual(PT.raw_best(pt), "passport")       # all zero: the first type, as the lab's stable sort
        weak = "blood group " + "lorem ipsum dolor sit amet " * 6       # one weak NID cue (1.5)
        pw = PT.page_features(weak)
        self.assertEqual(pw.best, "other")
        self.assertEqual(PT.raw_best(pw), "nid")
        strong = PT.page_features(TEXTS["trade"])
        self.assertEqual(PT.raw_best(strong), strong.best)

    def test_raw_best_equals_the_lab_ranking(self):
        """raw_best() rebuilds the lab's best_fine (ranking with photo 5.0 / 0.0 before any raise)."""
        P = self.ns.P
        for text in list(TEXTS.values()) + [nid_card(), death(), "blood group", "x" * 30, "seal " * 40]:
            sc = P.score_page(text, False)
            sc["photo"] = 5.0 if len(text.strip()) < P.EMPTY_CHARS else 0.0
            lab_best = sorted(sc.items(), key=lambda kv: -kv[1])[0][0]
            self.assertEqual(PT.raw_best(PT.page_features(text)), lab_best, text[:30])

    def test_photo(self):
        self.assertEqual(PT.page_features("").best, "photo")
        specks = "x1 .. ~~ " * 12                              # a scanned photo page: a few OCR specks
        self.assertGreater(len(specks), 20)
        self.assertLess(len(specks), 150)
        self.assertEqual(PT.page_features(specks, image_only=True).best, "photo")
        self.assertGreaterEqual(PT.page_features(specks, image_only=True).scores["photo"], 5.0)
        plain = PT.page_features(specks, image_only=False)
        self.assertEqual(plain.best, "other")
        self.assertEqual(plain.scores["photo"], 3.0)           # the lab's speck raise, not the photo
        self.assertNotEqual(PT.page_features(TEXTS["hsc_cert"] * 2, image_only=True).best, "photo")

    def test_translation_cue(self):
        self.assertTrue(PT.page_features(nid_tr()).translation)
        self.assertFalse(PT.page_features(nid_card()).translation)
        # a bare "translation" stamp on a card scan (mostly not English words) is not a translation
        stamp = "TRANSLATION 7x9 q1 zz 44 k2 b8 0p xq 12 v3 nid"
        self.assertFalse(PT.page_features(stamp).translation)
        typed = "This translation of the card is given in clean English words for the reader here"
        self.assertTrue(PT.page_features(typed).translation)

    def test_nid_numbers_and_birth_dates(self):
        pt = PT.page_features(nid_card(seed=3))
        self.assertEqual(pt.nid_numbers, [num(3)])
        self.assertEqual(pt.birth_dates, ["1960-01-01"])
        long17 = num(4, 17)
        self.assertEqual(PT.nid_numbers(f"nid no {long17}"), {long17[-13:]})
        self.assertEqual(PT.nid_numbers(f"nid no {num(5, 13)}"), {num(5, 13)})
        self.assertEqual(PT.nid_numbers(f"nid no {num(6, 11)}"), set())
        boiler = next(iter(self.ns.R.BOILERPLATE_NUMBERS))
        self.assertEqual(PT.nid_numbers(f"nid no {boiler}"), set())
        self.assertEqual(PT.birth_dates("date of birth 05/06/1990"), {(1990, 6, 5)})

    def test_holder_is_strict_words(self):
        """Initials, MD and two-letter words are kept (strict.words, S8); the first "Name" label wins;
        "Fathers Name" / "Husband Name" are skipped (the lab's guard, kept as it is: see labelled())."""
        pt = PT.page_features("National ID Card Name: Md. A. Sample Father's Name: Alpha Sample")
        self.assertEqual(pt.holder, ["MD", "A", "SAMPLE"])
        pt2 = PT.page_features("Fathers Name: Alpha Sample Husband Name: Delta Sample Name: Beta Sample")
        self.assertEqual(pt2.holder, ["BETA", "SAMPLE"])
        self.assertEqual(PT.labelled(TEXTS["passport"], "father"), [["ALPHA", "SAMPLE"]])
        self.assertEqual(PT.labelled(TEXTS["passport"], "mother"), [["BETA", "SAMPLE"]])
        self.assertEqual(PT.labelled("Name: LATE ALPHA SAMPLE", "name"), [])      # 'late' is a stop word
        # an initial's dot that OCR printed as "_" (integration, test 1: the father's NID lost its role by name)
        self.assertEqual(PT.labelled("NATIONAL ID CARD Name A_ ALPHA SAMPLE Affixed Father", "name"),
                         [["A", "ALPHA", "SAMPLE"]])

    def test_mrz_name(self):
        self.assertEqual(PT.mrz_name(TEXTS["passport"]), ["SAMPLE", "GAMMA"])
        self.assertEqual(PT.mrz_name("no machine zone here"), [])

    def test_levels_and_academic_kinds(self):
        cases = {"hsc_cert": ("HSC", "certificate"), "hsc_tr": ("HSC", "transcript"),
                 "ssc_cert": ("SSC", "certificate"), "ssc_tr": ("SSC", "transcript"),
                 "apostille": (None, "apostille"), "apostille_hsc": ("HSC", "apostille"),
                 "apostille_ssc": ("SSC", "apostille")}
        for k, (lv, kind) in cases.items():
            with self.subTest(k):
                pt = PT.page_features(TEXTS[k])
                self.assertEqual(pt.level, lv)
                self.assertEqual(pt.kind, kind)
        self.assertEqual(PT.page_features("Bachelor of Science degree university").level, "degree")
        self.assertEqual(PT.page_features("Diploma in Engineering polytechnic institute").level, "diploma")

    def test_bank_kinds_and_no_kind(self):
        self.assertEqual(PT.page_features(TEXTS["solvency"]).kind, "solvency")
        self.assertEqual(PT.page_features(TEXTS["statement"]).kind, "statement")
        for k in ("employment", "income_tax", "family", "passport", "trade"):
            with self.subTest(k):
                self.assertEqual(PT.page_features(TEXTS[k]).kind, "")
        # the split reads a page without an academic kind as the lab did: "transcript"
        self.assertEqual(PT.academic_kind(PT.page_features(TEXTS["unrecognised"])), "transcript")
        self.assertEqual(PT.academic_kind(PT.page_features(TEXTS["hsc_cert"])), "certificate")

    def test_fine_to_key(self):
        coarse = dict(self.ns.P.COARSE)
        coarse["other"] = "unrecognised"
        self.assertEqual(PT.FINE_TO_KEY, coarse)

    def test_type_pages_sets_the_index(self):
        pages = [page(i, t) for i, t in enumerate([TEXTS["passport"], "", TEXTS["birth"]])]
        types = PT.type_pages(pages)
        self.assertEqual([t.index for t in types], [0, 1, 2])
        self.assertEqual([t.best for t in types], ["passport", "photo", "birth_cert"])
        img = PT.type_pages([page(0, "x1 .. ~~ " * 12, image_only=True)])
        self.assertEqual(img[0].best, "photo")


if __name__ == "__main__":
    unittest.main()
