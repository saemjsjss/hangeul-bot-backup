"""
Field check — does the portal's record agree with the student's documents?

The progress sheet holds ~32 fields per student, but only the ones below can be proved
from documents (the rest are workflow fields: Student ID, Email, Mobile, Program,
Passport Status, Bank Certificate, Sponsor, Visa Rejection History, Study Status).

For each checkable field the value is looked for in the documents that could carry it:

  MATCH          the value appears in the right document
  DIFFERS        that document clearly holds a different value — read after its label
                 ("Father's Name:", "Sex:", "GPA") or from the passport's machine-readable
                 line (worth a human's eye)
  NOT CONFIRMED  the document reads well, but the value is not on it and no other value
                 could be read in its place (OCR may have missed it)
  UNREADABLE     none of the documents that could carry it could be read at all

Words are matched whole: "ali" is not found in "khalil", nor "male" in "female". An ID
card / CV is recorded as present only and is never read.

CLI (run from the BOT folder):
  python -m src.verify.field_check --passport A99900001
  python -m src.verify.field_check --program BACHELOR
  python -m src.verify.field_check --program MASTER --report master_fields.xlsx
"""
from __future__ import annotations

import argparse
import datetime as dt
import difflib
import logging
import re
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from src.sheets import progress_builder as pb
from src.verify import doc_verifier as dv
from src.verify import rules as R
from src.verify.common import name_in, name_tokens
from src.verify.doc_verifier import (REPORT_DIR, classify, find_dates, find_money_taka, norm,
                                     portal_students, program_of, read_document, readable,
                                     student_folders)

logger = logging.getLogger(__name__)

MATCH, DIFFERS, UNREADABLE, NO_DOC = "MATCH", "DIFFERS", "UNREADABLE", "NO DOCUMENT"
NOT_CONFIRMED = "NOT CONFIRMED"

# field -> the documents that could carry it (matched against the report's document titles)
SOURCES: Dict[str, List[str]] = {
    "Full Name": ["01 Passport", "03 Student NID", "04 Birth", "06 Family", "07 Academic", "10 Affidavit"],
    "Surname": ["01 Passport", "07 Academic", "04 Birth"],
    "Given Name": ["01 Passport", "07 Academic", "04 Birth"],
    "DOB": ["01 Passport", "03 Student NID", "04 Birth", "06 Family", "07 Academic"],
    "Gender": ["01 Passport", "03 Student NID", "04 Birth"],
    "District": ["01 Passport", "03 Student NID", "04 Birth", "05 ", "06 Family"],
    "Address": ["01 Passport", "03 Student NID", "04 Birth", "05 ", "06 Family"],
    "Father": ["01 Passport", "03 Student NID", "04 Birth", "05 Father", "06 Family", "07 Academic"],
    "Mother": ["01 Passport", "03 Student NID", "04 Birth", "05 Mother", "06 Family", "07 Academic"],
    "SSC Year": ["07 Academic"], "SSC GPA": ["07 Academic"], "SSC Group": ["07 Academic"],
    "SSC School": ["07 Academic"], "HSC Year": ["07 Academic"], "HSC GPA": ["07 Academic"],
    "HSC Group": ["07 Academic"], "HSC College": ["07 Academic"], "CGPA": ["07 Academic"],
    "Previous University": ["07 Academic"],
    "Passport No": ["01 Passport"], "Passport Expiry": ["01 Passport"], "Passport Issue": ["01 Passport"],
    "Guardian WhatsApp": ["01 Passport", "09 Financial"],
    "Sponsor Occupation": ["09 Financial"],
}
DATE_FIELDS = {"DOB", "Passport Expiry", "Passport Issue"}
NUMBER_FIELDS = {"Passport No", "Guardian WhatsApp", "SSC Year", "HSC Year"}
GPA_FIELDS = {"SSC GPA", "HSC GPA", "CGPA"}
NAME_FIELDS = {"Full Name", "Surname", "Given Name", "Father", "Mother"}
# the people whose name a document labels: field -> how the detail names the label
LABELLED_PEOPLE = {"Full Name": "name", "Father": "father's name", "Mother": "mother's name"}


_OCR_SWAPS = str.maketrans({"a": "4", "o": "0", "i": "1", "l": "1", "s": "5", "b": "8",
                            "A": "4", "O": "0", "I": "1", "L": "1", "S": "5", "B": "8"})


def _ocr_variant(s: str) -> str:
    """Normalise the letters OCR most often turns into digits, so A00990007 and the
    scanned '400990007' compare equal."""
    return re.sub(r"\W", "", str(s).lower()).translate(_OCR_SWAPS)


def _docs_for(field: str, texts: Dict[str, str]) -> Dict[str, str]:
    pats = SOURCES.get(field, [])
    return {k: v for k, v in texts.items() if any(k.startswith(p) or p in k for p in pats)}


def _validity_consistent(issue: str, expiry: str) -> str:
    """A Bangladeshi passport is issued for exactly 5 or 10 years, so the issue date is
    provable from the expiry date alone: expiry = issue + 5 or 10 years (less a day)."""
    try:
        i = dt.date.fromisoformat(pb.normalize_date(issue))
        e = dt.date.fromisoformat(pb.normalize_date(expiry))
    except ValueError:
        return ""
    for years in (5, 10):
        try:
            due = i.replace(year=i.year + years)
        except ValueError:                     # 29 February
            due = i.replace(year=i.year + years, day=28)
        if abs((e - due).days) <= 3:
            return f"{years}-year passport: issue {i} + {years} years = expiry {e}"
    return ""


# --- whole words ---------------------------------------------------------------------
def _words_in(text: str, value: str) -> bool:
    """The value's words (3+ characters) are whole words of the text — all but one when
    there are several, since OCR often loses one."""
    toks = list(dict.fromkeys(t for t in re.findall(r"[a-z0-9]+", norm(value)) if len(t) > 2))
    if not toks:
        return False
    words = set(re.findall(r"[a-z0-9]+", norm(text)))
    return sum(1 for t in toks if t in words) >= max(1, len(toks) - 1)


# --- names written after a label -------------------------------------------------------
# "Name:", "Father's Name:", "Name of Mother", "Name of Student". The word before "name"
# says whose name it is; on one line of OCR text it is often just the end of the value
# before, which counts as a plain "Name".
_NAME_LABEL = re.compile(r"(?:\b(?P<who>[a-z]+)(?:['’`]s)?[ \t]+)?\bname\b"
                         r"(?:[ \t]+of[ \t]+(?:the[ \t]+)?(?P<of>[a-z]+))?")
_NAME_VALUE = re.compile(r"[ \t]*[:;=\-–.]?[ \t]*([a-z][a-z.]*(?:[ \t]+[a-z][a-z.]*){0,7})")
_LABEL_FATHER = {"father", "fathers"}
_LABEL_MOTHER = {"mother", "mothers"}
_LABEL_SELF = {"student", "students", "candidate", "candidates", "child", "examinee", "person",
               "persons", "full", "your", "registered"}
# names that belong to someone else, or to a thing
_LABEL_OTHER = {"given", "sur", "husband", "husbands", "spouse", "spouses", "wife", "wifes",
                "guardian", "guardians", "bank", "account", "holder", "holders", "branch",
                "school", "college", "institute", "institution", "university", "board",
                "company", "business", "firm", "proprietor", "proprietors", "sponsor",
                "sponsors", "nominee", "nominees", "officer", "authority", "signatory",
                "notary", "advocate", "witness", "deponent", "applicant", "applicants",
                "contact", "emergency", "organization", "organisation", "brother", "sister",
                "son", "daughter", "grandfather", "grandmother", "uncle", "aunt", "centre",
                "center", "exam", "examination", "user", "file", "district", "village", "law"}
# where a name written after its label ends: the next label, or a joining word
_NAME_STOP = {"name", "names", "father", "fathers", "mother", "mothers", "husband", "husbands",
              "spouse", "guardian", "date", "birth", "dob", "sex", "gender", "address",
              "present", "permanent", "nid", "id", "no", "number", "place", "relation",
              "relationship", "religion", "nationality", "occupation", "profession", "village",
              "vill", "post", "office", "district", "dist", "upazila", "thana", "police",
              "station", "roll", "reg", "registration", "issue", "issued", "blood", "group",
              "signature", "son", "daughter", "wife", "of", "ward", "union", "mobile", "phone",
              "school", "college", "institute", "board", "exam", "examination", "year",
              "session", "passport", "type", "code", "country", "personal", "surname", "given",
              "holder", "marital", "status", "age", "serial", "sl", "gpa", "result", "centre",
              "center", "and", "is", "was", "in", "at", "on", "for", "the", "to", "from", "by",
              "with", "dated", "pin", "house", "road", "holding", "citizen", "sector", "block"}


def labelled_names(text: str) -> Dict[str, List[str]]:
    """Names written after a "Name" label, by whose they are: {"Full Name": [...],
    "Father": [...], "Mother": [...]}, each value lower-cased."""
    out: Dict[str, List[str]] = {f: [] for f in LABELLED_PEOPLE}
    low = (text or "").lower()
    for m in _NAME_LABEL.finditer(low):
        who, of = m.group("who") or "", m.group("of") or ""
        if of:
            field = ("Father" if of in _LABEL_FATHER else "Mother" if of in _LABEL_MOTHER
                     else "Full Name" if of in _LABEL_SELF else "")
        elif who in _LABEL_FATHER:
            field = "Father"
        elif who in _LABEL_MOTHER:
            field = "Mother"
        elif who in _LABEL_OTHER:
            field = ""
        else:
            field = "Full Name"
        if not field:
            continue
        vm = _NAME_VALUE.match(low, m.end())
        if not vm:
            continue
        words = vm.group(1).split()
        while words and words[0] in ("is", "was"):
            words.pop(0)
        kept: List[str] = []
        for w in words[:6]:
            if w.replace(".", "") in _NAME_STOP:
                break
            kept.append(w)
        if kept:
            out[field].append(" ".join(kept))
    return out


_LABEL_WORDS = _LABEL_FATHER | _LABEL_MOTHER | _LABEL_SELF | _LABEL_OTHER


def without_name_labels(text: str) -> str:
    """The text with its name labels taken out ("Father's Name", "Name of Student"), so
    that the label's own words are not taken for part of a name. A word before "Name"
    that is not a label word stays: on one line of OCR text it ends the value before."""
    low = (text or "").lower()

    def cut(m: re.Match) -> str:
        who = m.group("who") or ""
        keep = low[m.start():m.end("who")] if who and who not in _LABEL_WORDS else ""
        return keep + " "

    return _NAME_LABEL.sub(cut, low)


def _best_ratio(token: str, words: List[str]) -> float:
    return max((difflib.SequenceMatcher(None, token, w).ratio() for w in words), default=0.0)


def _name_differs(read: str, portal: str, others: List[str]) -> bool:
    """A name read after its label is clearly not the portal's: it has at least two name
    parts, the portal name is not in it, and the parts it lacks are not near-misses
    (difflib ratio below 0.6 to every part read — an OCR misreading stays above that).
    A name that is another person on the student's record (the label was the father's,
    say) proves nothing about this one."""
    parts = name_tokens(read)
    if len(parts) < 2 or name_in(read, portal) is not False:
        return False
    if any(o and name_in(read, o) for o in others):
        return False
    missing = [t for t in name_tokens(portal) if t not in parts]
    return bool(missing) and all(_best_ratio(t, parts) < 0.6 for t in missing)


# --- sex ---------------------------------------------------------------------------
_SEX_LABEL = re.compile(r"\b(?:sex|gender)\b[^a-z0-9\n]{0,12}(female|male|f|m)\b")


def _sex(value: str) -> str:
    """"M", "F" or "" for a portal or document value."""
    v = norm(value)
    if v in ("m", "male", "man", "boy"):
        return "M"
    if v in ("f", "female", "woman", "girl"):
        return "F"
    return ""


def _mrz_sex(text: str) -> str:
    """The sex in the passport's machine-readable line, when the line was read well
    enough to trust (doc_verifier.passport_mrz; "" without it)."""
    reader = getattr(dv, "passport_mrz", None)
    if not callable(reader):
        return ""
    try:
        mrz = reader(text) or {}
    except Exception as e:                    # an MRZ that cannot be parsed is simply not read
        logger.debug("MRZ not read: %s", e)
        return ""
    if not any(mrz.get(k) for k in ("line2_ok", "passport_no_ok", "dob_ok", "expiry_ok")):
        return ""
    return _sex(str(mrz.get("sex") or ""))


def _gender_on(portal: str, text: str) -> Tuple[str, str]:
    """(how it matched, "") on a match; ("", the sex read instead) when the document
    labels — or its machine-readable line gives — another one; ("", "") otherwise."""
    low = (text or "").lower()
    mrz = _mrz_sex(text)
    if mrz:
        if mrz == portal:
            return "found in the machine-readable line of {doc}", ""
        return "", mrz
    labelled = {_sex(m.group(1)) for m in _SEX_LABEL.finditer(low)}
    if labelled:
        if portal in labelled:
            return "found on {doc}", ""
        return "", sorted(labelled)[0]
    words = set(re.findall(r"[a-z]+", low)) & {"male", "female"}
    if words == {"male" if portal == "M" else "female"}:
        return "found on {doc}", ""
    return "", ""


# --- GPA ---------------------------------------------------------------------------
_GPA_LABEL = re.compile(r"\b(?:c\.?\s?g\.?\s?p\.?\s?a|g\.?\s?p\.?\s?a|grade\s+point\s+average)\b\.?")
_NUMBER = re.compile(r"(?<![\d.])\d+(?:\.\d+)?")
_GPA_FORM = re.compile(r"\d(?:\.\d{1,2})?")
_GPA_DECIMAL = re.compile(r"(?<![\d.])\d\.\d{1,2}(?!\d)(?!\.\d)")


def _gpa_value(value: str) -> Optional[float]:
    try:
        return round(float(str(value).strip()), 2)
    except ValueError:
        return None


def labelled_gpas(text: str) -> List[float]:
    """GPAs written after a GPA label ("GPA 5.00", "G.P.A.: 4.83", "CGPA 3.45 out of
    4.00"): the first number after it, skipping a scale ("out of 5"). A bare integer
    counts here, because the label says what it is."""
    low = (text or "").lower()
    out: List[float] = []
    for m in _GPA_LABEL.finditer(low):
        window = low[m.end():m.end() + 40]
        for n in _NUMBER.finditer(window):
            if re.search(r"(?:out\s+of|scale(?:\s+of)?)[\s:]*$", window[:n.start()]):
                continue
            if _GPA_FORM.fullmatch(n.group(0)):
                out.append(round(float(n.group(0)), 2))
            break
    return out


def _gpa_on(target: float, text: str) -> Tuple[bool, List[float], bool]:
    """(matched, the labelled GPAs, the value appears as a decimal anywhere). Unlabelled
    decimals count only on a document with no GPA label, since a transcript's subject
    grade points (5.00, 4.00, 3.50) are decimals too."""
    labelled = labelled_gpas(text)
    loose = any(round(float(d), 2) == target for d in _GPA_DECIMAL.findall(text or ""))
    if target in labelled:
        return True, labelled, loose
    if not labelled and loose:
        return True, labelled, loose
    return False, labelled, loose


def check_field(field: str, value: str, texts: Dict[str, str],
                student: Optional[Dict[str, str]] = None) -> Tuple[str, str]:
    docs = _docs_for(field, texts)
    if not docs:
        return NO_DOC, "none of the documents that carry this field were uploaded"
    v = str(value).strip()
    sex = _sex(v) if field == "Gender" else ""
    gpa = _gpa_value(v) if field in GPA_FIELDS else None
    others = []                                # the other people on the record (labelled names)
    if field in LABELLED_PEOPLE and student:
        others = [str(student.get(f) or "") for f in LABELLED_PEOPLE if f != field]
    differs: List[str] = []                    # values read after a label instead of this one
    loose_gpa = False
    for name, text in docs.items():
        if field in DATE_FIELDS:
            try:
                d = dt.date.fromisoformat(pb.normalize_date(v))
            except ValueError:
                continue
            if d in find_dates(text):
                return MATCH, f"found on {name}"
            # passports carry the date again as YYMMDD in the machine-readable lines
            if re.search(r"P<[A-Z<]{3,}", text.upper()) or "<<<" in text:
                if d.strftime("%y%m%d") in re.sub(r"[^0-9]", "", text):
                    return MATCH, f"found in the machine-readable line of {name}"
        elif field in NUMBER_FIELDS:
            flat = re.sub(r"\W", "", norm(text))
            if re.sub(r"\W", "", v).lower() in flat:
                return MATCH, f"found on {name}"
            # OCR confuses letters and digits on passport scans: A<->4, O<->0, I<->1, S<->5, B<->8
            if _ocr_variant(v) in _ocr_variant(flat):
                return MATCH, f"found on {name} (OCR read a digit for a letter)"
        elif sex:
            how, other = _gender_on(sex, text)
            if how:
                return MATCH, how.format(doc=name)
            if other and (readable(text) or _mrz_sex(text)):
                differs.append(f"{name} shows {'FEMALE' if other == 'F' else 'MALE'}")
        elif gpa is not None:
            matched, labelled, loose = _gpa_on(gpa, text)
            if matched:
                return MATCH, f"found on {name}"
            loose_gpa = loose_gpa or loose
            if labelled:
                shown = ", ".join(f"{x:.2f}" for x in sorted(set(labelled)))
                differs.append(f"{name} shows GPA {shown}")
        elif field in NAME_FIELDS:
            if name_in(without_name_labels(text), v):
                return MATCH, f"found on {name}"
            if field in LABELLED_PEOPLE and readable(text):
                for read in labelled_names(text)[field]:
                    if _name_differs(read, v, others):
                        differs.append(f"{name} shows '{read.upper()}' as the {LABELLED_PEOPLE[field]}")
                        break
        else:
            if _words_in(text, v):
                return MATCH, f"found on {name}"

    # not found: is the document readable (so the value really differs), or just unreadable?
    readable_docs = [n for n, t in docs.items() if readable(t)]
    if not readable_docs and not differs:
        return UNREADABLE, f"could not read {', '.join(docs)} well enough to compare"
    if student and field in ("Passport Issue", "Passport Expiry"):
        issue, expiry = ((v, student.get("Passport Expiry", "")) if field == "Passport Issue"
                         else (student.get("Passport Issue", "") or
                               __import__("src.sheets.passport_issue", fromlist=["load"]).load()
                               .get((student.get("Passport No") or "").upper(), ""), v))
        ok = _validity_consistent(issue, expiry)
        if ok:
            other = "expiry" if field == "Passport Issue" else "issue"
            return MATCH, f"consistent with the passport's {other} date ({ok})"
    where = ", ".join(readable_docs)
    if field in DATE_FIELDS:
        others_seen = sorted({d.isoformat() for n in readable_docs for d in find_dates(docs[n])})
        if others_seen:
            return DIFFERS, f"{where} shows {', '.join(others_seen[:4])} instead"
        return NOT_CONFIRMED, f"not found on {where} (no date could be read there)"
    if field == "Sponsor Occupation" and re.search(r"business", str(v), re.I):
        if any(re.search(r"trade\s*licen", docs[n], re.I) for n in readable_docs):
            return MATCH, ("a trade licence in the sponsor's name is on file — it names the trade "
                           "rather than the word 'business'")
    if field == "Passport No":
        # only a plausible Bangladeshi passport number counts as a real difference
        found = sorted({m.group(0) for n in readable_docs
                        for m in re.finditer(r"\b(?:[A-Z]\d{8}|B[MWXY]\d{7})\b", docs[n].upper())})
        found = [f for f in found if _ocr_variant(f) != _ocr_variant(v)]
        if found:
            return DIFFERS, f"the passport shows {', '.join(found[:3])} instead"
    if differs:
        if gpa is not None and loose_gpa:
            # the portal's GPA is printed on it too, unlabelled: the labelled one may be
            # another certificate's (an academic file holds SSC and HSC)
            return NOT_CONFIRMED, (f"not found after a GPA label on {where} ({'; '.join(differs[:2])}), "
                                   f"though {v} is printed there — check which certificate it is on")
        return DIFFERS, f"{'; '.join(differs[:2])} instead"
    return NOT_CONFIRMED, f"not found on {where} (OCR may have missed it)"


ISSUE_CACHE_MAX_HOURS = 26      # the scheduler refreshes it daily at 08:30


def issue_cache_age_hours() -> Optional[float]:
    """How old the passport-issue cache is, in hours.

    That cache is the one portal value not carried by the students export, so it is read
    separately and can fall behind.  Reporting a difference from a stale copy accuses
    someone of a mistake they have already corrected, which is worse than staying quiet.
    """
    try:
        from src.sheets import passport_issue
        path = passport_issue.CACHE_PATH
        if not path.exists():
            return None
        return (dt.datetime.now() - dt.datetime.fromtimestamp(path.stat().st_mtime)).total_seconds() / 3600
    except Exception as e:
        logger.warning("could not judge the age of the issue-date cache: %s", e)
        return None


def _portal_row(student: Dict[str, str]) -> Tuple[List[str], Dict[str, str]]:
    """This student's progress-sheet columns and their values, as the sheet shows them."""
    program = program_of(student)
    cfg = pb.target(program, pb.normalize_intake(student.get("Intake", "")))
    cols = pb.columns_for(cfg)
    headers = [h for h, _ in cols]
    return headers, dict(zip(headers, pb.build_row(student, cols)))


def checked_portal_values(student: Dict[str, str]) -> Dict[str, str]:
    """Every portal value the field check compares (the sheet's value of each SOURCES
    field this program has), plus the raw Program and Intake that decide those columns.
    A change to anything else — Current Stage, Progress %, Current Status — changes
    nothing here, so it does not mean the student must be checked again."""
    headers, row = _portal_row(student)
    out = {h: row.get(h, "") for h in headers if h in SOURCES}
    out["Program"] = str(student.get("Program", "") or "")
    out["Intake"] = str(student.get("Intake", "") or "")
    return out


def check_student(pas: str, folder: Path, student: Dict[str, str]) -> List[Dict[str, object]]:
    headers, row = _portal_row(student)
    # an ID card / CV is recorded as present only: it is never read (rules.PRESENCE_ONLY)
    presence_only = getattr(R, "PRESENCE_ONLY", ())
    texts: Dict[str, str] = {}
    for f in sorted(folder.iterdir()):
        if not f.is_file() or f.name.startswith("."):
            continue
        key = classify(f.name)
        if key in presence_only:
            continue
        title = R.TITLES.get(key, key or f.stem)
        try:
            t, _ = read_document(f)
        except Exception as e:
            t = ""
            logger.warning("%s unreadable: %s", f.name, e)
        texts[title] = texts.get(title, "") + "\n" + t
    out = []
    for field in headers:
        if field not in SOURCES:
            continue
        value = row.get(field, "")
        if not value:
            out.append({"field": field, "portal": "", "result": "BLANK",
                        "detail": "nothing on the portal to check"})
            continue
        result, detail = check_field(field, value, texts, student)
        if field == "Passport Issue" and result == DIFFERS:
            age = issue_cache_age_hours()
            if age is None or age > ISSUE_CACHE_MAX_HOURS:
                result = NOT_CONFIRMED
                detail = (f"the portal's issue date is held in a cache that is "
                          f"{'missing' if age is None else f'{age:.0f} hours old'} — refresh it "
                          "before treating this as an error "
                          "(python -m src.sheets.passport_issue --refresh)")
        out.append({"field": field, "portal": value, "result": result, "detail": detail})
    return out


def write_report(rows: List[Dict[str, object]], path: Path) -> Path:
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill
    colour = {MATCH: "C6EFCE", DIFFERS: "FFC7CE", NOT_CONFIRMED: "DDEBF7", UNREADABLE: "FFEB9C",
              NO_DOC: "F2F2F2", "BLANK": "FFFFFF"}
    path.parent.mkdir(parents=True, exist_ok=True)
    wb = Workbook()
    ws = wb.active
    ws.title = "Field check"
    ws.append(["Student", "Passport", "Program", "Field", "Portal value", "Result", "Detail"])
    for c in ws[1]:
        c.font = Font(bold=True)
    for r in rows:
        ws.append([r["student"], r["passport"], r["program"], r["field"], r["portal"], r["result"], r["detail"]])
        ws.cell(row=ws.max_row, column=6).fill = PatternFill("solid", fgColor=colour.get(str(r["result"]), "FFFFFF"))
    ws.freeze_panes = "A2"
    for col, w in zip("ABCDEFG", (28, 13, 11, 22, 30, 14, 70)):
        ws.column_dimensions[col].width = w

    ws2 = wb.create_sheet("Summary")
    ws2.append(["Student", "Passport", "Program", "Checked", "Match", "Differs", "Not confirmed",
                "Unreadable", "No document", "Blank"])
    for c in ws2[1]:
        c.font = Font(bold=True)
    per: Dict[str, Dict[str, object]] = {}
    for r in rows:
        s = per.setdefault(str(r["passport"]), {"student": r["student"], "program": r["program"],
                                                MATCH: 0, DIFFERS: 0, NOT_CONFIRMED: 0, UNREADABLE: 0,
                                                NO_DOC: 0, "BLANK": 0})
        s[str(r["result"])] = int(s.get(str(r["result"]), 0)) + 1
    for pas, s in per.items():
        checked = int(s[MATCH]) + int(s[DIFFERS]) + int(s[NOT_CONFIRMED]) + int(s[UNREADABLE])
        ws2.append([s["student"], pas, s["program"], checked, s[MATCH], s[DIFFERS], s[NOT_CONFIRMED],
                    s[UNREADABLE], s[NO_DOC], s["BLANK"]])
        if int(s[DIFFERS]):
            ws2.cell(row=ws2.max_row, column=6).fill = PatternFill("solid", fgColor="FFC7CE")
    ws2.freeze_panes = "A2"
    for col, w in zip("ABCDEFGHIJ", (28, 13, 11, 9, 8, 9, 14, 12, 13, 8)):
        ws2.column_dimensions[col].width = w
    wb.save(path)
    return path


def main() -> None:
    logging.basicConfig(level=logging.WARNING, format="%(asctime)s  %(message)s")
    ap = argparse.ArgumentParser(description="Check portal fields against the student's documents.")
    ap.add_argument("--passport", help="one student")
    ap.add_argument("--program", help="KLP | EAP | BACHELOR | MASTER")
    ap.add_argument("--report", default="", help="report file name")
    ap.add_argument("--skip", default="", help="comma-separated passports to skip")
    args = ap.parse_args()

    folders, portal = student_folders(), portal_students()
    skip = {s.strip().upper() for s in args.skip.split(",") if s.strip()}
    if args.passport:
        picks = [args.passport.strip().upper()]
    elif args.program:
        key = args.program.strip().upper()
        picks = sorted(p for p in folders if p in portal and p not in skip
                       and program_of(portal[p]) == key)
    else:
        ap.print_help()
        return

    out = REPORT_DIR / (args.report or f"field_check_{dt.datetime.now():%Y-%m-%d_%H%M}.xlsx")
    rows: List[Dict[str, object]] = []
    for i, pas in enumerate(picks, 1):
        if pas not in folders or pas not in portal:
            continue
        student = portal[pas]
        res = check_student(pas, folders[pas][0], student)
        counts: Dict[str, int] = {}
        for r in res:
            counts[str(r["result"])] = counts.get(str(r["result"]), 0) + 1
            rows.append({**r, "student": student.get("Full Name", ""), "passport": pas,
                         "program": program_of(student)})
        print(f"[{i}/{len(picks)}] {student.get('Full Name', '')[:28]:30} "
              f"match {counts.get(MATCH, 0):>2} | differs {counts.get(DIFFERS, 0)} | "
              f"not confirmed {counts.get(NOT_CONFIRMED, 0)} | "
              f"unreadable {counts.get(UNREADABLE, 0)}", flush=True)
        write_report(rows, out)
    print(f"\nreport: {out}")


if __name__ == "__main__":
    main()
