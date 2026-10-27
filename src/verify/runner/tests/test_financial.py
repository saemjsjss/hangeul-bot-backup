"""Task 4 tests for financial.py (RUNNER_SPEC 5.9.4; section 11 Task 4): the strict chain taxpayer = holder =
owner/employee (A1, A7), the whole-value owner row (critique 3), "MD <A> <B>" vs "<A> <B>" through every
comparison point of Task 4 (critique 2), the chain's placement (critique 11), the NEW tax-office check
(A6 j) and the ban on the loose name helpers.

Made-up placeholder names only ("SAMPLE ONE", "ALPHA BRAVO"); "MD" is the abbreviation the owner's rules talk
about. No personal data.
"""
from __future__ import annotations

import re
import tempfile
import unittest
from pathlib import Path

from src.verify.runner import bank as Bk
from src.verify.runner import botimport as B
from src.verify.runner import financial as F
from src.verify.runner import strict as S
from src.verify.runner import tin as Q
from src.verify.runner.model import FAIL, FLAG, NOTE, PASS, DocUnit, Identity, NameRef

RUNNER_DIR = Path(__file__).resolve().parents[1]
TIN_U = DocUnit("D08", "trade_license", "tin_certificate", "TIN CERTIFICATE", [10])
TL_U = DocUnit("D09", "trade_license", "trade_license", "TRADE LICENSE", [11, 12])
EMP_U = DocUnit("D10", "trade_license", "employment_certificate", "EMPLOYMENT CERTIFICATE", [13])
BANK_U = DocUnit("D07", "bank", "bank", "BANK STATEMENT", [8, 9])


def setUpModule():
    B.load(B._loaded.cache_root if B._loaded is not None else B.DEFAULT_CACHE_ROOT)


def ident() -> Identity:
    return Identity(student=NameRef(parts=("HOTEL", "GOLF"), written="GOLF HOTEL", confirmed=True),
                    father=NameRef(parts=("MD", "SAMPLE", "ONE"), written="MD. SAMPLE ONE", source="birth certificate",
                                   confirmed=True),
                    mother=NameRef(parts=("ECHO", "FOXTROT"), written="ECHO FOXTROT", source="birth certificate",
                                   confirmed=True))


def holder(name, page=0, suspect=False):
    return Bk.Holder(name, "solvency:certify", page, suspect)


def chain(tp_name="SAMPLE ONE", source="QR", owner="Proprietor's Name: SAMPLE ONE Address: Road 1", hold="SAMPLE ONE",
          units=("tin", "tl"), covered=None, confirm=None, emp_text=None):
    calls = []

    def conf(i, v):
        calls.append((i, v))
        return confirm(i, v) if confirm else "unclear"
    tp = {"name": tp_name, "source": source, "qr_page": 10 if source == "QR" else None,
          "printed_page": 10 if source == "PRINTED" else None,
          "printed": tp_name if source == "PRINTED" else ""} if tp_name else {}
    owners, texts = [], {}
    if "tl" in units:
        owners.append(TL_U)
        texts["D09"] = owner
    if "emp" in units:
        owners.append(EMP_U)
        texts["D10"] = emp_text if emp_text is not None else \
            "This is to certify that SAMPLE ONE, son of Late Kilo Lima, has been working as Officer since 2019."
    out = F.chain_findings(tp, TIN_U if "tin" in units else None, owners, texts,
                           holder(hold) if hold else None, BANK_U if "bank" not in units or True else None,
                           ident(), covered or (lambda a, b: False), conf)
    out = {k: v for k, v in out.items()}
    out["_calls"] = calls
    return out


def rule(out, uid, r):
    return [f.verdict for f in out.get(uid, []) if f.rule == r]


class TestJudgingTable(unittest.TestCase):
    """EQUAL -> PASS; LETTERS / SUBSET / ORDER / OCR -> FLAG unconfirmed, FAIL confirmed; covered -> NOTE."""
    VARIANTS = {"LETTERS": "SAMPLE ONNE", "SUBSET": "MD SAMPLE ONE", "ORDER": "ONE SAMPLE", "OCR": "SAMPLE 0NE",
                "DIFFERENT": "KILO LIMA"}

    def test_equal_pass(self):
        out = chain()
        self.assertEqual(rule(out, "D08", "financial.taxpayer_owner"), [PASS])
        self.assertEqual(rule(out, "D08", "financial.taxpayer_holder"), [PASS])

    def test_kinds_flag_unconfirmed_fail_confirmed(self):
        for kind, v in self.VARIANTS.items():
            self.assertEqual(S.compare("SAMPLE ONE", v, dots=False), kind)
            owner = f"Proprietor's Name: {v} Address: Road 1"
            out = chain(owner=owner)
            self.assertEqual(rule(out, "D08", "financial.taxpayer_owner"), [FLAG], kind)
            out = chain(owner=owner, confirm=lambda i, x: "confirmed")
            self.assertEqual(rule(out, "D08", "financial.taxpayer_owner"), [FAIL], kind)
            out = chain(owner=owner, confirm=lambda i, x: "confirmed", covered=lambda a, b: True)
            self.assertEqual(rule(out, "D08", "financial.taxpayer_owner"), [NOTE], kind)

    def test_owner_on_two_pages_is_confirmed(self):
        out = chain(owner=["Proprietor's Name: SAMPLE ONNE Address: Road 1", "Proprietor : SAMPLE ONNE"])
        self.assertEqual(rule(out, "D08", "financial.taxpayer_owner"), [FAIL])
        self.assertEqual(out["_calls"], [])

    def test_refuted_is_flag_probably_ocr(self):
        out = chain(owner="Proprietor's Name: SAMPLE ONNE Address: Road 1", confirm=lambda i, x: "refuted")
        f = [f for f in out["D08"] if f.rule == "financial.taxpayer_owner"][0]
        self.assertEqual(f.verdict, FLAG)
        self.assertIn("probably OCR", f.message)

    def test_holder_difference(self):
        self.assertEqual(rule(chain(hold="SAMPLE ONNE"), "D08", "financial.taxpayer_holder"), [FLAG])
        out = chain(hold="SAMPLE ONNE", confirm=lambda i, x: "confirmed")
        self.assertEqual(rule(out, "D08", "financial.taxpayer_holder"), [FAIL])
        self.assertIn((8, "SAMPLE ONNE"), out["_calls"])                          # the bank page, as a run page

    def test_garbled_holder_never_fails(self):
        out = F.chain_findings({"name": "SAMPLE ONE", "source": "QR"}, TIN_U, [], {}, holder("SAMPLE 0NE", suspect=True),
                               BANK_U, ident(), lambda a, b: False, lambda i, v: "confirmed")
        self.assertEqual(rule(out, "D08", "financial.taxpayer_holder"), [FLAG])


class TestOwnerRow(unittest.TestCase):
    def test_contained_in_a_longer_owner_value_is_not_owner(self):
        self.assertEqual(F.owner_slot("Proprietor's Name: MD SAMPLE ONE Address: Road 1", "SAMPLE ONE"), "PARTIAL")
        self.assertEqual(F.owner_slot("Name of Owner: SAMPLE ONE TWO, Vill X", "SAMPLE ONE"), "PARTIAL")

    def test_whole_value_equal_is_owner(self):
        self.assertEqual(F.owner_slot("Proprietor's Name: SAMPLE ONE Address: Road 1", "SAMPLE ONE"), "OWNER")
        self.assertEqual(F.owner_slot("Proprietor: Md: SAMPLE ONE, Vill X", "MD. SAMPLE ONE"), "OWNER")
        self.assertEqual(F.owner_slot("Owner's Name & Address: SAMPLE ONE, Road 1", "SAMPLE ONE"), "OWNER")

    def test_relation_other_absent(self):
        self.assertEqual(F.owner_slot("Father's Name: SAMPLE ONE Address: X", "SAMPLE ONE"), "RELATION")
        self.assertEqual(F.owner_slot("signed by SAMPLE ONE", "SAMPLE ONE"), "OTHER")
        self.assertEqual(F.owner_slot("no such name", "SAMPLE ONE"), "ABSENT")
        self.assertEqual(F.owner_slot("Proprietor: SAMPLE ONE", ""), "ABSENT")

    def test_partial_falls_through_to_judging(self):
        out = chain(owner="Proprietor's Name: MD SAMPLE ONE Address: Road 1")
        self.assertEqual(rule(out, "D08", "financial.taxpayer_owner"), [FLAG])

    def test_best_owner_match(self):
        self.assertEqual(F.best_owner_match("SAMPLE ONE", ["KILO LIMA", "MD SAMPLE ONE"]), ("SUBSET", "MD SAMPLE ONE"))
        self.assertEqual(F.best_owner_match("SAMPLE ONE", ["SAMPLE ONE", "X"]), ("EQUAL", "SAMPLE ONE"))
        self.assertEqual(F.best_owner_match("SAMPLE ONE", []), ("EMPTY", ""))


class TestDroppedMdEverywhere(unittest.TestCase):
    """critique 2: "MD <A> <B>" against "<A> <B>" is never a PASS at any comparison point of Task 4
    (the language point is test_language's)."""
    MD, BARE = "MD ALPHA BRAVO", "ALPHA BRAVO"

    def test_tin_qr_name(self):
        for qr, printed in ((self.MD, self.BARE), (self.BARE, self.MD)):
            c = Q.compare({"name": qr, "tin": "1"}, {"name": printed, "certify_name": printed, "tin": "1"})
            rows = Q.verdicts({"name": qr}, c, True)
            self.assertNotIn(PASS, [v for v, _, r in rows if r == "tin.qr_name"])
            rows = Q.verdicts({"name": qr}, c, True, name_confirmed=True)
            self.assertEqual([v for v, _, r in rows if r == "tin.qr_name"], [FAIL])

    def test_owner_slot(self):
        self.assertNotEqual(F.owner_slot(f"Proprietor's Name: {self.MD} Address: X", self.BARE), "OWNER")
        self.assertNotEqual(F.owner_slot(f"Proprietor's Name: {self.BARE} Address: X", self.MD), "OWNER")

    def test_chain_findings(self):
        for tp, own, hold in ((self.MD, self.BARE, self.BARE), (self.BARE, self.MD, self.MD)):
            out = F.chain_findings({"name": tp, "source": "QR"}, TIN_U, [TL_U, EMP_U],
                                   {"D09": f"Proprietor's Name: {own} Address: X",
                                    "D10": f"This is to certify that {own}, son of Kilo Lima, has been working as Officer"},
                                   holder(hold), BANK_U, ident(), lambda a, b: False, lambda i, v: "unclear")
            got = [f.verdict for f in out["D08"] if f.rule.startswith("financial.taxpayer_")]
            self.assertEqual(len(got), 3)
            self.assertNotIn(PASS, got)

    def test_bank_holder(self):
        idn = Identity(student=NameRef(parts=("HOTEL", "GOLF")),
                       father=NameRef(parts=("MD", "ALPHA", "BRAVO"), written=self.MD, confirmed=True),
                       mother=NameRef(), father_nid=NameRef())
        page = ("TO WHOM IT MAY CONCERN This is to certify that Alpha Bravo, S/O Kilo Lima, has been maintaining a "
                "Savings Account with our branch. Balance BDT 25,00,000.00." + " Sample branch text." * 15)
        out = Bk.check_bank_unit(BANK_U, [page], [8], idn, lambda a, b: False, lambda i, v: "unclear", lambda i: None)
        self.assertEqual([f.verdict for f in out.findings if f.rule == "bank.holder_parent"], [FLAG])
        idn = Identity(student=NameRef(parts=("HOTEL", "GOLF")),
                       father=NameRef(parts=("ALPHA", "BRAVO"), written=self.BARE, confirmed=True), mother=NameRef())
        out = Bk.check_bank_unit(BANK_U, [page.replace("Alpha Bravo,", "Md Alpha Bravo,")], [8], idn,
                                 lambda a, b: False, lambda i, v: "unclear", lambda i: None)
        self.assertNotIn(PASS, [f.verdict for f in out.findings if f.rule == "bank.holder_parent"])


class TestChainPlacement(unittest.TestCase):
    def test_print_only_taxpayer_flag(self):
        out = chain(source="PRINTED")
        self.assertEqual(rule(out, "D08", "financial.taxpayer_print_only"), [FLAG])
        self.assertEqual(rule(out, "D08", "financial.taxpayer_owner"), [PASS])

    def test_print_only_difference_needs_both_confirmed(self):
        out = chain(source="PRINTED", owner=["Proprietor's Name: SAMPLE ONNE Address: X", "Proprietor : SAMPLE ONNE"])
        self.assertEqual(rule(out, "D08", "financial.taxpayer_owner"), [FLAG])       # the printed taxpayer unconfirmed
        self.assertIn((10, "SAMPLE ONE"), out["_calls"])                              # asked on the TIN page
        out = chain(source="PRINTED", owner=["Proprietor's Name: SAMPLE ONNE Address: X", "Proprietor : SAMPLE ONNE"],
                    confirm=lambda i, v: "confirmed")
        self.assertEqual(rule(out, "D08", "financial.taxpayer_owner"), [FAIL])

    def test_taxpayer_unread_flag(self):
        out = chain(tp_name="")
        self.assertEqual(rule(out, "D08", "financial.taxpayer_read"), [FLAG])

    def test_owner_findings_echo_to_the_owner_unit(self):
        out = chain(units=("tin", "tl", "emp"))
        self.assertEqual(rule(out, "D09", "financial.taxpayer_owner"), [PASS])
        self.assertEqual(rule(out, "D10", "financial.taxpayer_employee"), [PASS])
        self.assertEqual(rule(out, "D09", "financial.taxpayer_holder"), [])          # the holder rule stays on the TIN
        self.assertEqual(rule(out, "D08", "financial.taxpayer_employee"), [PASS])

    def test_no_tin_unit_goes_to_the_owner_unit(self):
        out = chain(units=("tl",))
        self.assertNotIn("D08", out)
        self.assertEqual(rule(out, "D09", "financial.taxpayer_owner"), [PASS])

    def test_holder_owner_when_no_taxpayer(self):
        out = chain(tp_name="", units=("tl",), hold="SAMPLE ONE")
        self.assertEqual(rule(out, "D09", "financial.holder_owner"), [PASS])
        out = chain(tp_name="", units=("tl",), hold="KILO LIMA", confirm=lambda i, v: "confirmed")
        self.assertEqual(rule(out, "D09", "financial.holder_owner"), [FAIL])
        out = chain(tp_name="", units=("tl",), hold="KILO LIMA")
        self.assertEqual(rule(out, "D09", "financial.holder_owner"), [FLAG])

    def test_neither_goes_to_bank_then_cross_line(self):
        out = F.chain_findings({"name": "SAMPLE ONE", "source": "QR"}, None, [], {}, holder("SAMPLE ONE"), BANK_U,
                               ident(), lambda a, b: False, lambda i, v: "unclear")
        self.assertEqual([f.rule for f in out["D07"]], ["financial.taxpayer_holder"])
        out = F.chain_findings({"name": "SAMPLE ONE", "source": "QR"}, None, [], {}, None, None, ident(),
                               lambda a, b: False, lambda i, v: "unclear")
        self.assertEqual(list(out), [""])
        self.assertEqual([f.verdict for f in out[""]], [NOTE])          # no bank document: its MISSING line carries it

    def test_no_holder_flag(self):
        out = chain(hold="")
        self.assertEqual(rule(out, "D08", "financial.taxpayer_holder"), [FLAG])
        self.assertIn("no solvency holder read", out["D08"][-1].message)

    def test_employee_unread_flag(self):
        out = chain(units=("tin", "emp"), emp_text="Salary statement without a certify sentence")
        self.assertEqual(rule(out, "D10", "financial.taxpayer_employee"), [FLAG])

    def test_check_tin_certificate_result_is_accepted(self):
        res = {"qr": {"name": "SAMPLE ONE", "tin": "1"}, "qr_page": 0, "printed": {"name": "SAMPLE ONE"},
               "findings": [{"rule": "tin.qr_name", "page": 0}]}
        tp = F.taxpayer_from_tin(res, TIN_U)
        self.assertEqual((tp["name"], tp["source"], tp["qr_page"], tp["agree"]), ("SAMPLE ONE", "QR", 10, "EQUAL"))
        out = F.chain_findings(res, TIN_U, [TL_U], {"D09": "Proprietor's Name: SAMPLE ONE Address: X"},
                               holder("SAMPLE ONE"), BANK_U, ident(), lambda a, b: False, lambda i, v: "unclear")
        self.assertEqual(rule(out, "D08", "financial.taxpayer_owner"), [PASS])

    def test_chain_person_reads(self):
        tp = {"name": "MD SAMPLE ONE", "source": "PRINTED", "printed": "Md. Sample One", "printed_page": 10}
        reads = F.chain_person_reads(tp, TIN_U, [TL_U], {"D09": ["x", "Proprietor's Name: MD SAMPLE ONE Address: X"]},
                                     ident())
        self.assertEqual(reads, [("father", "D08", "Md. Sample One", 10), ("father", "D09", "MD SAMPLE ONE", 12)])
        tp = {"name": "MD SAMPLE ONE", "source": "QR", "printed": "", "qr_page": 10}
        self.assertEqual(F.chain_person_reads(tp, TIN_U, [], {}, ident()), [])      # a QR carries no dot evidence


class TestTaxOffice(unittest.TestCase):
    TIN = "Taxes Circle-205 (Salary) Taxes Zone 10, Dhaka"
    UNIT = DocUnit("D11", "income_tax", "income_tax", "INCOME TAX", [13, 14, 15])

    def test_different_circle_flag(self):
        out = F.income_tax_findings(self.UNIT, "Return of income ... Circle-210 Zone-10", self.TIN, "KLP")
        self.assertEqual([(f.rule, f.verdict) for f in out], [("income_tax.tax_office", FLAG)])
        self.assertIn("circle 210, zone 10", out[0].message)
        self.assertEqual(out[0].pages, (14, 15, 16))
        self.assertTrue(out[0].evidence["new_check"])

    def test_equal_pass(self):
        out = F.income_tax_findings(self.UNIT, "Circle : 205 Zone : 10", self.TIN, "MASTER")
        self.assertEqual([f.verdict for f in out], [PASS])

    def test_unread_no_row(self):
        self.assertEqual(F.income_tax_findings(self.UNIT, "no office printed", self.TIN, "KLP"), [])
        self.assertEqual(F.income_tax_findings(self.UNIT, "Circle-210", "no office", "KLP"), [])

    def test_zone_only_compared(self):
        out = F.income_tax_findings(self.UNIT, "Taxes Zone 11", self.TIN, "KLP")
        self.assertEqual([f.verdict for f in out], [FLAG])


class TestNoLooseHelpers(unittest.TestCase):
    """None of the loose name helpers test_contract bans (S8) in Task 4's files."""
    FILES = ("bank.py", "tin.py", "financial.py", "tests/test_bank.py", "tests/test_tin.py", "tests/test_financial.py",
             "selftest/sim_money.py")
    BANNED = re.compile("|".join([r"name" + r"_in\(", r"name_" + r"tokens\(", r"\b" + r"tokens\(", r"same_" + r"person\(",
                                  r"disagree" + r"ment\(", r"name_" + r"agreement\("]))
    MATCHER = "Sequence" + "Matcher"

    def test_clean(self):
        hits = []
        for rel in self.FILES:
            p = RUNNER_DIR / rel
            if not p.exists():
                continue
            for n, line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1):
                if self.BANNED.search(line) or (self.MATCHER in line and "# label-match only" not in line):
                    hits.append(f"{rel}:{n}")
        self.assertEqual(hits, [])


if __name__ == "__main__":
    unittest.main()
