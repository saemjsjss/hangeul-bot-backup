"""Document and overall verdicts (RUNNER_SPEC section 5.10, revision 2; owner decisions A1-A8 of 27 Oct 2026).

  * doc_verdict    one document line: NOT JUDGED (a blocking ORDER notice touches it), else FAIL > FLAG
                   (ERROR shows as FLAG, "could not be checked: ...") > PRESENT > PASS (NOTE counts as PASS).
  * merge          every unit's findings (split notes + each stage's map, in stage order) -> DocResults;
                   ORDER findings are lifted into non-blocking notices (critique 8); units a blocking ORDER
                   notice touches are NOT JUDGED ("fix pages a-b, then check again"); the cross findings
                   (NAMES, FINANCIAL CHAIN, BUNDLE) and every finding whose uid is no unit.
  * missing_lines  the "X MISSING" lines in slot order, the solvency line from bank.py and the conditional
                   death-certificate lines (critique 5 and 17).
  * overall        the owner's words only: STOP / FAIL / FLAG / PASS (critique 20), and the headline.

Standard library only, no I/O, no personal data. The split module is looked up when it is needed
(importlib, so the unit tests can stand a fake in for it).
"""
from __future__ import annotations

import importlib
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

from .model import (ERROR, FAIL, FLAG, MISSING, NOT_JUDGED, NOTE, ORDER, PASS, PRESENT, RANK, STOP, DocResult,
                    DocUnit, Finding, Notice, SplitResult)

MISSING_MAKES = FLAG                 # Q4: the overall when MISSING lines are the only problem
ERROR_PREFIX = "could not be checked: "
KNOWN = frozenset({PASS, NOTE, FLAG, FAIL, ERROR, PRESENT, ORDER})
PARENT_OF_SLOT = {"father_id": "father", "mother_id": "mother"}


def _split_module():
    return importlib.import_module(__package__ + ".split")


# ------------------------------------------------------------------------------------------ pages
def unit_range(u: DocUnit) -> Tuple[int, int]:
    """The unit's 1-based (first, last) page; (0, 0) for a unit without pages."""
    return (min(u.pages) + 1, max(u.pages) + 1) if u.pages else (0, 0)


def page_span(a: int, b: int) -> str:
    """"5" or "6-7" (1-based)."""
    return f"{a}" if a == b else f"{a}-{b}"


def pages_word(a: int, b: int) -> str:
    """"page 5" or "pages 6-7"."""
    return f"page {a}" if a == b else f"pages {a}-{b}"


def _touches(n: Notice, u: DocUnit) -> bool:
    if n.units:
        return u.uid in n.units
    if not u.pages:
        return False
    a, b = unit_range(u)
    return not (b < n.pages[0] or a > n.pages[1])


# ------------------------------------------------------------------------------------------ verdicts
def _level(f: Finding) -> str:
    """What a finding counts as on its document line. An unknown level counts as FLAG (never a silent PASS)."""
    v = f.verdict
    if v in (PASS, NOTE, ORDER):
        return PASS
    if v == PRESENT:
        return PRESENT
    if v == FAIL:
        return FAIL
    return FLAG                      # FLAG, ERROR, NOT JUDGED or anything unknown


def doc_verdict(findings: Sequence[Finding], blocked: bool = False) -> str:
    """NOT JUDGED when blocked; else FAIL > FLAG (ERROR counts as FLAG) > PASS (NOTE counts as PASS);
    PRESENT when a PRESENT finding is there and nothing is FLAG or FAIL."""
    if blocked:
        return NOT_JUDGED
    levels = {_level(f) for f in findings}
    for v in (FAIL, FLAG, PRESENT):
        if v in levels:
            return v
    return PASS


def reasons_of(findings: Sequence[Finding]) -> List[str]:
    """FAIL messages, then ERROR messages ("could not be checked: ..."), then FLAG messages; each once."""
    out: List[str] = []
    for want in (FAIL, ERROR, FLAG):
        for f in findings:
            v = f.verdict if f.verdict in KNOWN else FLAG
            if v != want:
                continue
            msg = (ERROR_PREFIX + f.message) if (v == ERROR and not f.message.startswith(ERROR_PREFIX)) \
                else f.message
            if msg not in out:
                out.append(msg)
    return out


def worst(findings: Iterable[Finding]) -> str:
    """The worst level of a set of findings as a line label: FAIL, FLAG (ERROR included) or PASS."""
    best = PASS
    for f in findings:
        lv = _level(f)
        if lv == FAIL:
            return FAIL
        if lv == FLAG:
            best = FLAG
    return best


# ------------------------------------------------------------------------------------------ merge
def _finding_range(f: Finding, u: DocUnit) -> Tuple[int, int]:
    ps = [p for p in f.pages if isinstance(p, int) and p > 0]
    return (min(ps), max(ps)) if ps else unit_range(u)


def merge(res: SplitResult, *per_unit_maps: Mapping[str, Sequence[Finding]], names: Any = None,
          chain_summary: Sequence[Finding] = (), bundle: Sequence[Finding] = (),
          program: str = "") -> Tuple[List[DocResult], List[Finding], List[Notice]]:
    """Every unit's findings = unit.notes + every map's list for its uid, in stage order (the positional maps,
    then names.per_unit). ORDER-level findings are lifted out of the units into Notice(kind="ORDER",
    blocks=False) (critique 8). Units named by a blocking ORDER notice get NOT JUDGED with the reason "fix
    pages a-b, then check again". cross = names.summary + chain_summary + bundle + every finding filed under
    a uid that is no unit (nothing is dropped). Documents and notices are returned in page order."""
    maps: List[Mapping[str, Sequence[Finding]]] = [m or {} for m in per_unit_maps]
    if names is not None and getattr(names, "per_unit", None):
        maps.append(names.per_unit)
    blocking = [n for n in res.notices if n.kind == ORDER and n.blocks]
    uids = {u.uid for u in res.units}
    lifted: List[Notice] = []
    docs: List[DocResult] = []
    for u in res.units:
        fs: List[Finding] = list(u.notes)
        for m in maps:
            fs.extend(m.get(u.uid, ()) or ())
        keep: List[Finding] = []
        for f in fs:
            if f.verdict == ORDER:
                lifted.append(Notice(kind=ORDER, pages=_finding_range(f, u), message=f.message, blocks=False,
                                     units=(u.uid,)))
            else:
                keep.append(f)
        hit = [n for n in blocking if _touches(n, u)]
        if hit:
            why = [f"fix {pages_word(*n.pages)}, then check again" for n in hit]
            docs.append(DocResult(unit=u, findings=keep, verdict=NOT_JUDGED, reasons=list(dict.fromkeys(why))))
        else:
            docs.append(DocResult(unit=u, findings=keep, verdict=doc_verdict(keep), reasons=reasons_of(keep)))
    cross: List[Finding] = []
    if names is not None:
        cross.extend(getattr(names, "summary", None) or ())
    cross.extend(chain_summary or ())
    cross.extend(bundle or ())
    for m in maps:
        for uid, fs in m.items():
            if uid in uids:
                continue
            for f in fs or ():
                if f not in cross:
                    cross.append(f)
    order = {id(u): i for i, u in enumerate(res.units)}
    docs.sort(key=lambda d: (unit_range(d.unit)[0] if d.unit.pages else 10 ** 6, order[id(d.unit)]))
    notices = sorted(list(res.notices) + lifted, key=lambda n: (n.pages[0], n.pages[1]))
    return docs, cross, notices


# ------------------------------------------------------------------------------------------ missing
def missing_lines(res: SplitResult, program: str, bank: Sequence[Any] = ()) -> List[Tuple[str, str]]:
    """[(MISSING, split.MISSING_TEXT[s]) for s in res.missing] in slot order (income tax only for MASTER),
    + (MISSING, split.SOLVENCY_MISSING_TEXT) when any bank outcome has solvency_missing,
    + (FLAG, "<X MISSING> unless the death certificate on pages a-b is the <parent>'s — check by eye") for
    each res.maybe slot (critique 17)."""
    S = _split_module()
    order = list(getattr(S, "SLOTS", ())) + ["income_tax"]
    idx: Dict[str, float] = {s: i for i, s in enumerate(order)}
    idx["parent_id"] = idx.get("mother_id", len(order)) + 0.5
    texts: Mapping[str, str] = getattr(S, "MISSING_TEXT", {})
    slots = [s for s in dict.fromkeys(res.missing)
             if not (s == "income_tax" and program and program.upper() != "MASTER")]
    slots.sort(key=lambda s: idx.get(s, len(order)))
    out: List[Tuple[str, str]] = [(MISSING, texts.get(s, f"{s.replace('_', ' ').upper()} MISSING")) for s in slots]
    bank = list(bank or ())
    # folder mode: a statement file has no certificate while another bank file has it
    if (any(getattr(o, "solvency_missing", False) for o in bank)
            and not any(getattr(o, "solvency_pages", None) for o in bank)):
        out.append((MISSING, getattr(S, "SOLVENCY_MISSING_TEXT", "BANK SOLVENCY CERTIFICATE MISSING")))
    by_uid = {u.uid: u for u in res.units}
    for slot, uid in sorted((res.maybe or {}).items(), key=lambda kv: idx.get(kv[0], len(order))):
        base = texts.get(slot, f"{slot.replace('_', ' ').upper()} MISSING")
        parent = PARENT_OF_SLOT.get(slot, slot.split("_")[0])
        u = by_uid.get(uid)
        if u is not None and u.pages and u.source_file:            # folder mode: the file names it
            where = f"{u.source_file}"
        else:
            where = pages_word(*unit_range(u)) if (u is not None and u.pages) else "its pages"
        out.append((FLAG, f"{base} unless the death certificate on {where} is the {parent}'s — check by eye"))
    return out


# ------------------------------------------------------------------------------------------ overall
def unattached(docs: Sequence[DocResult], cross: Sequence[Finding]) -> List[Finding]:
    """Cross findings that sit on no counted document line (a cross-only chain finding, a bundle finding), so
    the headline counts every line that decides the result. A finding counts as on a line only when a JUDGED
    document carries it: a NOT JUDGED line is counted as NOT JUDGED, never as its findings' FAIL or FLAG (a
    FINANCIAL CHAIN or bank-holder FAIL filed on a TIN unit a blocking ORDER made NOT JUDGED is a loose FAIL,
    review 2). A NAMES summary line counts when no JUDGED document carries a finding of its person at its
    level: "not compared" (it sits on no document), or a difference filed on a NOT JUDGED document."""
    judged = [f for d in docs if d.verdict != NOT_JUDGED for f in d.findings]
    out: List[Finding] = []
    names_loose: List[Finding] = []
    for f in cross:
        if f.rule.startswith("names."):
            lv = _level(f)
            if lv in (FAIL, FLAG) and not any(g.rule.startswith(f.rule) and _level(g) == lv for g in judged):
                names_loose.append(f)
        elif not any(f == g for g in judged):
            out.append(f)
    if names_loose:                     # the NAMES line is ONE line: counted once, at its worst loose level
        out.append(max(names_loose, key=lambda f: RANK.get(_level(f), 1)))
    return out


def overall(docs: Sequence[DocResult], missing: Sequence[Tuple[str, str]], res: SplitResult,
            cross: Sequence[Finding], notices: Sequence[Notice]) -> Tuple[str, str]:
    """STOP when res.stop; else FAIL when any FAIL (documents or cross); else FLAG when any FLAG, ERROR,
    NOT JUDGED, ORDER notice or missing line (MISSING_MAKES); else PASS. PRESENT lines never change it.
    headline: "<OVERALL> — <n> FAIL, <n> FLAG, <n> MISSING[, <n> ORDER][, <n> NOT JUDGED]" (lines: document
    lines plus the cross findings that sit on no document line; the conditional MISSING lines count as FLAG)."""
    n_order = sum(1 for n in notices if n.kind == ORDER)
    if res.stop:
        n_stop = sum(1 for n in notices if n.kind == STOP) or 1
        tail = f", {n_order} ORDER" if n_order else ""
        return STOP, (f"{STOP} — {n_stop} STOP notice{'s' if n_stop != 1 else ''}{tail}; "
                      "no document verdicts until the pages are fixed")
    loose = unattached(docs, cross)
    n_fail = sum(1 for d in docs if d.verdict == FAIL) + sum(1 for f in loose if _level(f) == FAIL)
    n_flag = (sum(1 for d in docs if d.verdict == FLAG) + sum(1 for f in loose if _level(f) == FLAG)
              + sum(1 for lv, _ in missing if lv == FLAG))
    n_missing = sum(1 for lv, _ in missing if lv == MISSING)
    n_nj = sum(1 for d in docs if d.verdict == NOT_JUDGED)
    any_fail = n_fail > 0 or any(_level(f) == FAIL for f in cross)
    if any_fail:
        result = FAIL
    elif (n_flag or n_nj or n_order or missing or any(_level(f) == FLAG for f in cross)):
        result = MISSING_MAKES if (missing and not (n_flag or n_nj or n_order)) else FLAG
    else:
        result = PASS
    head = f"{result} — {n_fail} FAIL, {n_flag} FLAG, {n_missing} MISSING"
    if n_order:
        head += f", {n_order} ORDER"
    if n_nj:
        head += f", {n_nj} NOT JUDGED"
    return result, head


def split_only(res: SplitResult, missing: Sequence[Tuple[str, str]],
               notices: Sequence[Notice]) -> Tuple[List[DocResult], str, str]:
    """--split-only: every unit NOT JUDGED ("split only — not checked"), overall NOT JUDGED and a headline that
    counts the documents, MISSING lines and ORDER notices."""
    docs = [DocResult(unit=u, findings=list(u.notes), verdict=NOT_JUDGED, reasons=["split only — not checked"])
            for u in sorted(res.units, key=lambda u: unit_range(u)[0])]
    n_order = sum(1 for n in notices if n.kind == ORDER)
    head = (f"{NOT_JUDGED} — split only: {len(docs)} document{'s' if len(docs) != 1 else ''}, "
            f"{sum(1 for lv, _ in missing if lv == MISSING)} MISSING, {n_order} ORDER")
    return docs, NOT_JUDGED, head


def chain_summary(*maps: Mapping[str, Sequence[Finding]], rules: Optional[Sequence[str]] = None,
                  prefixes: Sequence[str] = ("financial.",)) -> List[Finding]:
    """The worst finding per rule over the given uid -> findings maps (the FINANCIAL CHAIN line), first seen
    first. `prefixes` / `rules` choose the rules that belong to the line."""
    best: Dict[str, Finding] = {}
    for m in maps:
        for fs in (m or {}).values():
            for f in fs or ():
                if not (f.rule.startswith(tuple(prefixes)) or (rules and f.rule in rules)):
                    continue
                cur = best.get(f.rule)
                if cur is None or RANK.get(_level(f), 1) > RANK.get(_level(cur), 1):
                    best[f.rule] = f
    return list(best.values())
