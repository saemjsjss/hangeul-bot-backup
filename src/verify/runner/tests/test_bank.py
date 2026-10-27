"""Task 4 tests for bank.py (RUNNER_SPEC 5.9.2; section 11 Task 4): the 55 trap tests of the lab's
test_bank_holder.py (ported unchanged, one test each) and the holder / solvency / dates / USD / seal rules.

Every name and number is a made-up placeholder (NATO-alphabet words, "SAMPLE"); "MD" / "MST" are the
abbreviations the owner's rules talk about. Nothing is written outside private-runner. No personal data.
"""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from src.verify.runner import bank as Bk
from src.verify.runner import botimport as B
from src.verify.runner.model import FAIL, FLAG, NOTE, PASS, DocUnit, Identity, NameRef


def setUpModule():
    # one botimport.load() per test process (the contract test's root, or the default under PRIVATE)
    B.load(B._loaded.cache_root if B._loaded is not None else B.DEFAULT_CACHE_ROOT)


# --- the lab's trap rows (test_bank_holder.py, verbatim) ---------------------------------------------
H = "TO WHOM IT MAY CONCERN "
TAIL = " has been maintaining a Savings Account with our branch. Balance BDT 25,00,000.00."

SOLVENCY = [
    # (case, text, expected name or None, expected joint list)
    ("Pubali Father's/Mother's Name", H + "This is to certify that Md. Alpha Bravo, Father's Name: Charlie Delta and "
     "Mother's Name: Echo Foxtrot, having address # Northpara," + TAIL, "Md. Alpha Bravo"),
    ("Father's Name, no comma", H + "This is to certify that Alpha Bravo Father's Name: Charlie Delta" + TAIL, "Alpha Bravo"),
    ("S/O", H + "This is to certify that Alpha Bravo S/O Charlie Delta & Echo Foxtrot, Address: X" + TAIL, "Alpha Bravo"),
    ("S/9 OCR", H + "This is to certify that Alpha Bravo S/9 Charlie Delta Mst: Echo," + TAIL, "Alpha Bravo"),
    ("SIO OCR", H + "This is to certify that MD_ Alpha Bravo SIO- LATE Charlie Delta & Echo" + TAIL, "MD Alpha Bravo"),
    ("Slo OCR", H + "Certify that Alpha Bravo (Sierra), Slo. Charlie Delta and Echo Foxtrot," + TAIL, "Alpha Bravo"),
    ("S'o OCR", H + "We do hereby certify that Md Alpha Bravo; S'o. Late Charlie Delta" + TAIL, "Md Alpha Bravo"),
    ("9/O OCR", H + "hereby certify thot, Mohammad Alpha Bravo 9/O- Lote Charlie Delta hoving" + TAIL, "Mohammad Alpha Bravo"),
    ("S 0- OCR", H + "This is to certify that Alpha Bravo: S 0- Charlie Delta & Echo Foxtrot." + TAIL, "Alpha Bravo"),
    ("D/O", H + "This is to certify that Mst Echo Foxtrot D/O of Juliet Kilo and Mst Lima Mike Spouse Alpha Bravo." + TAIL, "Mst Echo Foxtrot"),
    ("Dlo OCR", H + "This is l9 certify that Echo Foxtrot; Dlo Juliet Kilo & Lima Mike Address" + TAIL, "Echo Foxtrot"),
    ("W/O", H + "THIS IS TO CERTIFY THAT ECHO FOXTROT W/O: LATE ALPHA BRAVO, ADDRESS: X" + TAIL, "ECHO FOXTROT"),
    ("WIO OCR", H + "This is to certify that Echo Foxtrot WIO. Alpha Bravo S/O. Kilo Lima" + TAIL, "Echo Foxtrot"),
    ("W / 0 OCR", H + "This is to certify that Ms. Echo Foxtrot, W / 0- Alpha Bravo, National ID No 1" + TAIL, "Ms. Echo Foxtrot"),
    ("son of", H + "This is to certify that Md. Alpha Bravo, Son of Charlie Delta & Echo Foxtrot;" + TAIL, "Md. Alpha Bravo"),
    ("daughter of", H + "This is to certify that Echo Foxtrot, daughter of Juliet Kilo and Lima Mike," + TAIL, "Echo Foxtrot"),
    ("son/daughter of", H + "This is to certify that Md. Alpha Bravo Son/Daughter of Charlie Delta" + TAIL, "Md. Alpha Bravo"),
    ("sonldaughter OCR", H + "This is to certify that MD: Alpha Bravo sonldaughter of LATE Charlie" + TAIL, "MD: Alpha Bravo"),
    ("wife of", H + "This is to certify that Mrs. Echo Foxtrot wife of Alpha Bravo" + TAIL, "Mrs. Echo Foxtrot"),
    ("husband of", H + "This is to certify that Alpha Bravo husband of Echo Foxtrot" + TAIL, "Alpha Bravo"),
    ("F/O student", H + "This is to certify that MD Alpha Bravo, NID Number: 123, F/O: Golf Hotel, S/O: X" + TAIL, "MD Alpha Bravo"),
    ("Father of student", H + "This is to certify that; Alpha Bravo Father of Golf Hotel having present address" + TAIL, "Alpha Bravo"),
    ("Falher of OCR", H + "This is t9 certify that Mr: MD. Alpha Bravo Falher of Mr: Golf Hotel having" + TAIL, "Mr: MD. Alpha Bravo"),
    ("mother of student", H + "This is to certify that Echo Foxtrot mother of Golf Hotel having address" + TAIL, "Echo Foxtrot"),
    ("Son Name", H + "This is to certify that Echo Foxtrot Son Name: Golf Hotel; Address: X" + TAIL, "Echo Foxtrot"),
    ("C/O", H + "This is to certify that; Alpha Bravo C/O: MD CHARLIE DELTA of VILL-X" + TAIL, "Alpha Bravo"),
    ("student is the holder", H + "This is to certify that Golf Hotel, son of Alpha Bravo and Echo Foxtrot, residing at X" + TAIL, "Golf Hotel"),
    ("purpose clause names the student", H + "This is to certify that Alpha Bravo, having address X" + TAIL +
     " The account holder's son Golf Hotel is going to Korea for higher study.", "Alpha Bravo"),
    ("signatory below", H + "This is to certify that Alpha Bravo, having address X" + TAIL +
     " Authorized Signature Kilo Lima Manager & Head of Branch", "Alpha Bravo"),
    ("nominee in table", H + "This is to certify that Alpha Bravo having address X" + TAIL + " Nominee: Golf Hotel", "Alpha Bravo"),
    ("our valued client", H + "This is to certify that our valued client Mr. Alpha Bravo, having address X" + TAIL, "Mr. Alpha Bravo"),
    ("joint account", H + "This is to certify that Mr. Alpha Bravo and Mrs. Echo Foxtrot have been maintaining a joint account"
     " with us. Balance BDT 25,00,000.", "Mr. Alpha Bravo", ["Mr. Alpha Bravo", "Mrs. Echo Foxtrot"]),
    ("business account M/S", H + "This is to certify that M/S Bravo Traders, Proprietor Alpha Bravo" + TAIL, None),
    ("business name without M/S", H + "This is to certify that Bravo Enterprise, Prop. Alpha Bravo" + TAIL, None),
    ("address in the name slot", H + "This is to certify that NORTHPARA, EASTPUR, NORTHPARA- 1234. WESTGANJ" + TAIL, None),
    ("the following customer + labels", H + "This is to certify that the following customer has been maintaining the "
     "mentioned account with the Bank. Account Number 1234 Account Title MD. ALPHA BRAVO Account Holder's Name MD. ALPHA "
     "BRAVO Account Currency BDT", "MD. ALPHA BRAVO"),
    ("the following person(s), unlabelled", H + "This is to certify that the following person(s) having address as "
     "mentioned below has been banking with us Customer Name Address ALPHA BRAVO NORTH BARI, EASTPUR balance", None),
    ("in the name of (subject unreadable)", H + "This is to certify that A/C No. 1234 has been maintained in the name of "
     "Alpha Bravo with us. Balance BDT 25,00,000.", "Alpha Bravo"),
    ("introduce", H + "We have the pleasure to introduce Echo Foxtrot, having address: Vill X has been maintaining a "
     "Savings Bank Account", "Echo Foxtrot"),
    ("OCR certiry/thct", H + "This is to certiry thct Alpha Bravo, having address" + TAIL, "Alpha Bravo"),
    ("OCR ccrtit,v", H + "lhis is ro ccrtit,v that Alpha Bravo Irathcr's \\anie: Charlie" + TAIL, "Alpha Bravo"),
    ("OCR glued certilythat", H + "This is t9 certilythat   MUHAMMAD ALPHA, SO. MUHAMMAD CHARLIE" + TAIL, "MUHAMMAD ALPHA"),
    ("OCR centify", H + "'This i9 To centify that; MD Alpha Bravo; Addres9- 1/2 X" + TAIL, "MD Alpha Bravo"),
    ("OCR Lhal", H + "'liis !S Lo certify Lhal Echo Foxtrot located at X" + TAIL, "Echo Foxtrot"),
    ("OCR thalt", H + "This is Lo certily thalt Echo Foxtrot, having address" + TAIL, "Echo Foxtrot"),
    ("OCR digit in name", H + "This is t9 certify that Mr. MoHAMMA0 Alpha H0ssAlN, Address: X" + TAIL, "Mr. MoHAMMA0 Alpha H0ssAlN"),
    ("garbled, no certify", H + "Tnhn tlt Echo Foxtrot. | Aher > Nane. Juliet" + TAIL, None),
    ("family certificate is not a solvency", "Family Certificate This is to certify that Golf Hotel, Father: Alpha Bravo, "
     "Mother: Echo Foxtrot are permanent residents of this Union.", None),
]

STATEMENT = [
    ("Pubali A/C NUMBER", "SAVINGS BANK ACCOUNT ALPHA BRAVO VILL X ACCOUNT STATEMENT FOR THE PERIOD : 01-01-2026 TO: "
     "01-09-2026 A/C NUMBER: 1234567890123 MD ALPHA BRAVO CUSTOMER ID: 1234 DATE TRN/TYPE AND DESCRIPTION", "MD ALPHA BRAVO"),
    ("NRBC A/C Name", "Statement of Account A/C Name ECHO FOXTROT A/C No 1234 12345678901 Address X Date Cheque No "
     "Narration Debit Credit Balance", "ECHO FOXTROT"),
    ("National Account Name", "STATEMENT OF ACCOUNT Account Name MD. ALPHA BRAVO Account No 1234 Account Type Saving",
     "MD. ALPHA BRAVO"),
    ("Islami header fields (no labelled holder)", "Statement of Account Customer ID 1234 Name ALPHA BRAVO Father Name "
     "CHARLIE DELTA Mother Name ECHO FOXTROT Spouse JULIET KILO Mobile/Phone 0171 Address X", None),
    ("narration name only", "Statement of Account Date Narration Debit Credit Balance 01-01-2026 TRANSFER TO BKASH GOLF "
     "HOTEL 1,000.00 Acc Name GOLF HOTEL Customer ID 1", None),
    ("ONE Bank address after Account Number", "ACCOUNT STATEMENT Account Number 1234567890123 NORTH SAMPLE BARI, "
     "EASTPUR, Customer ID 1", None),
    ("Agrani", "ACCOUNT STATEMENT MD ALPHA BRAVO Customer ID & Customer Type: 1234", "MD ALPHA BRAVO"),
]


class TestHolderTraps(unittest.TestCase):
    """The lab's 55 trap tests (48 solvency + 7 statement), one test method each (made below)."""


def _solvency_case(text, want, joint):
    def test(self):
        h = Bk.holder_from_solvency(text)
        self.assertEqual(h.name if h else None, want)
        if joint:
            self.assertEqual(h.joint, joint)
    return test


def _statement_case(text, want):
    def test(self):
        h = Bk.holder_from_statement(text)
        self.assertEqual(h.name if h else None, want)
    return test


for _n, _row in enumerate(SOLVENCY):
    setattr(TestHolderTraps, f"test_solvency_{_n:02d}", _solvency_case(_row[1], _row[2], _row[3] if len(_row) > 3 else []))
for _n, _row in enumerate(STATEMENT):
    setattr(TestHolderTraps, f"test_statement_{_n:02d}", _statement_case(_row[1], _row[2]))


# --- the runner's rules ----------------------------------------------------------------------------
PAD = " Branch: Sample Branch, Road 1, Sample Town. Customer services are available on all working days." * 3
SOLV = ("TO WHOM IT MAY CONCERN Date: 15-09-2026 This is to certify that {h}, S/O Kilo Lima, has been maintaining a "
        "Savings Account with our branch. Balance BDT 25,00,000.00 equivalent to USD 20,000.00. Authorized Signature"
        + PAD)
SOLV_JOINT = ("TO WHOM IT MAY CONCERN Date: 15-09-2026 This is to certify that {h} have been maintaining a joint "
              "account with us. Balance BDT 25,00,000.00 equivalent to USD 20,000.00. Authorized Signature" + PAD)
SOLV_PLAIN = ("TO WHOM IT MAY CONCERN Date: 15-09-2026 This is to certify that {h}, S/O Kilo Lima, has been maintaining "
              "a Savings Account with our branch. Balance BDT 25,00,000.00." + PAD)
STMT = ("STATEMENT OF ACCOUNT Account Name {h} Account No 1234 Account Type Saving Print Date {d} "
        "Date Narration Debit Credit Balance 01-09-2026 Opening Balance 2,500,000.00" + PAD)


def ident(father_nid: str = "") -> Identity:
    return Identity(
        student=NameRef(parts=("HOTEL", "GOLF"), written="GOLF HOTEL", source="passport MRZ", confirmed=True),
        father=NameRef(parts=("MD", "ALPHA", "BRAVO"), written="MD. ALPHA BRAVO", source="birth certificate",
                       confirmed=True),
        mother=NameRef(parts=("ECHO", "FOXTROT"), written="ECHO FOXTROT", source="birth certificate", confirmed=True),
        father_nid=NameRef(parts=tuple(father_nid.split()), written=father_nid, source="father NID", confirmed=True)
        if father_nid else NameRef())


UNIT = DocUnit("D07", "bank", "bank", "BANK STATEMENT", [10, 11])


def run(pages, idn=None, covered=None, confirm=None, solvency_pdf=None, index=(10, 11)):
    calls = []

    def conf(i, v):
        calls.append((i, v))
        return confirm(i, v) if confirm else "unclear"
    out = Bk.check_bank_unit(UNIT, pages, list(index)[:len(pages)], idn or ident(),
                             covered or (lambda a, b: False), conf, solvency_pdf or (lambda i: None))
    out.calls = calls
    return out


def rows(out, rule):
    return [f for f in out.findings if f.rule == rule]


def verdicts(out, rule):
    return [f.verdict for f in rows(out, rule)]


class TestHolderRule(unittest.TestCase):
    def test_father_on_birth_certificate_passes(self):
        out = run([SOLV.format(h="Md. Alpha Bravo"), STMT.format(h="MD. ALPHA BRAVO", d="15-09-2026")])
        self.assertEqual(verdicts(out, "bank.holder_parent"), [PASS])
        self.assertIn("as on the birth certificate", rows(out, "bank.holder_parent")[0].message)
        self.assertEqual(out.holder_role, "father")
        self.assertEqual(out.holder.name, "Md. Alpha Bravo")
        self.assertEqual(out.person_reads, [("father", "D07", "Md. Alpha Bravo", 10)])
        self.assertEqual(rows(out, "bank.holder_parent")[0].pages, (11,))     # 1-based run page

    def test_father_as_on_his_nid_only_passes_naming_the_nid(self):
        out = run([SOLV.format(h="Alpha Bravo"), STMT.format(h="ALPHA BRAVO", d="15-09-2026")],
                  idn=ident(father_nid="ALPHA BRAVO"))
        self.assertEqual(verdicts(out, "bank.holder_parent"), [PASS])
        self.assertIn("as on the father's NID", rows(out, "bank.holder_parent")[0].message)
        self.assertEqual(out.holder_role, "father")

    def test_mother_passes(self):
        out = run([SOLV.format(h="Mst Echo Foxtrot").replace("S/O", "W/O"), STMT.format(h="ECHO FOXTROT", d="x")])
        self.assertEqual(verdicts(out, "bank.holder_parent"), [FLAG])           # a dropped MST is a difference (A1)
        out = run([SOLV.format(h="Echo Foxtrot").replace("S/O", "W/O"), STMT.format(h="ECHO FOXTROT", d="x")])
        self.assertEqual(verdicts(out, "bank.holder_parent"), [PASS])
        self.assertEqual(out.holder_role, "mother")

    def test_student_is_fail(self):
        out = run([SOLV.format(h="Golf Hotel"), STMT.format(h="GOLF HOTEL", d="15-09-2026")])
        self.assertEqual(verdicts(out, "bank.holder_parent"), [FAIL])
        self.assertIn("student's own name", rows(out, "bank.holder_parent")[0].message)
        self.assertEqual(out.holder_role, "student")
        self.assertEqual(out.person_reads, [])

    def test_joint_with_a_parent_pass_and_flag(self):
        out = run([SOLV_JOINT.format(h="Mr. Alpha Bravo and Mrs. Echo Foxtrot"),
                   STMT.format(h="ALPHA BRAVO", d="15-09-2026")], idn=ident(father_nid="ALPHA BRAVO"))
        v = verdicts(out, "bank.holder_parent")
        self.assertEqual(v.count(PASS), 2)
        self.assertEqual(v.count(FLAG), 1)                                       # the joint-account FLAG (Q2)
        self.assertNotIn(FAIL, v)
        self.assertTrue(any("joint account" in f.message for f in rows(out, "bank.holder_parent")))
        self.assertEqual(out.holder_role, "father")
        self.assertEqual({p for p, *_ in out.person_reads}, {"father", "mother"})

    def test_joint_with_the_student_second_is_flag_not_fail(self):
        out = run([SOLV_JOINT.format(h="Mr. Alpha Bravo and Golf Hotel"), STMT.format(h="X Y", d="15-09-2026")],
                  idn=ident(father_nid="ALPHA BRAVO"))
        v = verdicts(out, "bank.holder_parent")
        self.assertNotIn(FAIL, v)
        self.assertIn(PASS, v)
        self.assertEqual(sum(1 for f in rows(out, "bank.holder_parent") if "student" in f.message and f.verdict == FLAG), 1)
        self.assertEqual(out.holder.name, "Mr. Alpha Bravo")                     # the chain uses the parent

    def test_joint_with_the_student_first_is_flag_not_fail(self):
        out = run([SOLV_JOINT.format(h="Golf Hotel and Echo Foxtrot"), STMT.format(h="X Y", d="15-09-2026")])
        v = verdicts(out, "bank.holder_parent")
        self.assertNotIn(FAIL, v)
        self.assertIn(PASS, v)
        self.assertEqual(out.holder.name, "Echo Foxtrot")

    def test_every_joint_name_is_judged(self):
        # no parent among the joint holders: one finding per name + the joint FLAG (critique 10)
        out = run([SOLV_JOINT.format(h="Kilo Lima and Juliet November"), STMT.format(h="X Y", d="15-09-2026")])
        named = [f for f in rows(out, "bank.holder_parent") if "joint account" not in f.message]
        self.assertEqual(len(named), 2)
        self.assertTrue(any("Kilo Lima" in f.message for f in named))
        self.assertTrue(any("Juliet November" in f.message for f in named))
        self.assertNotIn(PASS, verdicts(out, "bank.holder_parent"))

    def test_different_confirmed_name_without_affidavit_is_fail(self):
        # the statement header prints the same spelling: the holder read is confirmed (two pages)
        out = run([SOLV.format(h="Kilo Lima"), STMT.format(h="KILO LIMA", d="15-09-2026")])
        self.assertEqual(verdicts(out, "bank.holder_parent"), [FAIL])
        self.assertEqual(out.holder_role, "other")
        self.assertEqual(out.calls, [])                                          # no re-read needed

    def test_different_unconfirmed_name_is_flag(self):
        out = run([SOLV.format(h="Kilo Lima"), STMT.format(h="X Y", d="15-09-2026")])
        self.assertEqual(verdicts(out, "bank.holder_parent"), [FLAG])
        self.assertIn("check by eye, page 11", rows(out, "bank.holder_parent")[0].message)
        self.assertEqual(out.calls, [(10, "Kilo Lima")])                          # the re-read was asked, on run page 10

    def test_reread_confirmed_is_fail_and_refuted_is_flag(self):
        out = run([SOLV.format(h="Kilo Lima"), STMT.format(h="X Y", d="15-09-2026")], confirm=lambda i, v: "confirmed")
        self.assertEqual(verdicts(out, "bank.holder_parent"), [FAIL])
        out = run([SOLV.format(h="Kilo Lima"), STMT.format(h="X Y", d="15-09-2026")], confirm=lambda i, v: "refuted")
        self.assertEqual(verdicts(out, "bank.holder_parent"), [FLAG])
        self.assertIn("probably OCR", rows(out, "bank.holder_parent")[0].message)

    def test_unconfirmed_parent_reference_is_never_fail(self):
        idn = ident()
        idn.father.confirmed = False
        idn.mother.confirmed = False
        out = run([SOLV.format(h="Kilo Lima"), STMT.format(h="KILO LIMA", d="15-09-2026")], idn=idn,
                  confirm=lambda i, v: "confirmed")
        self.assertEqual(verdicts(out, "bank.holder_parent"), [FLAG])

    def test_statement_showing_the_reference_blocks_fail(self):
        # section 7: a read of the variant's document that shows the reference spelling keeps it a FLAG
        out = run([SOLV.format(h="Md. Alpha Brava"), STMT.format(h="MD. ALPHA BRAVO", d="15-09-2026")],
                  confirm=lambda i, v: "confirmed")
        self.assertEqual(verdicts(out, "bank.holder_parent"), [FLAG])
        self.assertIn("reads MD. ALPHA BRAVO", rows(out, "bank.holder_parent")[0].message)

    def test_covered_is_note(self):
        out = run([SOLV.format(h="Kilo Lima"), STMT.format(h="KILO LIMA", d="15-09-2026")],
                  covered=lambda a, b: True)
        self.assertEqual(verdicts(out, "bank.holder_parent"), [NOTE])

    def test_dropped_md_is_never_pass(self):
        # "MD <A> <B>" (reference) vs "<A> <B>" (holder): a SUBSET, never PASS (A1, critique 2)
        for confirm, want in ((lambda i, v: "unclear", FLAG), (lambda i, v: "confirmed", FAIL)):
            out = run([SOLV.format(h="Alpha Bravo"), STMT.format(h="X Y", d="15-09-2026")], confirm=confirm)
            self.assertEqual(verdicts(out, "bank.holder_parent"), [want])
            self.assertIn("a word is missing or extra", rows(out, "bank.holder_parent")[0].message)

    def test_holder_extending_the_reference_at_its_end(self):
        # A1 / A7: an extra word at the end is a difference like any other; confirmed -> FAIL
        out = run([SOLV.format(h="Echo Foxtrot Sierra").replace("S/O", "W/O"), STMT.format(h="ECHO FOXTROT SIERRA", d="x")],
                  confirm=lambda i, v: "confirmed")
        self.assertEqual(verdicts(out, "bank.holder_parent"), [FAIL])
        self.assertTrue(rows(out, "bank.holder_parent")[0].evidence["extends_reference"])
        # ... but when the birth certificate's own read goes on with that word, unconfirmed (NameRef.tail): FLAG
        idn = ident()
        idn.mother.tail = ("SIERRA",)
        out = run([SOLV.format(h="Echo Foxtrot Sierra").replace("S/O", "W/O"), STMT.format(h="ECHO FOXTROT SIERRA", d="x")],
                  idn=idn, confirm=lambda i, v: "confirmed")
        self.assertEqual(verdicts(out, "bank.holder_parent"), [FLAG])
        self.assertIn("goes on with SIERRA", rows(out, "bank.holder_parent")[0].message)
        # a word added at the START is a confirmed difference (A1: "MD" counts)
        out = run([SOLV.format(h="Mst Echo Foxtrot").replace("S/O", "W/O"), STMT.format(h="MST ECHO FOXTROT", d="x")])
        self.assertEqual(verdicts(out, "bank.holder_parent"), [FAIL])

    def test_garbled_holder_read_is_never_fail(self):
        out = run([SOLV.format(h="Mr. K1lo L1ma"), STMT.format(h="K1LO L1MA", d="15-09-2026")],
                  confirm=lambda i, v: "confirmed")
        self.assertEqual(verdicts(out, "bank.holder_parent"), [FLAG])

    def test_parents_unknown_is_flag(self):
        idn = Identity(student=NameRef(parts=("HOTEL", "GOLF")), father=NameRef(), mother=NameRef())
        out = run([SOLV.format(h="Kilo Lima"), STMT.format(h="KILO LIMA", d="15-09-2026")], idn=idn)
        self.assertEqual(verdicts(out, "bank.holder_parent"), [FLAG])
        self.assertIn("not known", rows(out, "bank.holder_parent")[0].message)


class TestSolvencyPresence(unittest.TestCase):
    def test_well_read_pages_without_a_certificate_are_missing(self):
        out = run([STMT.format(h="ALPHA BRAVO", d="15-09-2026"), STMT.format(h="ALPHA BRAVO", d="15-09-2026")])
        self.assertTrue(out.solvency_missing)
        self.assertIsNone(out.holder)
        self.assertEqual(out.solvency_pages, [])
        self.assertNotIn(FLAG, [f.verdict for f in out.findings])
        self.assertNotIn(FAIL, [f.verdict for f in out.findings])

    def test_unreadable_pages_flag(self):
        out = run(["..|  ;", "x" * 40])
        self.assertFalse(out.solvency_missing)
        self.assertEqual(verdicts(out, "bank.solvency_present"), [FLAG])

    def test_holder_unread_flag(self):
        # a certificate page whose subject is a business: the extractor returns nothing (never a guess)
        out = run(["TO WHOM IT MAY CONCERN This is to certify that M/S Bravo Traders, Proprietor Alpha Bravo has been "
                   "maintaining a Savings Account with our branch. Balance BDT 25,00,000.00." + PAD,
                   STMT.format(h="ALPHA BRAVO", d="15-09-2026")])
        self.assertEqual(verdicts(out, "bank.holder_read"), [FLAG])
        self.assertEqual(verdicts(out, "bank.holder_parent"), [])
        self.assertIsNone(out.holder)

    def test_certificate_wording_without_certify_clause(self):
        # the bot's certificate words find the page; the holder cannot be read -> FLAG, USD/seal still judged
        page = "BALANCE CERTIFICATE To whom it may concern. Balance BDT 25,00,000.00 USD 20,000. Signature" + PAD
        out = run([page, STMT.format(h="ALPHA BRAVO", d="15-09-2026")])
        self.assertEqual(verdicts(out, "bank.solvency_present"), [PASS])
        self.assertEqual(out.solvency_pages, [])
        self.assertEqual(verdicts(out, "bank.holder_read"), [FLAG])
        self.assertEqual(verdicts(out, "bank.usd"), [PASS])

    def test_solvency_page_found_wherever_it_sits(self):
        out = run([STMT.format(h="MD. ALPHA BRAVO", d="15-09-2026"), SOLV.format(h="Md. Alpha Bravo")])
        self.assertEqual(out.solvency_pages, [11])
        self.assertEqual(verdicts(out, "bank.holder_parent"), [PASS])
        self.assertEqual(rows(out, "bank.holder_parent")[0].pages, (12,))


class TestSolvencyPageRules(unittest.TestCase):
    def test_usd_on_a_statement_page_only_is_flag(self):
        stmt = STMT.format(h="MD. ALPHA BRAVO", d="15-09-2026") + " equivalent USD 20,000.00"
        out = run([SOLV_PLAIN.format(h="Md. Alpha Bravo"), stmt])
        self.assertEqual(verdicts(out, "bank.usd"), [FLAG])
        self.assertEqual(rows(out, "bank.usd")[0].pages, (11,))

    def test_usd_on_the_solvency_page_passes(self):
        out = run([SOLV.format(h="Md. Alpha Bravo"), STMT.format(h="MD. ALPHA BRAVO", d="15-09-2026")])
        self.assertEqual(verdicts(out, "bank.usd"), [PASS])

    def test_equivalent_alone_is_not_usd(self):
        page = SOLV_PLAIN.format(h="Md. Alpha Bravo").replace("BDT 25,00,000.00.", "BDT 25,00,000.00 equivalent to Taka.")
        out = run([page, STMT.format(h="MD. ALPHA BRAVO", d="15-09-2026")])
        self.assertEqual(verdicts(out, "bank.usd"), [FLAG])

    def test_seal_wording_on_a_statement_only_is_flag(self):
        sol = SOLV_PLAIN.format(h="Md. Alpha Bravo")
        stmt = STMT.format(h="MD. ALPHA BRAVO", d="15-09-2026") + " Authorized Signature, Manager, Seal"
        asked = []
        out = run([sol, stmt], solvency_pdf=lambda i: asked.append(i))
        self.assertEqual(verdicts(out, "bank.seal"), [FLAG])
        self.assertEqual(asked, [10])                                           # the solvency page's one-page PDF

    def test_seal_wording_on_the_solvency_page_passes(self):
        out = run([SOLV.format(h="Md. Alpha Bravo"), STMT.format(h="MD. ALPHA BRAVO", d="15-09-2026")])
        self.assertEqual(verdicts(out, "bank.seal"), [PASS])

    def test_seal_colour_on_the_solvency_page_passes(self):
        import pymupdf
        tmp_root = B.PRIVATE / "tmp"
        tmp_root.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=tmp_root) as d:
            pdf = Path(d) / "seal p01.pdf"
            doc = pymupdf.open()
            page = doc.new_page()
            page.insert_text((72, 72), "made-up solvency page")
            page.draw_circle((300, 600), 45, color=(0.5, 0.2, 0.8), fill=(0.5, 0.2, 0.8))
            doc.save(str(pdf))
            doc.close()
            out = run([SOLV_PLAIN.format(h="Md. Alpha Bravo"), STMT.format(h="MD. ALPHA BRAVO", d="15-09-2026")],
                      solvency_pdf=lambda i: pdf)
            self.assertEqual(verdicts(out, "bank.seal"), [PASS])
            self.assertEqual(rows(out, "bank.seal")[0].evidence.get("how"), "seal colour")


class TestDates(unittest.TestCase):
    def test_statement_first_uses_the_solvency_page(self):
        pages = [STMT.format(h="MD. ALPHA BRAVO", d="15-09-2026"), SOLV.format(h="Md. Alpha Bravo")]
        out = Bk.dates_rows(pages, [3, 4])
        self.assertEqual([f.verdict for f in out], [PASS])
        self.assertEqual(out[0].pages, (4, 5))
        self.assertEqual(out[0].evidence["solvency_page"], 5)

    def test_different_dates_flag(self):
        out = Bk.dates_rows([SOLV.format(h="Md. Alpha Bravo"), STMT.format(h="X", d="20-09-2026")])
        self.assertEqual([f.verdict for f in out], [FLAG])
        self.assertIn("SAME date", out[0].message)

    def test_unread_date_note_and_no_solvency_no_row(self):
        out = Bk.dates_rows([SOLV.format(h="Md. Alpha Bravo"), "STATEMENT OF ACCOUNT no dates here" + PAD])
        self.assertEqual([f.verdict for f in out], [NOTE])
        self.assertEqual(Bk.dates_rows([STMT.format(h="X", d="15-09-2026")]), [])


if __name__ == "__main__":
    unittest.main()
