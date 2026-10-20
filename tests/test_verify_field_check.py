"""The field check: does the portal's record agree with the documents? (src/verify/field_check.py)

  B6  words are matched whole ("ali" is not in "khalil", "male" is not in "female"); a
      value read after its label ("Father's Name:", "Sex:", "GPA") that differs gives
      DIFFERS; a GPA is compared as a number to 2 decimals ("5.00" is not "55"); a
      document that reads well but lacks the value gives NOT CONFIRMED, and UNREADABLE
      is kept for documents that cannot be read at all; the stale issue-date cache
      downgrade gives NOT CONFIRMED.
  P3  an ID card / CV is recorded as present only and never read.
      checked_portal_values: the portal values the check compares, and nothing else
      (a Current Stage edit must not re-queue a student).

Every name, passport and number is invented; every file is written under tmp_path; the
passport-issue cache is never read.

Run from the BOT folder:
    .venv\\Scripts\\python.exe -m pytest tests\\test_verify_field_check.py -q
"""
import sys
from pathlib import Path

import pytest

BOT_ROOT = Path(__file__).resolve().parent.parent
if str(BOT_ROOT) not in sys.path:
    sys.path.insert(0, str(BOT_ROOT))

from src.sheets import progress_builder as pb  # noqa: E402
from src.verify import doc_verifier as dv  # noqa: E402
from src.verify import field_check as fc  # noqa: E402
from src.verify import rules as R  # noqa: E402
from src.verify.common import norm  # noqa: E402
from verify_fixtures import FILLER, PASSPORTS, folder, mrz_lines, student  # noqa: E402

READ = f"{FILLER} {FILLER}"           # enough real words for doc_verifier.readable()
NOISE = "xq zz 1 3 kk ## 9 qq"


@pytest.fixture(autouse=True)
def _no_live_caches(monkeypatch):
    """The passport-issue cache lives under data\\: the tests never read it."""
    monkeypatch.setattr(pb, "issue_date_for", lambda s: s.get("Passport Issue Date", ""))
    monkeypatch.setattr(fc, "issue_cache_age_hours", lambda: 1.0)


def check(field, value, texts, who=None):
    return fc.check_field(field, value, texts, who)


# --------------------------------------------------------------------------- gender

def test_male_is_not_found_inside_female_and_a_labelled_sex_differs():
    result, detail = check("Gender", "MALE", {"01 Passport": f"Sex: F {READ} FEMALE"})
    assert result == fc.DIFFERS
    assert detail == "01 Passport shows FEMALE instead"


def test_an_unlabelled_female_is_never_a_match_for_male():
    result, _ = check("Gender", "MALE", {"01 Passport": f"{READ} FEMALE"})
    assert result == fc.NOT_CONFIRMED


@pytest.mark.parametrize("portal, text", [
    ("MALE", "Sex: M"), ("MALE", "SEX / M"), ("MALE", "Gender: Male"), ("MALE", "male"),
    ("FEMALE", "Sex: F"), ("FEMALE", "Gender : Female"), ("FEMALE", "FEMALE"),
])
def test_gender_matches(portal, text):
    assert check("Gender", portal, {"03 Student NID": f"{READ} {text}"})[0] == fc.MATCH


def test_both_words_unlabelled_confirm_nothing():
    assert check("Gender", "MALE", {"04 Birth Certificate": f"{READ} Male / Female"})[0] == fc.NOT_CONFIRMED


def test_a_labelled_sex_wins_over_a_stray_word_on_the_same_document():
    assert check("Gender", "MALE", {"01 Passport": f"{READ} Sex: F male"})[0] == fc.DIFFERS


def test_a_match_on_another_document_wins_over_a_difference():
    texts = {"01 Passport": f"Sex: F {READ}", "03 Student NID": f"{READ} Gender: Male"}
    assert check("Gender", "MALE", texts) == (fc.MATCH, "found on 03 Student NID")


def test_the_machine_readable_sex_is_used(monkeypatch):
    monkeypatch.setattr(dv, "passport_mrz", lambda t: {"sex": "F", "line2_ok": True}, raising=False)
    assert check("Gender", "MALE", {"01 Passport": NOISE}) == (fc.DIFFERS, "01 Passport shows FEMALE instead")
    monkeypatch.setattr(dv, "passport_mrz", lambda t: {"sex": "M", "dob_ok": True}, raising=False)
    assert check("Gender", "MALE", {"01 Passport": NOISE}) == (
        fc.MATCH, "found in the machine-readable line of 01 Passport")


def test_an_untrusted_machine_readable_line_is_ignored(monkeypatch):
    monkeypatch.setattr(dv, "passport_mrz", lambda t: {"sex": "F", "line2_ok": False}, raising=False)
    assert check("Gender", "MALE", {"01 Passport": f"{READ} Sex: M"})[0] == fc.MATCH
    assert check("Gender", "MALE", {"01 Passport": NOISE})[0] == fc.UNREADABLE


def test_the_real_machine_readable_line_when_doc_verifier_reads_it():
    if not hasattr(dv, "passport_mrz"):
        pytest.skip("doc_verifier.passport_mrz (WP1) is not in this tree yet")
    line1, line2 = mrz_lines(PASSPORTS[0], "2005-03-14", "2031-01-01", sex="F")
    text = f"PEOPLE'S REPUBLIC OF BANGLADESH\n{line1}\n{line2}"
    assert check("Gender", "MALE", {"01 Passport": text})[0] == fc.DIFFERS
    assert check("Gender", "FEMALE", {"01 Passport": text})[0] == fc.MATCH


# --------------------------------------------------------------------------- GPA

@pytest.mark.parametrize("text, result", [
    ("GPA 5.00", fc.MATCH),
    ("G.P.A. 5.00 on a scale of 5.00", fc.MATCH),
    ("GPA: 5", fc.MATCH),                      # a bare integer counts after the label
    ("Result 5.00", fc.MATCH),                 # no GPA label on the page: any decimal
    ("GPA: 4.83", fc.DIFFERS),
    ("Roll 55 Board 55", fc.NOT_CONFIRMED),    # "5.00" is not "55" (the old prefix match)
    ("GPA 55", fc.NOT_CONFIRMED),
    ("Result 5", fc.NOT_CONFIRMED),            # an unlabelled integer is not a GPA
])
def test_gpa_is_compared_as_a_number(text, result):
    assert check("SSC GPA", "5.00", {"07 Academic Certificate & Transcript": f"{READ} {text}"})[0] == result


def test_a_differing_gpa_names_what_was_read():
    result, detail = check("HSC GPA", "5.00", {"07 Academic Certificate & Transcript": f"{READ} GPA: 4.83"})
    assert result == fc.DIFFERS and detail == "07 Academic Certificate & Transcript shows GPA 4.83 instead"


def test_ssc_and_hsc_gpas_in_one_academic_file():
    texts = {"07 Academic Certificate & Transcript":
             f"{READ} Secondary School Certificate GPA 5.00 Higher Secondary Certificate GPA 4.83"}
    assert check("SSC GPA", "5.00", texts)[0] == fc.MATCH
    assert check("HSC GPA", "4.83", texts)[0] == fc.MATCH
    assert check("HSC GPA", "4.50", texts)[0] == fc.DIFFERS


def test_a_scale_after_the_label_is_not_the_gpa():
    texts = {"07 Academic Certificate & Transcript": f"{READ} GPA (out of 5) 4.83"}
    assert check("HSC GPA", "4.83", texts)[0] == fc.MATCH
    assert fc.labelled_gpas("CGPA 3.45 out of 4.00") == [3.45]
    assert fc.labelled_gpas("GPA 4.833 GPA 55") == []


def test_a_labelled_gpa_that_may_be_another_certificates_is_not_a_difference():
    """Subject grade points (5.00, 4.00) are decimals too: when the portal's value is
    printed unlabelled and the labelled one differs, it is not confirmed either way."""
    text = f"{READ} Bangla 5.00 English 5.00 Mathematics 4.00 GPA 4.83"
    result, detail = check("HSC GPA", "5.00", {"07 Academic Certificate & Transcript": text})
    assert result == fc.NOT_CONFIRMED and "4.83" in detail


def test_a_gpa_label_in_unreadable_noise_proves_nothing():
    texts = {"07 Academic Certificate & Transcript": f"{NOISE} GPA 4.83"}
    assert check("HSC GPA", "5.00", texts)[0] == fc.UNREADABLE


def test_a_non_numeric_portal_gpa_is_matched_as_words():
    assert check("CGPA", "FIRST CLASS", {"07 Academic Certificate & Transcript": f"{READ} First Class"})[0] == fc.MATCH


# --------------------------------------------------------------------------- names

def test_a_labelled_fathers_name_that_differs():
    text = f"{READ} Father's Name: SAMPLE OTHERMAN Mother's Name: SAMPLE MOTHER"
    result, detail = check("Father", "MD SAMPLE FATHER", {"06 Family Relationship Certificate": text},
                           student(Father="MD SAMPLE FATHER"))
    assert result == fc.DIFFERS
    assert detail == ("06 Family Relationship Certificate shows 'SAMPLE OTHERMAN' as the father's "
                      "name instead")


def test_an_ocr_near_miss_after_the_label_is_not_a_difference():
    text = f"{READ} Father's Name: SAMPLE FATHAR KHAN"
    assert check("Father", "SAMPLE FATHER", {"04 Birth Certificate": text}, student())[0] == fc.NOT_CONFIRMED


def test_the_portal_name_anywhere_on_a_document_is_a_match():
    texts = {"04 Birth Certificate": f"{READ} Father's Name: SAMPLE OTHERMAN",
             "05 Father NID": f"{READ} SAMPLE FATHER"}
    assert check("Father", "MD SAMPLE FATHER", texts, student()) == (fc.MATCH, "found on 05 Father NID")


def test_honorifics_are_ignored_anywhere():
    assert check("Father", "MD. SAMPLE FATHER", {"04 Birth Certificate": f"{READ} SAMPLE MD FATHER"})[0] == fc.MATCH
    assert check("Mother", "MOSAMMAT SAMPLE MOTHER", {"04 Birth Certificate": f"{READ} MST SAMPLE MOTHER"})[0] == fc.MATCH


def test_a_labelled_name_that_is_someone_else_on_the_record_proves_nothing():
    """The first "Name:" on a family certificate is often the father's."""
    who = student(Father="SAMPLE PAPA")
    texts = {"06 Family Relationship Certificate": f"{READ} Name: SAMPLE PAPA"}
    assert check("Full Name", "SAMPLE STUDENT ONE", texts, who)[0] == fc.NOT_CONFIRMED
    assert check("Full Name", "SAMPLE STUDENT ONE", texts)[0] == fc.DIFFERS      # nobody to compare with


def test_a_labelled_student_name_that_differs():
    texts = {"07 Academic Certificate & Transcript": f"{READ} Name of Student: SAMPLE PERSON TWO"}
    result, detail = check("Full Name", "SAMPLE STUDENT ONE", texts, student())
    assert result == fc.DIFFERS and "'SAMPLE PERSON TWO' as the name" in detail


@pytest.mark.parametrize("text, field, reads", [
    ("Father's Name: SAMPLE OTHERMAN Date of Birth", "Father", ["sample otherman"]),
    ("Fathers Name SAMPLE OTHERMAN NID 5551234567", "Father", ["sample otherman"]),
    ("Name of Mother: SAMPLE MAMA", "Mother", ["sample mama"]),
    ("Name of Student: SAMPLE STUDENT ONE Roll 123", "Full Name", ["sample student one"]),
    ("Given Name: STUDENT ONE", "Full Name", []),
    ("Name of Institution: SAMPLE COLLEGE", "Full Name", []),
    ("my father's name is SAMPLE OTHERMAN and", "Father", ["sample otherman"]),
])
def test_labelled_names(text, field, reads):
    assert fc.labelled_names(text)[field] == reads


def test_a_labels_own_words_are_not_part_of_a_name():
    assert fc.without_name_labels("Father's Name: SAMPLE OTHERMAN").split() == [":", "sample", "otherman"]
    assert "student" not in fc.without_name_labels("Name of Student: SAMPLE ONE")
    # on one line of OCR text the word before "Name" ends the value before: it stays
    assert fc.without_name_labels("Mother's Name: SAMPLE MAMA Name: X").split() == [
        ":", "sample", "mama", ":", "x"]


def test_ali_is_not_found_in_khalil():
    texts = {"04 Birth Certificate": f"{READ} Father's Name: KHALIL SAMPLE"}
    assert check("Father", "MD ALI", texts, student(Father="MD ALI"))[0] != fc.MATCH
    assert check("Father", "ALI SAMPLE", {"04 Birth Certificate": f"{READ} MD ALI SAMPLE"})[0] == fc.MATCH
    assert check("District", "ALI", {"06 Family Relationship Certificate": f"{READ} KHALIL"})[0] != fc.MATCH


def test_other_text_fields_need_whole_words():
    texts = {"06 Family Relationship Certificate": f"{READ} SAMPLE DISTRICTS"}
    assert check("District", "SAMPLE DISTRICT", texts)[0] == fc.MATCH        # one of two words is enough
    assert check("District", "DISTRICT", texts)[0] == fc.NOT_CONFIRMED       # "districts" is another word
    assert check("Address", "1 SAMPLE ROAD, SAMPLE TOWN",
                 {"04 Birth Certificate": f"{READ} Sample Road Sample Town"})[0] == fc.MATCH


# --------------------------------------------------------------------------- NOT CONFIRMED / UNREADABLE

def test_a_readable_document_without_the_value_is_not_confirmed():
    result, detail = check("District", "SAMPLE DISTRICT", {"04 Birth Certificate": READ})
    assert result == fc.NOT_CONFIRMED == "NOT CONFIRMED"
    assert detail == "not found on 04 Birth Certificate (OCR may have missed it)"


def test_unreadable_noise_is_unreadable():
    result, detail = check("District", "SAMPLE DISTRICT", {"04 Birth Certificate": NOISE})
    assert result == fc.UNREADABLE and "could not read 04 Birth Certificate" in detail


def test_no_document_is_still_no_document():
    assert check("Sponsor Occupation", "BUSINESS", {"04 Birth Certificate": READ})[0] == fc.NO_DOC


# --------------------------------------------------------------------------- dates and numbers (unchanged)

def test_dates_still_match_or_differ():
    assert check("DOB", "2005-03-14", {"04 Birth Certificate": f"{READ} Date of Birth: 14/03/2005"})[0] == fc.MATCH
    result, detail = check("DOB", "2005-03-14", {"04 Birth Certificate": f"{READ} Date of Birth: 15/04/2004"})
    assert result == fc.DIFFERS and "2004-04-15" in detail
    result, detail = check("DOB", "2005-03-14", {"04 Birth Certificate": READ})
    assert result == fc.NOT_CONFIRMED and detail.startswith("not found on 04 Birth Certificate")
    assert check("DOB", "2005-03-14", {"04 Birth Certificate": NOISE})[0] == fc.UNREADABLE


def test_a_passport_date_in_the_machine_readable_line():
    line1, line2 = mrz_lines(PASSPORTS[0], "2005-03-14", "2031-01-01")
    texts = {"01 Passport": f"{NOISE}\n{line1}\n{line2}"}
    assert check("DOB", "2005-03-14", texts) == (fc.MATCH, "found in the machine-readable line of 01 Passport")


def test_passport_number_rules_are_kept():
    assert check("Passport No", "A99900001", {"01 Passport": f"{READ} A99900002"}) == (
        fc.DIFFERS, "the passport shows A99900002 instead")
    assert check("Passport No", "A99900001", {"01 Passport": f"{READ} 499900001"})[0] == fc.MATCH


def test_the_passport_validity_rule_is_kept():
    who = student(**{"Passport Issue": "2021-01-02", "Passport Expiry": "2031-01-01"})
    result, detail = check("Passport Issue", "2021-01-02", {"01 Passport": READ}, who)
    assert result == fc.MATCH and "10-year passport" in detail


def test_the_sponsor_occupation_rule_is_kept():
    texts = {"09 Financial (Trade Licence / TIN / Employment)": f"{READ} Trade Licence SAMPLE TRADERS"}
    assert check("Sponsor Occupation", "BUSINESS", texts)[0] == fc.MATCH


# --------------------------------------------------------------------------- check_student

def _presence_only_rules(monkeypatch):
    """WP1's rules know "id_card_cv" as presence only; stand in for them until merged."""
    if "id_card_cv" not in getattr(R, "PRESENCE_ONLY", ()):
        monkeypatch.setattr(R, "PRESENCE_ONLY", frozenset({"id_card_cv"}), raising=False)
    if fc.classify("ID CARD CV.pdf") != "id_card_cv":
        real = fc.classify
        monkeypatch.setattr(fc, "classify",
                            lambda name: "id_card_cv" if "id card cv" in norm(name) else real(name))


def test_check_student_never_reads_an_id_card_cv(tmp_path, monkeypatch):
    _presence_only_rules(monkeypatch)
    who = student()
    d = folder(tmp_path, "KOREAN LANGUAGE PROGRAM (KLP)", who["Full Name"], who["Passport No"], {
        "PASSPORT.pdf": f"{READ} SAMPLE STUDENT ONE Sex: M A99900001",
        "ID CARD CV.pdf": f"{READ} Name: SAMPLE SOMEONE ELSE Sex: F",
        ".download_complete": "v2\nx",
    })
    seen = []

    def spy(path, max_pages=20):
        seen.append(Path(path).name)
        return dv.read_document(path, max_pages)

    monkeypatch.setattr(fc, "read_document", spy)
    rows = {r["field"]: r for r in fc.check_student(who["Passport No"], d, who)}
    assert seen == ["PASSPORT.pdf"]
    assert rows["Gender"]["result"] == fc.MATCH
    assert rows["Full Name"]["result"] == fc.MATCH


def test_field_check_reads_through_its_own_read_document_name():
    """WP3's text cache patches fc.read_document: the name import must stay."""
    assert fc.read_document is dv.read_document


def test_a_stale_issue_cache_turns_a_difference_into_not_confirmed(tmp_path, monkeypatch):
    who = student(**{"Passport Issue Date": "2020-05-05", "Passport Expiry": "2031-01-01"})
    d = folder(tmp_path, "KOREAN LANGUAGE PROGRAM (KLP)", who["Full Name"], who["Passport No"], {
        "PASSPORT.pdf": f"{READ} Date of Issue 02/01/2021",
    })
    monkeypatch.setattr(fc, "issue_cache_age_hours", lambda: 1.0)
    fresh = {r["field"]: r for r in fc.check_student(who["Passport No"], d, who)}
    assert fresh["Passport Issue"]["result"] == fc.DIFFERS
    monkeypatch.setattr(fc, "issue_cache_age_hours", lambda: None)
    stale = {r["field"]: r for r in fc.check_student(who["Passport No"], d, who)}
    assert stale["Passport Issue"]["result"] == fc.NOT_CONFIRMED
    assert "cache that is missing" in stale["Passport Issue"]["detail"]


# --------------------------------------------------------------------------- checked_portal_values

def test_checked_portal_values_ignore_workflow_fields():
    a = fc.checked_portal_values(student(**{"Current Stage": "Documents", "Current Status": "Open",
                                            "Progress %": "40"}))
    b = fc.checked_portal_values(student(**{"Current Stage": "Visa", "Current Status": "Closed",
                                            "Progress %": "90"}))
    assert a == b
    for key in ("Current Stage", "Current Status", "Progress %", "Email", "Mobile", "Sponsor"):
        assert key not in a
    assert a["Father"] == "SAMPLE FATHER" and a["SSC GPA"] == "5.00" and a["HSC GPA"] == "4.83"
    assert a["Program"] == "KOREAN LANGUAGE PROGRAM (KLP)" and a["Intake"] == "MARCH 2027"
    assert a["Gender"] == "MALE"                     # as the sheet shows it
    assert set(a) - {"Program", "Intake"} <= set(fc.SOURCES)


def test_checked_portal_values_follow_the_checked_fields():
    base = fc.checked_portal_values(student())
    assert fc.checked_portal_values(student(Father="SAMPLE OTHERMAN"))["Father"] != base["Father"]
    assert "CGPA" not in base                        # KLP has no university columns
    master = fc.checked_portal_values(student(Program="MASTER'S DEGREE", CGPA="3.45"))
    assert master["CGPA"] == "3.45" and master["Program"] == "MASTER'S DEGREE"


# --------------------------------------------------------------------------- the CLI report

def test_the_report_counts_not_confirmed(tmp_path):
    from openpyxl import load_workbook
    rows = [{"student": "SAMPLE STUDENT ONE", "passport": "A99900001", "program": "KLP",
             "field": f, "portal": "x", "result": r, "detail": ""}
            for f, r in (("DOB", fc.MATCH), ("Father", fc.NOT_CONFIRMED), ("Gender", fc.DIFFERS),
                         ("District", fc.UNREADABLE))]
    path = fc.write_report(rows, tmp_path / "fields.xlsx")
    wb = load_workbook(path)
    summary = list(wb["Summary"].values)
    assert summary[0] == ("Student", "Passport", "Program", "Checked", "Match", "Differs",
                          "Not confirmed", "Unreadable", "No document", "Blank")
    assert summary[1][3:] == (4, 1, 1, 1, 1, 0, 0)
    fill = wb["Field check"].cell(row=3, column=6).fill.fgColor.rgb
    assert fill.endswith("DDEBF7")
