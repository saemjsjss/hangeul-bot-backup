"""Cross-document names, their confirmation, the dot rule and the affidavit (RUNNER_SPEC 5.9.1 and 7;
owner decisions A1 and A2 of 27 Oct 2026).

A port of the lab's names_affidavit.py: the label vocabulary, the per-document extractors, the MRZ name
line, the affidavit parser and the canonical parent name are kept; every comparison that decides whether
two written names are the same goes through strict.py (letter for letter, every dot counts). Fuzzy
distances are used only to FIND and DESCRIBE a variant in an OCR window, never to call two names equal.

A1: a difference is FAIL only when it is CONFIRMED (two independent reads agree on it: two pages of the
document, a machine read, or the zoomed targeted re-read) and no read of that document shows the
reference, and no affidavit covers it; any other difference is FLAG "check by eye, page N"; never PASS.
A2: dots are judged by strict.dot_rule() against the passport's PRINTED name (or, for a parent, the birth
certificate).

Changes from the lab file (RUNNER_SPEC 5.9.1): imports from botimport (worktree bot code, stub config);
Read gains unit / written / page; NameFinding replaces the lab's Finding; the affidavit keeps spellings as
written; the loaders, the deponent, the honorific policy switch and every loose matcher are gone. One
extraction change: OCR prints the dot after an honorific or an initial as ',' ':' ';' or '_' ("MD:SAMPLE",
"A_ SAMPLE"); such a mark glued to MD/MST/an initial is read as the dot it stands for (A2 needs the dots).
No personal data: every word here is a label, a template word or a made-up placeholder.
"""
from __future__ import annotations

import collections
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence, Tuple

from . import botimport, strict
from .model import (FAIL, FLAG, NOTE, PASS, RANK, AffidavitInfo, BirthFacts, DocUnit, Finding, Identity,
                    MachineRead, PageRead)

REREAD_MAX = 24            # targeted re-reads per run, shared by every caller of the confirmer
REREAD_MIN_CONF = 0.80
REREAD_CAN_FAIL = True     # A1: a confirmed difference is FAIL (no audit gate)
SOURCE = "runner:names"
PEOPLE = ("student", "father", "mother")
_NEUTRAL = ("parent", "taxpayer", "proprietor", "employee", "holder")   # vocabulary persons (critique 4)

_BOT = None


def _bot():
    """The bot's code (worktree, stub config): the one botimport.load() of this process."""
    global _BOT
    if _BOT is None:
        _BOT = botimport._loaded if botimport._loaded is not None else botimport.load(botimport.DEFAULT_CACHE_ROOT)
    return _BOT


def edit_distance(a: str, b: str) -> int:
    return _bot().C.edit_distance(a, b)


# ============================================================================ normalising
_TOK = re.compile(r"\S+")


def alpha(s: str) -> str:
    """The ASCII letters of s (the lab's letters())."""
    return re.sub(r"[^A-Za-z]", "", s or "")


def nt(s: str) -> str:
    """One name part as the window holds it: its letters, upper case (dots and marks dropped)."""
    return alpha(s).upper()


def name_parts(s: str) -> Tuple[str, ...]:
    return tuple(p for p in (nt(x) for x in re.split(r"[^A-Za-z]+", s or "")) if p)


TITLES = frozenset(strict.TITLES)
_HON_SHORT = frozenset({"MD", "MST", "SK", "MOS"})


def is_honorific(p: str) -> bool:
    """MD, MST and the bot's honorific list (common.HONORIFICS / HONORIFIC_RE). Used only to DESCRIBE a
    difference or to see that an honorific printed before a name belongs to it; never to drop one (A1)."""
    up = nt(p)
    if not up:
        return False
    C = _bot().C
    low = up.lower()
    return up in _HON_SHORT or low in C.HONORIFICS or bool(C.HONORIFIC_RE.match(low))


# A dot OCR printed as another mark, glued to an honorific or an initial: "MD:SAMPLE", "A_ SAMPLE", "MD, SAMPLE".
_MARK_IN = re.compile(r"(?<![A-Za-z])([A-Za-z]{1,3})[,:;_]+(?=[A-Za-z])")
_MARK_END = re.compile(r"^([A-Za-z]{1,3})[,:;_]+$")


def _abbrev(s: str) -> bool:
    return len(s) == 1 or is_honorific(s)


def _dot_marks(tk: List[str]) -> List[str]:
    out = []
    for i, t in enumerate(tk):
        t2 = _MARK_IN.sub(lambda m: m.group(1) + "." if _abbrev(m.group(1)) else m.group(0), t)
        m = _MARK_END.match(t2)
        if m and _abbrev(m.group(1)) and i + 1 < len(tk) and name_like(tk[i + 1]) and not _is_stop(tk[i + 1]):
            t2 = m.group(1) + "."
        out.append(t2)
    return out


def _tokens(text: str) -> List[str]:
    return _dot_marks(_TOK.findall(text or ""))


# ============================================================================ labels (fuzzy on LABELS only)
def _near(tok: str, target: str, maxd: int) -> bool:
    w = alpha(tok).lower()
    if not w or abs(len(w) - len(target)) > maxd:
        return False
    return w == target or edit_distance(w, target) <= maxd


def is_father(tok: str) -> bool:
    w = alpha(tok).lower()
    if w.endswith("s") and len(w) >= 7:
        w = w[:-1]
    return len(w) >= 5 and w[0] in "fp" and _near(w, "father", 2)


def is_mother(tok: str) -> bool:
    w = alpha(tok).lower()
    if w.endswith("s") and len(w) >= 7:
        w = w[:-1]
    return len(w) >= 5 and w[0] in "mn" and _near(w, "mother", 2)


def is_name_word(tok: str, strict_label: bool = False) -> bool:
    w = alpha(tok).lower()
    if w in ("name", "names", "nam", "nama", "namc", "narne", "nanie", "nane", "naine", "namne", "nome"):
        return True
    return (not strict_label) and 3 <= len(w) <= 5 and w[0] in "nk" and _near(w, "name", 1)


def is_husband(tok: str) -> bool:
    return _near(tok, "husband", 2) or _near(tok, "spouse", 1) or alpha(tok).lower() in ("wife", "husbands", "spouses")


# Words that end a name read after its label (lower case, letters only).
STOP = {
    "name", "names", "father", "fathers", "mother", "mothers", "husband", "husbands", "spouse", "spouses",
    "wife", "date", "birth", "dob", "sex", "gender", "address", "present", "permanent", "nationality",
    "nid", "id", "no", "number", "place", "relation", "relationship", "religion", "occupation",
    "profession", "village", "vill", "post", "office", "district", "dist", "upazila", "upazilla", "thana",
    "police", "station", "roll", "reg", "registration", "issue", "issued", "blood", "group", "signature",
    "son", "daughter", "of", "ward", "union", "mobile", "phone", "school", "college", "institute",
    "institution", "board", "exam", "examination", "year", "session", "passport", "type", "code", "country",
    "personal", "surname", "given", "holder", "marital", "status", "age", "serial", "sl", "gpa", "result",
    "centre", "center", "and", "is", "was", "in", "at", "on", "for", "the", "to", "from", "by", "with",
    "dated", "pin", "house", "road", "holding", "citizen", "sector", "block", "picture", "photo",
    "bangladeshi", "bangladesh", "self", "herself", "himself", "applicant", "has", "have", "having",
    "bearing", "who", "whose", "emergency", "contact", "telephone", "tel", "permanently", "resident",
    "maintaining", "maintains", "maintained", "account", "a", "an", "her", "his", "s", "o", "d", "w", "c",
    "previous", "tin", "taxpayer", "registered", "current", "customer", "branch", "bank", "ltd",
    "limited", "plc", "proprietor", "owner", "business", "nature", "trade", "license", "licence",
    "brother", "sister", "grandfather", "grandmother", "paternal", "maternal", "alive", "dead", "deceased",
    "legal", "guardian", "guardians", "guardlan",
    "sd", "illegible", "illeglble", "seal", "official", "translation", "translated", "true", "copy",
    "certify", "certificate", "that", "this", "secondary", "higher", "duly", "passed", "obtained",
    "student", "candidate", "science", "commerce", "humanities", "arts", "regular", "irregular",
    "information", "existing", "different", "document", "documents", "my", "i", "am", "are", "same", "person",
    "namely", "refer", "known", "also", "alias", "or", "nominee", "officer", "manager", "signatory",
    "authorized", "authorised", "purpose", "study", "studies", "abroad", "education",
    "pourashava", "municipality", "city", "corporation", "word", "words", "national",
    "card", "smart", "verification", "verify", "rule", "registrar", "chairman", "member", "secretary",
    # layout words whole-page OCR runs on after a name (corpus: they ended up as "extra name words")
    "photograph", "photographs", "notarized", "notarised", "notary", "attested", "comment", "comments",
    "married", "unmarried", "death", "cardholder",
}
# Review 2: printed words that FOLLOW a name in a sentence, a transcript or a table row ("<A> <B> successfully
# completed", "<A> <B> Programme ...", "<A> <B> Farmer Father", "<A> <B> Freedom Fighter"). After at least one
# name word they end the value, so a value that runs on into them is not "the name plus a word" (a false FAIL once
# the re-read confirms the print). They are not STOP words: a value may START with one (a rank or a profession
# written before a name: "Advocate <A> <B>"), and the corpus showed real values that do; nor are they dropped from
# the vocabulary or the affidavit groups.
AFTER_NAME = frozenset({
    "successfully", "successful", "completed", "complete", "completing", "appeared", "appear", "appearing",
    "participated", "secured", "securing", "achieved", "awarded", "conferred", "graduated", "studied", "studying",
    "enrolled", "admitted", "attended", "held", "holds", "being", "been", "will", "shall", "under", "during",
    "after", "before", "within", "upon", "into", "as", "which", "whom", "employed", "working", "works", "worked",
    "serving", "served", "joined", "since", "residing", "resides", "living", "belongs", "belonging", "according",
    "requirement", "requirements", "all", "following", "persons", "members", "family", "born", "bears",
    "program", "programme", "programs", "programmes", "department", "dept", "cgpa", "course", "courses",
    "semester", "batch", "minor", "degree", "faculty", "subject", "subjects", "discipline", "duration",
    "level", "class", "division", "grade", "grades", "passing", "bachelor", "bachelors",
    "diploma", "honours", "honors", "hons", "university", "academic", "credit", "credits", "total", "marks",
    "position", "merit", "transcript", "enrolment", "enrollment", "admission", "convocation", "results",
    "farmer", "housewife", "homemaker", "household", "unemployed", "employee", "employment", "service",
    "serviceholder", "servant", "teacher", "doctor", "engineer", "businessman", "labour", "labor", "labourer",
    "laborer", "worker", "driver", "retired", "retd", "pensioner", "freedom", "fighter", "govt", "government",
    "private", "job", "jobholder", "expatriate", "agriculture", "agriculturist", "shopkeeper", "trader",
    "merchant", "contractor", "lawyer", "advocate", "banker", "nurse", "tailor", "carpenter", "fisherman", "nil",
    "none",
})
# Occupations and descriptors a family table prints between a person's name and the relation column
# ("<NAME> Farmer Father"): skipped when the row is read back from the relation word.
OCCUPATIONS = frozenset({
    "farmer", "housewife", "homemaker", "household", "unemployed", "employed", "employee", "service",
    "serviceholder", "servant", "teacher", "doctor", "engineer", "business", "businessman", "labour", "labor",
    "labourer", "laborer", "worker", "driver", "retired", "retd", "pensioner", "freedom", "fighter", "govt",
    "government", "private", "job", "jobholder", "expatriate", "agriculture", "agriculturist", "shopkeeper",
    "trader", "merchant", "contractor", "lawyer", "advocate", "banker", "nurse", "tailor", "carpenter",
    "fisherman", "student", "nil", "none", "alive", "dead", "deceased", "late"})
STOP_PREFIX = ("father", "mother", "nation", "addres", "relat", "permanen", "certif", "registr", "signat")


def _is_stop(tok: str) -> bool:
    w = alpha(tok).lower()
    if not w:
        return True
    if re.fullmatch(r"[A-Z]\.", tok):            # an initial with its dot, not the article 'a'
        return False
    if w in STOP or w.startswith(STOP_PREFIX):
        return True
    if len(w) >= 9 and (_near(w, "relationship", 4) or "relation" in w):
        return True
    return is_father(tok) or is_mother(tok) or is_husband(tok) or _near(w, "nationality", 2) \
        or _near(w, "address", 1) or _near(w, "signature", 2) or _near(w, "relationship", 2)


_CLOSE = re.compile(r"[,;:|()\[\]{}\"“”]")


def name_like(tok: str) -> bool:
    """A token that could be (part of) a name as OCR printed it: letters, at most a trailing dot,
    an apostrophe or a hyphen between letters; no digits, no symbols inside."""
    core = tok.strip(".,;:'\"“”()[]{}`~-_")
    if not core or re.search(r"\d", core):
        return False
    return bool(re.fullmatch(r"[A-Za-z]+(?:[.'\-][A-Za-z]+)*\.?", core))


def skip_intro(tk: Sequence[str], i: int, skip_name: bool = True) -> int:
    """Index of the first token of the value: "Name", "of", "the", separators skipped (at most 3)."""
    j, skipped = i, 0
    while j < len(tk) and skipped < 3:
        w = alpha(tk[j]).lower()
        if (not w) or (skip_name and is_name_word(tk[j])) or w in ("of", "the", "s", "is", "was", "mr", "mrs"):
            j += 1
            skipped += 1
            continue
        if skip_name and skipped < 2 and ((len(w) == 4 and w[0] == "n" and _near(w, "name", 2))
                                          or w in ("namna", "nomie", "namne", "nanie", "narne", "naine")) \
                and j + 1 < len(tk) and name_like(tk[j + 1]):
            j += 1
            skipped += 1
            continue
        break
    return j


def _case_style(tok: str) -> str:
    """"U" ALL CAPITALS, "L" all small, "T" Title, "M" mixed, "" fewer than two letters."""
    s = alpha(tok)
    if len(s) < 2:
        return ""
    if s.isupper():
        return "U"
    if s.islower():
        return "L"
    return "T" if (s[0].isupper() and s[1:].islower()) else "M"


def is_label_token(tok: str) -> bool:
    """"Program:", "CGPA:", "Department:": a word glued to a colon is the next field's label, never a name word
    ("MD:" before a name is a dot, see _dot_marks; an initial or honorific with a colon is no label)."""
    w = alpha(tok)
    return tok.rstrip().endswith(":") and len(w) >= 2 and not _abbrev(w)


def case_breaks(value: Sequence[str], tok: str) -> bool:
    """True when `tok` cannot continue the name `value` because of how it is printed (review 2): a value in ALL
    CAPITALS then a word in Title case or small letters ("<A> <B> successfully", "<A> <B> Farmer", a Title-case
    field word after a card's capitalised name), or a Title-case value then a word in small letters. Honorifics
    and initials carry no style; mixed-case OCR noise never breaks."""
    st = _case_style(tok)
    if st not in ("T", "L") or _abbrev(alpha(tok)):
        return False
    seen = [_case_style(x) for x in value if len(alpha(x)) >= 3 and not is_honorific(x)]
    if not seen:
        return False
    if all(s == "U" for s in seen):
        return True
    return st == "L" and all(s in ("U", "T") for s in seen)


def ends_name(value: Sequence[str], tok: str) -> bool:
    """Review 2: `tok` cannot continue the name `value` (at least one word read): a word that follows names
    (AFTER_NAME), the next field's label ("Program:") or a word printed in another case (case_breaks)."""
    return bool(value) and (alpha(tok).lower() in AFTER_NAME or is_label_token(tok) or case_breaks(value, tok))


def read_value(tk: Sequence[str], i: int, maxn: int = 7, skip_name: bool = True) -> Tuple[List[str], int]:
    """The name written from token i on: optional "Name"/"of"/separators skipped, then name-like
    tokens until a stop word, a number, a symbol, the next field's label ("Program:"), a word that follows
    names or is printed in another case than the name (ends_name), or a closing punctuation mark (kept)."""
    n = len(tk)
    j = skip_intro(tk, i, skip_name)
    out: List[str] = []
    while j < n and len(out) < maxn:
        t = tk[j]
        if not name_like(t) or _is_stop(t) or is_label_token(t) or ends_name(out, t):
            break
        out.append(t)
        j += 1
        if _CLOSE.search(t[-1:]) or t.endswith(":"):
            break
    return out, j


_HARD_EXACT = {"brn", "nid", "dob", "sex", "gender", "date", "birth", "place", "religion", "occupation",
               "profession", "blood", "mobile", "phone", "telephone", "tel", "vill", "village", "post", "district",
               "dist", "upazila", "upazilla", "thana", "ward", "roll", "reg", "session", "gpa", "passport",
               "issue", "spouse", "husband", "wife", "relation", "type", "institution", "centre", "center",
               "nationality", "address", "permanent", "present", "signature", "emergency", "contact", "id",
               "bangladeshi", "relationship", "registration", "examination", "board", "school", "college"}
_HARD_FUZZY = (("nationality", 2), ("permanent", 2), ("signature", 2), ("relationship", 2),
               ("registration", 2), ("address", 1), ("bangladeshi", 2), ("emergency", 2), ("institution", 2))


def hard_stop(tok: str) -> bool:
    w = alpha(tok).lower()
    if not w:
        return False
    if w in _HARD_EXACT:
        return True
    if len(w) >= 9 and (_near(w, "relationship", 4) or "relation" in w):
        return True
    return len(w) >= 6 and any(_near(w, t, d) for t, d in _HARD_FUZZY)


def _window(tk: Sequence[str], i: int, size: int = 12) -> Tuple[Tuple[str, ...], Tuple[str, ...]]:
    """(normalised parts, the same tokens as printed) of the name-like tokens of the next `size` tokens:
    where a strict match is looked for. It ends at the next person's label or another field's label."""
    win, raw = [], []
    i = skip_intro(tk, i)
    for t in tk[i:i + size]:
        if is_father(t) or is_mother(t) or hard_stop(t):
            break
        p = nt(t)
        if p and name_like(t):
            win.append(p)
            raw.append(t)
    return tuple(win), tuple(raw)


def window(tk: Sequence[str], i: int, size: int = 12) -> Tuple[str, ...]:
    return _window(tk, i, size)[0]


# ============================================================================ reads
@dataclass
class Read:
    person: str               # student | father | mother | parent | holder | taxpayer | proprietor | employee
    parts: Tuple[str, ...]    # normalised name parts as extracted (may be empty)
    doc: str                  # document key (passport, birth_cert, ...)
    src: str                  # which label / zone on the document
    page: int = 0             # extractors: page within the record; collect_reads: 0-based RUN page
    win: Tuple[str, ...] = ()       # the strict-match window after the label (normalised parts)
    raw: str = ""
    unit: str = ""                  # uid of the document unit
    written: str = ""               # the value as OCR printed it, dots kept, tokens joined by one space
    win_raw: Tuple[str, ...] = ()   # the window's tokens as printed (dots kept)


def _mk(person: str, vals: Sequence[str], doc: str, src: str, page: int, tk: Sequence[str], j: int,
        size: int = 12, win: Optional[Tuple[Tuple[str, ...], Tuple[str, ...]]] = None) -> Read:
    w, wr = _window(tk, j, size) if win is None else win
    written = " ".join(vals)
    return Read(person, tuple(nt(v) for v in vals if nt(v)), doc, src, page, w, written, written=written,
                win_raw=wr)


def _label_reads(text: str, doc: str, page: int, person: str, test, src: str, maxn: int = 7,
                 need_name_word: bool = False, after_skip: int = 0, skip: Iterable[int] = ()) -> List[Read]:
    """`skip`: token indices that are no label here (a family table's relation cell, read back by the table row)."""
    tk = _tokens(text)
    skip = set(skip)
    out = []
    for i, t in enumerate(tk):
        if i in skip or not test(t):
            continue
        j = i + 1 + after_skip
        if need_name_word:
            near_name = (j < len(tk) and is_name_word(tk[j])) or (i > 0 and is_name_word(tk[i - 1]))
            if not near_name:
                continue
        vals, _ = read_value(tk, j, maxn)
        out.append(_mk(person, vals, doc, src, page, tk, j))
    return out


_PLAIN_SKIP_PREV = ("spouse", "spouses", "husband", "husbands", "wife", "wifes", "given", "sur", "business",
                    "organization", "organisation", "bank", "branch", "account", "institution", "school",
                    "college", "centre", "center", "document", "documents", "proprietor", "proprietors",
                    "owner", "owners", "applicant", "contact", "nominee", "company", "firm", "board",
                    "exam", "examination", "registrar", "s")
_PLAIN_SKIP_WHOSE = ("institution", "school", "college", "centre", "center", "business", "proprietor", "the",
                     "board", "organization", "bank", "deceased")


_DEATH_DOCS = frozenset({"father_death_cert", "mother_death_cert", "death_cert"})


def _plain_name_reads(text: str, doc: str, page: int, person: str, src: str = "Name") -> List[Read]:
    """'Name: X' where the word before 'Name' is not another person's label."""
    tk = _tokens(text)
    out = []
    for i, t in enumerate(tk):
        if not is_name_word(t, strict_label=True):
            continue
        prev = alpha(tk[i - 1]).lower() if i else ""
        prev2 = alpha(tk[i - 2]).lower() if i > 1 else ""
        if (is_father(tk[i - 1]) if i else False) or (is_mother(tk[i - 1]) if i else False) or \
                prev in _PLAIN_SKIP_PREV or prev2 in ("father", "mother", "spouse"):
            continue
        nxt = alpha(tk[i + 1]).lower() if i + 1 < len(tk) else ""
        if nxt == "of":                              # "Name of Father", "Name of Student"
            whose = alpha(tk[i + 2]).lower() if i + 2 < len(tk) else ""
            j = i + 3
            if whose == "the" and i + 3 < len(tk):   # "Name of the Deceased"
                whose = alpha(tk[i + 3]).lower()
                j = i + 4
            if doc in _DEATH_DOCS and whose.startswith("decease"):
                if j < len(tk) and alpha(tk[j]).lower() == "person":
                    j += 1                           # "Name of the Deceased Person: X"
            elif is_father(whose) or is_mother(whose) or whose in _PLAIN_SKIP_WHOSE or whose == "the":
                continue
            elif j == i + 4:                         # "Name of the <something>": not a person's own name
                continue
        else:
            j = i + 1
        vals, _ = read_value(tk, j)
        out.append(_mk(person, vals, doc, src, page, tk, j))
    return out


def _labelled_of(text: str, doc: str, page: int, person: str, whose_test, src: str) -> List[Read]:
    """'Name of Father X' / 'Name of Mother X'."""
    tk = _tokens(text)
    out = []
    for i in range(len(tk) - 2):
        if is_name_word(tk[i], strict_label=True) and alpha(tk[i + 1]).lower() == "of" and whose_test(tk[i + 2]):
            vals, _ = read_value(tk, i + 3)
            out.append(_mk(person, vals, doc, src, page, tk, i + 3))
    return out


def _parent_reads(text: str, doc: str, page: int, father_as="father", mother_as="mother",
                  skip: Iterable[int] = ()) -> List[Read]:
    out = []
    if father_as:
        out += _label_reads(text, doc, page, father_as, is_father, "Father label", skip=skip)
        out += _labelled_of(text, doc, page, father_as, is_father, "Name of Father")
    if mother_as:
        out += _label_reads(text, doc, page, mother_as, is_mother, "Mother label", skip=skip)
        out += _labelled_of(text, doc, page, mother_as, is_mother, "Name of Mother")
    return out


# ---------------------------------------------------------------------------- MRZ name line
_MRZ_JUNK = re.compile(r"[^A-Z0-9<]")


def _l1_candidates(text: str, state: str = "BGD") -> List[str]:
    t = (text or "").upper().replace("«", "<<").replace("‹", "<")
    flat = _MRZ_JUNK.sub("", " ".join(t.split()))     # OCR splits the line where it likes
    rx = re.compile(r"P[<KC(]?" + state)
    cands = []
    for m in rx.finditer(flat):
        cands.append(flat[m.start():m.start() + 44])
    for line in t.splitlines():
        s = _MRZ_JUNK.sub("", line)
        for m in rx.finditer(s):
            cands.append(s[m.start():m.start() + 44])
    return list(dict.fromkeys(cands))


_FILLER_ONLY = re.compile(r"^[KCSLEX]+$")


def _mrz_name_field(fld: str) -> Tuple[List[str], List[str], bool]:
    """(surname parts, given parts, cut) from the MRZ name field (line 1 after 'P<BGD'): the name ends at
    the first run of 3 fillers; trailing parts made only of letters OCR reads for '<' are dropped; 'cut'
    says whether a filler run ended it (else it may be truncated)."""
    fld = fld.translate(str.maketrans("80152", "BOISZ"))
    fld = re.sub(r"[^A-Z<]", "", fld)
    m = re.search(r"<<<", fld)
    cut = bool(m)
    if m:
        fld = fld[:m.start()]
    sur, _, given = fld.partition("<<")
    s_parts = [p for p in sur.split("<") if p]
    g_parts = [p for p in given.split("<") if p]
    while g_parts and _FILLER_ONLY.match(g_parts[-1]):
        g_parts.pop()
        cut = True
    return s_parts, g_parts, cut


def passport_mrz_name(text: str, printed: Iterable[str] = ()) -> Optional[dict]:
    """The MRZ line-1 name: {"parts", "surname", "given", "ok", "truncated", "merged"} or None. Fillers
    read as letters are split off using the names printed on the same page (`printed`). `ok` =
    parse_mrz_line1's structural check. The name has no check digit: a well-formed line can still hold a
    misread letter."""
    OV = _bot().OV
    best = None
    for state in ("BGD", "[A-Z]{3}"):
        for c in _l1_candidates(text, state):
            one = OV.parse_mrz_line1(c)
            if not one:
                continue
            if best is None or (one["line1_ok"] and not best["line1_ok"]):
                best = one
        if best is not None and best["line1_ok"]:
            break
    if not best:
        return None
    s_parts, g_parts, cut = _mrz_name_field(best["line1"][5:])
    known = {p for p in (nt(x) for x in printed) if len(p) >= 2}
    parts: List[str] = []
    merged = False
    for p in s_parts + g_parts:
        sp = OV._split_merged(p, known) if known and p not in known and len(p) > 6 else None
        if sp and sp != [p]:
            merged = True
            parts.extend(sp)
        else:
            parts.append(p)
    return {"parts": tuple(parts), "ok": bool(best["line1_ok"]), "truncated": not cut, "merged": merged,
            "surname": " ".join(s_parts), "given": " ".join(g_parts), "line1": best["line1"]}


_TD1_NAME = re.compile(r"([A-Z]{2,}(?:<[A-Z]+)*<<[A-Z]+(?:<[A-Z]+)*)<{2,}")


def nid_mrz_names(text: str) -> List[Tuple[str, ...]]:
    """Smart NID back: TD1 line 3 'SURNAME<<GIVEN<NAMES<<<'."""
    t = (text or "").upper()
    out = []
    flat = re.sub(r"\s+", "", t)
    for m in _TD1_NAME.finditer(flat):
        s = m.group(1)
        pre = flat[max(0, m.start() - 2):m.start()]
        if s.startswith("BGD") or pre.endswith(("P<", "I<")):
            continue
        sur, given = s.split("<<", 1)
        parts = tuple(p for p in (sur.split("<") + given.split("<")) if p)
        if 1 <= len(parts) <= 6:
            out.append(parts)
    return list(dict.fromkeys(out))


# ============================================================================ per-document extractors
def _pages(rec: dict) -> List[str]:
    return list(rec.get("pages") or []) or [rec.get("text") or ""]


def extract_passport(rec: dict) -> Tuple[List[Read], Optional[dict]]:
    """The passport's name reads (data page Given Name(s) + Surname, the personal-data page, the parents'
    fields, the emergency contact) and the MRZ line-1 name (with "page": the page within the record)."""
    reads: List[Read] = []
    mrz = None
    for pi, text in enumerate(_pages(rec)):
        tk = _tokens(text)
        for i in range(len(tk) - 1):
            if _near(tk[i], "emergency", 2) and _near(tk[i + 1], "contact", 2):
                if i >= 2 and _near(tk[i - 2], "data", 1):            # "PERSONAL DATA AND EMERGENCY CONTACT"
                    vals, _ = read_value(tk, i + 2)
                    reads.append(_mk("student", vals, "passport", "personal page name", pi, tk, i + 2, 8))
                else:                                                  # "Emergency Contact: Name: X Relationship"
                    vals, j = read_value(tk, i + 2)
                    rel = " ".join(tk[j:j + 4]).lower()
                    who = "father" if "father" in rel else "mother" if "mother" in rel else ""
                    if who and vals:
                        reads.append(_mk(who, vals, "passport", "emergency contact", pi, tk, i + 2, 8))
        sur, giv, si, gi = None, None, -1, -1
        for i, t in enumerate(tk):
            if sur is None and _near(t, "surname", 2):
                sur, _ = read_value(tk, i + 1, maxn=3)
                si = i + 1
            if giv is None and _near(t, "given", 1) and i + 1 < len(tk) and is_name_word(tk[i + 1]):
                giv, _ = read_value(tk, i + 2, maxn=5)
                gi = i + 2
        if giv and sur:
            vals = list(giv) + list(sur)
            parts = tuple(nt(v) for v in vals if nt(v))
            reads.append(Read("student", parts, "passport", "VIZ given+surname", pi, parts, " ".join(vals),
                              written=" ".join(vals), win_raw=tuple(v for v in vals if nt(v))))
        reads += _parent_reads(text, "passport", pi)
        m = passport_mrz_name(text, printed=[w for r in reads for w in r.parts])
        if m and (mrz is None or (m["ok"] and not mrz["ok"])):
            m["page"] = pi
            mrz = m
    return reads, mrz


def extract_birth_cert(rec: dict) -> List[Read]:
    reads = []
    for pi, text in enumerate(_pages(rec)):
        reads += _plain_name_reads(text, "birth_cert", pi, "student")
        reads += _parent_reads(text, "birth_cert", pi)
    return reads


_MACHINE_ORDER = frozenset({"NID MRZ"})      # code lines printed surname first, without dots
_NID_HOLDER = {"father_nid": "father", "mother_nid": "mother", "student_nid": "student",
               "father_death_cert": "father", "mother_death_cert": "mother",
               "nid_unassigned": "parent", "death_cert": "parent"}


def extract_nid(rec: dict, doc: str) -> List[Read]:
    holder = _NID_HOLDER[doc]
    reads = []
    for pi, text in enumerate(_pages(rec)):
        reads += _plain_name_reads(text, doc, pi, holder)
        for parts in nid_mrz_names(text):
            reads.append(Read(holder, parts, doc, "NID MRZ", pi, parts, " ".join(parts), written=" ".join(parts),
                              win_raw=parts))
        if holder == "student":
            reads += _parent_reads(text, doc, pi)
        elif holder == "mother":            # old cards: 'Husband' in place of 'Father' for a married woman
            reads += _label_reads(text, doc, pi, "father", is_husband, "Husband/Spouse label")
        elif holder == "father":
            reads += _label_reads(text, doc, pi, "mother", lambda t: _near(t, "spouse", 1) or
                                  alpha(t).lower() == "wife", "Spouse/Wife label")
        else:                               # a parent whose role is not known yet: the spouse is a parent too
            reads += _label_reads(text, doc, pi, "parent", is_husband, "Husband/Spouse label")
    return reads


_REL_STUDENT = {"self", "herself", "himself", "applicant", "own"}


_ROW_NUMBER = re.compile(r"^[(\[]?\d{1,2}[.)\]:]?$")


def extract_family_cert(rec: dict) -> List[Read]:
    reads = []
    for pi, text in enumerate(_pages(rec)):
        tk = _tokens(text)
        for i in range(len(tk) - 1):
            if alpha(tk[i]).lower() in ("certify", "certified") and alpha(tk[i + 1]).lower() == "that":
                vals, _ = read_value(tk, i + 2)
                if vals:
                    reads.append(_mk("student", vals, "family_cert", "certify that", pi, tk, i + 2, 8))
        rows: List[Read] = []
        row_cells: List[int] = []
        for j, t in enumerate(tk):                    # table rows: NAME [occupation / numbers / dates] RELATION
            w = alpha(t).lower()
            who = ("student" if w in _REL_STUDENT else "father" if w == "father" and not
                   (j + 1 < len(tk) and is_name_word(tk[j + 1])) else "mother" if w == "mother" and not
                   (j + 1 < len(tk) and is_name_word(tk[j + 1])) else "")
            if not who:
                continue
            k = j - 1
            hops = 0
            while k >= 0 and hops < 5 and (not name_like(tk[k]) or alpha(tk[k]).lower() in OCCUPATIONS):
                k -= 1
                hops += 1
            vals: List[str] = []
            while k >= 0 and len(vals) < 6 and name_like(tk[k]) and not _is_stop(tk[k]):
                vals.insert(0, tk[k])
                k -= 1
            last = max((q for q, v in enumerate(vals) if alpha(v).lower() in AFTER_NAME), default=-1)
            vals = vals[last + 1:]                    # words that follow names stand before this row's name
            cut = next((q for q in range(1, len(vals)) if ends_name(vals[:q], vals[q])), len(vals))
            vals = vals[:cut]                         # "<NAME> Xxxxx Father": a Title-case cell after a capitalised name
            if vals:
                ps = tuple(nt(v) for v in vals if nt(v))
                rows.append(_mk(who, vals, "family_cert", "table row", pi, tk, 0,
                                win=(ps, tuple(v for v in vals if nt(v)))))
                if j + 1 < len(tk) and _ROW_NUMBER.match(tk[j + 1]):
                    row_cells.append(j)               # "... Father 2 <next row>": a relation cell, not a label
        reads += _parent_reads(text, "family_cert", pi, skip=row_cells)
        reads += rows
    return reads


def extract_academic(rec: dict) -> List[Read]:
    reads = []
    for pi, text in enumerate(_pages(rec)):
        tk = _tokens(text)
        for i in range(len(tk) - 1):                  # "certify that S son/daughter of F and M"
            if not (_near(tk[i], "certify", 2) and _near(tk[i + 1], "that", 1)):
                continue
            s_vals, j = read_value(tk, i + 2)
            if s_vals:
                reads.append(_mk("student", s_vals, "academic", "certificate sentence", pi, tk, i + 2, 8))
            k = j
            while k < min(len(tk), j + 4) and alpha(tk[k]).lower() not in ("of",):
                k += 1
            if k < len(tk) and alpha(tk[k]).lower() == "of" and any(
                    _near(x, "son", 0) or _near(x, "daughter", 2) for x in tk[j:k]):
                f_vals, jf = read_value(tk, k + 1, skip_name=False)
                if f_vals:
                    reads.append(_mk("father", f_vals, "academic", "certificate sentence", pi, tk, k + 1, 8))
                if jf < len(tk) and alpha(tk[jf]).lower() in ("and", "&"):
                    m_vals, _ = read_value(tk, jf + 1, skip_name=False)
                    if m_vals:
                        reads.append(_mk("mother", m_vals, "academic", "certificate sentence", pi, tk, jf + 1, 8))
        for i in range(len(tk) - 2):                  # transcript labels
            if is_name_word(tk[i]) and alpha(tk[i + 1]).lower() == "of" and _near(tk[i + 2], "student", 2):
                vals, _ = read_value(tk, i + 3)
                reads.append(_mk("student", vals, "academic", "Name of Student", pi, tk, i + 3, 8))
        reads += _parent_reads(text, "academic", pi)
    return reads


def _certify_that(tk: List[str], doc: str, pi: int, person: str, src: str, until=None) -> List[Read]:
    out = []
    for i in range(len(tk) - 1):
        if alpha(tk[i]).lower() in ("certify", "certified") and alpha(tk[i + 1]).lower() == "that":
            j = i + 2
            while j < len(tk) and alpha(tk[j]).lower() in ("mr", "mrs", "ms", "miss", ""):
                j += 1
            vals, k = read_value(tk, j, skip_name=False)
            nxt = " ".join(tk[k:k + 4]).lower()
            if until and not re.search(until, nxt):
                continue
            out.append(_mk(person, vals, doc, src, pi, tk, j, 8))
    return out


def extract_financial(rec: dict, doc: str) -> List[Read]:
    """TIN (taxpayer), trade licence (proprietor), employment certificate (employee). Used as vocabulary
    only (critique 4): their person checks are bank.py's and financial.py's."""
    reads = []
    for pi, text in enumerate(_pages(rec)):
        tk = _tokens(text)
        low = text.lower()
        if "taxpayer" in low:
            reads += _certify_that(tk, doc, pi, "taxpayer", "certify that .. is a registered taxpayer",
                                   until=r"\bis\b|registered|taxpayer")
            for i in range(len(tk) - 1):           # "1) Name X 2) Father's Name"
                if re.fullmatch(r"1\)?", tk[i]) and is_name_word(tk[i + 1], strict_label=True):
                    vals, _ = read_value(tk, i + 2)
                    reads.append(_mk("taxpayer", vals, doc, "1) Name", pi, tk, i + 2, 8))
        if "propriet" in low or "owner" in low:
            for i, t in enumerate(tk):
                w = alpha(t).lower()
                if w.startswith("propriet") or w in ("owners", "owner"):
                    j = i + 1
                    while j < len(tk) and (alpha(tk[j]).lower().startswith(("propriet", "assess", "owner")) or
                                           is_name_word(tk[j]) or not alpha(tk[j])):
                        j += 1
                    if j - i > 6:
                        continue
                    vals, _ = read_value(tk, j)
                    if vals:
                        reads.append(_mk("proprietor", vals, doc, "Proprietor/Owner name", pi, tk, j, 8))
        if re.search(r"employ|working\s+(?:as|with|in)|service\s+holder|salary", low):
            reads += _certify_that(tk, doc, pi, "employee", "certify that (employment)",
                                   until=r"employ|work|serv|\bis\b|s/o|son|daughter|has|join|holding")
    return reads


EXTRACTORS = {
    "birth_cert": extract_birth_cert,
    "father_nid": lambda r: extract_nid(r, "father_nid"),
    "mother_nid": lambda r: extract_nid(r, "mother_nid"),
    "student_nid": lambda r: extract_nid(r, "student_nid"),
    "father_death_cert": lambda r: extract_nid(r, "father_death_cert"),
    "mother_death_cert": lambda r: extract_nid(r, "mother_death_cert"),
    "nid_unassigned": lambda r: extract_nid(r, "nid_unassigned"),
    "death_cert": lambda r: extract_nid(r, "death_cert"),
    "family_cert": extract_family_cert,
    "academic": extract_academic,
    "income_tax": lambda r: extract_financial(r, "income_tax"),
}
_MONEY_EXTRACTORS = {"trade_license": lambda r: extract_financial(r, "trade_license")}


def unit_rec(u: DocUnit, pages: List[PageRead]) -> dict:
    texts = [pages[i].text for i in u.pages]
    return {"text": "\n".join(texts), "pages": texts}


def _unit_pages(u: DocUnit, births: Dict[Tuple[int, ...], BirthFacts]) -> List[int]:
    """The run pages a unit's names are read on: a birth certificate only on its English pages."""
    if u.key == "birth_cert":
        facts = births.get(tuple(u.pages))
        if facts is not None:
            own = set(u.pages)
            return [i for i in facts.english_pages if i in own]
    return list(u.pages)


def collect_reads(units: List[DocUnit], pages: List[PageRead], births: Dict[Tuple[int, ...], BirthFacts],
                  money: bool = False) -> Tuple[List[Read], Optional[dict]]:
    """Every name read on the units (EXTRACTORS by unit.key; the passport via extract_passport), each with
    its unit uid and 0-based run page, plus the passport's MRZ name (its "page" a run page, "unit" a uid).
    Bank and trade_license units are not read (their person checks are bank.py's and financial.py's);
    money=True reads the trade licence units too, as vocabulary only."""
    reads: List[Read] = []
    mrz = None
    for u in units:
        idx = _unit_pages(u, births)
        if not idx:
            continue
        rec = {"text": "\n".join(pages[i].text for i in idx), "pages": [pages[i].text for i in idx]}
        m = None
        if u.key == "passport":
            rs, m = extract_passport(rec)
        elif u.key in EXTRACTORS:
            rs = EXTRACTORS[u.key](rec)
        elif money and u.key in _MONEY_EXTRACTORS:
            rs = _MONEY_EXTRACTORS[u.key](rec)
        else:
            continue
        for r in rs:
            r.unit = u.uid
            r.page = idx[r.page] if 0 <= r.page < len(idx) else idx[0]
        reads += rs
        if m and (mrz is None or (m["ok"] and not mrz["ok"])):
            m["page"] = idx[m.get("page", 0)] if 0 <= m.get("page", 0) < len(idx) else idx[0]
            m["unit"] = u.uid
            mrz = m
    return reads, mrz


# ============================================================================ variants (describe only)
def _run_at(target: str, win: Sequence[str], any_order: bool = False, extra: int = 2,
            nwords: int = 0) -> Optional[Tuple[int, int]]:
    """(start, end) of the first contiguous run of window parts whose letters spell `target` (spacing
    aside; with any_order the parts of the run in any order), else None. The strict.find_run rule on a
    token list."""
    if not target:
        return None
    n = (nwords or 1) + extra
    for i in range(len(win)):
        acc = ""
        for j in range(i, min(len(win), i + n)):
            acc += win[j]
            if acc == target:
                return i, j + 1
            if any_order and len(acc) == len(target) and sorted(acc) == sorted(target) and \
                    strict.compare(target, tuple(win[i:j + 1]), any_order=True, dots=False) == "EQUAL":
                return i, j + 1
            if len(acc) >= len(target):
                break
    return None


def align_cost(a: Sequence[str], b: Sequence[str]) -> int:
    """Token alignment cost (order-insensitive greedy): each token's distance to its closest partner,
    an unpaired token costing its length. Describes a difference; never decides sameness."""
    a, b = list(a), list(b)
    cost = 0
    rest = list(b)
    for x in a:
        if not rest:
            cost += len(x)
            continue
        y = min(rest, key=lambda z: edit_distance(x, z))
        d = edit_distance(x, y)
        if d > max(2, len(x) // 2):
            cost += len(x)
        else:
            cost += d
            rest.remove(y)
    cost += sum(len(y) for y in rest)
    return cost


def best_variant(canon: Sequence[str], win: Sequence[str]) -> Tuple[Tuple[str, ...], int, int, int]:
    """(run, cost, start, length): the run of the window that lines up best with the reference parts
    (fuzzy, to DESCRIBE a difference only): lengths len-1 .. len+1, the lowest summed distance; an
    honorific printed right before the run belongs to the name (the cost stays the run's own, so a long
    honorific written where the reference has MD is still a variant, not an unread window)."""
    best: Tuple[Tuple[str, ...], int, int, int] = ((), 10 ** 6, -1, 0)
    best_key = (10 ** 6, 10 ** 6)
    k = len(canon)
    for L in (k, k - 1, k + 1):
        if L <= 0:
            continue
        for s in range(0, max(1, len(win) - L + 1)):
            run = tuple(win[s:s + L])
            if not run:
                continue
            if any(alpha(t).lower() in STOP and t not in canon for t in (run[0], run[-1])):
                continue
            cost = align_cost(canon, run)
            key = (-sum(1 for x in canon if x in run), cost)
            if key < best_key:
                best_key, best = key, (run, cost, s, L)
    run, cost, s, L = best
    if run and canon and is_honorific(canon[0]) and s > 0 and is_honorific(win[s - 1]) \
            and not is_honorific(run[0]):
        best = ((win[s - 1],) + run, cost, s - 1, L + 1)
    return best


def differing_pairs(canon: Sequence[str], var: Sequence[str]) -> List[Tuple[str, str]]:
    """(reference part, read part) pairs that differ (spelling-level), '' for an unpaired part."""
    rest = list(var)
    out = []
    for x in canon:
        if x in rest:
            rest.remove(x)
            continue
        if not rest:
            out.append((x, ""))
            continue
        y = min(rest, key=lambda z: edit_distance(x, z))
        if edit_distance(x, y) <= max(2, len(x) // 2):
            out.append((x, y))
            rest.remove(y)
        else:
            out.append((x, ""))
    out += [("", y) for y in rest]
    return out


# Glyph pairs EasyOCR confuses on scanned print (upper case after normalisation).
_CONFUSABLE = [
    ("C", "E"), ("C", "G"), ("C", "O"), ("O", "D"), ("O", "Q"), ("O", "U"), ("I", "L"), ("I", "J"), ("I", "T"),
    ("L", "T"), ("I", "Y"), ("U", "V"), ("N", "H"), ("N", "M"), ("N", "U"), ("H", "B"), ("H", "K"), ("E", "F"),
    ("P", "R"), ("B", "R"), ("A", "O"), ("A", "U"), ("E", "O"), ("G", "Q"), ("S", "Z"), ("K", "X"), ("A", "R"),
    ("Y", "V"), ("E", "L"), ("W", "V"), ("D", "B"), ("A", "E"),
    ("M", "H"), ("M", "R"), ("M", "V"), ("M", "L"), ("M", "E"), ("E", "I"), ("A", "N"), ("A", "M"),
    ("I", "A"), ("K", "C"), ("J", "T"), ("Q", "O"), ("U", "I"), ("K", "I"), ("K", "R"), ("A", "H"),
    ("B", "E"), ("H", "W"), ("G", "C"), ("X", "Y"), ("M", "K"), ("C", "D"),
]
_CONF = {frozenset(p) for p in _CONFUSABLE}
_MULTI = [("RN", "M"), ("M", "NN"), ("CL", "D"), ("II", "U"), ("VV", "W"), ("LI", "U"), ("IN", "M"),
          ("NI", "M"), ("RI", "N"), ("IL", "U")]
_NARROW = set("IL")


def narrow_only(a: str, b: str) -> bool:
    """`a` and `b` differ only by I<->L, or by one I or L dropped/added."""
    if not a or not b or a == b:
        return False
    if len(a) == len(b):
        diffs = [(x, y) for x, y in zip(a, b) if x != y]
        return 0 < len(diffs) <= 2 and all({x, y} <= _NARROW for x, y in diffs)
    if abs(len(a) - len(b)) == 1:
        lo, hi = (a, b) if len(a) < len(b) else (b, a)
        return any(hi[:i] + hi[i + 1:] == lo and hi[i] in _NARROW for i in range(len(hi)))
    return False


def ocr_explicable(a: str, b: str, conf: Optional[set] = None) -> bool:
    """Whether OCR alone could turn part `a` into part `b` (a description, never a match)."""
    conf = _CONF if conf is None else conf
    if not a or not b or a == b:
        return False
    if len(a) == len(b):
        diffs = [(x, y) for x, y in zip(a, b) if x != y]
        return 0 < len(diffs) <= 2 and all(frozenset((x, y)) in conf for x, y in diffs)
    for x, y in _MULTI:
        for s, t in ((x, y), (y, x)):
            i = a.find(s)
            while i >= 0:
                if a[:i] + t + a[i + len(s):] == b:
                    return True
                i = a.find(s, i + 1)
    if abs(len(a) - len(b)) == 1:
        lo, hi = (a, b) if len(a) < len(b) else (b, a)
        for i in range(len(hi)):
            if hi[:i] + hi[i + 1:] == lo and ((i > 0 and hi[i] == hi[i - 1]) or hi[i] in "IL"):
                return True
    return False


def diff_is_ocr_explicable(canon: Sequence[str], var: Sequence[str], conf: Optional[set] = None) -> bool:
    pairs = differing_pairs(canon, var)
    return bool(pairs) and all(x and y and ocr_explicable(x, y, conf) for x, y in pairs)


def diff_is_narrow_only(canon: Sequence[str], var: Sequence[str]) -> bool:
    pairs = differing_pairs(canon, var)
    return bool(pairs) and all(x and y and narrow_only(x, y) for x, y in pairs)


# ============================================================================ affidavit
_AFF_STOP = STOP | {
    "before", "notary", "public", "dhaka", "affidavit", "page", "passports", "passport", "other", "objected",
    "considered", "will", "be", "belongs", "belong", "one", "both", "appearing", "abovementioned", "above",
    "mentioned", "referring", "refers", "refer", "spelled", "spelt", "written", "shall", "treated", "accepted",
    "recognized", "recognised", "all", "purposes", "purpose", "matters", "matter", "visa", "academic", "pursuits",
    "it", "they", "he", "she", "namely", "identity", "individual", "aforementioned", "following", "papers",
    "deponent", "declare", "hereby", "solemnly", "affirm", "state", "names", "spelling", "spellings", "as",
    "contd", "continued", "drt", "sep", "oct", "nov", "dec", "jan", "feb", "mar", "apr", "may", "jun", "jul", "aug",
    "reg", "serial", "me", "family", "death", "nid", "birth", "certificate", "certificates", "financial", "bank",
}
_GROUP_LEAD = re.compile(r"(?:\b\d\s*[._,]\s+|\bso\b[,;:]?|declare\s+that|that[,;:]?\s|names?\s+(?:is|are|of)?|"
                         r"name\s+is|namely|\bis\b|known\s+as)", re.I)
_TRAIL = re.compile(r"(?:[\s,;:._]+(?:is|are|was|were|refers?|to|one|ona|and|the|both|belongs?|of|being|all|same|"
                    r"my|his|her|mother|father|namely|they|he|she|it|person))+[\s,;:._]*$", re.I)


@dataclass
class AffGroup:
    variants: List[str]        # the same-person spellings AS WRITTEN (dots kept)
    whose: str = ""            # father | mother | student | ""
    wording: str = ""


def _same_person_at(tk: List[str]) -> List[Tuple[int, int]]:
    """(start, end) token spans of 'same person' as OCR printed it."""
    out = []
    for i, t in enumerate(tk):
        w = alpha(t).lower()
        if not (w and (_near(w, "same", 1) or w in ("samn", "gamn", "sarne"))):
            continue
        for k in range(i + 1, min(len(tk), i + 3)):
            v = alpha(tk[k]).lower()
            if _near(v, "person", 2) or _near(v, "individual", 2) or _near(v, "identity", 2):
                out.append((i, k + 1))
                break
    return out


def _split_variants(chunk: str) -> List[str]:
    """'<A> and <B> & <C>' -> the name spellings as written; an honorific in the middle of a run starts a
    new name (OCR often drops the 'and')."""
    out: List[List[str]] = []
    for piece in re.split(r"\s*(?:\band\b|\bad\b|&|,|;|\bor\b|\balias\b|/|\"\s+\"?|“|”)\s*", chunk, flags=re.I):
        piece = re.sub(r"[\"“”'`*()\[\]]", " ", piece)
        words = [w for w in piece.split() if name_like(w)]
        cur: List[str] = []
        for w in words:
            p = nt(w)
            if not p or alpha(w).lower() in _AFF_STOP:
                if cur:
                    out.append(cur)
                    cur = []
                continue
            if cur and is_honorific(p) and len(cur) >= 2:
                out.append(cur)
                cur = []
            cur.append(w)
        if cur:
            out.append(cur)
    keep = []
    for v in out:
        ps = [nt(w) for w in v]
        if 1 <= len(ps) <= 6 and not (len(ps) == 1 and is_honorific(ps[0])):
            keep.append(" ".join(v))
    return keep


def _related(a: str, b: str) -> bool:
    """Two declared spellings of one person share a part, have parts within 2 letters, or alike letters
    (which affidavit names belong to one group; never whether two names are the same)."""
    import difflib
    ah = [x for x in name_parts(a) if not is_honorific(x)]
    bh = [x for x in name_parts(b) if not is_honorific(x)]
    if set(ah) & set(bh):
        return True
    if any(len(x) >= 4 and edit_distance(x, y) <= 2 for x in ah for y in bh):
        return True
    return difflib.SequenceMatcher(None, "".join(ah), "".join(bh)).ratio() >= 0.6   # label-match only


def affidavit_groups(text: str) -> List[AffGroup]:
    """The name groups an affidavit declares to be one person, with whose name they are (the lab's five
    templates: '<A> and <B> & <C> are the same person', 'the names "<A>" and "<B>" refer to one and the same
    person, namely my father', ...). A group keeps only names related to another name of the group."""
    groups: List[AffGroup] = []
    tk = _TOK.findall(text or "")
    for s, e in _same_person_at(tk):
        if any(_near(t, "affidavit", 3) for t in tk[max(0, s - 6):s]):
            continue                      # the title "AFFIDAVIT OF SAME PERSON"
        lo = max(0, s - 45)
        before = tk[lo:s]
        seg = " ".join(before)
        while True:
            t2 = _TRAIL.sub("", seg)
            if t2 == seg:
                break
            seg = t2
        cut = 0
        for m in _GROUP_LEAD.finditer(seg):
            if re.search(r"(?:father|mother)\W{0,3}s?\W{0,2}$", seg[max(0, m.start() - 12):m.start()], re.I):
                continue
            cut = m.end()
        body = seg[cut:]
        after = " ".join(tk[e:e + 8]).lower()
        tail = " ".join(before[-6:]).lower()
        whose = ("father" if "father" in after else "mother" if "mother" in after else
                 "father" if re.search(r"my\s+father\W*$|father\W+(?:and|&)\W*$", tail) else
                 "mother" if re.search(r"my\s+mother\W*$|mother\W+(?:and|&)\W*$", tail) else "")
        sub = re.split(r"(?:and\s+)?(?:my\s+)?(father|mother)['’`]?s?\s*name\s*(?:is)?", body, flags=re.I)
        chunks: List[Tuple[str, str]] = []
        if len(sub) > 1:
            if sub[0].strip():
                chunks.append((whose, sub[0]))
            for k in range(1, len(sub), 2):
                chunks.append((sub[k].lower(), sub[k + 1] if k + 1 < len(sub) else ""))
        else:
            chunks.append((whose, body))
        for who, chunk in chunks:
            vs = list(dict.fromkeys(_split_variants(chunk)))
            keep = [v for v in vs if any(_related(v, u) for u in vs if u is not v)]
            if len(keep) >= 2:
                groups.append(AffGroup(keep, who, "same-person sentence"))
    return groups


_AFF_DOCS = {"nid": r"\bnid\b|national\s+id", "birth": r"birth\s+(?:reg|cert)", "academic": r"academic|ssc|hsc|"
             r"transcript|certificates?", "passport": r"passport", "family": r"family\s+(?:cert|relation)|warish",
             "financial": r"tin\b|trade\s+licen|financial|bank|tax"}


def affidavit_documents(text: str) -> List[str]:
    """Which document kinds the affidavit names."""
    low = (text or "").lower()
    return [k for k, rx in _AFF_DOCS.items() if re.search(rx, low)]


def affidavit_info(units: List[DocUnit], pages: List[PageRead]) -> AffidavitInfo:
    """affidavit_groups / affidavit_documents over every affidavit unit."""
    info = AffidavitInfo()
    docs: List[str] = []
    for u in units:
        if u.key != "affidavit":
            continue
        info.present = True
        info.units.append(u.uid)
        text = unit_rec(u, pages)["text"]
        for g in affidavit_groups(text):
            info.groups.append((g.whose, list(g.variants)))
        docs += affidavit_documents(text)
    info.documents = list(dict.fromkeys(docs))
    return info


def _printed(v: str, x: "strict.Name") -> bool:
    """Spelling v of an affidavit group IS name x: letters and dots (a machine read, given as its parts,
    has no dots and is surname first: letters only, any order)."""
    if not strict.letters(x) or not strict.letters(v):
        return False
    if not isinstance(x, str):
        return strict.compare(x, v, any_order=True, dots=False) == "EQUAL"
    return strict.form(v) == strict.form(x)


def covered(aff: AffidavitInfo, a: "strict.Name", b: "strict.Name") -> bool:
    """Both spellings are printed, letter for letter and dot for dot, in ONE same-person group (section 7)."""
    for _, spellings in (aff.groups if aff else ()):
        if any(_printed(v, a) for v in spellings) and any(_printed(v, b) for v in spellings):
            return True
    return False


def covered_by_noise(aff: AffidavitInfo, a: "strict.Name", b: "strict.Name") -> bool:
    """Both spellings appear in one group only up to OCR glyph confusions (the affidavit was read badly):
    FLAG, never NOTE."""
    def close(v: str, x) -> bool:
        k = strict.compare(x, v, any_order=not isinstance(x, str), dots=False)
        if k in ("EQUAL", "OCR"):
            return True
        wx, wv = strict.words(x), strict.words(v)
        return len(wx) == len(wv) and diff_is_ocr_explicable(wx, wv)
    for _, spellings in (aff.groups if aff else ()):
        for i, va in enumerate(spellings):
            if close(va, a) and any(close(vb, b) for k, vb in enumerate(spellings)
                                    if k != i and strict.form(vb) != strict.form(va)):
                return True
    return False


# ============================================================================ canonical names
def _near_any(tok: str, vocab: Iterable[str], maxd: int = 1) -> bool:
    return any(abs(len(tok) - len(v)) <= maxd and edit_distance(tok, v) <= maxd for v in vocab)


def _lead_titles(win: Sequence[str]) -> int:
    """How many words at the START of the window are titles (Q16: LATE, MR, MRS, MS, MISS, DR)."""
    k = 0
    while k < len(win) and win[k] in TITLES:
        k += 1
    return k


def _drop_titles(win: Sequence[str], raw: Sequence[str]) -> Tuple[Tuple[str, ...], Tuple[str, ...]]:
    """The window without its LEADING titles (Q16, as strict.form drops them). A title inside or after a
    name is a word like any other (A1)."""
    raw = tuple(raw) if len(raw) == len(win) else tuple(win)
    k = _lead_titles(win)
    return tuple(win[k:]), tuple(raw[k:])


def _value_span(r: "Read") -> Optional[Tuple[int, int]]:
    """(start, end) of the label's VALUE inside the title-dropped window: read_value's words, the name-like
    words right after the label up to a stop word, a number, a symbol or a closing mark. A trailing "(Late)"
    is a title, not a name word. None when the value is empty or cannot be placed in the window."""
    parts, win = tuple(r.parts), tuple(r.win)
    if not parts or not win:
        return None
    s = 0 if win[:len(parts)] == parts else next(
        (i for i in range(len(win) - len(parts) + 1) if win[i:i + len(parts)] == parts), None)
    if s is None:
        return None
    e = s + len(parts)
    while e > s and win[e - 1] == "LATE":
        e -= 1
    k = _lead_titles(win)
    s = max(s, k) - k
    e -= k
    return (s, e) if e > s else None


def _name_value(parts: Sequence[str]) -> bool:
    """A label value that reads as a person's name (defect: a wholly different name must not pass silently):
    at least two words, at least two of them of three letters or more that are not honorifics."""
    long_ = [p for p in parts if len(p) >= 3 and not is_honorific(p)]
    return len(parts) >= 2 and len(long_) >= 2 and all(len(p) <= 16 for p in parts)


def canonical_detail(person: str, reads: List[Read], mrz: Optional[dict],
                     vocabulary: Sequence[Read] = ()) -> Dict[str, Any]:
    """canonical() with the read it came from: {"parts", "written", "how", "unit", "page", "read", "tail",
    "tail_written"}. Student: the MRZ. Parent (A3): the birth certificate's read (English pages only), cut
    where the name ends: after the last part that some OTHER document also prints (a part within one letter
    counts here ONLY to find where the label's value ends). Whole-page OCR prints a bilingual print's Bangla
    text as Latin junk right after the English value (152 of 390 corpus parents), so the label value alone
    cannot say where the name ends. The value's words after the cut are returned as "tail": identity confirms
    them with the targeted re-read (A1: two reads) before they become part of the name, and a document that
    adds exactly them is FLAG, not FAIL. `vocabulary`: reads of every other unit (critique 4), so a passport
    scan without its parents' page does not leave the cut empty. No vocabulary hit: the birth certificate's
    own read (its label value). No birth-certificate read: the passport field."""
    empty = {"parts": (), "written": "", "how": "no canonical read", "unit": "", "page": -1, "read": None,
             "tail": (), "tail_written": ""}
    if person == "student":
        if mrz and mrz.get("parts"):
            return dict(empty, parts=tuple(mrz["parts"]), written="",
                        how="MRZ" + ("" if mrz.get("ok") else " (line not well formed)"),
                        unit=mrz.get("unit", ""), page=mrz.get("page", -1))
        return dict(empty, how="MRZ unreadable")
    vocab = {p for r in reads if r.person == person and r.doc != "birth_cert" for p in r.parts
             if alpha(p).lower() not in STOP}
    vocab |= {p for r in vocabulary if r.person in (person,) + _NEUTRAL and r.doc != "birth_cert"
              for p in r.parts if alpha(p).lower() not in STOP}
    best: Tuple[int, Tuple[str, ...], Tuple[str, ...], Optional[Read]] = (0, (), (), None)
    for r in reads:
        if r.person != person or r.doc != "birth_cert" or not r.win:
            continue
        win, raw = _drop_titles(r.win, r.win_raw)
        cut, score = 0, 0
        for i, p in enumerate(win[:7]):
            if p in vocab or (len(p) >= 4 and _near_any(p, vocab, 1)) or (i == 0 and is_honorific(p)):
                cut, score = i + 1, score + 1
            elif i - cut >= 1:
                break
        if score > best[0] and any(not is_honorific(p) for p in win[:cut]):
            best = (score, win[:cut], raw[:cut], r)
    if best[1]:
        r = best[3]
        win, raw = _drop_titles(r.win, r.win_raw)
        span = _value_span(r)
        n = len(best[1])
        tail_end = min(span[1], n + 3) if (span and span[0] == 0 and span[1] > n) else n
        return {"parts": best[1], "written": " ".join(best[2]), "how": "birth certificate", "unit": r.unit,
                "page": r.page, "read": r, "tail": tuple(win[n:tail_end]), "tail_written": " ".join(raw[n:tail_end])}
    for r in reads:                                   # the certificate's own label value (no vocabulary hit)
        if r.person == person and r.doc == "birth_cert":
            win, raw = _drop_titles(r.parts, tuple(r.written.split()))
            if any(not is_honorific(p) for p in win):
                return {"parts": win[:6], "written": " ".join(raw[:6]), "how": "birth certificate",
                        "unit": r.unit, "page": r.page, "read": r, "tail": (), "tail_written": ""}
    for r in reads:                                   # fallback: the passport's Father's/Mother's Name
        if r.person == person and r.doc == "passport":
            win, raw = _drop_titles(r.parts, tuple(r.written.split()))
            if any(not is_honorific(p) for p in win):
                return {"parts": win[:6], "written": " ".join(raw[:6]), "how": "passport field",
                        "unit": r.unit, "page": r.page, "read": r, "tail": (), "tail_written": ""}
    return empty


def canonical(person: str, reads: List[Read], mrz: Optional[dict],
              vocabulary: Sequence[Read] = ()) -> Tuple[Tuple[str, ...], str]:
    """(canonical name parts, how it was settled): RUNNER_SPEC 5.5 step 3 (+ vocabulary, critique 4)."""
    d = canonical_detail(person, reads, mrz, vocabulary)
    return d["parts"], d["how"]


# ============================================================================ one read against the reference
@dataclass
class ReadResult:
    read: Read
    status: str                    # MATCH | DIFF | NOT_READ
    variant: Tuple[str, ...] = ()  # normalised parts of the variant (DIFF)
    written: str = ""              # the matched run or the variant, as printed (dots kept)
    kind: str = ""                 # strict.compare kind of the variant (DIFF)
    loose: bool = False            # DIFF: a label value too unlike the reference to line up with it
    added: bool = False            # DIFF: the value is the reference plus other words that are not honorifics
    match_written: str = ""        # added: the reference's run as printed


def judge_read(ref: "strict.Name", r: Read, any_order: bool = False) -> ReadResult:
    """One read against the reference, STRICTLY (A1): the reference's letters must be a contiguous run of the
    read's window (spacing aside). When that run lies inside the label's VALUE (the name-like words right
    after the label), every other word of the value belongs to the document's name: an extra word before or
    after it ("<A> <B> BEGUM", an added MD) is a difference, not a match (no token subset). An honorific
    printed right before the run belongs to the name too. With no run, the closest run of the window names
    the variant (fuzzy, to describe it); a window too unlike the name to line up with it is still a
    difference when the label's value reads as a name of two words or more (loose=True); else NOT_READ."""
    win, raw = _drop_titles(r.win, r.win_raw)
    target = strict.letters(ref)
    ref_words = strict.words(ref)
    if not target or not win:
        return ReadResult(r, "NOT_READ")
    span = _value_span(r) if r.src not in _MACHINE_ORDER else None
    at = _run_at(target, win, any_order, nwords=len(ref_words))
    if at:
        s, e = at
        if span and span[0] <= s and e <= span[1] and (span[0] < s or e < span[1]):
            vs, ve = span
            w = " ".join(raw[vs:ve])
            kind = strict.compare(ref, w, any_order=any_order, dots=False)
            if kind != "EQUAL":
                lead_hon = s > vs and all(is_honorific(p) for p in win[vs:s])
                return ReadResult(r, "DIFF", tuple(win[vs:ve]), w, kind, added=not lead_hon,
                                  match_written=" ".join(raw[s:e]))
        if s > 0 and is_honorific(win[s - 1]) and not any(is_honorific(c) for c in ref_words):
            w = " ".join(raw[s - 1:e])
            return ReadResult(r, "DIFF", tuple(win[s - 1:e]), w,
                              strict.compare(ref, w, any_order=any_order, dots=False))
        return ReadResult(r, "MATCH", tuple(win[s:e]), " ".join(raw[s:e]), "EQUAL")
    run, cost, s, L = best_variant(ref_words, win)
    share = cost / max(1, len(target))
    dropped = (s == 0 and r.src != "table row" and bool(run) and set(run) <= set(ref_words)
               and any(len(w) >= 3 for w in run))
    if not run or (share > 0.5 and not dropped):        # a word dropped right after the label is a variant
        if span and _name_value(win[span[0]:span[1]]):
            vs, ve = span
            w = " ".join(raw[vs:ve])
            kind = strict.compare(ref, w, any_order=any_order, dots=False)
            if kind != "EQUAL":
                return ReadResult(r, "DIFF", tuple(win[vs:ve]), w, kind, loose=True)
        return ReadResult(r, "NOT_READ")
    w = " ".join(raw[s:s + L])
    kind = strict.compare(ref, w, any_order=any_order, dots=False)
    if kind == "EQUAL":
        return ReadResult(r, "MATCH", run, w, "EQUAL")
    return ReadResult(r, "DIFF", run, w, kind)


# ============================================================================ the targeted re-read
def _box_xyxy(box) -> Tuple[float, float, float, float]:
    if box and isinstance(box[0], (list, tuple)):
        xs, ys = [float(p[0]) for p in box], [float(p[1]) for p in box]
        return min(xs), min(ys), max(xs), max(ys)
    x0, y0, x1, y1 = (float(v) for v in box)
    return x0, y0, x1, y1


def _reading_order(items: Sequence[Tuple[Any, str, float]]) -> List[Tuple[Tuple[float, float, float, float], str, float]]:
    """OCR boxes in reading order: rows by vertical centre, left to right inside a row."""
    boxes = [(_box_xyxy(b), str(t or ""), float(c or 0.0)) for b, t, c in items]
    rows: List[Dict[str, Any]] = []
    for it in sorted(boxes, key=lambda x: (x[0][1] + x[0][3]) / 2):
        cy = (it[0][1] + it[0][3]) / 2
        h = max(1.0, it[0][3] - it[0][1])
        for row in rows:
            if abs(cy - row["cy"]) <= 0.5 * max(h, row["h"]):
                row["items"].append(it)
                break
        else:
            rows.append({"cy": cy, "h": h, "items": [it]})
    return [it for row in rows for it in sorted(row["items"], key=lambda x: x[0][0])]


def _upper_ascii(s: str) -> str:
    import unicodedata
    return unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode().upper()


def _box_words(boxes) -> List[Tuple[str, int]]:
    out = []
    for bi, (_, text, _) in enumerate(boxes):
        for w in re.findall(r"[A-Z0-9|]+", _upper_ascii(text)):
            out.append((w, bi))
    return out


def _find_box_run(boxes, target: str, slack: int = 0) -> Optional[Tuple[int, int]]:
    """(first box, last box) of a contiguous word run whose letters spell `target` (slack > 0: the run
    within `slack` edits of it, the closest one), else None."""
    words = _box_words(boxes)
    best = None
    for i in range(len(words)):
        acc = ""
        for j in range(i, min(len(words), i + 8)):
            acc += words[j][0]
            if acc == target:
                return words[i][1], words[j][1]
            if slack and abs(len(acc) - len(target)) <= slack:
                d = edit_distance(acc, target)
                if d <= slack and (best is None or d < best[0]):
                    best = (d, words[i][1], words[j][1])
            if len(acc) >= len(target) + slack:
                break
    return (best[1], best[2]) if best else None


def _word_run(boxes, target: str) -> Optional[Tuple[int, int]]:
    """(first word, last word) indices, in _box_words order, of the first word run spelling `target` exactly."""
    words = _box_words(boxes)
    for i in range(len(words)):
        acc = ""
        for j in range(i, min(len(words), i + 8)):
            acc += words[j][0]
            if acc == target:
                return i, j
            if len(acc) >= len(target):
                break
    return None


def _printed_form(boxes, b0: int, b1: int, target: str) -> Tuple[str, str]:
    """(the run's letters-and-dots form around `target`, the run's text): the dots between the name's
    first and last letter as the re-read printed them."""
    text = " ".join(t for _, t, _ in boxes[b0:b1 + 1])
    f = re.sub(r"\.+", ".", re.sub(r"[^A-Z0-9|.]", "", _upper_ascii(text)))
    lets = f.replace(".", "")
    at = lets.find(target)
    if at < 0:
        return strict.form(text), text
    out, n = [], 0
    for ch in f:
        if ch == ".":
            if at < n < at + len(target):
                out.append(ch)
            continue
        if at <= n < at + len(target):
            out.append(ch)
        n += 1
    return "".join(out), text


class Confirmer:
    """The targeted name re-read (RUNNER_SPEC 5.9.1, section 7), shared by every name check of a run:
    (run page index, variant as written) -> "confirmed" | "refuted" | "unclear".
      1. the page at 300 dpi (engine.page_image_of, memoised) and engine.read_boxes on it (memoised per page);
      2. the box run whose letters equal strict.letters(variant) (else the closest run within 2 edits);
         its union cropped with a 30% margin, scaled x2, read again; the crop saved as work/name_p<NN>_<k>.png;
      3. "confirmed": the re-read prints strict.form(variant) exactly (dots included) with every word's
         confidence >= REREAD_MIN_CONF; "refuted": it prints another spelling with that confidence;
         "unclear": anything else (no box run, low confidence, no engine, budget spent).
    Every re-read is logged in .log (crop, text, confidence, result) for by-eye review (T3)."""

    def __init__(self, engine: Optional[Any], pages: List[PageRead], work_dir: Path,
                 budget: int = REREAD_MAX) -> None:
        self.engine = engine
        self.pages = pages
        self.work_dir = Path(work_dir)
        self.budget = budget
        self.used = 0
        self.memo: Dict[Tuple[int, str], Tuple[str, str]] = {}
        self._boxes: Dict[int, Any] = {}
        self._images: Dict[int, Any] = {}
        self._k: Dict[int, int] = collections.Counter()
        self.log: List[Dict[str, Any]] = []

    def printed(self, page_index: int, variant: str) -> str:
        """The form the re-read printed for (page, variant), "" when unknown."""
        return self.memo.get((page_index, strict.form(variant)), ("", ""))[1]

    def __call__(self, page_index: int, variant: str, reference: "strict.Name" = "") -> str:
        """`reference` (optional, names.evaluate): the spelling the variant differs from. A re-read that
        shows the reference around the variant (a word the page read dropped) refutes the variant."""
        key = (page_index, strict.form(variant))
        if key in self.memo:
            return self.memo[key][0]
        result, printed = self._reread(page_index, variant, reference)
        self.memo[key] = (result, printed)
        return result

    def _reread(self, i: int, variant: str, reference: "strict.Name" = "") -> Tuple[str, str]:
        target = strict.letters(variant)
        ref_letters = strict.letters(reference) if reference else ""
        if ref_letters == target:
            ref_letters = ""
        if self.engine is None or not target or not (0 <= i < len(self.pages)):
            return "unclear", ""
        if self.used >= self.budget:
            self.log.append({"run_page": i, "page": _p1(self.pages, i), "variant": variant, "crop": "",
                             "text": "", "confidence": 0.0, "result": "unclear", "why": "re-read budget spent"})
            return "unclear", ""
        self.used += 1
        entry: Dict[str, Any] = {"run_page": i, "page": _p1(self.pages, i), "variant": variant, "crop": "",
                                 "text": "", "confidence": 0.0}
        try:
            img = self._images.get(i)
            if img is None:
                img = self._images[i] = self.engine.page_image_of(self.pages[i], 300)
            if i not in self._boxes:
                self._boxes[i] = _reading_order(self.engine.read_boxes(img))
            boxes = self._boxes[i]
            run = _find_box_run(boxes, target) or _find_box_run(boxes, target, slack=2)
            if run is None:
                entry.update(result="unclear", why="the name was not found on the page")
                self.log.append(entry)
                return "unclear", ""
            x0 = min(boxes[k][0][0] for k in range(run[0], run[1] + 1))
            y0 = min(boxes[k][0][1] for k in range(run[0], run[1] + 1))
            x1 = max(boxes[k][0][2] for k in range(run[0], run[1] + 1))
            y1 = max(boxes[k][0][3] for k in range(run[0], run[1] + 1))
            my, mx = max(4.0, 0.3 * (y1 - y0)), max(4.0, 0.3 * (x1 - x0))
            h, w = img.shape[:2]
            ya, yb = int(max(0, y0 - my)), int(min(h, y1 + my))
            xa, xb = int(max(0, x0 - mx)), int(min(w, x1 + mx))
            crop = _scale2(img[ya:yb, xa:xb])
            self._k[i] += 1
            path = self.work_dir / f"name_p{i + 1:02d}_{self._k[i]}.png"
            _save_png(crop, path)
            entry["crop"] = str(path)
            again = _reading_order(self.engine.read_boxes(crop))
            entry["text"] = " ".join(t for _, t, _ in again)
            near = _find_box_run(again, ref_letters) if ref_letters else None
            if near is not None:
                wv, wr = _word_run(again, target), _word_run(again, ref_letters)
                if wv is not None and wr is not None and wv != wr and wv[0] <= wr[0] and wr[1] <= wv[1]:
                    near = None      # the reference is only a part of the variant printed here (an added word)
            if near is not None:
                conf = min(c for _, _, c in again[near[0]:near[1] + 1])
                printed = _printed_form(again, near[0], near[1], ref_letters)[0]
                result = "refuted" if conf >= REREAD_MIN_CONF else "unclear"
                entry.update(confidence=round(conf, 3), printed=printed, result=result,
                             why="the re-read shows the reference spelling")
                self.log.append(entry)
                return result, printed if result == "refuted" else ""
            hit = _find_box_run(again, target)
            if hit is None:
                hit = _find_box_run(again, target, slack=2)
            if hit is None:
                entry.update(result="unclear", why="the re-read does not hold the name")
                self.log.append(entry)
                return "unclear", ""
            conf = min(c for _, _, c in again[hit[0]:hit[1] + 1])
            entry["confidence"] = round(conf, 3)
            lets = "".join(w for w, _ in _box_words(again[hit[0]:hit[1] + 1]))
            if target in lets:
                printed, _ = _printed_form(again, hit[0], hit[1], target)
            else:
                printed = strict.form(entry["text"])
                best = None
                for a in range(hit[0], hit[1] + 1):
                    for b in range(a, hit[1] + 1):
                        t = " ".join(x for _, x, _ in again[a:b + 1])
                        d = edit_distance(strict.letters(t), target)
                        if best is None or d < best[0]:
                            best = (d, strict.form(t))
                printed = best[1] if best else printed
            entry["printed"] = printed
            if conf < REREAD_MIN_CONF:
                result = "unclear"
            elif printed == strict.form(variant):
                result = "confirmed"
            else:
                result = "refuted"
            entry["result"] = result
            self.log.append(entry)
            return result, printed if result != "unclear" else ""
        except Exception as exc:             # a re-read never breaks the run: the answer is "unclear"
            entry.update(result="unclear", why=f"re-read failed: {type(exc).__name__}")
            self.log.append(entry)
            return "unclear", ""


def _scale2(img):
    try:
        import cv2
        return cv2.resize(img, None, fx=2.0, fy=2.0, interpolation=cv2.INTER_CUBIC)
    except ImportError:                       # pragma: no cover - cv2 is in the numpy venv
        return img.repeat(2, axis=0).repeat(2, axis=1)


def _save_png(img, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        import cv2
        import numpy as np
        arr = np.ascontiguousarray(img[:, :, ::-1] if getattr(img, "ndim", 2) == 3 else img)
        ok, buf = cv2.imencode(".png", arr)
        if ok:
            with open(path, "wb") as fh:
                fh.write(buf.tobytes())
    except ImportError:                       # pragma: no cover
        pass


def make_confirmer(engine: Optional[Any], pages: List[PageRead], work_dir: Path) -> Callable[[int, str], str]:
    """(run page index, variant as written) -> "confirmed" | "refuted" | "unclear"; engine None -> "unclear"."""
    return Confirmer(engine, pages, work_dir)


def _unclear(_page: int, _variant: str) -> str:
    return "unclear"


# ============================================================================ the A1/A2 evaluation
@dataclass
class NameFinding:
    """One judged difference (the names-internal finding; model.Finding is what leaves this module)."""
    person: str
    unit: str
    page: int                      # 0-based run page of the variant's first read
    variant: str                   # as written
    kind: str                      # strict.compare kind (letters), "DOTS", or "UNREAD" (a single unlike read)
    verdict: str                   # PASS | NOTE | FLAG | FAIL
    reason: str
    confirmed: bool = False
    contradicted: bool = False
    affidavit: str = ""            # covered | covered-up-to-noise | not-covered | no-affidavit
    reread: str = ""               # confirmed | refuted | unclear | ""
    pages: Tuple[int, ...] = ()    # 0-based run pages


@dataclass
class NamesResult:
    per_unit: Dict[str, List[Finding]]   # uid -> model.Finding (rule "names.<person>" / "names.<person>_dots")
    per_person: Dict[str, str]           # "student"/"father"/"mother" -> worst verdict
    summary: List[Finding]               # one line per person for the NAMES cross line
    rereads: List[Dict[str, Any]]        # crop, re-read text, confidence, effect
    findings: List[NameFinding] = field(default_factory=list)


@dataclass
class _Ref:
    name: "strict.Name"            # what the letters are compared with
    any_order: bool                # MRZ words (surname first): any order, no dots
    written: str                   # the dot reference (A2): passport printed / birth certificate
    confirmed: bool
    source: str
    unit: str
    display: str
    tail: Tuple[str, ...] = ()     # the birth certificate's unconfirmed further words (NameRef.tail)


_SRC_TEXT = {"passport MRZ": "passport (MRZ)", "passport MRZ re-read": "passport (MRZ)",
             "passport printed": "passport", "birth certificate": "birth certificate",
             "passport field": "passport", "father NID": "father's NID", "mother NID": "mother's NID"}
_WHO = {"student": "the student's", "father": "the father's", "mother": "the mother's"}


def _reference(ident: Identity, person: str) -> Optional[_Ref]:
    ref = getattr(ident, person)
    src = _SRC_TEXT.get(ref.source, ref.source or "reference")
    if person == "student" and ref.written and strict.letters(ref.written):
        return _Ref(ref.written, False, ref.written, ref.confirmed, "passport", ref.unit, ref.written)
    if person == "student" and ref.parts:
        return _Ref(tuple(ref.parts), True, ref.written, ref.confirmed, src, ref.unit, " ".join(ref.parts))
    tail = tuple(getattr(ref, "tail", ()) or ())
    if ref.written and strict.letters(ref.written):
        return _Ref(ref.written, False, ref.written, ref.confirmed, src, ref.unit, ref.written, tail)
    if ref.parts:
        name = " ".join(ref.parts)
        return _Ref(name, False, ref.written, ref.confirmed, src, ref.unit, name, tail)
    return None


def adds_tail(ref: "strict.Name", variant: "strict.Name", tail: Sequence[str]) -> bool:
    """True when the variant is the reference plus (an OCR reading of) the first word(s) of its unconfirmed tail:
    the reference's own document may print them too (NameRef.tail), so the difference cannot be FAIL. Letters
    are compared (a glued "MdName" counts), the added part within OCR distance of the tail (the tail is itself
    a single whole-page read)."""
    lr, lv = strict.letters(ref), strict.letters(variant)
    if not tail or not lr or not lv.startswith(lr) or len(lv) == len(lr):
        return False
    rest = lv[len(lr):]
    for k in range(1, len(tail) + 1):
        t = "".join(strict.letters(w) for w in tail[:k])
        if t and strict._edit(rest, t) <= (2 if len(t) >= 5 else 1 if len(t) >= 3 else 0):
            return True
    return False


def adds_at_tail(ref: "strict.Name", variant: str, tail: Sequence[str]) -> bool:
    """True when the variant is the reference plus at most as many words as its unconfirmed tail, at its END, and
    the words differ from the tail (adds_tail covers an OCR reading of the tail itself). The reference's own
    document goes on there with words no second read shows: whether it ends at the reference ("<A> <B>" + OCR of
    the Bangla print) or prints a word of its own there ("<A> <B> <Y>"), the variant's "<A> <B> <X>" differs from
    it only through an unconfirmed read, so the difference is "check by eye" (A1), never FAIL (review 2, Q19)."""
    if not tail:
        return False
    rw, vw = list(strict.words(ref)), list(strict.words(variant))
    return bool(rw) and 0 < len(vw) - len(rw) <= len(tail) and vw[:len(rw)] == rw


def _p1(pages: List[PageRead], i: int) -> int:
    """1-based input page of run page i (bundle page, or page of the folder file)."""
    if 0 <= i < len(pages):
        return int(pages[i].file_page) + 1
    return i + 1


def _pages_text(pages: List[PageRead], idx: Iterable[int]) -> str:
    ps = sorted({_p1(pages, i) for i in idx})
    return ("page " if len(ps) == 1 else "pages ") + ", ".join(str(p) for p in ps)


def _extends(ref: "strict.Name", variant: str) -> bool:
    """The variant only extends the (possibly cut) MRZ name: every MRZ word but the last is a word of the
    variant and the last is the start of one (or the variant only adds words)."""
    rw, vw = list(strict.words(ref)), list(strict.words(variant))
    if not rw or len(vw) < len(rw):
        return False
    rest = list(vw)
    for w in rw[:-1]:
        if w not in rest:
            return False
        rest.remove(w)
    return any(v.startswith(rw[-1]) for v in rest)


def _separators(name: str) -> set:
    """Letter counts at which the name as written has a separator (a space or any mark) between two letters."""
    out, n, gap = set(), 0, False
    for ch in strict._upper(name):
        if re.match(r"[A-Z0-9|]", ch):
            if gap and n:
                out.add(n)
            gap = False
            n += 1
        else:
            gap = True
    return out


def glued_at(written: str, positions: Iterable[int]) -> bool:
    """True when the read lacks a dot at one of `positions` (letter counts where some spelling of the name has a
    dot) AND, at that place, OCR printed the two letters with nothing between them ("M.Sample" read
    "MSample", "Md.Sample" read "MdSample"). That is how OCR drops a dot printed with no space after it; the
    zoomed re-read drops it the same way (test 1, integration), so such a read can never confirm that the dot
    is absent: its dots stay "check by eye" (A1), never confirmed."""
    if not written:
        return False
    have, seps = set(strict.dot_pattern(written)), _separators(written)
    n_letters = len(strict.letters(written))
    return any(0 < n < n_letters and n not in have and n not in seps for n in positions)


def glued_dot_loss(written: str, dot_ref: Optional[str]) -> bool:
    """glued_at() against the reference's dots only."""
    if not dot_ref or not written:
        return False
    return glued_at(written, strict.dot_pattern(dot_ref))


def _reread_text(confirm: Callable, page: int, variant: str) -> str:
    fn = getattr(confirm, "printed", None)
    try:
        return fn(page, variant) if callable(fn) else ""
    except Exception:
        return ""


def evaluate(ident: Identity, units: List[DocUnit], pages: List[PageRead], aff: AffidavitInfo,
             machine: List[MachineRead], births: Dict[Tuple[int, ...], BirthFacts],
             confirm: Callable[[int, str], str],
             extra_reads: Sequence[Tuple[str, str, str, int]] = ()) -> NamesResult:
    """The A1 letters rule and the A2 dot rule for the student, the father and the mother, document by
    document (RUNNER_SPEC 5.9.1 steps 1-5). Bank and trade-licence units produce no finding here; their
    `extra_reads` (person, uid, written, run page) enter the dot rule only."""
    confirm = confirm or _unclear
    reads, _ = collect_reads(units, pages, births)
    order = {u.uid: n for n, u in enumerate(units)}
    title = {u.uid: u.title for u in units}
    per_unit: Dict[str, List[Finding]] = collections.defaultdict(list)
    summary: List[Finding] = []
    per_person: Dict[str, str] = {}
    all_nf: List[NameFinding] = []
    effects: Dict[Tuple[int, str], str] = {}
    has_aff = bool(aff and aff.present)

    def aff_state(cov: bool, noise: bool) -> str:
        return "covered" if cov else "covered-up-to-noise" if noise else "not-covered" if has_aff else "no-affidavit"

    for person in PEOPLE:
        ref = _reference(ident, person)
        if ref is None:
            why = {"student": "the passport's name could not be read",
                   "father": "no birth certificate, passport field or role-confirmed NID gave the father's name",
                   "mother": "no birth certificate, passport field or role-confirmed NID gave the mother's name"}[person]
            summary.append(Finding(FLAG, f"names.{person}",
                                   f"{_WHO[person][4:]} name was not compared across the documents: {why} — check by eye",
                                   SOURCE))
            per_person[person] = FLAG
            continue
        nfs: List[NameFinding] = []
        letters_ok: Dict[str, List[Tuple[str, int]]] = collections.defaultdict(list)
        compared = set()
        uids = sorted({r.unit for r in reads if r.person == person} |
                      {m.unit for m in machine if m.person == person}, key=lambda u: order.get(u, 10 ** 6))
        for uid in uids:
            if uid == ref.unit or uid not in order:
                continue
            js = [judge_read(ref.name, r, ref.any_order or r.src in _MACHINE_ORDER)
                  for r in reads if r.person == person and r.unit == uid]
            ms = [m for m in machine if m.person == person and m.unit == uid and strict.letters(m.name)]
            m_match = [m for m in ms if strict.compare(ref.name, m.name, any_order=True, dots=False) == "EQUAL"]
            m_diff = [m for m in ms if m not in m_match]
            match = [j for j in js if j.status == "MATCH"]
            diff = [j for j in js if j.status == "DIFF"]
            # A1 confirmation for the two unsure shapes: a value that ADDS words other than an honorific to the
            # name (whole-page OCR runs a misread label, a table's district or the next field's words, or Bangla
            # text read as Latin, on before or after it: 124 such FLAGs on the corpus, nearly all of them OCR) and
            # a value too unlike the name to line up with it (a misparsed label). Each is a difference only when
            # two reads agree on it: another page of the document or, for added words, the zoomed re-read.
            # Otherwise the added-words read counts as the name inside it, and the unlike value as not read (a
            # NOTE names it). An added honorific (MD, MST) is always a difference (A1).
            keep_d: List[ReadResult] = []
            unread: List[ReadResult] = []
            for j in diff:
                if not (j.added or j.loose):
                    keep_d.append(j)
                    continue
                lk = strict.letters(j.written)
                pgs = {x.read.page for x in diff if (x.added or x.loose) and strict.letters(x.written) == lk}
                ok = len(pgs) >= 2
                if not ok and j.added:
                    ans = (confirm(j.read.page, j.written, reference=ref.display) if isinstance(confirm, Confirmer)
                           else confirm(j.read.page, j.written))
                    ok = ans == "confirmed"
                    if ans:
                        effects[(j.read.page, strict.form(j.written))] = (
                            f"names.{person} {'difference kept' if ok else 'added words not confirmed: same name'}")
                if ok:
                    keep_d.append(j)
                elif j.added:
                    match.append(ReadResult(j.read, "MATCH", tuple(strict.words(j.match_written)), j.match_written,
                                            "EQUAL"))
                else:
                    unread.append(j)
            diff = keep_d
            # A value too unlike the name, read once: not compared, so "check by eye" (FLAG), never PASS (A1);
            # it is not FAIL either (a misparsed label reads like this). No FLAG when another read of this
            # document shows the name.
            for j in (unread[:1] if not match else []):
                why_u = (f"{_WHO[person]} name was not compared here: the label on "
                         f"{_pages_text(pages, [j.read.page])} reads {j.written}, a single read too unlike "
                         f"{ref.display} to be that name — check by eye")
                nfs.append(NameFinding(person, uid, j.read.page, j.written, "UNREAD", FLAG, why_u, False, False,
                                       aff_state(False, False), "", (j.read.page,)))
                per_unit[uid].append(Finding(
                    FLAG, f"names.{person}_unread", why_u,
                    SOURCE, (_p1(pages, j.read.page),), {"person": person, "variant": j.written}))
            if match:      # a card's MRZ name line (30 characters, OCR'd) only supports a printed name read;
                # a label value too unlike the name to line up with it is a misparse when the name is printed
                diff = [j for j in diff if j.read.src not in _MACHINE_ORDER and not j.loose]
            if not (match or diff or ms):
                continue
            compared.add(uid)
            contradicted = bool(match or m_match)
            groups: List[Dict[str, Any]] = []
            for j in diff:
                lk = strict.letters(j.written)
                g = next((g for g in groups if g["letters"] == lk), None)
                if g is None:
                    g = {"letters": lk, "written": j.written, "pages": set(), "machine": [], "variant": j.variant}
                    groups.append(g)
                g["pages"].add(j.read.page)
            for m in m_diff:
                g = next((g for g in groups
                          if strict.compare(g["written"], m.name, any_order=True, dots=False) == "EQUAL"), None)
                if g is None:
                    g = {"letters": strict.letters(m.name), "written": m.name, "pages": set(), "machine": [],
                         "variant": strict.words(m.name)}
                    groups.append(g)
                g["machine"].append(m)
            if not groups:
                for j in match:
                    if j.read.src not in _MACHINE_ORDER:        # an MRZ line has no dots
                        letters_ok[uid].append((j.written, j.read.page))
                continue
            for g in groups:
                v = g["written"]
                kind = strict.compare(ref.name, v, any_order=ref.any_order or bool(g["machine"] and not g["pages"]),
                                      dots=False)
                if kind == "EQUAL":
                    continue
                vpages = sorted(g["pages"])
                vpage = vpages[0] if vpages else g["machine"][0].page
                conf_v = bool(g["machine"]) or (len(vpages) >= 2 and kind != "OCR")
                reread = ""
                if not conf_v and not contradicted and vpages:
                    reread = (confirm(vpages[0], v, reference=ref.display) if isinstance(confirm, Confirmer)
                              else confirm(vpages[0], v))
                    if reread == "confirmed":
                        conf_v = True
                cov = covered(aff, ref.name, v)
                noise = (not cov) and covered_by_noise(aff, ref.name, v)
                both = conf_v and ref.confirmed and not contradicted and reread != "refuted"
                where = _pages_text(pages, vpages or [vpage])
                if person == "student" and ident.student.truncated and ref.any_order and _extends(ref.name, v):
                    verdict = FLAG
                    reason = (f"the student's name reads {v} here ({where}) but the MRZ name may be cut "
                              f"(39 characters) — check by eye, {where}")
                else:
                    tail_hit = adds_tail(ref.name, v, ref.tail)
                    tail_other = (not tail_hit) and adds_at_tail(ref.name, v, ref.tail)
                    verdict = strict.judge(kind, cov, both and not (tail_hit or tail_other))
                    if noise and verdict == FAIL:
                        verdict = FLAG
                    if verdict == FAIL:
                        reason = (f"{_WHO[person]} name is {v} here ({where}) but {ref.display} on the "
                                  f"{ref.source}; no affidavit covers it")
                    elif verdict == NOTE:
                        reason = (f"{_WHO[person]} name is {v} here ({where}) but {ref.display} on the "
                                  f"{ref.source}; the affidavit covers both spellings")
                    else:
                        why = []
                        if reread == "refuted":
                            why.append(f"a closer read shows {ref.display} — probably OCR")
                        elif contradicted:
                            why.append(f"another read of this document shows {ref.display} — probably OCR")
                        if noise:
                            why.append("the affidavit names it, but only as OCR read it")
                        if conf_v and not ref.confirmed and not contradicted:
                            why.append(f"the {ref.source} reading itself is a single read")
                        if tail_hit:
                            why.append(f"the {ref.source}'s read goes on with {' '.join(ref.tail)} too, but no "
                                       f"second read shows it")
                        elif tail_other:
                            added = " ".join(strict.words(v)[len(strict.words(ref.name)):])
                            why.append(f"this document adds {added} where the {ref.source}'s read goes on with "
                                       f"{' '.join(ref.tail)}, which no second read shows")
                        reason = (f"{_WHO[person]} name reads {v} here but {ref.display} on the {ref.source} — "
                                  f"check by eye, {where}" + (f" ({'; '.join(why)})" if why else ""))
                nf = NameFinding(person, uid, vpage, v, kind, verdict, reason, conf_v, contradicted,
                                 aff_state(cov, noise), reread, tuple(vpages or [vpage]))
                nfs.append(nf)
                if reread:
                    effects[(vpage, strict.form(v))] = f"names.{person} {verdict}"
                per_unit[uid].append(Finding(verdict, f"names.{person}", reason, SOURCE,
                                             tuple(sorted({_p1(pages, i) for i in nf.pages})),
                                             {"person": person, "variant": v, "reference": ref.display,
                                              "reference_source": ref.source, "kind": kind,
                                              "confirmed": conf_v, "reference_confirmed": ref.confirmed,
                                              "contradicted": contradicted, "reread": reread,
                                              "affidavit": nf.affidavit,
                                              "machine": [m.source for m in g["machine"]]}))
        # ---- the birth certificate's unconfirmed tail (Q19, review 2): the reference is the part another document
        # also prints, but the certificate's read goes on and no second read (another page, the re-read) shows the
        # further words. They may be OCR of the Bangla print, or the parent's real last name: an unconfirmed
        # difference between the birth certificate and every document that stops before them, so "check by eye"
        # (A1: never a silent PASS). One FLAG, on the birth certificate.
        if ref.tail:
            rp = tuple(i for i in (getattr(getattr(ident, person, None), "pages", ()) or ()) if isinstance(i, int)
                       and i >= 0)
            tail_txt = " ".join(ref.tail)
            where_t = f" ({_pages_text(pages, rp)})" if rp else ""
            why_t = (f"{_WHO[person]} name on the {ref.source} reads on after {ref.display} with {tail_txt}"
                     f"{where_t}, but no second read shows {tail_txt} (it may be OCR of the Bangla print) — the other "
                     f"documents are compared with {ref.display}; check by eye (Q19)")
            nfs.append(NameFinding(person, ref.unit, rp[0] if rp else -1, f"{ref.display} {tail_txt}", "TAIL", FLAG,
                                   why_t, False, False, aff_state(False, False), "", rp))
            if ref.unit in order:
                per_unit[ref.unit].append(Finding(FLAG, f"names.{person}_tail", why_t, SOURCE,
                                                  tuple(sorted({_p1(pages, i) for i in rp})),
                                                  {"person": person, "reference": ref.display,
                                                   "tail": list(ref.tail), "run_pages": list(rp)}))
        # ---- A2: the dot rule over the documents whose letters equal the reference's
        target = strict.letters(ref.name)
        for p, uid, written, page in extra_reads:
            if p == person and uid in order and uid != ref.unit and strict.letters(written) == target \
                    and not any(nf.unit == uid for nf in nfs):
                letters_ok[uid].append((written, page))
                compared.add(uid)
        dot_ref = ref.written if (ref.written and strict.letters(ref.written) == target) else None
        entries = {uid: list(rs) for uid, rs in letters_ok.items() if rs}
        every = {strict.dot_pattern(w) for rs in entries.values() for w, _ in rs}
        # a read that ran two letters together where ANY spelling (the reference or another document) has a
        # dot cannot show that dot either way: not on a document, and not on the passport itself
        dot_pos = set(strict.dot_pattern(dot_ref)) if dot_ref else set()
        for rs in entries.values():
            for w, _ in rs:
                dot_pos |= set(strict.dot_pattern(w))
        glued = {uid: any(glued_at(w, dot_pos) for w, _ in rs) for uid, rs in entries.items()}
        ref_glued = bool(dot_ref) and glued_at(dot_ref, dot_pos)
        dot_judge = None if ref_glued else dot_ref
        rereads_of: Dict[str, str] = {}
        if len(every) > 1:
            for uid, rs in entries.items():
                pats = {strict.dot_pattern(w) for w, _ in rs}
                if len(pats) == 1 and len({pg for _, pg in rs}) >= 2 and not glued[uid]:
                    continue                                   # confirmed by two pages
                if len(pats) != 1:
                    continue                                   # the document disagrees with itself: FLAG
                w0, pg0 = rs[0]
                ans = confirm(pg0, w0)
                if ans == "confirmed" and glued[uid]:
                    ans = "unclear"                            # the re-read glues the letters the same way
                rereads_of[uid] = ans
                if ans == "confirmed":
                    rs.append((w0, pg0))
                elif ans == "refuted":
                    seen = _reread_text(confirm, pg0, w0)
                    rs.append(((seen if seen and strict.letters(seen) == target else dot_ref) or
                               (w0.replace(".", " ") if "." in w0 else w0 + "."), pg0))
        others = []
        for uid, rs in entries.items():
            pats = [strict.dot_pattern(w) for w, _ in rs]
            ok = len(set(pats)) == 1 and (len({pg for _, pg in rs}) >= 2 or rereads_of.get(uid) == "confirmed")
            ok = ok and not glued[uid]
            others.append((uid, [w for w, _ in rs], ok))
        out = strict.dot_rule(dot_judge, others)
        ref_pat = strict.dot_pattern(dot_judge) if dot_judge else None
        if ref_pat is not None and len(every) == 1 and entries:
            pat = next(iter(every))
            if (set(ref_pat) < set(pat) and all(out.get(uid, PASS) == PASS for uid in entries)
                    and not any(glued.values())):
                summary.append(Finding(NOTE, "names.assumed_dots",
                                       f"{_WHO[person][4:]} name has a dot on every other document but not on the "
                                       f"{'passport' if person == 'student' else ref.source} — taken as OK (A2 "
                                       f"unstated case, Q13)", SOURCE, (),
                                       {"person": person, "reference": dot_judge}))
        for uid, rs in entries.items():         # glued reads that differ from the reference: never PASS (A1)
            if out.get(uid, PASS) == PASS and glued[uid] and (
                    ref_pat is None or {strict.dot_pattern(w) for w, _ in rs} != {ref_pat}):
                out[uid] = FLAG
        for uid, names_, ok in others:
            v = out.get(uid, PASS)
            if v == PASS:
                continue
            rs = entries[uid]
            pg = [g for _, g in rs]
            spelled = next((w for w, _ in rs if strict.dot_pattern(w) != strict.dot_pattern(dot_ref or "")),
                           rs[0][0])
            where = _pages_text(pages, pg)
            if v == FAIL and dot_ref and covered(aff, dot_ref, spelled):
                v = NOTE
                reason = (f"the dots in {_WHO[person][4:]} name differ here ({spelled}, {where}) from the "
                          f"passport's printed {dot_ref}; the affidavit covers both spellings")
            elif v == FAIL:
                reason = (f"the dots in {_WHO[person][4:]} name are mixed across the documents, and this one "
                          f"({spelled}, {where}) differs from the {'passport' if person == 'student' else ref.source}"
                          f"'s {dot_ref}; no affidavit covers it")
            elif dot_ref and all(strict.dot_pattern(w) == strict.dot_pattern(dot_ref) for w, _ in rs):
                reason = (f"the dots in {_WHO[person][4:]} name here ({spelled}, {where}) follow the "
                          f"{'passport' if person == 'student' else ref.source}, but another document reads them "
                          f"differently — check by eye")
            else:
                why = []
                if glued.get(uid):
                    why.append("the letters run together here, so OCR may have dropped a dot")
                if ref_glued:
                    why.append(f"the {'passport' if person == 'student' else ref.source}'s own read runs the "
                               f"letters together, so its dot is not clear either")
                if rereads_of.get(uid) == "refuted":
                    why.append("a closer read shows another dot — probably OCR")
                reason = (f"the dot in {_WHO[person][4:]} name differs on {where} ({spelled}) — check by eye"
                          + (f" ({'; '.join(why)})" if why else ""))
            nf = NameFinding(person, uid, pg[0], spelled, "DOTS", v, reason, ok, False,
                             "covered" if v == NOTE else ("not-covered" if has_aff else "no-affidavit"),
                             rereads_of.get(uid, ""), tuple(pg))
            nfs.append(nf)
            if rereads_of.get(uid):
                effects[(pg[0], strict.form(rs[0][0]))] = f"names.{person}_dots {v}"
            per_unit[uid].append(Finding(v, f"names.{person}_dots", reason, SOURCE,
                                         tuple(sorted({_p1(pages, i) for i in pg})),
                                         {"person": person, "spellings": names_, "reference": dot_ref or "",
                                          "confirmed": ok, "reread": rereads_of.get(uid, "")}))
        # ---- the NAMES cross line for this person
        all_nf += nfs
        worst = max((nf.verdict for nf in nfs), key=lambda x: RANK.get(x, 0), default=PASS)
        if not any(nf.verdict in (FAIL, FLAG, NOTE) for nf in nfs):
            n = len(compared) + (1 if ref.unit else 0)
            summary.append(Finding(PASS, f"names.{person}",
                                   f"{_WHO[person][4:]} name is the same on {n} document{'s' if n != 1 else ''}",
                                   SOURCE, (), {"reference": ref.display, "reference_source": ref.source}))
            per_person[person] = PASS
            continue
        bits = []
        for lvl in (FAIL, FLAG, NOTE):
            for nf in nfs:
                if nf.verdict != lvl:
                    continue
                what = ("dots differ" if nf.kind == "DOTS" else "not compared (a single read unlike the name)"
                        if nf.kind == "UNREAD" else f"the read goes on with {nf.variant[len(ref.display) + 1:]}, "
                        f"unconfirmed" if nf.kind == "TAIL" else "spelled differently")
                where_nf = f" ({_pages_text(pages, nf.pages)})" if nf.pages else ""
                bits.append(f"{what} on {title.get(nf.unit, nf.unit)}{where_nf} [{lvl}]")
        run_pages = sorted({i for nf in nfs for i in nf.pages if isinstance(i, int) and i >= 0})
        summary.append(Finding(worst, f"names.{person}", f"{_WHO[person][4:]} name: " + "; ".join(bits), SOURCE,
                               tuple(sorted({_p1(pages, i) for i in run_pages})),
                               {"reference": ref.display, "reference_source": ref.source, "run_pages": run_pages}))
        per_person[person] = worst
    for u in units:                                         # step 6: the family certificate's first row
        if u.key == "family_cert":
            f = family_first_row(unit_rec(u, pages)["text"], ident)
            f.pages = tuple(sorted({_p1(pages, i) for i in u.pages}))
            per_unit[u.uid].append(f)
    rereads = []
    for e in getattr(confirm, "log", []) or []:
        d = dict(e)
        k = (int(d.get("run_page", -1)), strict.form(d.get("variant", "")))
        if k in effects:
            d["effect"] = effects[k]
        rereads.append(d)
    return NamesResult(dict(per_unit), per_person, summary, rereads, all_nf)


# ============================================================================ smaller rows
def family_first_row(text: str, ident: Identity) -> Finding:
    """names.family_first_row: the student's passport name, letter for letter (words in any order), in the
    first 1200 characters of the family certificate."""
    name = ident.student.parts or strict.words(ident.student.written)
    if not strict.letters(name):
        return Finding(FLAG, "names.family_first_row",
                       "the student's passport name could not be read, so the family certificate's first row "
                       "was not compared — check by eye", SOURCE)
    if strict.find_run(name, (text or "")[:1200], any_order=True):
        return Finding(PASS, "names.family_first_row",
                       "the student's name (as on the passport) is in the family certificate's first row", SOURCE)
    return Finding(FLAG, "names.family_first_row",
                   "the student's name (as on the passport) was not found letter for letter in the family "
                   "certificate's first row — check by eye", SOURCE)


def printed_student_name(unit: DocUnit, pages: List[PageRead], facts: Optional[BirthFacts]) -> Tuple[str, bool]:
    """The student's name as written on a birth-certificate unit's ENGLISH pages, and whether two reads
    (on different pages) agree on its letters. A Bangla page's OCR noise never becomes a name."""
    if facts is not None:
        own = set(unit.pages)
        idx = [i for i in facts.english_pages if i in own]
    else:
        idx = list(unit.pages)
    if not idx:
        return "", False
    rec = {"text": "\n".join(pages[i].text for i in idx), "pages": [pages[i].text for i in idx]}
    by: Dict[str, List[Tuple[str, int]]] = collections.defaultdict(list)
    for r in extract_birth_cert(rec):
        if r.person == "student" and r.parts and any(not is_honorific(p) for p in r.parts):
            by[strict.letters(r.written)].append((r.written, idx[r.page] if r.page < len(idx) else idx[0]))
    if not by:
        return "", False
    best = max(by.values(), key=lambda rs: (len({p for _, p in rs}), len(rs)))
    return best[0][0], len({p for _, p in best}) >= 2
