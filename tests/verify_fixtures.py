"""Synthetic builders for the document-check tests (src/verify).

Every value here is invented: SAMPLE names, passports A99900001..A99900009, NID-like
numbers that never start with 01. No test may read or copy anything from data\\, the
VERIFIED STUDENT DOCUMENTS folders or results.json — build what a test needs with these
and write it under pytest's tmp_path.

  make_pdf(path, pages)                 a PDF with a real text layer, one string per page
  make_image(path, size, colour, stamp) a plain scan-like image (or an image-only PDF)
  make_qr(path, text)                   a QR code image (or a PDF page carrying it)
  mrz_lines(passport_no, dob, expiry)   the two TD3 machine-readable lines, check digits valid
  student(**over)                       a portal row with fake values
  folder(tmp_path, program_dir, name, passport, files)
                                        a downloaded student's folder, laid out as on disk

Note: doc_verifier.read_document only trusts a PDF page's text layer when it holds more
than 80 characters (read_pages: 40); a shorter page is sent to OCR. Pad short pages with
FILLER, or stub the reader.
"""
from __future__ import annotations

import datetime as dt
import sys
import textwrap
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple, Union

BOT_ROOT = Path(__file__).resolve().parent.parent
if str(BOT_ROOT) not in sys.path:
    sys.path.insert(0, str(BOT_ROOT))

# Neutral words that trip no rule (no notary, seal, bank, apostille or birth wording), so a
# short synthetic page can be made long enough to be read from its text layer.
FILLER = ("Lorem ipsum dolor sit amet, consectetur adipiscing elit, sed do eiusmod tempor "
          "incididunt ut labore et dolore magna aliqua.")

PASSPORTS = [f"A9990000{i}" for i in range(1, 10)]

Colour = Union[str, Tuple[int, int, int]]

_A4 = (595, 842)                     # points
_FONT_SIZE = 9
_LINE = 12                           # points between lines at that size
_WRAP = 95                           # characters per line: well inside an A4 page


def make_pdf(path: Union[str, Path], pages: Sequence[str]) -> Path:
    """A PDF whose pages carry `pages` as a real text layer (pymupdf insert_text).

    Long lines are wrapped and a page grows taller when it needs to, so no text falls
    outside the page (pymupdf does not extract text beyond the page's edge)."""
    import pymupdf
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    doc = pymupdf.open()
    for text in pages:
        lines: List[str] = []
        for raw in (text or "").splitlines() or [""]:
            lines += textwrap.wrap(raw, _WRAP, break_long_words=True) or [""]
        height = max(_A4[1], 72 + _LINE * len(lines) + 36)
        page = doc.new_page(width=_A4[0], height=height)
        if any(lines):
            page.insert_text((36, 54), "\n".join(lines), fontsize=_FONT_SIZE, lineheight=_LINE / _FONT_SIZE)
    if not len(doc):
        doc.new_page(width=_A4[0], height=_A4[1])
    doc.save(str(path))
    doc.close()
    return path


def _rgb(colour: Colour) -> Tuple[int, int, int]:
    from PIL import ImageColor
    return ImageColor.getrgb(colour) if isinstance(colour, str) else tuple(colour)


def _save_image(img, path: Path) -> Path:
    """Save a PIL image as `path` says: .jpg/.jpeg/.png as that image, .pdf as one
    image-only page (no text layer, like a scan)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    suffix = path.suffix.lower()
    if suffix == ".pdf":
        import io

        import pymupdf
        buf = io.BytesIO()
        img.convert("RGB").save(buf, format="PNG")
        doc = pymupdf.open()
        w, h = img.size
        page = doc.new_page(width=w * 72 / 150, height=h * 72 / 150)      # 150 dpi
        page.insert_image(page.rect, stream=buf.getvalue())
        doc.save(str(path))
        doc.close()
    elif suffix in (".jpg", ".jpeg"):
        img.convert("RGB").save(path, format="JPEG", quality=95)
    else:
        img.save(path)
    return path


def make_image(path: Union[str, Path], size: Tuple[int, int] = (600, 800), colour: Colour = "white",
               stamp: Optional[Colour] = None) -> Path:
    """A plain `size` (width, height) image filled with `colour`. `stamp`, when given, is
    the colour of a round seal drawn on it (violet (128, 0, 160) looks like a notary
    stamp to page_checks.seal_present; red (220, 20, 20) like a wax seal). A .pdf path
    gives an image-only PDF page."""
    from PIL import Image, ImageDraw
    path = Path(path)
    img = Image.new("RGB", tuple(size), _rgb(colour))
    if stamp is not None:
        w, h = img.size
        r = max(8, min(w, h) // 6)
        cx, cy = w * 2 // 3, h * 3 // 4
        ImageDraw.Draw(img).ellipse((cx - r, cy - r, cx + r, cy + r), fill=_rgb(stamp))
    return _save_image(img, path)


def make_qr(path: Union[str, Path], text: str) -> Path:
    """A QR code carrying `text` (cv2.QRCodeEncoder), scaled up with a white margin so a
    decoder reads it. A .pdf path puts it on an image-only PDF page. The calling test is
    skipped when this OpenCV build has no QR encoder."""
    import cv2
    import numpy as np
    import pytest
    from PIL import Image
    if not hasattr(cv2, "QRCodeEncoder"):
        pytest.skip("this OpenCV build has no QRCodeEncoder")
    code = cv2.QRCodeEncoder.create().encode(text)
    if code.ndim == 3:
        code = cv2.cvtColor(code, cv2.COLOR_BGR2GRAY)
    code = cv2.resize(code, None, fx=8, fy=8, interpolation=cv2.INTER_NEAREST)
    code = cv2.copyMakeBorder(code, 64, 64, 64, 64, cv2.BORDER_CONSTANT, value=255)
    img = Image.fromarray(np.ascontiguousarray(code)).convert("RGB")
    return _save_image(img, Path(path))


def _yymmdd(value: Union[str, dt.date]) -> str:
    """A date, "YYYY-MM-DD" or "YYMMDD" as the MRZ's YYMMDD."""
    if isinstance(value, dt.date):
        return value.strftime("%y%m%d")
    v = str(value).strip()
    if len(v) == 6 and v.isdigit():
        return v
    return dt.date.fromisoformat(v).strftime("%y%m%d")


def mrz_lines(passport_no: str, dob: Union[str, dt.date], expiry: Union[str, dt.date], sex: str = "M",
              surname: str = "SAMPLE", given: str = "STUDENT ONE") -> Tuple[str, str]:
    """The two 44-character TD3 lines of a Bangladeshi passport, every check digit valid
    (ocr_validator.compute_icao_check_digit), so parse_mrz_line1/line2 read them as a
    valid MRZ. Join them with "\\n" to put them in a page's text."""
    from src.scraper.ocr_validator import compute_icao_check_digit as cd

    def field(s: str) -> str:
        return "<".join(p for p in "".join(c if c.isalpha() else " " for c in s.upper()).split())

    line1 = ("P<BGD" + field(surname) + "<<" + field(given)).ljust(44, "<")[:44]
    doc = passport_no.upper().replace(" ", "").ljust(9, "<")[:9]
    birth, until = _yymmdd(dob), _yymmdd(expiry)
    personal = "<" * 14
    body = (doc + cd(doc), birth + cd(birth), until + cd(until), personal + cd(personal))
    line2 = (body[0] + "BGD" + body[1] + (sex or "<")[0].upper() + body[2] + body[3]
             + cd(body[0] + body[1] + body[2] + body[3]))
    assert len(line1) == 44 and len(line2) == 44, (line1, line2)
    return line1, line2


_STUDENT = {
    "Student ID": "S-9990001",
    "Full Name": "SAMPLE STUDENT ONE",
    "Surname": "SAMPLE",
    "Given Name": "STUDENT ONE",
    "Email": "sample.one@example.com",
    "Mobile": "+10000000001",
    "Guardian WhatsApp": "+10000000002",
    "DOB": "2005-03-14",
    "Gender": "Male",
    "District": "SAMPLE DISTRICT",
    "Address": "1 SAMPLE ROAD, SAMPLE TOWN",
    "Father": "SAMPLE FATHER",
    "Mother": "SAMPLE MOTHER",
    "Study Status": "",
    "SSC Year": "2021",
    "SSC GPA": "5.00",
    "SSC Group": "SCIENCE",
    "SSC School": "SAMPLE HIGH SCHOOL",
    "HSC Year": "2023",
    "HSC GPA": "4.83",
    "HSC Group": "SCIENCE",
    "HSC College": "SAMPLE COLLEGE",
    "Previous University": "",
    "Subject": "",
    "Degree": "",
    "CGPA": "",
    "Program": "KOREAN LANGUAGE PROGRAM (KLP)",
    "Intake": "MARCH 2027",
    "Source": "Direct",
    "Current Stage": "Documents",
    "Passport Status": "Verified",
    "Passport No": "A99900001",
    "Passport Expiry": "2031-01-01",
    "Visa Rejection History": "",
    "Sponsor": "FATHER",
    "Sponsor Occupation": "BUSINESS",
    "Bank Certificate": "",
}


def student(**over: Any) -> Dict[str, str]:
    """A portal row (the keys progress_builder.fetch_all_students gives) with fake values.
    Override any field by keyword; an underscore stands for a space, so
    student(Full_Name="SAMPLE STUDENT TWO", Passport_No="A99900002") works, as does
    student(**{"Full Name": ...})."""
    row = dict(_STUDENT)
    for k, v in over.items():
        row[k if k in row else k.replace("_", " ")] = v
    return row


FileSpec = Union[None, bytes, str, Sequence[str], Callable[[Path], Any]]


def folder(tmp_path: Union[str, Path], program_dir: str, name: str, passport: str,
           files: Dict[str, FileSpec]) -> Path:
    """`tmp_path/<program_dir>/<name> (<passport>)/` holding `files`, the layout
    doc_verifier.student_folders reads. Each value says how to make that file:

      bytes              written as they are
      str / [str, ...]   a .pdf gets make_pdf pages (a str is one page); any other file
                         gets the text written as it is (a ".download_complete" marker)
      None               a .pdf gets one FILLER page; an image a plain white 600x800 one
      callable           called with the file's path to build it
    """
    d = Path(tmp_path) / program_dir / f"{name} ({passport})"
    d.mkdir(parents=True, exist_ok=True)
    for fname, spec in files.items():
        p = d / fname
        suffix = p.suffix.lower()
        if callable(spec):
            spec(p)
        elif isinstance(spec, bytes):
            p.write_bytes(spec)
        elif spec is None:
            if suffix == ".pdf":
                make_pdf(p, [FILLER])
            elif suffix in (".jpg", ".jpeg", ".png"):
                make_image(p)
            else:
                p.write_text("", encoding="utf-8")
        elif suffix == ".pdf":
            make_pdf(p, [spec] if isinstance(spec, str) else list(spec))
        else:
            p.write_text(spec if isinstance(spec, str) else "\n".join(spec), encoding="utf-8")
    return d
