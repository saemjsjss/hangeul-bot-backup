"""
The document check's shared contract: verdict names, the error types, and the name and
sponsor rules every part of the check (doc_verifier, page_checks, field_check,
auto_verify) must agree on.

Nothing here reads a file, the portal or the network, so it is safe to import anywhere.

Verdicts on a single finding or document row:
  PASS · NOTE (worth a glance, never changes a verdict) · FLAG (needs a human eye)
  FAIL (rule broken) · MISSING (required, not uploaded)
  ERROR (the file could not be CHECKED — locked, unrenderable, OCR crashed; never a
         problem with the document itself, and retried on the next pass)
Verdicts on a whole student:
  INCOMPLETE > FAIL > ERROR > REVIEW > PASS
"""
from __future__ import annotations

import difflib
import os
import re
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

# Bump whenever any rule in rules / doc_verifier / page_checks / field_check changes: every
# stored verdict made under another version is re-evaluated from the saved text.
RULES_VERSION = "2026-10-21.2"

PASS, FLAG, FAIL, MISSING, NOTE, ERROR = "PASS", "FLAG", "FAIL", "MISSING", "NOTE", "ERROR"
INCOMPLETE, REVIEW = "INCOMPLETE", "REVIEW"


class CheckError(Exception):
    """A technical failure while checking a file: it is locked, cannot be rendered, or OCR
    or zbar broke. Never a problem with the document itself — the row becomes ERROR and
    the file is checked again on the next pass."""


class NotCached(CheckError):
    """Cache-only mode: the text this check needs was never saved, so it cannot be judged
    without reading the file again (OCR)."""

    def __init__(self, path: Any, what: str = "text"):
        self.path = path
        self.what = what
        name = getattr(path, "name", None) or os.path.basename(str(path))
        super().__init__(f"no saved {what} for {name} — it needs a fresh read")


def norm(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "")).strip().lower()


# --- names -------------------------------------------------------------------------
# Titles and religious prefixes that are part of how a name is written, not of who it is.
# They are dropped wherever they sit: OCR and the portal put "MD" first, last or in the
# middle ("SAMPLE MD STUDENT"), and spell Muhammad and Mosammat a dozen ways.
HONORIFICS = frozenset({"md", "mst", "most", "mr", "mrs", "miss", "ms", "late", "sk", "mohd"})
HONORIFIC_RE = re.compile(r"^(?:m[ou]h[a]?m+[aeo]?d|mos+[ao]m+[ae]t)$")
# muhammad / mohammad / mohammed / mohamad / muhammed / mohammod
# mosammat / mossammat / mosamat / mosammet

_FUZZY_MIN_LEN = 5        # shorter tokens must match exactly: "ali" is not "alim"
_FUZZY_RATIO = 0.85


def _honorific(token: str) -> bool:
    return token in HONORIFICS or bool(HONORIFIC_RE.match(token))


def name_tokens(name: str) -> List[str]:
    """The parts of a name that identify the person, in order: lower-cased, split on
    anything that is not a letter, honorifics dropped wherever they are, parts shorter
    than 3 letters (initials) dropped, repeats dropped."""
    out: List[str] = []
    for tok in re.split(r"[^a-z]+", norm(name)):
        if len(tok) < 3 or _honorific(tok) or tok in out:
            continue
        out.append(tok)
    return out


def _word_hit(token: str, words: Sequence[str], exact: set) -> bool:
    """`token` is one of the text's words, or — when it is long enough for a misread
    letter not to change who it is — a word of nearly the same length that OCR spelled
    almost the same."""
    if token in exact:
        return True
    if len(token) < _FUZZY_MIN_LEN:
        return False
    for w in words:
        if abs(len(w) - len(token)) > 1:
            continue
        sm = difflib.SequenceMatcher(None, token, w)
        if (sm.real_quick_ratio() >= _FUZZY_RATIO and sm.quick_ratio() >= _FUZZY_RATIO
                and sm.ratio() >= _FUZZY_RATIO):
            return True
    return False


def name_in(text: str, name: str) -> Optional[bool]:
    """Whether `name` is written in `text`; None when the name has nothing to look for.

    Each name part must be a WHOLE word of the text ("ali" is not found in "khalil"),
    allowing one misread letter in parts of 5 letters or more. A name of 1-2 parts needs
    every part; a longer one may miss one (OCR often loses a single word)."""
    toks = name_tokens(name)
    if not toks:
        return None
    words = sorted(set(re.findall(r"[a-z]{3,}", norm(text))))
    exact = set(words)
    hits = sum(1 for tok in toks if _word_hit(tok, words, exact))
    need = len(toks) if len(toks) <= 2 else len(toks) - 1
    return hits >= need


# --- sponsor -------------------------------------------------------------------------
_SELF_WORDS = {"self", "own", "student", "applicant"}
# "step father", "grandfather", "father in law": not the applicant's parent
_NOT_A_PARENT = {"step", "grand", "law", "foster", "uncle", "aunt", "brother", "sister"}


def sponsor_kind(value: str) -> str:
    """The portal's free-text Sponsor value as one of FATHER, MOTHER, PARENTS, SELF or
    OTHER ("" when it is blank).

      PARENTS  "Both Parents", "Parents", "Father & Mother", "Father and Mother"
      SELF     "Self", "Self funded", "Self-funded", "Own", "Student", "Applicant"
    """
    words = re.findall(r"[a-z]+", norm(value))
    if not words:
        return ""
    if _NOT_A_PARENT & set(words) or any(w.startswith(("grand", "step")) for w in words):
        return "OTHER"
    father, mother = "father" in words, "mother" in words
    if (father and mother) or any(w in ("parent", "parents") for w in words):
        return "PARENTS"
    if father:
        return "FATHER"
    if mother:
        return "MOTHER"
    if _SELF_WORDS & set(words):
        return "SELF"
    return "OTHER"


def edit_distance(a: str, b: str) -> int:
    """Levenshtein distance: the fewest single-character insertions, deletions and
    substitutions that turn `a` into `b`."""
    a, b = a or "", b or ""
    if len(a) < len(b):
        a, b = b, a
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]


# --- verdicts ------------------------------------------------------------------------
def row_verdict(findings: Iterable[Tuple[str, str]]) -> str:
    """One document row's verdict from its findings: FAIL > ERROR > FLAG > PASS. NOTE and
    PASS findings both leave the row at PASS."""
    seen = {v for v, _ in findings}
    for v in (FAIL, ERROR, FLAG):
        if v in seen:
            return v
    return PASS


def has_error(findings: Iterable[Tuple[str, str]]) -> bool:
    """Whether any finding is ERROR — the row must be checked again, even when its
    verdict shows FAIL (a FAIL finding outranks ERROR for display only)."""
    return any(v == ERROR for v, _ in findings)


_ERROR_FINDING = re.compile(r"(?:^|\|\s)ERROR:")


def row_has_error(row: Dict[str, Any]) -> bool:
    """Whether a stored document row holds an ERROR finding: its `has_error` flag, an
    ERROR verdict, or an "ERROR:" finding in its detail (rows saved before the flag)."""
    return bool(row.get("has_error") or row.get("verdict") == ERROR
                or _ERROR_FINDING.search(row.get("detail") or ""))


def student_verdict(rows: Iterable[Dict[str, Any]]) -> str:
    """The student's verdict from their document rows: any MISSING -> INCOMPLETE; else any
    FAIL -> FAIL; else any ERROR -> ERROR; else any FLAG -> REVIEW; else PASS."""
    seen = {r.get("verdict") for r in rows}
    if MISSING in seen:
        return INCOMPLETE
    if FAIL in seen:
        return FAIL
    if ERROR in seen:
        return ERROR
    if FLAG in seen:
        return REVIEW
    return PASS
