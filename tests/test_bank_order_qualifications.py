"""The owner's two document rules (made-up data only):

  * Document 08 — the bank file holds the solvency certificate on page 1 and the statement from
    page 2 to the last page (check_bank / bank_order);
  * Document 07 — the academic file holds every qualification the program requires: SSC and HSC
    (or a Diploma) for all, plus a Bachelor's degree for MASTER (check_academic /
    page_checks.qualification_check).
"""
import datetime as dt
from pathlib import Path

import pytest

from src.verify import doc_verifier as dv
from src.verify import page_checks as PC
from src.verify import rules as R

DAY = dv.TODAY - dt.timedelta(days=10)
SLASH = DAY.strftime("%d/%m/%Y")
MON = DAY.strftime("%d-%b-%Y")
OTHER = (DAY - dt.timedelta(days=5)).strftime("%d-%b-%Y")

SOLVENCY = (f"SAMPLE BANK LIMITED  Ref: SBL/SOL/0001  Date: {SLASH}\n"
            "TO WHOM IT MAY CONCERN\nSOLVENCY CERTIFICATE\n"
            "This is to certify that SAMPLE FATHER is maintaining a savings account with our "
            "branch. The balance (including deposit) as on that date is BDT 3,000,000.00 "
            "(in words: thirty lakh taka only), equivalent to USD 25,000.00.\nBranch Manager")
STATEMENT = (f"SAMPLE BANK LIMITED  Statement of Account  Statement Date: {MON}\n"
             "Date Particulars Cheque No Debit Credit Balance\n"
             "01-03-2026 Opening Balance 2,900,000.00\n"
             "05-03-2026 Cash deposit 50,000.00 2,950,000.00\n"
             "09-04-2026 Transfer 10,000.00 2,940,000.00\n"
             "02-05-2026 Interest 5,000.00 2,945,000.00\n"
             "02-06-2026 Excise duty 500.00 2,944,500.00\n"
             "We certify that this is a computer generated statement.  Page 1 of 2")
STATEMENT_2 = ("Date Particulars Cheque No Debit Credit Balance\n"
               "01-07-2026 Transfer 1,000.00 2,943,500.00\n02-07-2026 Charges 100.00 2,943,400.00\n"
               "03-08-2026 Deposit 5,000.00 2,948,400.00\n04-08-2026 Fee 50.00 2,948,350.00\n"
               "05-09-2026 Closing Balance 2,948,350.00\nPage 2 of 2")
UNCLEAR = "SAMPLE scan smudged xq zv"
STUDENT = {"Full Name": "SAMPLE STUDENT", "Father": "SAMPLE FATHER", "Mother": "SAMPLE MOTHER",
           "Sponsor": "FATHER", "Passport No": "A99900001"}


def _bank(monkeypatch, pages, program="KLP"):
    monkeypatch.setattr(dv, "read_pages", lambda path, max_pages=20, sideways=None: list(pages))
    text = "\n".join(pages)
    return dv.check_bank(text, STUDENT, [], program, Path("SAMPLE (A99900001)/BANK STATEMENT.pdf"), {})


def _order(results):
    """The page-order line of check_bank's results."""
    hits = [(v, m) for v, m in results if "solvency certificate" in m and
            ("page 1" in m or "one page" in m)]
    assert hits, results
    return hits[0]


# --- telling the pages apart ---------------------------------------------------------------
def test_page_kinds_survive_the_shared_words():
    # a solvency certificate says "deposit" and "balance"; a statement carries a "certify" footer
    assert dv.bank_page_kind(SOLVENCY, first=True) == dv.SOLVENCY_PAGE
    assert dv.bank_page_kind(STATEMENT, first=True) == dv.STATEMENT_PAGE
    assert dv.bank_page_kind(STATEMENT_2) == dv.STATEMENT_PAGE
    assert dv.bank_page_kind(UNCLEAR, first=True) == dv.UNCLEAR_PAGE
    assert dv.bank_page_kind("", first=True) == dv.UNCLEAR_PAGE


def test_a_thin_later_page_is_taken_as_the_statement_going_on_but_never_page_1():
    footer = "Branch: SAMPLE  Account statement  *** End of statement ***  Page 3 of 3"
    assert dv.bank_page_kind(footer) == dv.STATEMENT_PAGE
    assert dv.bank_page_kind(footer, first=True) == dv.UNCLEAR_PAGE


# --- the order ---------------------------------------------------------------------------
def test_right_order_passes_and_names_the_pages(monkeypatch):
    v, m = _order(_bank(monkeypatch, [SOLVENCY, STATEMENT, STATEMENT_2]))
    assert v == dv.PASS
    assert "page 1 is the solvency certificate and pages 2-3 are the statement" in m


def test_two_page_right_order_passes(monkeypatch):
    v, m = _order(_bank(monkeypatch, [SOLVENCY, STATEMENT]))
    assert (v, "page 2 is the statement" in m) == (dv.PASS, True)


def test_statement_first_fails(monkeypatch):
    v, m = _order(_bank(monkeypatch, [STATEMENT, SOLVENCY]))
    assert v == dv.FAIL
    assert m.startswith("page 1 is the bank statement, the solvency certificate is on page 2")
    assert "must start with the solvency certificate" in m


def test_solvency_last_fails(monkeypatch):
    v, m = _order(_bank(monkeypatch, [STATEMENT, STATEMENT_2, SOLVENCY]))
    assert v == dv.FAIL and "the solvency certificate is on page 3" in m


def test_statement_first_without_any_solvency_page_fails(monkeypatch):
    v, m = _order(_bank(monkeypatch, [STATEMENT, STATEMENT_2]))
    assert v == dv.FAIL and "no page reads as the solvency certificate" in m


def test_solvency_in_the_middle_fails(monkeypatch):
    (v, m), _ = dv.bank_order([SOLVENCY, STATEMENT, SOLVENCY, STATEMENT_2])
    assert v == dv.FAIL
    assert "a solvency certificate is on page 3, after the statement on page 2" in m
    (v, m), _ = dv.bank_order([STATEMENT, SOLVENCY, STATEMENT_2])
    assert v == dv.FAIL and "solvency certificate is on page 2" in m


def test_unclear_page_1_is_a_flag_never_a_fail(monkeypatch):
    v, m = _order(_bank(monkeypatch, [UNCLEAR, SOLVENCY, STATEMENT]))
    assert v == dv.FLAG
    assert "page 1 could not be told apart" in m and "check the order by eye" in m
    assert "reads on page 2" in m
    (v, _), _ = dv.bank_order(["", ""])                 # nothing readable at all
    assert v == dv.FLAG


def test_unclear_later_page_is_a_flag():
    (v, m), _ = dv.bank_order([SOLVENCY, STATEMENT, UNCLEAR])
    assert v == dv.FLAG and "page 3 could not be told apart" in m
    (v, m), _ = dv.bank_order([SOLVENCY, SOLVENCY, STATEMENT])
    assert v == dv.FLAG and "page 2 also reads as a solvency certificate" in m


def test_one_page_keeps_its_flag(monkeypatch):
    results = _bank(monkeypatch, [SOLVENCY])
    assert (dv.FLAG, "the bank file has only one page, so it cannot hold both a solvency "
                     "certificate and a statement") in results
    assert not any(v == dv.FAIL and "page 1" in m for v, m in results)


# --- the date comparison reads each document's own page -----------------------------------
def test_dates_are_read_from_the_identified_pages(monkeypatch):
    # statement first: the old code compared the statement's page 1 with the solvency page
    results = _bank(monkeypatch, [STATEMENT, SOLVENCY])
    assert (dv.PASS, f"solvency certificate and statement both dated {DAY}") in results


def test_date_difference_stays_a_flag(monkeypatch):
    late = STATEMENT.replace(MON, OTHER)
    results = _bank(monkeypatch, [SOLVENCY, late])
    assert any(v == dv.FLAG and "they must carry the SAME date" in m for v, m in results)


def test_unread_statement_date_stays_a_note(monkeypatch):
    results = _bank(monkeypatch, [SOLVENCY, STATEMENT_2])
    assert any(v == dv.NOTE and "generation date could not be read" in m for v, m in results)


# --- Document 07: required qualifications ----------------------------------------------------
APOSTILLE = ("e-Apostille (Convention de La Haye du 5 octobre 1961) 1. Country: Bangladesh. "
             "This public document has been signed by SAMPLE OFFICER acting in the capacity of "
             "the Controller, bears the seal of the Board. Ministry of Foreign Affairs, Dhaka. "
             "Certificate number SAMPLE-0001. Signature and seal.")
SSC = ("Board of Intermediate and Secondary Education, Dhaka. Secondary School Certificate "
       "Examination 2019. This is to certify that SAMPLE STUDENT son of SAMPLE FATHER and "
       "SAMPLE MOTHER of SAMPLE High School bearing Roll 100001 duly passed the examination "
       "in the Science group and secured GPA 5.00 on a scale of 5.00. Notary public.")
SSC_TRANSCRIPT = ("Board of Intermediate and Secondary Education, Dhaka. Academic Transcript. "
                  "Secondary School Certificate Examination 2019. Name of student SAMPLE "
                  "STUDENT. Roll 100001. Registration 900001. Subject Bangla grade A+, "
                  "English grade A+, Mathematics grade A+. GPA 5.00. Date of publication of results.")
HSC = ("Board of Intermediate and Secondary Education, Dhaka. Higher Secondary Certificate "
       "Examination 2021. This is to certify that SAMPLE STUDENT of SAMPLE College bearing "
       "Roll 200001 duly passed the examination and secured GPA 5.00. Notary public.")
HSC_TRANSCRIPT = ("Academic Transcript. Higher Secondary Certificate Examination 2021. Name of "
                  "student SAMPLE STUDENT. Roll 200001. Subject Physics grade A+, Chemistry "
                  "grade A. GPA 5.00. Date of publication of results.")
DIPLOMA = ("Bangladesh Technical Education Board, Dhaka. Diploma in Engineering (Computer). "
           "This is to certify that SAMPLE STUDENT of SAMPLE Polytechnic Institute passed the "
           "examination held in the year 2022 and obtained CGPA 3.50. Roll 300001.")
BACHELOR = ("University of SAMPLE. This is to certify that SAMPLE STUDENT has been awarded the "
            "degree of Bachelor of Science in Computer Science and Engineering. CGPA 3.60. "
            "The degree was conferred in the year 2024. Registration 400001.")
DEGREE_TRANSCRIPT_WITH_HSC = (
    "University of SAMPLE. Academic Transcript. B.Sc. in Engineering. Name of student SAMPLE "
    "STUDENT. Admission record: HSC GPA 5.00, SSC GPA 5.00. Semester 1 credit 18 grade A. "
    "Semester 2 credit 18 grade A-. CGPA 3.60. Date of issue.")
POOR = "xq zv kp"
NOTARY_PAD = ("Attested. Adv. SAMPLE NOTARY, LL.B (Hons), Dhaka Judge Court. This is to certify "
              "that the document of the student is a true copy of the original. Notary public.")


def _qual(program, pages, complete=True):
    return PC.qualification_check(program, [(str(i), t) for i, t in enumerate(pages, 1)],
                                  complete=complete)


def _verdicts(results):
    return [v for v, _ in results]


def test_levels_on_whole_words_and_the_known_traps():
    assert PC.qualifications_on(SSC) == {"SSC"}
    # "Higher Secondary Certificate" contains "Secondary ... Certificate": it is not an SSC
    assert PC.qualifications_on(HSC) == {"HSC"}
    assert PC.qualifications_on(DIPLOMA) == {"DIPLOMA"}
    assert PC.qualifications_on(BACHELOR) == {"BACHELOR"}
    # a degree transcript lists the SSC/HSC results; that is not an HSC certificate
    assert PC.qualifications_on(DEGREE_TRANSCRIPT_WITH_HSC) == {"BACHELOR"}
    # the notary's LL.B (Hons) is not the student's degree
    assert PC.qualifications_on(NOTARY_PAD) == set()
    # equivalents
    assert PC.qualifications_on("Bangladesh Madrasah Education Board. Dakhil Examination 2019") == {"SSC"}
    assert PC.qualifications_on("Bangladesh Madrasah Education Board. Alim Examination 2021") == {"HSC"}
    assert PC.qualifications_on("Pearson Edexcel GCE A Level results") == {"HSC"}
    assert PC.qualifications_on("Cambridge O Level statement of results") == {"SSC"}
    assert PC.qualifications_on("SSC (Vocational) Examination") == {"SSC"}
    assert PC.qualifications_on("HSC (BM) Examination") == {"HSC"}
    assert PC.qualifications_on("Junior School Certificate Examination 2016") == set()
    # whole words only, and "a level of" in plain English is not an A Level
    assert PC.qualifications_on("the classic glassware, a level of detail") == set()


@pytest.mark.parametrize("program", ["KLP", "EAP", "BACHELOR"])
def test_school_programs_pass_with_ssc_and_hsc(program):
    res = _qual(program, [APOSTILLE, SSC, SSC_TRANSCRIPT, APOSTILLE, HSC, HSC_TRANSCRIPT])
    assert _verdicts(res) == ["PASS"]
    assert res[0][1] == (f"academic file contains SSC (pages 2, 3), HSC (pages 5, 6) — all that "
                         f"{program} requires")


def test_master_passes_with_ssc_hsc_and_bachelor():
    res = _qual("MASTER", [APOSTILLE, SSC, APOSTILLE, HSC, APOSTILLE, BACHELOR])
    assert _verdicts(res) == ["PASS"] and "Bachelor's degree (page 6)" in res[0][1]


@pytest.mark.parametrize("program", ["KLP", "EAP", "BACHELOR", "MASTER"])
def test_missing_ssc_fails(program):
    extra = [APOSTILLE, BACHELOR] if program == "MASTER" else []
    res = _qual(program, [APOSTILLE, HSC, HSC_TRANSCRIPT] + extra)
    assert ("FAIL", f"academic file has no SSC — {program} requires SSC and HSC (or a Diploma)"
                    + (" and Bachelor's degree" if program == "MASTER" else "")) in res


@pytest.mark.parametrize("program", ["KLP", "EAP", "BACHELOR", "MASTER"])
def test_missing_hsc_fails(program):
    extra = [APOSTILLE, BACHELOR] if program == "MASTER" else []
    res = _qual(program, [APOSTILLE, SSC, SSC_TRANSCRIPT] + extra)
    fails = [m for v, m in res if v == "FAIL"]
    assert len(fails) == 1 and fails[0].startswith("academic file has no HSC or Diploma — ")


def test_missing_bachelor_fails_for_master_only():
    pages = [APOSTILLE, SSC, APOSTILLE, HSC]
    res = _qual("MASTER", pages)
    assert ("FAIL", "academic file has no Bachelor's degree — MASTER requires SSC and HSC "
                    "(or a Diploma) and Bachelor's degree") in res
    assert _verdicts(_qual("BACHELOR", pages)) == ["PASS"]


def test_each_missing_qualification_gets_its_own_line():
    res = _qual("MASTER", [APOSTILLE, NOTARY_PAD])
    assert _verdicts(res) == ["FAIL", "FAIL", "FAIL"]


def test_a_diploma_counts_instead_of_hsc():
    res = _qual("KLP", [APOSTILLE, SSC, APOSTILLE, DIPLOMA])
    assert _verdicts(res) == ["PASS"] and "Diploma (page 4)" in res[0][1]


def test_a_degree_transcript_that_mentions_hsc_is_not_an_hsc():
    res = _qual("MASTER", [APOSTILLE, SSC, SSC_TRANSCRIPT, APOSTILLE, DEGREE_TRANSCRIPT_WITH_HSC])
    fails = [m for v, m in res if v == "FAIL"]
    assert len(fails) == 1 and fails[0].startswith("academic file has no HSC or Diploma")


def test_poor_text_is_a_flag_not_a_fail():
    res = _qual("MASTER", [APOSTILLE, SSC, APOSTILLE, HSC, APOSTILLE, POOR])
    assert ("FLAG", "could not confirm Bachelor's degree in the academic file (page 6 could not "
                    "be read) — check by eye (MASTER requires SSC and HSC (or a Diploma) and "
                    "Bachelor's degree)") in res
    assert "FAIL" not in _verdicts(res)


def test_an_unnamed_certificate_page_is_a_flag_not_a_fail():
    garbled = ("Board of Intermediate and Secondary Education. Academic Transcript. Name of "
               "student SAMPLE STUDENT. Name of father SAMPLE FATHER. Roll 100001. "
               "Subject Bangla grade A+. Subject English grade A+. GPA 5.00.")
    res = _qual("KLP", [APOSTILLE, garbled, APOSTILLE, HSC])
    assert _verdicts(res) == ["PASS", "FLAG"]
    assert "page 2 could not be named as a qualification" in res[1][1]


def test_a_file_cut_at_the_page_limit_is_a_flag():
    res = _qual("KLP", [APOSTILLE, SSC], complete=False)
    assert _verdicts(res) == ["PASS", "FLAG"] and "not every page was read" in res[1][1]


def test_no_program_means_no_requirement():
    assert _qual("", [SSC]) == [] and _qual("OTHER", [SSC]) == []


def test_requirements_live_in_the_rules_file():
    assert R.REQUIRED_QUALIFICATIONS["MASTER"] == [("SSC",), ("HSC", "DIPLOMA"), ("BACHELOR",)]
    for p in ("KLP", "EAP", "BACHELOR"):
        assert R.REQUIRED_QUALIFICATIONS[p] == [("SSC",), ("HSC", "DIPLOMA")]


# --- wired into check_academic and the CHECKS table -----------------------------------------
def test_checks_table_passes_the_program_to_check_academic():
    text = "\n".join([APOSTILLE, SSC, APOSTILLE, HSC])
    res = dv.CHECKS["academic"](text, STUDENT, [], {}, "MASTER", None)
    assert any(v == dv.FAIL and m.startswith("academic file has no Bachelor's degree") for v, m in res)
    res = dv.CHECKS["academic"](text, STUDENT, [], {}, "KLP", None)
    assert any(v == dv.PASS and m.endswith("all that KLP requires") for v, m in res)


def test_check_academic_without_a_program_is_unchanged():
    res = dv.check_academic("\n".join([SSC, HSC]), STUDENT, [])
    assert not any("academic file" in m for _, m in res)


def test_check_academic_reads_the_pages_of_the_file(monkeypatch):
    pages = [APOSTILLE, SSC, SSC_TRANSCRIPT, APOSTILLE, HSC, HSC_TRANSCRIPT]
    monkeypatch.setattr(dv, "read_pages", lambda path, max_pages=20, sideways=None: list(pages))
    monkeypatch.setattr(PC, "academic_check", lambda path, pages_text: [])
    # the whole-file read stops at page 2; the page-by-page read finds the HSC on page 5
    res = dv.check_academic("\n".join(pages[:2]), STUDENT, [],
                            Path("SAMPLE (A99900001)/ACADEMIC CERTIFICATE.pdf"), {}, "BACHELOR")
    assert (dv.PASS, "academic file contains SSC (pages 2, 3), HSC (pages 5, 6) — all that "
                     "BACHELOR requires") in res


def test_two_academic_files_are_judged_together(monkeypatch, tmp_path):
    first, second = tmp_path / "ACADEMIC CERTIFICATE SSC.pdf", tmp_path / "ACADEMIC CERTIFICATE HSC.pdf"
    for f in (first, second):
        f.write_bytes(b"%PDF-1.4 SAMPLE")
    content = {second.name: [APOSTILLE, HSC], first.name: [APOSTILLE, SSC]}
    monkeypatch.setattr(dv, "read_pages",
                        lambda path, max_pages=20, sideways=None: list(content[Path(path).name]))
    monkeypatch.setattr(PC, "academic_check", lambda path, pages_text: [])
    texts = {}
    # verify_student's order: sorted names, so the HSC file comes first here
    earlier = dv.check_academic(HSC, STUDENT, [], second, texts, "KLP")
    assert (dv.NOTE, f"the required qualifications are judged together with {first.name}") in earlier
    assert not any(v == dv.FAIL for v, _ in earlier)
    last = dv.check_academic(SSC, STUDENT, [], first, texts, "KLP")
    line = [m for v, m in last if m.startswith("academic file contains")]
    assert line and "all that KLP requires" in line[0]
    assert f"{second.name} p2" in line[0] and f"{first.name} p2" in line[0]
