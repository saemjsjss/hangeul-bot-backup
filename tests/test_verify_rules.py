"""The document check's shared contract (src/verify/common.py) and the synthetic fixtures
every document-check test builds on (tests/verify_fixtures.py).

  common: the verdict names and their order (a row: FAIL > ERROR > FLAG > PASS; a student:
  INCOMPLETE > FAIL > ERROR > REVIEW > PASS), CheckError / NotCached, the name rules
  (honorifics dropped anywhere, whole words only, a misread letter forgiven in long
  parts), the portal's Sponsor values, and Levenshtein distance. doc_verifier still
  exports norm, name_tokens, name_in and student_verdict under their old names.

Every name, passport and number here is invented (SAMPLE names, A9990000n passports);
every file is written under tmp_path.

Run from the BOT folder:
    .venv\\Scripts\\python.exe -m pytest tests\\test_verify_rules.py -q
"""
import re
import sys
from pathlib import Path

import pytest

BOT_ROOT = Path(__file__).resolve().parent.parent
if str(BOT_ROOT) not in sys.path:
    sys.path.insert(0, str(BOT_ROOT))

from src.verify import common as C  # noqa: E402
from src.verify import doc_verifier as dv  # noqa: E402
from verify_fixtures import (FILLER, PASSPORTS, folder, make_image, make_pdf, make_qr,  # noqa: E402
                             mrz_lines, student)


# --------------------------------------------------------------------------- the contract

def test_rules_version_and_verdict_names():
    assert C.RULES_VERSION == "2026-10-21.1"
    assert (C.PASS, C.FLAG, C.FAIL, C.MISSING, C.NOTE, C.ERROR) == (
        "PASS", "FLAG", "FAIL", "MISSING", "NOTE", "ERROR")
    assert (C.INCOMPLETE, C.REVIEW) == ("INCOMPLETE", "REVIEW")


def test_not_cached_is_a_check_error_that_names_the_file(tmp_path):
    p = tmp_path / "PASSPORT.pdf"
    e = C.NotCached(p)
    assert isinstance(e, C.CheckError) and isinstance(e, Exception)
    assert e.path == p and e.what == "text"
    assert "PASSPORT.pdf" in str(e) and "text" in str(e)
    assert C.NotCached("x/BANK STATEMENT.pdf", what="image facts").what == "image facts"
    assert "BANK STATEMENT.pdf" in str(C.NotCached("x/BANK STATEMENT.pdf", what="image facts"))
    with pytest.raises(C.CheckError):
        raise C.NotCached(p)


def test_doc_verifier_reexports_the_shared_helpers():
    """field_check imports norm from doc_verifier, auto_verify calls dv.student_verdict:
    both must keep working, and be the very same functions as common's."""
    assert dv.norm is C.norm
    assert dv.name_tokens is C.name_tokens
    assert dv.name_in is C.name_in
    assert dv.student_verdict is C.student_verdict
    from src.verify import field_check as fc
    assert fc.norm is C.norm
    assert (dv.PASS, dv.FLAG, dv.FAIL, dv.MISSING, dv.NOTE, dv.ERROR) == (
        C.PASS, C.FLAG, C.FAIL, C.MISSING, C.NOTE, C.ERROR)


def test_norm_is_unchanged():
    assert C.norm("  SAMPLE\tStudent\n ONE  ") == "sample student one"
    assert C.norm("") == "" and C.norm(None) == ""


# --------------------------------------------------------------------------- name rules

@pytest.mark.parametrize("name, tokens", [
    ("SAMPLE STUDENT ONE", ["sample", "student", "one"]),
    ("MD SAMPLE STUDENT", ["sample", "student"]),             # honorific first
    ("SAMPLE MD STUDENT", ["sample", "student"]),             # ...in the middle
    ("SAMPLE STUDENT MD", ["sample", "student"]),             # ...last
    ("Md. Sample Student", ["sample", "student"]),            # with a dot
    ("Md.Sample", ["sample"]),                                # no space after the dot
    ("MST. SAMPLE MOTHER", ["sample", "mother"]),
    ("MOST SAMPLE MOTHER", ["sample", "mother"]),
    ("MRS SAMPLE MOTHER", ["sample", "mother"]),
    ("MISS SAMPLE", ["sample"]),
    ("MS SAMPLE", ["sample"]),
    ("MR SAMPLE FATHER", ["sample", "father"]),
    ("LATE SAMPLE FATHER", ["sample", "father"]),
    ("SK SAMPLE FATHER", ["sample", "father"]),
    ("MOHD SAMPLE FATHER", ["sample", "father"]),
    ("A.K.M. SAMPLE", ["sample"]),                            # initials are dropped
    ("A K M SAMPLE", ["sample"]),
    ("SAMPLE AL STUDENT", ["sample", "student"]),             # 2-letter parts are dropped
    ("SAMPLE SAMPLE STUDENT", ["sample", "student"]),         # repeats once, order kept
    ("SAMPLE-STUDENT", ["sample", "student"]),                # split on anything not a letter
    ("", []),
    ("MD", []),
    ("MD MST", []),
])
def test_name_tokens(name, tokens):
    assert C.name_tokens(name) == tokens


@pytest.mark.parametrize("prefix", [
    "muhammad", "mohammad", "mohammed", "mohamad", "muhammed", "mohammod", "mohamed", "mohmmad",
    "mosammat", "mossammat", "mosamat", "mosammet",
])
def test_name_tokens_drops_the_muhammad_and_mosammat_spellings_anywhere(prefix):
    assert C.name_tokens(f"{prefix} SAMPLE STUDENT") == ["sample", "student"]
    assert C.name_tokens(f"SAMPLE {prefix} STUDENT") == ["sample", "student"]
    assert C.name_tokens(f"SAMPLE STUDENT {prefix.upper()}") == ["sample", "student"]


@pytest.mark.parametrize("kept", ["mohan", "mahmud", "mostafa", "mosharraf", "mona"])
def test_name_tokens_keeps_names_that_only_look_like_an_honorific(kept):
    assert C.name_tokens(f"SAMPLE {kept}") == ["sample", kept]


def test_name_in_needs_whole_words():
    assert C.name_in("Name: KHALIL", "ALI") is False                # 'ali' inside 'khalil'
    assert C.name_in("Name: ALI", "ALI") is True
    assert C.name_in("SAMPLE KHALIL", "ALI SAMPLE") is False
    assert C.name_in("the samples", "SAMPLE") is True               # one letter more, 5+ letters
    assert C.name_in("examplesample", "SAMPLE") is False            # glued into a longer word


def test_name_in_needs_every_part_of_a_short_name():
    # 1-2 parts: all of them. The old rule took one shared surname as a match.
    assert C.name_in("Father: SAMPLE FATHER", "SAMPLE MOTHER") is False
    assert C.name_in("Mother: SAMPLE MOTHER", "SAMPLE MOTHER") is True
    assert C.name_in("SAMPLE", "SAMPLE") is True


def test_name_in_allows_one_missing_part_of_a_longer_name():
    assert C.name_in("Name: SAMPLE STUDENT", "SAMPLE STUDENT ONE") is True
    assert C.name_in("Name: STUDENT ONE", "SAMPLE STUDENT ONE") is True
    assert C.name_in("Name: SAMPLE", "SAMPLE STUDENT ONE") is False
    assert C.name_in("Name: SAMPLE STUDENT", "SAMPLE TALL STUDENT ONE") is False   # 2 of 4


def test_name_in_ignores_honorifics_on_either_side():
    assert C.name_in("Name: MD. SAMPLE STUDENT", "SAMPLE STUDENT") is True
    assert C.name_in("Name: SAMPLE STUDENT", "MOHAMMAD SAMPLE STUDENT") is True
    assert C.name_in("Name: MOSAMMAT SAMPLE MOTHER", "MST SAMPLE MOTHER") is True


def test_name_in_forgives_an_ocr_misread_in_long_parts_only():
    assert C.name_in("Name: SAMPLE STUOENT", "SAMPLE STUDENT") is True    # D read as O (7 letters)
    assert C.name_in("Name: SAMPE STUDENT", "SAMPLE STUDENT") is True     # a letter lost
    assert C.name_in("Name: SAMPLLE STUDENT", "SAMPLE STUDENT") is True   # a letter doubled
    assert C.name_in("Name: SAMPLE STUDENT", "SAMPLE STUDIES") is False   # not a misread
    assert C.name_in("Name: SAMPLE ONO", "SAMPLE ONE") is False           # 3 letters: exact only
    assert C.name_in("Name: SAMPLE STUD", "SAMPLE STUDENT") is False      # length differs by 3


def test_name_in_is_none_when_there_is_no_name_to_look_for():
    assert C.name_in("anything at all", "") is None
    assert C.name_in("anything at all", "MD") is None
    assert C.name_in("anything at all", None) is None
    assert C.name_in("", "SAMPLE") is False
    assert C.name_in(None, "SAMPLE") is False


def test_name_in_reads_across_lines_and_case():
    text = "PEOPLE'S REPUBLIC\nName\nsample\nStudent ONE\n"
    assert C.name_in(text, "SAMPLE STUDENT ONE") is True


# --------------------------------------------------------------------------- sponsor

@pytest.mark.parametrize("value, kind", [
    ("FATHER", "FATHER"), ("Father", "FATHER"), (" father ", "FATHER"), ("Father's", "FATHER"),
    ("MOTHER", "MOTHER"), ("mother", "MOTHER"),
    ("BOTH PARENTS", "PARENTS"), ("Parents", "PARENTS"), ("Father & Mother", "PARENTS"),
    ("father and mother", "PARENTS"), ("Mother and Father", "PARENTS"), ("FATHER/MOTHER", "PARENTS"),
    ("Self", "SELF"), ("SELF FUNDED", "SELF"), ("Self-funded", "SELF"), ("own", "SELF"),
    ("Student", "SELF"), ("APPLICANT", "SELF"),
    ("UNCLE SAMPLE", "OTHER"), ("Brother", "OTHER"), ("Grandfather", "OTHER"),
    ("Step Father", "OTHER"), ("Father in law", "OTHER"), ("SAMPLE COMPANY LTD", "OTHER"),
    ("", ""), ("   ", ""), (None, ""), ("-", ""),
])
def test_sponsor_kind(value, kind):
    assert C.sponsor_kind(value) == kind


# --------------------------------------------------------------------------- edit distance

@pytest.mark.parametrize("a, b, d", [
    ("", "", 0), ("abc", "", 3), ("", "abc", 3), ("abc", "abc", 0),
    ("kitten", "sitting", 3), ("flaw", "lawn", 2),
    ("5551234567", "5551234568", 1),          # one digit misread
    ("5551234567", "5551234657", 2),          # two digits swapped
    ("5551234567", "7779876543", 9),
    ("A99900001", "A9990000I", 1),
])
def test_edit_distance(a, b, d):
    assert C.edit_distance(a, b) == d
    assert C.edit_distance(b, a) == d


# --------------------------------------------------------------------------- verdicts

@pytest.mark.parametrize("findings, verdict", [
    ([], "PASS"),
    ([("PASS", "ok")], "PASS"),
    ([("PASS", "ok"), ("NOTE", "worth a glance")], "PASS"),
    ([("NOTE", "worth a glance")], "PASS"),
    ([("PASS", "ok"), ("FLAG", "check by eye")], "FLAG"),
    ([("FLAG", "check by eye"), ("ERROR", "could not render")], "ERROR"),
    ([("ERROR", "could not render"), ("FAIL", "expired")], "FAIL"),
    ([("FAIL", "expired"), ("FLAG", "check"), ("NOTE", "n"), ("PASS", "ok")], "FAIL"),
])
def test_row_verdict(findings, verdict):
    assert C.row_verdict(findings) == verdict
    assert C.row_verdict(iter(findings)) == verdict


def test_has_error_sees_an_error_under_a_fail():
    """A FAIL row may still hold a technical failure: it must be retried (addendum A3)."""
    findings = [("FAIL", "provisional certificate"), ("ERROR", "could not read the QR codes")]
    assert C.row_verdict(findings) == "FAIL"
    assert C.has_error(findings) is True
    assert C.has_error([("FAIL", "expired"), ("FLAG", "x")]) is False
    assert C.has_error([]) is False


@pytest.mark.parametrize("row, retry", [
    ({"verdict": "FAIL", "has_error": True, "detail": "FAIL: x | ERROR: y"}, True),
    ({"verdict": "FAIL", "detail": "FAIL: provisional | ERROR: could not render page 2"}, True),
    ({"verdict": "ERROR", "detail": "ERROR: could not check this file (locked)"}, True),
    ({"verdict": "ERROR", "detail": ""}, True),
    ({"verdict": "PASS", "detail": "PASS: valid until 2031-01-01"}, False),
    ({"verdict": "FLAG", "detail": "FLAG: the word ERROR: inside a finding"}, False),
    ({"verdict": "FAIL", "has_error": False, "detail": "FAIL: expired"}, False),
])
def test_row_has_error(row, retry):
    assert C.row_has_error(row) is retry


def _rows(*verdicts):
    return [{"doc": f"doc {i}", "file": "", "verdict": v, "detail": f"{v}: x"} for i, v in enumerate(verdicts)]


@pytest.mark.parametrize("verdicts, student_verdict", [
    ((), "PASS"),
    (("PASS",), "PASS"),
    (("PASS", "PASS"), "PASS"),
    (("PASS", "FLAG"), "REVIEW"),
    (("FLAG", "ERROR"), "ERROR"),           # ERROR outranks REVIEW...
    (("PASS", "ERROR"), "ERROR"),
    (("ERROR", "FAIL"), "FAIL"),            # ...but never hides a FAIL
    (("FLAG", "FAIL", "PASS"), "FAIL"),
    (("FAIL", "MISSING"), "INCOMPLETE"),
    (("ERROR", "MISSING"), "INCOMPLETE"),
    (("MISSING",), "INCOMPLETE"),
])
def test_student_verdict_precedence(verdicts, student_verdict):
    assert C.student_verdict(_rows(*verdicts)) == student_verdict
    assert dv.student_verdict(_rows(*verdicts)) == student_verdict


# --------------------------------------------------------------------------- the fixtures

def test_fixture_passports_and_student_are_synthetic():
    assert PASSPORTS == [f"A9990000{i}" for i in range(1, 10)]
    s = student()
    assert s["Full Name"] == "SAMPLE STUDENT ONE" and s["Passport No"] in PASSPORTS
    assert s["Father"] == "SAMPLE FATHER" and s["Mother"] == "SAMPLE MOTHER"
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2}", s["DOB"]) and re.fullmatch(r"\d{4}-\d{2}-\d{2}", s["Passport Expiry"])
    from src.sheets import progress_builder as pb
    assert pb.program_key_of(s) == "KLP"
    assert pb.program_key_of(student(Program="BACHELOR'S DEGREE")) == "BACHELOR"
    assert pb.program_key_of(student(Program="MASTER'S DEGREE")) == "MASTER"


def test_fixture_student_overrides():
    s = student(Full_Name="SAMPLE STUDENT TWO", Passport_No="A99900002", Sponsor="BOTH PARENTS",
                **{"HSC GPA": "5.00"})
    assert s["Full Name"] == "SAMPLE STUDENT TWO" and s["Passport No"] == "A99900002"
    assert s["Sponsor"] == "BOTH PARENTS" and s["HSC GPA"] == "5.00"
    assert "Full_Name" not in s
    assert student()["Full Name"] == "SAMPLE STUDENT ONE"          # the default is not changed


def test_fixture_mrz_lines_are_a_valid_td3_mrz():
    from src.scraper import ocr_validator as ocr
    l1, l2 = mrz_lines("A99900001", "2005-03-14", "2031-01-01")
    assert len(l1) == len(l2) == 44
    one = ocr.parse_mrz_line1(l1)
    assert one["line1_ok"] and one["surname"] == "SAMPLE" and one["given_name"] == "STUDENT ONE"
    two = ocr.parse_mrz_line2(l2)
    assert two["line2_ok"] and two["passport_no"] == "A99900001" and two["passport_no_ok"]
    assert two["dob"] == "2005-03-14" and two["dob_ok"]
    assert two["expiry"] == "2031-01-01" and two["expiry_ok"]
    assert two["sex"] == "MALE"


def test_fixture_mrz_lines_other_values():
    import datetime as dt

    from src.scraper import ocr_validator as ocr
    l1, l2 = mrz_lines("A99900007", dt.date(1999, 12, 31), "300615", sex="F",
                       surname="SAMPLE", given="MOTHER")
    assert ocr.parse_mrz_line1(l1)["full_name"] == "SAMPLE MOTHER"
    two = ocr.parse_mrz_line2(l2)
    assert two["line2_ok"] and two["passport_no"] == "A99900007"
    assert (two["dob"], two["expiry"], two["sex"]) == ("1999-12-31", "2030-06-15", "FEMALE")


def test_fixture_make_pdf_has_a_text_layer_the_reader_uses(tmp_path):
    p = make_pdf(tmp_path / "PASSPORT.pdf", [f"Name SAMPLE STUDENT ONE. {FILLER}",
                                             f"Second page A99900001. {FILLER}"])
    text, sizes = dv.read_document(p)            # > 80 characters a page: no OCR
    assert "SAMPLE STUDENT ONE" in text and "A99900001" in text
    assert sizes == [(595, 842), (595, 842)]
    pages = dv.read_pages(p)
    assert len(pages) == 2 and "A99900001" in pages[1] and "A99900001" not in pages[0]


def test_fixture_make_pdf_wraps_long_lines_and_grows_tall_pages(tmp_path):
    words = [f"word{i:03d}" for i in range(120)]
    lines = [f"line {i} {FILLER}" for i in range(90)]
    p = make_pdf(tmp_path / "BANK STATEMENT.pdf", [" ".join(words), "\n".join(lines)])
    import pymupdf
    with pymupdf.open(str(p)) as doc:
        first, second = doc[0].get_text(), doc[1].get_text()
        assert doc[1].rect.height > 842
    assert all(w in first for w in words)
    assert all(f"line {i} " in second for i in range(90))


def test_fixture_make_image(tmp_path):
    from PIL import Image
    plain = make_image(tmp_path / "PASSPORT SIZE PHOTO.jpg", (350, 450), "white")
    with Image.open(plain) as im:
        assert im.size == (350, 450) and im.format == "JPEG"
        assert min(im.convert("RGB").getpixel((5, 5))) > 245
    stamped = make_image(tmp_path / "stamped.png", (600, 800), (240, 240, 240), stamp=(128, 0, 160))
    with Image.open(stamped) as im:
        assert im.convert("RGB").getpixel((400, 600)) == (128, 0, 160)       # the seal's centre
        assert im.convert("RGB").getpixel((10, 10)) == (240, 240, 240)
    scan = make_image(tmp_path / "FATHER NID.pdf", (600, 800), "white", stamp="red")
    import pymupdf
    with pymupdf.open(str(scan)) as doc:
        assert len(doc) == 1 and doc[0].get_text().strip() == "" and doc[0].get_images()


def test_fixture_make_qr_is_readable(tmp_path):
    import cv2
    import numpy as np
    import pymupdf
    payload = "https://example.com/verify?id=SAMPLE-0001"
    img = cv2.imread(str(make_qr(tmp_path / "qr.png", payload)))
    assert cv2.QRCodeDetector().detectAndDecode(img)[0] == payload
    pdf = make_qr(tmp_path / "BIRTH CERTIFICATE.pdf", payload)
    with pymupdf.open(str(pdf)) as doc:
        pix = doc[0].get_pixmap(dpi=150)
    arr = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width, pix.n)[:, :, :3]
    assert cv2.QRCodeDetector().detectAndDecode(np.ascontiguousarray(arr))[0] == payload


def test_fixture_folder_is_found_by_student_folders(tmp_path, monkeypatch):
    def build(p):
        p.write_bytes(b"built by a callable")

    d = folder(tmp_path, "KOREAN LANGUAGE PROGRAM (KLP)", "SAMPLE STUDENT ONE", "A99900001", {
        "PASSPORT.pdf": f"SAMPLE STUDENT ONE A99900001 {FILLER}",
        "ACADEMIC CERTIFICATE.pdf": [f"page one {FILLER}", f"page two {FILLER}"],
        "PASSPORT SIZE PHOTO.jpg": None,
        "BANK STATEMENT.pdf": None,
        "OTHER.bin": b"\x00\x01",
        "NOTES.txt": build,
        ".download_complete": "docs-fingerprint",
    })
    assert d == tmp_path / "KOREAN LANGUAGE PROGRAM (KLP)" / "SAMPLE STUDENT ONE (A99900001)"
    assert (d / "OTHER.bin").read_bytes() == b"\x00\x01"
    assert (d / "NOTES.txt").read_bytes() == b"built by a callable"
    assert (d / ".download_complete").read_text(encoding="utf-8") == "docs-fingerprint"
    assert "page two" in dv.read_pages(d / "ACADEMIC CERTIFICATE.pdf")[1]
    monkeypatch.setattr(dv, "DOCS_ROOTS", [tmp_path])
    assert dv.student_folders() == {"A99900001": (d, "SAMPLE STUDENT ONE (A99900001)")}


# --------------------------------------------------------------------------- rules.py: document keys

from src.verify import rules as R  # noqa: E402

# The portal's own key for each upload slot, the ZIP's title for it, and the document key
# the checker files it under.
SLOTS = [
    ("passport", "PASSPORT", "passport"),
    ("photo", "PASSPORT SIZE PHOTO", "photo"),
    ("student_nid", "STUDENT NID", "student_nid"),
    ("id_card_cv", "ID CARD CV", "id_card_cv"),
    ("birth_cert", "BIRTH CERTIFICATE", "birth_cert"),
    ("father_nid", "FATHER NID", "father_nid"),
    ("mother_nid", "MOTHER NID", "mother_nid"),
    ("father_death_cert", "FATHER DEATH CERTIFICATE", "father_death_cert"),
    ("mother_death_cert", "MOTHER DEATH CERTIFICATE", "mother_death_cert"),
    ("family_cert", "FAMILY RELATIONSHIP CERTIFICATE", "family_cert"),
    ("academic_cert", "ACADEMIC CERTIFICATE", "academic"),
    ("bank_statement", "BANK STATEMENT", "bank"),
    ("trade_license", "TRADE LICENSE", "trade_license"),
    ("income_tax", "INCOME TAX CERTIFICATE", "income_tax"),
    ("affidavit", "AFFIDAVIT", "affidavit"),
    ("personal_statement", "PERSONAL STATEMENT AND STUDY PLAN", "personal_statement"),
    ("recommendation", "RECOMMENDATION LETTER", "recommendation"),
    ("eca", "EXTRA CURRICULAR ACTIVITIES (ECA)", "eca"),
    ("ielts_topik", "IELTS TOPIK CERTIFICATE", "language_cert"),
]


@pytest.mark.parametrize("portal_key, title, key", SLOTS)
def test_classify_reads_the_portal_key_form(portal_key, title, key):
    assert dv.classify(f"{portal_key}.pdf") == key
    assert dv.classify(f"{portal_key}_1760000000.pdf") == key
    assert dv.classify(f"{portal_key.upper()}_1760000000.JPG") == key


@pytest.mark.parametrize("portal_key, title, key", SLOTS)
def test_classify_reads_the_zip_title(portal_key, title, key):
    assert dv.classify(f"{title}.pdf") == key
    assert dv.classify(f"{title.lower()}.jpg") == key
    assert dv.classify(title.replace(" ", "_") + ".pdf") == key


@pytest.mark.parametrize("name, key", [
    ("BIRTH CERT.pdf", "birth_cert"), ("FAMILY CERT.pdf", "family_cert"),
    ("ACADEMIC CERT.pdf", "academic"), ("DEATH CERT.pdf", "death_cert"),
    ("DEATH CERTIFICATE.pdf", "death_cert"), ("FATHER DEATH CERT.pdf", "father_death_cert"),
    ("MOTHER DEATH CERT.pdf", "mother_death_cert"), ("NID CARD.pdf", "student_nid"),
    ("id_card.pdf", "id_card_cv"),
    # A parent's ID card is not the student's CV slot, and there is no bare "id card" rule.
    ("FATHER ID CARD.pdf", None), ("SAMPLE.pdf", None),
])
def test_classify_space_forms_and_what_is_not_a_cv(name, key):
    assert dv.classify(name) == key


def test_id_card_cv_patterns_and_presence_only():
    assert R.FILE_PATTERNS["id_card_cv"] == ["id card cv", "id_card_cv", "id_card"]
    assert not {"id card cv", "id_card", "id card"} & set(R.FILE_PATTERNS["student_nid"])
    assert R.PRESENCE_ONLY == {"id_card_cv"}
    assert R.TITLES["id_card_cv"] == "03 ID Card / CV"


def test_death_certificates_stand_in_for_a_parents_nid():
    assert R.ALTERNATIVES == {"father_nid": ["father_death_cert"], "mother_nid": ["mother_death_cert"]}
    assert R.TITLES["father_death_cert"] == "05 Father Death Certificate"
    assert R.TITLES["mother_death_cert"] == "05 Mother Death Certificate"
    assert "death cert" in R.FILE_PATTERNS["death_cert"]
    for key in ("id_card_cv", "father_death_cert", "mother_death_cert", "death_cert", "student_nid"):
        assert key in R.OPTIONAL
        assert all(key not in req for req in R.REQUIRED.values())


def test_required_lists_are_unchanged():
    base = ["passport", "photo", "birth_cert", "father_nid", "mother_nid", "family_cert",
            "academic", "bank", "trade_license"]
    assert R.REQUIRED == {"KLP": base, "EAP": base, "BACHELOR": base, "MASTER": base + ["income_tax"]}


def test_p2_income_tax_stays_required_for_masters():
    assert "income_tax" in R.REQUIRED["MASTER"]
    assert all("income_tax" not in R.REQUIRED[p] for p in ("KLP", "EAP", "BACHELOR"))


def test_p4_study_plan_is_never_required():
    assert all("personal_statement" not in req for req in R.REQUIRED.values())
    assert "personal_statement" in R.OPTIONAL


def test_every_document_key_has_a_title_and_the_old_constants_stay():
    for key in set(R.FILE_PATTERNS) | set(R.OPTIONAL) | {k for v in R.REQUIRED.values() for k in v}:
        assert key in R.TITLES, key
    # read by the DOCCHECK parity test and the parked rules branch
    for name in ("PROGRAMS", "MIN_BANK_TAKA", "MIN_TAX_PAID_TAKA", "MIN_NET_WEALTH_TAKA",
                 "PASSPORT_MIN_MONTHS", "FAMILY_CERT_MAX_MONTHS", "BANK_MIN_ACCOUNT_MONTHS",
                 "BANK_MIN_STATEMENT_MONTHS", "BANK_RELAXED_STATEMENT_MONTHS", "PHOTO_RATIO",
                 "PHOTO_FORMATS", "MIN_OPENING_BALANCE", "PREFERRED_OPENING_MAX", "MIN_COLOUR_PERCENT",
                 "IDENTITY_DOCS", "QR_DOCS", "TRANSLATION_WORDS", "BANGLA_RANGE", "NOTARY_WORDS",
                 "LAWYER_PAD_WORDS", "APOSTILLE_WORDS", "PROVISIONAL_WORDS", "ONLINE_BIRTH_WORDS",
                 "OPENING_BALANCE_WORDS", "SOLVENCY_WORDS", "SEAL_WORDS", "USD_WORDS",
                 "UNION_ISSUER_WORDS", "REF_NO_WORDS", "TRADE_RUNNING_WORDS", "BOILERPLATE_NUMBERS",
                 "TYPED_DOCS", "BALANCE_LABELS", "TAX_PAID_LABELS", "ID_NUMBER_LABELS", "DOB_LABELS",
                 "FISCAL_LABELS"):
        assert hasattr(R, name), name
    assert not set(R.BALANCE_LABELS) & set(R.OPENING_BALANCE_WORDS)


# --------------------------------------------------------------------------- P1 / P3 in verify_student

from src.verify import page_checks as PC  # noqa: E402

KLP_DIR = "KOREAN LANGUAGE PROGRAM (KLP)"
# Every document KLP requires, as the ZIP names them.
KLP_FILES = {"PASSPORT.pdf": None, "PASSPORT SIZE PHOTO.jpg": None, "BIRTH CERTIFICATE.pdf": None,
             "FATHER NID.pdf": None, "MOTHER NID.pdf": None, "FAMILY RELATIONSHIP CERTIFICATE.pdf": None,
             "ACADEMIC CERTIFICATE.pdf": None, "BANK STATEMENT.pdf": None, "TRADE LICENSE.pdf": None}


@pytest.fixture
def reads(monkeypatch):
    """No image work and no OCR: page checks say nothing, and read_document is a spy that
    returns each file's own words without opening it."""
    monkeypatch.setattr(PC, "inspect", lambda path: {})
    monkeypatch.setattr(PC, "colour_check", lambda info, key="": [])
    monkeypatch.setattr(PC, "qr_check", lambda info, required=False, text="": [])
    monkeypatch.setattr(PC, "bangla_check", lambda text: [])
    monkeypatch.setattr(PC, "seal_present", lambda path: False)
    monkeypatch.setattr(PC, "academic_check", lambda path, pages: [])
    seen = []

    def read_document(path, max_pages=20):
        seen.append(path.name)
        return f"{path.stem} {FILLER}", [(595, 842)]

    monkeypatch.setattr(dv, "read_document", read_document)
    return seen


def klp(tmp_path, drop=(), add=None):
    files = {k: v for k, v in KLP_FILES.items() if k not in drop}
    files.update(add or {})
    return folder(tmp_path, KLP_DIR, "SAMPLE STUDENT ONE", PASSPORTS[0], files)


def test_p1_father_death_certificate_in_place_of_his_nid(tmp_path, reads):
    d = klp(tmp_path, drop=["FATHER NID.pdf"], add={"FATHER DEATH CERTIFICATE.pdf": None})
    rows = dv.verify_student(d, student(), "KLP")
    assert not [r for r in rows if r["key"] == "father_nid"]
    row, = [r for r in rows if r["key"] == "father_death_cert"]
    assert row["doc"] == "05 Father Death Certificate" and row["verdict"] == "PASS"
    assert row["detail"] == "PASS: present — accepted in place of the father's NID (05 Father NID not needed)"
    assert "MISSING" not in {r["verdict"] for r in rows}
    assert C.student_verdict(rows) != C.INCOMPLETE
    assert "FATHER DEATH CERTIFICATE.pdf" in reads           # read like any other document


def test_p1_mother_death_certificate_in_place_of_her_nid(tmp_path, reads):
    d = klp(tmp_path, drop=["MOTHER NID.pdf"], add={"mother_death_cert_1760000000.pdf": None})
    rows = dv.verify_student(d, student(), "KLP")
    assert not [r for r in rows if r["key"] == "mother_nid"]
    row, = [r for r in rows if r["key"] == "mother_death_cert"]
    assert row["detail"] == "PASS: present — accepted in place of the mother's NID (05 Mother NID not needed)"
    assert C.student_verdict(rows) != C.INCOMPLETE


def test_p1_neither_nid_nor_death_certificate_is_missing(tmp_path, reads):
    d = klp(tmp_path, drop=["FATHER NID.pdf"])
    rows = dv.verify_student(d, student(), "KLP")
    assert [r for r in rows if r["verdict"] == "MISSING"] == [
        {"doc": "05 Father NID", "file": "", "key": "father_nid", "verdict": "MISSING",
         "detail": "required by the guideline but not uploaded"}]
    assert C.student_verdict(rows) == C.INCOMPLETE
    # the mother's death certificate does not stand in for the father's NID
    d2 = klp(tmp_path / "two", drop=["FATHER NID.pdf"], add={"MOTHER DEATH CERTIFICATE.pdf": None})
    rows = dv.verify_student(d2, student(), "KLP")
    assert [r["key"] for r in rows if r["verdict"] == "MISSING"] == ["father_nid"]
    row, = [r for r in rows if r["key"] == "mother_death_cert"]
    assert row["detail"] == "PASS: present (no automatic rule for this document)"


def test_p1_both_present_the_nid_is_checked_as_usual(tmp_path, reads):
    d = klp(tmp_path, add={"FATHER DEATH CERTIFICATE.pdf": None})
    rows = dv.verify_student(d, student(), "KLP")
    nid, = [r for r in rows if r["key"] == "father_nid"]
    assert nid["file"] == "FATHER NID.pdf"
    assert "FAIL: no notarisation found — parents' NID must be notarised" in nid["detail"]
    cert, = [r for r in rows if r["key"] == "father_death_cert"]
    assert cert["detail"] == "PASS: present (no automatic rule for this document)"


def test_p3_the_cv_is_recorded_as_present_and_never_read(tmp_path, reads):
    d = klp(tmp_path, add={"ID CARD CV.pdf": [f"Curriculum vitae of OTHER PERSON {FILLER}"],
                           "id_card_1760000000.jpg": None})
    rows = dv.verify_student(d, student(), "KLP")
    assert "ID CARD CV.pdf" not in reads and "id_card_1760000000.jpg" not in reads
    assert "PASSPORT.pdf" in reads
    cv = [r for r in rows if r["key"] == "id_card_cv"]
    assert cv == [{"doc": "03 ID Card / CV", "file": name, "key": "id_card_cv", "verdict": "PASS",
                   "detail": "PASS: present — recorded as present only (not checked)"}
                  for name in ("ID CARD CV.pdf", "id_card_1760000000.jpg")]
    # nothing else changes: no NID findings, no cross-check row comes from the CV
    plain = dv.verify_student(klp(tmp_path / "no-cv"), student(), "KLP")
    assert [r for r in rows if r["key"] != "id_card_cv"] == plain
