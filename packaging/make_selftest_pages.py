"""Makes the self-test's made-up pages (APP_SPEC rev 1 section 1.2; revision 2 R2.3.4 and R2.4.4 step 6; Task D).

    <python> -I -B packaging\\make_selftest_pages.py --out <stage>\\selftest

writes into --out (created when missing):
  selftest_pages.pdf      two image-only pages; each is ONE JPEG (quality 85) of the page at 200 dpi covering the
                          whole page, with no text layer:
                            page 1  an upright A4 page (portrait);
                            page 2  the same kind of page turned 90 degrees clockwise (A4 landscape), so the bot's
                                    reader must turn it upright (dv._ocr_image's rotation retries; reported sideways);
  selftest_page1.jpg      page 1's JPEG, byte for byte (the card part reads it with dv.read_document from the
                          self-test's non-ASCII scratch folder, R2.3.4 step 3);
  selftest_expected.json  the expected lines of each page, sizes, and the sha256 of the two files.

Made-up text only ("SAMPLE BOARD OF EXAMINATIONS", "SAMPLE STUDENT ONE", "Roll No 000123", ...): no name, number or
document text of any student, no digit run longer than 6, no date but the placeholder year 2000. Each page holds more
than 300 characters, so page 1 is read in one pass and page 2 is accepted after one turn (the bot's _MIN_PAGE_CHARS).

Rev 1 also put a QR code on page 2. It is left out: nothing in the rev 2 self-test reads it (the qr part encodes its
own made-up code), and its modules could be read as stray characters and blur page 2's CER.

The pages are drawn with PyMuPDF's built-in Helvetica, rendered at 200 dpi, and encoded with Pillow; the build env
and the runner venv both have both. The output is the same for the same PyMuPDF and Pillow versions.
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

DPI = 200
JPEG_QUALITY = 85
A4_PT = (595.0, 842.0)                 # A4 portrait in points
MARGIN_PT = 56.0
TOP_PT = 110.0
LINE_GAP = 2.1                         # line pitch in font sizes
TITLE_FS = 20.0
BODY_FS = 17.0
MIN_FS = 12.0
MIN_PAGE_CHARS = 300                   # dv._MIN_PAGE_CHARS: a page reading fewer characters is turned and read again
SCHEMA = "hdc-selftest-expected/1"
PDF_NAME = "selftest_pages.pdf"
JPG_NAME = "selftest_page1.jpg"
EXPECTED_NAME = "selftest_expected.json"

# Made-up text only. The first line of each page is the title (bold).
PAGE_LINES: Dict[int, List[str]] = {
    1: ["SAMPLE BOARD OF EXAMINATIONS",
        "HIGHER SECONDARY CERTIFICATE (SAMPLE)",
        "This is to certify that SAMPLE STUDENT ONE",
        "son of SAMPLE PARENT ONE and SAMPLE PARENT TWO",
        "passed the sample examination in the year 2000.",
        "Roll No 000123   Registration No 000456",
        "Group Science   Result GPA 5.00 out of 5.00",
        "This page is made up for the self-test only.",
        "It holds no real name and no real number."],
    2: ["SAMPLE ACADEMIC TRANSCRIPT",
        "Marks of the sample examination (made up)",
        "English 80   Mathematics 75   Physics 70",
        "Chemistry 72   Biology 68   Statistics 77",
        "Total marks 442 out of 600   Grade A",
        "Medium of instruction: English",
        "Issued by the sample controller of examinations.",
        "This page is turned on its side on purpose,",
        "so the reader must turn it upright first."],
}
TURNED = {1: False, 2: True}           # page 2 is turned 90 degrees clockwise


def _pymupdf():
    try:
        import pymupdf
    except ImportError:                 # an older PyMuPDF has only the fitz name
        import fitz as pymupdf
    return pymupdf


def check_text(lines_by_page: Dict[int, List[str]] = PAGE_LINES) -> None:
    """The made-up text's own rules: no digit run longer than 6, at least MIN_PAGE_CHARS per page."""
    for page, lines in lines_by_page.items():
        for line in lines:
            if re.search(r"\d{7,}", line):
                raise ValueError(f"page {page}: a digit run longer than 6")
        if len(" ".join(lines)) < MIN_PAGE_CHARS:
            raise ValueError(f"page {page} holds fewer than {MIN_PAGE_CHARS} characters")


def _fit(pm, lines: Sequence[str], width: float) -> float:
    """The body font size (BODY_FS, smaller in 0.5 pt steps) at which every body line fits the text width."""
    fs = BODY_FS
    while fs > MIN_FS and max(pm.get_text_length(t, fontname="helv", fontsize=fs) for t in lines) > width:
        fs -= 0.5
    return fs


def render_page(lines: Sequence[str]):
    """One upright A4 page with ``lines`` (the first as the title), rendered at DPI: a Pillow RGB image."""
    pm = _pymupdf()
    from PIL import Image
    doc = pm.open()
    try:
        page = doc.new_page(width=A4_PT[0], height=A4_PT[1])
        width = A4_PT[0] - 2 * MARGIN_PT
        title_fs = TITLE_FS
        while title_fs > MIN_FS and pm.get_text_length(lines[0], fontname="hebo", fontsize=title_fs) > width:
            title_fs -= 0.5
        body_fs = _fit(pm, lines[1:], width)
        y = TOP_PT
        page.insert_text((MARGIN_PT, y), lines[0], fontname="hebo", fontsize=title_fs, color=(0, 0, 0))
        y += LINE_GAP * title_fs
        for t in lines[1:]:
            page.insert_text((MARGIN_PT, y), t, fontname="helv", fontsize=body_fs, color=(0, 0, 0))
            y += LINE_GAP * body_fs
        pix = page.get_pixmap(dpi=DPI, alpha=False)
        img = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
    finally:
        doc.close()
    return img


def jpeg_bytes(img) -> bytes:
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=JPEG_QUALITY)
    return buf.getvalue()


def image_pdf(jpegs: Sequence[bytes]) -> bytes:
    """A PDF with one page per JPEG, each page exactly the JPEG's size at DPI (so a 200-dpi render gives the JPEG's
    pixels), the JPEG embedded as it is (no re-encode), no text layer."""
    pm = _pymupdf()
    from PIL import Image
    doc = pm.open()
    try:
        for data in jpegs:
            with Image.open(io.BytesIO(data)) as im:
                w, h = im.size
            page = doc.new_page(width=w * 72.0 / DPI, height=h * 72.0 / DPI)
            page.insert_image(page.rect, stream=data)
        doc.set_metadata({"title": "Self-test pages (made up)", "author": "", "subject": "", "keywords": "",
                          "creator": "make_selftest_pages.py", "producer": "", "creationDate": "", "modDate": ""})
        return doc.tobytes(garbage=4, deflate=True, no_new_id=True)
    finally:
        doc.close()


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _write(path: Path, data: bytes) -> None:
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_bytes(data)
    os.replace(tmp, path)


def make(out_dir: Any) -> Dict[str, Path]:
    """Writes the three files into ``out_dir`` and returns their paths by name."""
    check_text()
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    jpegs: Dict[int, bytes] = {}
    sizes: Dict[int, List[int]] = {}
    for page in sorted(PAGE_LINES):
        img = render_page(PAGE_LINES[page])
        if TURNED[page]:
            from PIL import Image
            img = img.transpose(Image.Transpose.ROTATE_270)     # 90 degrees clockwise
        jpegs[page] = jpeg_bytes(img)
        sizes[page] = [img.width, img.height]
    pdf = image_pdf([jpegs[p] for p in sorted(jpegs)])
    paths = {PDF_NAME: out / PDF_NAME, JPG_NAME: out / JPG_NAME, EXPECTED_NAME: out / EXPECTED_NAME}
    _write(paths[PDF_NAME], pdf)
    _write(paths[JPG_NAME], jpegs[1])
    pm = _pymupdf()
    import PIL
    expected = {
        "schema": SCHEMA,
        "dpi": DPI,
        "jpeg_quality": JPEG_QUALITY,
        "pages": {str(p): list(PAGE_LINES[p]) for p in sorted(PAGE_LINES)},
        "sideways": {str(p): TURNED[p] for p in sorted(TURNED)},
        "page_px": {str(p): sizes[p] for p in sorted(sizes)},
        "photo": {"file": JPG_NAME, "page": 1},
        "files": {PDF_NAME: {"size": len(pdf), "sha256": _sha256(pdf)},
                  JPG_NAME: {"size": len(jpegs[1]), "sha256": _sha256(jpegs[1])}},
        "made_with": {"pymupdf": str(getattr(pm, "VersionBind", getattr(pm, "__version__", "?"))),
                      "pillow": str(getattr(PIL, "__version__", "?"))},
    }
    _write(paths[EXPECTED_NAME], (json.dumps(expected, indent=1, ensure_ascii=True) + "\n").encode("ascii"))
    return paths


def main(argv: Optional[Sequence[str]] = None) -> int:
    ap = argparse.ArgumentParser(prog="make_selftest_pages.py",
                                 description="Writes the self-test's made-up pages (selftest_pages.pdf, "
                                             "selftest_page1.jpg, selftest_expected.json) into --out.")
    ap.add_argument("--out", required=True, help="the folder to write into (the stage's selftest folder)")
    a = ap.parse_args(list(sys.argv[1:] if argv is None else argv))
    paths = make(Path(os.path.abspath(a.out)))
    for name in sorted(paths):
        print(f"{name}: {paths[name].stat().st_size} bytes")
    return 0


if __name__ == "__main__":
    sys.exit(main())
