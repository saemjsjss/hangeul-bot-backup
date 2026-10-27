"""The student's identity without the portal (RUNNER_SPEC 5.5; owner decisions A1, A3, A6 a and A7).

The passport gives the student's name (MRZ line 1, plus the printed name as the dot reference of A2), the
date of birth, the expiry and the number; the birth certificate gives the parents' names (A3), with every
other document as vocabulary (critique 4); after the split, a parent's role-confirmed NID gives a second
reference for the bank holder (A7).

A6 a: OCR often joins the two MRZ lines into one run, and the bot's passport_mrz then misparses a valid
MRZ. mrz_from_words() reads line 2 from its own word; reread_mrz() reads a zoomed crop of the MRZ again.
A valid MRZ (every line-2 check digit agrees) is trusted on doc_verifier._trusted terms.

Only DerivedRecord.portal_view() (all empty but the program) ever reaches the bot's checkers.
No personal data: test names are made-up placeholders.
"""
from __future__ import annotations

import dataclasses
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

from . import names, strict
from .model import FLAG, NOTE, BirthFacts, DocUnit, Finding, Identity, NameRef, PageRead

SOURCE = "runner:identity"
_PARENT_RULES = ("identity.parents_from_passport", "identity.parents_unknown", "identity.parents_from_nid")


def _bot():
    return names._bot()


# ============================================================================ MRZ
_MRZ_KEEP = re.compile(r"[^A-Z0-9<]")
_L1_AT = re.compile(r"P[<KC(]?[A-Z]{3}")
_PASSPORT_KEYS = ("passport_no", "passport_no_ok", "dob", "dob_ok", "expiry", "expiry_ok", "sex", "line2_ok",
                  "checks", "name")


def _mrz_clean(tok: str) -> str:
    t = (tok or "").upper().replace("«", "<<").replace("‹", "<")
    return _MRZ_KEEP.sub("", t)


def _key(m: Optional[Dict[str, Any]]) -> Tuple[bool, int]:
    if not m or "checks" not in m:
        return (False, -1)
    return (bool(m.get("line2_ok")), int(m.get("checks") or 0))


def mrz_from_words(text: str) -> Dict[str, Any]:
    """Line 2 read from its own word (A6 a), in doc_verifier.passport_mrz's keys (+ "line1_ok"), {} when no
    line 2 with a single agreeing check digit is found. Every whitespace token and every pair of adjacent
    tokens (a line OCR split in two) is mapped to [A-Z0-9<] ('«' -> '<<'); a token of >= 88 characters that
    starts with 'P' is cut at 44 into line 1 and line 2; OV.parse_mrz_line2 runs on every 44-character
    window of every token of >= 28 characters, OV.parse_mrz_line1 at each P[<KC(]?[A-Z]{3}."""
    OV = _bot().OV
    toks = [t for t in (_mrz_clean(w) for w in (text or "").split()) if t]
    cands = list(dict.fromkeys(toks + [a + b for a, b in zip(toks, toks[1:])]))
    best2: Optional[Dict[str, Any]] = None
    best1: Optional[Dict[str, Any]] = None
    for t in cands:
        pieces = [t]
        if len(t) >= 88 and t.startswith("P"):
            pieces += [t[:44], t[44:]]
        for p in pieces:
            if len(p) >= 28:
                for s in range(0, max(1, len(p) - 43)):
                    two = OV.parse_mrz_line2(p[s:s + 44])
                    if two and (best2 is None or (two["line2_ok"], two["checks"]) > (best2["line2_ok"],
                                                                                      best2["checks"])):
                        best2 = two
            for m in _L1_AT.finditer(p):
                one = OV.parse_mrz_line1(p[m.start():m.start() + 44])
                if one and (best1 is None or (one["line1_ok"] and not best1["line1_ok"])):
                    best1 = one
    if not best2:
        return {}
    ok1 = bool(best1 and best1.get("line1_ok"))
    return {"passport_no": best2["passport_no"], "passport_no_ok": bool(best2["passport_no_ok"]),
            "dob": best2["dob"] or "", "dob_ok": bool(best2["dob_ok"]),
            "expiry": best2["expiry"] or "", "expiry_ok": bool(best2["expiry_ok"]),
            "sex": best2["sex"], "line2_ok": bool(best2["line2_ok"]), "checks": best2["checks"],
            "name": best1["full_name"] if ok1 else "", "line1_ok": ok1}


def _best_mrz(*cands: Dict[str, Any]) -> Dict[str, Any]:
    best: Dict[str, Any] = {}
    for m in cands:
        if m and _key(m) > _key(best):
            best = m
    return best


_LINE2_HINT = re.compile(r"[A-Z<]{3}\d{7}[MF<]")


def reread_mrz(engine: Any, pages: List[PageRead], page_index: int, work_dir: Path) -> Dict[str, Any]:
    """The targeted MRZ re-read (RUNNER_SPEC 5.5 step 1): the page at 300 dpi, its bottom 35% cropped,
    scaled x2 and read with engine.read_text; both readers run on the result. A sideways page also tries
    the two quarter turns; when no line 2 is found, the boxes that look like MRZ lines are located on the
    page (engine.read_boxes) and that strip is read instead. The crop goes to work/mrz_p<NN>.png. Returns
    the best read in passport_mrz keys plus "crop" and "_text" (the re-read text; the caller drops it)."""
    import numpy as np

    dv = _bot().dv
    nn = names._p1(pages, page_index)
    img = engine.page_image_of(pages[page_index], 300)
    tries = [img]
    if getattr(pages[page_index], "sideways", False):
        tries += [np.rot90(img, 1), np.rot90(img, 3)]
    best: Dict[str, Any] = {}
    best_text, best_crop = "", ""
    for k, im in enumerate(tries):
        h = im.shape[0]
        crop = names._scale2(np.ascontiguousarray(im[int(h * 0.65):, :]))
        path = Path(work_dir) / (f"mrz_p{nn:02d}.png" if k == 0 else f"mrz_p{nn:02d}_turn{k}.png")
        names._save_png(crop, path)
        text = engine.read_text(crop) or ""
        m = _best_mrz(dv.passport_mrz(text), mrz_from_words(text))
        if _key(m) > _key(best) or not best_text:
            best, best_text, best_crop = m, text, str(path)
        if best.get("line2_ok"):
            break
    if not best.get("line2_ok") and hasattr(engine, "read_boxes"):
        try:
            boxes = names._reading_order(engine.read_boxes(img))
            hits = [b for b, t, _ in boxes if _mrz_clean(t).count("<") >= 4 or _LINE2_HINT.search(_mrz_clean(t))]
            if hits:
                y0, y1 = min(b[1] for b in hits), max(b[3] for b in hits)
                x0, x1 = min(b[0] for b in hits), max(b[2] for b in hits)
                m_ = max(4.0, 0.3 * (y1 - y0))
                H, W = img.shape[:2]
                crop = names._scale2(np.ascontiguousarray(
                    img[int(max(0, y0 - m_)):int(min(H, y1 + m_)), int(max(0, x0 - m_)):int(min(W, x1 + m_))]))
                path = Path(work_dir) / f"mrz_p{nn:02d}_boxes.png"
                names._save_png(crop, path)
                text = engine.read_text(crop) or ""
                m = _best_mrz(dv.passport_mrz(text), mrz_from_words(text))
                if _key(m) > _key(best):
                    best, best_text, best_crop = m, text, str(path)
        except Exception:                      # the fallback never breaks the run
            pass
    out = dict(best)
    out["crop"] = best_crop
    out["_text"] = best_text
    return out


# ============================================================================ helpers
def _name_verified(text: str, surname: str, given: str) -> bool:
    """language.passport_name_verified(): every MRZ name word is a whole word of the page text outside
    the MRZ. Task 5's language module when it can be imported; else the same rule on strict.words."""
    try:
        from . import language as L
        fn = getattr(L, "passport_name_verified", None)
        if callable(fn):
            return bool(fn(text, surname, given))
    except Exception:
        pass
    toks = list(strict.words(surname)) + list(strict.words(given))
    if not toks:
        return False
    viz = " ".join(w for w in (text or "").upper().split() if "<" not in w)
    words = set(re.findall(r"[A-Z]+", viz))
    return all(t in words for t in toks)


_MON = r"(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*"
# the date shapes dv.find_dates reads, found WITH their position (find_dates lists numeric dates first)
_DATE_SPAN = re.compile(r"(?i)\b\d{1,2}[./-]\d{1,2}[./-]\d{4}\b|\b\d{4}[./-]\d{1,2}[./-]\d{1,2}\b"
                        r"|\b\d{1,2}\s*(?:st|nd|rd|th)?[ ,.-]*" + _MON + r"[ ,.-]+\d{4}"
                        r"|\b" + _MON + r"[ ,.-]+\d{1,2}(?:st|nd|rd|th)?[ ,.-]+\d{4}")
_DATE_LABEL = re.compile(r"(?i)date\s*of\s*(birth|issue|expiry)|(expiry|issue|birth)\s*date")
EXPIRY_WINDOW = 90           # characters after the label row in which its dates are looked for


def dates_in_order(text: str, find_dates: Callable[[str], List[Any]]) -> List[Any]:
    """The dates of `text` in reading order (each span parsed by the bot's find_dates)."""
    out = []
    for m in _DATE_SPAN.finditer(text or ""):
        got = find_dates(m.group(0))
        if got:
            out.append(got[0])
    return out


def expiry_from_label(pp_text: str, find_dates: Callable[[str], List[Any]]) -> Optional[Tuple[Any, str, bool]]:
    """(expiry, source, trusted) from the passport's "Date of Expiry" label when the MRZ gave none, else None.
    Labels may be printed in a row with their values after them ("Date of Birth Date of Issue Date of Expiry /
    01 JAN 2000 12 FEB 2024 11 FEB 2034", "Date of Issue Issuing Authority Date of Expiry 12 FEB 2024 DIP 11 FEB
    2034"): the date labels of the row (no date between them) are counted, and the expiry is the date at the
    expiry label's position among the dates that follow the row, in reading order (review 2: the first two
    dates, or a numeric date further on, were taken). Trusted only when the row's dates were all read and the
    expiry is the latest of them; else "passport label (unclear)", not trusted. Line breaks count as spaces (a
    text box may wrap "11 FEB" / "2034")."""
    pp_text = " ".join((pp_text or "").split())
    for m in re.finditer(r"(?i)date\s*of\s*expiry|expiry\s*date", pp_text):
        labels = [x for x in _DATE_LABEL.finditer(pp_text) if x.end() <= m.start()]
        row_before = 0
        prev_start = m.start()
        for x in reversed(labels):                # the date labels right before this one, with no date between
            if dates_in_order(pp_text[x.end():prev_start], find_dates) or prev_start - x.end() > 60:
                break
            row_before += 1
            prev_start = x.start()
        after = pp_text[m.end():]
        row_after = 0
        end = m.end()
        for x in _DATE_LABEL.finditer(after):     # date labels after it, before the first date
            if dates_in_order(after[:x.start()], find_dates) or x.start() - (end - m.end()) > 60:
                break
            row_after += 1
            end = m.end() + x.end()
        found = dates_in_order(pp_text[end:end + EXPIRY_WINDOW], find_dates)
        if not found:
            continue
        n_row = row_before + 1 + row_after
        row = found[:n_row]
        if len(row) <= row_before:                # fewer dates than labels: which one is the expiry is not clear
            return row[-1], "passport label (unclear)", False
        exp = row[row_before]
        others = [d for k, d in enumerate(row) if k != row_before]
        if len(row) == n_row and all(exp > d for d in others):
            return exp, "passport label", True
        return exp, "passport label (unclear)", False
    return None


def _note(verdict: str, rule: str, message: str, pages: Sequence[int] = (), **evidence: Any) -> Finding:
    return Finding(verdict, rule, message, SOURCE, tuple(pages), dict(evidence))


def _pick_written(rs: List["names.Read"]) -> "names.Read":
    """Of several printed reads with the right letters: the dot pattern read most often; on a tie the one
    with more dots (OCR drops a dot far more often than it adds one); then the data page first."""
    by: Dict[str, List[names.Read]] = {}
    for r in rs:
        by.setdefault(strict.form(r.written), []).append(r)
    order = {"VIZ given+surname": 0, "personal page name": 1}
    best = max(by.items(), key=lambda kv: (len(kv[1]), kv[0].count("."), -min(order.get(r.src, 2) for r in kv[1])))
    return sorted(best[1], key=lambda r: order.get(r.src, 2))[0]


def _confirmed_elsewhere(written: str, unit: str, page: int, reads: Sequence["names.Read"],
                         confirm: Callable[[int, str], str]) -> bool:
    """A reference spelling is confirmed when another page of its document, another document's read, or
    the targeted re-read gives the same letters (RUNNER_SPEC 5.5 step 3)."""
    target = strict.letters(written)
    if not target:
        return False
    nw = len(strict.words(written))
    for r in reads:
        if r.unit == unit and r.page == page:
            continue
        mo = r.src in names._MACHINE_ORDER
        if (names._run_at(target, r.win, any_order=mo, nwords=nw)
                and names.judge_read(written, r, any_order=mo).status == "MATCH"):    # A1: no token subset
            return True
    return page >= 0 and confirm(page, written) == "confirmed"


def _tail_on_another_page(person: str, longer: str, unit: str, page: int,
                          reads: Sequence["names.Read"]) -> bool:
    """Spec section 7: the same spelling on another page of the same document confirms it. True when another
    page of the birth certificate (its English pages: names.collect_reads reads no other) prints the whole
    value `longer` (the cut name and its tail) as that person's label value, letter for letter."""
    target = strict.letters(longer)
    nw = len(strict.words(longer))
    for r in reads:
        if r.person != person or r.unit != unit or r.page == page or r.doc != "birth_cert":
            continue
        if (names._run_at(target, r.win, nwords=nw)
                and names.judge_read(longer, r).status == "MATCH"):     # the value IS it (no token subset)
            return True
    return False


def _parent_refs(ident: Identity, reads: List["names.Read"], pages: List[PageRead],
                 confirm: Callable[[int, str], str]) -> None:
    """Step 3: the parents' canonical names (A3), every other unit's reads as vocabulary (critique 4)."""
    ident.notes = [n for n in ident.notes if n.rule not in _PARENT_RULES]
    for person in ("father", "mother"):
        d = names.canonical_detail(person, reads, None, reads)
        if d["how"] in ("birth certificate", "passport field") and strict.letters(d["written"]):
            pg = d["page"]
            written, tail = d["written"], tuple(d.get("tail") or ())
            if tail and pg >= 0:
                # A3 + A1: the value's further words count once a second read shows them: another page of the
                # birth certificate printing the whole value, or the zoomed re-read. Unconfirmed, the tail stays
                # and names.evaluate FLAGs it on the birth certificate (never a silent PASS).
                longer = f"{written} {d.get('tail_written') or ' '.join(tail)}"
                if (_tail_on_another_page(person, longer, d["unit"], pg, reads)
                        or confirm(pg, longer) == "confirmed"):
                    written, tail = longer, ()
            ref = NameRef(parts=tuple(strict.words(written)), written=written, source=d["how"],
                          unit=d["unit"], pages=(pg,) if pg >= 0 else (), tail=tail)
            ref.confirmed = _confirmed_elsewhere(written, d["unit"], pg, reads, confirm)
            if d["how"] == "passport field":
                ident.notes.append(_note(NOTE, "identity.parents_from_passport",
                                         f"the {person}'s name is taken from the passport's {person.capitalize()}'s "
                                         f"Name field (no birth-certificate read)",
                                         (names._p1(pages, pg),) if pg >= 0 else (), person=person))
        else:
            ref = NameRef()
            ident.notes.append(_note(FLAG, "identity.parents_unknown",
                                     f"the {person}'s name could not be read on the birth certificate or the "
                                     f"passport — the {person}'s documents cannot be compared strictly; check by eye",
                                     person=person))
        setattr(ident, person, ref)


def _nid_name(person: str, u: DocUnit, reads: List["names.Read"], parent: NameRef,
              pages: List[PageRead]) -> NameRef:
    """The strict name printed on a parent's NID unit (A7): the reference's letters as a run of a read
    when the NID prints them; else the "Name" label value read on the most pages."""
    rs = [r for r in reads if r.unit == u.uid and r.person == person]
    if not rs:
        return NameRef()
    mrz_reads = [r for r in rs if r.src in names._MACHINE_ORDER]
    label = [r for r in rs if r.src not in names._MACHINE_ORDER and r.parts]
    target = strict.letters(parent.written or parent.parts)
    nw = len(strict.words(parent.written or parent.parts))
    if target:
        hits = []
        ref_name = parent.written or " ".join(parent.parts)
        for r in label:                       # A1: the NID prints the reference only when its value IS it
            if names._run_at(target, r.win, nwords=nw):
                jr = names.judge_read(ref_name, r)
                if jr.status == "MATCH":              # a value that adds words is not the reference: the
                    hits.append((jr.written, r.page))  # "Name" value read on the most pages is the NID's name
        if hits:
            ps = tuple(sorted({p for _, p in hits}))
            mrz_ok = any(strict.compare(hits[0][0], m.parts, any_order=True, dots=False) == "EQUAL"
                         for m in mrz_reads)
            return NameRef(parts=tuple(strict.words(hits[0][0])), written=hits[0][0], source=f"{person} NID",
                           unit=u.uid, pages=ps, confirmed=len(ps) >= 2 or mrz_ok)
    by: Dict[str, List[names.Read]] = {}
    for r in label:
        if r.src == "Name":
            by.setdefault(strict.letters(r.written), []).append(r)
    if not by:
        return NameRef()
    best = max(by.values(), key=lambda g: (len({x.page for x in g}), len(g)))
    w = best[0].written
    ps = tuple(sorted({x.page for x in best}))
    mrz_ok = any(strict.compare(w, m.parts, any_order=True, dots=False) == "EQUAL" for m in mrz_reads)
    return NameRef(parts=tuple(strict.words(w)), written=w, source=f"{person} NID", unit=u.uid, pages=ps,
                   confirmed=len(ps) >= 2 or mrz_ok)


# ============================================================================ build / finish
def build_identity(units: List[DocUnit], pages: List[PageRead], births: Dict[Tuple[int, ...], BirthFacts],
                   engine: Optional[Any], work_dir: Path,
                   confirm: Optional[Callable[[int, str], str]] = None) -> Identity:
    """RUNNER_SPEC 5.5 steps 1-8 on the provisional units (before the NID roles are known)."""
    dv = _bot().dv
    confirm = confirm or names._unclear
    ident = Identity(student=NameRef(), father=NameRef(), mother=NameRef())
    pp = [u for u in units if u.key == "passport"]
    pp_uids = {u.uid for u in pp}
    pp_pages = [i for u in pp for i in u.pages]
    unit_of = {i: u.uid for u in pp for i in u.pages}
    pp_text = "\n".join(pages[i].text for i in pp_pages)
    p1 = tuple(names._p1(pages, i) for i in pp_pages)
    reads, mrzname = names.collect_reads(units, pages, births, money=True)
    printed_words = [w for r in reads if r.unit in pp_uids for w in r.parts]

    # 1. MRZ (A6 a): the bot's reader and line 2 from its own word, then the targeted re-read
    best: Dict[str, Any] = {}
    best_page: Optional[int] = None
    best_src = ""
    for i in pp_pages:
        m = _best_mrz(dv.passport_mrz(pages[i].text), mrz_from_words(pages[i].text))
        if _key(m) > _key(best):
            best, best_page, best_src = m, i, "MRZ"
    name_src = "passport MRZ"
    reread_fields: List[str] = []
    reread_pages: List[int] = []
    if engine is not None and pp_pages and (dv.mrz_dob(best) is None or dv.mrz_expiry(best) is None
                                            or not (mrzname and mrzname.get("ok"))):
        order = ([best_page] if best_page is not None else []) + [i for i in pp_pages if i != best_page]
        for i in order[:2]:
            try:
                rr = reread_mrz(engine, pages, i, work_dir)
            except Exception:
                continue
            text = rr.pop("_text", "")
            crop = rr.pop("crop", "")
            if _key(rr) > _key(best):
                before = (dv.mrz_dob(best), dv.mrz_expiry(best), bool(best.get("passport_no_ok")))
                after = (dv.mrz_dob(rr), dv.mrz_expiry(rr), bool(rr.get("passport_no_ok")))
                for f, b, a in zip(("date of birth", "expiry", "passport number"), before, after):
                    if a and not b:
                        reread_fields.append(f)
                rr["crop"] = crop
                best, best_page, best_src = rr, i, "MRZ re-read"
                reread_pages.append(i)
            n2 = names.passport_mrz_name(text, printed=printed_words)
            if n2 and n2.get("ok") and not (mrzname and mrzname.get("ok")):
                n2["page"], n2["unit"] = i, unit_of.get(i, "")
                mrzname, name_src = n2, "passport MRZ re-read"
                reread_fields.append("name line")
                reread_pages.append(i)
            if dv.mrz_dob(best) and dv.mrz_expiry(best) and mrzname and mrzname.get("ok"):
                break
    ident.mrz = {k: v for k, v in best.items() if not k.startswith("_")}

    # 2. the student's name: MRZ letters; the printed name as the dot reference (A2)
    cands = [r for r in reads if r.unit in pp_uids and r.person == "student" and r.parts and
             r.src in ("VIZ given+surname", "personal page name")]
    cands.sort(key=lambda r: 0 if r.src == "VIZ given+surname" else 1)
    student = NameRef()
    if mrzname and mrzname.get("parts"):
        mrz_words = tuple(mrzname["parts"])
        student = NameRef(parts=mrz_words, source=name_src, unit=mrzname.get("unit", ""),
                          pages=(mrzname["page"],) if mrzname.get("page", -1) >= 0 else (),
                          truncated=bool(mrzname.get("truncated")))
        ident.name_trusted = bool(mrzname.get("ok"))
        ident.surname, ident.given_names = mrzname.get("surname", ""), mrzname.get("given", "")
        equal = [r for r in cands if strict.compare(r.written, mrz_words, any_order=True) == "EQUAL"]
        if equal:
            r = _pick_written(equal)
            student.written, student.confirmed = r.written, True
            student.pages = tuple(sorted({r.page} | set(student.pages)))
        elif cands:
            r = cands[0]
            if confirm(r.page, r.written) == "confirmed":
                student.written, student.confirmed = r.written, True
                if not (student.truncated and names._extends(mrz_words, r.written)):
                    ident.notes.append(_note(
                        FLAG, "identity.printed_name",
                        f"the passport's printed name ({r.written}) and its MRZ name ({' '.join(mrz_words)}) differ "
                        f"(page {names._p1(pages, r.page)}) — the printed name is used; check the passport by eye",
                        (names._p1(pages, r.page),)))
            else:
                ident.notes.append(_note(
                    FLAG, "identity.printed_name",
                    f"the passport's printed name could not be read letter for letter (page "
                    f"{names._p1(pages, r.page)}) — dots on the other documents are judged without it",
                    (names._p1(pages, r.page),)))
    elif cands:
        r = cands[0]
        student = NameRef(parts=tuple(strict.words(r.written)), written=r.written, source="passport printed",
                          unit=r.unit, pages=(r.page,))
        student.confirmed = confirm(r.page, r.written) == "confirmed"
        ident.notes.append(_note(FLAG, "identity.mrz_name_unread",
                                 f"the MRZ name line could not be read; the student's name is taken from the "
                                 f"printed name (page {names._p1(pages, r.page)}) — check the passport by eye",
                                 (names._p1(pages, r.page),)))
    else:
        ident.notes.append(_note(FLAG, "identity.mrz_name_unread",
                                 "the student's name could not be read on the passport (no MRZ name line, no "
                                 "printed name) — check the passport by eye", p1))
    ident.name_verified = _name_verified(pp_text, ident.surname, ident.given_names)
    if student.parts and not student.written and not student.confirmed:
        student.confirmed = ident.name_verified     # the printed words are the MRZ letters' second read
    ident.student = student

    # 3. the parents (A3), every unit as vocabulary (critique 4)
    _parent_refs(ident, reads, pages, confirm)

    # 4. date of birth: the trusted MRZ first; untrusted fallbacks
    d = dv.mrz_dob(best) if best else None
    if d:
        ident.dob, ident.dob_source, ident.dob_trusted = d, best_src, True
    else:
        d = dv.labelled_dob(pp_text) if pp_text else None
        if d:
            ident.dob, ident.dob_source = d, "passport label"
        else:
            for facts in births.values():
                if facts.dob:
                    ident.dob, ident.dob_source = facts.dob, "birth certificate"
                    break

    # 5. expiry: the trusted MRZ, the "Date of Expiry" label (trusted), the latest future date (never trusted)
    e = dv.mrz_expiry(best) if best else None
    if e:
        ident.expiry, ident.expiry_source, ident.expiry_trusted = e, best_src, True
    else:
        got = expiry_from_label(pp_text, dv.find_dates)
        if got is not None:
            ident.expiry, ident.expiry_source, ident.expiry_trusted = got
        if ident.expiry is None and pp_text:
            today = getattr(dv, "TODAY", None)
            future = [x for x in dv.find_dates(pp_text) if today is None or x > today]
            if future:
                ident.expiry, ident.expiry_source = max(future), "scan date"

    # 6. passport number: the MRZ (check digit), and the printed one after its label
    ident.passport_no = str(best.get("passport_no") or "")
    ident.passport_no_ok = bool(best.get("passport_no_ok"))
    ident.sex = str(best.get("sex") or "")
    for m in re.finditer(r"(?i)passport\s*(?:no|number|num)\b\.?", pp_text):
        n = re.search(r"(?<![A-Z0-9])([A-Z]{1,2}\d{7,8})(?!\d)", pp_text[m.end():m.end() + 30].upper())
        if n:
            ident.printed_passport_no = n.group(1)
            break

    # 8. notes (to the passport unit's line)
    if student.truncated:
        ident.notes.append(_note(FLAG, "identity.mrz_truncated",
                                 "the MRZ name field is full (39 characters): the name may be cut — the printed "
                                 "name is compared where it was read; check by eye", p1))
    if not best and pp_pages:
        ident.notes.append(_note(FLAG, "identity.mrz_unread",
                                 "the MRZ could not be read — the passport's dates and number are not trusted; "
                                 "check the passport by eye", p1))
    if reread_fields:
        pg = sorted({names._p1(pages, i) for i in reread_pages})
        ident.notes.append(_note(NOTE, "identity.mrz_reread",
                                 f"the MRZ was read again from a zoomed crop (page {', '.join(map(str, pg))}): "
                                 f"{', '.join(dict.fromkeys(reread_fields))}", pg))
    return ident


def finish_identity(ident: Identity, units: List[DocUnit], pages: List[PageRead],
                    births: Dict[Tuple[int, ...], BirthFacts],
                    confirm: Optional[Callable[[int, str], str]] = None) -> Identity:
    """After split.finish (critique 4, A7): the parents' names again with every role-labelled read as
    vocabulary; father_nid / mother_nid from a role-confirmed NID ("name" or "file-name"); when a parent's
    name is still unknown, that NID's name becomes the reference. A position-only role never does."""
    confirm = confirm or names._unclear
    reads, _ = names.collect_reads(units, pages, births, money=True)
    _parent_refs(ident, reads, pages, confirm)
    for person, key in (("father", "father_nid"), ("mother", "mother_nid")):
        nref = NameRef()
        for u in units:
            if u.key == key and u.role_how in ("name", "file-name"):
                nref = _nid_name(person, u, reads, getattr(ident, person), pages)
                if nref.written:
                    break
        setattr(ident, f"{person}_nid", nref)
        if nref.written and not strict.letters(getattr(ident, person).written or getattr(ident, person).parts):
            setattr(ident, person, dataclasses.replace(nref))
            ident.notes = [n for n in ident.notes
                           if not (n.rule == "identity.parents_unknown" and n.evidence.get("person") == person)]
            ident.notes.append(_note(NOTE, "identity.parents_from_nid",
                                     f"the {person}'s name is taken from the {person}'s NID (no birth-certificate "
                                     f"or passport read)", tuple(names._p1(pages, i) for i in nref.pages),
                                     person=person))
    return ident


@dataclass
class _Refs:                       # split.Refs when Task 2's module cannot be imported
    father: List[str]
    mother: List[str]
    student: List[str]


def refs(ident: Identity) -> Any:
    """split.Refs: upper-case strict.words lists for role scoring."""
    try:
        from .split import Refs
    except Exception:
        Refs = _Refs

    def w(ref: NameRef) -> List[str]:
        return list(strict.words(ref.written or ref.parts))
    return Refs(father=w(ident.father), mother=w(ident.mother), student=w(ident.student))


def passport_for_language(ident: Identity) -> Dict[str, Any]:
    """What language.check_language_file gets (critique 12): the date of birth ONLY when trusted."""
    return {"surname": ident.surname, "given_names": ident.given_names, "passport_no": ident.passport_no,
            "dob": ident.dob if ident.dob_trusted else None, "name_verified": ident.name_verified,
            "name_trusted": ident.name_trusted}
