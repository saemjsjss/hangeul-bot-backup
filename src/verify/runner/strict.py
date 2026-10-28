"""The runner's one strict 'same written name' comparison (RUNNER_SPEC section 7; owner decisions A1 and
A2 of 27 Oct 2026). Letter for letter: case and whitespace do not count, every LETTER and every DOT does.
EQUAL is the only match. Every other kind only DESCRIBES a difference (for the message); none of them is
ever a match. Whether a difference is FAIL or FLAG is judge() (A1) and, for dots, dot_rule() (A2).
Standard library only. No personal data."""
from __future__ import annotations

import itertools
import re
import unicodedata
from typing import Dict, Optional, Sequence, Tuple, Union

TITLES = ("LATE", "MRS", "MR", "MISS", "MS", "DR")    # dropped only at the START of a name (Q16)
KINDS = ("EQUAL", "DOTS", "OCR", "ORDER", "SUBSET", "LETTERS", "DIFFERENT", "EMPTY")
KIND_RANK = {k: i for i, k in enumerate(KINDS)}
OCR_KINDS = frozenset({"DOTS", "OCR"})     # a misread can explain these (message wording only, never a pass)
Name = Union[str, Sequence[str]]
_TITLE_RE = re.compile(r"^[^A-Z0-9|]*(?:(?:%s)(?![A-Z0-9|])[^A-Z0-9|]*)+" % "|".join(TITLES))
_FOLD = str.maketrans({"I": "1", "L": "1", "|": "1", "O": "0"})    # OCR look-alikes: I/l/1/| and O/0
_MAX_PERM = 6


def _upper(name: Name) -> str:
    if not isinstance(name, str):
        name = " ".join(name or ())
    s = unicodedata.normalize("NFKD", name or "").encode("ascii", "ignore").decode().upper()
    return _TITLE_RE.sub("", s)


def words(name: Name) -> Tuple[str, ...]:
    """The name's words: runs of letters (and of the OCR look-alikes 0-9 and '|'), cut at spaces, dots and
    every other mark. Leading TITLES are dropped; initials, two-letter words and MD/MST are kept."""
    return tuple(re.findall(r"[A-Z0-9|]+", _upper(name)))


parts = words      # the revision-1 name, kept for callers (split role scoring)


def form(name: Name) -> str:
    """What is compared: upper case, letters (and look-alikes) and dots only. Spaces and every other mark
    are removed; a run of dots is one dot; a dot before the first letter or after the last one is
    punctuation (Q16)."""
    s = re.sub(r"[^A-Z0-9|.]", "", _upper(name))
    return re.sub(r"\.+", ".", s).strip(".")


def letters(name: Name) -> str:
    """form() without its dots."""
    return form(name).replace(".", "")


def dot_pattern(name: Name) -> Tuple[int, ...]:
    """Where the dots stand: for each dot, how many letters come before it ("MD. X" -> (2,))."""
    out, n = [], 0
    for ch in form(name):
        if ch == ".":
            out.append(n)
        else:
            n += 1
    return tuple(out)


def _perm_equal(ws: Sequence[str], target: str) -> bool:
    if not ws or len(ws) > _MAX_PERM or sum(map(len, ws)) != len(target):
        return False
    return any("".join(p) == target for p in itertools.permutations(ws))


def _edit(a: str, b: str) -> int:
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]


def compare(a: Name, b: Name, any_order: bool = False, dots: bool = True) -> str:
    """One of KINDS.
    dots=False compares letters only: the who-is-this checks, whose dots go to dot_rule() through names.
    any_order=True only where one side is a machine read (MRZ, QR): surname-first and without dots. The
    words may then stand in any order and dots are not compared."""
    la, lb = letters(a), letters(b)
    if not la or not lb:
        return "EMPTY"
    if la == lb:
        return "EQUAL" if (any_order or not dots or form(a) == form(b)) else "DOTS"
    wa, wb = words(a), words(b)
    if _perm_equal(wa, lb) or _perm_equal(wb, la):
        return "EQUAL" if any_order else "ORDER"
    if la.translate(_FOLD) == lb.translate(_FOLD):
        return "OCR"
    if set(wa) < set(wb) or set(wb) < set(wa):
        return "SUBSET"
    if _edit(la, lb) <= 2:
        return "LETTERS"
    return "DIFFERENT"


def best_match(name: Name, candidates: Sequence[Name], any_order: bool = False,
               dots: bool = False) -> Tuple[int, str]:
    """(index, kind) of the candidate closest to `name` by KIND_RANK; (-1, "EMPTY") when there is none."""
    best = (-1, "EMPTY")
    for i, c in enumerate(candidates):
        k = compare(name, c, any_order=any_order, dots=dots)
        if best[0] < 0 or KIND_RANK[k] < KIND_RANK[best[1]]:
            best = (i, k)
    return best


def find_run(name: Name, text: str, any_order: bool = False) -> bool:
    """True when a contiguous run of the text's words spells the name letter for letter (dots and spaces
    aside; with any_order, the words of the run in any order)."""
    target = letters(name)
    if not target:
        return False
    ws = re.findall(r"[A-Z0-9|]+", _upper(text))
    n = len(words(name)) + 2
    for i in range(len(ws)):
        acc = ""
        for j in range(i, min(len(ws), i + n)):
            acc += ws[j]
            if acc == target or (any_order and _perm_equal(ws[i:j + 1], target)):
                return True
            if len(acc) >= len(target):
                break
    return False


def judge(kind: str, covered: bool, confirmed: bool) -> str:
    """A1, the one policy for every person check (who-is-this and same-on-every-document):
    EQUAL -> PASS; EMPTY (a side unread) -> FLAG; covered by an affidavit -> NOTE; a CONFIRMED
    difference -> FAIL; any other difference -> FLAG ("check by eye, page N"). `confirmed` means BOTH
    spellings are confirmed (RUNNER_SPEC section 7): each is a machine read, or two independent reads
    agree on it. A dot-only difference (DOTS) is never judged here: dot_rule() decides it."""
    if kind == "DOTS":
        raise ValueError("a dot-only difference is judged by dot_rule()")
    if kind == "EQUAL":
        return "PASS"
    if kind == "EMPTY":
        return "FLAG"
    if covered:
        return "NOTE"
    return "FAIL" if confirmed else "FLAG"


def dot_rule(reference: Optional[Name],
             others: Sequence[Tuple[str, Sequence[Name], bool]]) -> Dict[str, str]:
    """A2, for one person. `reference`: the passport's PRINTED name (student) or the birth certificate (a
    parent); None or "" when it could not be read letter for letter. `others`: (doc id, the spellings read
    on that document, confirmed) for every OTHER document whose letters equal the reference's; confirmed =
    two independent reads agree on one dot pattern and none disagrees. Returns doc id -> PASS | FLAG | FAIL:
      * one dot pattern on every other document -> PASS for all, whether or not the reference has it;
      * confirmed spellings with different dots (mixed) -> a document following the reference: PASS; a
        document whose confirmed dots differ from the reference: FAIL; any other: FLAG;
      * the confirmed spellings agree, an unconfirmed read does not -> that document FLAG, the others PASS;
      * nothing confirmed -> a document whose reads all follow the reference: PASS; the others FLAG.
    The caller turns a FAIL into NOTE when an affidavit covers the two spellings."""
    cands = {doc: {dot_pattern(n) for n in names} for doc, names, _ in others}
    every = set().union(*cands.values()) if cands else set()
    sure = {next(iter(cands[doc])) for doc, _, ok in others if ok and len(cands[doc]) == 1}
    ref = dot_pattern(reference) if reference and letters(reference) else None
    out: Dict[str, str] = {}
    for doc, _, ok in others:
        c = cands[doc]
        firm = ok and len(c) == 1
        if len(every) <= 1:
            out[doc] = "PASS"
        elif len(sure) >= 2:
            if ref is not None and c == {ref}:
                out[doc] = "PASS"
            else:
                out[doc] = "FAIL" if (firm and ref is not None) else "FLAG"
        elif sure:
            s = next(iter(sure))
            if ref is not None and s != ref and any(ref in cands[d] for d, _, _ in others):
                # rev 2.5 (integrator, CHECK_0001): the confirmed dots differ from the reference's while another
                # document reads the reference's dots (not confirmed): the dots may be mixed. A document following
                # the reference is never the one that fails; the confirmed different one is FLAG, never PASS.
                out[doc] = "PASS" if c == {ref} else "FLAG"
            else:
                out[doc] = "PASS" if c <= sure else "FLAG"
        else:
            out[doc] = "PASS" if (ref is not None and c == {ref}) else "FLAG"
    return out
