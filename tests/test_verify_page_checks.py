"""Page checks that look at the scan itself (src/verify/page_checks.py).

  B14c  a file that cannot be opened or drawn is not checked, which is not the same as
        failing a check: inspect sets info["error"] and colour_check reports ERROR;
        seal_present and page_codes raise CheckError; a missing QR reader raises
        CheckError and zbar_available() says why.
  B9    a bare "MA" (often a name) is not a Master's degree; "M.A." / "M.A" / "M.Sc." are.
  P5    an apostille that is not on page 1 FAILs once; the "not followed by the
        certificate" FLAG is not added on top of it.
  and the ordinary colour, seal and QR rules on synthetic images.

Every file is synthetic and written under tmp_path; the apostille-subject cache is pointed
at tmp_path too.

Run from the BOT folder:
    .venv\\Scripts\\python.exe -m pytest tests\\test_verify_page_checks.py -q
"""
import sys
from pathlib import Path

import pytest

BOT_ROOT = Path(__file__).resolve().parent.parent
if str(BOT_ROOT) not in sys.path:
    sys.path.insert(0, str(BOT_ROOT))

from src.verify import page_checks as PC  # noqa: E402
from src.verify import rules as R  # noqa: E402
from src.verify.common import CheckError  # noqa: E402
from verify_fixtures import FILLER, make_image, make_pdf, make_qr  # noqa: E402

APOSTILLE_URL = "https://apostille.mygov.bd/verify/999000111"
HSC_PAGE = "Board of Intermediate and Secondary Education. Higher Secondary Certificate. " + FILLER
SSC_PAGE = "Board of Intermediate and Secondary Education. Secondary School Certificate. " + FILLER
APOSTILLE_PAGE = "e-Apostille. Ministry of Foreign Affairs. " + FILLER


@pytest.fixture(autouse=True)
def _no_live_apostille_cache(tmp_path, monkeypatch):
    """apostille_subject reads data/verification/apostille.json: never the live one here."""
    monkeypatch.setattr(PC, "APOSTILLE_SUBJECTS", tmp_path / "apostille.json")


def _broken_pdf(tmp_path, name="ACADEMIC CERTIFICATE.pdf"):
    p = tmp_path / name
    p.write_bytes(b"this is not a pdf " * 20)
    return p


def _no_zbar(monkeypatch):
    """`from pyzbar.pyzbar import decode` fails, as on a machine without the zbar DLL."""
    monkeypatch.setitem(sys.modules, "pyzbar.pyzbar", None)


# --------------------------------------------------------------------------- B9 levels

@pytest.mark.parametrize("text", [
    "Name of Student: MA SAMPLE",
    "MA SAMPLE STUDENT ONE, son of SAMPLE FATHER",
    "Roll 123456 M A SAMPLE",
    "Grade: A+ Marks obtained",
])
def test_a_bare_ma_is_not_a_masters_degree(text):
    assert PC.level_of(text) != "MASTER"


@pytest.mark.parametrize("text", [
    "Master of Science in Physics",
    "Masters degree examination",
    "M.Sc. Final Examination",
    "MSc in Chemistry",
    "M.A. in English",
    "M.A in English",
    "Master of Business Administration (MBA)",
])
def test_masters_wording_is_master(text):
    assert PC.level_of(text) == "MASTER"


@pytest.mark.parametrize("text, level", [
    ("Higher Secondary School Certificate Examination", "HSC"),   # holds the SSC wording too
    ("H.S.C. Examination 2023", "HSC"),
    ("Alim Examination", "HSC"),
    ("Secondary School Certificate Examination", "SSC"),
    ("S.S.C Examination 2021", "SSC"),
    ("Dakhil Examination", "SSC"),
    ("Diploma in Engineering", "DIPLOMA"),
    ("Bachelor of Arts (Honours)", "BACHELOR"),
    ("B.A. Examination", "BACHELOR"),
    ("B.Sc. in Computer Science", "BACHELOR"),
    ("Name of Student: SAMPLE STUDENT ONE", None),
])
def test_other_levels_and_their_order_are_unchanged(text, level):
    assert PC.level_of(text) == level


# --------------------------------------------------------------------------- P5 apostille

def _academic(monkeypatch, tmp_path, codes, pages):
    """academic_check with page_codes stubbed: it must call it through the module, as the
    image-facts cache patches PC.page_codes."""
    monkeypatch.setattr(PC, "page_codes", lambda path, max_pages=20: codes)
    return PC.academic_check(tmp_path / "ACADEMIC CERTIFICATE.pdf", pages)


def _not_followed(out):
    return [d for v, d in out if v == "FLAG" and "is not followed by the certificate" in d]


def test_apostille_on_page_2_only_fails_once_without_the_duplicate_flag(monkeypatch, tmp_path):
    out = _academic(monkeypatch, tmp_path, [[], [APOSTILLE_URL]], [HSC_PAGE, APOSTILLE_PAGE])
    fails = [d for v, d in out if v == "FAIL"]
    assert len(fails) == 1 and fails[0].startswith("page 1 is not an e-Apostille")
    assert "first apostille is on page 2" in fails[0]
    assert not _not_followed(out)


def test_a_later_apostille_without_pages_after_it_adds_no_flag_when_page_1_failed(monkeypatch, tmp_path):
    codes = [[], [APOSTILLE_URL], [], [APOSTILLE_URL + "2"]]
    pages = [SSC_PAGE, APOSTILLE_PAGE, HSC_PAGE, APOSTILLE_PAGE]
    out = _academic(monkeypatch, tmp_path, codes, pages)
    assert [v for v, _ in out].count("FAIL") == 1
    assert not _not_followed(out)
    assert any(v == "PASS" and "followed by HSC" in d for v, d in out)


def test_apostille_on_page_1_with_nothing_after_it_keeps_the_flag(monkeypatch, tmp_path):
    out = _academic(monkeypatch, tmp_path, [[APOSTILLE_URL]], [APOSTILLE_PAGE])
    assert not [v for v, _ in out if v == "FAIL"]
    assert _not_followed(out) == ["the e-Apostille on page 1 is not followed by the certificate "
                                  "and transcript it covers"]


def test_a_second_apostille_at_the_end_keeps_its_flag_when_page_1_is_right(monkeypatch, tmp_path):
    codes = [[APOSTILLE_URL], [], [APOSTILLE_URL + "2"]]
    out = _academic(monkeypatch, tmp_path, codes, [APOSTILLE_PAGE, HSC_PAGE, APOSTILLE_PAGE])
    assert not [v for v, _ in out if v == "FAIL"]
    assert len(_not_followed(out)) == 1 and "page 3" in _not_followed(out)[0]


def test_mixed_levels_after_one_apostille_are_still_a_flag(monkeypatch, tmp_path):
    out = _academic(monkeypatch, tmp_path, [[APOSTILLE_URL], [], []],
                    [APOSTILLE_PAGE, HSC_PAGE, SSC_PAGE])
    mixed = [d for v, d in out if v == "FLAG" and "not all for one qualification" in d]
    assert mixed and "page 2: HSC" in mixed[0] and "page 3: SSC" in mixed[0]


def test_no_apostille_qr_is_still_a_fail(monkeypatch, tmp_path):
    out = _academic(monkeypatch, tmp_path, [[], []], [APOSTILLE_PAGE, HSC_PAGE])
    assert out == [("FAIL", "an e-Apostille page is there but its QR could not be read — "
                            "the apostille QR must be scannable")]


def test_the_apostille_subject_cache_still_decides_a_mismatch(monkeypatch, tmp_path):
    (tmp_path / "apostille.json").write_text('{"999000111": "SSC"}', encoding="utf-8")
    out = _academic(monkeypatch, tmp_path, [[APOSTILLE_URL], []], [APOSTILLE_PAGE, HSC_PAGE])
    assert any(v == "FAIL" and "issued for SSC" in d for v, d in out)


# --------------------------------------------------------------------------- B14c errors

def test_inspect_records_a_render_failure_and_colour_check_reports_error(monkeypatch, tmp_path):
    p = make_image(tmp_path / "BIRTH CERTIFICATE.jpg")

    def broken(path, dpi=250, max_pages=4):
        raise RuntimeError("cannot draw page 1")

    monkeypatch.setattr(PC, "_pages", broken)
    info = PC.inspect(p)
    assert info["error"] == "RuntimeError: cannot draw page 1"
    assert info["saturation"] is None and info["qr"] == []
    assert PC.colour_check(info, "birth_cert") == [
        ("ERROR", "could not render the pages to judge colour (RuntimeError: cannot draw page 1)")]
    assert PC.qr_check(info, required=True) == []           # one ERROR per file, not two


def test_inspect_on_a_broken_file_names_the_file_not_its_folder(tmp_path):
    p = _broken_pdf(tmp_path)
    info = PC.inspect(p)
    assert info["error"] and "ACADEMIC CERTIFICATE.pdf" in info["error"]
    assert str(tmp_path) not in info["error"] and repr(str(tmp_path))[1:-1] not in info["error"]
    assert PC.colour_check(info, "academic")[0][0] == "ERROR"


def test_the_typed_and_computer_made_exemptions_come_before_the_error():
    info = {"saturation": None, "qr": [], "qr_seen": False, "pages": 0, "digital": False,
            "error": "RuntimeError: x"}
    typed = next(iter(R.TYPED_DOCS))
    assert PC.colour_check(info, typed)[0][0] == "PASS"
    digital = dict(info, digital=True)
    assert PC.colour_check(digital, "bank")[0][0] == "PASS"
    # the colour rule said nothing about the error there, so the unread QR is reported
    assert PC.qr_check(digital) == [
        ("ERROR", "could not render the pages to read a QR code (RuntimeError: x)")]


def test_a_code_fault_in_rendering_is_not_turned_into_an_error_row(monkeypatch, tmp_path):
    p = make_image(tmp_path / "BIRTH CERTIFICATE.jpg")

    def buggy(path, dpi=250, max_pages=4):
        raise TypeError("bad argument")

    monkeypatch.setattr(PC, "_pages", buggy)
    with pytest.raises(TypeError):
        PC.inspect(p)
    with pytest.raises(TypeError):
        PC.seal_present(p)


@pytest.mark.parametrize("name", ["FAMILY CERTIFICATE.pdf", "FAMILY CERTIFICATE.jpg"])
def test_seal_present_raises_check_error_on_a_broken_file(tmp_path, name):
    p = tmp_path / name
    p.write_bytes(b"broken " * 30)
    with pytest.raises(CheckError) as e:
        PC.seal_present(p)
    assert name in str(e.value) and str(tmp_path) not in str(e.value)


def test_seal_present_raises_check_error_on_a_missing_file(tmp_path):
    with pytest.raises(CheckError):
        PC.seal_present(tmp_path / "GONE.pdf")


def test_page_codes_raises_check_error_on_a_broken_file(tmp_path):
    with pytest.raises(CheckError):
        PC.page_codes(_broken_pdf(tmp_path))
    with pytest.raises(CheckError):
        PC.page_codes(tmp_path / "GONE.jpg")


def test_a_missing_qr_reader_is_a_check_error_not_an_empty_read(monkeypatch, tmp_path):
    pdf = make_pdf(tmp_path / "ACADEMIC CERTIFICATE.pdf", [APOSTILLE_PAGE, HSC_PAGE])
    img = make_image(tmp_path / "BIRTH CERTIFICATE.png")
    _no_zbar(monkeypatch)
    ok, why = PC.zbar_available()
    assert ok is False and why
    with pytest.raises(CheckError) as e:
        PC.page_codes(pdf)
    assert "pyzbar (QR reader) not available" in str(e.value)
    with pytest.raises(CheckError):
        PC.inspect(img)
    with pytest.raises(CheckError):                      # academic_check passes it on
        PC.academic_check(pdf, [APOSTILLE_PAGE, HSC_PAGE])


def test_zbar_available_when_the_reader_works():
    pytest.importorskip("pyzbar.pyzbar")
    assert PC.zbar_available() == (True, "available")


# --------------------------------------------------------------------------- synthetic scans

def test_a_coloured_stamp_is_a_colour_scan(tmp_path):
    info = PC.inspect(make_image(tmp_path / "BIRTH CERTIFICATE.jpg", stamp=(128, 0, 160)))
    assert "error" not in info and info["pages"] == 1 and info["digital"] is False
    assert PC.colour_check(info, "birth_cert")[0][0] == "PASS"


def test_a_grey_scan_fails_the_colour_rule(tmp_path):
    info = PC.inspect(make_image(tmp_path / "BIRTH CERTIFICATE.png", colour=(128, 128, 128)))
    verdict, detail = PC.colour_check(info, "birth_cert")[0]
    assert verdict == "FAIL" and "black-and-white" in detail


def test_a_computer_made_pdf_is_exempt_from_the_colour_rule(tmp_path):
    words = " ".join(["SAMPLE", "STATEMENT", "LINE"] * 20)
    info = PC.inspect(make_pdf(tmp_path / "BANK STATEMENT.pdf", [words]))
    assert info["digital"] is True
    assert PC.colour_check(info, "bank")[0][0] == "PASS"


def test_seal_present_sees_a_violet_or_red_stamp(tmp_path):
    assert PC.seal_present(make_image(tmp_path / "violet.png", stamp=(128, 0, 160))) is True
    assert PC.seal_present(make_image(tmp_path / "red.jpg", stamp="red")) is True
    assert PC.seal_present(make_image(tmp_path / "plain.png")) is False
    assert PC.seal_present(make_image(tmp_path / "scan.pdf", stamp=(128, 0, 160))) is True


def test_a_readable_qr_passes_the_qr_rule(tmp_path):
    pytest.importorskip("pyzbar.pyzbar")
    info = PC.inspect(make_qr(tmp_path / "BIRTH CERTIFICATE.png", APOSTILLE_URL))
    assert APOSTILLE_URL in info["qr"] and info["qr_seen"] is True
    verdict, detail = PC.qr_check(info, required=True)[0]
    assert verdict == "PASS" and "QR code scans cleanly" in detail


def test_no_qr_on_a_birth_certificate_is_flagged_only_when_required(tmp_path):
    info = PC.inspect(make_image(tmp_path / "BIRTH CERTIFICATE.png"))
    assert PC.qr_check(info, required=True)[0][0] == "FLAG"
    assert PC.qr_check(info, required=False)[0][0] == "PASS"


def test_a_real_apostille_qr_followed_by_a_certificate_passes(tmp_path):
    pytest.importorskip("pyzbar.pyzbar")
    import pymupdf
    qr = make_qr(tmp_path / "qr.pdf", APOSTILLE_URL)
    cert = make_pdf(tmp_path / "cert.pdf", [HSC_PAGE])
    out = tmp_path / "ACADEMIC CERTIFICATE.pdf"
    doc = pymupdf.open(str(qr))
    other = pymupdf.open(str(cert))
    doc.insert_pdf(other)
    doc.save(str(out))
    other.close()
    doc.close()

    codes = PC.page_codes(out)
    assert codes[0] == [APOSTILLE_URL] and codes[1] == []
    result = PC.academic_check(out, [APOSTILLE_PAGE, HSC_PAGE])
    assert [v for v, _ in result] == ["PASS", "PASS"]
    assert "followed by HSC" in result[1][1]
