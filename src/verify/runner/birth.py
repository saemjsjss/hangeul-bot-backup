"""The birth certificate (RUNNER_SPEC section 5.12; owner decision A3 of 27 Oct 2026).

A port of the lab study C:\\Hangeul\\DOCCHECK\\lab\\private-runner\\birth_cert\\ (birth_cert.py, birth_qr.py,
dob_boxes.py; read-only references), plus the owner's A3 rules:

* Formats. A Bangla-only original (bdris Bangla print) MUST come with an English translation and a notary;
  its fields are read from the translation. The new bilingual print (English + Bangla, QR top-left, barcode
  top-right) and the older English bdris print (BDR Form-3, or the everify record print) need no
  translation. Every format needs the notary.
* The bilingual print's QR MUST be decoded and must match. The bdris QR holds an opaque key (no name, BRN
  or date of birth), so offline the strongest check is: a genuine https bdris link with a well-formed key,
  a barcode BRN equal to the printed BRN, and the BRN's year equal to the date of birth's. A name, BRN or
  date of birth carried by a payload is compared with the print. Undecodable -> FLAG.
* The date of birth on a bdris print is the BOXED field "DD [block] MM [block] YYYY"; OCR reads a block as
  1 / l / I / | / i or drops it. It is read from the text, else from the page image by layout (the blocks
  are never handed to OCR). The "In word(s)" line is the cross-check: boxes (or the label) and words agree
  -> trusted; they disagree -> "DOB unclear". The Date of Issue and the Date of Registration are NEVER
  taken for the date of birth.

Not ported: the online lookup of the QR link (S4: no network), the lab's name extraction and loose name
matching (names are names.py's, strict), and the key-reuse register (one case per run; Q17).

Page numbers kept in BirthFacts (pages, english_pages, notary_pages, seal_only_pages, dob_readings) are
0-based RUN pages. Finding.pages and the "page N" in messages are 1-based INPUT pages (the bundle page, or
the page of the folder file), mapped by read_birth_certificate().

Standard library at import; numpy, OpenCV, PyMuPDF and pyzbar (vendored) are imported when a page image is
looked at. The bot's notarisation test is passed in by the caller. No network, no personal data.
"""
from __future__ import annotations

import base64
import binascii
import datetime as dt
import difflib
import json
import re
import time
from pathlib import Path
from typing import Any, Callable, Dict, Iterator, List, Optional, Sequence, Tuple

from . import strict
from .botimport import PRIVATE
from .model import FAIL, FLAG, NOTE, PASS, BirthFacts, DocUnit, Finding, Identity, MachineRead, PageRead

date = dt.date
SOURCE = "runner:birth"
CODE_FAULTS = (NameError, AttributeError, ImportError, TypeError)   # a bug, never a document problem
MIN_BIRTH_YEAR = 1940
MAX_BIRTH_YEAR = dt.date.today().year + 1
BDRIS_HOSTS = ("bdris.gov.bd", "everify.bdris.gov.bd")
QR_BUDGET_S = 10.0          # the decode ladder's time budget per page
IMAGE_DPI = 300             # page images for the QR, the barcode, the boxed date and the seal
CERT_PRINT_KINDS = ("bilingual_qr", "english_bdris", "everify", "manual_english", "bangla")
ENGLISH_ORDER = {"bilingual_qr": 0, "english_bdris": 1, "everify": 1, "manual_english": 2, "translation": 3}
NOTARY_WORDS = ["notar", "advocate", "attested", "authenticated", "affidavit", "judge court"]


# ----------------------------------------------------------------------------------------------------------
# small helpers
_MONTHS = ["january", "february", "march", "april", "may", "june", "july", "august", "september", "october",
           "november", "december"]
_MON3 = [m[:3] for m in _MONTHS]


def _valid(y: int, m: int, d: int) -> Optional[date]:
    if not (MIN_BIRTH_YEAR <= y <= MAX_BIRTH_YEAR):
        return None
    try:
        return dt.date(y, m, d)
    except (ValueError, TypeError):
        return None


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", s or "").strip()


def _ratio(a: str, b: str) -> float:
    return difflib.SequenceMatcher(None, a, b).ratio()   # label-match only (month / number words, labels)


_DIGITISH = str.maketrans({"O": "0", "o": "0", "Q": "0", "D": "0", "I": "1", "l": "1", "i": "1", "|": "1",
                           "!": "1", "S": "5", "s": "5", "B": "8", "Z": "2", "z": "2", "G": "6", "q": "9",
                           "g": "9", "T": "7", "?": "7"})


def _digits(s: str) -> str:
    """OCR look-alike letters turned into digits (only for spans already known to be numbers)."""
    return s.translate(_DIGITISH)


def _has_any(text: str, words: Sequence[str]) -> bool:
    """The bot's has_any: substring, then a fuzzy pass for OCR's 'nolary', 'advocale', 'attarta'."""
    t = _norm(text).lower()
    if any(w in t for w in words):
        return True
    toks = re.findall(r"[a-z]{4,}", t)
    for w in words:
        w = w.split()[0]
        if len(w) < 5:
            continue
        for tok in toks:
            if abs(len(tok) - len(w)) <= 2 and _ratio(tok, w) >= 0.8:
                return True
    return False


def notary_wording(text: str) -> bool:
    """The notary's wording (NOTARY_WORDS, OCR-tolerant) is on the page."""
    return _has_any(text, NOTARY_WORDS)


# ----------------------------------------------------------------------------------------------------------
# dates (OCR-tolerant, day first)
# A date written with separators (made-up dates): 09/11/1990, 28-02-1991, 17.03-1992, Q8/0.4/1993, 15/1021994
# (a slash read as 2), 05/0511995 (a slash read as 1). Inside a date token O folds to 0.
_NUM = r"[0-9OoQDIl|SsBZz]"
_DATE_SEP_RE = re.compile(rf"(?<![0-9A-Za-z])({_NUM}{{1,2}})\s?[/\-.,]\s?\.?({_NUM}{{1,2}})\s?[/\-.,]?\s?({_NUM}{{4}})(?![0-9])")
_DATE_SLASH1_RE = re.compile(r"(?<![0-9])(\d{2})[/\-.](\d{2})[127l|I](\d{4})(?![0-9])")
_DATE_SPLITYEAR_RE = re.compile(r"(?<![0-9A-Za-z])(\d{1,2})\s?[/\-.]\s?(\d{1,2})\s?[/\-.]\s?(\d{3})\s(\d)(?![0-9])")
_DATE_WORDMON_RE = re.compile(r"(?<![0-9A-Za-z])(\d{1,2})\s*(?:st|nd|rd|th)?[\s,.\-]*([A-Za-z]{3,9})\.?[\s,.;\-]*(\d{4})(?![0-9])")


def _month_any(tok: str) -> Optional[int]:
    """A month name or abbreviation, allowing OCR's misreadings ("FEZRUARY", "Septembar")."""
    t = tok.lower()
    if t[:3] in _MON3 and (len(t) == 3 or _MONTHS[_MON3.index(t[:3])].startswith(t) or
                           _ratio(t, _MONTHS[_MON3.index(t[:3])]) >= 0.75):
        return _MON3.index(t[:3]) + 1
    if len(t) >= 5:
        best = max(range(12), key=lambda i: _ratio(t, _MONTHS[i]))
        if _ratio(t, _MONTHS[best]) >= 0.75:
            return best + 1
    return None


def find_dates_ocr(text: str) -> List[Tuple[int, int, date]]:
    """(start, end, date) of every day-first date in `text`, OCR-tolerant, in reading order."""
    out: List[Tuple[int, int, date]] = []
    t = text or ""
    for m in _DATE_SEP_RE.finditer(t):
        d, mo, y = (int(_digits(g)) if _digits(g).isdigit() else -1 for g in m.groups())
        v = _valid(y, mo, d)
        if v:
            out.append((m.start(), m.end(), v))
    for m in _DATE_SLASH1_RE.finditer(t):
        if any(s <= m.start() < e for s, e, _ in out):
            continue
        d, mo, y = (int(g) for g in m.groups())
        v = _valid(y, mo, d)
        if v:
            out.append((m.start(), m.end(), v))
    for m in _DATE_SPLITYEAR_RE.finditer(t):
        if any(s <= m.start() < e for s, e, _ in out):
            continue
        d, mo, y = int(m.group(1)), int(m.group(2)), int(m.group(3) + m.group(4))
        v = _valid(y, mo, d)
        if v:
            out.append((m.start(), m.end(), v))
    for m in _DATE_WORDMON_RE.finditer(t):
        mon = _month_any(m.group(2))
        v = _valid(int(m.group(3)), mon, int(m.group(1))) if mon else None
        if v:
            out.append((m.start(), m.end(), v))
    out.sort(key=lambda x: x[0])
    return out


def _parse_date(value: str) -> Optional[date]:
    """An ISO (YYYY-MM-DD) or day-first (DD/MM/YYYY, DD-MM-YYYY) date, as a payload may carry it."""
    v = (value or "").strip()
    m = re.fullmatch(r"(\d{4})-(\d{2})-(\d{2})", v)
    if m:
        return _valid(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    m = re.fullmatch(r"(\d{1,2})[/-](\d{1,2})[/-](\d{4})", v)
    if m:
        return _valid(int(m.group(3)), int(m.group(2)), int(m.group(1)))
    return None


# ----------------------------------------------------------------------------------------------------------
# labels (OCR-tolerant)
_DOB_LABEL = re.compile(r"(?<![a-z])(?:d[ao][tl][ea]\s*[:;]?\s*(?:[o0%c][fl1£]?\s*|of\s*)?[bk][a-z0-9]{0,3}?(?:t[hfn6]|lh|h)(?![a-z])"
                        r"|birth\s*date|d\.?\s?o\.?\s?b(?![a-z]))\.?", re.I)
_ISSUE_LABEL = re.compile(r"(?<![a-z])(?:date\s*(?:[o0%][f]?\s*)?(?:of\s*)?(?:issu\w*|1ssu\w*|jasue)|issu(?:e|ed|ing|ance)\s*date"
                          r"|certificate\s*issu\w*\s*date|d[ao][tl][ea]\s*\S{0,3}\s*issu\w*|date\s*of\s*issue\s*of\s*certificate)", re.I)
_REG_LABEL = re.compile(r"(?<![a-z])(?:d[ao][tl][ea]\s*\S{0,3}\s*(?:of\s*)?reg[il1]s\w*|reg[il1]s\w*\s*date)", re.I)
# Where a DOB label's window ends. A bare "Date:" / "Dated" (an issue date's label) ends it: the owner's rule is
# never the Date of Issue. "(In Figure)" does NOT end it: a translation prints "Date of Birth (In Figure): D/M/Y".
_STOP = re.compile(r"(?<![a-z])(?:sex|gender|s[ea]x|[il1j][na]\s*w[o0]r[a-z]*|place|order|name|father|mother|registration"
                   r"|registr\w*|issu\w*|date\s*[o0%]|date\s*[:;.]|dated|permanent|nationality|word)(?![a-z])", re.I)


def _windows(text: str, label: "re.Pattern[str]", size: int = 60) -> List[Tuple[int, str]]:
    """(end of label, text after it up to the next stop word, at most `size` characters)."""
    out = []
    for m in label.finditer(text or ""):
        w = text[m.end():m.end() + size]
        cut = _STOP.search(w)
        out.append((m.end(), w[:cut.start()] if cut else w))
    return out


# ----------------------------------------------------------------------------------------------------------
# the boxed date of birth: DD [block] MM [block] YYYY
_BLOCK_CHARS = "\u25a0\u25aa\u2588\u25ae\u25ac\u25fc"            # the solid squares OCR may print for a block
_BOXED_RUN = re.compile("[0-9" + _BLOCK_CHARS + r"|lIi!/\\\[\](){}\-_.,:;'\"~*]{8,16}")


def _boxed_candidates(window: str) -> List[date]:
    """Every reading of a boxed DOB field as DD-block-MM-block-YYYY. The black blocks come out of OCR as
    '1', 'l', 'I', '|', 'i', a square or nothing, so DD[]MM[]YYYY can read DD1MM1YYYY, DD|MM|YYYY or
    DDMMYYYY. Only dates that fit exactly are returned."""
    s = re.sub(r"\s+", "", window or "")
    s = s.translate(str.maketrans({"O": "0", "o": "0", "Q": "0", "D": "0", "S": "5", "s": "5", "B": "8",
                                   "Z": "2", "z": "2", "G": "6", "q": "9", "g": "9"}))
    m = _BOXED_RUN.search(s)
    if not m:
        return []
    run = m.group(0)
    out: List[date] = []
    # (a) explicit separators: groups of digits split by block-ish characters
    groups = [g for g in re.split(r"[^0-9]+", run) if g]
    if len(groups) == 3 and len(groups[2]) == 4 and len(groups[0]) <= 2 and len(groups[1]) <= 2:
        v = _valid(int(groups[2]), int(groups[1]), int(groups[0]))
        if v:
            out.append(v)
    # (b) blocks read as '1'/'l'/'I'/'|' or dropped: a run of digits/1-likes of length 8..10
    flat = run.translate(str.maketrans({c: "1" for c in "lIi|!"}))
    flat = re.sub(r"[^0-9]", "", flat)
    for k in range(len(flat)):
        for n in (10, 9, 8):
            seg = flat[k:k + n]
            if len(seg) != n:
                continue
            shapes = {10: [(0, 2, 3, 5, 6)], 9: [(0, 2, 3, 5, 5), (0, 2, 2, 4, 5)], 8: [(0, 2, 2, 4, 4)]}[n]
            for d0, d1, m0, m1, y0 in shapes:
                blocks = [i for i in range(n) if i not in range(d0, d1) and i not in range(m0, m1)
                          and i not in range(y0, y0 + 4)]
                if any(seg[i] != "1" for i in blocks):
                    continue
                v = _valid(int(seg[y0:y0 + 4]), int(seg[m0:m1]), int(seg[d0:d1]))
                if v and v not in out:
                    out.append(v)
        if out:
            break
    return out


def extract_dob_boxed(text: str) -> List[date]:
    """Readings of the boxed DOB field right after the "Date of Birth" label (old English bdris print).
    Empty when the boxes were not read (whole-page OCR often skips them)."""
    out: List[date] = []
    for _, w in _windows(text, _DOB_LABEL, 45):
        for v in _boxed_candidates(w):
            if v not in out:
                out.append(v)
    return out


# ----------------------------------------------------------------------------------------------------------
# the "In word(s)" line
_UNITS = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9,
          "ten": 10, "eleven": 11, "twelve": 12, "thirteen": 13, "fourteen": 14, "fifteen": 15, "sixteen": 16,
          "seventeen": 17, "eighteen": 18, "nineteen": 19}
_TENS = {"twenty": 20, "thirty": 30, "forty": 40, "fifty": 50, "sixty": 60, "seventy": 70, "eighty": 80,
         "ninety": 90}
_ORD = {"first": 1, "second": 2, "third": 3, "fourth": 4, "fifth": 5, "sixth": 6, "seventh": 7, "eighth": 8,
        "ninth": 9, "tenth": 10, "eleventh": 11, "twelfth": 12, "thirteenth": 13, "fourteenth": 14,
        "fifteenth": 15, "sixteenth": 16, "seventeenth": 17, "eighteenth": 18, "nineteenth": 19,
        "twentieth": 20, "thirtieth": 30}
_VOCAB = list(_UNITS) + list(_TENS) + list(_ORD) + _MONTHS + ["thousand", "hundred", "and", "of"]


def _word(tok: str) -> Optional[str]:
    """A number / month word, allowing OCR's misspellings ("Tuo", "TwO", "Thousan", "Ninty")."""
    t = tok.lower()
    if t in _VOCAB:
        return t
    if t in ("tuo", "iwo", "twa", "lwo", "tw0"):
        return "two"
    if len(t) < 4:
        return None
    best, score = None, 0.0
    for w in _VOCAB:
        if abs(len(w) - len(t)) > 2:
            continue
        r = _ratio(t, w)
        if r > score:
            best, score = w, r
    return best if score >= 0.8 else None


def _day_from(words: List[Optional[str]]) -> Optional[int]:
    """A day from the words before the month: 'twenty ninth', 'thirty first', 'eleven', 'tenth'."""
    if not words:
        return None
    last = words[-1]
    prev = words[-2] if len(words) >= 2 else None
    if last in _ORD or last in _UNITS:
        v = _ORD.get(last, _UNITS.get(last))
        if prev in _TENS and v < 10:
            v += _TENS[prev]
        return v if 1 <= v <= 31 else None
    if last in _TENS:
        v = _TENS[last]
        return v if v <= 31 else None
    return None


def _year_from(words: List[Optional[str]]) -> Optional[int]:
    """A year from the words after the month: 'nineteen ninety nine', 'two thousand and five', 'two
    thousand', 'thousand seven' (OCR lost the 'two')."""
    w = [x for x in words if x != "and"]
    if not w:
        return None
    if "thousand" in w[:2]:
        i = w.index("thousand")
        if i == 1 and w[0] != "two":
            return None
        rest = w[i + 1:]
        add = 0
        if rest and rest[0] in _TENS:
            add = _TENS[rest[0]]
            if len(rest) > 1 and rest[1] in _UNITS and _UNITS[rest[1]] < 10:
                add += _UNITS[rest[1]]
        elif rest and rest[0] in _UNITS:
            add = _UNITS[rest[0]]
        return 2000 + add
    if w[0] == "nineteen":
        rest = w[1:]
        if rest and rest[0] == "hundred":
            rest = rest[1:]
        if rest and rest[0] in _TENS:
            v = _TENS[rest[0]]
            if len(rest) > 1 and rest[1] in _UNITS and _UNITS[rest[1]] < 10:
                v += _UNITS[rest[1]]
            return 1900 + v
        if rest and rest[0] in _UNITS:
            return 1900 + _UNITS[rest[0]]
    return None


_INWORD_LABEL = re.compile(r"(?<![a-z])(?:[il1jf][na]\s*w[o0]r[a-z]?s?|in\s*speech|in\s*words?|b\s*word)(?![a-z])\s*[:;)\]]?", re.I)
_NUM_ORD = re.compile(r"(?<![0-9A-Za-z])([0-9IlzZ]{1,2})\s*(st|nd|rd|th|tf|tn|t6|tb|16|1h|lh)?[\s,.;:]*([A-Za-z]{3})[a-z]*\.?[\s,.;:]*([0-9]{4})(?![0-9])")


def _month3(tok: str) -> Optional[int]:
    """A 3-letter month abbreviation, allowing one misread letter when that leaves only one month ("Fes"
    -> Feb, "ApT" -> Apr, "Scp" -> Sep; "Mav" is Mar or May and is refused)."""
    t = tok.lower()[:3]
    if t in _MON3:
        return _MON3.index(t) + 1
    hits = [i for i, m in enumerate(_MON3) if sum(a != b for a, b in zip(t, m)) == 1 and t[0] == m[0]]
    return hits[0] + 1 if len(hits) == 1 else None


def _word_date_at(words: List[Optional[str]], i: int) -> Tuple[Optional[date], Optional[int]]:
    """(date, year) of '<day words> (of) <Month> <year words>' around the month word at index i."""
    w = words[i]
    before = [x for x in words[max(0, i - 4):i] if x and x != "of"]
    after: List[str] = []
    for x in words[i + 1:i + 7]:
        if x is None or x in _MONTHS or x == "of":
            break
        after.append(x)
    day, year = _day_from(before), _year_from(after)
    if day and year:
        v = _valid(year, _MONTHS.index(w) + 1, day)
        if v:
            return v, year
    return None, year


def _word_date(words: List[Optional[str]]) -> Tuple[Optional[date], str]:
    """'<day words> (of) <Month> <year words>' in a list of vocabulary words: (date, partial "YYYY-MM")."""
    for i, w in enumerate(words):
        if w not in _MONTHS:
            continue
        v, year = _word_date_at(words, i)
        if v:
            return v, ""
        if year:
            return None, f"{year:04d}-{_MONTHS.index(w) + 1:02d}"
    return None, ""


def _cut_at_other_dates(segment: str) -> str:
    """A words segment ends where a Date of Issue / Date of Registration label starts (never read past)."""
    cuts = [m.start() for rx in (_ISSUE_LABEL, _REG_LABEL) for m in [rx.search(segment)] if m]
    return segment[:min(cuts)] if cuts else segment


def _words_in(segment: str) -> Tuple[Optional[date], str]:
    """(date, partial "YYYY-MM") from one in-words segment."""
    segment = _cut_at_other_dates(segment)
    # numeric ordinal form of the old English bdris print: "5th May, 2004", "18th Dec; 2004"
    m = _NUM_ORD.search(segment)
    if m:
        mon = _month3(m.group(3))
        if mon:
            y = int(m.group(4))
            day = _digits(m.group(1))
            suffix = (m.group(2) or "").lower()
            # "3th": a 1, 2 or 3 printed with "th" lost a digit (13th, 23rd, 31st ...): no day
            lost = day in ("1", "2", "3") and suffix not in ("st", "nd", "rd", "")
            if day.isdigit() and not lost:
                v = _valid(y, mon, int(day))
                if v:
                    return v, ""
            if MIN_BIRTH_YEAR <= y <= MAX_BIRTH_YEAR:
                return None, f"{y:04d}-{mon:02d}"
    m = re.search(r"(?<![0-9A-Za-z])([A-Za-z]{3})[a-z]*[\s,.;:]+([0-9]{4})(?![0-9])", segment)
    if m and _month3(m.group(1)) and not re.search(r"[A-Za-z]", segment[:m.start()].strip()[-1:] or ""):
        y = int(m.group(2))
        if MIN_BIRTH_YEAR <= y <= MAX_BIRTH_YEAR:
            return None, f"{y:04d}-{_month3(m.group(1)):02d}"
    # word form: "<day words> (of) <Month> <year words>"
    return _word_date([_word(t) for t in re.findall(r"[A-Za-z]+", segment)])


def in_words_reading(text: str) -> Tuple[Optional[date], str]:
    """(the date of birth written in words, partial "YYYY-MM" when only the month and year were read).
    After an "In word(s)" label first; else (some translations) a full word-form date anywhere that does
    not follow a Date of Issue / Date of Registration label. Never a numeric stamp."""
    t = text or ""
    partial = ""
    for m in _INWORD_LABEL.finditer(t):
        v, p = _words_in(t[m.end():m.end() + 80])
        if v:
            return v, ""
        partial = partial or p
    toks = list(re.finditer(r"[A-Za-z]+", t))
    words = [_word(x.group(0)) for x in toks]
    for i, w in enumerate(words):
        if w not in _MONTHS:
            continue
        before_text = t[max(0, toks[i].start() - 60):toks[i].start()]
        other = max([m.end() for rx in (_ISSUE_LABEL, _REG_LABEL) for m in rx.finditer(before_text)], default=-1)
        if other > max([m.end() for m in _DOB_LABEL.finditer(before_text)], default=-1):
            continue                                    # a date of issue / registration written in words
        v, _ = _word_date_at(words, i)
        if v:
            return v, ""
    return None, partial


def parse_dob_in_words(text: str) -> Optional[date]:
    return in_words_reading(text)[0]


# ----------------------------------------------------------------------------------------------------------
# the labelled DOB (bilingual print, translations, everify, other English forms)
_EVERIFY_DOB = re.compile(r"date\s*of\s*birth\W+birth\s*reg\S*\s*num\S*\W+sex\W+", re.I)


def labelled_dob(text: str) -> Optional[date]:
    """A plainly written date right after a "Date of Birth" label, read only up to the next label, so a
    registration or issue date is never taken."""
    t = text or ""
    m = _EVERIFY_DOB.search(t)
    if m:
        found = find_dates_ocr(t[m.end():m.end() + 25])
        if found:
            return found[0][2]
    for _, w in _windows(t, _DOB_LABEL, 40):
        found = find_dates_ocr(w)
        if found:
            return found[0][2]
    return None


# ----------------------------------------------------------------------------------------------------------
# issue / registration dates (kept apart: NEVER the date of birth)
def issue_and_registration(text: str, kind: str) -> Tuple[Optional[date], Optional[date]]:
    """(date of issue, date of registration). The bilingual print puts the three headers on one row ("Date
    of Registration  Birth Registration Number  Date of Issuance") and the values on the row below."""
    t = text or ""
    issue = reg = None
    if kind == "bilingual_qr":
        m = re.search(r"reg\w*\W+birth\s*reg\w*\s*num\w*\W+(?:\S+\s+){0,2}?da\w+\s*(?:of\s*)?issu\w*", t, re.I)
        if m:
            row = t[m.end():m.end() + 80]
            cut = re.search(r"d[ao][tl][ea]\b|of\s*b[il1]rth|\bbirth\b", row, re.I)
            row = row[:cut.start()] if cut else row
            ds = find_dates_ocr(row)
            brn = re.search(r"(?<!\d)\d{17}(?!\d)", row)
            if brn:
                before = [d for s, e, d in ds if e <= brn.start()]
                after = [d for s, e, d in ds if s >= brn.end()]
                reg = before[-1] if before else None
                issue = after[0] if after else None
            elif len(ds) >= 2:
                reg, issue = ds[0][2], ds[1][2]
            return issue, reg
    if kind == "everify":
        m = re.search(r"registration\s*date.{0,60}?issuance\s*date", t, re.I | re.S)
        if m:
            ds = find_dates_ocr(t[m.end():m.end() + 90])
            if ds:
                reg = ds[0][2]
                if len(ds) > 1:
                    issue = ds[1][2]
            return issue, reg
    for m in _ISSUE_LABEL.finditer(t):
        ds = find_dates_ocr(t[m.end():m.end() + 30])
        if ds and ds[0][0] < 12:
            issue = ds[0][2]
            break
    for m in _REG_LABEL.finditer(t):
        ds = find_dates_ocr(t[m.end():m.end() + 30])
        if ds and ds[0][0] < 12:
            reg = ds[0][2]
            break
    return issue, reg


# ----------------------------------------------------------------------------------------------------------
# birth registration number
_BRN_LABEL = re.compile(r"(?<![a-z'$])(?:birth\s*(?:of\s*)?reg\w*\s*(?:num\w*|no\.?)|br\s*n[uo]mber|br\s*monber|br\s*niunber"
                        r"|personal\s*identification\s*no|registration\s*number)(?![a-z])", re.I)
_PARENT_BRN = re.compile(r"(?:father|mother|fat\w*|mot\w*)\W{0,4}\s*(?:birth|br)", re.I)


def extract_brn(text: str, kind: str = "") -> str:
    """The certificate's own birth registration number (17 digits: birth year + 13), never a parent's.
    Boxed digits are joined; a partial read gives ''."""
    t = text or ""
    if kind == "bilingual_qr":
        m = re.search(r"birth\s*reg\w*\s*num\w*\W+(?:da\w+\s*(?:of\s*)?issu\w*\W+)?(?:\S+\s+){0,2}?(\d{17})(?!\d)", t, re.I)
        if m:
            return m.group(1)
    for m in _BRN_LABEL.finditer(t):
        before = t[max(0, m.start() - 12):m.start()]
        if re.search(r"father|mother|fat\w|mot\w|fath|moth", before, re.I):
            continue
        w = t[m.end():m.end() + 70]
        cut = re.search(r"name|date|sex|nam[ce]|~amc", w, re.I)
        w = w[:cut.start()] if cut else w
        m17 = re.search(r"(?<!\d)(\d{17})(?!\d)", w)
        if m17:
            return m17.group(1)
        digits = re.sub(r"[^0-9]", "", re.sub(r"(?<=\d)[|lI](?=\s*\d)", "", w))
        if len(digits) == 17:
            return digits
    if kind == "english_bdris":
        # the old print boxes the child's own BRN (often lost by OCR) and prints the parents' BRN / NID plainly,
        # with labels OCR garbles: a bare 17-digit run there is a parent's number (owner's example 20)
        return ""
    m = re.search(r"(?<!\d)((?:19[4-9]\d|20[0-2]\d)\d{13})(?!\d)", t)
    if m and not _PARENT_BRN.search(t[max(0, m.start() - 30):m.start()]):
        return m.group(1)
    return ""


# ----------------------------------------------------------------------------------------------------------
# page kind
_EN_WORDS = {"birth", "date", "registration", "name", "father", "mother", "nationality", "sex", "male", "female",
             "place", "permanent", "address", "government", "republic", "office", "registrar", "union", "parishad",
             "upazila", "district", "certificate", "issue", "issuance", "word", "words", "bangladesh", "bangladeshi",
             "death", "number", "the", "people", "child", "order", "rule", "rules", "village", "post"}


def english_score(text: str) -> int:
    return sum(1 for w in re.findall(r"[a-z]{2,}", (text or "").lower()) if w in _EN_WORDS)


def page_kind(text: str) -> str:
    """"bilingual_qr" | "english_bdris" | "everify" | "translation" | "bangla" | "manual_english" | "other".
    The English OCR engine reads Bangla as noise, so a page with too few English certificate words is
    "bangla"; a page with (almost) no text is "other"."""
    t = text or ""
    low = t.lower()
    bangla = len(re.findall(r"[\u0980-\u09ff]", t))
    if bangla > 40 and bangla > len(re.findall(r"[A-Za-z]", t)) / 2:
        return "bangla"
    if len(re.sub(r"\W", "", t)) < 40:
        return "other"
    en = english_score(t)
    head = low[:700]
    translated = bool(re.search(r"translat\w*\s*(?:true\s*)?copy|true\s*copy|\(translat|translated\s*true|translation\s*cent(?:er|re)"
                                r"|translation\s*service|approved\s*translation|translate\s*true|govt\.?\s*(?:reg|approv)", head))
    bil = bool(re.search(r"date\s*of\s*issuance|issuance|generated\s*(?:from|irom|fram)|scan\s*the\s*above|qr\s*code|bar\s*code"
                         r"|assist\w*\s*to\s*regis|preparation,?\s*verification", low))
    ever = bool(re.search(r"record\s*verification|everify|retri\w*\s*from\s*birth|registered\s*person", low))
    old = bool(re.search(r"bdr\s*f?or[mn]|br\s*n[uoi]\w*ber|br\s*monber|extract\w*\s*from\s*(?:br|kr|#r)|note\s*t[o0]\s*rules?"
                         r"|register\s*no|regis[lt]er\s*no|signature\s*an\w\s*seal\s*of\s*the\s*registrar|certificatelanguage-english"
                         # a low-resolution read ("XDR Form", "SDR Form"); the bdris admin print's browser header
                         r"|[a-z]dr\s*f[o0]r[mn]|b?d[a-z]?[il1n]?s\W{0,2}g[o0]v\W{0,2}bd\W{0,2}\w{0,3}adm|br\s*m[uo]+mber", low))
    manual = bool(re.search(r"\((?:up|cc|ps|pc|mc)?\s*bdr\s*form\s*-?\s*3a\)|form\s*-?\s*3a|\(upbdr|\(ccbdr|\(psbdr", low))
    if translated and not (bil and re.search(r"generated|scan\s*the\s*above|issuance", low[:1500])):
        return "translation" if en >= 6 else "bangla"
    if ever:
        return "everify"
    if bil and en >= 6:
        return "bilingual_qr"
    if manual and en >= 6:
        return "manual_english"
    if old and en >= 6:
        return "english_bdris"
    if en >= 10:
        # English certificate wording without a known print's markers: a translation lacking its header, or
        # an English form we do not know
        return "translation" if re.search(r"translat|true\s*copy|lawyer|notary", low) else "manual_english"
    return "bangla"


def _format_of(kinds: List[str]) -> Tuple[str, str, List[str]]:
    """(format, variant, kinds after the reclassification of a Bangla original's English pages)."""
    has = set(kinds)
    english = {"english_bdris", "manual_english", "translation"}
    if "bilingual_qr" in has:
        return "bilingual_qr", "", kinds
    if "bangla" in has and has & english:
        # a Bangla-only original with an English page beside it: that page is its translation, whatever
        # layout the translator copied (owner's example: a translation laid out like the old English print)
        return "bangla_original", "", ["translation" if k in english else k for k in kinds]
    if "english_bdris" in has:
        return "english_bdris", "", kinds
    if "everify" in has:
        return "english_bdris", "everify", kinds
    if "bangla" in has:
        return "bangla_original", "", kinds
    if "translation" in has:
        return "english_translation", "", kinds
    if "manual_english" in has:
        return "unknown", "manual_english", kinds
    return "unknown", "", kinds


# ----------------------------------------------------------------------------------------------------------
# images: loading, the seal colour
def _np():
    import numpy as np
    return np


def _cv2():
    import cv2
    return cv2


def load_image(source, page: int = 0, dpi: int = IMAGE_DPI):
    """A BGR image from a PNG/JPG path (unicode-safe), a PDF page (rendered read-only), or an array passed
    through (a grey array becomes BGR)."""
    np, cv2 = _np(), _cv2()
    if isinstance(source, np.ndarray):
        return source if source.ndim == 3 else cv2.cvtColor(source, cv2.COLOR_GRAY2BGR)
    p = Path(source)
    if p.suffix.lower() == ".pdf":
        import pymupdf
        with pymupdf.open(str(p)) as doc:
            if page >= doc.page_count:
                return None
            pix = doc[page].get_pixmap(dpi=dpi)
            a = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width, pix.n)
            return cv2.cvtColor(a, cv2.COLOR_RGB2BGR if pix.n == 3 else cv2.COLOR_RGBA2BGR)
    data = np.fromfile(str(p), dtype=np.uint8)
    return cv2.imdecode(data, cv2.IMREAD_COLOR) if data.size else None


def _bgr(rgb):
    """The engine's RGB page image as BGR (OpenCV's order)."""
    return None if rgb is None else rgb[:, :, ::-1].copy()


def seal_on_image(bgr) -> bool:
    """A violet/purple round stamp or a red wax seal on a BGR page image (the bot's page_checks.seal_present
    colour test, on an array). Used only when no page file can be handed to the caller's test."""
    np, cv2 = _np(), _cv2()
    if bgr is None:
        return False
    H, W = bgr.shape[:2]
    hsv = cv2.cvtColor(bgr, cv2.COLOR_BGR2HSV)
    violet = ((hsv[:, :, 0] > 115) & (hsv[:, :, 0] < 165) & (hsv[:, :, 1] > 60) & (hsv[:, :, 2] > 60)).astype("uint8")
    red = ((hsv[:, :, 1] > 90) & (hsv[:, :, 2] > 70) & ((hsv[:, :, 0] < 10) | (hsv[:, :, 0] > 170))).astype("uint8")
    for mask, need in ((violet, 0.0008), (red, 0.0015)):
        m = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, np.ones((9, 9), np.uint8))
        n, _, stats, _ = cv2.connectedComponentsWithStats(m, 8)
        for i in range(1, n):
            if stats[i, cv2.CC_STAT_AREA] > need * H * W:
                return True
    return False


def _gray(img):
    cv2 = _cv2()
    return img if img.ndim == 2 else cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)


def _crop(img, region: Tuple[float, float, float, float]):
    h, w = img.shape[:2]
    x0, y0, x1, y1 = region
    return img[int(y0 * h):int(y1 * h), int(x0 * w):int(x1 * w)]


# ----------------------------------------------------------------------------------------------------------
# QR decoding (port of birth_qr.py: region crop x rescales x binarisations; cv2, cv2-Aruco, pyzbar)
QR_REGION = (0.0, 0.0, 0.42, 0.30)          # x0, y0, x1, y1 as fractions of the page: the bilingual QR
BARCODE_REGION = (0.50, 0.0, 1.0, 0.22)     # the bilingual barcode
_BRN_RE = re.compile(r"(?<!\d)(\d{17})(?!\d)")
_DET: Dict[str, Any] = {}


def _detectors():
    if not _DET:
        cv2 = _cv2()
        _DET["cv2"] = cv2.QRCodeDetector()
        _DET["aruco"] = cv2.QRCodeDetectorAruco() if hasattr(cv2, "QRCodeDetectorAruco") else None
    return _DET


def _zbar():
    """(decode, ZBarSymbol) of the vendored pyzbar, or None when it cannot load."""
    if "zbar" not in _DET:
        try:
            from pyzbar.pyzbar import ZBarSymbol, decode
            _DET["zbar"] = (decode, ZBarSymbol)
        except Exception:                       # pyzbar or its DLL missing: OpenCV only
            _DET["zbar"] = None
    return _DET["zbar"]


def _clean(raw) -> str:
    if raw is None:
        return ""
    if isinstance(raw, bytes):
        for enc in ("utf-8", "latin-1"):
            try:
                raw = raw.decode(enc)
                break
            except UnicodeDecodeError:
                continue
    return str(raw).strip().strip("\x00")


def _real(s: str) -> bool:
    """The Aruco detector sometimes 'reads' 1-3 characters out of noise; such a read is junk."""
    return len(s) >= 8


def _try_qr(gray, use_zbar: bool = True) -> Tuple[str, str]:
    """One image through cv2, cv2-Aruco and pyzbar: (payload, decoder) or ("", "")."""
    cv2 = _cv2()
    det = _detectors()
    for name in ("cv2", "aruco"):
        d = det.get(name)
        if d is None:
            continue
        try:
            s, _pts, _ = d.detectAndDecode(gray)
        except cv2.error:
            s = ""
        s = _clean(s)
        if _real(s):
            return s, name
    zb = _zbar() if use_zbar else None
    if zb is not None:
        decode, sym = zb
        try:
            for z in decode(gray, symbols=[sym.QRCODE]):
                s = _clean(z.data)
                if _real(s):
                    return s, "pyzbar"
        except Exception:                       # a zbar failure on one variant: try the next one
            pass
    return "", ""


def _variants(crop) -> Iterator[Tuple[str, Any]]:
    """Rescales x binarisations. Small images (a 96-150 dpi screenshot) are upscaled first; 200-300 dpi
    scans are tried as is and DOWNscaled first (toner spread and paper grain decode better smaller).
    Smudged photocopies need a grey dilation or a fixed threshold."""
    np, cv2 = _np(), _cv2()
    g0 = _gray(crop)
    small = max(g0.shape) < 700
    scales = (1.0, 2.0, 3.0, 4.0, 6.0, 1.5) if small else (1.0, 0.5, 0.75, 1.5, 2.0, 0.35)
    sharp = np.array([[0, -1, 0], [-1, 5, -1], [0, -1, 0]], np.float32)
    for f in scales:
        if f == 1.0:
            g = g0
        else:
            if max(g0.shape) * f > 4200:                 # a whole-page crop: do not blow it up
                continue
            g = cv2.resize(g0, None, fx=f, fy=f, interpolation=cv2.INTER_AREA if f < 1 else cv2.INTER_CUBIC)
        tag = f"x{f:g}"
        yield f"{tag}-gray", g
        _, o = cv2.threshold(g, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
        yield f"{tag}-otsu", o
        yield f"{tag}-sharp", cv2.filter2D(g, -1, sharp)
        yield f"{tag}-blur", cv2.GaussianBlur(g, (3, 3), 0)
        yield f"{tag}-median", cv2.medianBlur(g, 3)
        yield f"{tag}-adapt", cv2.adaptiveThreshold(g, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY, 31, 10)
        c = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8)).apply(g)
        _, co = cv2.threshold(c, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
        yield f"{tag}-clahe", co
        for k in (2, 3):
            yield f"{tag}-dilate{k}", cv2.dilate(g, np.ones((k, k), np.uint8))
        for t in (90, 120):
            yield f"{tag}-t{t}", ((g > t) * 255).astype(np.uint8)


def _qr_boxes(img) -> List[Tuple[int, int, int, int]]:
    """Boxes where OpenCV sees a QR (finder patterns), as x0, y0, x1, y1 with a margin."""
    cv2 = _cv2()
    out = []
    g = _gray(img)
    for name in ("cv2", "aruco"):
        d = _detectors().get(name)
        if d is None:
            continue
        try:
            ok, pts = d.detectMulti(g)
        except cv2.error:
            ok, pts = False, None
        if ok and pts is not None:
            for quad in pts:
                xs, ys = quad[:, 0], quad[:, 1]
                m = 0.15 * max(xs.max() - xs.min(), ys.max() - ys.min())
                out.append((max(0, int(xs.min() - m)), max(0, int(ys.min() - m)),
                            min(g.shape[1], int(xs.max() + m)), min(g.shape[0], int(ys.max() + m))))
    return out


def _seen(img) -> bool:
    """A QR is visible (finder patterns found) even if it does not decode."""
    cv2 = _cv2()
    for scale in (1.0, 2.0):
        g = _gray(img) if scale == 1.0 else cv2.resize(_gray(img), None, fx=scale, fy=scale,
                                                      interpolation=cv2.INTER_CUBIC)
        try:
            ok, pts = _detectors()["cv2"].detect(g)
        except cv2.error:
            ok, pts = False, None
        if ok and pts is not None:
            return True
    return False


def decode_qr_image(img, budget_s: float = QR_BUDGET_S, use_zbar: bool = True) -> Tuple[str, str, bool]:
    """(payload, stage, seen). The top-left region (where the bilingual print has it) at several scales and
    binarisations; then any box OpenCV detects on the page; then the whole page. Stops at the first real
    payload."""
    t0 = time.time()
    region = _crop(img, QR_REGION)
    for tag, v in _variants(region):
        s, dec = _try_qr(v, use_zbar)
        if s:
            return s, f"region:{tag}:{dec}", True
        if time.time() - t0 > budget_s:
            break
    for (x0, y0, x1, y1) in _qr_boxes(img):
        for tag, v in _variants(img[y0:y1, x0:x1]):
            s, dec = _try_qr(v, use_zbar)
            if s:
                return s, f"box:{tag}:{dec}", True
            if time.time() - t0 > budget_s:
                break
    s, dec = _try_qr(_gray(img), use_zbar)
    if s:
        return s, f"page:{dec}", True
    return "", "", _seen(region)


# --- barcode: a small Code 128 decoder (numpy only) -------------------------------------------------------
# Standard Code 128 symbol widths (bar, space, bar, space, bar, space), values 0..105.
_C128 = (
    "212222 222122 222221 121223 121322 131222 122213 122312 132212 221213 221312 231212 112232 "
    "122132 122231 113222 123122 123221 223211 221132 221231 213212 223112 312131 311222 321122 "
    "321221 312212 322112 322211 212123 212321 232121 111323 131123 131321 112313 132113 132311 "
    "211313 231113 231311 112133 112331 132131 113123 113321 133121 313121 211331 231131 213113 "
    "213311 213131 311123 311321 331121 312113 312311 332111 314111 221411 431111 111224 111422 "
    "121124 121421 141122 141221 112214 112412 122114 122411 142112 142211 241211 221114 413111 "
    "241112 134111 111242 121142 121241 114212 124112 124211 411212 421112 421211 212141 214121 "
    "412121 111143 111341 131141 114113 114311 411113 411311 113141 114131 311141 411131 211412 "
    "211214 211232").split()
C128_STOP = "2331112"
_C128_NP: Dict[str, Any] = {}


def _c128_tables():
    if not _C128_NP:
        np = _np()
        pat = np.array([[int(c) for c in p] for p in _C128], dtype=np.float32)          # (106, 6)
        _C128_NP["pat"] = pat
        _C128_NP["edge"] = np.stack([pat[:, j] + pat[:, j + 1] for j in range(4)], axis=1)
        _C128_NP["stop"] = np.array([int(c) for c in C128_STOP], dtype=np.float32)
    return _C128_NP


def _runs(profile, thr) -> Tuple[Any, bool]:
    """Sub-pixel run widths of one scanline (dark < thr). Returns (widths, first_run_is_dark)."""
    np = _np()
    d = profile.astype(np.float32) - thr
    s = d < 0
    idx = np.nonzero(s[1:] != s[:-1])[0]
    if idx.size < 8:
        return np.zeros(0, np.float32), False
    a, b = d[idx], d[idx + 1]
    x = idx + a / np.where(a - b == 0, 1e-6, a - b)                   # linear crossing position
    return np.diff(x), bool(s[idx[0] + 1])


def _symbol(w) -> Tuple[int, float]:
    """Best Code 128 value for six run widths, by edge-to-similar-edge distances (immune to ink spread),
    ties broken by the element widths. (value, cost); a cost over ~1.2 means no symbol."""
    np = _np()
    tb = _c128_tables()
    T = float(w.sum())
    if T <= 0:
        return -1, 99.0
    e = np.array([w[j] + w[j + 1] for j in range(4)], np.float32) * (11.0 / T)
    c = np.abs(tb["edge"] - e).sum(axis=1)
    k = int(np.argmin(c))
    if c[k] > 1.6:
        return -1, float(c[k])
    near = np.nonzero(c <= c[k] + 0.25)[0]
    if near.size > 1:
        n11 = w * (11.0 / T)
        k = int(near[np.argmin(np.abs(tb["pat"][near] - n11).sum(axis=1))])
    return k, float(c[k])


def _c128_text(vals: List[int]) -> Optional[str]:
    """Data symbols (after the start, before the check symbol) to text; None on a structural error."""
    cs = {103: "A", 104: "B", 105: "C"}.get(vals[0])
    if cs is None:
        return None
    out = []
    shift = False
    for v in vals[1:]:
        cur = cs if not shift else ("B" if cs == "A" else "A")
        shift = False
        if cur == "C":
            if v < 100:
                out.append(f"{v:02d}")
            elif v == 100:
                cs = "B"
            elif v == 101:
                cs = "A"
            elif v == 102:
                continue                                           # FNC1
            else:
                return None
        else:
            if v < 96:
                if cur == "B":
                    out.append(chr(32 + v))
                else:
                    out.append(chr(32 + v) if v < 64 else chr(v - 64))
            elif v == 98:
                shift = True
            elif v == 99:
                cs = "C"
            elif v == 100:
                cs = "B" if cur == "A" else cs                   # FNC4 in B: ignored
            elif v == 101:
                cs = "A" if cur == "B" else cs
            elif v in (96, 97, 102):
                continue                                           # FNC3 / FNC2 / FNC1
            else:
                return None
    return "".join(out)


def _decode_runs(w, first_dark: bool) -> List[str]:
    """All checksum-valid Code 128 reads in one scanline's runs (either direction)."""
    np = _np()
    stop = _c128_tables()["stop"]
    found = []
    for runs, fd in ((w, first_dark), (w[::-1], first_dark if len(w) % 2 else not first_dark)):
        n = len(runs)
        for k in range(0, n - 18):
            if (k % 2 == 0) != fd:                                # a symbol starts with a bar
                continue
            v, c = _symbol(runs[k:k + 6])
            if v not in (103, 104, 105) or c > 0.9:
                continue
            q = runs[k:k + 6].sum() / 11.0                         # module width from the start symbol
            if k > 0 and runs[k - 1] < 5 * q:                      # quiet zone before the start
                continue
            vals = [v]
            i = k + 6
            ok = False
            while i + 7 <= n:
                seven = runs[i:i + 7]
                if abs(seven.sum() / 13.0 - q) < 0.35 * q:
                    st = seven * (13.0 / seven.sum())
                    if np.abs(st - stop).sum() < 2.2 and (i + 7 == n or runs[i + 7] > 4 * q):
                        ok = True
                        break
                v, c = _symbol(runs[i:i + 6])
                if v < 0 or v > 102 or abs(runs[i:i + 6].sum() / 11.0 - q) > 0.3 * q:
                    break
                vals.append(v)
                i += 6
            if not ok or len(vals) < 3:
                continue
            data, check = vals[:-1], vals[-1]
            if (data[0] + sum(j * x for j, x in enumerate(data[1:], 1))) % 103 != check:
                continue
            t = _c128_text(data)
            if t:
                found.append(t)
    return found


def _barcode_band(g) -> Optional[Tuple[int, int, int, int]]:
    """(y0, y1, x0, x1) of the 1-D barcode in a crop: the biggest wide blob of strong horizontal and weak
    vertical gradient (bars), so text lines, which change from row to row, are not taken."""
    np, cv2 = _np(), _cv2()
    gx = cv2.Sobel(g, cv2.CV_32F, 1, 0, ksize=3)
    gy = cv2.Sobel(g, cv2.CV_32F, 0, 1, ksize=3)
    m = cv2.blur(np.clip(np.abs(gx) - np.abs(gy), 0, None), (9, 9))
    if float(m.max()) <= 0:
        return None
    m8 = cv2.convertScaleAbs(m, alpha=255.0 / float(m.max()))
    _, b = cv2.threshold(m8, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    kw = max(15, g.shape[1] // 60)
    b = cv2.morphologyEx(b, cv2.MORPH_CLOSE, cv2.getStructuringElement(cv2.MORPH_RECT, (kw, 5)))
    b = cv2.morphologyEx(b, cv2.MORPH_OPEN, cv2.getStructuringElement(cv2.MORPH_RECT, (kw // 2, 7)))
    n, _lab, stats, _ = cv2.connectedComponentsWithStats(b)
    best = None
    for i in range(1, n):
        x, y, w, h, area = stats[i]
        if w < 60 or h < 8 or w < 2.0 * h:
            continue
        fill = area / float(w * h)
        score = area * fill
        if best is None or score > best[0]:
            best = (score, x, y, w, h)
    if best is None:
        return _barcode_band_rows(g)
    _, x, y, w, h = best
    pad = max(12, int(0.08 * w))
    return (max(0, y), min(g.shape[0], y + h), max(0, x - pad), min(g.shape[1], x + w + pad))


def _barcode_band_rows(g) -> Optional[Tuple[int, int, int, int]]:
    """Fallback: the rows with the most dark/light transitions."""
    np = _np()
    b = g < (int(g.min()) + int(g.max())) // 2
    trans = (b[:, 1:] != b[:, :-1]).sum(axis=1).astype(np.float32)
    if trans.max() < 40:
        return None
    sm = np.convolve(trans, np.ones(5, np.float32) / 5, mode="same")
    top = int(np.argmax(sm))
    lim = 0.55 * sm[top]
    y0 = top
    while y0 > 0 and sm[y0 - 1] >= lim:
        y0 -= 1
    y1 = top
    while y1 < len(sm) - 1 and sm[y1 + 1] >= lim:
        y1 += 1
    cols = np.nonzero((b[y0:y1 + 1, 1:] != b[y0:y1 + 1, :-1]).sum(axis=0) > 0.3 * (y1 - y0 + 1))[0]
    if cols.size < 20:
        return None
    pad = max(12, int(0.06 * (cols[-1] - cols[0])))
    return y0, y1 + 1, max(0, int(cols[0]) - pad), min(g.shape[1], int(cols[-1]) + pad)


def _envelope_thr(p, win: int, lo: float, hi: float):
    """Per-pixel threshold halfway between the local min and max; flat stretches use the global midpoint."""
    np = _np()
    win = max(3, int(win) | 1)
    pad = win // 2
    pp = np.pad(p, (pad, pad), mode="edge")
    view = np.lib.stride_tricks.sliding_window_view(pp, win)
    mx, mn = view.max(axis=1)[:len(p)], view.min(axis=1)[:len(p)]
    thr = (mx + mn) / 2.0
    thr[(mx - mn) < 0.25 * (hi - lo)] = (lo + hi) / 2.0
    return thr


def code128_votes(gray, max_lines: int = 40, budget_s: float = 3.0) -> Dict[str, int]:
    """Checksum-valid Code 128 reads over many scanlines of the barcode band: {text: votes}; one read per
    scanline at most, so the votes count scanlines."""
    np, cv2 = _np(), _cv2()
    t0 = time.time()
    votes: Dict[str, int] = {}
    band = _barcode_band(gray)
    if band is None:
        return votes
    y0, y1, x0, x1 = band
    sub = gray[y0:y1, x0:x1].astype(np.float32)
    if sub.shape[1] < 450:                                          # < ~3 px a module: interpolate
        sub = cv2.resize(sub, None, fx=450.0 / sub.shape[1], fy=1.0, interpolation=cv2.INTER_CUBIC)
    q_est = sub.shape[1] / 170.0                                    # ~150 modules + quiet zones
    rows = np.linspace(0.1 * sub.shape[0], 0.9 * sub.shape[0], num=min(max_lines, sub.shape[0])).astype(int)
    lo, hi = float(np.percentile(sub, 5)), float(np.percentile(sub, 95))
    for y in sorted(set(rows.tolist())):
        prof = sub[max(0, y - 1):y + 2].mean(axis=0)                # 3-row average against speckle
        thrs = [lo + f * (hi - lo) for f in (0.5, 0.4, 0.6)]
        thrs += [_envelope_thr(prof, 2.5 * q_est, lo, hi), _envelope_thr(prof, 4 * q_est, lo, hi)]
        for thr in thrs:
            w, fd = _runs(prof, thr)
            hits = _decode_runs(w, fd) if w.size else []
            if hits:
                votes[hits[0]] = votes.get(hits[0], 0) + 1
                break
        if time.time() - t0 > budget_s:
            break
    return votes


def zbar_votes(gray) -> Dict[str, int]:
    """pyzbar (vendored) over a few rescales and binarisations of the barcode crop: {text: votes}."""
    cv2 = _cv2()
    votes: Dict[str, int] = {}
    zb = _zbar()
    if zb is None:
        return votes
    decode, sym = zb
    one_d = [s for s in sym if s.name not in ("QRCODE", "PDF417", "SQCODE", "NONE", "PARTIAL", "COMPOSITE")]
    band = _barcode_band(gray)
    if band is not None:
        y0, y1, x0, x1 = band
        m = max(10, (y1 - y0) // 2)
        gray = gray[max(0, y0 - m):y1 + m, x0:x1]
    for fx, fy in ((1, 1), (0.5, 0.5), (0.75, 0.75), (1.5, 1.5), (2, 2), (2, 1), (3, 1)):
        g = gray if (fx, fy) == (1, 1) else cv2.resize(gray, None, fx=fx, fy=fy,
                                                       interpolation=cv2.INTER_AREA if fx < 1 else cv2.INTER_CUBIC)
        _, o = cv2.threshold(g, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
        for v in (g, o):
            try:
                for z in decode(v, symbols=one_d):
                    s = _clean(z.data)
                    if s:
                        votes[s] = votes.get(s, 0) + 1
            except Exception:                   # a zbar failure on one variant: try the next one
                pass
    return votes


def _brn_shaped(s: str) -> bool:
    return bool(re.fullmatch(r"\d{17}", s or ""))


def decode_barcode_image(img, region: Optional[Tuple[float, float, float, float]] = BARCODE_REGION,
                         use_zbar: bool = True) -> Dict[str, Any]:
    """The top-right 1-D barcode of the bilingual print (a Code 128 of the 17-digit BRN), decided by
    CONSENSUS over the numpy decoder's scanlines and pyzbar's rescales, never on one read:
      "decoded"   the top 17-digit value has >= 2 votes and more than any other value;
      "conflict"  two values tie (a degraded print: not to be trusted);
      "weak"      a single read only (reported, never used for a FAIL);
      "undecoded" nothing read."""
    g = _gray(img if region is None else _crop(img, region))
    votes: Dict[str, int] = {}
    for src in (code128_votes(g), zbar_votes(g) if use_zbar else {}):
        for k, v in src.items():
            if _brn_shaped(k):
                votes[k] = votes.get(k, 0) + v
    if not votes:
        return {"status": "undecoded", "type": "", "payload": "", "votes": 0, "alternatives": {}}
    ranked = sorted(votes.items(), key=lambda kv: -kv[1])
    top, n = ranked[0]
    second = ranked[1][1] if len(ranked) > 1 else 0
    status = "decoded" if (n >= 2 and n > second) else ("conflict" if n == second else "weak")
    return {"status": status, "type": "CODE128", "payload": top, "votes": n, "alternatives": dict(ranked[1:])}


# --- payload parsing ---------------------------------------------------------------------------------------
def _b64_len(value: str) -> Optional[int]:
    v = value.replace(" ", "+")                   # a '+' in a query string often arrives as a space
    for pad in ("", "=", "=="):
        for fn in (base64.b64decode, base64.urlsafe_b64decode):
            try:
                return len(fn(v + pad))
            except (binascii.Error, ValueError):
                continue
    return None


def parse_payload(payload: str) -> Dict[str, Any]:
    """What a QR / barcode payload is. URL: scheme, host, path, params, key (the bdris verify token). Text:
    any 17-digit BRN, a date and a 'Name' field it carries (text_fields)."""
    from urllib.parse import parse_qsl, urlsplit          # parsing only: no request is ever made
    out: Dict[str, Any] = {"kind": "", "scheme": "", "host": "", "path": "", "params": {}, "key": "",
                           "key_len": 0, "key_bytes": None, "text_fields": {}}
    s = (payload or "").strip()
    if not s:
        return out
    if re.match(r"^[a-z][a-z0-9+.-]*://", s, re.I):
        u = urlsplit(s)
        host = (u.hostname or "").lower()
        params = dict(parse_qsl(u.query, keep_blank_values=True))
        # parse_qsl turns '+' into ' ': keep the key exactly as printed in the payload
        raw = dict(p.split("=", 1) if "=" in p else (p, "") for p in u.query.split("&") if p)
        key = raw.get("key", "") or raw.get("k", "")
        out.update(scheme=u.scheme.lower(), host=host, path=u.path, params=params, key=key, key_len=len(key),
                   key_bytes=_b64_len(key) if key else None)
        official = any(host == h or host.endswith("." + h) for h in BDRIS_HOSTS)
        if official and "verify" in u.path.lower():
            out["kind"] = "bdris_verify_url"
        elif official and "birthqr" in u.path.lower() and (raw.get("qc") or raw.get("QC")):
            out["kind"] = "bdris_everify_url"
        else:
            out["kind"] = "url"
        fields: Dict[str, str] = {}
        for name, value in params.items():                 # a URL that carries the record itself
            v = value.strip()
            low = re.sub(r"[^a-z]", "", name.lower())
            if _BRN_RE.fullmatch(v):
                fields["brn"] = v
            elif re.fullmatch(r"\d{4}-\d{2}-\d{2}|\d{2}[/-]\d{2}[/-]\d{4}", v):
                fields["dob"] = v
            elif low in _QR_PARENT_KEYS and re.search(r"[A-Za-z]{2}", v):      # revision 2.3: the parents
                fields[_QR_PARENT_KEYS[low]] = v
            elif name.lower() in ("name", "fullname", "full_name", "nm") and re.search(r"[A-Za-z]{2}", v):
                fields["name"] = v
        out["text_fields"] = fields
        return out
    out["kind"] = "text"
    fields = {}
    m = _BRN_RE.search(s)
    if m:
        fields["brn"] = m.group(1)
    m = re.search(r"(\d{2}[/-]\d{2}[/-]\d{4}|\d{4}-\d{2}-\d{2})", s)
    if m:
        fields["dob"] = m.group(1)
    fields.update(_text_name_fields(s))
    out["text_fields"] = fields
    return out


# revision 2.3: a payload that carries the parents' names (the owner: the bilingual QR must decode and MATCH)
_QR_PARENT_KEYS = {"father": "father", "fathername": "father", "fathersname": "father",
                   "mother": "mother", "mothername": "mother", "mothersname": "mother"}
_QR_LABEL = re.compile(r"(?i)\b(?:(father|mother)(?:'?s)?\s*(?:name)?|(?<![a-z])name)\s*[:=]\s*")
_QR_VALUE_END = re.compile(r"(?i)[\n\r;,|]|\b(?:father|mother|name|date|dob|birth|brn|reg|sex|gender)\b")


def _text_name_fields(s: str) -> Dict[str, str]:
    """A text payload's 'Name:', "Father's Name:" and "Mother's Name:" values ({"name", "father", "mother"}); a
    value ends at a line break, a separator or the next field word. The first of each kind counts."""
    out: Dict[str, str] = {}
    for m in _QR_LABEL.finditer(s or ""):
        key = (m.group(1) or "name").lower()
        rest = s[m.end():]
        end = _QR_VALUE_END.search(rest)
        value = re.sub(r"[^A-Za-z .'-]", " ", rest[:end.start()] if end else rest)
        value = " ".join(value.split()).strip(" .'-")
        if key not in out and len(re.sub(r"[^A-Za-z]", "", value)) >= 3:
            out[key] = value
    return out


def qr_names(facts: BirthFacts) -> Dict[str, str]:
    """The names a decoded birth-certificate payload carries: {"student": .., "father": .., "mother": ..} (only
    those present). The genuine bdris verification link carries none (an opaque key)."""
    tf = (facts.qr or {}).get("text_fields") or {}
    out = {"student": str(facts.qr_name or tf.get("name") or "")}
    for p in ("father", "mother"):
        out[p] = str(tf.get(p) or "")
    return {k: v for k, v in out.items() if v and strict.letters(v)}


def qr_parent_reads(facts: BirthFacts, unit: DocUnit) -> List[MachineRead]:
    """The parents' names a birth-certificate QR payload carries, as machine reads (A1: letters, never dots) for
    names.py: a parent's reference (this certificate's printed read) is confirmed by them when the letters agree
    (revision 2.3)."""
    if facts.qr_status != "decoded":
        return []
    page = int((facts.qr or {}).get("page", unit.pages[0] if unit.pages else 0))
    names = qr_names(facts)
    return [MachineRead(unit=unit.uid, person=p, name=names[p], source="birth QR", page=page)
            for p in ("father", "mother") if names.get(p)]


def decode_birth_qr(image, page: int = 0, dpi: int = IMAGE_DPI, budget_s: float = QR_BUDGET_S,
                    barcode: bool = True, use_zbar: bool = True) -> Dict[str, Any]:
    """Read the QR (and the barcode) of one birth-certificate page. `image` is a BGR array, a PNG/JPG path
    or a PDF (page `page` rendered at `dpi`). No image data in the result."""
    t0 = time.time()
    img = load_image(image, page=page, dpi=dpi)
    if img is None:
        return {"status": "absent", "payload": "", "kind": "", "stage": "unreadable image",
                "barcode": {"status": "undecoded", "type": "", "payload": ""}, "seconds": 0.0}
    payload, stage, seen = decode_qr_image(img, budget_s=budget_s, use_zbar=use_zbar)
    out: Dict[str, Any] = {"status": "decoded" if payload else ("undecoded" if seen else "absent"),
                           "payload": payload, "stage": stage}
    out.update(parse_payload(payload))
    if barcode:
        out["barcode"] = decode_barcode_image(img, use_zbar=use_zbar)
    out["seconds"] = round(time.time() - t0, 2)
    return out


def key_well_formed(qr: Dict[str, Any]) -> bool:
    """The genuine bdris key: 64 base64 characters = 48 bytes (three 16-byte cipher blocks), no spaces."""
    key = str(qr.get("key") or "")
    return bool(key) and " " not in key and qr.get("key_len") == 64 and qr.get("key_bytes") == 48


def official_link(qr: Dict[str, Any]) -> bool:
    """An https link to bdris.gov.bd / everify.bdris.gov.bd (exactly, or a sub-domain of them)."""
    host = str(qr.get("host") or "").lower()
    https = str(qr.get("payload") or "").strip().lower().startswith("https://")
    return https and any(host == h or host.endswith("." + h) for h in BDRIS_HOSTS)


def offline_match(qr: Dict[str, Any], printed_brn: str = "", dob: Optional[date] = None) -> Tuple[str, List[str]]:
    """The lab's offline tie of the codes to THIS printed certificate (without the key-reuse register):
    1. an https link to the government verify endpoint; 2. a well-formed key; 3. the barcode BRN equals the
    printed BRN; 4. the BRN's first four digits equal the year of the date of birth.
    Returns ("match" | "mismatch" | "undecoded" | "absent", reasons)."""
    status = str(qr.get("status") or "absent")
    if status != "decoded":
        return status, []
    bad: List[str] = []
    if not official_link(qr):
        bad.append("the QR does not point to the government bdris site")
    if qr.get("kind") == "bdris_verify_url" and not key_well_formed(qr):
        bad.append("the QR key is not in the bdris format (64 base64 characters)")
    bc = qr.get("barcode") or {}
    bdig = str(bc.get("payload") or "") if bc.get("status") == "decoded" else ""
    bdig = bdig if _brn_shaped(bdig) else ""
    if bdig and printed_brn and bdig != printed_brn:
        bad.append("the barcode's BRN differs from the printed BRN")
    brn = printed_brn or bdig
    if brn and dob is not None and brn[:4] != f"{dob.year:04d}":
        bad.append("the BRN's birth year differs from the printed date of birth")
    return ("mismatch", bad) if bad else ("match", [])


# ----------------------------------------------------------------------------------------------------------
# the boxed date of birth from the page image
def _box_xyxy(box) -> Tuple[float, float, float, float]:
    xs = [p[0] for p in box]
    ys = [p[1] for p in box]
    return min(xs), min(ys), max(xs), max(ys)


_TR_DIGITS = str.maketrans({"O": "0", "o": "0", "Q": "0", "D": "0", "I": "1", "l": "1", "i": "1", "|": "1",
                            "!": "1", "S": "5", "s": "5", "B": "8", "Z": "2", "z": "2", "G": "6", "q": "9",
                            "g": "9", "T": "7", "J": "1", "j": "1", "t": "1", "L": "1"})


def reread_boxed_dob(image_rgb, readtext: Callable[..., Any], line: Optional[Callable[[Any], str]] = None,
                     crop_png: Optional[Path] = None, top_fraction: float = 0.65) -> Dict[str, Any]:
    """The boxed DOB field of an old English bdris print, read off the page image (lab reread_boxed_dob).
    Whole-page OCR mostly skips the boxes (the grid lines defeat the text detector), so: find the "Date of
    Birth" label with readtext(detail=1), crop its row up to the "Sex" label, find the two BLACK BLOCKS (the
    separators of DD [block] MM [block] YYYY), white out the grid lines and the blocks, and read the digit
    groups (the field, larger and thresholded, and each group alone). `line(grey) -> str` reads one line of
    text (the engine's recogniser); without it readtext is used.
    Returns {'dob': date|None (>= 2 readings agree), 'candidates': [date], 'blocks': n, 'why': str, ...}."""
    np, cv2 = _np(), _cv2()
    out: Dict[str, Any] = {"dob": None, "candidates": [], "blocks": 0, "raw": "", "why": ""}
    img = image_rgb
    if img is None:
        out["why"] = "no image"
        return out
    H, W = img.shape[:2]
    res = readtext(img[:int(H * top_fraction)], detail=1)
    boxes = [(_box_xyxy(b), t or "") for b, t, _ in res]
    sexes = [bx for bx, t in boxes if re.match(r"\W*s[ea3][xk]", t, re.I)]
    cands = []
    for bx, t in boxes:
        if re.search(r"place|plac|pl[a-z]c|regis|issu", t, re.I):
            continue
        full = _DOB_LABEL.search(t)
        loose = re.search(r"d[ao][tl][ea]\W*\S{0,3}\W*[bk8]", t, re.I)
        if not (full or loose):
            continue
        x0, y0, x1, y1 = bx
        hh = max(8.0, y1 - y0)
        row_sex = [s for s in sexes if s[0] > x1 - hh and abs((s[1] + s[3]) / 2 - (y0 + y1) / 2) < hh]
        score = (2 if row_sex else 0) + (1 if full else 0)
        cands.append((score, -y0, bx, t, full, row_sex))
    if not cands:
        out["why"] = "no Date of Birth label found"
        return out
    cands.sort(key=lambda c: (c[0], c[1]), reverse=True)
    _, _, (lx0, ly0, lx1, ly1), ltext, full, row_sex = cands[0]
    h = max(8.0, ly1 - ly0)
    m = full or re.search(r"d[ao][tl][ea]\W*\S{0,3}\W*[bk8]\S*", ltext, re.I)
    if m and m.end() < len(ltext) - 2:          # a box that also swallowed the field reads past "Birth"
        lx1 = lx0 + (lx1 - lx0) * (m.end() / max(1, len(ltext)))
    xe = min(s[0] for s in row_sex) if row_sex else min(W, lx1 + 16 * h)
    cx0, cx1 = int(max(0, lx1 + 0.1 * h)), int(min(W, xe - 0.1 * h))
    cy0, cy1 = int(max(0, ly0 - 1.0 * h)), int(min(H, ly1 + 1.0 * h))
    if cx1 - cx0 < 4 * h or cy1 - cy0 < h:
        out["why"] = "field crop too small"
        return out
    crop = img[cy0:cy1, cx0:cx1]
    _save_png(crop_png, crop[:, :, ::-1] if crop.ndim == 3 else crop)
    gray = crop.mean(axis=2) if crop.ndim == 3 else crop.astype(float)
    lo, hi = float(np.percentile(gray, 2)), float(np.percentile(gray, 90))
    thr = min(150.0, lo + 0.5 * (hi - lo))
    dark = gray < thr

    def longest(row):
        best = cur = start = bs = 0
        for k, v in enumerate(row):
            if v:
                if cur == 0:
                    start = k
                cur += 1
                if cur > best:
                    best, bs = cur, start
            else:
                cur = 0
        return best, bs
    frame = []
    for r in range(dark.shape[0]):
        ln, st = longest(dark[r])
        if ln >= 3 * h:
            frame.append((r, st, st + ln))
    ncc, _, stc, _ = cv2.connectedComponentsWithStats(dark.astype(np.uint8), 8)
    grids = [tuple(int(v) for v in stc[k]) for k in range(1, ncc)
             if stc[k][2] >= 4 * h and 0.6 * h <= stc[k][3] <= 2.5 * h]
    if grids:
        gx, gy, gw, gh_, _ = max(grids, key=lambda g: g[2])
        r0, r1 = gy, gy + gh_ - 1
    elif len(frame) >= 2 and frame[-1][0] - frame[0][0] > 0.6 * h:
        r0, r1 = frame[0][0], frame[-1][0]
    else:
        r0, r1 = 0, dark.shape[0] - 1
    band = dark[r0:r1 + 1]
    cols = band.mean(axis=0) if band.size else np.zeros(dark.shape[1])
    solid = cols > 0.8
    runs: List[Tuple[int, int]] = []
    i = 0
    while i < len(solid):
        if solid[i]:
            j = i
            while j + 1 < len(solid) and solid[j + 1]:
                j += 1
            runs.append((i, j))
            i = j + 1
        else:
            i += 1
    cell_h = max(h, (r1 - r0))
    blocks = [r for r in runs if (r[1] - r[0] + 1) >= 0.3 * cell_h and cols[r[0]:r[1] + 1].mean() > 0.85]
    lines = [r for r in runs if r not in blocks]
    if len(blocks) < 2:
        # a light scan: the grid is grey, the blocks are still solid dark squares
        n0, _, st0, _ = cv2.connectedComponentsWithStats(dark.astype(np.uint8), 8)
        sq = []
        for k in range(1, n0):
            x, y, w_, h_, area = (int(v) for v in st0[k])
            if h_ >= 0.8 * h and w_ >= 0.3 * h and 0.3 <= w_ / h_ <= 1.5 and area / (w_ * h_) >= 0.75:
                sq.append((x, y, w_, h_))
        sq.sort()
        if len(sq) >= 2:
            blocks = [(x, x + w_ - 1) for x, y, w_, h_ in sq[:2]]
            r0 = min(y for x, y, w_, h_ in sq[:2])
            r1 = max(y + h_ - 1 for x, y, w_, h_ in sq[:2])
            cell_h = r1 - r0
            lines = [r for r in lines if r[1] - r[0] + 1 < 0.3 * cell_h]
    centres = sorted((a + b) / 2 for a, b in lines)
    diffs = [b - a for a, b in zip(centres, centres[1:]) if 0.4 * cell_h <= b - a <= 1.6 * cell_h]
    cw = sorted(diffs)[len(diffs) // 2] if diffs else 0.9 * cell_h
    edges = [x for r in runs for x in r] + [x for b in blocks for x in b]
    if edges:
        c0, c1 = int(max(0, min(edges) - cw)), int(min(dark.shape[1] - 1, max(edges) + cw))
    else:
        c0, c1 = 0, dark.shape[1] - 1
    out["blocks"] = len(blocks)
    clean = crop.copy()
    for a, b in lines + blocks:
        clean[:, max(0, a - 1):b + 2] = 255
    for r, _, _ in frame:
        clean[max(0, r - 1):r + 2, :] = 255
    clean[:max(0, r0 - 1), :] = 255
    clean[r1 + 2:, :] = 255
    clean[:, :max(0, c0 - 1)] = 255
    clean[:, c1 + 1:] = 255
    band_img = clean[r0:r1 + 1]
    ink = ((band_img.mean(axis=2) if band_img.ndim == 3 else band_img) < thr).astype(np.uint8)
    n, lab, stats, _ = cv2.connectedComponentsWithStats(ink, 8)
    glyphs = []
    for k in range(1, n):
        x, y, w_, h_, area = stats[k]
        if h_ < 0.2 * cell_h or h_ > 0.95 * cell_h or area < 15 or w_ > 0.9 * cell_h:
            continue
        glyphs.append((int(x), int(y), int(w_), int(h_), k))
    if glyphs:
        mh = sorted(g[3] for g in glyphs)[len(glyphs) // 2]
        glyphs = [g for g in glyphs if g[3] >= 0.6 * mh]
    glyphs.sort()
    merged: List[List[Any]] = []
    for x, y, w_, h_, k in glyphs:                  # merge blobs that overlap horizontally (a broken stroke)
        if merged and x < merged[-1][0] + merged[-1][2] - 0.2 * w_:
            m0 = merged[-1]
            x1_ = max(m0[0] + m0[2], x + w_)
            y0_ = min(m0[1], y)
            y1_ = max(m0[1] + m0[3], y + h_)
            m0[0], m0[1], m0[2], m0[3] = min(m0[0], x), y0_, x1_ - min(m0[0], x), y1_ - y0_
            m0[4].append(k)
        else:
            merged.append([x, y, w_, h_, [k]])
    gh = max([g[3] for g in merged] or [int(h)])
    if len(blocks) >= 2:
        cuts = [blocks[0][0], blocks[1][0]]
        grp = [[g for g in merged if g[0] + g[2] / 2 < cuts[0]],
               [g for g in merged if cuts[0] <= g[0] + g[2] / 2 < cuts[1]],
               [g for g in merged if g[0] + g[2] / 2 >= cuts[1]]]
    else:
        grp = [merged]
    if len(grp) == 3:
        grp = [grp[0][-2:], grp[1][-2:] if len(grp[1]) <= 2 else grp[1], grp[2][:4]]
    out["glyphs"] = [len(g) for g in grp]

    def compose(groups_, dash: bool):
        gap, side = max(2, int(0.25 * gh)), int(0.8 * gh)
        width = side * 2 + sum(sum(g[2] for g in gg) + gap * max(0, len(gg) - 1) for gg in groups_) \
            + (int(0.9 * gh) + 2 * gap) * (len(groups_) - 1)
        canvas = np.full((int(gh * 2.2), max(width, 4)), 255, dtype=np.uint8)
        x = side
        top = int(0.6 * gh)
        for gi, gg in enumerate(groups_):
            for j, (gx_, gy_, gw_, ghh, ks) in enumerate(gg):
                piece = np.isin(lab[gy_:gy_ + ghh, gx_:gx_ + gw_], ks)
                yoff = top + (gh - ghh)                    # bottom-aligned, like printed digits
                canvas[yoff:yoff + ghh, x:x + gw_][piece] = 0
                x += gw_ + (gap if j < len(gg) - 1 else 0)
            if gi < len(groups_) - 1:
                x += gap
                if dash:
                    cv2.line(canvas, (x, top + gh // 2), (x + int(0.9 * gh) - 1, top + gh // 2), 0,
                             max(2, int(0.14 * gh)))
                x += int(0.9 * gh) + gap
        return canvas

    def read_grey(grey) -> str:
        grey = cv2.resize(grey, None, fx=2, fy=2, interpolation=cv2.INTER_CUBIC)
        if line is not None:
            return line(grey)
        rr = sorted(readtext(cv2.cvtColor(grey, cv2.COLOR_GRAY2RGB), detail=1), key=lambda r: _box_xyxy(r[0])[0])
        return " ".join(str(t) for _, t, _ in rr)

    if not merged:
        out["why"] = "no digits found in the field"
        return out
    packed = compose(grp, True)
    variants = {"field": packed}
    if len(grp) == 3 and all(grp):
        for gi, g in enumerate(grp):
            variants[f"group{gi}"] = compose([g], False)
    texts = {k: read_grey(im) for k, im in variants.items()}
    big = cv2.resize(packed, None, fx=1.5, fy=1.5, interpolation=cv2.INTER_CUBIC)
    _, big = cv2.threshold(big, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    texts["field2"] = read_grey(big)
    out["raw"] = texts.get("field", "")
    want = [len(g) for g in grp] if len(grp) == 3 else []

    def split_field(t: str) -> Optional[List[str]]:
        flat = re.sub(r"\s+", "", t)
        mm = re.search(r"([0-9A-Za-z|!{}]{1,3})[-~_=.,:;/]+([0-9A-Za-z|!{}]{1,3})[-~_=.,:;/]+([0-9A-Za-z|!{}]{3,5})", flat)
        if mm:
            return [re.sub(r"[^0-9]", "", g.translate(_TR_DIGITS)) for g in mm.groups()]
        digs = re.sub(r"[^0-9]", "", re.sub(r"[-~_=]", "", flat).translate(_TR_DIGITS))
        if want == [2, 2, 4] and len(digs) == 8:
            return [digs[:2], digs[2:4], digs[4:]]
        return None

    per_group: List[List[str]] = [[], [], []]
    votes: List[Tuple[str, date]] = []
    for k in ("field", "field2"):
        parts_ = split_field(texts.get(k, ""))
        if parts_:
            for gi in range(3):
                per_group[gi].append(parts_[gi])
            if [len(x) for x in parts_] == [2, 2, 4] and want == [2, 2, 4]:
                v = _valid(int(parts_[2]), int(parts_[1]), int(parts_[0]))
                if v:
                    votes.append((k, v))
    if all(f"group{gi}" in texts for gi in range(3)):
        parts_ = [re.sub(r"[^0-9]", "", texts[f"group{gi}"].translate(_TR_DIGITS)) for gi in range(3)]
        for gi in range(3):
            per_group[gi].append(parts_[gi])
        if [len(x) for x in parts_] == [2, 2, 4] and want == [2, 2, 4]:
            v = _valid(int(parts_[2]), int(parts_[1]), int(parts_[0]))
            if v:
                votes.append(("groups", v))
    cands: List[date] = []
    for dd in sorted({x for x in per_group[0] if len(x) == 2}):
        for mo in sorted({x for x in per_group[1] if len(x) == 2}):
            for yy in sorted({x for x in per_group[2] if len(x) == 4}):
                v = _valid(int(yy), int(mo), int(dd))
                if v and v not in cands:
                    cands.append(v)
    out["candidates"] = cands
    out["votes"] = [(k, v.isoformat()) for k, v in votes]
    tally: Dict[date, int] = {}
    for _, v in votes:
        tally[v] = tally.get(v, 0) + 1
    if tally:
        best = max(tally, key=lambda d: tally[d])
        if tally[best] >= 2 and sum(1 for d in tally if tally[d] >= tally[best]) == 1:
            out["dob"] = best
            out["why"] = f"{tally[best]} of {len(votes)} readings agree"
            return out
    out["why"] = ("readings disagree" if len(tally) > 1 else "one reading only" if tally else
                  "blocks found, digits unreadable" if len(blocks) >= 2 else "no block-separated date")
    return out


def find_dob_row(img) -> Optional[Dict[str, Any]]:
    """The two solid black blocks side by side on one line (on the bdris print they exist only in the
    date-of-birth row) and from them the three digit groups: DD left of block 1, MM between the blocks, YYYY
    right of block 2 (cell width = half the gap between the blocks). Pure layout (lab dob_boxes.py)."""
    np, cv2 = _np(), _cv2()
    gray = _gray(img)
    h, w = gray.shape
    _, b = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
    # an opening with a square ~1% of the page width wipes out text strokes and the thin cell walls and
    # leaves only solid areas: the black blocks (and seals, photos, QR finders: filtered below)
    k = max(3, int(0.010 * w))
    solid = cv2.morphologyEx(b, cv2.MORPH_OPEN, np.ones((k, k), np.uint8))
    n, _lab, stats, _ = cv2.connectedComponentsWithStats(solid, 8)
    cands = []
    for i in range(1, n):
        x, y, bw, bh, area = stats[i]
        if not (0.012 * h <= bh <= 0.05 * h and 0.012 * w <= bw <= 0.045 * w):
            continue
        if area / float(bw * bh) >= 0.85 and 0.8 <= bh / float(bw) <= 2.6:
            cands.append((int(x), int(y), int(bw), int(bh)))
    best = None
    for a in cands:
        for c in cands:
            if c[0] <= a[0]:
                continue
            same_line = abs((a[1] + a[3] / 2) - (c[1] + c[3] / 2)) < 0.3 * a[3]
            same_size = 0.75 < c[3] / a[3] < 1.33 and 0.66 < c[2] / a[2] < 1.5
            gap = c[0] - (a[0] + a[2])
            if not (same_line and same_size and 1.0 * a[2] <= gap <= 3.5 * a[2]):
                continue
            if best is None or gap < best[0]:
                best = (gap, a, c)
    if best is None:
        return None
    _, a, c = best
    cell = (c[0] - (a[0] + a[2])) / 2.0
    top = int(min(a[1], c[1]) - 0.15 * a[3])
    bot = int(max(a[1] + a[3], c[1] + c[3]) + 0.15 * a[3])
    pad = int(0.12 * cell)
    groups = {"dd": (int(a[0] - 2 * cell - pad), int(a[0] - pad // 2)),
              "mm": (int(a[0] + a[2] + pad // 2), int(c[0] - pad // 2)),
              "yyyy": (int(c[0] + c[2] + pad // 2), int(c[0] + c[2] + 4 * cell + pad))}
    return {"blocks": [a, c], "cell": cell, "top": max(0, top), "bottom": min(h, bot), "groups": groups}


def _strip_walls(gray):
    """Paint the cell walls (long thin vertical / horizontal dark lines) white; stretch the ink to black."""
    np, cv2 = _np(), _cv2()
    b = (gray < 200).astype(np.uint8) * 255
    hh, ww = b.shape
    vert = cv2.morphologyEx(b, cv2.MORPH_OPEN, np.ones((max(3, int(hh * 0.45)), 1), np.uint8))
    horiz = cv2.morphologyEx(b, cv2.MORPH_OPEN, np.ones((1, max(3, int(ww * 0.45))), np.uint8))
    walls = cv2.dilate(vert | horiz, np.ones((3, 3), np.uint8))
    out = gray.copy()
    out[walls > 0] = 255
    ink = out < 200
    return np.where(ink, np.clip(out.astype(np.int32) - 80, 0, 255), 255).astype(np.uint8)


def read_dob_groups(img, row: Dict[str, Any], recognize: Callable[[Any], str]) -> Tuple[str, str, str]:
    """Each digit group cropped with a margin, cell walls removed, handed to `recognize(grey) -> str` (one
    line); read cell by cell when the group does not give the right number of digits."""
    cv2 = _cv2()
    gray = _gray(img)
    top, bot = row["top"], row["bottom"]
    out = []
    for key, n_cells in (("dd", 2), ("mm", 2), ("yyyy", 4)):
        x0, x1 = row["groups"][key]
        x0, x1 = max(0, x0), min(gray.shape[1], x1)
        if x1 - x0 < 2 or bot - top < 2:
            out.append("")
            continue
        crop = _strip_walls(gray[top:bot, x0:x1])
        got = "".join(ch for ch in recognize(cv2.copyMakeBorder(crop, 8, 8, 12, 12, cv2.BORDER_CONSTANT, value=255))
                      if ch.isdigit())
        if len(got) != n_cells:
            # CTC merges a doubled digit ("1 1" -> "1") and drops faint ones: read cell by cell instead
            step = crop.shape[1] / float(n_cells)
            cells = []
            for i in range(n_cells):
                c = crop[:, int(i * step):int((i + 1) * step)]
                c = cv2.copyMakeBorder(c, 8, 8, 16, 16, cv2.BORDER_CONSTANT, value=255)
                d = "".join(ch for ch in recognize(c) if ch.isdigit())
                cells.append(d[:1])
            if all(cells):
                got = "".join(cells)
        out.append(got)
    return out[0], out[1], out[2]


# ----------------------------------------------------------------------------------------------------------
# the engine adapters (Task 1's Engine: page_image_of, read_boxes, cut_page; bot.dv._reader)
def _line_reader(eng) -> Callable[[Any], str]:
    """One line of text from a grey crop: the engine reader's own recogniser on the whole crop (no text
    detection) when the engine exposes it (bot.dv._reader.recognize), else read_boxes left to right."""
    cv2 = _cv2()
    rec = getattr(getattr(getattr(eng, "bot", None), "dv", None), "_reader", None)
    fn = getattr(rec, "recognize", None)
    if callable(fn):
        def line(grey) -> str:
            h, w = grey.shape[:2]
            return " ".join(str(r[1]) for r in fn(grey, [[0, w, 0, h]], []))
        return line

    def line_boxes(grey) -> str:
        rgb = cv2.cvtColor(grey, cv2.COLOR_GRAY2RGB) if grey.ndim == 2 else grey
        res = sorted(eng.read_boxes(rgb), key=lambda r: _box_xyxy(r[0])[0])
        return " ".join(str(t) for _, t, _ in res)
    return line_boxes


def _safe_dir(work_dir: Optional[Path]) -> Optional[Path]:
    """work_dir when it lies under private-runner (S3), else None (nothing is written then)."""
    if work_dir is None:
        return None
    try:
        p = Path(work_dir).resolve()
    except OSError:
        return None
    return p if p.is_relative_to(PRIVATE) else None


def _save_png(path: Optional[Path], img) -> None:
    if path is None or img is None:
        return
    try:
        cv2 = _cv2()
        path.parent.mkdir(parents=True, exist_ok=True)
        ok, buf = cv2.imencode(".png", img)
        if ok:
            buf.tofile(str(path))
    except CODE_FAULTS:
        raise
    except Exception:                                   # a review crop is a bonus, never fatal
        pass


def _save_json(path: Optional[Path], obj: Dict[str, Any]) -> None:
    if path is None:
        return
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(obj, indent=1, default=str), encoding="utf-8")
    except CODE_FAULTS:
        raise
    except Exception:
        pass


class _Images:
    """Page images through the engine, read once per run page (the engine memoises too)."""

    def __init__(self, eng, pages: Sequence[PageRead]):
        self.eng, self.pages, self.cache = eng, pages, {}

    def rgb(self, i: int):
        if self.eng is None or not hasattr(self.eng, "page_image_of"):
            return None
        if i not in self.cache:
            try:
                self.cache[i] = self.eng.page_image_of(self.pages[i], IMAGE_DPI)
            except CODE_FAULTS:
                raise
            except Exception:
                self.cache[i] = None
        return self.cache[i]


# ----------------------------------------------------------------------------------------------------------
# the date of birth on one page
def _dob_on_page(text: str, kind: str, image_rgb=None, eng=None, crop_png: Optional[Path] = None) -> Dict[str, Any]:
    """{'sure': [(source, date)], 'maybe': [date], 'words': date|None, 'partial': str, 'reread': dict|None}.
    sure: the label, a boxes reading that fits one date only, the image re-read when its readings agree;
    maybe: what the boxes could say (for the words line to confirm)."""
    res: Dict[str, Any] = {"sure": [], "maybe": [], "words": None, "partial": "", "reread": None}
    w, p = in_words_reading(text)
    res["words"], res["partial"] = w, p
    if kind == "english_bdris":
        got = extract_dob_boxed(text)
        if len(got) == 1:
            res["sure"].append(("boxes", got[0]))
        res["maybe"] += got
        if got:
            return res
        lab = labelled_dob(text)                   # another old layout prints the date plainly
        if lab:
            res["sure"].append(("label", lab))
            return res
        if image_rgb is not None and eng is not None and hasattr(eng, "read_boxes"):
            res["reread"] = _reread_image(image_rgb, eng, crop_png)
            if res["reread"].get("dob"):
                res["sure"].append(("boxes", res["reread"]["dob"]))
            res["maybe"] += [c for c in res["reread"].get("candidates", []) if c not in res["maybe"]]
        return res
    lab = labelled_dob(text)
    if lab:
        res["sure"].append(("label", lab))
    return res


def _reread_image(image_rgb, eng, crop_png: Optional[Path]) -> Dict[str, Any]:
    """The image re-read of the boxed field: the label-anchored reader, then the black-block layout reader.
    A layout read (one reading only) is a candidate: it needs the words line, or stands alone untrusted."""
    out: Dict[str, Any] = {"dob": None, "candidates": [], "why": ""}
    line = _line_reader(eng)
    try:
        rr = reread_boxed_dob(image_rgb, lambda img, detail=1: eng.read_boxes(img), line=line, crop_png=crop_png)
        out.update(dob=rr.get("dob"), candidates=list(rr.get("candidates") or []), why=rr.get("why", ""),
                   blocks=rr.get("blocks", 0))
    except CODE_FAULTS:
        raise
    except Exception as e:                                  # the re-read is a bonus, never fatal
        out["why"] = f"label re-read failed: {type(e).__name__}"
    if out["dob"] is None and not out["candidates"]:
        try:
            bgr = _bgr(image_rgb)
            row = find_dob_row(bgr)
            if row:
                dd, mm, yy = read_dob_groups(bgr, row, lambda g: line(g).translate(_TR_DIGITS))
                v = _valid(int(yy), int(mm), int(dd)) if (len(dd) == 2 and len(mm) == 2 and len(yy) == 4) else None
                out["layout"] = "read" if v else "row found, digits unreadable"
                if v:
                    out["candidates"].append(v)
            else:
                out["layout"] = "no black-block row"
        except CODE_FAULTS:
            raise
        except Exception as e:
            out["layout"] = f"failed: {type(e).__name__}"
    return out


def read_dob(text: str, image=None, eng=None, kind: Optional[str] = None) -> List[Tuple[str, date]]:
    """(source, date) readings of the date of birth on one page: "label" | "boxes" | "words". A boxes
    candidate counts only when it fits one date, or when the words line on the page confirms it. Never an
    issue or registration date. `image` (RGB) and `eng` enable the image re-read of the boxed field."""
    r = _dob_on_page(text, kind or page_kind(text), image, eng)
    out = list(r["sure"])
    if r["words"]:
        for d in r["maybe"]:
            if d == r["words"] and ("boxes", d) not in out:
                out.append(("boxes", d))
        out.append(("words", r["words"]))
    return out


def _resolve_dob(per_page: List[Tuple[int, Dict[str, Any]]], f: BirthFacts) -> None:
    """The certificate's date of birth from every English page's readings (A3): the boxes or the label
    (primary) and the words line (cross-check). Agree -> trusted; disagree -> None ("DOB unclear"); one
    source only -> set, untrusted."""
    sure = [(s, d, i) for i, r in per_page for s, d in r["sure"]]
    maybe = [(d, i) for i, r in per_page for d in r["maybe"]]
    words = [("words", r["words"], i) for i, r in per_page if r["words"]]
    wset = {d for _, d, _ in words}
    primary = list(sure)
    for d, i in maybe:
        if d in wset and not any(s == "boxes" and x == d for s, x, _ in primary):
            primary.append(("boxes", d, i))
    readings = primary + words
    if maybe and not any(d in wset for d, _ in maybe) and not any(s == "boxes" for s, _, _ in sure):
        # what the boxes could say, none of it confirmed: shown, and a disagreement when the words read
        # something else (A3: boxes and words disagree -> DOB unclear)
        readings += [("boxes", d, i) for d, i in dict.fromkeys(maybe)]
    f.dob_readings = [(s, d.isoformat(), i) for s, d, i in readings]
    dates = {d for _, d, _ in readings}
    if len(dates) != 1:
        if len(dates) > 1:
            f.notes.append("the date of birth readings disagree")
        f.dob, f.dob_trusted, f.dob_source = None, False, ""
        return
    f.dob = next(iter(dates))
    psrc = [s for s, _, _ in primary]
    if primary and words:
        f.dob_trusted = True
        f.dob_source = ("boxes" if "boxes" in psrc else "label") + "+words"
    else:
        f.dob_trusted = False
        f.dob_source = ("boxes" if "boxes" in psrc else "label") if primary else ("words" if words else "boxes")


# ----------------------------------------------------------------------------------------------------------
# reading one birth-certificate unit
def read_birth_certificate(unit: DocUnit, pages: List[PageRead], eng: Optional[Any], work_dir: Optional[Path],
                           notarised: Callable[[str, Optional[Path]], bool]) -> BirthFacts:
    """Everything the A3 check needs from one birth-certificate unit (its run pages in order).
    notarised(text, path): the bot's dv.notarised (wording OR seal); `path` is a one-page cut of the page
    (eng.cut_page) so the seal is looked for on that page only. Page images: eng.page_image_of(page, 300).
    Without eng: text only (no QR, no image re-read of the boxes, no seal)."""
    idx = list(unit.pages)
    f = BirthFacts(pages=tuple(idx))
    aux: Dict[str, Any] = {"input_page": {i: pages[i].file_page + 1 for i in idx}, "brn_page": None}
    f._aux = aux                                           # not a contract field: page numbers for messages
    wd = _safe_dir(work_dir)
    images = _Images(eng, pages)
    texts = {i: pages[i].text or "" for i in idx}
    kinds0 = [page_kind(texts[i]) for i in idx]
    f.format, f.variant, kinds = _format_of(kinds0)
    f.page_kinds = kinds
    kind_of = dict(zip(idx, kinds))
    f.has_translation = "translation" in kinds
    f.english_pages = sorted([i for i in idx if kind_of[i] in ENGLISH_ORDER],
                             key=lambda i: (ENGLISH_ORDER[kind_of[i]], idx.index(i)))

    # the date of birth (never an issue / registration date)
    per_page = []
    for i in f.english_pages:
        k = kind_of[i]
        img = images.rgb(i) if k == "english_bdris" and not extract_dob_boxed(texts[i]) \
            and not labelled_dob(texts[i]) else None
        crop = wd / f"birth_dob_p{i + 1:02d}.png" if wd is not None else None
        r = _dob_on_page(texts[i], k, img, eng, crop)
        if r.get("reread") is not None:
            f.notes.append(f"boxed date re-read on page {aux['input_page'][i]}: {r['reread'].get('why', '')}"
                           + (f"; layout {r['reread']['layout']}" if r["reread"].get("layout") else ""))
        per_page.append((i, r))
    _resolve_dob(per_page, f)
    issue = reg = None
    for i in f.english_pages:
        a, b = issue_and_registration(texts[i], kind_of[i])
        issue = issue or a
        reg = reg or b
    f.issue_date, f.registration_date = issue, reg

    # the BRN: read alike on two pages = confirmed (the zoomed re-read can confirm it in the check)
    brns = [(i, extract_brn(texts[i], kind_of[i])) for i in f.english_pages]
    brns = [(i, b) for i, b in brns if b]
    if brns:
        f.brn, aux["brn_page"] = brns[0][1], brns[0][0]
        f.brn_confirmed = sum(1 for _, b in brns if b == f.brn) >= 2

    _notarisation(f, idx, kind_of, texts, pages, eng, wd, images, notarised)
    if f.format == "bilingual_qr":
        _read_qr(f, [i for i in idx if kind_of[i] == "bilingual_qr"], images, wd, aux)
    return f


def _notarisation(f: BirthFacts, idx: List[int], kind_of: Dict[int, str], texts: Dict[int, str],
                  pages: List[PageRead], eng, wd: Optional[Path], images: _Images,
                  notarised: Callable[[str, Optional[Path]], bool]) -> None:
    """Notarised pages, and pages with only a seal colour. A seal ALONE on the certificate's own print is not
    counted (the registrar's office seal looks the same); a Bangla original's notary must be on its
    translation (the original's own seal is the registrar's)."""
    inp = f._aux["input_page"]
    for i in idx:
        kind = kind_of[i]
        path = None
        if eng is not None and wd is not None and hasattr(eng, "cut_page"):
            try:
                path = eng.cut_page(pages[i], wd)
            except CODE_FAULTS:
                raise
            except Exception as e:
                f.notes.append(f"page {inp[i]}: no one-page cut for the seal test ({type(e).__name__})")
        try:
            yes = bool(notarised(texts[i], path))
        except CODE_FAULTS:
            raise
        except Exception as e:                               # a seal test that could not run
            f.notes.append(f"notarisation test failed on page {inp[i]}: {type(e).__name__}")
            yes = False
        if not yes and path is None:
            yes = seal_on_image(_bgr(images.rgb(i)))         # no page file: the same colour test on the image
        if not yes:
            continue
        wording = notary_wording(texts[i])
        if f.format == "bangla_original" and kind == "bangla":
            if wording:
                f.notes.append(f"notary wording on the Bangla original (page {inp[i]})")
                f._aux.setdefault("original_notary", []).append(i)
            continue                                         # the original's seal is the registrar's
        if wording or kind not in CERT_PRINT_KINDS:
            f.notary_pages.append(i)
        else:
            f.seal_only_pages.append(i)
    f.notarised = bool(f.notary_pages)
    if f.notarised:
        f.seal_only_pages = []


def _read_qr(f: BirthFacts, qr_pages: List[int], images: _Images, wd: Optional[Path], aux: Dict[str, Any]) -> None:
    """The QR and the barcode of the bilingual print, on its own page(s) only (never a translation
    centre's code). qr_status: decoded on any page > undecoded (a QR seen) > absent."""
    if not qr_pages or images.eng is None:
        f.qr_status = "undecoded"
        f.qr = {"status": "undecoded", "reason": "no page image (text only)"}
        return
    best: Optional[Dict[str, Any]] = None
    rank = {"decoded": 2, "undecoded": 1, "absent": 0}
    for i in qr_pages:
        rgb = images.rgb(i)
        if rgb is None:
            r = {"status": "undecoded", "reason": "page image not available"}
        else:
            try:
                r = decode_birth_qr(_bgr(rgb), budget_s=QR_BUDGET_S)
            except CODE_FAULTS:
                raise
            except Exception as e:
                r = {"status": "undecoded", "reason": f"decode failed: {type(e).__name__}"}
        r["page"] = i
        _save_json(wd / f"birth_qr_p{i + 1:02d}.json" if wd is not None else None,
                   {k: v for k, v in r.items() if k not in ("payload", "key", "params")})
        if best is None or rank.get(r.get("status"), 0) > rank.get(best.get("status"), 0) or \
                (best.get("status") == r.get("status") and not (best.get("barcode") or {}).get("payload")
                 and (r.get("barcode") or {}).get("payload")):
            best = r
    q = dict(best or {"status": "absent"})
    q["https"] = str(q.get("payload") or "").strip().lower().startswith("https://")
    q["official"] = official_link(q) if q.get("status") == "decoded" else False
    f.qr = q
    f.qr_status = str(q.get("status") or "absent")
    tf = q.get("text_fields") or {}
    f.qr_name = str(tf.get("name") or "")
    f.qr_dob = _parse_date(str(tf.get("dob") or "")) if tf.get("dob") else None
    bc = q.get("barcode") or {}
    if tf.get("brn"):
        f.qr_brn = str(tf["brn"])
        aux["qr_brn_from"] = "QR"
    elif bc.get("status") in ("decoded", "weak") and _brn_shaped(str(bc.get("payload") or "")):
        f.qr_brn = str(bc["payload"])
        aux["qr_brn_from"] = "barcode"
    elif bc.get("status") == "conflict":
        f.notes.append("the barcode reads two different numbers equally often — not used")


def qr_machine_read(facts: BirthFacts, unit: DocUnit) -> Optional[MachineRead]:
    """The student's name a birth-certificate QR payload carries, as a machine read for names.py (A1), or
    None (the genuine bdris QR carries no name)."""
    if not facts.qr_name:
        return None
    page = int((facts.qr or {}).get("page", unit.pages[0] if unit.pages else 0))
    return MachineRead(unit=unit.uid, person="student", name=facts.qr_name, source="birth QR", page=page)


# ----------------------------------------------------------------------------------------------------------
# the A3 check
def _inp(facts: BirthFacts, i: int, pages: Optional[Sequence[PageRead]] = None) -> int:
    """1-based input page of run page i (bundle page, or the page of the folder file)."""
    if pages is not None and 0 <= i < len(pages):
        return pages[i].file_page + 1
    m = (getattr(facts, "_aux", None) or {}).get("input_page") or {}
    return int(m.get(i, i + 1))


def _pages_text(nums: Sequence[int]) -> str:
    nums = sorted(dict.fromkeys(nums))
    if not nums:
        return ""
    return ("page " if len(nums) == 1 else "pages ") + ", ".join(str(n) for n in nums)


def check_birth_certificate(facts: BirthFacts, unit: DocUnit, ident: Identity, printed_name: str = "",
                            printed_confirmed: bool = False,
                            confirm: Optional[Callable[[int, str], str]] = None,
                            pages: Optional[Sequence[PageRead]] = None) -> List[Finding]:
    """The A3 rows for one birth-certificate unit: birth.format, birth.notary, birth.qr (+ birth.qr_name,
    birth.qr_brn, birth.qr_dob; bilingual print only), birth.dob and birth.brn_year.
    printed_name / printed_confirmed: the student's name as names.printed_student_name() read it on the
    English pages (the QR-name rule only); confirm: the shared targeted re-read (BRN confirmation).
    `pages` (optional) maps run pages to input pages; read_birth_certificate() already stored that map.
    Names are not judged here: names.py reads them from facts.english_pages."""
    out: List[Finding] = []
    P = lambda i: _inp(facts, i, pages)                    # noqa: E731
    all_pages = tuple(P(i) for i in (facts.pages or tuple(unit.pages)))

    def add(verdict: str, rule: str, message: str, pg: Sequence[int] = (), **ev) -> None:
        out.append(Finding(verdict, rule, message, SOURCE, tuple(sorted(dict.fromkeys(pg))) or all_pages,
                           dict(ev)))

    _format_rows(facts, add, P)
    _notary_rows(facts, add, P)
    if facts.format == "bilingual_qr":
        _qr_rows(facts, unit, add, P, printed_name, printed_confirmed, confirm)
        _qr_parent_rows(facts, unit, ident, add, P)
    _dob_rows(facts, ident, add, P)
    brn = facts.brn or facts.qr_brn
    if brn and len(brn) == 17 and facts.dob is not None and brn[:4] != f"{facts.dob.year:04d}":
        bp = (getattr(facts, "_aux", None) or {}).get("brn_page")
        add(FLAG, "birth.brn_year", f"the birth registration number does not start with the birth year "
            f"{facts.dob.year} (it starts {brn[:4]}) — check by eye", [P(bp)] if bp is not None else (),
            brn_year=brn[:4], dob_year=facts.dob.year)
    return out


def _format_rows(facts: BirthFacts, add, P) -> None:
    fm, var = facts.format, facts.variant
    eng_p = [P(i) for i in facts.english_pages]
    if fm == "bangla_original":
        if facts.has_translation:
            add(PASS, "birth.format", "Bangla-only certificate with its English translation", format=fm)
        else:
            add(FAIL, "birth.format", "Bangla-only certificate with no English translation — it must be "
                "translated and notarised", format=fm)
    elif fm == "bilingual_qr":
        add(PASS, "birth.format", "new bilingual bdris certificate — needs no translation", format=fm)
    elif fm == "english_bdris":
        what = " (record-verification print)" if var == "everify" else ""
        add(PASS, "birth.format", f"older English bdris print{what} — needs no translation", eng_p,
            format=fm, variant=var)
    elif fm == "english_translation":
        add(FLAG, "birth.format", "only an English translation; the original was not found — check by eye",
            eng_p, format=fm)
    elif var == "manual_english":
        add(FLAG, "birth.format", "older hand-issued certificate, not a bdris print — check by eye", eng_p,
            format=fm, variant=var)
    else:
        add(FLAG, "birth.format", "birth certificate format not recognised — check by eye", format=fm,
            kinds=list(facts.page_kinds))


def _notary_rows(facts: BirthFacts, add, P) -> None:
    if facts.notarised:
        add(PASS, "birth.notary", f"notarised ({_pages_text([P(i) for i in facts.notary_pages])})",
            [P(i) for i in facts.notary_pages])
        return
    orig = (getattr(facts, "_aux", None) or {}).get("original_notary") or []
    if facts.format == "bangla_original" and orig:
        add(FLAG, "birth.notary", f"notary wording is on the Bangla original ({_pages_text([P(i) for i in orig])}) "
            "but not on its English translation — check by eye", [P(i) for i in orig])
    elif facts.seal_only_pages:
        add(FLAG, "birth.notary", "a seal but no notary wording — check that it is a notary's seal "
            f"({_pages_text([P(i) for i in facts.seal_only_pages])})", [P(i) for i in facts.seal_only_pages])
    else:
        add(FAIL, "birth.notary", "no notarisation found — the birth certificate must be notarised")


def _qr_rows(facts: BirthFacts, unit: DocUnit, add, P, printed_name: str, printed_confirmed: bool,
             confirm: Optional[Callable[[int, str], str]]) -> None:
    q = facts.qr or {}
    qp = q.get("page")
    pg = [P(qp)] if isinstance(qp, int) else [P(i) for i, k in zip(facts.pages, facts.page_kinds) if k == "bilingual_qr"]
    if facts.qr_status != "decoded":
        why = {"absent": " (no QR found on the page)"}.get(facts.qr_status, "")
        if q.get("reason"):
            why = f" ({q['reason']})"
        add(FLAG, "birth.qr", f"the QR could not be decoded{why} — check the QR by eye", pg,
            status=facts.qr_status)
    else:
        host = str(q.get("host") or "?")
        if not official_link(q):
            if q.get("kind") == "text" or not q.get("host"):
                how = "it holds text, not a link"
            elif any(host == h or host.endswith("." + h) for h in BDRIS_HOSTS):
                how = "it is not an https link"
            else:
                how = f"it points to {host}"
            add(FAIL, "birth.qr", f"the QR does not point to the government bdris site ({how})", pg,
                host=host, https=bool(q.get("https")))
        elif q.get("kind") == "bdris_verify_url":
            if key_well_formed(q):
                add(PASS, "birth.qr", "QR is a genuine-looking bdris verification link", pg, host=host,
                    key_len=q.get("key_len"), key_bytes=q.get("key_bytes"))
                add(NOTE, "birth.qr", "the name behind the bdris QR can only be checked online — not done "
                    "(no network)", pg)
            else:
                add(FLAG, "birth.qr", f"the QR's key is not in the bdris format ({q.get('key_len')} characters"
                    f"{', with a space' if ' ' in str(q.get('key') or '') else ''}; a genuine key has 64) — "
                    "check by eye", pg, key_len=q.get("key_len"), key_bytes=q.get("key_bytes"))
        elif q.get("kind") == "bdris_everify_url":
            add(PASS, "birth.qr", "QR is a genuine-looking bdris verification link (older everify format)", pg,
                host=host)
            add(NOTE, "birth.qr", "the name behind the bdris QR can only be checked online — not done "
                "(no network)", pg)
        else:
            add(FLAG, "birth.qr", "the QR points to the bdris site but not to a verification link — check by "
                "eye", pg, host=host, path=str(q.get("path") or ""))
    # a name the payload carries (A1: a machine read; letters only)
    if facts.qr_name:
        if not printed_name:
            add(FLAG, "birth.qr_name", "the QR carries a name, but the printed name could not be read — "
                "compare them by eye", pg)
        else:
            kind = strict.compare(facts.qr_name, printed_name, dots=False)
            if kind == "EQUAL":
                add(PASS, "birth.qr_name", "the name in the QR is the printed name", pg)
            elif strict.judge(kind, covered=False, confirmed=printed_confirmed) == FAIL:
                add(FAIL, "birth.qr_name", "the name in the QR is not the printed name — the certificate may be "
                    "altered", pg, kind=kind, qr_name=facts.qr_name, printed=printed_name)
            else:
                add(FLAG, "birth.qr_name", f"the name in the QR differs from the printed name ({kind}) — check by "
                    f"eye, {_pages_text(pg)}", pg, kind=kind, qr_name=facts.qr_name, printed=printed_name)
    # a BRN the payload or the barcode carries
    if facts.qr_brn:
        src = (getattr(facts, "_aux", None) or {}).get("qr_brn_from") or "barcode"
        bp = (getattr(facts, "_aux", None) or {}).get("brn_page")
        bpg = [P(bp)] if bp is not None else pg
        if not facts.brn:
            add(NOTE, "birth.qr_brn", f"the {src} carries a BRN; the printed BRN could not be read to compare it",
                pg)
        elif facts.qr_brn == facts.brn:
            add(PASS, "birth.qr_brn", f"the {src}'s BRN is the printed BRN", pg)
        else:
            sure_code = src == "QR" or (q.get("barcode") or {}).get("status") == "decoded"
            confirmed = facts.brn_confirmed
            if not confirmed and confirm is not None and bp is not None:
                try:
                    confirmed = confirm(bp, facts.brn) == "confirmed"
                except CODE_FAULTS:
                    raise
                except Exception:
                    confirmed = False
                if confirmed:
                    facts.brn_confirmed = True
            if confirmed and sure_code:
                add(FAIL, "birth.qr_brn", f"the {src}'s BRN is not the printed BRN — the certificate may be "
                    "altered", sorted(set(pg + bpg)), code_brn=facts.qr_brn, printed_brn=facts.brn)
            else:
                add(FLAG, "birth.qr_brn", f"the {src}'s BRN differs from the printed BRN — check by eye, "
                    f"{_pages_text(sorted(set(pg + bpg)))}", sorted(set(pg + bpg)), code_brn=facts.qr_brn,
                    printed_brn=facts.brn, printed_confirmed=confirmed,
                    code_status=(q.get("barcode") or {}).get("status", ""))
    # a date of birth the payload carries
    if facts.qr_dob is not None:
        if facts.dob is None:
            add(FLAG, "birth.qr_dob", f"the QR carries a date of birth ({facts.qr_dob.isoformat()}) but the "
                "certificate's could not be read for sure — compare them by eye", pg)
        elif facts.qr_dob == facts.dob:
            add(PASS, "birth.qr_dob", "the QR's date of birth is the printed one", pg)
        elif facts.dob_trusted:
            add(FAIL, "birth.qr_dob", f"the QR's date of birth {facts.qr_dob.isoformat()} is not the printed "
                f"{facts.dob.isoformat()} — the certificate may be altered", pg)
        else:
            add(FLAG, "birth.qr_dob", f"the QR's date of birth {facts.qr_dob.isoformat()} differs from the "
                f"printed {facts.dob.isoformat()} (one reading) — check by eye", pg)


def _qr_parent_rows(facts: BirthFacts, unit: DocUnit, ident: Optional[Identity], add, P) -> None:
    """Revision 2.3: a decoded payload that carries a parent's name must match the certificate's printed read of it
    (identity's reference, when it was read off this certificate): the same letters (any order, dots aside) ->
    PASS, and names.py takes the QR as the independent read that confirms the reference; else FLAG "check by
    eye" (the printed side is a whole-page OCR read, so never FAIL here; names.py then treats the reference as
    unconfirmed)."""
    if facts.qr_status != "decoded":
        return
    q = facts.qr or {}
    qp = q.get("page")
    pg = [P(qp)] if isinstance(qp, int) else []
    found = qr_names(facts)
    for person in ("father", "mother"):
        name = found.get(person)
        if not name:
            continue
        ref = getattr(ident, person, None) if ident is not None else None
        printed = (ref.written or " ".join(ref.parts)) if (ref is not None and ref.unit == unit.uid
                                                           and ref.source == "birth certificate") else ""
        if not strict.letters(printed):
            add(FLAG, f"birth.qr_{person}", f"the QR carries the {person}'s name, but the printed one could not be "
                "read — compare them by eye", pg)
            continue
        kind = strict.compare(name, printed, any_order=True, dots=False)
        if kind == "EQUAL":
            add(PASS, f"birth.qr_{person}", f"the {person}'s name in the QR is the printed name", pg)
        else:
            add(FLAG, f"birth.qr_{person}", f"the {person}'s name in the QR differs from the printed read ({kind}) — "
                f"check by eye{', ' + _pages_text(pg) if pg else ''}", pg, kind=kind, qr_name=name, printed=printed)


def _dob_rows(facts: BirthFacts, ident: Identity, add, P) -> None:
    reads = [(s, d, i) for s, d, i in facts.dob_readings]
    rpages = [P(i) for _, _, i in reads]
    # the passport's date of birth; a date identity took from this very certificate is no passport date
    pdob = ident.dob if (ident is not None and ident.dob is not None and ident.dob_source != "birth certificate") \
        else None
    psrc = (ident.dob_source or "passport") if pdob is not None else ""
    ptrusted = bool(pdob is not None and ident.dob_trusted)
    if facts.dob is None:
        if not reads:
            add(FLAG, "birth.dob", "the date of birth could not be read — compare it with the passport by eye")
            return
        values = sorted({d for _, d, _ in reads})
        shown = ", ".join(f"{d} ({'/'.join(sorted({s for s, x, _ in reads if x == d}))})" for d in values)
        has = "; one reading is the passport's" if pdob is not None and pdob.isoformat() in values else ""
        add(FLAG, "birth.dob", f"DOB unclear, check by eye ({_pages_text(rpages)}): the certificate reads "
            f"{shown}{has}", rpages, readings=reads)
        return
    src = facts.dob_source or "?"
    two = src.replace("+", " and ") + " agree" if facts.dob_trusted else f"{src}, one reading"
    if pdob is None:
        add(FLAG, "birth.dob", f"no passport date of birth to compare the certificate's {facts.dob.isoformat()} "
            "with — check by eye", rpages, dob=facts.dob.isoformat(), trusted=facts.dob_trusted)
    elif facts.dob == pdob:
        add(PASS, "birth.dob", f"date of birth {facts.dob.isoformat()} ({two}) is the passport's ({psrc})",
            rpages, dob=facts.dob.isoformat(), trusted=facts.dob_trusted, passport_source=psrc)
    elif facts.dob_trusted and ptrusted:
        add(FAIL, "birth.dob", f"the certificate's date of birth {facts.dob.isoformat()} ({two}) is not the "
            f"passport's {pdob.isoformat()} ({psrc})", rpages, dob=facts.dob.isoformat(),
            passport=pdob.isoformat(), passport_source=psrc)
    else:
        add(FLAG, "birth.dob", f"DOB unclear, check by eye ({_pages_text(rpages)}): the certificate reads "
            f"{facts.dob.isoformat()} ({two}), the passport {pdob.isoformat()} ({psrc}"
            f"{'' if ptrusted else ', not trusted'})", rpages, dob=facts.dob.isoformat(),
            passport=pdob.isoformat(), trusted=facts.dob_trusted, passport_trusted=ptrusted)
