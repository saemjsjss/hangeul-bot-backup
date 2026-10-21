"""
Document verifier — checks a student's DOWNLOADED documents against the program
guidelines (src/verify/rules.py) and the portal's own record of that student.

It never touches the portal beyond the read-only student export the rest of the bot
already uses; all reading happens on the local files.

What it does per student:
  1. Works out which program they are on and which documents are required.
  2. Reads every file: PDF text layer first, OCR (easyocr) only when there is none.
  3. Runs the per-document rules (validity dates, amounts, notarisation, apostille,
     photo size, issue dates …) plus cross-checks against the portal record.
  4. Writes an Excel report: one row per document with verdict + reason, and a
     summary sheet for the whole run.

Verdicts: PASS · FLAG (needs a human eye) · FAIL · MISSING · ERROR (the file could not be
checked — locked, unrenderable, OCR broke; never a document problem, retried next pass)

CLI (run from the BOT folder):
  python -m src.verify.doc_verifier --student "SAMPLE STUDENT ONE"
  python -m src.verify.doc_verifier --passport A99900001
  python -m src.verify.doc_verifier --all                  # every downloaded student
  python -m src.verify.doc_verifier --all --since 2026-09-01
"""
from __future__ import annotations

import argparse
import datetime as dt
import logging
import os
import re
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Tuple

from src.config import settings
from src.verify import page_checks as PC
from src.verify import rules as R
# The verdict names and the name rules live in common, shared with field_check and
# auto_verify; norm, name_tokens, name_in and student_verdict stay importable from here.
from src.verify.common import (ERROR, FAIL, FLAG, MISSING, NOTE, PASS,  # noqa: F401
                               CheckError, edit_distance, has_error, name_in, name_tokens, norm,
                               row_verdict, sponsor_kind, student_verdict)

logger = logging.getLogger(__name__)

DOCS_ROOTS = [settings.docs_root(), settings.konyang_root()]   # an absent one is skipped
REPORT_DIR = settings.verification_dir()
TODAY = dt.date.today()

# The portal fields the document check reads.  A change to any of them means the stored
# verdict must be re-evaluated (auto_verify fingerprints exactly these).
PORTAL_KEYS_READ = ("Full Name", "Father", "Mother", "DOB", "Gender", "Passport No", "Passport Expiry",
                    "Sponsor", "SSC GPA", "HSC GPA", "Program")

# Faults in this code, not in a file: they must stop the run, never become an ERROR row.
CODE_FAULTS = (NameError, AttributeError, ImportError, TypeError)

# The `key` of a row that compares documents with each other rather than checking one.
CROSS_CHECK = "cross_check"


# --- text extraction ---------------------------------------------------------------
_reader = None


def _ocr_reader():
    global _reader
    if _reader is None:
        import easyocr
        import torch
        # verbose=False: the first-run model download draws a "█" progress bar, which a
        # Windows console (cp1252) cannot print. The download then dies and every scan
        # reads as unreadable.
        _reader = easyocr.Reader(["en"], gpu=torch.cuda.is_available(), verbose=False)
    return _reader


_MIN_PAGE_CHARS = 300      # a full certificate page reads well past this when upright


def _ocr_image(arr, turned=None) -> str:
    """OCR a page, turning it upright first if it was scanned sideways.

    Board certificates are printed landscape and scanned on their side; EasyOCR reads
    almost nothing off them until they are rotated.  Only pages that read poorly are
    retried, so upright pages cost nothing extra."""
    import cv2
    text = " ".join(_ocr_reader().readtext(arr, detail=0))
    if len(text) >= _MIN_PAGE_CHARS:
        return text
    best = text
    for rot in (cv2.ROTATE_90_COUNTERCLOCKWISE, cv2.ROTATE_90_CLOCKWISE, cv2.ROTATE_180):
        try:
            t = " ".join(_ocr_reader().readtext(cv2.rotate(arr, rot), detail=0))
        except Exception:
            continue
        if len(t) > len(best):
            best = t
            if turned is not None:
                turned.append(True)
        if len(best) >= _MIN_PAGE_CHARS:
            break
    return best


def _no_path(message: Any, path: Path) -> str:
    """A reader's error message with the file's full path cut down to its name: the message
    goes into the report and Telegram, where the student's folder path is only noise (as
    page_checks._why does)."""
    msg = str(message)
    path = Path(path)
    for full in (str(path), repr(str(path))[1:-1]):
        if full:
            msg = msg.replace(full, path.name)
    return msg


def read_pages(path: Path, max_pages: int = 20, sideways: Optional[List[int]] = None) -> List[str]:
    """The text of each page on its own, in order.  Needed where page ORDER matters — an
    e-Apostille and the certificate that follows it.

    A file that cannot be opened raises CheckError: an empty page list would read as "no
    number on any page" and be judged, where the truth is that nothing was read."""
    import pymupdf
    if path.suffix.lower() in (".jpg", ".jpeg", ".png"):
        return [" ".join(_ocr_reader().readtext(str(path), detail=0))]
    try:
        doc = pymupdf.open(str(path))
    except Exception as e:
        raise CheckError(f"could not open {path.name}: {type(e).__name__}: {_no_path(e, path)}") from e
    out: List[str] = []
    try:
        for i, page in enumerate(doc):
            if i >= max_pages:
                break
            t = page.get_text().strip()
            if len(t) < 40:                      # a scan: no text layer to read
                import numpy as np
                pix = page.get_pixmap(dpi=200)
                a = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width, pix.n)
                turned: List[bool] = []
                t = _ocr_image(a[:, :, :3], turned)
                if turned and sideways is not None:
                    sideways.append(i + 1)
            out.append(t)
    finally:
        doc.close()
    return out


def read_document(path: Path, max_pages: int = 20) -> Tuple[str, List[Tuple[int, int]]]:
    """All text in a file, plus each page's pixel size. Uses the PDF text layer when
    there is one; falls back to OCR for scans and images."""
    import pymupdf
    text, sizes = [], []
    if path.suffix.lower() in (".jpg", ".jpeg", ".png"):
        from PIL import Image
        with Image.open(path) as im:
            sizes.append(im.size)
        text.append(" ".join(_ocr_reader().readtext(str(path), detail=0)))
        return "\n".join(text), sizes
    try:
        doc = pymupdf.open(str(path))
    except Exception as e:
        return f"[unreadable: {_no_path(e, path)}]", []
    for i, page in enumerate(doc):
        if i >= max_pages:
            break
        sizes.append((round(page.rect.width), round(page.rect.height)))
        layer = page.get_text().strip()
        if len(layer) > 80:
            text.append(layer)
        else:
            pix = page.get_pixmap(dpi=150)
            import numpy as np
            arr = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width, pix.n)
            text.append(_ocr_image(arr[:, :, :3]))
    doc.close()
    return "\n".join(text), sizes


def notarised(text: str, path: Optional[Path]) -> bool:
    """Notarisation is proved by the wording OR by a visible notary/wax seal."""
    if has_any(text, R.NOTARY_WORDS):
        return True
    return bool(path) and PC.seal_present(path)


def has_any(text: str, words: List[str]) -> bool:
    """Substring match, then a fuzzy pass: OCR turns 'advocate' into 'advocale',
    'notary' into 'nolary' and 'attested' into 'attarta'."""
    import difflib
    t = norm(text)
    if any(w in t for w in words):
        return True
    tokens = re.findall(r"[a-z]{4,}", t)
    for w in words:
        parts = w.split()
        if len(parts) > 1:                     # multi-word phrases: match the first word
            w = parts[0]
        if len(w) < 5:
            continue
        for tok in tokens:
            if abs(len(tok) - len(w)) <= 2 and difflib.SequenceMatcher(None, tok, w).ratio() >= 0.8:
                return True
    return False


# --- small parsers -----------------------------------------------------------------
_MONTHS = {m: i for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], 1)}


_DIGIT_LOOKALIKE = str.maketrans({"I": "1", "l": "1", "S": "5", "s": "5", "O": "0",
                                  "o": "0", "B": "8", "Z": "2", "G": "6", "q": "9"})


def find_dates(text: str) -> List[dt.date]:
    """Every date we can recognise, in any of the formats these documents use."""
    out = []
    t = text.replace("\u2013", "-")
    for m in re.finditer(r"\b(\d{1,2})[./-](\d{1,2})[./-](\d{4})\b", t):           # 25-06-2008
        d, mo, y = (int(x) for x in m.groups())
        if d > 12 and mo <= 12:
            pass
        elif mo > 12:
            d, mo = mo, d
        try:
            out.append(dt.date(y, mo, d))
        except ValueError:
            pass
    for m in re.finditer(r"\b(\d{4})[./-](\d{1,2})[./-](\d{1,2})\b", t):           # 2026-09-15
        y, mo, d = (int(x) for x in m.groups())
        try:
            out.append(dt.date(y, mo, d))
        except ValueError:
            pass
    # "16 September 2026" and "15th Oct, 2004"
    for m in re.finditer(r"\b(\d{1,2})\s*(?:st|nd|rd|th)?[ ,.-]*"
                         r"(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*[ ,.-]+(\d{4})", t, re.I):
        d, mon, y = m.group(1), m.group(2).lower(), m.group(3)
        try:
            out.append(dt.date(int(y), _MONTHS[mon], int(d)))
        except ValueError:
            pass
    for m in re.finditer(r"\b(\d{4})\.(\d{1,2})\.(\d{1,2})\b", t):              # 2026.09.15
        y, mo, d = (int(x) for x in m.groups())
        try:
            out.append(dt.date(y, mo, d))
        except ValueError:
            pass
    for m in re.finditer(r"\b(\d{1,2})\.(\d{1,2})\.(\d{4})\b", t):              # 15.09.2026
        d, mo, y = (int(x) for x in m.groups())
        if mo > 12 >= d:
            d, mo = mo, d
        try:
            out.append(dt.date(y, mo, d))
        except ValueError:
            pass
    # "Sep 16, 2026" / "September 16 2026" — month first
    for m in re.finditer(r"\b(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*"
                         r"[ ,.-]+(\d{1,2})(?:st|nd|rd|th)?[ ,.-]+(\d{4})", t, re.I):
        mon, d, y = m.group(1).lower(), m.group(2), m.group(3)
        try:
            out.append(dt.date(int(y), _MONTHS[mon], int(d)))
        except ValueError:
            pass
    # two-digit year: 15-09-26
    for m in re.finditer(r"\b(\d{1,2})[./-](\d{1,2})[./-](\d{2})\b(?!\d)", t):
        d, mo, y = (int(x) for x in m.groups())
        if mo > 12 >= d:
            d, mo = mo, d
        year = 2000 + y if y <= (TODAY.year % 100) + 15 else 1900 + y
        try:
            out.append(dt.date(year, mo, d))
        except ValueError:
            pass
    # "ISth September 2026" — OCR reads 1 as I and 5 as S in an ordinal day.  Only the
    # day is allowed to carry look-alike letters, and only in the ordinal form, so this
    # cannot invent a date out of ordinary words.
    for m in re.finditer(r"\b([0-9IlSsOoBZGq]{1,2})\s*(?:st|nd|rd|th)[ ,.-]*"
                         r"(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*[ ,.-]+(\d{4})",
                         t, re.I):
        day = m.group(1).translate(_DIGIT_LOOKALIKE)
        if not day.isdigit():
            continue
        try:
            out.append(dt.date(int(m.group(3)), _MONTHS[m.group(2).lower()], int(day)))
        except (ValueError, KeyError):
            pass
    # digits printed one per box: "1 5  1 0  2 0 0 4" -> 15/10/2004
    for m in re.finditer(r"(?<!\d)(\d)[\s|]{0,3}(\d)[\s|]{0,4}(\d)[\s|]{0,3}(\d)[\s|]{0,4}"
                         r"(\d)[\s|]{0,3}(\d)[\s|]{0,3}(\d)[\s|]{0,3}(\d)(?!\d)", t):
        g = "".join(m.groups())
        try:
            cand = dt.date(int(g[4:]), int(g[2:4]), int(g[:2]))
        except ValueError:
            continue
        if 1900 <= cand.year <= TODAY.year + 15:
            out.append(cand)
    return out


def money_value(raw: str) -> Optional[float]:
    """'25,73,067.50' -> 2573067.5 ; '33,74,119,69' (OCR writes the decimal point as a
    comma) -> 3374119.69 ; '2,300,900.30' -> 2300900.3"""
    s = raw.strip()
    groups = s.split(",")
    if len(groups) >= 3 and "." not in s and len(groups[-1]) == 2:
        s = ",".join(groups[:-1]) + "." + groups[-1]
    try:
        return float(s.replace(",", ""))
    except ValueError:
        return None


def find_money_taka(text: str) -> List[int]:
    """Amounts in taka, in lakh grouping or western grouping."""
    out = []
    for m in re.finditer(r"\b(\d{1,3}(?:,\d{2,3})+(?:\.\d{1,2})?|\d{6,9}(?:\.\d{1,2})?)\b", text):
        v = money_value(m.group(1))
        if v is not None and 100_000 <= v <= 100_000_000:
            out.append(int(v))
    return sorted(set(out), reverse=True)


def months_between(a: dt.date, b: dt.date) -> int:
    return (b.year - a.year) * 12 + (b.month - a.month) - (1 if b.day < a.day else 0)


# A figure counts as money only when it is written like money: thousands separators
# ("25,00,000", "2,500,000.00"), decimals ("2500000.00"), or a Tk / BDT / taka / ৳ marker.
# A bare digit run is an account, customer, cheque or phone number far more often.
_NOT_BEFORE = r"(?<![\d,/])(?<!\d[.-])"        # not the middle of a date or a longer number
_NOT_AFTER = r"(?!\d|[.,/-]\d|\s*%)"
_MONEY_RE = re.compile(
    _NOT_BEFORE + r"(?:(?P<sep>\d{1,3}(?:,\d{2,3})+(?:[.,]\d{1,2})?)|(?P<dec>\d+\.\d{1,2}))" + _NOT_AFTER
    + r"|(?:(?<![a-z])(?:tk|bdt|taka)\.?|৳)\s*[:=]?\s*(?P<pre>\d+)" + _NOT_AFTER
    + r"|" + _NOT_BEFORE + r"(?P<post>\d+)\s*(?:/-\s*)?(?:tk|bdt|taka)(?![a-z])",
    re.I)
# A plain number, for a field whose label already says it is an amount ("Tax paid 15000"): at
# most 9 digits (a longer run is a TIN, challan or reference number), and never one written
# after an ID label or a challan's ("Tax paid vide Challan No 123456789").
_PLAIN_RE = re.compile(_NOT_BEFORE + r"\d{3,9}" + _NOT_AFTER)
_PLAIN_ID_LABELS = ["challan"]
_YEAR_RE = re.compile(r"(?:19|20)\d{2}")
# Words that make the figure after an "Account …" label money after all ("Account Balance").
_MONEY_WORDS = re.compile(r"balance|amount|paid|tk|taka|bdt|৳", re.I)
_ID_AFTER = 25                 # a figure this close after an ID label is that ID


def _label_re(labels: List[str]) -> "re.Pattern[str]":
    """Any of `labels` as a whole word (spaces in a label match any spacing)."""
    alts = sorted({re.escape(l).replace(r"\ ", r"\s*") for l in labels}, key=len, reverse=True)
    return re.compile(r"(?<![a-z0-9])(?:" + "|".join(alts) + r")(?![a-z0-9])\.?", re.I)


def _id_label_re() -> "re.Pattern[str]":
    return _label_re(R.ID_NUMBER_LABELS)


def money_figures(text: str) -> List[Tuple[int, float]]:
    """(position, value) of every figure written like money, in reading order.

    A figure within 25 characters after an account / customer / ID / number / phone label
    is that number, not money — unless a money word ("balance", "amount", "paid", "Tk")
    sits between, as in "Account Balance: Tk 25,00,000.00". Labels are whole words, so
    "Paid" holds no "id" and "Nominee" no "no"."""
    text = text or ""
    ids = [m.end() for m in _id_label_re().finditer(text)]
    out: List[Tuple[int, float]] = []
    for m in _MONEY_RE.finditer(text):
        group = next(g for g in ("sep", "dec", "pre", "post") if m.group(g) is not None)
        pos = m.start(group)
        if _after_id(text, ids, pos):
            continue
        v = money_value(m.group(group))
        if v is not None:
            out.append((pos, v))
    return out


def _after_id(text: str, ids: List[int], pos: int) -> bool:
    """Is the figure at `pos` written right after an ID label (one of `ids`, their end positions)
    with no money word between: that number, not money."""
    return any(0 <= pos - end <= _ID_AFTER and not _MONEY_WORDS.search(text[end:pos]) for end in ids)


def _opening_spans(text: str) -> List[Tuple[int, int]]:
    return [m.span() for m in _label_re(R.OPENING_BALANCE_WORDS).finditer(text or "")]


def _labelled_figures(text: str, labels: List[str], window: int = 60,
                      plain: bool = False) -> List[Tuple[int, float]]:
    """(position, value) of the first money figure within `window` characters after each
    of `labels`. A label that is part of an opening-balance label ("Opening Balance",
    "Balance B/F") is not the balance. `plain` also takes a bare number of 3 to 9 digits
    that is not a year, for a label that already says the figure is an amount — unless an
    ID or challan label comes right before it (that number is the ID)."""
    text = text or ""
    figures = money_figures(text)
    if plain:
        seen = {p for p, _ in figures}
        ids = [m.end() for m in _label_re(R.ID_NUMBER_LABELS + _PLAIN_ID_LABELS).finditer(text)]
        for m in _PLAIN_RE.finditer(text):
            if (m.start() not in seen and not _YEAR_RE.fullmatch(m.group())
                    and not _after_id(text, ids, m.start())):
                figures.append((m.start(), float(m.group())))
        figures.sort()
    opening = _opening_spans(text)
    out: Dict[int, float] = {}
    for m in _label_re(labels).finditer(text):
        if any(a < m.end() and m.start() < b for a, b in opening):
            continue
        for pos, v in figures:
            if pos >= m.end():
                if pos - m.end() <= window:
                    out[pos] = v
                break
    return sorted(out.items())


def labelled_amounts(text: str, labels: List[str], window: int = 60, plain: bool = False) -> List[float]:
    """The amounts the document itself labels with one of `labels` (rules.BALANCE_LABELS,
    rules.TAX_PAID_LABELS): the first money figure after each label, in reading order."""
    return [v for _, v in _labelled_figures(text, labels, window, plain)]


# --- dates of birth, the passport's MRZ and its number ------------------------------
_DOB_LABEL_RE = re.compile(r"(?<![a-z])(?:date\s*of\s*birth|birth\s*date|d\.?\s?o\.?\s?b)(?![a-z])\.?", re.I)


def labelled_dob(text: str) -> Optional[dt.date]:
    """The date printed beside a "Date of Birth" / "Birth Date" / "DOB" label: the first
    date within 60 characters after it (digits printed one per box included), read only
    up to the next label ("Date of Registration", "Issue Date") so that date is never
    taken for the birth date."""
    stop = _label_re(R.DOB_STOP_LABELS)
    for m in _DOB_LABEL_RE.finditer(text or ""):
        window = text[m.end():m.end() + 60]
        cut = stop.search(window)
        found = find_dates(window[:cut.start()] if cut else window)
        if found:
            return found[0]
    return None


_MRZ_JUNK = re.compile(r"[^A-Z0-9<]")
_MRZ_WORD = re.compile(r"[A-Z0-9<]*(?:<|\d)[A-Z0-9<]*")
_LINE2_HINT = re.compile(r"[A-Z<]{3}\d{7}[MF<]")


def _mrz_runs(text: str) -> List[str]:
    """Stretches of the text that could be a machine-readable line, with the spaces OCR put
    inside them taken out: every line of the text that holds a '<', and every run of
    neighbouring words made of MRZ characters (OCR joins a page's words with spaces and
    splits a line where it likes)."""
    t = (text or "").upper().replace("«", "<<").replace("‹", "<")
    runs: List[str] = []
    for line in t.splitlines():
        s = _MRZ_JUNK.sub("", line)
        if len(s) >= 28 and ("<" in s or _LINE2_HINT.search(s)):
            runs.append(s)
    group: List[str] = []
    for word in t.split() + [""]:
        w = _MRZ_JUNK.sub("", word)
        if w and len(w) >= 3 and _MRZ_WORD.fullmatch(w):
            group.append(w)
            continue
        s = "".join(group)
        if len(s) >= 28 and ("<" in s or _LINE2_HINT.search(s)):
            runs.append(s)
        group = []
    return list(dict.fromkeys(runs))


def passport_mrz(text: str) -> Dict[str, Any]:
    """The passport's machine-readable zone as read off the scan text:
    {passport_no, passport_no_ok, dob, dob_ok, expiry, expiry_ok, sex, line2_ok, checks,
    name}, the dates as "YYYY-MM-DD" ("" when unreadable), each *_ok meaning its own check
    digit agrees, line2_ok every check digit, checks how many agree. {} when the text holds
    no line 2 with a single check digit that agrees."""
    from src.scraper.ocr_validator import parse_mrz_line1, parse_mrz_line2
    best, name = None, ""
    for run in _mrz_runs(text):
        two = parse_mrz_line2(run)
        if two and (best is None or (two["line2_ok"], two["checks"]) > (best["line2_ok"], best["checks"])):
            best = two
        if not name:
            for m in re.finditer(r"P[<KC(]?[A-Z]{3}", run):
                one = parse_mrz_line1(run[m.start():m.start() + 44])
                if one and one.get("line1_ok"):
                    name = one["full_name"]
                    break
    if not best:
        return {}
    return {"passport_no": best["passport_no"], "passport_no_ok": bool(best["passport_no_ok"]),
            "dob": best["dob"] or "", "dob_ok": bool(best["dob_ok"]),
            "expiry": best["expiry"] or "", "expiry_ok": bool(best["expiry_ok"]),
            "sex": best["sex"], "line2_ok": bool(best["line2_ok"]), "checks": best["checks"], "name": name}


def _iso(value: Any) -> Optional[dt.date]:
    try:
        return dt.date.fromisoformat(str(value or "").strip()[:10])
    except ValueError:
        return None


def mrz_dob(mrz: Dict[str, Any], portal_dob: Optional[dt.date] = None) -> Optional[dt.date]:
    """The MRZ's date of birth when it can be trusted: its check digit agrees AND the line
    is whole, or at least 2 check digits agree, or it is the portal's own date of birth.
    One agreeing digit alone is a 1-in-10 chance."""
    return _trusted(mrz, "dob", portal_dob)


def mrz_expiry(mrz: Dict[str, Any], portal_expiry: Optional[dt.date] = None) -> Optional[dt.date]:
    """The MRZ's expiry date, trusted on the same terms as its date of birth."""
    return _trusted(mrz, "expiry", portal_expiry)


def _trusted(mrz: Dict[str, Any], field: str, portal: Optional[dt.date]) -> Optional[dt.date]:
    d = _iso(mrz.get(field)) if mrz.get(f"{field}_ok") else None
    if d and (mrz.get("line2_ok") or (mrz.get("checks") or 0) >= 2 or d == portal):
        return d
    return None


# OCR's usual letter/digit confusions, folded the same way on both sides (field_check
# does the same in _ocr_variant).
_OCR_FOLD = str.maketrans({"a": "4", "o": "0", "i": "1", "l": "1", "s": "5", "b": "8"})


def _ocr_fold(s: str) -> str:
    return re.sub(r"\W", "", str(s or "").lower()).translate(_OCR_FOLD)


def passport_number_on(text: str, number: str) -> Tuple[str, str]:
    """Where the portal's passport number is on a scan: ("exact", number); ("ocr", token)
    when a 9-character token is within 2 edits of it once OCR's letter/digit confusions
    are folded (A99900001 read as 499900001); ("", "") when neither."""
    pno = re.sub(r"\s+", "", (number or "").upper())
    if not pno:
        return "", ""
    if pno in re.sub(r"\s+", "", (text or "").upper()):
        return "exact", pno
    want = _ocr_fold(pno)
    best: Tuple[int, str] = (3, "")
    for tok in re.findall(r"(?<![A-Za-z0-9])[A-Za-z0-9]{9}(?![A-Za-z0-9])", text or ""):
        d = edit_distance(_ocr_fold(tok), want)
        if d < best[0]:
            best = (d, tok.upper())
    return ("ocr", best[1]) if best[1] else ("", "")


# --- per-document checks -----------------------------------------------------------
def check_passport(text: str, student: Dict[str, str], sizes, path: Optional[Path] = None) -> List[Tuple[str, str]]:
    out = []
    mrz = passport_mrz(text)
    expiry = student.get("Passport Expiry", "")
    portal_exp = None
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", expiry or ""):
        portal_exp = dt.date.fromisoformat(expiry)
    # The validity is read from the passport itself when its MRZ expiry is check-digit
    # valid (and not a lone lucky digit); the portal's value is what a consultant typed.
    exp_date = mrz_expiry(mrz, portal_exp)
    notes = []
    if exp_date:
        if portal_exp and portal_exp != exp_date:
            notes.append((FLAG, f"portal expiry {portal_exp}, the passport's MRZ reads {exp_date} — "
                                "correct the portal"))
    elif portal_exp:
        exp_date = portal_exp
        notes.append((NOTE, "the expiry date was taken from the portal (the passport's MRZ could not be read)"))
    else:
        doc_dates = [d for d in find_dates(text) if d.year >= TODAY.year]
        exp_date = max(doc_dates) if doc_dates else None
    if exp_date:
        months = months_between(TODAY, exp_date)
        if months < 0:
            out.append((FAIL, f"passport expired on {exp_date}"))
        elif months < R.PASSPORT_MIN_MONTHS:
            out.append((FAIL, f"only {months} month(s) validity left (expires {exp_date}); "
                              "guideline requires at least 1 year — must be renewed"))
        else:
            out.append((PASS, f"valid until {exp_date} ({months} months left)"))
    else:
        out.append((FLAG, "could not read an expiry date from the scan"))
    out += notes
    pno = re.sub(r"\s+", "", (student.get("Passport No") or "").upper())
    if pno:
        how, token = passport_number_on(text, pno)
        if how == "exact":
            out.append((PASS, f"passport number {pno} found on the scan"))
        elif mrz.get("passport_no_ok") and mrz.get("passport_no") and mrz["passport_no"] != pno:
            out.append((FLAG, f"the passport's MRZ reads number {mrz['passport_no']}, the portal says "
                              f"{pno} — check which is right"))
        elif how == "ocr":
            out.append((PASS, f"passport number {pno} found on the scan, read as {token}"))
            out.append((NOTE, f"the scan reads {token} for {pno} — probably an OCR misread"))
        else:
            out.append((FLAG, f"portal passport number {pno} not found in the scan text"))
    # NOTE: seal-placement check disabled — it cannot yet separate the passport
    # image from the page reliably, so it flagged every scan. Being reworked.
    # out += PC.passport_seal_check(path) if path else []
    return out


def check_photo(text: str, student: Dict[str, str], sizes, path: Optional[Path] = None) -> List[Tuple[str, str]]:
    out = []
    if not path or not path.exists():
        return [(FLAG, "photo file could not be opened")]
    try:
        from PIL import Image, ImageStat
        with Image.open(path) as im:
            w, h = im.size
            ratio = w / h if h else 0
            if abs(ratio - R.PHOTO_RATIO) <= 0.06:
                out.append((PASS, f"{w}x{h}px, ratio matches 3.5x4.5 cm"))
            else:
                out.append((FLAG, f"{w}x{h}px, ratio {ratio:.2f} differs from 3.5x4.5 (0.78)"))
            if min(w, h) < 300:
                out.append((FLAG, f"low resolution ({w}x{h}px) — may print blurry"))
            rgb = im.convert("RGB")
            # sample the background ABOVE the head (top band + top corners); the lower
            # edges are the subject's shoulders and hair, not the backdrop
            band = [rgb.getpixel((x, y))
                    for y in range(0, max(2, h // 8), max(1, h // 40))
                    for x in range(0, w, max(1, w // 30))]
            corners = [rgb.getpixel((x, y))
                       for y in range(0, max(2, h // 4), max(1, h // 30))
                       for x in list(range(0, max(2, w // 10))) + list(range(w - max(2, w // 10), w))]
            sample = band + corners
            avg = tuple(sum(c[i] for c in sample) / len(sample) for i in range(3))
            if min(avg) > 190:                 # off-white is fine; only a dark background fails
                out.append((PASS, "background is white or near-white"))
            else:
                out.append((FAIL, f"background is not white (avg {tuple(int(a) for a in avg)}) — "
                                  "guideline requires a white background"))
            gray = rgb.convert("L")
            if ImageStat.Stat(gray).stddev[0] < 30:
                out.append((FLAG, "image looks flat/low contrast — check sharpness"))
    except CODE_FAULTS:
        raise
    except Exception as e:                 # locked, truncated or not an image: not a photo problem
        out.append((ERROR, f"could not open the image ({type(e).__name__}: {_no_path(e, path)})"))
    if path.suffix.lower() not in R.PHOTO_FORMATS:
        out.append((FAIL, f"photo is {path.suffix} — reviewer requires JPG/JPEG"))
    if (student.get("Gender") or "").upper().startswith("F"):
        out.append((NOTE, "female applicant — worth a glance that both ears are visible"))
    return out


def check_birth_cert(text: str, student, sizes, path=None, others=None) -> List[Tuple[str, str]]:
    out = []
    if has_any(text, R.ONLINE_BIRTH_WORDS):
        out.append((PASS, "looks like an online birth registration certificate"))
    else:
        out.append((FLAG, "could not confirm it is an ONLINE birth certificate"))
    out.append((PASS, "notarised (wording or seal found)") if notarised(text, path)
               else (FAIL, "no notarisation found — guideline requires notarisation"))
    if not re.search(r"[a-z]", text.lower()):
        out.append((FLAG, "little English text — if the certificate is not in English it must be "
                          "translated on a lawyer pad and notarised"))
    out += birth_date_check(text, (others or {}).get("passport", ""), student)
    return out


def birth_date_check(cert_text: str, passport_text: str, student) -> List[Tuple[str, str]]:
    """The date of birth must agree with the PASSPORT, not with what the portal says.

    Each side is read where it is labelled: the certificate's "Date of Birth" field, the
    passport's MRZ (when its check digits can be trusted) or else its "Date of Birth"
    field. FAIL only when both are read and differ and the passport's date is sure (a
    trusted MRZ, or a labelled date that is also the portal's); anything less is a FLAG."""
    if not passport_text:
        return [(FLAG, "no passport on file to check the date of birth against")]
    portal = _iso(student.get("DOB"))
    cert = labelled_dob(cert_text)
    mrz = mrz_dob(passport_mrz(passport_text), portal)
    passport = mrz or labelled_dob(passport_text)
    if cert and passport and cert == passport:
        return [(PASS, f"date of birth {cert} is the same on the birth certificate and the passport")]
    if not cert or not passport:
        unread = ("the birth certificate" if not cert else "") + \
                 (" and " if not cert and not passport else "") + ("the passport" if not passport else "")
        read = cert or passport
        side = "birth certificate" if cert else "passport"
        portal_bit = (f"; the {side}'s {read} is the portal's date of birth" if read and read == portal
                      else "")
        return [(FLAG, f"could not read the date of birth on {unread}{portal_bit} — compare the birth "
                       "certificate and the passport by eye")]
    if cert.year == passport.year:
        return [(FLAG, f"the birth certificate gives {cert} and the passport {passport}: the year agrees, "
                       "the day and month do not (boxed digits often defeat OCR) — compare them by eye")]
    sure = "MRZ" if mrz else ("date of birth field, which is also the portal's" if passport == portal else "")
    if sure:
        return [(FAIL, f"the birth certificate gives date of birth {cert} but the passport ({sure}) "
                       f"gives {passport}")]
    return [(FLAG, f"the birth certificate gives date of birth {cert} and the passport {passport} — "
                   "compare them by eye")]


def nid_numbers(text: str) -> List[str]:
    """NID and birth-registration numbers found in a scan.

    Bangladesh uses a 10-digit Smart NID, a 13 or 17-digit legacy NID, and a 17-digit
    birth registration number.  Cards print them in spaced groups ("123 456 7890") and
    OCR keeps the spaces, so digit runs are joined up before their length is judged.
    Mobile numbers sit on the same pages and look the same once joined, so anything
    starting 01 is left out: an 11-digit mobile clipped by OCR is otherwise a perfect
    10-digit Smart NID.
    """
    out = set()
    for run in re.findall(r"\d[\d\s.\-]{7,32}\d", text or ""):
        digits = re.sub(r"\D", "", run)
        for size in (17, 13, 10):
            if len(digits) == size:
                if digits in R.BOILERPLATE_NUMBERS:
                    break                       # printed on the form, not a person's
                if size <= 13 and digits.startswith("01"):
                    break                       # a mobile number
                if size == 13 and digits.startswith("880"):
                    break                       # a mobile number with the country code
                out.add(digits)
                break
    return sorted(out)


def check_nid(text: str, student, sizes, whose: str, path=None, others=None) -> List[Tuple[str, str]]:
    out = []
    out.append((PASS, "notarised (wording or seal found)") if notarised(text, path)
               else (FAIL, "no notarisation found — parents' NID must be notarised"))
    out.append((PASS, "on an advocate/lawyer pad") if has_any(text, R.LAWYER_PAD_WORDS)
               else (FLAG, "could not confirm the translation is on a lawyer pad"))

    # The name must agree with the PASSPORT — a Bangladeshi passport carries the holder's
    # name and both parents' names.
    key = {"father": "Father", "mother": "Mother", "student": "Full Name"}.get(whose, "Full Name")
    name = student.get(key, "")
    passport_text = (others or {}).get("passport", "")
    # The whole name, not its first word: a parent's NID that shares only the family name
    # is not that parent's.
    if name and name_in(text, name) is not None:
        on_nid = bool(name_in(text, name))
        on_passport = bool(name_in(passport_text, name))
        if on_nid and on_passport:
            out.append((PASS, f"{whose}'s name '{name}' appears on both the NID and the passport"))
        elif on_nid and not passport_text:
            out.append((FLAG, f"{whose}'s name is on the NID, but there is no passport to check it against"))
        elif on_nid:
            out.append((FLAG, f"{whose}'s name '{name}' is on the NID but could not be found on "
                              "the passport — compare them by eye"))
        else:
            out.append((FLAG, f"{whose}'s name '{name}' not found in the NID scan text"))

    # Bangladesh issues two kinds of NID: a 10-digit Smart card and a 13-digit old one.
    # Both are valid, and a document may show either.  What must agree is the number on
    # the translation and the number on the original — so numbers are only ever compared
    # against numbers of the SAME length.  A 10-digit on one page and a 13-digit on the
    # other is two formats of one identity, not a mismatch.
    pages: List[str] = []
    if path is not None:
        try:
            pages = read_pages(path, max_pages=8)
        except CODE_FAULTS:
            raise
        except Exception as e:
            out.append((ERROR, f"could not read the NID page by page ({_no_path(e, path)})"))
    per_page = [set(nid_numbers(t)) for t in pages]
    every = sorted({n for s in per_page for n in s}) or nid_numbers(text)

    if not every:
        out.append((FLAG, "no NID/birth-registration number could be read from the scan"))
    else:
        out += nid_number_agreement(per_page, every)
    return out


def nid_number_agreement(per_page: List[set], every: List[str]) -> List[Tuple[str, str]]:
    """Whether the ID number reads the same on the original and the translation.

    Numbers are only compared with numbers of the SAME length (a 10-digit Smart NID and a
    13-digit old one are two formats of one identity). A number on 2 or more pages agrees.
    A page none of whose numbers is on any other page is compared with the closest number
    elsewhere: 1-2 digits apart is an OCR misread (NOTE); 3 or more is worth a human eye
    (FLAG, naming both numbers) — never a verdict, OCR misreads digits too often."""
    agreed: List[str] = []
    misread: List[Tuple[str, str, int]] = []
    differ: List[Tuple[str, str, int]] = []
    for size in (10, 13, 17):
        pages_with = [s for s in ({n for n in page if len(n) == size} for page in per_page) if s]
        if len(pages_with) < 2:
            continue
        for i, nums in enumerate(pages_with):
            elsewhere = set().union(*(s for j, s in enumerate(pages_with) if j != i))
            shared = nums & elsewhere
            if shared:
                agreed += [n for n in sorted(shared) if n not in agreed]
                continue
            d, mine, theirs = min((edit_distance(a, b), a, b) for a in sorted(nums) for b in sorted(elsewhere))
            pair = (mine, theirs, size)
            if any({mine, theirs} == {a, b} for a, b, _ in misread + differ):
                continue                   # the same two numbers, seen from the other page
            (misread if d <= 2 else differ).append(pair)
    out: List[Tuple[str, str]] = []
    for mine, theirs, size in differ[:2]:
        out.append((FLAG, f"the {size}-digit ID number reads {mine} on one page and {theirs} on another "
                          "— check the translation against the original by eye"))
    for mine, theirs, size in misread[:2]:
        out.append((NOTE, f"the {size}-digit ID number reads {mine} on one page and {theirs} on another "
                          "— probably an OCR misread"))
    if agreed:
        out.append((PASS, "ID number " + ", ".join(agreed[:3]) +
                          " reads the same on the original and the translation"))
    if not out:
        kinds = ", ".join(f"{n} ({len(n)}-digit)" for n in every[:3])
        out.append((NOTE, f"ID number {kinds} read, but not on two pages in the same format, "
                          "so the original and the translation could not be compared"))
    return out


_DATE_WORD = r"(?:dated?|datc|dale|datex)"          # OCR: "Datc", "Dale", "Issue Datex"
_NOT_BIRTH = r"(?!\s*o[fl]\s*birth)"                # "Date of Birth", OCR "Date ol Birth"
# whole words only ("candidate", "update", "therefore" are no label), never "date of birth"
_ISSUE_LABEL = re.compile(
    r"\b(?:" + _DATE_WORD + _NOT_BIRTH + r"|ref(?:erence)?|memo|serial)\b\.?\s*(?:no\b\.?)?\s*[:.\-]?"
    r"[^\n]{0,90}", re.I)
# The certificate's own number-and-date line, searched in the WHOLE text because a notary or
# translation page often comes first. Only the 25 characters after the date word are read, never
# the certificate number (a number like "UC-2201-2026-.." would otherwise give label-digits 22-01-2026).
_STRONG_ISSUE_LABEL = re.compile(
    r"(?:\bcerti\w{2,8}\s*no\b[^\n]{0,40}?\b" + _DATE_WORD + _NOT_BIRTH +
    r"|\bdate\s*of\s*issue|\bissue\s*" + _DATE_WORD + r")\b(?P<rest>\s*[:.;\-]?[^\n]{0,25})", re.I)
# Dates that belong to another paper: an affidavit the certificate cites, the notary's register,
# the advocate's attestation, the translator.
_NOT_ISSUE = re.compile(r"affidavit|notary\s*public\s*(?:no|:)|notari[sz]ed|\badv(?:ocate)?\b|attest|translat",
                        re.I)
_NOT_ISSUE_BEFORE = 90      # how far back on the same line that context is looked for


def _issue_recent(d: dt.date) -> bool:
    return 0 <= (TODAY - d).days <= 3 * 365          # never a future date, never a birth date


def _issue_date_in(window: str) -> Tuple[Optional[dt.date], str]:
    cut = _NOT_ISSUE.search(window)
    if cut:
        window = window[:cut.start()]               # the line runs on into the notary's part
    got = [d for d in find_dates(window) if _issue_recent(d)]
    if got:
        return max(got), "label"
    digits = re.sub(r"\D", "", window)
    for i in range(len(digits) - 7):                # find DDMMYYYY inside the digit run
        chunk = digits[i:i + 8]
        try:
            d = dt.date(int(chunk[4:]), int(chunk[2:4]), int(chunk[:2]))
        except ValueError:
            continue
        if _issue_recent(d):                        # was abs(): 2027-02-23 passed as "-5 months ago"
            return d, "label-digits"
    return None, ""


def issue_date_labelled(text: str) -> Tuple[Optional[dt.date], str]:
    """The date a certificate was issued, and where it was read:

      "label"         a date on a 'Date'/'Ref'/'Memo'/'Serial' line near the top, or on the
                      certificate's own "Certificate No .. Date" / "Date of Issue" / "Issue Date"
                      line anywhere in the text (a notary or translation page often comes first)
      "label-digits"  DDMMYYYY found in that line's digits (OCR often inserts stray dots:
                      "302.083.2026")
      "fallback"      no such line: the latest date of the last 3 years anywhere on the
                      page — which may be the notary's date, not the issue date
      ""              no date at all

    Every label in the first 1200 characters is tried, not only the first. A date written after
    "affidavit", "Notary Public No", "Adv.", "attested" or "translation" on the same line is never
    the issue date. Never a family member's date of birth and never a future date: only dates of
    the last 3 years count."""
    text = text or ""
    for rx, where in ((_ISSUE_LABEL, text[:1200]), (_STRONG_ISSUE_LABEL, text)):
        for m in rx.finditer(where):
            line_start = where.rfind("\n", 0, m.start()) + 1
            if _NOT_ISSUE.search(where[max(line_start, m.start() - _NOT_ISSUE_BEFORE):m.start()]):
                continue                            # the affidavit's / notary's / translator's date
            d, src = _issue_date_in(m.group("rest") if rx is _STRONG_ISSUE_LABEL else m.group(0))
            if d:
                return d, src
    recent = [d for d in find_dates(text) if _issue_recent(d)]
    return (max(recent), "fallback") if recent else (None, "")


def issue_date(text: str) -> Optional[dt.date]:
    """The date a certificate was issued (issue_date_labelled without the source)."""
    return issue_date_labelled(text)[0]


def check_family_cert(text: str, student, sizes, path=None) -> List[Tuple[str, str]]:
    out = []
    recent, source = issue_date_labelled(text)
    if recent and source == "fallback":
        out.append((FLAG, f"no Date/Ref line could be read; the latest recent date on the certificate is "
                          f"{recent}, which may be the notary date — check the issue date (3-month "
                          "validity) by eye"))
    elif recent:
        age = months_between(recent, TODAY)
        if age <= R.FAMILY_CERT_MAX_MONTHS:
            out.append((PASS, f"issued {recent} ({age} month(s) ago) — within the 3-month validity"))
        else:
            out.append((FAIL, f"issued {recent} ({age} months ago) — older than the 3-month validity"))
    else:
        out.append((FLAG, "could not read the issue date from the scan — check the 3-month validity by eye"))
    out.append((PASS, "issued by Union Parishad / City Corporation") if has_any(text, R.UNION_ISSUER_WORDS)
               else (FLAG, "issuer (Union Parishad / City Corporation) not recognised"))
    out.append((PASS, "notarised through an advocate (wording or seal found)") if notarised(text, path)
               else (FAIL, "no notarisation found — guideline requires an advocate's notarisation"))
    name = student.get("Full Name", "")
    first_chunk = norm(text)[:1200]
    if name and name_in(first_chunk, name) is not None:
        out.append((PASS, "student's name appears at the top of the certificate")
                   if name_in(first_chunk, name)
                   else (FLAG, "student's name not found near the top — it must be in the first row"))
    ids = len(re.findall(r"\b\d{10,17}\b", text))
    out.append((PASS, f"{ids} NID/birth-registration numbers listed") if ids >= 2
               else (FLAG, "few or no NID/birth numbers found — each member needs one"))
    return out


def check_academic(text: str, student, sizes, path=None, others=None) -> List[Tuple[str, str]]:
    out = []
    # The apostille QR is mandatory here, and each apostille must be followed by the
    # certificate and transcript for the qualification it covers.
    if path is not None:
        try:
            sideways: List[int] = []
            out += PC.academic_check(path, read_pages(path, sideways=sideways))
            if sideways:
                out.append((NOTE, "page(s) " + ", ".join(str(n) for n in sideways) +
                                  " are scanned sideways — they were turned upright to be read, "
                                  "but the student should upload them the right way up"))
        except CODE_FAULTS:
            raise
        except Exception as e:
            out.append((ERROR, f"could not check the e-Apostille pages ({_no_path(e, path)})"))
    elif has_any(text, R.APOSTILLE_WORDS):
        out.append((PASS, "e-apostille wording found"))
    else:
        out.append((FAIL, "no e-apostille found — certificate and transcript must be "
                          "apostilled via mygov.bd"))
    if has_any(text, R.PROVISIONAL_WORDS):
        out.append((FAIL, "provisional certificate — not accepted; the original certificate is required"))
    out.append((PASS, "notarised (wording or seal found)") if notarised(text, path)
               else (FLAG, "no notarisation found on the photocopies"))
    # The student's and both parents' names must appear on the academic papers.
    for who, col in (("student", "Full Name"), ("father", "Father"), ("mother", "Mother")):
        name = (student.get(col) or "").strip()
        if not name:
            continue
        out.append((PASS, f"{who}'s name ({name}) appears on the academic papers")
                   if name_in(text, name)
                   else (FLAG, f"{who}'s name '{name}' not found on the academic papers"))
    for label, col in (("HSC", "HSC GPA"), ("SSC", "SSC GPA")):
        gpa = (student.get(col) or "").strip()
        if gpa:
            out.append((PASS, f"{label} GPA {gpa} matches the portal")
                       if gpa_on(text, gpa)
                       else (FLAG, f"portal {label} GPA {gpa} not found in the certificate text"))
    return out


_GPA_LABEL_RE = re.compile(r"(?<![a-z])(?:c?gpa|grade\s*point\s*average)(?![a-z])\W{0,15}?"
                           r"(\d(?:\.\d{1,2})?)(?![\d]|\.\d)", re.I)
_GPA_NUMBER_RE = re.compile(r"(?<![\d.])(\d\.\d{1,2})(?![\d]|\.\d)")


def gpa_on(text: str, gpa: str) -> bool:
    """Whether the certificate shows the portal's GPA, compared as a number to 2 decimals:
    "5.00" is found as "5.00", "5.0" or a labelled "GPA: 5", never inside "55", "15.00" or
    "5.25". A bare whole number counts only beside a GPA label. When the text labels any GPA,
    only the labelled values count: a transcript's subject grade points (5.00, 4.00) are
    decimals too (as field_check does)."""
    try:
        want = round(float(gpa), 2)
    except ValueError:
        return bool(re.search(rf"(?<![\w.]){re.escape(gpa)}(?![\w]|\.\d)", text or ""))
    found = [m.group(1) for m in _GPA_LABEL_RE.finditer(text or "")]
    if not found:
        found = _GPA_NUMBER_RE.findall(text or "")
    return any(round(float(v), 2) == want for v in found)


def looks_like_solvency(text: str) -> bool:
    return has_any(text, R.SOLVENCY_WORDS)


def looks_like_statement(text: str) -> bool:
    t = (text or "").lower()
    hits = sum(w in t for w in ("statement", "debit", "credit", "withdrawal", "deposit",
                                "transaction", "balance b/f", "closing balance"))
    return hits >= 2


def document_date(text: str) -> Optional[dt.date]:
    """The date the paper itself carries — the one beside a Date/Issued label if there is
    one, otherwise the latest date on the page."""
    d = issue_date(text)
    if d:
        return d
    dates = sorted(set(find_dates(text)))
    return dates[-1] if dates else None


_ROW_DATE_AT = re.compile(r"\d{1,2}\s*[./-]\s*(?:\d{1,2}|[a-z]{3,9})\s*[./-]\s*\d{2,4}"
                          r"|\d{4}[./-]\d{1,2}[./-]\d{1,2}", re.I)
_ROW_STOP = re.compile(r"balance|closing|branch|total", re.I)
_ZERO_AFTER = re.compile(r"\s*[:=]?\s*0[.,]00(?!\d)")
_OTHER_BALANCE_BEFORE = re.compile(r"(?:closing|current|available|ledger|present|total)\s*balance\W{0,5}$"
                                   r"|total\W{0,5}$", re.I)


def opening_balance(text: str) -> Tuple[Optional[int], bool]:
    """The balance a statement starts from, and whether the statement labels it.

    Bangladeshi banks label it differently — Opening Balance, Previous Balance, B/F,
    Brought Forward — and some print no label at all, in which case the balance on the
    first transaction row is the opening figure (unlabelled: a guess, never judged).

    The labelled value is read from the label's own row, as these statements lay it out:
      - a plausible balance (1 lakh or more) printed just before the label, with at most a date
        between ("Debit Credit Balance 2,740,217.50 Opening Balance", "24,93,857.00 15-09-2026
        OPENING BALANCE") — not one labelled as another balance or a total;
      - else the first balance of 1 lakh or more after it, skipping a value date and an "as on"
        date, up to the next date or label ("OPENING BALANCE 0.00 0.00 2,004,865.00 CR");
      - else exactly 0.00 and nothing else (a new account).
    A debit or credit beside the label (under 1 lakh), a year, a date or an account number is
    never taken for it; a label whose row holds none of these is not read."""
    text = text or ""
    ids = [m.end() for m in _id_label_re().finditer(text)]
    figures = [(p, v) for p, v in plausible_money(text) if not _after_id(text, ids, p)]
    for m in _label_re(R.OPENING_BALANCE_WORDS).finditer(text):
        before = [(p, v) for p, v in figures if m.start() - 40 <= p < m.start() and v >= _BALANCE_FLOOR]
        if before:
            p, v = before[-1]
            gap = _ROW_DATE_AT.sub("", re.sub(r"^[\d,.]+", "", text[p:m.start()]), count=1)
            if re.fullmatch(r"[\s|=\-:CRcr]*", gap) and not _OTHER_BALANCE_BEFORE.search(text[max(0, p - 30):p]):
                return int(v), True
        start = m.end()
        lead = re.match(r"\s*(?:as\s*(?:on|at)|on|dated?)?\W{0,3}", text[start:start + 16])
        first = _ROW_DATE_AT.match(text, start + lead.end())
        if first:
            start = first.end()
        end = start + 60
        for stop in (_ROW_DATE_AT.search(text, start, end), _ROW_STOP.search(text, start, end)):
            if stop:
                end = min(end, stop.start())
        row = [v for p, v in figures if start <= p < end and v >= _BALANCE_FLOOR]
        if row:
            return int(row[0]), True
        if _ZERO_AFTER.match(text, m.end()) and not [v for p, v in figures if m.end() <= p < end]:
            return 0, True
    # no label: take the balance on the first transaction row
    for line in (text or "").splitlines():
        if not re.search(r"\d{1,2}[./-]\w{2,9}[./-]\d{2,4}", line):
            continue                      # not a transaction row
        figures = [money_value(x) for x in re.findall(r"[\d,]{4,15}(?:\.\d{1,2})?", line)]
        figures = [f for f in figures if f is not None and f >= 100]
        if figures:
            return int(figures[-1]), False  # the running balance is the last figure on the row
    return None, False


def opening_balance_check(statement: str, program: str) -> List[Tuple[str, str]]:
    """The reviewer's opening-balance rule. Only a LABELLED opening balance is judged, and
    only ever as FLAG: an unlabelled first-row figure is a guess."""
    low, high = R.MIN_OPENING_BALANCE.get(program, 0), R.PREFERRED_OPENING_MAX.get(program)
    value, labelled = opening_balance(statement)
    if value is None or not labelled:
        seen = f" (the first transaction row shows {value:,} BDT)" if value is not None else ""
        return [(NOTE, "no line is labelled as the opening balance" + seen + " — check by eye that the "
                       f"statement opens at {low:,} BDT or more")]
    if low and value < low:
        return [(FLAG, f"the statement opens at {value:,} BDT — below the {low:,} BDT opening balance "
                       f"expected for {program}")]
    if high and value > high:
        return [(FLAG, f"the statement opens at {value:,} BDT — above the preferred maximum of "
                       f"{high:,} BDT for {program}")]
    return [(PASS, f"opening balance {value:,} BDT is within the expected range")]


_DATE_TEXT = r"\d{1,2}[\s./-]{1,2}\w{1,9}[\s./-]{1,2}\d{2,4}|\d{4}[./-]\d{1,2}[./-]\d{1,2}"
_PERIOD_RE = re.compile(r"(?:statement\s*period|period|from(?:\s*date)?)\W{0,12}(" + _DATE_TEXT + r")"
                        r"\s*(?:to|till|until|-|–)\s*(?:date\W{0,4})?(" + _DATE_TEXT + r")", re.I)
_ROW_DATE_RE = re.compile(r"\s*(" + _DATE_TEXT + r")")
_MIN_DATED_ROWS = 5


def statement_period(pages: List[str]) -> Optional[Tuple[dt.date, dt.date]]:
    """The first and last day a statement covers: its "Statement Period … to …" / "From …
    To …" line, or else the earliest and latest of at least 5 dated transaction rows."""
    for page in pages:
        m = _PERIOD_RE.search(page or "")
        if m:
            a, b = find_dates(m.group(1)), find_dates(m.group(2))
            if a and b and a[0] <= b[0]:
                return a[0], b[0]
    rows = []
    for page in pages:
        for line in (page or "").splitlines():
            m = _ROW_DATE_RE.match(line)
            found = find_dates(m.group(1)) if m else []
            if found and TODAY - dt.timedelta(days=5 * 365) <= found[0] <= TODAY + dt.timedelta(days=31):
                rows.append(found[0])
    if len(rows) >= _MIN_DATED_ROWS:
        return min(rows), max(rows)
    return None


def statement_period_check(pages: List[str]) -> List[Tuple[str, str]]:
    period = statement_period(pages)
    if not period:
        return [(NOTE, "the statement period could not be read — check it covers the last "
                       f"{R.BANK_MIN_STATEMENT_MONTHS} months")]
    start, end = period
    months = months_between(start, end + dt.timedelta(days=1))     # 1 Apr - 30 Sep is 6 months
    if months < R.BANK_MIN_STATEMENT_MONTHS:
        return [(FLAG, f"the statement covers {months} month(s) ({start} to {end}) — the guideline "
                       f"asks for the last {R.BANK_MIN_STATEMENT_MONTHS} months "
                       f"({R.BANK_RELAXED_STATEMENT_MONTHS} months is accepted only for excellent "
                       "accredited universities)")]
    return [(PASS, f"the statement covers {months} months ({start} to {end})")]


# --- the bank balance (B3) ----------------------------------------------------------
# Money on a bank file is plausible only when written like money AND sized like money: 10,000
# to 10 crore taka, in a real digit grouping (western 3-3-3 or lakh 2-2-3; OCR may print the
# separators as dots). A balance is at least 1 lakh: "Balance 0", "SL 124" and column headers are
# not balances. Certificates print the taka amount with its USD equivalent at their own exchange
# rate (BDT 115-130 per USD in this corpus); a statement never does.
_PLAUSIBLE_MONEY = (10_000, 100_000_000)
_BALANCE_FLOOR = 100_000
_USD_RATE = (115, 130)
_USD_WINDOW = 260                       # the USD figure follows the amount and its amount in words
_CERT_WORDS = re.compile(r"solven|to\s*whom|whom\s*it|financially\s*sound|this\s*is\s*to\s*certify"
                         r"|balance\s*confirmation|balance\s*certificate", re.I)
_CERT_LABELS = ["balance", "amount", "total", "sum of", "bdt", "tk"]     # figure within 120 chars
_CERT_TAKA_LABELS = ["taka"]            # "(Taka Twenty Lac ...)" are words: the figure must follow at once
_CLOSING_LABELS = ["closing balance", "current balance", "available balance", "ledger balance",
                   "present balance", "balance as on", "balance as at"]
_BANK_MONEY_RE = re.compile(
    _NOT_BEFORE + r"(?:(?P<sep>\d{1,3}(?:,\d{2,3})+(?:[.,]\d{1,2})?|\d{1,3}(?:[.,]\d{2,3}){2,}[.,]\d{2})"
    + r"|(?P<dec>\d+\.\d{1,2}))" + _NOT_AFTER
    + r"|(?:(?<![a-z])(?:tk|bdt|taka)\.?|৳)[ \t]*[:=\-]?[ \t]*(?P<pre>\d{1,8})" + _NOT_AFTER
    + r"|" + _NOT_BEFORE + r"(?P<post>\d{1,8})[ \t]*(?:/-[ \t]*)?(?:tk|bdt|taka)(?![a-z])", re.I)
_USD_BEFORE = re.compile(r"(?:usd|dollar|\$|u\.s\.)[^a-z0-9]{0,4}$", re.I)
_USD_MARK = re.compile(r"us[d0o]|dollar|\$", re.I)
_ANY_NUMBER = re.compile(r"(?<![\d,.])(\d{1,3}(?:[.,]\d{3})+(?:[.,]\d{1,4})?|\d{3,6}(?:[.,]\d{1,4})?)(?!\d)")


def grouped_value(raw: str) -> Optional[float]:
    """'25,73,067.50', '2,573,067.50', OCR '25.73.067.50' or '30.07,200.00' -> the value; None when
    the digit groups are neither western (3-3-3) nor lakh (2-2-3). A last group of 1-2 digits is the
    paisa, whichever mark OCR printed before it."""
    toks = re.split(r"[.,]", (raw or "").strip())
    if not all(t.isdigit() for t in toks):
        return None
    dec = ""
    if len(toks) > 1 and len(toks[-1]) <= 2:
        dec, toks = toks[-1], toks[:-1]
    if len(toks) > 1:
        first, rest = toks[0], toks[1:]
        western = all(len(t) == 3 for t in rest)
        lakh = all(len(t) == 2 for t in rest[:-1]) and len(rest[-1]) == 3
        if not 1 <= len(first) <= 3 or not (western or lakh):
            return None
    return float("".join(toks) + ("." + dec if dec else ""))


def plausible_money(text: str) -> List[Tuple[int, float]]:
    """(position, value) of every figure on a bank file that can be a taka amount: written like
    money, a real digit grouping, 10,000 to 10 crore, not marked as USD, and not right after an ID
    label -- unless it carries thousands separators (an account number never does, and certificate
    tables print the amount straight after the account number)."""
    text = text or ""
    ids = [m.end() for m in _id_label_re().finditer(text)]
    out: List[Tuple[int, float]] = []
    for m in _BANK_MONEY_RE.finditer(text):
        group = next(g for g in ("sep", "dec", "pre", "post") if m.group(g) is not None)
        pos = m.start(group)
        if group != "sep" and _after_id(text, ids, pos):
            continue
        if _USD_BEFORE.search(text[max(0, m.start() - 14):m.start()]):
            continue
        v = grouped_value(m.group(group))
        if v is not None and _PLAUSIBLE_MONEY[0] <= v <= _PLAUSIBLE_MONEY[1]:
            out.append((pos, v))
    return out


def _labelled_balances(page: str, labels: List[str], window: int) -> List[Tuple[int, float]]:
    """The first plausible figure within `window` characters after each label (not an opening-
    balance label), when it is at least 1 lakh and not the USD equivalent of another figure."""
    figures = plausible_money(page)
    values = [v for _, v in figures]
    opening = _opening_spans(page)
    out: Dict[int, float] = {}
    for m in _label_re(labels).finditer(page or ""):
        if any(a < m.end() and m.start() < b for a, b in opening):
            continue
        for pos, v in figures:
            if pos >= m.end():
                if pos - m.end() <= window:
                    out[pos] = v
                break
    return [(p, v) for p, v in sorted(out.items())
            if v >= _BALANCE_FLOOR and not any(80 <= w / v <= 140 for w in values)]


def usd_confirms(page: str, pos: int, value: float, marked: bool = False) -> bool:
    """Is the taka figure at `pos` followed (within _USD_WINDOW characters) by a figure it is
    _USD_RATE times -- its own USD equivalent, so the taka figure was read whole? `marked`: a
    USD / dollar / $ mark must also sit within 200 characters before the figure or between."""
    page = page or ""
    for m in _ANY_NUMBER.finditer(page[pos:pos + _USD_WINDOW]):
        if marked and not _USD_MARK.search(page[max(0, pos - 200):pos + m.start()]):
            continue
        w = grouped_value(m.group(1))
        if w and _USD_RATE[0] <= value / w <= _USD_RATE[1]:
            return True
    return False


def _one_amount(values: List[float]) -> bool:
    """One amount (repeated or OCR'd twice within 1%), or the largest is the total of the rest."""
    top = max(values)
    others = sorted({round(v) for v in values if v < top * 0.99})
    return not others or abs(sum(others) - top) <= top * 0.01


def balance_check(text: str, program: str, pages: Optional[List[str]] = None) -> List[Tuple[str, str]]:
    """The minimum balance, read in this order:
      1. the amount the solvency certificate states: on pages with certificate wording ("solvency",
         "to whom it may concern", "this is to certify", ...), the figure after a balance / amount /
         total / BDT / Tk label (straight after "Taka"); with no such page, the figure after a
         balance label on any page that is not a statement;
      2. a taka figure the file converts to USD (only certificates do);
      3. the statement's labelled closing / current / available / ledger / present balance.
    Only these can PASS. Anything else is a FLAG showing the largest plausible amount. A FAIL needs
    clear evidence: the certificate's own stated amount, below the minimum, one amount (or the total
    of the others), confirmed by its USD equivalent or printed twice, and no plausible figure at or
    above the minimum anywhere in the file; every other low reading is a FLAG."""
    need = R.MIN_BANK_TAKA[program]
    pages = [p for p in (pages or []) if p] or [text or ""]
    every = [v for p in pages for _, v in plausible_money(p)]
    top = int(max(every)) if every else None
    cert = [j for j, p in enumerate(pages) if _CERT_WORDS.search(p)]
    pure = [j for j in cert if not looks_like_statement(pages[j])]
    if cert:
        stated = [(j, p, v) for j in (pure or cert)
                  for p, v in sorted(set(_labelled_balances(pages[j], _CERT_LABELS, 120))
                                     | set(_labelled_balances(pages[j], _CERT_TAKA_LABELS, 25)))]
    else:
        stated = [(j, p, v) for j, pg in enumerate(pages) if not looks_like_statement(pg)
                  for p, v in _labelled_balances(pg, R.BALANCE_LABELS, 60)]
    from_cert = bool(stated)
    if not stated:
        stated = [(j, p, v) for j, pg in enumerate(pages)
                  if not looks_like_statement(pg) or _CERT_WORDS.search(pg)
                  for p, v in plausible_money(pg) if v >= _BALANCE_FLOOR and usd_confirms(pg, p, v, marked=True)]
        if not stated and text and pages != [text]:
            pages = pages + [text]          # the whole-file read may hold a figure a page read garbled
            stated = [(len(pages) - 1, p, v) for p, v in plausible_money(text)
                      if v >= _BALANCE_FLOOR and usd_confirms(text, p, v, marked=True)]
    if stated:
        j, pos, s = max(stated, key=lambda x: x[2])
        s = int(s)
        if s >= need:
            return [(PASS, f"balance {s:,} BDT meets the {need:,} BDT minimum")]
        if top is not None and top >= need:
            return [(FLAG, f"the balance reads {s:,} BDT — below the {need:,} BDT minimum for {program} — but "
                           f"{top:,} BDT also appears in the file; check which is the balance")]
        values = [v for _, _, v in stated]
        confirmed = usd_confirms(pages[j], pos, s) or [round(v) for v in values].count(s) >= 2
        if from_cert and j in pure and _one_amount(values) and confirmed:
            return [(FAIL, f"the balance is {s:,} BDT — below the {need:,} BDT minimum for {program}")]
        return [(FLAG, f"the balance reads {s:,} BDT — below the {need:,} BDT minimum for {program} — but it "
                       "could not be confirmed on the solvency certificate; check by eye")]
    close = [(p, v) for pg in pages for p, v in _labelled_balances(pg, _CLOSING_LABELS, 60)]
    if close:
        c = int(max(v for _, v in close))
        if c >= need:
            return [(PASS, f"balance {c:,} BDT meets the {need:,} BDT minimum")]
        more = f" — but {top:,} BDT also appears in the file" if top is not None and top >= need else ""
        return [(FLAG, f"the statement's closing balance reads {c:,} BDT — below the {need:,} BDT minimum for "
                       f"{program}{more}; no solvency amount could be read, check by eye")]
    if top is not None:
        return [(FLAG, f"no figure is labelled as the balance; the largest amount is {top:,} BDT — check the "
                       f"balance against the {need:,} BDT minimum for {program} by eye")]
    return [(FLAG, "no balance amount could be read")]


def sponsor_holder(student: Dict[str, str], text: str = "") -> Tuple[str, str]:
    """(sponsor kind, whose name the bank account should be in), from the portal's Sponsor.
    Both parents: whichever parent the document names, the father when it names neither.
    Self: the applicant. Anyone else: "" (the caller decides)."""
    kind = sponsor_kind(student.get("Sponsor") or "")
    father, mother = student.get("Father") or "", student.get("Mother") or ""
    if kind == "FATHER":
        return kind, father
    if kind == "MOTHER":
        return kind, mother
    if kind == "PARENTS":
        if mother and not name_in(text, father) and name_in(text, mother):
            return kind, mother
        return kind, father or mother
    if kind == "SELF":
        return kind, student.get("Full Name") or ""
    return kind, ""


STATEMENT_DATE_LABELS = (
    r"generation\s*date(?:\s*/?\s*time)?", r"print(?:ed)?\s*date(?:\s*&?\s*time)?",
    r"statement\s*date", r"date\s*of\s*issue", r"issue\s*date", r"report\s*date",
    r"run\s*date", r"as\s*on", r"printed\s*on", r"statement\s*printed(?:\s*on)?",
)


def statement_date(pages: List[str]) -> Optional[dt.date]:
    """When the statement was generated — the label the bank prints at the top.

    A statement is full of transaction dates; the only one that means "this document was
    produced on" sits beside Generation Date / Print Date / as on.  Taking any other date
    compares the statement's period against the solvency certificate and reports a
    difference that is not there."""
    for page in pages:
        for label in STATEMENT_DATE_LABELS:
            m = re.search(label + r"\W{0,12}((?:\d{1,2}[\s./-]{1,2}\w{1,9}[\s./-]{1,2}\d{2,4})"
                                  r"|(?:\d{4}[./-]\d{1,2}[./-]\d{1,2}))", page or "", re.I)
            if m:
                found = find_dates(m.group(1))
                if found:
                    return found[0]
    for page in pages:
        m = re.search(r"(?:from\s*date|statement\s*period|period)\W{0,6}"
                      r"(?:\d{1,2}[\s./-]{1,2}\w{1,9}[\s./-]{1,2}\d{2,4})\s*(?:to|-|–)\s*"
                      r"((?:\d{1,2}[\s./-]{1,2}\w{1,9}[\s./-]{1,2}\d{2,4}))", page or "", re.I)
        if m:
            found = find_dates(m.group(1))
            if found:
                return found[0]          # the period end is the as-on date
    return None


def check_bank(text: str, student, sizes, program: str, path=None, others=None) -> List[Tuple[str, str]]:
    # The file always has the solvency certificate on page 1 and the statement from
    # page 2 onwards (or the other way round), so the balance and the two dates are read
    # from their own pages rather than guessed out of one merged block of text.
    pages: List[str] = []
    page_error = None
    if path is not None:
        try:
            pages = read_pages(path, max_pages=10)
        except CODE_FAULTS:
            raise
        except Exception as e:
            page_error = (ERROR, f"could not read the bank file page by page ({_no_path(e, path)})")
    out = balance_check(text, program, pages)
    if page_error:
        out.append(page_error)
    # The USD total belongs on the solvency certificate; a statement never carries one.
    if looks_like_solvency(text):
        out.append((PASS, "solvency amount stated in USD") if has_any(text, R.USD_WORDS)
                   else (FLAG, "no USD equivalent found — the solvency certificate must state "
                               "the total in USD"))
    # Any seal and signature will do; only their complete absence is worth reporting.
    has_seal = has_any(text, R.SEAL_WORDS) or (PC.seal_present(path) if path else False)
    out.append((PASS, "carries a seal and signature") if has_seal
               else (FLAG, "no seal or signature of any kind found on the document"))
    opened = None
    m = re.search(r"(open(?:ing)?\s*date|a/c open|account opening date)\D{0,20}"
                  r"(\d{1,2}[./-]\w{3,9}[./-]\d{2,4}|\d{4}-\d{2}-\d{2}|\d{1,2}[./-]\d{1,2}[./-]\d{4})",
                  text, re.I)
    if m:
        d = find_dates(m.group(2))
        opened = d[0] if d else None
    if opened:
        age = months_between(opened, TODAY)
        if age >= R.BANK_MIN_ACCOUNT_MONTHS:
            out.append((PASS, f"account opened {opened} ({age} months old)"))
        else:
            out.append((FLAG, f"account opened {opened} — only {age} month(s) old; guideline prefers "
                              "6 months or more (embassies query new accounts)"))
    ctx = others if isinstance(others, dict) else {}
    sponsor = (student.get("Sponsor") or "").upper()
    _, who = sponsor_holder(student, text)
    holder = who or student.get("Full Name", "")

    if len(pages) >= 2:
        sol = document_date(pages[0])
        stm = statement_date(pages[1:])
        # FLAG, not FAIL: this rule produced false failures (HANDOFF §5.1) and its rewritten
        # date logic has not yet been proven against real statements (HANDOFF §8.1).
        if sol and stm:
            out.append((PASS, f"solvency certificate and statement both dated {sol}") if sol == stm
                       else (FLAG, f"the solvency certificate is dated {sol} but the statement was "
                                   f"generated on {stm} — they must carry the SAME date"))
        elif sol and not stm:
            out.append((NOTE, f"solvency certificate dated {sol}; the statement's generation "
                              "date could not be read from the scan — compare them by eye"))
        elif stm and not sol:
            out.append((NOTE, f"statement generated {stm}; no date could be read from the "
                              "solvency certificate — compare them by eye"))
        else:
            out.append((NOTE, "neither date could be read — check the solvency certificate and "
                              "the statement carry the same date"))
    elif pages:
        out.append((FLAG, "the bank file has only one page, so it cannot hold both a solvency "
                          "certificate and a statement"))
    # The statement's own rules (opening balance, the months it covers) are read from the
    # statement pages; a file read as one block of text is all there is to go on.
    statement = pages[1:] if len(pages) >= 2 else [text]
    out += opening_balance_check("\n".join(statement), program)
    out += statement_period_check(statement)

    if holder:
        ctx["_account_holder"] = holder
        out.append((PASS, f"account appears to be in the name of {holder}")
                   if name_in(text, holder)
                   else (FLAG, f"expected account holder '{holder}' (sponsor: {sponsor or 'unknown'}) "
                               "not found in the document"))
    return out


def current_fiscal_year() -> str:
    """Bangladeshi fiscal year runs July-June, e.g. 2026-2027 from July 2026."""
    y = TODAY.year if TODAY.month >= 7 else TODAY.year - 1
    return f"{y}-{y + 1}"


_FY_RE = re.compile(r"(?<!\d)(20\d{2})\s*[-–/]\s*(20\d{2}|\d{2})(?![\d]|[-–/.]\d)"
                    r"|(?<!\d)(20\d{2})\s*(?:a\.?\s?d\b\.?\s*)?(?:t[o0]|till|until)\s*"
                    r"(?:\d{1,2}\w{0,2}\s+\w+,?\s+)?(20\d{2})(?!\d)",
                    re.I)                       # "1st July 2025 AD to 30th June 2026", OCR "t0"
_FY_NEAR = 40
# "Fiscal Year 2026 2027": OCR dropped the dash. Two bare years count only beside a label.
_FY_BARE = re.compile(r"(?<![\d/.-])(20\d{2})\s+(20\d{2})(?![\d]|[-–/.]\d)")
_FY_BEFORE = 15     # "for 2026-2027 financial year": the year just before its label


def fiscal_years(text: str) -> List[Tuple[int, int, str]]:
    """(start, end, "YYYY-YYYY") of every fiscal year written on the page: "2026-2027",
    "2026-27", "2026/27", "1st July 2026 to 30th June 2027". Two years that are not
    consecutive are no fiscal year."""
    out = []
    for m in _FY_RE.finditer(text or ""):
        a, b = (m.group(1), m.group(2)) if m.group(1) else (m.group(3), m.group(4))
        first = int(a)
        second = int(b) if len(b) == 4 else first // 100 * 100 + int(b)
        if second == first + 1:
            out.append((m.start(), m.end(), f"{first}-{second}"))
    return out


def fiscal_year_check(text: str) -> List[Tuple[str, str]]:
    """A trade licence must be renewed for the current fiscal year (July-June). The year
    beside a Fiscal Year / Renewal / Validity label decides; with none labelled, the
    current year written anywhere is enough; any other year alone is only a FLAG (it may
    be a footer, a previous renewal or the form's print year). A year counts as labelled within
    _FY_NEAR characters after its label or _FY_BEFORE characters before it ("for 2026-2027
    financial year"); two bare years ("Fiscal Year 2026 2027") count only when labelled."""
    want = current_fiscal_year()
    years = fiscal_years(text)
    labels = [(m.start(), m.end()) for m in _label_re(R.FISCAL_LABELS).finditer(text or "")]

    def beside(start: int, end: int) -> bool:
        return any(0 <= start - le <= _FY_NEAR or 0 <= ls - end <= _FY_BEFORE for ls, le in labels)

    years = sorted(years + [(m.start(), m.end(), f"{m.group(1)}-{m.group(2)}")
                            for m in _FY_BARE.finditer(text or "")
                            if int(m.group(2)) == int(m.group(1)) + 1 and beside(m.start(), m.end())])
    labelled = [fy for start, end, fy in years if beside(start, end)]
    if labelled:
        return [(PASS, f"licence fiscal year {want} is the current {want}") if want in labelled
                else (FAIL, f"licence fiscal year {labelled[0]} is not the current fiscal year ({want})")]
    if any(fy == want for _, _, fy in years):
        return [(PASS, f"the current fiscal year {want} is on the licence")]
    if years:
        return [(FLAG, f"the licence shows {years[0][2]}, but not beside a Fiscal Year / Renewal / Validity "
                       f"label — check it is renewed for {want}")]
    return [(FLAG, "no fiscal year found on the trade licence")]


def taxpayer_name(text: str) -> str:
    """The person a TIN certificate is issued to.

    Bangladeshi TIN certificates carry no "Taxpayer's Name" field.  The name appears in a
    sentence: "This is to Certify that MST. SAMPLE MOTHER is a Registered Taxpayer of
    National Board of Revenue ...".  Some also print it beside a label, so both are tried.
    """
    patterns = (
        r"certify\s+that\s+([A-Za-z.\s]{4,60}?)\s+is\s+a\s+registered\s+taxpayer",
        r"taxpayer'?s?\s*name\s*[:\-]?\s*([A-Za-z.\s]{4,60})",
        r"name\s+of\s+(?:the\s+)?taxpayer\s*[:\-]?\s*([A-Za-z.\s]{4,60})",
    )
    for pat in patterns:
        m = re.search(pat, text or "", re.I)
        if m:
            return re.sub(r"\s+", " ", m.group(1)).strip(" .")
    return ""


def proprietor_name(text: str) -> str:
    """The person a trade licence is issued to — "Name of Proprietor / Assesse"."""
    patterns = (
        r"name\s+of\s+proprietor\s*/?\s*(?:assesse|assessee)?\s*[:\-]?\s*([A-Za-z.\s]{4,60}?)"
        r"\s+name\s+of",
        r"name\s+of\s+proprietor\s*/?\s*(?:assesse|assessee)?\s*[:\-]?\s*([A-Za-z.\s]{4,60})",
        r"proprietor\s*[:\-]\s*([A-Za-z.\s]{4,60})",
    )
    for pat in patterns:
        m = re.search(pat, text or "", re.I)
        if m:
            return re.sub(r"\s+", " ", m.group(1)).strip(" .")
    return ""


def is_tin(text: str) -> bool:
    t = norm(text)
    return "taxpayers identification" in t or "tin certificate" in t or "etin" in t


def check_financial(text: str, student, sizes, path=None, others=None) -> List[Tuple[str, str]]:
    out = []
    t = norm(text)
    ctx = others if isinstance(others, dict) else {}
    sponsor = (student.get("Sponsor") or "").upper()
    kind, holder = sponsor_holder(student, text)
    if kind == "OTHER":            # both parents, or the applicant themself, are fine
        out.append((FLAG, f"portal sponsor is '{sponsor}' — only the applicant's parents may sponsor"))
    # It must be in the name of whoever holds the bank account.
    holder = ctx.get("_account_holder") or holder
    if holder and not is_tin(text):
        out.append((PASS, f"issued in the bank account holder's name ({holder})")
                   if name_in(text, holder)
                   else (FLAG, f"bank account holder '{holder}' not found — the Trade Licence/TIN "
                               "should be in the same name as the bank account"))
    kinds = [k for k, w in (("trade licence", "trade licen"), ("TIN certificate", "taxpayer's identification"),
                            ("TIN certificate", "tin certificate"),
                            ("employment certificate", "employment"), ("salary", "salary"))
             if w in t]
    out.append((PASS, "contains: " + ", ".join(sorted(set(kinds)))) if kinds
               else (FLAG, "could not tell which financial document this is"))
    if "trade licen" in t:
        out += fiscal_year_check(text)
        m = re.search(r"business start(?:ing)? date\D{0,20}([\d./-]{6,12}|\d{1,2}\s+\w+\s+\d{4})", text, re.I)
        if m:
            d = find_dates(m.group(1))
            if d:
                yrs = (TODAY - d[0]).days / 365.25
                out.append((PASS, f"business started {d[0]} ({yrs:.1f} years ago)") if yrs >= 2
                           else (FLAG, f"business started {d[0]} — under 2 years; older businesses are "
                                       "viewed more positively"))
    if "trade licen" in t:
        out.append((PASS, "trade licence is notarised (wording or seal found)") if notarised(text, path)
                   else (FAIL, "trade licence is not notarised — notarisation is required for it"))
        ctx["_trade_text"] = (ctx.get("_trade_text") or "") + " " + t
    if is_tin(text):
        ctx["_tin_name"] = taxpayer_name(text) or ctx.get("_tin_name", "")

    # One person must hold the bank account, the TIN and the trade licence.  Matching the
    # known account-holder name against each document is far more reliable than trying to
    # lift a name out of two different layouts, so that is the primary test; where both
    # names can be read they are compared to each other as well.
    tin_name = taxpayer_name(text) or ctx.get("_tin_name", "")
    if tin_name:
        ctx["_tin_name"] = tin_name
    prop_name = proprietor_name(text) or ctx.get("_proprietor_name", "")
    if prop_name:
        ctx["_proprietor_name"] = prop_name

    if holder and not ctx.get("_financial_name_done"):
        ctx["_financial_name_done"] = True
        if name_in(text, holder):
            out.append((PASS, f"the bank account holder ({holder}) is named on this document"))
        else:
            named = tin_name or prop_name
            out.append((FLAG, f"the bank account holder is '{holder}' but this document is in "
                              + (f"the name of '{named}'" if named else "another name")
                              + " — account, TIN and trade licence should be one person"))

    if tin_name and prop_name and not ctx.get("_tin_vs_trade_done"):
        ctx["_tin_vs_trade_done"] = True
        a, b = set(name_tokens(tin_name)), set(name_tokens(prop_name))
        out.append((PASS, f"TIN and trade licence are both in the name of {tin_name}") if a & b
                   else (FLAG, f"the TIN names '{tin_name}' but the trade licence names "
                               f"'{prop_name}' — they should be the same person"))
    return out


def check_income_tax(text: str, student, sizes, program: str, path=None) -> List[Tuple[str, str]]:
    out = []
    amounts = find_money_taka(text)
    if program == "MASTER":
        wealth = [a for a in amounts if a >= R.MIN_NET_WEALTH_TAKA]
        out.append((PASS, f"net wealth figure {wealth[0]:,} BDT meets the 50 lakh guideline") if wealth
                   else (FLAG, "no net wealth of 50 lakh BDT or more found (Master's guideline)"))
    # The amount the paper says was paid, of any size: "Tax paid 8,500" is below the
    # guideline, not "no amount found".
    paid = labelled_amounts(text, R.TAX_PAID_LABELS, plain=True)
    need = R.MIN_TAX_PAID_TAKA
    if not paid:
        out.append((FLAG, f"no amount labelled as tax paid could be read — at least {need:,} BDT is "
                          "expected"))
    elif max(paid) >= need:
        out.append((PASS, f"tax paid {int(max(paid)):,} BDT (at least {need:,} BDT is expected)"))
    else:
        out.append((FLAG, f"tax paid {int(max(paid)):,} BDT — below the {need:,} BDT expected"))
    out.append((PASS, "notarised (wording or seal found)") if notarised(text, path)
               else (FLAG, "income tax papers should be notarised through an advocate"))
    return out


# `p` is the documents already read for this student, so a check can compare against them
# (a birth certificate against the passport, a trade licence against the bank account).
CHECKS = {
    "passport": lambda t, s, z, p, prog, path: check_passport(t, s, z, path),
    "photo": lambda t, s, z, p, prog, path: check_photo(t, s, z, path),
    "birth_cert": lambda t, s, z, p, prog, path: check_birth_cert(t, s, z, path, p),
    "father_nid": lambda t, s, z, p, prog, path: check_nid(t, s, z, "father", path, p),
    "mother_nid": lambda t, s, z, p, prog, path: check_nid(t, s, z, "mother", path, p),
    "student_nid": lambda t, s, z, p, prog, path: check_nid(t, s, z, "student", path, p),
    "family_cert": lambda t, s, z, p, prog, path: check_family_cert(t, s, z, path),
    "academic": lambda t, s, z, p, prog, path: check_academic(t, s, z, path, p),
    "bank": lambda t, s, z, p, prog, path: check_bank(t, s, z, prog, path, p),
    "trade_license": lambda t, s, z, p, prog, path: check_financial(t, s, z, path, p),
    "income_tax": lambda t, s, z, p, prog, path: check_income_tax(t, s, z, prog, path),
}



# --- cross-document consistency (the reviewer's first rule) --------------------------
def readable(text: str) -> bool:
    """Whether OCR produced usable words. Bangladeshi passport data pages and heavy
    stamps often come out as noise, and a name must not be judged 'missing' on those."""
    words = re.findall(r"[A-Za-z]{3,}", text or "")
    if len(words) < 25:
        return False
    vowelly = sum(1 for w in words if re.search(r"[aeiou]", w, re.I))
    return vowelly / len(words) > 0.6


def cross_checks(texts: Dict[str, str], student: Dict[str, str]) -> List[Dict[str, Any]]:
    texts = {k: v for k, v in texts.items() if not k.startswith("_") and isinstance(v, str)}
    """Names, DOB and numbers must agree across the identity documents; if they do not,
    an affidavit IN THE APPLICANT'S NAME is required."""
    rows: List[Dict[str, Any]] = []
    people = {"applicant": student.get("Full Name", ""), "father": student.get("Father", ""),
              "mother": student.get("Mother", "")}
    mismatches = []
    for who, name in people.items():
        if not name:
            continue
        missing, unreadable = [], []
        for key in R.IDENTITY_DOCS:
            if key not in texts:
                continue
            if who == "father" and key == "mother_nid":
                continue
            if who == "mother" and key == "father_nid":
                continue
            if who == "applicant" and key in ("father_nid", "mother_nid"):
                continue        # a parent's NID does not carry the applicant's name
            if not readable(texts[key]):
                unreadable.append(R.TITLES.get(key, key))
                continue
            if name_in(texts[key], name) is False:
                missing.append(R.TITLES.get(key, key))
        note = f" (too blurred to read: {', '.join(unreadable)})" if unreadable else ""
        if len(missing) >= 2:       # one document alone is usually an OCR failure
            mismatches.append(f"{who} '{name}' not matched on: {', '.join(missing)}")
            rows.append({"doc": "CROSS-CHECK name", "file": "", "key": CROSS_CHECK, "verdict": FLAG,
                         "detail": f"FLAG: {who}'s name '{name}' could not be matched on "
                                   f"{len(missing)} documents: {', '.join(missing)} — check whether the "
                                   f"spelling really differs{note}"})
        elif missing:
            rows.append({"doc": "CROSS-CHECK name", "file": "", "key": CROSS_CHECK, "verdict": PASS,
                         "detail": f"PASS: {who}'s name '{name}' consistent (not readable on "
                                   f"{missing[0]} alone){note}"})
        else:
            rows.append({"doc": "CROSS-CHECK name", "file": "", "key": CROSS_CHECK, "verdict": PASS,
                         "detail": f"PASS: {who}'s name '{name}' consistent across the identity documents{note}"})

    dob = student.get("DOB", "")
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", dob or ""):
        d = dt.date.fromisoformat(dob)
        bad = [R.TITLES.get(k, k) for k in ("passport", "student_nid", "birth_cert", "family_cert")
               if k in texts and readable(texts[k]) and d not in find_dates(texts[k])]
        wrong = [k for k in bad if any(abs((x - d).days) < 4000 and x != d
                                       for x in find_dates(texts.get(k, "")))]
        rows.append({"doc": "CROSS-CHECK date of birth", "file": "", "key": CROSS_CHECK,
                     "verdict": FLAG if len(bad) >= 2 else PASS,
                     "detail": (f"FLAG: portal date of birth {d} could not be matched on: {', '.join(bad)}"
                                if len(bad) >= 2 else
                                f"PASS: date of birth {d} consistent" +
                                (f" (not readable on {bad[0]})" if bad else ""))})

    pno = (student.get("Passport No") or "").upper()
    if pno:
        seen, misread = [], []
        for k, t in texts.items():
            how, token = passport_number_on(t, pno)
            if how:
                seen.append(R.TITLES.get(k, k))
                if how == "ocr":
                    misread.append(f"{R.TITLES.get(k, k)}: {token}")
        note = f" (read with an OCR misread on {'; '.join(misread)})" if misread else ""
        rows.append({"doc": "CROSS-CHECK passport number", "file": "", "key": CROSS_CHECK,
                     "verdict": PASS if seen else FLAG,
                     "detail": (f"PASS: passport number {pno} appears on: {', '.join(seen)}{note}" if seen
                                else f"FLAG: passport number {pno} not found on any document")})

    if mismatches:
        aff = texts.get("affidavit", "")
        if not aff:
            rows.append({"doc": "CROSS-CHECK affidavit", "file": "", "key": CROSS_CHECK, "verdict": FLAG,
                         "detail": "FLAG: a name could not be matched across several documents and no "
                                   "affidavit is in the file — if the spellings really differ, an "
                                   "affidavit in the APPLICANT'S name is required (" +
                                   "; ".join(mismatches)[:200] + ")"})
        else:
            ok = name_in(aff, student.get("Full Name", ""))
            rows.append({"doc": "CROSS-CHECK affidavit", "file": "", "key": CROSS_CHECK,
                         "verdict": PASS if ok else FAIL,
                         "detail": ("PASS: affidavit is in the applicant's name and covers the name difference"
                                    if ok else
                                    "FAIL: affidavit does not appear to be in the applicant's name — an "
                                    "affidavit in the father's or mother's name is not accepted")})
    return rows


# --- per-student run ----------------------------------------------------------------
def classify(filename: str) -> Optional[str]:
    """Longest matching pattern wins, so "PASSPORT SIZE PHOTO.jpg" is a photo, not a
    passport, and "FATHER NID.pdf" is not mistaken for a student NID.

    The name is read as it is and with "_" as a space, so the portal's own key form
    ("family_cert_1234.pdf") and the ZIP's title form ("FAMILY CERT.pdf") both match."""
    names = {norm(filename), norm(filename.replace("_", " "))}
    best, best_len = None, 0
    for key, pats in R.FILE_PATTERNS.items():
        for p in pats:
            if len(p) > best_len and any(p in n for n in names):
                best, best_len = key, len(p)
    return best


def _row(title: str, file: str, key: str, findings: List[Tuple[str, str]]) -> Dict[str, Any]:
    """A document row: its verdict from the findings (common.row_verdict), and `has_error`
    whenever any finding is ERROR, even under a FAIL, so the row is checked again."""
    row = {"doc": title, "file": file, "key": key, "verdict": row_verdict(findings),
           "detail": " | ".join(f"{v}: {d}" for v, d in findings)}
    if has_error(findings):
        row["has_error"] = True
    return row


def _error_row(title: str, path: Path, key: str, msg: str) -> Dict[str, Any]:
    """The file could not be checked at all (locked, unrenderable, OCR broke): ERROR, never
    a verdict on the document, and retried on the next pass."""
    return {"doc": title, "file": path.name, "key": key, "verdict": ERROR, "has_error": True,
            "detail": f"ERROR: could not check this file ({_no_path(msg, path)}) — it is retried on the next pass"}


def _stand_ins(program: str, files: Dict[str, List[Path]]) -> Dict[str, str]:
    """alternative key -> the required key it stands in for, when that one is absent: a
    deceased parent's death certificate in place of their NID (rules.ALTERNATIVES)."""
    out: Dict[str, str] = {}
    for need, alts in R.ALTERNATIVES.items():
        if need in R.REQUIRED[program] and need not in files:
            for alt in alts:
                if alt in files:
                    out.setdefault(alt, need)
    return out


def _check_file(path: Path, key: str, title: str, student: Dict[str, str], program: str,
                texts: Dict[str, str], first: Tuple[str, str]) -> Dict[str, Any]:
    """Read one file and run its rules and page checks. A file that cannot be read or
    checked is an ERROR row; a fault in this code (CODE_FAULTS) stops the run."""
    try:
        text, sizes = read_document(path)
        if (text or "").lstrip().startswith("[unreadable:"):
            return _error_row(title, path, key, text.strip().strip("[]"))
        texts[key] = texts.get(key, "") + "\n" + text
        checker = CHECKS.get(key)
        results = checker(text, student, sizes, texts, program, path) if checker else [first]
        if key != "photo":          # the photo has its own image checks
            info = PC.inspect(path)
            results += PC.colour_check(info, key)
            results += PC.qr_check(info, required=key in R.QR_DOCS, text=text)
            results += PC.bangla_check(text)
    except CODE_FAULTS:
        raise
    except CheckError as e:
        return _error_row(title, path, key, str(e) or type(e).__name__)
    except Exception as e:
        return _error_row(title, path, key, f"{type(e).__name__}: {_no_path(e, path)}")
    return _row(title, path.name, key, results)


def verify_student(folder: Path, student: Dict[str, str], program: str) -> List[Dict[str, Any]]:
    files: Dict[str, List[Path]] = {}
    for f in sorted(folder.iterdir()):
        if f.name.startswith(".") or not f.is_file():
            continue
        key = classify(f.name)
        files.setdefault(key or "other", []).append(f)

    rows: List[Dict[str, Any]] = []
    texts: Dict[str, str] = {}
    stands_in = _stand_ins(program, files)
    for key in R.REQUIRED[program] + R.OPTIONAL:
        title = R.TITLES.get(key, key)
        if key not in files:
            if key in R.REQUIRED[program] and key not in stands_in.values():
                rows.append({"doc": title, "file": "", "key": key, "verdict": MISSING,
                             "detail": "required by the guideline but not uploaded"})
            continue
        for path in files[key]:
            if key in R.PRESENCE_ONLY:
                # never read: no text for the cross-checks, no page checks
                rows.append(_row(title, path.name, key, [(PASS, "present — recorded as present only "
                                                                "(not checked)")]))
                continue
            first = (PASS, "present (no automatic rule for this document)")
            if key in stands_in:
                need = stands_in[key]
                first = (PASS, f"present — accepted in place of the {need.split('_')[0]}'s NID "
                               f"({R.TITLES.get(need, need)} not needed)")
            rows.append(_check_file(path, key, title, student, program, texts, first))
    rows += cross_checks(texts, student)
    return rows


# --- finding students and writing the report ---------------------------------------
def student_folders(portal: Optional[Mapping[str, Mapping]] = None) -> Dict[str, Tuple[Path, str]]:
    """passport -> (folder, folder name) for every downloaded student.

    One passport can have two folders, when a student moved program and was downloaded
    again under the new one. With `portal` (passport -> portal row) the folder in the
    student's current program wins; otherwise, or when neither is, the folder downloaded
    last (its .download_complete marker, else the folder itself) wins."""
    found: Dict[str, List[Path]] = {}
    for root in DOCS_ROOTS:
        if not root.exists():
            continue
        for prog_dir in root.iterdir():
            if not prog_dir.is_dir():
                continue
            kids = [p for p in prog_dir.iterdir() if p.is_dir()] or []
            targets = kids if kids else []
            if not targets and re.search(r"\(([^)]+)\)$", prog_dir.name):
                targets = [prog_dir]
            for d in targets:
                m = re.match(r"(.*?)\s*\(([^)]+)\)$", d.name)
                if m:
                    found.setdefault(m.group(2).upper(), []).append(d)
    out: Dict[str, Tuple[Path, str]] = {}
    for pas, dirs in found.items():
        d = dirs[0] if len(dirs) == 1 else _pick_folder(dirs, (portal or {}).get(pas))
        out[pas] = (d, d.name)
    return out


def _downloaded_at(d: Path) -> float:
    for p in (d / ".download_complete", d):
        try:
            return p.stat().st_mtime
        except OSError:
            continue
    return 0.0


def _pick_folder(dirs: List[Path], student: Optional[Mapping]) -> Path:
    if student:
        from src.sheets import progress_builder as pb
        from src.sheets.verified_docs import _safe_path_part
        want = _safe_path_part(pb.PROGRAMS[program_of(dict(student))]["name"])
        mine = [d for d in dirs if d.parent.name == want]
        if mine:
            dirs = mine
    return max(dirs, key=_downloaded_at)


def portal_students(rows: Optional[Iterable[dict]] = None) -> Dict[str, Dict[str, str]]:
    """passport -> portal row. `rows` (an export already fetched, e.g. a saved snapshot)
    is used as it is; without it the portal export is fetched."""
    if rows is None:
        from src.sheets import progress_builder as pb
        rows = pb.fetch_all_students()
    out = {}
    for s in rows:
        if s.get("Passport No"):
            out[s["Passport No"].upper()] = s
    return out


def program_of(student: Dict[str, str]) -> str:
    from src.sheets import progress_builder as pb
    return pb.program_key_of(student) or "KLP"


def write_report(all_rows: List[Dict[str, Any]], path: Path) -> Path:
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill
    colours = {"PASS": "C6EFCE", "FLAG": "FFEB9C", "FAIL": "FFC7CE", "MISSING": "F2F2F2",
               "REVIEW": "FFEB9C", "INCOMPLETE": "F2F2F2", "ERROR": "D9D2E9"}
    path.parent.mkdir(parents=True, exist_ok=True)
    wb = Workbook()
    ws = wb.active
    ws.title = "Documents"
    ws.append(["Student", "Passport", "Program", "Student verdict", "Document", "File", "Verdict", "Details"])
    for c in ws[1]:
        c.font = Font(bold=True)
    for r in all_rows:
        ws.append([r["student"], r["passport"], r["program"], r["student_verdict"],
                   r["doc"], r["file"], r["verdict"], r["detail"]])
        ws.cell(row=ws.max_row, column=7).fill = PatternFill("solid", fgColor=colours.get(r["verdict"], "FFFFFF"))
        ws.cell(row=ws.max_row, column=4).fill = PatternFill("solid", fgColor=colours.get(r["student_verdict"], "FFFFFF"))
    ws.freeze_panes = "A2"
    for col, w in zip("ABCDEFGH", (28, 13, 12, 15, 38, 34, 10, 120)):
        ws.column_dimensions[col].width = w

    ws2 = wb.create_sheet("Summary")
    ws2.append(["Student", "Passport", "Program", "Verdict", "Missing", "Fail", "Flag", "Pass", "Error"])
    for c in ws2[1]:
        c.font = Font(bold=True)
    seen = {}
    for r in all_rows:
        k = r["passport"]
        s = seen.setdefault(k, {"student": r["student"], "program": r["program"],
                                "verdict": r["student_verdict"], "MISSING": 0, "FAIL": 0, "FLAG": 0, "PASS": 0,
                                "ERROR": 0})
        s[r["verdict"]] = s.get(r["verdict"], 0) + 1
    for k, s in seen.items():
        ws2.append([s["student"], k, s["program"], s["verdict"], s["MISSING"], s["FAIL"], s["FLAG"], s["PASS"],
                    s["ERROR"]])
        ws2.cell(row=ws2.max_row, column=4).fill = PatternFill("solid", fgColor=colours.get(s["verdict"], "FFFFFF"))
    ws2.freeze_panes = "A2"
    for col, w in zip("ABCDEFGHI", (28, 13, 12, 14, 10, 8, 8, 8, 8)):
        ws2.column_dimensions[col].width = w
    wb.save(path)
    return path


def run(passports: List[str], report_name: str = "") -> Tuple[Path, Dict[str, str]]:
    portal = portal_students()
    folders = student_folders(portal)
    all_rows, verdicts = [], {}
    stamp = dt.datetime.now().strftime("%Y-%m-%d_%H%M")
    out = REPORT_DIR / (report_name or f"document_check_{stamp}.xlsx")
    for i, pas in enumerate(passports, 1):
        folder = folders.get(pas)
        student = portal.get(pas)
        if not folder or not student:
            logger.warning("skipping %s (folder=%s, portal=%s)", pas, bool(folder), bool(student))
            continue
        program = program_of(student)
        rows = verify_student(folder[0], student, program)
        v = student_verdict(rows)
        verdicts[pas] = v
        name = student.get("Full Name", folder[1])
        print(f"[{i}/{len(passports)}] {name} ({pas}) — {v}")
        for r in rows:
            all_rows.append({**r, "student": name, "passport": pas, "program": program, "student_verdict": v})
        # save after every student, so a run that is interrupted still leaves a usable report
        try:
            write_report(all_rows, out)
        except Exception as e:
            logger.warning("could not write the report yet: %s", e)
    write_report(all_rows, out)
    return out, verdicts


def main() -> None:
    logging.basicConfig(level=logging.WARNING, format="%(asctime)s  %(message)s")
    ap = argparse.ArgumentParser(description="Verify downloaded student documents against the program guidelines.")
    ap.add_argument("--student", help="name (or part of it) of one student")
    ap.add_argument("--passport", help="passport number of one student")
    ap.add_argument("--all", action="store_true", help="every downloaded student")
    ap.add_argument("--program", help="only this program: KLP | EAP | BACHELOR | MASTER")
    ap.add_argument("--limit", type=int, default=0, help="stop after N students")
    ap.add_argument("--report", default="", help="report file name")
    args = ap.parse_args()

    portal = portal_students()
    folders = student_folders(portal)
    if args.passport:
        picks = [args.passport.strip().upper()]
    elif args.student:
        q = norm(args.student)
        picks = [p for p, (d, n) in folders.items() if q in norm(n)]
        if not picks:
            raise SystemExit(f"No downloaded student matches {args.student!r}")
    elif args.all or args.program:
        picks = sorted(folders)
        if args.program:
            key = args.program.strip().upper()
            picks = [p for p in picks if p in portal and program_of(portal[p]) == key]
    else:
        ap.print_help()
        return
    if args.limit:
        picks = picks[:args.limit]
    out, verdicts = run(picks, args.report)
    counts = {}
    for v in verdicts.values():
        counts[v] = counts.get(v, 0) + 1
    print(f"\nchecked {len(verdicts)} student(s): " + ", ".join(f"{k} {v}" for k, v in sorted(counts.items())))
    print(f"report: {out}")


if __name__ == "__main__":
    main()
