"""Self-test T0: no personal data in the runner's code, tests or specs (RUNNER_SPEC S9 and section 10).

    C:\\Hangeul\\DOCCHECK\\lab\\private-runner\\.venv-runner\\Scripts\\python.exe -I -B -m src.verify.runner.selftest.pii_scan

It loads, into memory and READ-ONLY (this harness is not the runner process):
  * the portal values in the bot's C:\\Hangeul\\BOT\\data\\verification\\results.json: name parts (student full
    name, given name, surname, father, mother), dates of birth and numbers
    (passport number, guardian phone);
  * the names the runner READ OFF DOCUMENTS in the private runs: the student's, father's and mother's names in
    every C:\\Hangeul\\DOCCHECK\\lab\\private-runner\\runs\\*\\run.json (identity);
and scans every file under src\\verify\\runner, C:\\Hangeul\\DOCCHECK\\lab\\private-runner\\run_case.py,
RUNNER_SPEC.md and APP_SPEC.md for:
  * any of those name parts of 4 or more letters (whole words, any case), except the generic words in
    GENERIC_WORDS, which the specs themselves prescribe;
  * any 10-, 11-, 12-, 13-, 16- or 17-digit run (NID, phone, e-TIN, account and birth-registration shapes);
  * any [A-Z]{1,2}\\d{7,8} token (passport-number shapes, the e-passport's 1 letter + 8 digits included);
  * any portal date of birth, written YYYY-MM-DD, DD/MM/YYYY, DD-MM-YYYY, DD.MM.YYYY or DD MON YYYY;
  * any portal number (passport number, phone), digits only.
An obviously made-up digit run (one digit repeated, or consecutive digits up or down, as in "0000000000" or
"1234567890") is a placeholder and allowed, and so are the placeholder dates in PLACEHOLDER_DATES (1 January
2000 and 2030: a real date of birth can coincide with them) and a line marked "pii-scan: placeholder" or
"pii-scan: not a number". It prints only file:line and the category, never the matched
text, then "clean" or the count. Exit 0 when clean, 1 otherwise.
"""
from __future__ import annotations

import datetime as dt
import json
import re
import sys
from pathlib import Path
from typing import Iterable, List, Optional, Set, Tuple

from src.verify.runner import botimport

RESULTS = Path(r"C:\Hangeul\BOT\data\verification\results.json")
RUNS = botimport.PRIVATE / "runs"
RUNNER = botimport.WORKTREE / "src" / "verify" / "runner"
TARGETS = [RUNNER, botimport.PRIVATE / "run_case.py", botimport.WORKTREE / "RUNNER_SPEC.md",
           botimport.WORKTREE / "APP_SPEC.md"]
NAME_FIELDS = ("Full Name", "Given Name", "Surname", "Father", "Mother")
DATE_FIELDS = ("DOB",)       # issue and expiry dates are not personal, and too many to tell from placeholders
NUMBER_FIELDS = ("Passport No", "Guardian WhatsApp")
MIN_LETTERS = 4
# Generic words that are also somebody's name part and that the spec and code use as vocabulary: the
# program name MASTER (model.PROGRAMS); the honorifics and titles the name rules talk about (MOHAMMAD, A1's
# "MD" vs "MOHAMMAD"; MUHAMMAD; MOST, the long form of MST; BEGAM, an OCR form of a title in the bank
# extractor's notes; BEGUM and KHATUN, the women's name endings the strict-name examples use); and the English
# words MOST ("at most"), SAID and MEMBER. Only words that occur in the scanned files are listed; every other
# part of a real name is still a finding.
GENERIC_WORDS = frozenset({"MASTER", "MOHAMMAD", "MUHAMMAD", "MOST", "BEGAM", "BEGUM", "KHATUN", "SAID",
                           "MEMBER"})
TEXT_SUFFIXES = (".py", ".md", ".txt", ".json", ".cfg", ".toml", ".ini", ".csv")
PLACEHOLDER_DATES = frozenset({dt.date(2000, 1, 1), dt.date(2030, 1, 1)})
_ALLOW = re.compile(r"pii-scan: (?:placeholder|not a number)")
MONTHS = {m: i for i, m in enumerate(("JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV",
                                      "DEC"), 1)}

_DIGITS = re.compile(r"(?<!\d)(\d{10,13}|\d{16,17})(?!\d)")
_PASSPORT = re.compile(r"(?<![A-Za-z0-9])[A-Z]{1,2}(\d{7,8})(?!\d)")
_WORD = re.compile(r"[A-Za-z]{%d,}" % MIN_LETTERS)
_ISO = re.compile(r"(?<!\d)(\d{4})-(\d{1,2})-(\d{1,2})(?!\d)")
_DMY = re.compile(r"(?<!\d)(\d{1,2})[/.\-](\d{1,2})[/.\-](\d{4})(?!\d)")
_DMONY = re.compile(r"(?<!\d)(\d{1,2})\s*([A-Za-z]{3})[A-Za-z]*\s*(\d{4})(?!\d)")
_ANY_DIGITS = re.compile(r"\d{6,}")


def placeholder_digits(s: str) -> bool:
    """One digit repeated, or consecutive digits counting up or down (mod 10): made-up, not a real number."""
    if len(set(s)) == 1:
        return True
    d = [int(c) for c in s]
    steps = {(b - a) % 10 for a, b in zip(d, d[1:])}
    return steps in ({1}, {9})


def _date(y: int, m: int, d: int) -> Optional[dt.date]:
    try:
        return dt.date(y, m, d)
    except ValueError:
        return None


def parse_date(s: str) -> Optional[dt.date]:
    s = (s or "").strip()
    for rx, order in ((_ISO, "ymd"), (_DMY, "dmy")):
        m = rx.search(s)
        if m:
            a, b, c = (int(x) for x in m.groups())
            return _date(a, b, c) if order == "ymd" else _date(c, b, a)
    m = _DMONY.search(s)
    if m and m.group(2).upper() in MONTHS:
        return _date(int(m.group(3)), MONTHS[m.group(2).upper()], int(m.group(1)))
    return None


def _results(results: Path) -> dict:
    return json.loads(results.read_text(encoding="utf-8"))


def _rows(data: dict):
    for rec in (data.get("fields") or {}).values():
        for row in (rec.get("rows") or []) if isinstance(rec, dict) else []:
            if isinstance(row, dict) and isinstance(row.get("portal"), str):
                yield row.get("field"), row["portal"]


def portal_name_parts(results: Path = RESULTS) -> Set[str]:
    """Upper-case name parts of >= 4 letters of every portal name in results.json (memory only)."""
    data = _results(results)
    names: List[str] = []
    for rec in (data.get("documents") or {}).values():
        if isinstance(rec, dict) and isinstance(rec.get("student"), str):
            names.append(rec["student"])
    for rec in (data.get("fields") or {}).values():
        if isinstance(rec, dict) and isinstance(rec.get("student"), str):
            names.append(rec["student"])
    names += [v for f, v in _rows(data) if f in NAME_FIELDS]
    for rec in data.get("corrections") or []:
        if isinstance(rec, dict) and isinstance(rec.get("student"), str):
            names.append(rec["student"])
    return _parts(names)


def _parts(names: Iterable[str]) -> Set[str]:
    parts = set()
    for n in names:
        parts.update(w.upper() for w in re.findall(r"[A-Za-z]+", n or "") if len(w) >= MIN_LETTERS)
    return parts - GENERIC_WORDS


def run_name_parts(runs: Path = RUNS) -> Set[str]:
    """Name parts the runner read off documents in the private runs (run.json identity: student, father,
    mother; written and parts). Memory only."""
    names: List[str] = []
    for f in sorted(Path(runs).glob("*/run.json")) if Path(runs).is_dir() else []:
        try:
            ident = (json.loads(f.read_text(encoding="utf-8")) or {}).get("identity") or {}
        except (OSError, ValueError):
            continue
        for who in ("student", "father", "mother"):
            ref = ident.get(who) or {}
            if isinstance(ref, dict):
                names.append(str(ref.get("written") or ""))
                names.append(" ".join(str(p) for p in (ref.get("parts") or [])))
    return _parts(names)


def portal_dates(results: Path = RESULTS) -> Set[dt.date]:
    return {d for f, v in _rows(_results(results)) if f in DATE_FIELDS for d in [parse_date(v)] if d}


def portal_numbers(results: Path = RESULTS) -> Set[str]:
    out = set()
    for f, v in _rows(_results(results)):
        if f in NUMBER_FIELDS:
            digits = re.sub(r"\D", "", v)
            if len(digits) >= 7:
                out.add(digits)
                if f == "Guardian WhatsApp" and digits.startswith("880"):
                    out.add("0" + digits[3:])
    return out


def files_to_scan(targets: Iterable[Path] = TARGETS) -> List[Path]:
    out: List[Path] = []
    for t in targets:
        t = Path(t)
        if t.is_dir():
            out.extend(sorted(f for f in t.rglob("*")
                              if f.is_file() and "__pycache__" not in f.parts and f.suffix.lower() in TEXT_SUFFIXES))
        elif t.is_file():
            out.append(t)
    return out


def _line_dates(line: str) -> Set[dt.date]:
    out = set()
    for m in _ISO.finditer(line):
        d = _date(*(int(x) for x in m.groups()))
        if d:
            out.add(d)
    for m in _DMY.finditer(line):
        a, b, c = (int(x) for x in m.groups())
        d = _date(c, b, a)
        if d:
            out.add(d)
    for m in _DMONY.finditer(line):
        if m.group(2).upper() in MONTHS:
            d = _date(int(m.group(3)), MONTHS[m.group(2).upper()], int(m.group(1)))
            if d:
                out.add(d)
    return out


def scan_text(text: str, parts: Set[str], dates: Set[dt.date] = frozenset(),
              numbers: Set[str] = frozenset()) -> List[Tuple[int, str]]:
    """(line number, category) of every hit; the matched text is never returned."""
    hits: List[Tuple[int, str]] = []
    for no, line in enumerate(text.splitlines(), 1):
        if any(w.upper() in parts for w in _WORD.findall(line)):
            hits.append((no, "real name part"))
        if _ALLOW.search(line):
            continue
        if any(not placeholder_digits(m.group(1)) for m in _DIGITS.finditer(line)):
            hits.append((no, "10-13/16/17-digit run"))
        if any(not placeholder_digits(m.group(1)) for m in _PASSPORT.finditer(line)):
            hits.append((no, "passport-like token"))
        if dates and (_line_dates(line) - PLACEHOLDER_DATES) & set(dates):
            hits.append((no, "real date"))
        if numbers:
            runs = _ANY_DIGITS.findall(line)
            if any(n in r for r in runs for n in numbers):
                hits.append((no, "real number"))
    return hits


def _label(f: Path) -> str:
    for root in (botimport.WORKTREE, botimport.PRIVATE):
        try:
            return str(f.relative_to(root))
        except ValueError:
            continue
    return f.name


def main() -> int:
    parts = portal_name_parts() | run_name_parts()
    dates, numbers = portal_dates(), portal_numbers()
    files = files_to_scan()
    total = 0
    for f in files:
        try:
            text = f.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            text = f.read_text(encoding="utf-8", errors="replace")
        for no, cat in scan_text(text, parts, dates, numbers):
            print(f"{_label(f)}:{no}  {cat}")
            total += 1
    print(f"scanned {len(files)} file(s) against {len(parts)} name part(s) (portal and document reads), "
          f"{len(dates)} date(s) and {len(numbers)} number(s)")
    print("clean" if total == 0 else f"{total} finding(s)")
    return 0 if total == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
