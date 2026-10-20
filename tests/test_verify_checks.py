"""Golden cases for the document rules in src/verify/doc_verifier.py.

  B3   bank: only a figure LABELLED as the balance passes or fails the minimum; account,
       customer and phone numbers are never money; opening balance and statement period
       are FLAG / PASS / NOTE, never FAIL
  B4   birth certificate date of birth against the passport (a trusted MRZ, or a labelled
       date that is also the portal's), FAIL only on sure evidence
  B7   whole names (common.name_in) on NIDs, academic papers, the bank, the trade licence
  A22  GPA compared as a number, never as a prefix ("5" is not found in "55")
  B8   NID numbers compared page against page: an OCR misread is a NOTE, a different number
       a FLAG naming both, the A/A/B artefact gone
  B10  income tax: the labelled tax-paid amount, of any size
  B11  Sponsor values: both parents, self, someone else
  B12  family certificate issue date (labelled / fallback), trade-licence fiscal year
  B13  passport: MRZ expiry first, the number matched through OCR's confusions
  B14c a file that cannot be read or checked is an ERROR row, never a verdict on the
       document; a code fault still stops the run
  B18  two folders for one passport
  and verify_student end to end over a synthetic folder, pinning the rows' shape.

Every name, passport and number is invented (SAMPLE names, A9990000n passports, NIDs that
never start 01); every file is written under tmp_path. Image work (colour, QR, seals) is
stubbed where it is not what the test is about, and OCR is never run.

Run from the BOT folder:
    .venv\\Scripts\\python.exe -m pytest tests\\test_verify_checks.py -q
"""
import datetime as dt
import os
import sys
from pathlib import Path

import pytest

BOT_ROOT = Path(__file__).resolve().parent.parent
if str(BOT_ROOT) not in sys.path:
    sys.path.insert(0, str(BOT_ROOT))

from src.verify import common as C  # noqa: E402
from src.verify import doc_verifier as dv  # noqa: E402
from src.verify import page_checks as PC  # noqa: E402
from src.verify import rules as R  # noqa: E402
from verify_fixtures import FILLER, folder, make_image, make_pdf, mrz_lines, student  # noqa: E402

TODAY = dt.date(2026, 10, 21)
KLP = "KOREAN LANGUAGE PROGRAM (KLP)"


@pytest.fixture(autouse=True)
def today(monkeypatch):
    monkeypatch.setattr(dv, "TODAY", TODAY)


@pytest.fixture
def no_images(monkeypatch):
    """Page checks that say nothing: no colour, QR, Bangla or seal findings."""
    monkeypatch.setattr(PC, "inspect", lambda path: {})
    monkeypatch.setattr(PC, "colour_check", lambda info, key="": [])
    monkeypatch.setattr(PC, "qr_check", lambda info, required=False, text="": [])
    monkeypatch.setattr(PC, "bangla_check", lambda text: [])
    monkeypatch.setattr(PC, "seal_present", lambda path: False)
    monkeypatch.setattr(PC, "academic_check", lambda path, pages: [(C.PASS, "e-Apostille checked (stub)")])


@pytest.fixture
def no_ocr(monkeypatch):
    """An OCR engine that reads nothing, so an image is never really OCR'd."""
    class Reader:
        def readtext(self, image, detail=0):
            return []
    monkeypatch.setattr(dv, "_ocr_reader", lambda: Reader())


def verdicts(findings):
    return [v for v, _ in findings]


def only(findings, verdict):
    return [t for v, t in findings if v == verdict]


# --------------------------------------------------------------------------- B3 bank: the balance

def test_b3_a_customer_id_is_not_money():
    assert dv.money_figures("Customer ID 12345678") == []
    assert dv.balance_check("Customer ID 12345678", "KLP") == [(C.FLAG, "no balance amount could be read")]


def test_b3_a_low_closing_balance_with_a_large_unlabelled_credit_is_a_flag():
    (v, text), = dv.balance_check("Closing balance 450,000.00 Total credit 3,500,000.00", "BACHELOR")
    assert v == C.FLAG
    assert "450,000" in text and "3,500,000" in text and "2,500,000" in text


def test_b3_a_labelled_balance_in_lakh_grouping_passes():
    assert dv.balance_check("Balance: Tk 25,00,000.00", "KLP") == [
        (C.PASS, "balance 2,500,000 BDT meets the 1,800,000 BDT minimum")]


def test_b3_a_labelled_balance_below_the_minimum_with_nothing_else_fails():
    (v, text), = dv.balance_check(f"Solvency certificate. {FILLER} Available balance: 1,500,000.00", "BACHELOR")
    assert v == C.FAIL and "1,500,000" in text and "2,500,000" in text and "BACHELOR" in text


def test_b3_only_unlabelled_money_is_a_flag_whatever_its_size():
    for text in ("Deposit 9,900,000.00", "Deposit 12,000.00"):
        (v, msg), = dv.balance_check(text, "KLP")
        assert v == C.FLAG and "no figure is labelled as the balance" in msg


def test_b3_the_opening_balance_is_never_taken_for_the_balance():
    assert dv.labelled_amounts("Opening Balance 2,000,000.00", R.BALANCE_LABELS) == []
    assert dv.labelled_amounts("Balance B/F 2,000,000.00 Closing Balance 2,600,000.00",
                               R.BALANCE_LABELS) == [2_600_000]


@pytest.mark.parametrize("text, money", [
    ("Paid 2,500,000.00", [2_500_000]),                       # "paid" holds no "id"
    ("Nominee 2,500,000.00", [2_500_000]),                    # nor "nominee" a "no"
    ("Note: 2,500,000.00", [2_500_000]),
    ("Account Balance: Tk 1,500,000.00", [1_500_000]),        # a money word after the label
    ("A/C No. 1,234,567.00", []),
    ("Mobile 1,712,345.00", []),
    ("Tk.25,00,000.00", [2_500_000]),
    ("2500000/- Taka", [2_500_000]),
    ("BDT 1800000", [1_800_000]),
    ("33,74,119,69", [3_374_119.69]),                         # OCR wrote the decimal point as a comma
    ("on 15.10.2026, born 14/03/2005, rate 5.25%", []),       # dates and rates are not money
    ("Account 0123456789012 opened 2019", []),                # a bare digit run is not money
])
def test_a14_money_figures(text, money):
    assert [v for _, v in dv.money_figures(text)] == money


@pytest.mark.parametrize("opening, verdict", [(2_000_000, C.FLAG), (2_400_000, C.PASS), (2_700_000, C.FLAG)])
def test_b3_opening_balance_bachelor(opening, verdict):
    (v, text), = dv.opening_balance_check(f"Opening Balance {opening:,}.00\n{FILLER}", "BACHELOR")
    assert v == verdict and f"{opening:,}" in text
    if opening > 2_600_000:
        assert "above the preferred maximum" in text


def test_b3_opening_balance_klp_has_no_maximum_and_an_unlabelled_one_is_a_note():
    assert verdicts(dv.opening_balance_check("Opening Balance 2,700,000.00", "KLP")) == [C.PASS]
    assert verdicts(dv.opening_balance_check("Opening Balance 1,000,000.00", "KLP")) == [C.FLAG]
    (v, text), = dv.opening_balance_check("01/05/2026 Salary 2,000,000.00", "BACHELOR")
    assert v == C.NOTE and "2,000,000" in text
    assert verdicts(dv.opening_balance_check(FILLER, "MASTER")) == [C.NOTE]
    assert dv.opening_balance("Opening Balance 2,000,000.00") == (2_000_000, True)
    assert dv.opening_balance("01/05/2026 Salary 2,000,000.00") == (2_000_000, False)
    assert dv.opening_balance(FILLER) == (None, False)


@pytest.mark.parametrize("period, verdict", [
    ("Statement Period: 01/07/2026 to 30/09/2026", C.FLAG),
    ("Statement Period: 01/04/2026 to 30/09/2026", C.PASS),
    ("From Date: 01-Apr-2026 To Date: 30-Sep-2026", C.PASS),
    ("From 01/09/2026 to 30/09/2026", C.FLAG),
    (FILLER, C.NOTE),
])
def test_b3_statement_period(period, verdict):
    (v, text), = dv.statement_period_check([f"{period}\n{FILLER}"])
    assert v == verdict
    if verdict == C.FLAG:
        assert "6 months" in text and "2 months is accepted only for excellent accredited universities" in text


def test_b3_statement_period_from_five_dated_rows():
    rows = "\n".join(f"{d:02d}/{m:02d}/2026 Deposit 1,000.00 Balance 2,{m}00,000.00"
                     for d, m in ((5, 4), (9, 5), (2, 7), (8, 8), (28, 9)))
    assert dv.statement_period([rows]) == (dt.date(2026, 4, 5), dt.date(2026, 9, 28))
    four = "\n".join(rows.splitlines()[:4])
    assert dv.statement_period([four]) is None


def test_b3_check_bank_reads_the_statement_from_page_two(tmp_path, no_images):
    p = make_pdf(tmp_path / "BANK STATEMENT.pdf", [
        f"SOLVENCY CERTIFICATE To whom it may concern. Date: 15/10/2026. This is to certify that "
        f"SAMPLE FATHER holds an account with a balance of Tk 25,00,000.00 (USD 20,491.80). "
        f"Branch Manager, authorized signature. {FILLER}",
        f"STATEMENT OF ACCOUNT Generation Date: 15/10/2026 Statement Period: 01/04/2026 to 30/09/2026\n"
        f"Opening Balance 2,400,000.00\n{FILLER}",
    ])
    out = dv.check_bank(dv.read_document(p)[0], student(), [], "BACHELOR", p, {})
    assert (C.PASS, "balance 2,500,000 BDT meets the 2,500,000 BDT minimum") in out
    assert (C.PASS, "opening balance 2,400,000 BDT is within the expected range") in out
    assert (C.PASS, "the statement covers 6 months (2026-04-01 to 2026-09-30)") in out
    assert (C.PASS, "solvency certificate and statement both dated 2026-10-15") in out
    assert (C.PASS, "account appears to be in the name of SAMPLE FATHER") in out
    assert C.FAIL not in verdicts(out) and C.ERROR not in verdicts(out)


def test_b3_the_opening_balance_and_period_rules_never_fail():
    for program in R.PROGRAMS:
        for text in ("Opening Balance 1.00", "Opening Balance 99,000,000.00", FILLER,
                     "Statement Period: 01/10/2026 to 02/10/2026"):
            assert C.FAIL not in verdicts(dv.opening_balance_check(text, program))
            assert C.FAIL not in verdicts(dv.statement_period_check([text]))


# --------------------------------------------------------------------------- B11 sponsor values

def test_b11_both_parents_no_sponsor_flag_and_the_holder_is_the_parent_named():
    s = student(Sponsor="BOTH PARENTS")
    out = dv.check_financial(f"Trade licence of SAMPLE MOTHER. {FILLER}", s, [], None, {})
    assert not any("portal sponsor" in t for t in only(out, C.FLAG))
    assert (C.PASS, "issued in the bank account holder's name (SAMPLE MOTHER)") in out
    assert dv.sponsor_holder(s, FILLER) == ("PARENTS", "SAMPLE FATHER")        # neither named: the father
    bank = dv.check_bank(f"Balance Tk 25,00,000.00 of SAMPLE MOTHER {FILLER}", s, [], "KLP", None, {})
    assert (C.PASS, "account appears to be in the name of SAMPLE MOTHER") in bank


def test_b11_self_funded_no_flag_and_the_holder_is_the_applicant():
    s = student(Sponsor="Self-funded")
    out = dv.check_financial(f"Employment certificate for SAMPLE STUDENT ONE. {FILLER}", s, [], None, {})
    assert not any("portal sponsor" in t for t in only(out, C.FLAG))
    assert (C.PASS, "issued in the bank account holder's name (SAMPLE STUDENT ONE)") in out


def test_b11_anyone_else_keeps_the_sponsor_flag():
    s = student(Sponsor="UNCLE SAMPLE")
    out = dv.check_financial(f"Trade licence. {FILLER}", s, [], None, {})
    assert (C.FLAG, "portal sponsor is 'UNCLE SAMPLE' — only the applicant's parents may sponsor") in out
    bank = dv.check_bank(f"Balance Tk 25,00,000.00 {FILLER}", s, [], "KLP", None, {})
    assert any("SAMPLE STUDENT ONE" in t and "UNCLE SAMPLE" in t for t in only(bank, C.FLAG))


# --------------------------------------------------------------------------- B4 date of birth

PASSPORT_OK = "\n".join(mrz_lines("A99900001", "2005-03-14", "2031-01-01"))


def dob_check(cert, passport, dob="2005-03-14"):
    (v, text), = dv.birth_date_check(cert, passport, student(DOB=dob))
    return v, text


def test_b4_same_date_passes():
    assert dob_check("Date of Birth: 14/03/2005", PASSPORT_OK) == (
        C.PASS, "date of birth 2005-03-14 is the same on the birth certificate and the passport")


def test_b4_boxed_digits_on_the_certificate_are_read():
    assert dob_check("Date of Birth 1 4 0 3 2 0 0 5 Sex Male", PASSPORT_OK)[0] == C.PASS


def test_b4_a_different_date_against_a_trusted_mrz_fails():
    v, text = dob_check("Date of Birth: 14/03/2006", PASSPORT_OK)
    assert v == C.FAIL and "2006-03-14" in text and "2005-03-14" in text and "MRZ" in text


def test_b4_a_different_date_against_a_labelled_passport_date_that_is_the_portals_fails():
    v, text = dob_check("Date of Birth: 14/03/2006", f"Date of Birth 14 MAR 2005 {FILLER}")
    assert v == C.FAIL and "also the portal's" in text


def test_b4_a_labelled_passport_date_the_portal_does_not_share_is_only_a_flag():
    v, text = dob_check("Date of Birth: 15/06/2006", f"Date of Birth 14 MAR 2004 {FILLER}")
    assert v == C.FLAG and "2006-06-15" in text and "2004-03-14" in text


def test_b4_year_only_agreement_is_a_flag():
    v, text = dob_check("Date of Birth: 14/06/2005", PASSPORT_OK)
    assert v == C.FLAG and "the year agrees" in text


def test_b4_an_unread_certificate_date_is_a_flag_naming_the_portal_match():
    v, text = dob_check(f"Birth Registration Certificate {FILLER}", PASSPORT_OK)
    assert v == C.FLAG and "the birth certificate" in text
    assert "passport's 2005-03-14 is the portal's date of birth" in text


def test_b4_an_unread_passport_date_is_a_flag():
    v, text = dob_check("Date of Birth: 14/03/2005", f"PASSPORT {FILLER}")
    assert v == C.FLAG and "on the passport" in text
    assert "birth certificate's 2005-03-14 is the portal's date of birth" in text


def test_b4_no_passport_on_file():
    assert dob_check("Date of Birth: 14/03/2005", "") == (
        C.FLAG, "no passport on file to check the date of birth against")


def test_a9_the_certificate_window_stops_at_the_next_label():
    """An unreadable birth date is never replaced by the registration date after it."""
    assert dv.labelled_dob("Date of Birth: Date of Registration 01/04/2026") is None
    assert dv.labelled_dob("Date of Birth: ??? Registration No 2005 Date of Issue 01/04/2026") is None
    assert dv.labelled_dob("Date of Birth: 14/03/2005 Registration 01/04/2026") == dt.date(2005, 3, 14)
    v, _ = dob_check("Date of Birth: Date of Registration 01/04/2026", PASSPORT_OK)
    assert v == C.FLAG


def test_a9_an_mrz_birth_date_with_one_agreeing_digit_is_trusted_only_when_it_is_the_portals():
    l1, l2 = mrz_lines("A99900001", "2004-01-01", "2031-01-01")
    weak = l1 + "\n" + l2[:9] + "9" + l2[10:27] + "9" + l2[28:43] + "9"      # only the DOB digit agrees
    mrz = dv.passport_mrz(weak)
    assert mrz["dob_ok"] and mrz["checks"] == 1 and not mrz["line2_ok"]
    assert dv.mrz_dob(mrz, dt.date(2005, 3, 14)) is None
    assert dv.mrz_dob(mrz, dt.date(2004, 1, 1)) == dt.date(2004, 1, 1)
    # untrusted, and no labelled passport date: unread, so a FLAG, not a FAIL
    assert dob_check("Date of Birth: 14/03/2006", weak)[0] == C.FLAG


def test_b4_check_birth_cert_uses_the_passport_already_read():
    out = dv.check_birth_cert("BDRIS online birth registration. Notary public. Date of Birth: 14/03/2005",
                              student(), [], None, {"passport": PASSPORT_OK})
    assert (C.PASS, "date of birth 2005-03-14 is the same on the birth certificate and the passport") in out


# --------------------------------------------------------------------------- B13 passport

def passport_text(number="A99900001", dob="2005-03-14", expiry="2031-01-01"):
    return f"PASSPORT People's Republic of Bangladesh {FILLER}\n" + "\n".join(mrz_lines(number, dob, expiry))


def test_b13_passport_mrz_reads_the_fixture_lines_however_ocr_spaced_them():
    l1, l2 = mrz_lines("A99900001", "2005-03-14", "2031-01-01")
    want = {"passport_no": "A99900001", "passport_no_ok": True, "dob": "2005-03-14", "dob_ok": True,
            "expiry": "2031-01-01", "expiry_ok": True, "sex": "MALE", "line2_ok": True, "checks": 4,
            "name": "SAMPLE STUDENT ONE"}
    assert dv.passport_mrz(f"{FILLER}\n{l1}\n{l2}") == want
    assert dv.passport_mrz(f"{FILLER} {l1} {l2} {FILLER}") == want                 # one OCR line
    assert dv.passport_mrz(f"x {l1[:20]} {l1[20:]} {l2[:9]} {l2[9:30]} {l2[30:]} y") == want
    assert dv.passport_mrz(FILLER) == {} and dv.passport_mrz("") == {}


def test_b13_validity_is_read_from_the_mrz():
    out = dv.check_passport(passport_text(expiry="2027-03-01"), student(Passport_Expiry="2027-03-01"), [])
    assert out[0] == (C.FAIL, "only 4 month(s) validity left (expires 2027-03-01); guideline requires at "
                              "least 1 year — must be renewed")
    assert C.NOTE not in verdicts(out)


def test_b13_an_mrz_expiry_the_portal_does_not_share_is_a_flag_and_the_mrz_decides():
    out = dv.check_passport(passport_text(expiry="2027-03-01"), student(Passport_Expiry="2031-01-01"), [])
    assert out[0][0] == C.FAIL and "2027-03-01" in out[0][1]
    assert (C.FLAG, "portal expiry 2031-01-01, the passport's MRZ reads 2027-03-01 — correct the portal") in out


def test_b13_without_an_mrz_the_portal_expiry_is_used_and_said_so():
    out = dv.check_passport(f"PASSPORT A99900001 {FILLER}", student(), [])
    assert out[0] == (C.PASS, "valid until 2031-01-01 (50 months left)")
    assert (C.NOTE, "the expiry date was taken from the portal (the passport's MRZ could not be read)") in out
    assert (C.PASS, "passport number A99900001 found on the scan") in out


def test_b13_a_number_ocr_misread_passes_with_a_note():
    for scanned in ("499900001", "A9990O001", "A99900007"):
        out = dv.check_passport(f"Passport No {scanned} {FILLER}", student(), [])
        assert (C.PASS, f"passport number A99900001 found on the scan, read as {scanned.upper()}") in out
        assert (C.NOTE, f"the scan reads {scanned.upper()} for A99900001 — probably an OCR misread") in out
        assert C.FLAG not in verdicts(out)


def test_b13_a_different_mrz_valid_number_is_a_flag():
    out = dv.check_passport(passport_text(number="A99900002"), student(), [])
    assert (C.FLAG, "the passport's MRZ reads number A99900002, the portal says A99900001 — check which "
                    "is right") in out


def test_b13_an_unrelated_number_is_a_flag():
    out = dv.check_passport(f"Passport No B12345678 {FILLER}", student(), [])
    assert (C.FLAG, "portal passport number A99900001 not found in the scan text") in out


def test_b13_the_cross_check_uses_the_same_matcher():
    rows = dv.cross_checks({"passport": f"Passport No 499900001 {FILLER}", "bank": FILLER}, student())
    row, = [r for r in rows if r["doc"] == "CROSS-CHECK passport number"]
    assert row["verdict"] == C.PASS and row["key"] == dv.CROSS_CHECK
    assert row["detail"] == ("PASS: passport number A99900001 appears on: 01 Passport (read with an OCR "
                             "misread on 01 Passport: 499900001)")
    rows = dv.cross_checks({"passport": FILLER}, student())
    assert [r["verdict"] for r in rows if r["doc"] == "CROSS-CHECK passport number"] == [C.FLAG]


# --------------------------------------------------------------------------- B7 names, A22 GPA

def test_b7_a_parent_nid_with_only_the_shared_surname_is_a_flag():
    nid = "SAMPLE notary public advocate " + FILLER
    out = dv.check_nid(nid, student(), [], "father", None, {"passport": f"SAMPLE FATHER {FILLER}"})
    assert (C.FLAG, "father's name 'SAMPLE FATHER' not found in the NID scan text") in out


def test_b7_the_full_name_with_honorifics_anywhere_passes():
    nid = "MD. SAMPLE FATHER notary public advocate " + FILLER
    out = dv.check_nid(nid, student(Father="SAMPLE MD FATHER"), [], "father", None,
                       {"passport": f"Father: Mohammad SAMPLE FATHER {FILLER}"})
    assert (C.PASS, "father's name 'SAMPLE MD FATHER' appears on both the NID and the passport") in out


def test_b7_academic_papers_need_the_whole_name():
    s = student(Mother="SAMPLE SAMPLE", Father="SAMPLE KARIM")
    out = dv.check_academic(f"Certificate. Name: SAMPLE STUDENT ONE. Father's name MD. SAMPLE KARIM. "
                            f"Mother's name OTHER SAMPLE. apostille {FILLER}", s, [])
    assert (C.PASS, "student's name (SAMPLE STUDENT ONE) appears on the academic papers") in out
    assert (C.PASS, "father's name (SAMPLE KARIM) appears on the academic papers") in out
    assert (C.FLAG, "mother's name 'SAMPLE SAMPLE' not found on the academic papers") in out


def test_b7_family_certificate_name_at_the_top():
    s = student()
    good = dv.check_family_cert(f"SAMPLE STUDENT ONE {FILLER}", s, [])
    assert (C.PASS, "student's name appears at the top of the certificate") in good
    bad = dv.check_family_cert(f"SAMPLE OTHER {FILLER}", s, [])          # 1 of 3 parts
    assert (C.FLAG, "student's name not found near the top — it must be in the first row") in bad


def test_b7_tin_and_trade_licence_compare_name_tokens():
    s = student()
    tin = ("This is to Certify that MD. SAMPLE FATHER is a Registered Taxpayer of National Board of "
           f"Revenue. Taxpayer's Identification Number certificate. {FILLER}")
    ctx = {"_account_holder": "SAMPLE FATHER"}
    dv.check_financial(tin, s, [], None, ctx)
    trade = f"Trade licence. Name of Proprietor / Assesse: SAMPLE FATHER Name of Business SAMPLE STORE {FILLER}"
    out = dv.check_financial(trade, s, [], None, ctx)
    assert any(v == C.PASS and t.startswith("TIN and trade licence are both in the name of") for v, t in out)
    ctx = {"_account_holder": "SAMPLE FATHER"}
    dv.check_financial(tin, s, [], None, ctx)
    other = f"Trade licence. Name of Proprietor / Assesse: OTHER PERSON Name of Business X {FILLER}"
    out = dv.check_financial(other, s, [], None, ctx)
    assert any(v == C.FLAG and "the TIN names" in t for v, t in out)


@pytest.mark.parametrize("text, gpa, found", [
    ("GPA 55", "5.00", False), ("GPA: 4.83", "5.00", False), ("GPA: 5", "5.00", True),
    ("obtained 5.00 in", "5.00", True), ("15.00", "5.00", False), ("5.0", "5.00", True),
    ("5.25", "5.00", False), ("CGPA 4.83 out of 5.00", "4.83", True), ("Grade Point Average 4.5", "4.50", True),
    ("born 05.00.2005", "5.00", False),
])
def test_a22_gpa_is_compared_as_a_number(text, gpa, found):
    assert dv.gpa_on(text, gpa) is found


def test_a22_check_academic_does_not_find_5_00_in_55():
    out = dv.check_academic(f"apostille SSC result GPA 55 {FILLER}", student(SSC_GPA="5.00", HSC_GPA=""), [])
    assert (C.FLAG, "portal SSC GPA 5.00 not found in the certificate text") in out


# --------------------------------------------------------------------------- B8 NID numbers

def test_b8_one_digit_apart_is_an_ocr_misread_note():
    out = dv.nid_number_agreement([{"5551234567"}, {"5551234568"}], ["5551234567", "5551234568"])
    assert out == [(C.NOTE, "the 10-digit ID number reads 5551234567 on one page and 5551234568 on another "
                            "— probably an OCR misread")]


def test_b8_unrelated_numbers_are_a_flag_naming_both():
    out = dv.nid_number_agreement([{"5551234567"}, {"7779876543"}], ["5551234567", "7779876543"])
    assert out == [(C.FLAG, "the 10-digit ID number reads 5551234567 on one page and 7779876543 on another "
                            "— check the translation against the original by eye")]


def test_b8_a_a_b_never_names_one_number_twice():
    a, b = "5551234567", "7779876543"
    out = dv.nid_number_agreement([{a}, {a}, {b}], [a, b])
    assert (C.PASS, f"ID number {a} reads the same on the original and the translation") in out
    flag, = only(out, C.FLAG)
    assert a in flag and b in flag and flag.count(a) == 1


def test_b8_other_formats_and_single_pages():
    a10, a13 = "5551234567", "5551234567890"
    assert dv.nid_number_agreement([{a10}, {a13}], [a10, a13])[0][0] == C.NOTE      # two formats, not a mismatch
    assert verdicts(dv.nid_number_agreement([{a10}, {a10}], [a10])) == [C.PASS]


def test_b8_check_nid_reads_the_pages_of_the_file(tmp_path):
    p = make_pdf(tmp_path / "FATHER NID.pdf", [f"NID 555 123 4567 {FILLER}", f"NID 5551234567 {FILLER}",
                                               f"NID 7779876543 {FILLER}"])
    out = dv.check_nid(f"advocate notary {FILLER}", student(Father=""), [], "father", p, {})
    assert (C.PASS, "ID number 5551234567 reads the same on the original and the translation") in out
    assert any(v == C.FLAG and "7779876543" in t and t.count("5551234567") == 1 for v, t in out)


# --------------------------------------------------------------------------- B10 income tax

@pytest.mark.parametrize("text, verdict, words", [
    ("Total tax paid: Tk 15,000.00", C.PASS, "tax paid 15,000 BDT"),
    ("Tax paid 15000", C.PASS, "tax paid 15,000 BDT"),
    ("Tax paid 8,500.00", C.FLAG, "tax paid 8,500 BDT — below the 10,000 BDT expected"),
    ("Amount 15,000.00 deposited", C.FLAG, "no amount labelled as tax paid"),
    ("Tax paid in assessment year 2025-2026: Tk 12,500.00", C.PASS, "tax paid 12,500 BDT"),
])
def test_b10_tax_paid(text, verdict, words):
    out = dv.check_income_tax(f"{text} {FILLER}", student(), [], "KLP")
    v, t = out[0]
    assert v == verdict and words in t


def test_b10_net_wealth_is_unchanged_for_masters():
    out = dv.check_income_tax(f"Net wealth 60,00,000 Tax paid 20,000.00 {FILLER}", student(), [], "MASTER")
    assert out[0] == (C.PASS, "net wealth figure 6,000,000 BDT meets the 50 lakh guideline")
    assert out[1] == (C.PASS, "tax paid 20,000 BDT (at least 10,000 BDT is expected)")


# --------------------------------------------------------------------------- B12 dates

@pytest.mark.parametrize("text, source, date", [
    ("Date: 01/10/2026 " + FILLER, "label", dt.date(2026, 10, 1)),
    ("Ref no 12 Date: 30.08..2026 " + FILLER, "label-digits", dt.date(2026, 8, 30)),
    (FILLER + " signed 15/09/2026", "fallback", dt.date(2026, 9, 15)),
    (FILLER, "", None),
])
def test_b12_issue_date_labelled(text, source, date):
    assert dv.issue_date_labelled(text) == (date, source)
    assert dv.issue_date(text) == date


def test_b12_family_certificate_dates():
    s = student()
    assert dv.check_family_cert("Date: 01/10/2026 " + FILLER, s, [])[0] == (
        C.PASS, "issued 2026-10-01 (0 month(s) ago) — within the 3-month validity")
    assert dv.check_family_cert("Date: 01/01/2026 " + FILLER, s, [])[0] == (
        C.FAIL, "issued 2026-01-01 (9 months ago) — older than the 3-month validity")
    v, text = dv.check_family_cert(FILLER + " notarised 15/09/2026", s, [])[0]
    assert v == C.FLAG and "2026-09-15" in text and "may be the notary date" in text
    assert dv.check_family_cert(FILLER, s, [])[0][0] == C.FLAG


@pytest.mark.parametrize("text, verdict", [
    ("Fiscal Year: 2026-2027", C.PASS),
    ("Fiscal Year: 2026-27", C.PASS),                                         # short form
    ("License validity 1st July 2026 to 30th June 2027", C.PASS),
    ("Fiscal Year: 2024-2025 " + FILLER + " printed 2026-2027", C.FAIL),       # the label decides
    ("Renewed: " + FILLER + " 2026-2027", C.PASS),                              # current year, unlabelled
    (FILLER + " (c) 2019-2020", C.FLAG),                                      # a footer year alone
    (FILLER, C.FLAG),
])
def test_b12_trade_licence_fiscal_year(text, verdict):
    (v, _), = dv.fiscal_year_check(text)
    assert v == verdict


def test_b12_fiscal_year_forms():
    assert [fy for _, _, fy in dv.fiscal_years("2026-27, 2026/2027, 2026 – 2027, 2026-10-21, 2025-2027")] == [
        "2026-2027", "2026-2027", "2026-2027"]
    assert dv.current_fiscal_year() == "2026-2027"


# --------------------------------------------------------------------------- B14c errors

def test_b14c_a_reader_raising_check_error_gives_an_error_row(tmp_path, no_images, monkeypatch):
    d = folder(tmp_path, KLP, "SAMPLE STUDENT ONE", "A99900001",
               {"PASSPORT.pdf": None, "BANK STATEMENT.pdf": None})
    real = dv.read_document

    def reader(path, max_pages=20):
        if path.name == "BANK STATEMENT.pdf":
            raise C.CheckError("the file is locked")
        return real(path, max_pages)

    monkeypatch.setattr(dv, "read_document", reader)
    rows = dv.verify_student(d, student(), "KLP")
    bank, = [r for r in rows if r["key"] == "bank"]
    assert bank == {"doc": "08 Bank Solvency & Statement", "file": "BANK STATEMENT.pdf", "key": "bank",
                    "verdict": C.ERROR, "has_error": True,
                    "detail": "ERROR: could not check this file (the file is locked) — it is retried on the "
                              "next pass"}
    passport, = [r for r in rows if r["key"] == "passport"]
    assert passport["verdict"] != C.ERROR and "has_error" not in passport


def test_b14c_unreadable_text_gives_an_error_row(tmp_path, no_images, monkeypatch):
    d = folder(tmp_path, KLP, "SAMPLE STUDENT ONE", "A99900001", {"PASSPORT.pdf": None})
    monkeypatch.setattr(dv, "read_document", lambda path, max_pages=20: ("[unreadable: cannot open broken document]", []))
    row, = [r for r in dv.verify_student(d, student(), "KLP") if r["key"] == "passport"]
    assert row["verdict"] == C.ERROR and row["has_error"]
    assert row["detail"] == ("ERROR: could not check this file (unreadable: cannot open broken document) — "
                             "it is retried on the next pass")


def test_b14c_a_broken_pdf_is_unreadable_and_read_pages_raises(tmp_path):
    p = tmp_path / "FATHER NID.pdf"
    p.write_bytes(b"this is not a pdf at all")
    assert dv.read_document(p)[0].startswith("[unreadable:")
    with pytest.raises(C.CheckError):
        dv.read_pages(p)


def test_b14c_other_exceptions_are_errors_but_code_faults_stop_the_run(tmp_path, no_images, monkeypatch):
    d = folder(tmp_path, KLP, "SAMPLE STUDENT ONE", "A99900001", {"PASSPORT.pdf": None})
    monkeypatch.setitem(dv.CHECKS, "passport", lambda *a: (_ for _ in ()).throw(OSError("disk went away")))
    row, = [r for r in dv.verify_student(d, student(), "KLP") if r["key"] == "passport"]
    assert row["verdict"] == C.ERROR and "OSError: disk went away" in row["detail"]
    for fault in (NameError, AttributeError, ImportError, TypeError):
        monkeypatch.setitem(dv.CHECKS, "passport", lambda *a, f=fault: (_ for _ in ()).throw(f("a bug")))
        with pytest.raises(fault):
            dv.verify_student(d, student(), "KLP")


def test_b14c_a_page_check_raising_check_error_is_an_error_row(tmp_path, no_images, monkeypatch):
    d = folder(tmp_path, KLP, "SAMPLE STUDENT ONE", "A99900001", {"PASSPORT.pdf": None})

    def inspect(path):
        raise C.CheckError("could not render page 1")

    monkeypatch.setattr(PC, "inspect", inspect)
    row, = [r for r in dv.verify_student(d, student(), "KLP") if r["key"] == "passport"]
    assert row["verdict"] == C.ERROR and "could not render page 1" in row["detail"]


def test_b14c_check_error_findings_inside_a_check(tmp_path, monkeypatch):
    def broken(path, max_pages=20, sideways=None):
        raise C.CheckError("could not open FATHER NID.pdf")

    monkeypatch.setattr(dv, "read_pages", broken)
    monkeypatch.setattr(PC, "seal_present", lambda path: False)
    out = dv.check_nid(f"advocate notary {FILLER}", student(), [], "father", tmp_path / "FATHER NID.pdf", {})
    assert (C.ERROR, "could not read the NID page by page (could not open FATHER NID.pdf)") in out
    out = dv.check_bank(f"Balance Tk 25,00,000.00 {FILLER}", student(), [], "KLP", tmp_path / "BANK.pdf", {})
    assert (C.ERROR, "could not read the bank file page by page (could not open FATHER NID.pdf)") in out
    out = dv.check_academic(f"apostille {FILLER}", student(), [], tmp_path / "ACADEMIC.pdf", {})
    assert (C.ERROR, "could not check the e-Apostille pages (could not open FATHER NID.pdf)") in out
    bad = tmp_path / "PASSPORT SIZE PHOTO.jpg"
    bad.write_bytes(b"not a jpeg")
    out = dv.check_photo("", student(), [], bad)
    assert any(v == C.ERROR and t.startswith("could not open the image") for v, t in out)


def test_b14c_error_does_not_mask_fail_and_a3_has_error_under_a_fail():
    row = dv._row("08 Bank Solvency & Statement", "BANK.pdf", "bank",
                  [(C.FAIL, "below the minimum"), (C.ERROR, "could not read page 2")])
    assert row["verdict"] == C.FAIL and row["has_error"] is True
    assert C.row_has_error(row)
    assert "has_error" not in dv._row("x", "y", "z", [(C.PASS, "ok"), (C.NOTE, "fyi"), (C.FLAG, "hm")])
    assert C.student_verdict([{"verdict": C.FAIL}, {"verdict": C.ERROR}, {"verdict": C.FLAG}]) == C.FAIL
    assert C.student_verdict([{"verdict": C.ERROR}, {"verdict": C.FLAG}]) == C.ERROR


# --------------------------------------------------------------------------- B18 two folders

def two_folders(tmp_path):
    klp = folder(tmp_path, KLP, "SAMPLE STUDENT ONE", "A99900001", {".download_complete": "v"})
    bach = folder(tmp_path, "BACHELOR'S DEGREE", "SAMPLE STUDENT ONE", "A99900001", {".download_complete": "v"})
    solo = folder(tmp_path, KLP, "SAMPLE STUDENT TWO", "A99900002", {})
    return klp, bach, solo


def test_b18_the_folder_in_the_students_program_wins(tmp_path, monkeypatch):
    klp, bach, solo = two_folders(tmp_path)
    monkeypatch.setattr(dv, "DOCS_ROOTS", [tmp_path])
    os.utime(klp / ".download_complete", (2_000_000_000, 2_000_000_000))       # the newer download
    os.utime(bach / ".download_complete", (1_000_000_000, 1_000_000_000))
    portal = {"A99900001": student(Program="BACHELOR'S DEGREE")}
    got = dv.student_folders(portal)
    assert got["A99900001"] == (bach, bach.name) and got["A99900002"] == (solo, solo.name)
    assert dv.student_folders({"A99900001": student()})["A99900001"][0] == klp


def test_b18_otherwise_the_newest_download_wins(tmp_path, monkeypatch):
    klp, bach, _ = two_folders(tmp_path)
    monkeypatch.setattr(dv, "DOCS_ROOTS", [tmp_path])
    os.utime(klp / ".download_complete", (1_000_000_000, 1_000_000_000))
    os.utime(bach / ".download_complete", (2_000_000_000, 2_000_000_000))
    assert dv.student_folders()["A99900001"][0] == bach
    assert dv.student_folders({"A99900001": student(Program="MASTER'S DEGREE")})["A99900001"][0] == bach
    os.utime(klp / ".download_complete", (3_000_000_000, 3_000_000_000))
    assert dv.student_folders()["A99900001"][0] == klp
    (bach / ".download_complete").unlink()                                      # no marker: the folder's own time
    os.utime(bach, (4_000_000_000, 4_000_000_000))
    assert dv.student_folders()["A99900001"][0] == bach


def test_portal_students_from_given_rows_never_fetches(monkeypatch):
    from src.sheets import progress_builder as pb
    monkeypatch.setattr(pb, "fetch_all_students", lambda: pytest.fail("the portal was fetched"))
    rows = [student(), student(Passport_No="a99900002", Full_Name="SAMPLE STUDENT TWO"), student(Passport_No="")]
    got = dv.portal_students(rows=rows)
    assert sorted(got) == ["A99900001", "A99900002"] and got["A99900002"]["Full Name"] == "SAMPLE STUDENT TWO"
    assert dv.portal_students([]) == {}


def test_portal_keys_read():
    assert dv.PORTAL_KEYS_READ == ("Full Name", "Father", "Mother", "DOB", "Gender", "Passport No",
                                   "Passport Expiry", "Sponsor", "SSC GPA", "HSC GPA", "Program")
    assert all(k in student() for k in dv.PORTAL_KEYS_READ)


# --------------------------------------------------------------------------- verify_student end to end

def _photo(path):
    from PIL import Image, ImageDraw
    img = Image.new("RGB", (350, 450), "white")
    ImageDraw.Draw(img).ellipse((100, 120, 250, 330), fill=(90, 60, 40))           # a face, not a flat page
    img.save(path, format="JPEG", quality=95)


def test_verify_student_end_to_end(tmp_path, no_images, no_ocr):
    l1, l2 = mrz_lines("A99900001", "2005-03-14", "2031-01-01")
    people = "Name SAMPLE STUDENT ONE Father SAMPLE FATHER Mother SAMPLE MOTHER"
    d = folder(tmp_path, KLP, "SAMPLE STUDENT ONE", "A99900001", {
        "PASSPORT.pdf": f"PASSPORT {people}\nDate of Birth 14 MAR 2005\n{FILLER}\n{l1}\n{l2}",
        "PASSPORT SIZE PHOTO.jpg": _photo,
        "BIRTH CERTIFICATE.pdf": f"BDRIS online birth registration certificate. {people}. Date of Birth: "
                                 f"14/03/2005. Notary public, advocate. {FILLER}",
        "FATHER DEATH CERTIFICATE.pdf": f"Death certificate of SAMPLE FATHER. {FILLER}",
        "MOTHER NID.pdf": [f"National ID SAMPLE MOTHER 555 123 4567 {FILLER}",
                           f"Translation. Name SAMPLE MOTHER NID 5551234567. Notary public, advocate. {FILLER}"],
        "FAMILY RELATIONSHIP CERTIFICATE.pdf": [f"SAMPLE STUDENT ONE family certificate. Date: 01/10/2026.\n"
                                                f"{people}\nborn 14/03/2005\nUnion Parishad. 5551234567 "
                                                f"7779876543. Notary advocate. {FILLER}"],
        "ACADEMIC CERTIFICATE.pdf": f"{people}. SSC GPA 5.00 HSC GPA 4.83. Notary. {FILLER}",
        "BANK STATEMENT.pdf": [f"Solvency certificate. Date: 15/10/2026. SAMPLE FATHER holds a balance of "
                               f"Tk 25,00,000.00 (USD 20,491.80). Branch manager signature. {FILLER}",
                               f"Statement Generation Date: 15/10/2026 Statement Period: 01/04/2026 to "
                               f"30/09/2026\nOpening Balance 2,000,000.00\n{FILLER}"],
        "TRADE LICENSE.pdf": f"Trade licence of SAMPLE FATHER. Fiscal Year: 2026-2027. Notary. {FILLER}",
        "ID CARD CV.pdf": f"Curriculum vitae of OTHER PERSON, passport B12345678. {FILLER}",
        ".download_complete": "v2",
    })
    rows = dv.verify_student(d, student(), "KLP")
    for r in rows:
        assert set(r) <= {"doc", "file", "key", "verdict", "detail", "has_error"}, r
        assert r["verdict"] in (C.PASS, C.FLAG, C.FAIL, C.MISSING, C.ERROR)
        if r["key"] != dv.CROSS_CHECK:
            parts = r["detail"].split(" | ")
            assert all(p.split(": ", 1)[0] in (C.PASS, C.FLAG, C.FAIL, C.NOTE, C.ERROR) for p in parts), r
            assert r["verdict"] == C.row_verdict([tuple(p.split(": ", 1)) for p in parts])
    assert [(r["key"], r["file"]) for r in rows if r["key"] != dv.CROSS_CHECK] == [
        ("passport", "PASSPORT.pdf"), ("photo", "PASSPORT SIZE PHOTO.jpg"),
        ("birth_cert", "BIRTH CERTIFICATE.pdf"), ("mother_nid", "MOTHER NID.pdf"),
        ("family_cert", "FAMILY RELATIONSHIP CERTIFICATE.pdf"), ("academic", "ACADEMIC CERTIFICATE.pdf"),
        ("bank", "BANK STATEMENT.pdf"), ("trade_license", "TRADE LICENSE.pdf"),
        ("id_card_cv", "ID CARD CV.pdf"), ("father_death_cert", "FATHER DEATH CERTIFICATE.pdf"),
    ]
    by_key = {r["key"]: r for r in rows if r["key"] != dv.CROSS_CHECK}
    assert by_key["id_card_cv"] == {"doc": "03 ID Card / CV", "file": "ID CARD CV.pdf", "key": "id_card_cv",
                                    "verdict": C.PASS,
                                    "detail": "PASS: present — recorded as present only (not checked)"}
    assert by_key["father_death_cert"]["detail"] == (
        "PASS: present — accepted in place of the father's NID (05 Father NID not needed)")
    assert by_key["passport"]["detail"] == ("PASS: valid until 2031-01-01 (50 months left) | "
                                            "PASS: passport number A99900001 found on the scan")
    assert "PASS: date of birth 2005-03-14 is the same on the birth certificate and the passport" in \
        by_key["birth_cert"]["detail"]
    assert "PASS: ID number 5551234567 reads the same on the original and the translation" in \
        by_key["mother_nid"]["detail"]
    # KLP: an opening balance of 2,000,000 is above its 1,800,000 minimum and it has no maximum
    assert by_key["bank"]["detail"] == (
        "PASS: balance 2,500,000 BDT meets the 1,800,000 BDT minimum | PASS: solvency amount stated in USD | "
        "PASS: carries a seal and signature | PASS: solvency certificate and statement both dated 2026-10-15 | "
        "PASS: opening balance 2,000,000 BDT is within the expected range | "
        "PASS: the statement covers 6 months (2026-04-01 to 2026-09-30) | "
        "PASS: account appears to be in the name of SAMPLE FATHER")
    assert "PASS: licence fiscal year 2026-2027 is the current 2026-2027" in by_key["trade_license"]["detail"]
    assert "PASS: SSC GPA 5.00 matches the portal" in by_key["academic"]["detail"]
    assert all(r["verdict"] == C.PASS for r in rows), [r for r in rows if r["verdict"] != C.PASS]
    cross = [r for r in rows if r["key"] == dv.CROSS_CHECK]
    assert [(r["doc"], r["file"], r["detail"]) for r in cross] == [
        ("CROSS-CHECK name", "", "PASS: applicant's name 'SAMPLE STUDENT ONE' consistent across the identity "
                                 "documents"),
        ("CROSS-CHECK name", "", "PASS: father's name 'SAMPLE FATHER' consistent across the identity documents"),
        ("CROSS-CHECK name", "", "PASS: mother's name 'SAMPLE MOTHER' consistent across the identity documents"),
        ("CROSS-CHECK date of birth", "", "PASS: date of birth 2005-03-14 consistent"),
        ("CROSS-CHECK passport number", "", "PASS: passport number A99900001 appears on: 01 Passport"),
    ]
    # the CV is never read: its other name and passport reach no row
    assert "OTHER PERSON" not in str(rows) and "B12345678" not in str(rows)
    assert dv.student_verdict(rows) == C.PASS
