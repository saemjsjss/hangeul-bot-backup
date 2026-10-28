"""Language certificates: the offline IELTS / TOEFL / TOPIK checks (RUNNER_SPEC 5.8, owner rule R29).

A port of the lab's language.py, trimmed (critique 20) and put on the runner's one strict name rule (A1):
  * Only IELTS, TOEFL and TOPIK are checked. Every other certificate in the slot (Duolingo, PTE,
    EnglishScore, a medium-of-instruction letter, anything unrecognised) is PRESENT only.
  * Removed extras: the 60-day expiry warning, the administrator's-date and retake 60-day FLAGs, the
    General Training FLAG and the "verify online" authenticity NOTEs.
  * Names (owner 28 Oct): when MRZ line 1 is not usable the passport's PRINTED name is the reference (never
    "passport name not available" when it is known), and a word that reads differently gets the closer look on
    the certificate's page and on the passport's (passport["closer"]): an OCR misread is no difference.
  * Names (A1, critique 14): the candidate's name is compared with the passport's MRZ words by
    strict.compare(any_order=True, dots=False) (the MRZ is a machine read) and judged by strict.judge. A
    difference is FAIL only when both spellings are confirmed (the passport's MRZ name confirmed by its
    printed name; the certificate's name corroborated by the IELTS TRF number, which spells it apart from
    the name field) and no affidavit covers it; the affidavit test is the caller's `covered_fn`
    (names.covered: both spellings in one same-person group). Any other difference is FLAG; never PASS.
  * Date of birth (critique 12): the passport's date is compared only when the caller passes it, which
    identity.passport_for_language() does only when the MRZ date is trusted.
  * Minimum scores per program are placeholders (MIN_SCORES None -> NOTE).

No network (authenticity can only be confirmed online; that is not done). No personal data.

Public entry points
  detect_test(text)                    -> "IELTS" | "TOEFL" | "TOPIK" | "DUOLINGO" | "PTE" | "ENGLISHSCORE" |
                                          "OTHER_ENGLISH" | "MOI" | "UNKNOWN"
  parse_ielts / parse_topik / parse_toefl (text) -> fields dict
  ielts_overall(l, r, w, s)            -> the official overall band
  passport_name_verified(passport_text, surname, given_names) -> the MRZ name is confirmed by the printed name
  check_language(text, passport, program, today, covered_fn=None) -> report dict
  check_language_file(text, passport, program, today, covered_fn=None) -> {"certificates": [...], "verdict"}
  rows_text(items)                     -> OCR items with boxes as row-ordered text
"""
from __future__ import annotations

import datetime as dt
import re
from fractions import Fraction
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence, Tuple

from . import strict
from .model import FAIL, FLAG, NOTE, PASS, PRESENT

CHECKED_TESTS = ("IELTS", "TOEFL", "TOPIK")
TEST_LABEL = {"IELTS": "IELTS", "TOEFL": "TOEFL", "TOPIK": "TOPIK", "DUOLINGO": "Duolingo", "PTE": "PTE",
              "ENGLISHSCORE": "EnglishScore", "OTHER_ENGLISH": "another English certificate",
              "MOI": "a medium-of-instruction letter", "UNKNOWN": "not recognised as IELTS, TOEFL or TOPIK"}
PRESENT_ONLY_TEXT = "present — only IELTS, TOEFL and TOPIK are checked"

# ---------------------------------------------------------------------------------------------
# Rules (constants the owner can change)
# ---------------------------------------------------------------------------------------------
VALIDITY_RULES = {
    "IELTS": "test date + 2 years (the TRF itself says the result should be re-assessed after two "
             "years from the date of the test); the TRF number's 2-digit year is the April-March "
             "IELTS year, not the calendar year",
    "TOEFL": "test date + 2 years (ETS reports scores for 2 years after the test date)",
    "TOPIK": "the 'Valid Until' date printed on the score report (2 years from the result "
             "announcement); when it is not read, test date + 2 years is used and FLAGged",
}
VALID_YEARS = 2
# An expired certificate: FAIL (Q9 default). The slot is optional for every program; change here.
EXPIRED_VERDICT = FAIL

# Minimum scores per program: NOT given by the owner yet. None = not checked (NOTE only).
MIN_SCORES: Dict[str, Dict[str, Optional[float]]] = {
    "KLP":      {"IELTS": None, "TOEFL_IBT": None, "TOPIK_LEVEL": None},
    "EAP":      {"IELTS": None, "TOEFL_IBT": None, "TOPIK_LEVEL": None},
    "BACHELOR": {"IELTS": None, "TOEFL_IBT": None, "TOPIK_LEVEL": None},
    "MASTER":   {"IELTS": None, "TOEFL_IBT": None, "TOPIK_LEVEL": None},
}

# IELTS overall band -> CEFR level as printed on the TRF (IELTS's own mapping).
IELTS_CEFR = ((8.5, "C2"), (7.0, "C1"), (5.5, "B2"), (4.0, "B1"))
# TOPIK level cut scores (total), per test.
TOPIK_LEVELS = {"I": ((140, 2), (80, 1)), "II": ((230, 6), (190, 5), (150, 4), (120, 3))}
TOPIK_MAX = {"I": 200, "II": 300}

MONTHS = ["JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"]

# OCR confusions, applied only where a position's character class is fixed by the format.
_TO_DIGIT = str.maketrans("ODQUILZSBGT|", "000011258671")   # pii-scan: not a number (a translation table)
_TO_LETTER = str.maketrans("0158264", "OISBZGA")
# Inside the TRF number (seen on real scans): 'BD' read as '30' / '80', a 0 read as C.
_TRF_LETTER = str.maketrans("01582643", "OISBZGAB")
_TRF_DIGIT = str.maketrans("ODQUILZSBGT|C", "000011258671" + "0")   # pii-scan: not a number

# How a name difference is described in a message (strict.KINDS).
_KIND_WORDS = {"OCR": "letters OCR could confuse", "ORDER": "the same words in another order",
               "SUBSET": "a word added or left out", "LETTERS": "a letter or two differ",
               "DIFFERENT": "different words", "DOTS": "the dots differ"}


# ---------------------------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------------------------
def _flat(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").replace(" ", " ")).strip()


def _date(y: int, m: int, d: int) -> Optional[dt.date]:
    try:
        return dt.date(y, m, d)
    except ValueError:
        return None


def add_years(d: dt.date, n: int) -> dt.date:
    try:
        return d.replace(year=d.year + n)
    except ValueError:                      # 29 Feb -> 28 Feb
        return d.replace(year=d.year + n, day=28)


def _month(tok: str) -> Tuple[Optional[int], bool]:
    """A 3-letter month as read by OCR -> (month, repaired). '0CT' / 'OcT' / '5EP' are read;
    one wrong letter is accepted only when it leaves a single month ('AUC' -> AUG)."""
    t = tok.upper().translate(_TO_LETTER)
    if t in MONTHS:
        return MONTHS.index(t) + 1, t != tok.upper()
    near = [i for i, m in enumerate(MONTHS) if sum(a != b for a, b in zip(m, t)) == 1]
    if len(near) == 1:
        return near[0] + 1, True
    return None, False


def _lev(a: str, b: str) -> int:
    if len(a) < len(b):
        a, b = b, a
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]


def passport_name_verified(passport_text: str, surname: str, given_names: str) -> bool:
    """The MRZ line-1 name confirmed by the passport's printed (visual-zone) name: every MRZ name
    word is also a whole word of the page text outside the MRZ. MRZ line 1 has NO check digit, so a
    well-formed line 1 can still be misread. A name FAIL needs this; without it a difference is FLAG."""
    want = strict.words(surname) + strict.words(given_names)
    if not want:
        return False
    viz = " ".join(w for w in (passport_text or "").upper().split() if "<" not in w)
    seen = set(re.findall(r"[A-Z]+", viz))
    return all(w in seen for w in want)


def _pp_number(raw: str) -> str:
    """A Bangladeshi-style passport number read by OCR (1-2 letters then 7-8 digits), with
    letters/digits repaired by position. Any other shape is returned as read."""
    s = re.sub(r"[^A-Z0-9]", "", (raw or "").upper())
    if len(s) == 9:
        cand = s[0].translate(_TO_LETTER) + s[1:].translate(_TO_DIGIT)
        if re.fullmatch(r"[A-Z]\d{8}", cand):
            return cand
        cand = s[:2].translate(_TO_LETTER) + s[2:].translate(_TO_DIGIT)
        if re.fullmatch(r"[A-Z]{2}\d{7}", cand):
            return cand
    return s


ROW_GAP = 2.5          # a gap wider than 2.5 text heights separates two columns of a row


def rows_text(items: Iterable[Any]) -> str:
    """OCR items [(box, text, conf)] or [{"box": (x0,y0,x1,y1), "text"}] as text, one row per
    line, rows top to bottom and left to right: a label and its value on one line (the cached
    whole-file text has no boxes and keeps EasyOCR's own order)."""
    pieces = []
    for it in items:
        if isinstance(it, dict):
            box, text = it.get("box"), it.get("text", "")
            if box and len(box) == 4 and not isinstance(box[0], (list, tuple)):
                x0, y0, x1, y1 = box
            elif box:
                xs, ys = [p[0] for p in box], [p[1] for p in box]
                x0, y0, x1, y1 = min(xs), min(ys), max(xs), max(ys)
            else:
                continue
        else:
            box, text = it[0], it[1]
            xs, ys = [float(p[0]) for p in box], [float(p[1]) for p in box]
            x0, y0, x1, y1 = min(xs), min(ys), max(xs), max(ys)
        pieces.append((x0, y0, x1, y1, str(text)))
    rows: List[Dict[str, Any]] = []
    for p in sorted(pieces, key=lambda q: (q[1] + q[3]) / 2):
        cy, h = (p[1] + p[3]) / 2, max(1.0, p[3] - p[1])
        for r in rows:
            if abs(cy - r["cy"]) <= 0.5 * max(h, r["h"]):
                r["items"].append(p)
                break
        else:
            rows.append({"cy": cy, "h": h, "items": [p]})
    lines = []
    for r in rows:
        items = sorted(r["items"], key=lambda q: q[0])
        hs = sorted(q[3] - q[1] for q in items)
        h = max(1.0, hs[len(hs) // 2])
        out = items[0][4]
        for a, b in zip(items, items[1:]):
            # a wide gap (another column, a stamp beside the field) is marked " | ": a field's
            # value is never read across it
            out += (" | " if b[0] - a[2] > ROW_GAP * h else " ") + b[4]
        lines.append(out)
    return "\n".join(lines)


# ---------------------------------------------------------------------------------------------
# Which test
# ---------------------------------------------------------------------------------------------
def detect_test(text: str) -> str:
    low = _flat(text).lower()
    if not low:
        return "UNKNOWN"
    if "englishscore" in low or "english score" in low:
        return "ENGLISHSCORE"
    if re.search(r"duolingo", low):
        return "DUOLINGO"
    if re.search(r"\btoefl\b|test of english as a foreign language", low):
        return "TOEFL"
    if re.search(r"\bpte\b|pearson test of english", low):
        return "PTE"
    if re.search(r"topik|test of proficiency in korean|한국어능력시험", low):
        return "TOPIK"
    if re.search(r"ielts|test report form", low):
        return "IELTS"
    if re.search(r"medium of instruction|\bmoi\b|language of instruction", low):
        return "MOI"
    if re.search(r"\bcefr\b|english test|english certificate|english language test", low):
        return "OTHER_ENGLISH"
    return "UNKNOWN"


# ---------------------------------------------------------------------------------------------
# IELTS
# ---------------------------------------------------------------------------------------------
_I_FAMILY = re.compile(r"Fam[il1|]{1,2}y\s*Nam[e€]", re.I)
_I_FIRST = re.compile(r"F[il1|]rst\s*N\w{2,4}\s*(?:\(\s*s\s*\)|\(s|s\))?", re.I)
_I_CAND_ID = re.compile(r"Cand[il1|]date\s*[I1l|]\s*D\b", re.I)
_I_DOB = re.compile(r"Date\s*of\s*B\w{3,4}", re.I)
_I_CENTRE = re.compile(r"Cent(?:re|er)\s*N\w{4,6}", re.I)
_I_CAND_NO = re.compile(r"Cand[il1|]date\s*N\w{4,6}", re.I)
_I_RESULTS = re.compile(r"Test\s*Results?", re.I)
_I_RESULTS_END = re.compile(r"Administrator|Centre\s*stamp|Validation|Comments", re.I)
_DATE_MON = re.compile(r"(\d{1,2})\s*[/\-. ]?\s*([A-Za-z0-9]{3})\s*[/\-. ]?\s*((?:19|20)\d{2})")
_DATE_NUM = re.compile(r"(\d{1,2})\s*[/\-.]\s*(\d{1,2})\s*[/\-.]\s*((?:19|20)\d{2})")


def ielts_overall(l: float, r: float, w: float, s: float) -> float:
    """IELTS overall band: the mean of the four bands rounded to the nearest half band; a mean
    ending in .25 goes up to the next half band and one ending in .75 up to the next whole band
    (exact arithmetic: means are multiples of 1/8)."""
    mean = (Fraction(str(l)) + Fraction(str(r)) + Fraction(str(w)) + Fraction(str(s))) / 4
    halves = mean * 2
    whole = int(halves)                         # floor (scores are >= 0)
    if halves - whole >= Fraction(1, 2):
        whole += 1
    return whole / 2


def _ielts_cefr(overall: float) -> str:
    for cut, level in IELTS_CEFR:
        if overall >= cut:
            return level
    return "below B1"


# The TRF prints its test date as DD/MMM/YYYY. OCR reads the slash as I / 1 / l, the 0 of a day
# as O, lower-cases the month ('0ct'): every such substitution is recorded as a repair.
_TRF_DATE = re.compile(r"(?<![A-Za-z0-9])([0-9OIlSZ|]{1,2})\s*([/Il1|])?\s*([A-Za-z0-9]{3})\s*([/Il1|])?\s*((?:19|20)\d{2})(?!\d)")
_DAY_DIGIT = str.maketrans("OIlSZ|", "011521")


def _ielts_test_date(t: str) -> Tuple[Optional[dt.date], bool]:
    """The test date: 'DD/MMM/YYYY' between 'Centre Number' and 'Candidate Number' (or after a
    'Test Date' label). Never a date from elsewhere on the page: attested copies carry the
    notary's dated stamp, which is not the test date. -> (date, repaired)."""
    wins = []
    for m in _I_CENTRE.finditer(t):
        w = t[m.end():m.end() + 70]
        e = re.search(r"Ca\w{2,8}\s*N\w{4,6}|Fam\w{2,4}\s*Name", w, re.I)
        wins.append(w[:e.start()] if e else w)
    for m in re.finditer(r"Test\s*Date", t, re.I):
        wins.append(t[m.end():m.end() + 25])
    for w in wins:
        dm = re.search(r"Da[it]e", w, re.I)
        seg = w[dm.end():] if dm else w
        for m in _TRF_DATE.finditer(seg):
            day_raw, s1, mon_raw, s2, year = m.groups()
            mon, rep = _month(mon_raw)
            if mon is None:
                continue
            day = day_raw.translate(_DAY_DIGIT)
            if not day.isdigit():
                continue
            d = _date(int(year), mon, int(day))
            if d:
                repaired = rep or day != day_raw or (s1 or "") != "/" or (s2 or "") != "/"
                return d, repaired
    return None, False


def _ielts_trf_number(t: str, cand_no: Optional[str]) -> Tuple[Optional[str], Optional[str]]:
    """The Test Report Form number, 18 characters: YY CC NNNNNN LLLL DDD M
    (IELTS year, centre letters, candidate number, family[:3] + first[:1], centre digits,
    module A/G), repaired by position. -> (number, raw read) or (None, None)."""
    T = re.sub(r"[^A-Z0-9]+", " ", t.upper())
    toks = T.split()
    cands: List[Tuple[str, str]] = []
    for i in range(len(toks)):
        joined = ""
        for j in range(i, min(i + 4, len(toks))):
            joined += toks[j]
            if len(joined) > 19:
                break
            if len(joined) in (17, 18):
                s = joined
                rep = (s[0:2].translate(_TO_DIGIT) + s[2:4].translate(_TRF_LETTER) + s[4:10].translate(_TRF_DIGIT)
                       + s[10:14].translate(_TO_LETTER) + s[14:17].translate(_TRF_DIGIT)
                       + s[17:18].replace("4", "A").replace("6", "G"))
                # 18 characters; 17 = the module letter lost at the edge of the scan (kept, marked "?")
                if re.fullmatch(r"\d{2}[A-Z]{2}\d{6}[A-Z]{4}\d{3}[AG]", rep):
                    cands.append((rep, s))
                elif len(s) == 17 and re.fullmatch(r"\d{2}[A-Z]{2}\d{6}[A-Z]{4}\d{3}", rep):
                    cands.append((rep + "?", s + "?"))
    if not cands:
        return None, None
    cands.sort(key=lambda c: c[0].endswith("?"))            # whole reads first
    if cand_no:
        for rep, raw in cands:
            if rep[4:10] == cand_no or _digits_match(raw.upper()[4:10], cand_no):
                return rep, raw
    return cands[0]


# The four skills and the overall band, in the TRF's printed order.
_SKILLS = (("L", r"L[il1]?[sa]?t[eo]n[il1]n[gs]"), ("R", r"R[eo]a?d[il1]n[gs]"), ("W", r"Wr[il1]t[il1]n[gs]"),
           ("S", r"Sp[eo]a?k[il1]n[gs]"), ("O", r"Band(?:\s*Score)?"))
# A band right after its label: '6.5', '6,5', '6 5', '6.0i', '60' / '65' (dot lost), '4o'.
_VCORE = r"(\d)(?:\s?[.,]{1,2}\s?([0-9oO])|\s([05])(?=\s|$)|([05oO])(?=\s|$))?[il|]?(?![\d])"
_VALUE = re.compile(r"\W{0,3}" + _VCORE)
_VALUE_FREE = re.compile(r"(?<![\w.,])" + _VCORE)


def _band_value(m: re.Match) -> Tuple[Optional[float], bool, bool]:
    """-> (band, repaired, invalid). invalid: a decimal other than .0 / .5 (an OCR misread)."""
    whole = int(m.group(1))
    dec = m.group(2) or m.group(3) or m.group(4)
    repaired = m.group(2) is None and dec is not None or bool(re.search(r"[il|oO]", m.group(0)))
    if dec is None:
        return float(whole), False, whole > 9
    dec = dec.replace("o", "0").replace("O", "0")
    if dec not in "05" or whole > 9 or (whole == 9 and dec == "5"):
        return None, repaired, True
    return whole + (0.5 if dec == "5" else 0.0), repaired, False


def _ielts_bands(t: str) -> Dict[str, Any]:
    """The four bands and the overall band from the 'Test Results' block. Values are taken right
    after their own label (the usual reading order); when no label has its value beside it (the
    labels read as one row, the values as the next) the five values are taken in printed order."""
    out: Dict[str, Any] = {"bands": None, "overall": None, "repaired": False, "found": 0,
                           "read": {}, "invalid": 0}
    m = _I_RESULTS.search(t)
    if not m:
        return out
    tail = t[m.end():m.end() + 400]
    e = _I_RESULTS_END.search(tail, 20)
    win = tail[:e.start()] if e else tail[:220]
    read: Dict[str, float] = {}
    rep = False
    used: List[Tuple[int, int]] = []
    for key, pat in _SKILLS:
        lm = re.search(pat, win, re.I)
        if not lm:
            continue
        vm = _VALUE.match(win, lm.end())
        if not vm:
            continue
        v, r, bad = _band_value(vm)
        used.append(vm.span(1))
        if bad:
            out["invalid"] += 1
        elif v is not None:
            read[key] = v
            rep = rep or r
    # values not beside a label (labels read as one row, values as the next): in printed order
    free = []
    for vm in _VALUE_FREE.finditer(win):
        if any(a <= vm.start(1) < b for a, b in used):
            continue
        v, r, bad = _band_value(vm)
        if bad:
            out["invalid"] += 1
        elif v is not None:
            free.append((v, r))
    todo = [k for k in "LRWSO" if k not in read]
    if todo and len(free) == len(todo):
        for k, (v, r) in zip(todo, free):
            read[k] = v
            rep = rep or r
    out["found"] = len(read)
    out["read"] = read
    if all(k in read for k in "LRWSO"):
        out["bands"] = [read[k] for k in "LRWS"]
        out["overall"] = read["O"]
        out["repaired"] = rep
    return out


def _clean_written(s: str) -> str:
    return re.sub(r"\s+", " ", s or "").strip(" |")


def parse_ielts(text: str) -> Dict[str, Any]:
    t = _flat(text)
    f: Dict[str, Any] = {"test": "IELTS"}
    low = t.lower()
    # 'Test Report Form ACADEMIC' (older TRF) / 'Test Report Form | IELTS Academic' (newer, row text).
    f["module"] = ("A" if re.search(r"test report form\W{0,4}(?:ielts\s*)?academic", low)
                   else "G" if re.search(r"test report form\W{0,4}(?:ielts\s*)?general\s*training", low) else None)
    # One Skill Retake TRF: the top 'Date' is the ORIGINAL test's (validity runs from it).
    f["one_skill_retake"] = bool(re.search(r"one\s*skill\s*retake|retake\s*was\s*taken|includes\s*the\s*res\w{2,4}\s*of\s*a\s*\w+\s*retake", low))
    f["ukvi"] = bool(re.search(r"ukvi|ukv[il1]", low))
    f["online"] = bool(re.search(r"ielts online", low))
    m = re.search(_I_CENTRE.pattern + r"\W{0,3}([A-Z0-9]{2})\s?([A-Z0-9]{3})(?![A-Z0-9])", t, re.I)
    f["centre"] = (m.group(1).upper().translate(_TO_LETTER) + m.group(2).upper().translate(_TO_DIGIT)) if m else None
    if f["centre"] and not re.fullmatch(r"[A-Z]{2}\d{3}", f["centre"]):
        f["centre"] = None
    m = re.search(_I_CAND_NO.pattern + r"\W{0,3}(\d{6})(?!\d)", t, re.I)
    f["candidate_no"] = m.group(1) if m else None
    f["test_date"], f["test_date_repaired"] = _ielts_test_date(t)
    m = re.search(_I_DOB.pattern + r"\W{0,3}" + _DATE_NUM.pattern, t, re.I)
    f["dob"] = _date(int(m.group(3)), int(m.group(2)), int(m.group(1))) if m else None
    m = re.search(_I_CAND_ID.pattern + r"\W{0,3}([A-Z0-9]{5,17})\b", t, re.I)
    f["candidate_id"] = _pp_number(m.group(1)) if m else None
    # names: between the labels (both bounded => "clean"), kept AS WRITTEN (dots kept)
    fam, first = _I_FAMILY.search(t), _I_FIRST.search(t)
    cid = _I_CAND_ID.search(t)
    f["family_name"] = f["first_name"] = None
    f["name_clean"] = False

    def _field(s: str) -> str:
        """A value up to the next column gap (' | ', fresh reads with boxes only)."""
        return s.strip().lstrip("|").split("|")[0]

    if fam and first and fam.end() <= first.start():
        fam_txt = _field(t[fam.end():first.start()])
        first_txt = _field(t[first.end():cid.start()] if cid and cid.start() > first.end()
                           else t[first.end():first.end() + 40])
        fam_w, first_w = strict.words(fam_txt), strict.words(first_txt)
        if 1 <= len(fam_w) <= 4 and len(first_w) <= 6:
            f["family_name"], f["first_name"] = _clean_written(fam_txt), _clean_written(first_txt)
            # clean: bounded by both labels, letters and spaces only, every word 2+ letters
            f["name_clean"] = bool(cid and cid.start() > first.end()
                                   and re.fullmatch(r"[A-Za-z .'\-]*", fam_txt.strip() + first_txt.strip())
                                   and all(len(x) >= 2 for x in fam_w + first_w))
    b = _ielts_bands(t)
    f["bands"], f["overall"], f["bands_repaired"] = b["bands"], b["overall"], b["repaired"]
    f["band_numbers_found"], f["bands_read"], f["bands_invalid"] = b["found"], b["read"], b["invalid"]
    m = re.search(r"CEFR\s*Level\W{0,3}([ABC][12])\b|Level\W{0,3}([ABC][12])\b|\b([ABC][12])\s*Level", t)
    f["cefr"] = next((g for g in (m.groups() if m else ()) if g), None)
    f["trf_number"], f["trf_raw"] = _ielts_trf_number(t, f["candidate_no"])
    return f


# What OCR may print for each digit of the TRF number (T is a 1 or a 7 in this font).
_DIGIT_READS = {"0": "0ODQUC", "1": "1IlLT|", "2": "2Z", "3": "3", "4": "4A", "5": "5S", "6": "6G",
                "7": "7TZ", "8": "8B", "9": "9g"}


def _digits_match(raw: str, digits: str) -> bool:
    """`raw` (as OCR read it) can be `digits`: every character is that digit or a usual misread."""
    return len(raw) == len(digits) and all(c in _DIGIT_READS.get(d, d) for c, d in zip(raw, digits))


def _trf_letters(family: Optional[str], first: Optional[str]) -> str:
    """The TRF number's name letters for a printed name: family[:3] + first[:1] ('' when short)."""
    fam, fst = strict.letters(family or ""), strict.letters(first or "")
    want = fam[:3] + fst[:1]
    return want if len(want) == 4 and len(fam) >= 3 else ""


def trf_consistency(f: Dict[str, Any]) -> List[Tuple[str, str, str]]:
    """The TRF number against the fields printed on the same form. -> [(verdict, check, why)]."""
    out: List[Tuple[str, str, str]] = []
    trf = f.get("trf_number")
    raw = (f.get("trf_raw") or "").upper()
    if not trf:
        return [(FLAG, "trf_format", "Test Report Form number not read (18 characters: YY CC NNNNNN LLLL DDD M)")]
    out.append((PASS, "trf_format", "Test Report Form number has the IELTS format"
                + (" (its last character, the module letter, not read)" if trf.endswith("?") else "")))
    if f.get("candidate_no"):
        ok = trf[4:10] == f["candidate_no"] or _digits_match(raw[4:10], f["candidate_no"])
        out.append((PASS if ok else FLAG, "trf_candidate",
                    "TRF number carries the candidate number" if ok else
                    "TRF number does not carry the printed candidate number"))
    if f.get("test_date"):
        d = f["test_date"]
        yy = (d.year if d.month >= 4 else d.year - 1) % 100
        ok = int(trf[0:2]) == yy or _digits_match(raw[0:2], "%02d" % yy)
        out.append((PASS if ok else FLAG, "trf_year",
                    "TRF year agrees with the test date (April-March IELTS year)" if ok else
                    "TRF year does not agree with the test date"))
    if f.get("centre"):
        ok_l = trf[2:4] == f["centre"][:2] or {trf[2:4], f["centre"][:2]} == {"BO", "BD"}
        ok_d = trf[14:17] == f["centre"][2:] or _digits_match(raw[14:17], f["centre"][2:])
        out.append((PASS if ok_l and ok_d else FLAG, "trf_centre",
                    "TRF number carries the centre number" if ok_l and ok_d else
                    "TRF number does not carry the printed centre number"))
    want = _trf_letters(f.get("family_name"), f.get("first_name"))
    if want:
        ok = trf[10:14] == want
        out.append((PASS if ok else FLAG, "trf_name",
                    "TRF letters agree with the printed name (family 3 + first 1)" if ok else
                    "TRF letters do not agree with the printed name"))
    if f.get("module") and trf[17] != "?":
        ok = trf[17] == f["module"]
        out.append((PASS if ok else FLAG, "trf_module",
                    "TRF module letter agrees" if ok else "TRF module letter does not agree with the printed module"))
    return out


# ---------------------------------------------------------------------------------------------
# TOPIK
# ---------------------------------------------------------------------------------------------
_YMD = re.compile(r"((?:19|20)\d{2})\s*[/.\-]\s*(\d{1,2})\s*[/.\-]\s*(\d{1,2})")


def parse_topik(text: str) -> Dict[str, Any]:
    t = _flat(text)
    f: Dict[str, Any] = {"test": "TOPIK"}
    # the first 'TOPIK I' / 'TOPIK II' (later ones are the score report's level descriptions)
    m = re.search(r"TOPIK\s*(II|Ⅱ|11|Il|lI|ll|I|Ⅰ|1|l)(?![A-Za-z0-9])", t)
    f["level_test"] = None if not m else ("I" if m.group(1) in ("I", "Ⅰ", "1", "l") else "II")
    m = re.search(r"(?<!\d)(\d{4})-(\d{4})-(\d{4})-(\d{4})(?!\d)", t)
    f["print_no"] = "".join(m.groups()) if m else None
    m = re.search(r"(?<!\d)(\d{13})(?!\d)", t)
    f["registration_no"] = m.group(1) if m else None
    m = re.search(r"(?<![\d/])(\d{2,3})\s*/\s*((?:19|20)\d{2})\s*/\s*(\d{1,2})\s*/\s*(\d{1,2})", t)
    f["round"] = int(m.group(1)) if m else None
    f["test_date"] = _date(int(m.group(2)), int(m.group(3)), int(m.group(4))) if m else None
    dates = [(_date(int(a), int(b), int(c)), mm.start()) for mm in _YMD.finditer(t)
             for a, b, c in [mm.groups()]]
    dates = [(d, p) for d, p in dates if d]
    # 'Date of Birth' ... <sex> <YYYY/MM/DD> <ROMANISED NAME> [M|F] BANGLADESH (the order of the
    # date and the name varies): both are read from that stretch only.
    m = re.search(r"Date\s*of\s*Birth", t, re.I)
    f["dob"] = None
    f["name"] = None
    if m:
        end = re.search(r"BANGLADESH|Country|yyyy", t[m.end():m.end() + 160])
        stretch = t[m.end():m.end() + (end.end() if end and end.group(0) == "BANGLADESH" else
                                       end.start() if end else 120)]
        after = [d for d, p in dates if m.end() < p < m.end() + len(stretch) + 30]
        if after and (f["test_date"] is None or after[0] < f["test_date"]):
            f["dob"] = after[0]
        name_words = [w for w in stretch.split() if re.fullmatch(r"[A-Za-z][A-Za-z.'\-]+", w)
                      and (w.isupper() or w[0].isupper()) and w.upper() not in ("BANGLADESH", "MALE", "FEMALE")]
        if end and end.group(0) == "BANGLADESH" and 1 <= len(name_words) <= 6:
            f["name"] = " ".join(name_words)
    vu = re.search(r"Valid\s*\w{0,6}", t, re.I)
    f["valid_until"] = None
    later = sorted({d for d, _ in dates if f["test_date"] and d > f["test_date"]})
    if later:
        f["valid_until"] = later[0] if vu else None
    scores = [(int(a), int(b)) for a, b in re.findall(r"(?<!\d)(\d{1,3})\s*/\s*(100|200|300)(?!\d)", t)]
    sect = [a for a, b in scores if b == 100 and a <= 100]
    tot = [a for a, b in scores if b in (200, 300)]
    f["sections"] = sect[:3] if f["level_test"] == "II" else sect[:2]
    f["total"] = tot[0] if tot else None
    f["total_of"] = next((b for a, b in scores if b in (200, 300)), None)
    m = re.search(r"(\d)\s*급", t)
    f["level"] = int(m.group(1)) if m else None
    f["level_test_conflict"] = False
    if f["total_of"]:                       # the total's maximum says which test it is: /200 I, /300 II
        by_total = "I" if f["total_of"] == 200 else "II"
        f["level_test_conflict"] = bool(f["level_test"] and f["level_test"] != by_total)
        f["level_test"] = f["level_test"] or by_total
        f["sections"] = sect[:3] if f["level_test"] == "II" else sect[:2]
    return f


def topik_level(test: str, total: int) -> Optional[int]:
    for cut, lvl in TOPIK_LEVELS.get(test, ()):
        if total >= cut:
            return lvl
    return 0 if test in TOPIK_LEVELS else None


# ---------------------------------------------------------------------------------------------
# TOEFL (no sample in the corpus: the parser is best effort, unmeasured)
# ---------------------------------------------------------------------------------------------
def parse_toefl(text: str) -> Dict[str, Any]:
    t = _flat(text)
    f: Dict[str, Any] = {"test": "TOEFL"}
    if re.search(r"\bITP\b|institutional\s*testing|structure\s*and\s*written\s*expression", t, re.I):
        # TOEFL ITP (paper, institutional): 3 sections, total = sum x 10 / 3 (310-677)
        f["variant"] = "ITP"
        sec = {}
        for k, pat in (("listening", r"Listening\s*Comprehension"), ("structure", r"Structure\s*(?:and|&)\s*Written\s*Expression"),
                       ("reading", r"Reading\s*Comprehension")):
            m = re.search(pat + r"\W{0,4}(\d{2})(?!\d)", t, re.I)
            sec[k] = int(m.group(1)) if m else None
        f["sections"] = sec
        m = re.search(r"Total\s*(?:Score)?\W{0,4}(\d{3})(?!\d)", t, re.I)
        f["total"] = int(m.group(1)) if m else None
        m = re.search(r"Test\s*Date\W{0,3}(.{0,20})", t, re.I)
        f["test_date"] = _any_date(m.group(1)) if m else None
        m = re.search(r"(?:Date\s*of\s*Birth|Birth\s*Date)\W{0,3}(.{0,20})", t, re.I)
        f["dob"] = _any_date(m.group(1)) if m else None
        return f
    f["variant"] = "iBT"
    sec = {}
    for k in ("Reading", "Listening", "Speaking", "Writing"):
        m = re.search(k + r"\W{0,4}(\d{1,2}(?:\.\d)?)(?![\d])", t, re.I)
        sec[k.lower()] = float(m.group(1)) if m else None
    f["sections"] = sec
    m = re.search(r"Total\s*Score\W{0,4}(\d{1,3}(?:\.\d)?)", t, re.I)
    f["total"] = float(m.group(1)) if m else None
    # Since 21 Jan 2026 ETS reports a 1-6 band scale (0-120 still shown in the transition).
    f["scale"] = ("1-6" if all(v is not None and v <= 6 for v in sec.values()) and (f["total"] or 0) <= 6
                  else "0-120")
    m = re.search(r"(?:Registration|Appointment)\s*(?:Number|No\.?)\W{0,3}(\d[\d ]{14,20}\d)", t, re.I)
    f["registration_no"] = re.sub(r"\D", "", m.group(1)) if m else None
    m = re.search(r"Test\s*Date\W{0,3}(.{0,20})", t, re.I)
    f["test_date"] = _any_date(m.group(1)) if m else None
    m = re.search(r"(?:Date\s*of\s*Birth|Birth\s*Date)\W{0,3}(.{0,20})", t, re.I)
    f["dob"] = _any_date(m.group(1)) if m else None
    return f


def _any_date(s: str) -> Optional[dt.date]:
    s = s or ""
    m = _YMD.search(s)
    if m:
        return _date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    m = _DATE_MON.search(s)
    if m:
        mon, _ = _month(m.group(2))
        if mon:
            return _date(int(m.group(3)), mon, int(m.group(1)))
    m = re.search(r"([A-Za-z]{3})[a-z]*\.?\s+(\d{1,2}),?\s+((?:19|20)\d{2})", s)
    if m:
        mon, _ = _month(m.group(1)[:3])
        if mon:
            return _date(int(m.group(3)), mon, int(m.group(2)))
    m = _DATE_NUM.search(s)
    if m:
        return _date(int(m.group(3)), int(m.group(2)), int(m.group(1)))
    return None


# ---------------------------------------------------------------------------------------------
# The checks
# ---------------------------------------------------------------------------------------------
def _validity(test: str, test_date: Optional[dt.date], today: dt.date,
              valid_until: Optional[dt.date] = None, repaired: bool = False) -> Tuple[str, str]:
    if valid_until is None and test_date is None:
        return FLAG, "test date not read: validity (%s) not checked" % VALIDITY_RULES.get(test, "2 years")
    last = valid_until or add_years(test_date, VALID_YEARS)
    if test_date and test_date > today:
        return FLAG, f"test date read as {test_date.isoformat()}, after today: misread?"
    if today > last:
        why = f"expired on {last.isoformat()} ({VALIDITY_RULES.get(test, '2 years')})"
        return (FLAG if repaired else EXPIRED_VERDICT), why + (" (date read with OCR repair)" if repaired else "")
    return PASS, f"valid until {last.isoformat()}"


def _word_pairs(pp_words: Sequence[str], cert_words: Sequence[str]) -> List[Tuple[str, str]]:
    """(passport word, certificate word) pairs that differ, in any order ("" for a word the other lacks): each
    certificate word takes the closest unused passport word within a few letters (describes a difference only)."""
    rest, out = list(pp_words), []
    for w in cert_words:
        if w in rest:
            rest.remove(w)
            continue
        p = min(rest, key=lambda z: strict._edit(z, w)) if rest else ""
        if p and strict._edit(p, w) <= max(2, len(p) // 2):
            out.append((p, w))
            rest.remove(p)
        else:
            out.append(("", w))
    return out + [(p, "") for p in rest]


def _closer_words(closer: Optional[Callable[[str, str], Tuple[str, str]]], pp_words: Sequence[str],
                  cert_written: str) -> Tuple[str, List[Tuple[str, str, str, str]]]:
    """Owner 28 Oct: the closer look on each word that reads differently, on the certificate's page and on the
    passport's. -> ("misread" every certificate word shows the passport's spelling | "confirmed" a word differs on
    the certificate and the passport prints its own | "unclear" | "" no look), [(passport word, certificate word,
    certificate look, passport look)]."""
    pairs = _word_pairs(pp_words, strict.words(cert_written))
    if closer is None or not pairs or any(not p or not c for p, c in pairs):
        return "", []
    got = []
    for p, c in pairs:
        try:
            v, r = closer(c, p)
        except Exception:
            v, r = "unclear", "unclear"
        got.append((p, c, v, r))
    if all(v == "same" for _, _, v, _ in got):
        return "misread", got
    if any(v == "differs" and r == "same" for _, _, v, r in got):        # one word confirmed is a difference
        return "confirmed", got
    return "unclear", got


def _name_finding(f: Dict[str, Any], passport: Dict[str, Any], covered_fn: Optional[Callable[[str], bool]],
                  cert_written: Optional[str], clean: bool, corroborated: bool) -> Tuple[str, str, str]:
    """The candidate's name against the passport's name (A1, one policy, section 7; owner 28 Oct).

    The passport's name is its MRZ line 1 (a machine read) when well formed, else its PRINTED name (never "not
    available" when the printed name is known). EQUAL (any word order: the certificate prints the family and first
    names in fields of its own; dots aside, they go to the dot rule) -> PASS. Otherwise the closer look on each word
    that reads differently, on the certificate's page and on the passport's (passport["closer"], identity.py):
    every certificate word showing the passport's spelling -> PASS (an OCR misread); a word that differs on the
    certificate while the passport prints its own -> confirmed. Then strict.judge: covered by an affidavit
    (covered_fn: names.covered) -> NOTE; confirmed -> FAIL; anything else -> FLAG "check by eye". Without a closer
    look, confirmed needs BOTH spellings confirmed as before: the passport's name (MRZ confirmed by its printed name,
    or the printed name confirmed), and the certificate's by the IELTS TRF number, which spells it apart from the
    name field."""
    sur, giv = passport.get("surname") or "", passport.get("given_names") or ""
    pp_words = tuple(strict.words(sur)) + tuple(strict.words(giv))
    source = "MRZ"
    if pp_words and passport.get("name_trusted") is False:
        pp_words = ()                    # an MRZ line 1 that is not well formed is not a name source
    printed = passport.get("printed_name") or ""
    if not pp_words and strict.letters(printed):
        pp_words, source = tuple(strict.words(printed)), "printed"
    if not pp_words:
        return FLAG, "name", ("passport name not available (neither MRZ line 1 nor the printed name could be read): "
                              "name not compared")
    if not cert_written or not strict.letters(cert_written):
        return FLAG, "name", "candidate name not read on the certificate"
    kind = strict.compare(cert_written, pp_words, any_order=True, dots=False)
    against = "the passport" if source == "MRZ" else "the passport's printed name (MRZ line 1 not read cleanly)"
    if kind == "EQUAL":
        return PASS, "name", f"name matches {against}"
    covered = bool(covered_fn(cert_written)) if covered_fn else False
    how = _KIND_WORDS.get(kind, kind.lower())
    looked, got = _closer_words(passport.get("closer"), pp_words, cert_written)
    if looked == "misread" and not covered:
        return PASS, "name", (f"name matches {against}: the closer look shows the passport's spelling on the "
                              f"certificate (the page read was an OCR misread)")
    if looked:
        verdict = strict.judge(kind, covered, looked == "confirmed")
        if verdict == NOTE:
            return NOTE, "name", f"name differs from {against} ({how}); an affidavit covers both spellings"
        if verdict == FAIL:
            return FAIL, "name", (f"name differs from {against} ({how}; the closer look confirms the certificate's "
                                  f"and the passport's spellings) and no affidavit covers it")
        return FLAG, "name", (f"name may differ from {against} ({how}; the closer look could not tell): "
                              f"check by eye")
    trf_letters = (f.get("trf_number") or "")[10:14]
    pp_letters = _trf_letters(sur, giv) if source == "MRZ" else ""
    trf_says_other = bool(corroborated and len(trf_letters) == 4 and len(pp_letters) == 4
                          and trf_letters != pp_letters)
    cert_confirmed = bool(clean and trf_says_other)
    pp_confirmed = bool(passport.get("name_verified")) if source == "MRZ" else bool(passport.get("printed_confirmed"))
    verdict = strict.judge(kind, covered, cert_confirmed and pp_confirmed)
    if verdict == NOTE:
        return NOTE, "name", f"name differs from {against} ({how}); an affidavit covers both spellings"
    if verdict == FAIL:
        return FAIL, "name", (f"name differs from {against} ({how}; the TRF number spells the certificate's "
                              "name too) and no affidavit covers it")
    if cert_confirmed and not pp_confirmed:
        return FLAG, "name", (f"name differs from the passport's MRZ ({how}), but the MRZ name is not confirmed by "
                              "the passport's printed name (MRZ line 1 has no check digit): check by eye")
    return FLAG, "name", f"name may differ from {against} ({how}; the read is not confirmed): check by eye"


def _identity(f: Dict[str, Any], passport: Dict[str, Any], covered_fn: Optional[Callable[[str], bool]],
              cert_written: Optional[str], clean: bool, corroborated: bool) -> List[Tuple[str, str, str]]:
    """Name / date of birth / ID number against the passport."""
    out: List[Tuple[str, str, str]] = [_name_finding(f, passport, covered_fn, cert_written, clean, corroborated)]
    pdob, cdob = passport.get("dob"), f.get("dob")
    if passport.get("dob_trusted") is False:
        pdob = None                      # critique 12: only a trusted passport date is compared
    if not pdob:
        out.append((FLAG, "dob", "passport date of birth not available (MRZ date not trusted): not compared"))
    elif not cdob:
        out.append((FLAG, "dob", "date of birth not read on the certificate"))
    elif cdob == pdob:
        out.append((PASS, "dob", "date of birth matches the passport"))
    else:
        a, b = cdob.strftime("%d%m%Y"), pdob.strftime("%d%m%Y")
        ndiff = sum(x != y for x, y in zip(a, b))
        if ndiff <= 1:
            out.append((FLAG, "dob", "date of birth differs from the passport by one digit (OCR?)"))
        else:
            out.append((FAIL, "dob", "date of birth differs from the passport"))
    ppn, cid = passport.get("passport_no"), f.get("candidate_id")
    if "candidate_id" in f:
        if not cid:
            out.append((FLAG, "id", "candidate ID (passport number) not read"))
        elif not ppn:
            out.append((FLAG, "id", "passport number not available: candidate ID not compared"))
        elif cid == ppn:
            out.append((PASS, "id", "candidate ID is the passport number"))
        elif re.fullmatch(r"\d{10}|\d{13}|\d{17}", cid):
            out.append((FLAG, "id", "candidate ID looks like an NID number, not this passport"))
        elif _lev(cid, ppn) == 1:
            out.append((FLAG, "id", "candidate ID differs from the passport number by one character (OCR?)"))
        else:
            out.append((FLAG, "id", "candidate ID is another number (an older passport?): ask, or verify online"))
    return out


def split_certificates(text: str) -> List[str]:
    """One file may hold several certificates (an IELTS TRF then a 2-page TOPIK report). Pages are
    split on form feeds (fresh reads) or, in the bot's cached text (pages joined by newlines, an OCR'd
    page being one line), on newlines when there are few lines; consecutive pages of the same test,
    and pages naming no test, are kept together."""
    if "\f" in text:
        pages = text.split("\f")
    else:
        lines = text.split("\n")
        pages = lines if len(lines) <= 20 else [text]
    groups: List[Tuple[str, List[str]]] = []
    for p in pages:
        kind = detect_test(p)
        if groups and (kind in ("UNKNOWN", groups[-1][0])):
            groups[-1][1].append(p)
        else:
            groups.append((kind, [p]))
    if len(groups) > 1 and groups[0][0] == "UNKNOWN":      # a leading cover page joins the next
        kind, ps = groups.pop(0)
        groups[0][1][:0] = ps
    return ["\n".join(ps) for _, ps in groups] or [text]


def _verdict(levels: Iterable[str]) -> str:
    seen = set(levels)
    if FAIL in seen:
        return FAIL
    if FLAG in seen:
        return FLAG
    if seen and seen <= {PRESENT}:
        return PRESENT
    return PASS


def check_language_file(text: str, passport: Dict[str, Any], program: str, today: dt.date,
                        covered_fn: Optional[Callable[[str], bool]] = None) -> Dict[str, Any]:
    """Every certificate in one uploaded file -> {"certificates": [report, ...], "verdict"}."""
    reps = [check_language(seg, passport, program, today, covered_fn) for seg in split_certificates(text)]
    return {"certificates": reps, "verdict": _verdict(r["verdict"] for r in reps)}


def check_language(text: str, passport: Dict[str, Any], program: str, today: dt.date,
                   covered_fn: Optional[Callable[[str], bool]] = None) -> Dict[str, Any]:
    """One language certificate -> {"test", "fields", "findings": [(verdict, check, why)], "verdict"}.
    `passport`: identity.passport_for_language() — {"surname", "given_names", "passport_no", "dob" (None
    unless the MRZ date is trusted), "name_verified", "name_trusted"}. `covered_fn(cert_name)`: whether an
    affidavit covers the passport's spelling and the certificate's (names.covered)."""
    test = detect_test(text)
    findings: List[Tuple[str, str, str]] = []
    prog = (program or "").upper()
    if test not in CHECKED_TESTS:
        why = f"{PRESENT_ONLY_TEXT} ({TEST_LABEL.get(test, test)})"
        return {"test": test, "fields": {"test": test}, "findings": [(PRESENT, "present_only", why)],
                "verdict": PRESENT}
    if test == "IELTS":
        f = fields = parse_ielts(text)
        name = (" ".join(x for x in (f["family_name"], f["first_name"]) if x)
                if f.get("family_name") else None)
        trf = trf_consistency(f)
        corroborated = any(c == "trf_name" and v == PASS for v, c, _ in trf)
        findings += _identity(f, passport, covered_fn, name, f.get("name_clean", False), corroborated)
        v, why = _validity("IELTS", f["test_date"], today, repaired=f.get("test_date_repaired", False))
        findings.append((v, "validity", why))
        if f.get("one_skill_retake"):
            findings.append((NOTE, "retake", "One Skill Retake TRF: validity counted from the original test date "
                             "(the date at the top), as IELTS rules"))
        if f["bands"] is None and f.get("bands_invalid"):
            findings.append((FLAG, "arithmetic", "a band was read as an impossible value (e.g. 6.6): OCR, "
                             "check by eye"))
        elif f["bands"] is None and f.get("bands_read"):
            findings.append((FLAG, "arithmetic", "%d of the 5 band values read: arithmetic not checked"
                             % len(f["bands_read"])))
        if f["bands"] is not None:
            want = ielts_overall(*f["bands"])
            if want == f["overall"]:
                findings.append((PASS, "arithmetic", "overall band = rounded mean of the four bands"))
            else:
                findings.append((FLAG, "arithmetic", "overall band does not equal the rounded mean of the four "
                                 "bands (misread or altered): check by eye"))
            if f.get("cefr"):
                ok = _ielts_cefr(f["overall"]) == f["cefr"]
                findings.append((PASS if ok else FLAG, "cefr", "CEFR level agrees with the overall band" if ok
                                 else "CEFR level does not agree with the overall band"))
        elif not f.get("bands_invalid") and not f.get("bands_read"):
            findings.append((FLAG, "arithmetic", "band scores not read: arithmetic not checked"))
        findings += trf
        minimum = MIN_SCORES.get(prog, {}).get("IELTS")
    elif test == "TOPIK":
        f = fields = parse_topik(text)
        findings += _identity(f, passport, covered_fn, f.get("name"), False, False)
        v, why = _validity("TOPIK", f["test_date"], today, f.get("valid_until"))
        if f.get("valid_until") is None and v == PASS:
            v, why = FLAG, why + " (Valid Until not read: test date + 2 years used)"
        findings.append((v, "validity", why))
        if f.get("valid_until") and f.get("test_date"):
            days = (f["valid_until"] - add_years(f["test_date"], 2)).days
            if not 0 <= days <= 90:
                findings.append((FLAG, "valid_until", "Valid Until is not about 2 years after the test (+ result "
                                 "announcement)"))
        lt = f.get("level_test")
        if lt and f["sections"] and len(f["sections"]) == (3 if lt == "II" else 2) and f["total"] is not None:
            ok = sum(f["sections"]) == f["total"] and f.get("total_of") == TOPIK_MAX[lt]
            findings.append((PASS if ok else FLAG, "arithmetic", "total = sum of the sections" if ok else
                             "total does not equal the sum of the sections (misread or altered)"))
            f["level_from_total"] = topik_level(lt, f["total"])
            if f.get("level") is not None:
                ok = f["level_from_total"] == f["level"]
                findings.append((PASS if ok else FLAG, "level", "level agrees with the total" if ok else
                                 "level does not agree with the total score"))
            else:
                findings.append((NOTE, "level", "level not read (printed in Korean); from the total it is "
                                 "level %s" % f["level_from_total"]))
            if f.get("level_test_conflict"):
                findings.append((FLAG, "level", "TOPIK I/II as printed does not match the total's maximum"))
        else:
            findings.append((FLAG, "arithmetic", "section scores / total not read: arithmetic not checked"))
        findings.append((PASS if f.get("print_no") else FLAG, "number_format",
                         "print number 4x4 digits present" if f.get("print_no") else "print number (4x4 digits) not read"))
        minimum = MIN_SCORES.get(prog, {}).get("TOPIK_LEVEL")
    else:                                   # TOEFL
        f = fields = parse_toefl(text)
        findings += _identity(f, passport, covered_fn, None, False, False)
        v, why = _validity("TOEFL", f["test_date"], today)
        findings.append((v, "validity", why))
        if f.get("variant") == "ITP":
            sec = list(f["sections"].values())
            if all(x is not None for x in sec) and f["total"] is not None:
                ok = (31 <= sec[0] <= 68 and 31 <= sec[1] <= 68 and 31 <= sec[2] <= 67
                      and f["total"] == int(Fraction(sum(sec) * 10, 3) + Fraction(1, 2)))
                findings.append((PASS if ok else FLAG, "arithmetic", "ITP total = sections x 10 / 3" if ok else
                                 "ITP total does not agree with the sections (x 10 / 3) or a section is out of range"))
            else:
                findings.append((FLAG, "arithmetic", "ITP section scores not read"))
            findings.append((FLAG, "test_type", "TOEFL ITP is an institutional (paper) test, not TOEFL iBT: the "
                             "owner decides whether it is accepted"))
        else:
            sec = [f["sections"][k] for k in ("reading", "listening", "speaking", "writing")]
            if all(x is not None for x in sec) and f["total"] is not None:
                if f["scale"] == "0-120":
                    ok = all(0 <= x <= 30 for x in sec) and sum(sec) == f["total"]
                else:
                    ok = abs(ielts_overall(*sec) - f["total"]) < 1e-9    # mean of 4, nearest 0.5 (ETS 1-6)
                findings.append((PASS if ok else FLAG, "arithmetic", "total agrees with the sections" if ok else
                                 "total does not agree with the sections"))
            else:
                findings.append((FLAG, "arithmetic", "section scores not read"))
            rn = f.get("registration_no")
            findings.append((PASS if rn and len(rn) == 16 else FLAG, "number_format",
                             "registration number 16 digits" if rn and len(rn) == 16 else "registration number not read"))
        minimum = MIN_SCORES.get(prog, {}).get("TOEFL_IBT")
    findings.append((NOTE, "minimum", "minimum score for %s not set by the owner yet: not checked" % (prog or "?"))
                    if minimum is None else (NOTE, "minimum", "minimum %s set: compare" % minimum))
    return {"test": test, "fields": fields, "findings": findings, "verdict": _verdict(v for v, _, _ in findings)}
