"""The order-aware split (RUNNER_SPEC section 5.4, revision 2; Task 2).

A port of the lab's order_split.py lines 46-98 and 239-706: the owner scans every document of one student
into ONE bundle PDF in a FIXED order

    passport, photo, NIDs (each translation in front of its original; father, mother, student; a parent's
    death certificate stands in that parent's place), birth certificate, family certificate, academic
    (newest first; certificate then transcript), bank (solvency, then statement), trade licence / TIN /
    employment certificate, income tax

and this module turns the pages' features (pagetype.py) into documents (DocUnits), gives each NID and death
certificate its person without any portal record, lists the missing required documents, and tells the
owner which pages to fix when the pages do not fit the order.

Kept from the lab: the section alignment (Viterbi, `align`, verbatim), the documents inside a section
(`documents`, `split_persons`, `split_academic`, `split_financial`), the person assignment
(`assign_persons`), the misplaced-run messages, the missing-document hints.

Revision-2 changes (owner decisions A1-A8 and the critique, items 5, 6, 7, 16, 17):
  1. academic: one unit per level (bundle); an e-Apostille page joins the level its text names, else the
     level that follows it; a transcript before its certificate stays in its level (an ORDER notice);
  2. financial: one unit per part (trade licence, employment certificate, TIN certificate);
  3. unrecognised page(s) at the end of the bundle right after income tax join the income-tax unit
     (NOTE `split.attached_trailing`, no ORDER notice);
  4. a death certificate belongs to the NID block (no longer a float); outside it, it is misplaced;
  5. notices: STOP only for the NID-person ambiguity; blocking ORDER for misplaced runs and unrecognised
     runs in the middle (those units are NOT JUDGED); non-blocking ORDER for the order breaks inside a
     document or block; HINT for "X MISSING in its place; page N reads partly like it"; no cap;
  6. missing slots: owner_doc (trade licence or employment certificate) and tin are their own slots; a
     death certificate whose person is not known fills no slot (`SplitResult.maybe`);
  7. folder mode: one unit per file, roles from the file name, generic death certificates and unnamed
     NIDs by strict name, FLAG `split.name_vs_content`, no ORDER/STOP;
  8. role scoring and every name helper are strict (strict.compare / strict.words, S8).

Messages hold page numbers and document titles only. No personal data.
"""
from __future__ import annotations

import collections
import re
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Set, Tuple, Union

from . import pagetype as PT
from . import strict
from .model import (FLAG, NOTE, DocUnit, Finding, Notice, PageRead, PageType, Source, SplitResult, page_no)

# ============================================================== 1. slots (what the report gives a line)
SLOTS = ["passport", "photo", "father_id", "mother_id", "birth_cert", "family_cert", "academic", "bank",
         "owner_doc", "tin"]                            # + "income_tax" for MASTER
SLOT_ACCEPTS = {"father_id": ("father_nid", "father_death_cert"), "mother_id": ("mother_nid", "mother_death_cert"),
                "owner_doc": ("trade_license", "employment_certificate"),   # fine types of the trade_license key
                "tin": ("tin_certificate",)}
MISSING_TEXT = {"passport": "PASSPORT MISSING", "photo": "PHOTO MISSING",
                "father_id": "FATHER NID (OR DEATH CERTIFICATE) MISSING",
                "mother_id": "MOTHER NID (OR DEATH CERTIFICATE) MISSING",
                "birth_cert": "BIRTH CERTIFICATE MISSING", "family_cert": "FAMILY CERTIFICATE MISSING",
                "academic": "ACADEMIC CERTIFICATE MISSING", "bank": "BANK SOLVENCY / STATEMENT MISSING",
                "owner_doc": "TRADE LICENSE / EMPLOYMENT CERTIFICATE MISSING",
                "tin": "TIN CERTIFICATE MISSING", "income_tax": "INCOME TAX MISSING",
                # both parents' slots open, fewer death certificates of an unknown person than open slots
                "parent_id": "FATHER OR MOTHER NID (OR DEATH CERTIFICATE) MISSING — a death certificate stands "
                             "for one parent only"}
SOLVENCY_MISSING_TEXT = "BANK SOLVENCY CERTIFICATE MISSING"   # printed by verdict from bank.py's outcome
OPTIONAL_SLOTS = ["student_nid", "language_cert", "affidavit", "eca", "personal_statement", "recommendation",
                  "id_card_cv"]                         # never reported missing; income tax is optional outside MASTER
HINT_MIN = 5.0          # cue score for "page N reads partly like the missing document"


def slots_for(program: str) -> List[str]:
    """The required slots of a program (A8: the owner's --program)."""
    return SLOTS + (["income_tax"] if (program or "").upper() == "MASTER" else [])


# ============================================================== 2. labels of the alignment
SECTIONS = ["passport", "photo", "nid", "birth_cert", "family_cert", "academic", "bank", "financial",
            "income_tax"]                                   # the owner's fixed order
# may sit between any two documents; "death_cert" is no longer one (rev 2: it belongs to the NID block)
FLOATS = ["affidavit", "language_cert", "eca", "personal_statement", "recommendation", "id_card_cv",
          "unrecognised"]
LABELS = SECTIONS + FLOATS
SEC_IDX = {s: i for i, s in enumerate(SECTIONS)}
FINE_OF = {"passport": ["passport"], "photo": ["photo"], "nid": ["nid", "death_cert"],
           "birth_cert": ["birth_cert"], "family_cert": ["family_cert"],
           "academic": ["academic_apostille", "academic_certificate", "academic_transcript"],
           "bank": ["bank_solvency", "bank_statement"],
           "financial": ["trade_license", "tin_certificate", "employment_certificate"],
           "income_tax": ["income_tax"], "death_cert": ["death_cert"], "affidavit": ["affidavit"],
           "language_cert": ["language_cert"], "eca": ["eca"], "personal_statement": ["personal_statement"],
           "recommendation": ["recommendation"], "id_card_cv": ["id_card_cv"], "unrecognised": []}
NICE = {"passport": "PASSPORT", "photo": "PHOTO", "nid": "NID", "birth_cert": "BIRTH CERTIFICATE",
        "family_cert": "FAMILY CERTIFICATE", "academic": "ACADEMIC", "bank": "BANK",
        "financial": "TRADE LICENCE / TIN / EMPLOYMENT", "income_tax": "INCOME TAX",
        "death_cert": "DEATH CERTIFICATE", "affidavit": "AFFIDAVIT", "language_cert": "LANGUAGE CERTIFICATE",
        "eca": "ECA", "personal_statement": "PERSONAL STATEMENT", "recommendation": "RECOMMENDATION",
        "id_card_cv": "ID CARD / CV", "unrecognised": "UNRECOGNISED",
        "owner_doc": "TRADE LICENSE / EMPLOYMENT CERTIFICATE", "tin": "TIN CERTIFICATE"}
# missing slot -> (alignment section, fine types a hint page must read as)
SLOT_SECTION = {"passport": "passport", "photo": "photo", "birth_cert": "birth_cert", "family_cert": "family_cert",
                "academic": "academic", "bank": "bank", "owner_doc": "financial", "tin": "financial",
                "income_tax": "income_tax"}
SLOT_FINE = {"owner_doc": ["trade_license", "employment_certificate"], "tin": ["tin_certificate"]}

# ============================================================== 3. units: keys, titles, file names
ROLE_KEY = {("nid", "father"): "father_nid", ("nid", "mother"): "mother_nid", ("nid", "student"): "student_nid",
            ("death_cert", "father"): "father_death_cert", ("death_cert", "mother"): "mother_death_cert"}
KEY_ROLE = {"father_nid": "father", "mother_nid": "mother", "student_nid": "student",
            "father_death_cert": "father", "mother_death_cert": "mother"}
# folder mode: file-name key -> alignment label
KEY_LABEL = {"passport": "passport", "photo": "photo", "father_nid": "nid", "mother_nid": "nid",
             "student_nid": "nid", "father_death_cert": "death_cert", "mother_death_cert": "death_cert",
             "death_cert": "death_cert", "birth_cert": "birth_cert", "family_cert": "family_cert",
             "academic": "academic", "bank": "bank", "trade_license": "financial", "income_tax": "income_tax",
             "affidavit": "affidavit", "language_cert": "language_cert", "eca": "eca",
             "personal_statement": "personal_statement", "recommendation": "recommendation",
             "id_card_cv": "id_card_cv"}
FINE_LABEL = {"passport": "passport", "photo": "photo", "nid": "nid", "nid_translation": "nid",
              "death_cert": "death_cert", "birth_cert": "birth_cert", "family_cert": "family_cert",
              "academic_apostille": "academic", "academic_certificate": "academic",
              "academic_transcript": "academic", "bank_solvency": "bank", "bank_statement": "bank",
              "trade_license": "financial", "tin_certificate": "financial", "employment_certificate": "financial",
              "income_tax": "income_tax", "language_cert": "language_cert", "affidavit": "affidavit",
              "personal_statement": "personal_statement", "recommendation": "recommendation", "eca": "eca",
              "id_card_cv": "id_card_cv"}
FINANCIAL_TITLE = {"trade_license": "TRADE LICENSE", "employment_certificate": "EMPLOYMENT CERTIFICATE",
                   "tin_certificate": "TIN CERTIFICATE"}
TITLE = {"passport": "PASSPORT", "photo": "PHOTO", "father_nid": "FATHER NID", "mother_nid": "MOTHER NID",
         "student_nid": "STUDENT NID", "father_death_cert": "FATHER DEATH CERTIFICATE",
         "mother_death_cert": "MOTHER DEATH CERTIFICATE", "death_cert": "DEATH CERTIFICATE",
         "birth_cert": "BIRTH CERTIFICATE", "family_cert": "FAMILY CERTIFICATE", "academic": "ACADEMIC",
         "bank": "BANK SOLVENCY / STATEMENT", "trade_license": "TRADE LICENSE", "income_tax": "INCOME TAX",
         "affidavit": "AFFIDAVIT", "language_cert": "LANGUAGE CERTIFICATE", "eca": "ECA",
         "personal_statement": "PERSONAL STATEMENT", "recommendation": "RECOMMENDATION",
         "id_card_cv": "ID CARD / CV", "nid_unassigned": "NID (PERSON NOT KNOWN)",
         "unrecognised": "UNRECOGNISED PAGES"}
# cut file titles: dv.classify(<title> pNN.pdf) gives the key back (tested); the last two are never checked
FILE_TITLE = {"passport": "PASSPORT", "photo": "PASSPORT SIZE PHOTO", "father_nid": "FATHER NID",
              "mother_nid": "MOTHER NID", "student_nid": "STUDENT NID",
              "father_death_cert": "FATHER DEATH CERTIFICATE", "mother_death_cert": "MOTHER DEATH CERTIFICATE",
              "death_cert": "DEATH CERTIFICATE", "birth_cert": "BIRTH CERTIFICATE",
              "family_cert": "FAMILY CERTIFICATE", "academic": "ACADEMIC CERTIFICATE", "bank": "BANK STATEMENT",
              "trade_license": "TRADE LICENSE", "income_tax": "INCOME TAX", "affidavit": "AFFIDAVIT",
              "language_cert": "IELTS TOEFL TOPIK", "personal_statement": "PERSONAL STATEMENT",
              "recommendation": "RECOMMENDATION", "eca": "ECA", "id_card_cv": "ID CARD CV",
              "nid_unassigned": "NID UNASSIGNED", "unrecognised": "UNRECOGNISED"}
NEVER_CHECKED = ("nid_unassigned", "unrecognised")
SOURCE = "runner:split"


# ============================================================== 4. the page view the alignment reads
@dataclass
class _F:
    """The lab's PageFeat, read off a model.PageType (pagetype.py)."""
    scores: Dict[str, float]
    best_fine: str               # pagetype.raw_best: the top type even under the 'other' bar
    best: float
    translation: bool
    numbers: Set[str]
    dobs: Set[str]
    holder: List[str]
    level: Optional[Tuple[str, int]]
    kind: str                    # academic kind (pagetype.academic_kind)
    page_kind: str               # PageType.kind as typed (bank kinds kept)


def _view(pt: Union[PageType, _F]) -> _F:
    if isinstance(pt, _F):
        return pt
    return _F(scores=pt.scores, best_fine=PT.raw_best(pt), best=pt.score, translation=pt.translation,
              numbers=set(pt.nid_numbers), dobs=set(pt.birth_dates), holder=list(pt.holder),
              level=(pt.level, PT.LEVEL_RANK[pt.level]) if pt.level in PT.LEVEL_RANK else None,
              kind=PT.academic_kind(pt), page_kind=pt.kind)


def _views(feats: Sequence[Union[PageType, _F]]) -> List[_F]:
    return [_view(f) for f in feats]


def _other_below() -> float:
    return PT.proto().OTHER_BELOW


# ============================================================== 5. section alignment (Viterbi, verbatim)
@dataclass
class Costs:
    gap: float = 1.0            # per point the label's cue score is below the page's best score
    weak: float = 1.0           # the label's own score is under the 'other' bar
    unrec_enter: float = 0.5    # start an UNRECOGNISED document
    float_enter: float = 1.0    # start an optional document (affidavit, ECA, language ...)
    reenter: float = 2.5        # come back to the section just left (after a float)
    order: float = 6.0          # a section that is not where the fixed order puts it (misplaced run)
    order_page: float = 0.3     # per misplaced page, so the shorter of two runs is the one blamed
    order_photo: float = 6.0    # a misplaced photo (an empty page elsewhere is a blank page)


def _emit(f: _F, lab: str, c: Costs, other_below: float) -> float:
    if lab == "unrecognised":
        return 0.0 if f.best < other_below else c.gap * (f.best - other_below) + c.weak
    s = max(f.scores.get(x, 0.0) for x in FINE_OF[lab])
    best = max(f.best, f.scores["photo"])
    # weak evidence counts against a label only when the page leans to another type
    weak = s < other_below and f.best_fine not in FINE_OF[lab]
    return c.gap * max(0.0, best - s) + (c.weak if weak else 0.0)


@dataclass
class Alignment:
    labels: List[str]
    misplaced: List[bool]        # page is in a run that came out of order


def align(feats, c: Costs = Costs()) -> Alignment:
    """Monotone alignment of the pages to the owner's section order (Viterbi).

    State = (label of this page, m = the furthest section reached in order, misplaced flag).
      * an in-order section k moves m forward to k (k > m), free; coming back to the section just
        left (after an optional document) costs `reenter`;
      * an optional document or an UNRECOGNISED page leaves m alone (`float_enter` / `unrec_enter`);
      * a section that is NOT where the fixed order puts it (k != m, earlier OR later) is allowed only
        as a MISPLACED run: m stays, cost `order` per run (+ `order_page` per page, so the shorter of
        two runs is the one blamed). Every misplaced run becomes an ORDER notice to the owner.
    Emission = how far the label's cue score is below the page's best (see _emit)."""
    feats = _views(feats)
    n = len(feats)
    if n == 0:
        return Alignment([], [])
    ob = _other_below()
    INF = float("inf")
    M = list(range(-1, len(SECTIONS)))
    states: List[Tuple[str, int, bool]] = []
    for l in LABELS:
        for m in M:
            if l in SEC_IDX:
                k = SEC_IDX[l]
                if k == m:
                    states.append((l, m, False))
                elif m >= -1:
                    states.append((l, m, True))
            else:
                states.append((l, m, False))
    S = len(states)
    lidx = {l: i for i, l in enumerate(LABELS)}
    emit = [[_emit(f, l, c, ob) for l in LABELS] for f in feats]
    by_m: Dict[int, List[int]] = collections.defaultdict(list)
    for j, (l, m, f) in enumerate(states):
        by_m[m].append(j)

    def ord_cost(l):
        return c.order_photo if l == "photo" else c.order

    def page_cost(t, j):
        l, m, f = states[j]
        return emit[t][lidx[l]] + (c.order_page if f else 0.0)

    cost = [INF] * S
    for j, (l, m, f) in enumerate(states):
        if l in SEC_IDX:
            if not f and SEC_IDX[l] == m:
                cost[j] = page_cost(0, j)                         # first page starts section m
            elif f and m == -1:
                cost[j] = page_cost(0, j) + ord_cost(l)           # first page already misplaced
        elif m == -1:
            cost[j] = page_cost(0, j) + (c.unrec_enter if l == "unrecognised" else c.float_enter)
    back: List[List[int]] = []
    for t in range(1, n):
        # best previous state per m (and its index)
        best_m: Dict[int, Tuple[float, int]] = {}
        for m in M:
            js = by_m[m]
            jb = min(js, key=lambda x: cost[x])
            best_m[m] = (cost[jb], jb)
        prefix: Dict[int, Tuple[float, int]] = {}          # min over pm < m
        run = (INF, -1)
        for m in M:
            prefix[m] = run
            if best_m[m][0] < run[0]:
                run = best_m[m]
        new = [INF] * S
        bk = [-1] * S
        for j, (l, m, f) in enumerate(states):
            cand = [(cost[j], j)]                                   # continue the same run
            if l in SEC_IDX and not f:
                v, i = prefix[m]                                     # next section in order
                cand.append((v, i))
                # back to the section just left: from any other state at m (free after a
                # misplaced run - that interruption is already paid for)
                for i in by_m[m]:
                    if i != j:
                        cand.append((cost[i] + (0.0 if states[i][2] else c.reenter), i))
            elif l in SEC_IDX and f:
                for i in by_m[m]:                                    # a misplaced run starts
                    if i != j:
                        cand.append((cost[i] + ord_cost(l), i))
            else:
                enter = c.unrec_enter if l == "unrecognised" else c.float_enter
                for i in by_m[m]:
                    if i != j:
                        cand.append((cost[i] + enter, i))
            v, i = min(cand, key=lambda x: x[0])
            if v < INF:
                new[j], bk[j] = v + page_cost(t, j), i
        back.append(bk)
        cost = new
    j = min(range(S), key=lambda x: cost[x])
    path = [j]
    for bk in reversed(back):
        j = bk[j]
        path.append(j)
    path.reverse()
    return Alignment([states[j][0] for j in path], [states[j][2] for j in path])


# ============================================================== 6. documents inside sections
@dataclass
class Doc:
    label: str                   # section / float label (or "death_cert", "unrecognised")
    start: int
    end: int                     # inclusive, 0-based
    misplaced: bool = False
    role: str = ""               # NID block: father / mother / student / extra
    role_how: str = ""           # name / name-partial / position / file-name
    sub: List[Tuple[int, int, str]] = field(default_factory=list)
    attached: List[int] = field(default_factory=list)   # trailing pages kept with income tax (A6 f)
    key: str = ""                # folder mode: the file-name key (ingest.classify_name), "" when unknown
    file: str = ""               # folder mode: the input file name


@dataclass
class SplitCfg:
    dob_same: float = 4.0
    dob_diff: float = 4.0
    num_same: float = 3.0
    num_diff: float = 1.5
    name_same: float = 2.0
    name_diff: float = 1.5
    alt_new: float = 1.5         # a translation after an original: the next person's translation
    alt_join: float = 1.0        # an original (or unreadable page) after a translation: its card
    long_person: float = 1.0     # the person already has 3+ pages
    # unrecognised pages at a document's edge: "attach" = ONE such page joins a neighbouring identity /
    # certificate document as an unread page of it (its Bangla original: QR, seal), never bank or income
    # tax (amounts). Revision 2: always "attach" (no CLI switch); "separate" is kept for measurement only.
    edge: str = "attach"


def _close(a: str, b: str) -> bool:
    return len(a) == len(b) and sum(x != y for x, y in zip(a, b)) <= 2


def _person_evidence(pf: _F, cur: List[_F], cfg: SplitCfg) -> float:
    """> 0: page `pf` starts a new person; < 0: it belongs to the current person `cur`."""
    if pf.best_fine == "death_cert" or (cur and cur[-1].best_fine == "death_cert"):
        if not (pf.best_fine == "death_cert" and cur and all(x.best_fine == "death_cert" for x in cur)):
            return 10.0
    e = 0.0
    cd = set().union(*(x.dobs for x in cur)) if cur else set()
    if pf.dobs and cd:
        e += -cfg.dob_same if pf.dobs & cd else cfg.dob_diff
    cn = set().union(*(x.numbers for x in cur)) if cur else set()
    if pf.numbers and cn:
        e += -cfg.num_same if any(_close(a, b) for a in pf.numbers for b in cn) else cfg.num_diff
    ch = [x.holder for x in cur if x.holder]
    if pf.holder and ch:
        jac = max(len(set(pf.holder) & set(h)) / len(set(pf.holder) | set(h)) for h in ch)
        if jac >= 0.5:
            e -= cfg.name_same
        elif jac == 0:
            e += cfg.name_diff
    has_orig = any(not x.translation for x in cur)
    if pf.translation and has_orig:
        e += cfg.alt_new
    elif not pf.translation and cur and cur[-1].translation:
        e -= cfg.alt_join
    if len(cur) >= 3:
        e += cfg.long_person
    return e


def split_persons(feats, a: int, b: int, cfg: SplitCfg = SplitCfg()) -> List[Tuple[int, int, bool]]:
    """Pages a..b of the NID block -> persons [(start, end, is_death_cert)], left to right."""
    feats = _views(feats)
    out: List[List] = []
    cur: List[_F] = []
    for i in range(a, b + 1):
        f = feats[i]
        if not cur or _person_evidence(f, cur, cfg) > 0:
            out.append([i, i])
            cur = [f]
        else:
            out[-1][1] = i
            cur.append(f)
    # no more than 3 NID persons (+ death certificates): merge the weakest boundaries
    while sum(1 for s, e in out if feats[s].best_fine != "death_cert") > 3:
        best_k, best_e = None, None
        for k in range(1, len(out)):
            if feats[out[k][0]].best_fine == "death_cert" or feats[out[k - 1][0]].best_fine == "death_cert":
                continue
            ev = _person_evidence(feats[out[k][0]], list(feats[out[k - 1][0]:out[k - 1][1] + 1]), cfg)
            if best_e is None or ev < best_e:
                best_k, best_e = k, ev
        if best_k is None:
            break
        out[best_k - 1][1] = out[best_k][1]
        del out[best_k]
    return [(s, e, feats[s].best_fine == "death_cert") for s, e in out]


def split_academic(feats, a: int, b: int) -> List[Tuple[int, int, str]]:
    """Academic section -> one set per level (newest first expected): (start, end, level name or "?").
    A new set starts when the level changes, or when a certificate comes again after a transcript in a
    set that already has its certificate (two documents of one level). Revision 2: a transcript before
    its certificate stays in its level (an ORDER notice says so); an e-Apostille page joins the level
    its text names when that is the set before or after it, else the level that follows it."""
    feats = _views(feats)
    sets: List[List] = []               # [start, end, level name, has certificate]
    lvl: Optional[int] = None
    seen_transcript = False
    pending: List[int] = []             # e-Apostille pages waiting for the set they belong to

    def place(nxt: Optional[List]) -> None:
        prv = sets[-2] if nxt is not None and len(sets) >= 2 else (sets[-1] if nxt is None and sets else None)
        to_next = False
        for i in pending:
            lv = feats[i].level[0] if feats[i].level else None
            if not to_next:
                if nxt is None:
                    to_next = False
                elif prv is None:
                    to_next = True
                elif lv is not None and lv == prv[2] and lv != nxt[2]:
                    to_next = False
                else:
                    to_next = True
            if to_next:
                nxt[0] = min(nxt[0], i)
                if nxt[2] == "?" and lv:
                    nxt[2] = lv
            else:
                prv[1] = max(prv[1], i)
                if prv[2] == "?" and lv:
                    prv[2] = lv
        pending.clear()

    for i in range(a, b + 1):
        f = feats[i]
        if f.kind == "apostille":
            pending.append(i)
            continue
        new = not sets
        if f.level and lvl is not None and f.level[1] != lvl:
            new = True
        if (f.kind == "certificate" and seen_transcript and sets and sets[-1][3]
                and (f.level is None or f.level[1] == lvl)):
            new = True
        if new:
            sets.append([i, i, f.level[0] if f.level else "?", f.kind == "certificate"])
            seen_transcript = False
            lvl = f.level[1] if f.level else lvl
            if pending:
                place(sets[-1])
                if sets[-1][2] in PT.LEVEL_RANK:
                    lvl = PT.LEVEL_RANK[sets[-1][2]]
        else:
            if pending:                  # apostilles in the middle of one set stay in it
                pending.clear()
            sets[-1][1] = i
            if sets[-1][2] == "?" and f.level:
                sets[-1][2] = f.level[0]
            if f.level:
                lvl = f.level[1]
            if f.kind == "certificate":
                sets[-1][3] = True
        if f.kind == "transcript":
            seen_transcript = True
    if pending:
        if sets:
            place(None)
        else:                            # only apostille pages
            lv = next((feats[i].level[0] for i in pending if feats[i].level), "?")
            sets.append([pending[0], pending[-1], lv, False])
            pending.clear()
    return [(s, e, lv) for s, e, lv, _ in sets]


def split_financial(feats, a: int, b: int) -> List[Tuple[int, int, str]]:
    """Trade licence / TIN / employment sub-documents (unreadable pages join the one before)."""
    feats = _views(feats)
    ob = _other_below()
    out: List[List] = []
    for i in range(a, b + 1):
        f = feats[i]
        t = f.best_fine if f.best_fine in FINE_OF["financial"] and f.best >= ob else None
        if not out or (t and t != out[-1][2] and out[-1][2] is not None):
            out.append([i, i, t])
        else:
            out[-1][1] = i
            if out[-1][2] is None:
                out[-1][2] = t
    return [(s, e, t or "financial?") for s, e, t in out]


def documents(feats, al: Alignment, cfg: SplitCfg = SplitCfg()) -> List[Doc]:
    """The alignment's runs -> documents, in page order."""
    feats = _views(feats)
    runs: List[List] = []
    for i, (l, mis) in enumerate(zip(al.labels, al.misplaced)):
        if runs and runs[-1][0] == l and runs[-1][3] == mis:
            runs[-1][2] = i
        else:
            runs.append([l, i, i, mis])
    if cfg.edge == "attach":
        fwd = {"academic", "financial"}
        back = {"nid", "birth_cert", "family_cert"}
        k = 0
        while k < len(runs):
            l, a, b, mis = runs[k]
            if l == "unrecognised" and a == b:
                nxt = runs[k + 1] if k + 1 < len(runs) else None
                prv = runs[k - 1] if k > 0 else None
                if nxt and nxt[0] in fwd and not nxt[3]:
                    nxt[1] = a
                    del runs[k]
                    continue
                if prv and prv[0] in back and not prv[3]:
                    prv[2] = b
                    del runs[k]
                    continue
            k += 1
    attached: List[int] = []
    # rev 2 (A6 f): unrecognised page(s) at the very end, right after income tax, are part of it
    if (len(runs) >= 2 and runs[-1][0] == "unrecognised" and not runs[-1][3]
            and runs[-2][0] == "income_tax" and not runs[-2][3]):
        attached = list(range(runs[-1][1], runs[-1][2] + 1))
        runs[-2][2] = runs[-1][2]
        del runs[-1]
    docs: List[Doc] = []
    for l, a, b, mis in runs:
        if l == "nid":
            for s, e, death in split_persons(feats, a, b, cfg):
                docs.append(Doc("death_cert" if death else "nid", s, e, mis))
        elif l == "unrecognised":
            docs.append(Doc(l, a, b, mis))
        elif l == "academic":
            docs.append(Doc(l, a, b, mis, sub=split_academic(feats, a, b)))
        elif l == "financial":
            docs.append(Doc(l, a, b, mis, sub=split_financial(feats, a, b)))
        elif l == "income_tax" and attached and b == attached[-1]:
            docs.append(Doc(l, a, b, mis, attached=list(attached)))
        else:
            docs.append(Doc(l, a, b, mis))
    return docs


# ============================================================== 7. persons without a portal
@dataclass
class Refs:
    """Strict reference names for role scoring (identity.refs(): upper-case strict.words lists)."""
    father: List[str] = field(default_factory=list)
    mother: List[str] = field(default_factory=list)
    student: List[str] = field(default_factory=list)


def references(texts: Sequence[str], docs: Sequence[Doc]) -> Refs:
    """order_split.references() with strict words (measurement and the text-level self-tests; the runner
    takes its references from identity.refs()). Parents' names from the birth certificate, else the
    passport's father / mother fields; the student's name from the passport MRZ, else the birth
    certificate's Name."""
    def join(d):
        return "\n".join(texts[d.start:d.end + 1])
    birth = [join(d) for d in docs if d.label == "birth_cert"]
    pas = [join(d) for d in docs if d.label == "passport"]
    fa: List[str] = []
    mo: List[str] = []
    st: List[str] = []
    for t in birth:
        fa = fa or next(iter(PT.labelled(t, "father")), [])
        mo = mo or next(iter(PT.labelled(t, "mother")), [])
        st = st or next(iter(PT.labelled(t, "name")), [])
    for t in pas:
        fa = fa or next(iter(PT.labelled(t, "father")), [])
        mo = mo or next(iter(PT.labelled(t, "mother")), [])
        st = PT.mrz_name(t) or st
    return Refs(list(fa), list(mo), list(st))


def _strict(h: Sequence[str], r: Sequence[str], any_order: bool = False) -> str:
    """'yes': the same name letter for letter (strict.compare EQUAL, dots aside; any_order for the
    student, whose reference is the surname-first MRZ); 'part': at least half the words shared (a person
    must look: never an automatic PASS); 'no' otherwise."""
    if not h or not r:
        return "no"
    if strict.compare(h, r, any_order=any_order, dots=False) == "EQUAL":
        return "yes"
    H, Rr = set(strict.words(h)), set(strict.words(r))
    if not H or not Rr:
        return "no"
    return "part" if len(H & Rr) / len(H | Rr) >= 0.5 else "no"


ORDER_ROLES = ["father", "mother", "student"]


def _doc_holders(d: Doc, feats: Sequence[_F], texts: Sequence[str]) -> List[List[str]]:
    holders = [feats[i].holder for i in range(d.start, d.end + 1) if feats[i].holder]
    if d.label == "death_cert":
        holders += PT.labelled("\n".join(texts[d.start:d.end + 1]), "name")
    return holders


def _evidence(d: Doc, feats: Sequence[_F], texts: Sequence[str], refs: Refs) -> Dict[str, str]:
    ref = {"father": refs.father, "mother": refs.mother, "student": refs.student}
    holders = _doc_holders(d, feats, texts)
    e = {}
    for role in ORDER_ROLES:
        vals = [_strict(h, ref[role], any_order=role == "student") for h in holders] if ref[role] else []
        e[role] = ("yes" if "yes" in vals else "part" if "part" in vals else "no" if vals else "none")
    return e


_ROLE_KEY = {"father": 0, "mother": 1, "student": 2, "extra": 3}


def _role_assignments(block: Sequence[Doc]) -> List[List[str]]:
    """Every way of giving the block's documents the roles father / mother / student (each at most once; a
    death certificate never the student) and 'extra' to the rest, in the order the revision-1 search over
    permutations met them (lexicographic, father < mother < student < extra), so ties resolve the same way.
    (k + 1)^3 ways at most instead of (k + 3)! / 3! permutations: a long NID block no longer hangs."""
    k = len(block)
    out: List[List[str]] = []
    choices = [None] + list(range(k))
    for f in choices:
        for m in choices:
            if m is not None and m == f:
                continue
            for st in choices:
                if st is not None and (st == f or st == m or block[st].label == "death_cert"):
                    continue
                roles = ["extra"] * k
                for idx, r in ((f, "father"), (m, "mother"), (st, "student")):
                    if idx is not None:
                        roles[idx] = r
                out.append(roles)
    out.sort(key=lambda rs: [_ROLE_KEY[r] for r in rs])
    return out


def assign_persons(docs: List[Doc], feats, texts: Sequence[str], refs: Refs,
                   place_weight: float = 1.5) -> List[str]:
    """Roles for the NID-block documents (in place), no portal.

    Every way of giving the units the roles father / mother / student (death certificates: father or
    mother only; a unit may stay 'extra') is scored: +1.5 when a unit sits in its role's place in the
    fixed order (father, mother, student), +5 when its holder name is STRICTLY equal to that role's
    reference name (strict.compare, dots aside), +2 when they share at least half their words (a person
    must look), -1 when both names are read and share nothing. The best assignment wins. role_how:
    'name' (strict), 'name-partial' or 'position'. Returns the "not in the order" message, if any."""
    feats = _views(feats)
    notes: List[str] = []
    block = [d for d in docs if d.label in ("nid", "death_cert") and not d.misplaced]
    ev = [_evidence(d, feats, texts, refs) for d in block]
    best, best_sc = None, None
    for roles in _role_assignments(block):
        sc = 0.0
        for pos, (d, r, e) in enumerate(zip(block, roles, ev)):
            if r == "extra":
                sc -= 2.0
                continue
            if pos < len(ORDER_ROLES) and ORDER_ROLES[pos] == r:
                sc += place_weight
            sc += {"yes": 5.0, "part": 2.0, "no": -1.0, "none": 0.0}[e[r]]
        if best_sc is None or sc > best_sc + 1e-9:
            best, best_sc = roles, sc
    for d, r, e in zip(block, best or [], ev):
        d.role = r
        d.role_how = ("name" if r != "extra" and e[r] == "yes" else
                      "name-partial" if r != "extra" and e[r] == "part" else "position")
    seq = [ORDER_ROLES.index(d.role) for d in block if d.role in ORDER_ROLES]
    if seq != sorted(seq):
        pages = ", ".join(f"{d.start + 1 if d.start == d.end else f'{d.start + 1}-{d.end + 1}'} {d.role}"
                          for d in block if d.role in ORDER_ROLES)
        notes.append(f"the NIDs are not in the order father, mother, student (the names say: {pages}) "
                     f"— put them in that order")
    return notes


def _role_by_name(d: Doc, feats: Sequence[_F], texts: Sequence[str], refs: Refs) -> Tuple[str, str]:
    """The person of one NID / death certificate by its names alone (no place): (role, role_how)."""
    e = _evidence(d, feats, texts, refs)
    roles = ["father", "mother"] if d.label == "death_cert" else ORDER_ROLES
    yes = [r for r in roles if e[r] == "yes"]
    part = [r for r in roles if e[r] == "part"]
    if len(yes) == 1:
        return yes[0], "name"
    if not yes and len(part) == 1:
        return part[0], "name-partial"
    return "", ""


# ============================================================== 8. segment / provisional / finish
@dataclass
class Segmentation:
    mode: str                   # "bundle" | "folder"
    docs: List[Doc]
    feats: List[PageType]


def segment(types: List[PageType], program: str, cfg: SplitCfg = SplitCfg(), costs: Costs = Costs()) -> Segmentation:
    """A bundle's pages -> documents (alignment to the fixed order, then the documents inside each
    section). Roles, notices and missing slots come in finish()."""
    al = align(types, costs)
    return Segmentation("bundle", documents(types, al, cfg), list(types))


def _majority_label(feats: Sequence[PageType], a: int, b: int) -> Optional[str]:
    """The alignment label the majority of pages a..b read as (thresholded best type; ties: the earliest page)."""
    labs = [FINE_LABEL.get(feats[i].best) for i in range(a, b + 1)]
    votes = collections.Counter(x for x in labs if x)
    if not votes:
        return None
    top = max(votes.values())
    return next(x for x in labs if x and votes[x] == top)


def _file_fine(name: str) -> Optional[str]:
    """Folder mode: the financial fine type a file name gives (TIN / employment / trade licence)."""
    low = name.lower().replace("_", " ")
    if any(w in low for w in ("tin certificate", "e-tin", "etin")) or re.search(r"\btin\b", low):
        return "tin_certificate"
    if any(w in low for w in ("employment", "salary certificate", "experience certificate")):
        return "employment_certificate"
    if "trade licen" in low:
        return "trade_license"
    return None


def segment_folder(src: Source, pages: List[PageRead], types: List[PageType]) -> Segmentation:
    """One Doc per input file (pages in run order). The label is the section / float of the file's
    name key (ingest.classify_name), or of its majority page type when the name gives none. A file
    holding several fine types or levels keeps one Doc with `sub` = its parts."""
    docs: List[Doc] = []
    for sf in src.files:
        idx = [p.index for p in pages if p.file == sf.name]
        if not idx:
            continue
        a, b = min(idx), max(idx)
        key = sf.key_by_name or ""
        guess = _majority_label(types, a, b)
        if not key and guess == "photo":       # a file with little text is not the photo unless its name says so
            guess = None
        label = KEY_LABEL.get(key) or guess or "unrecognised"
        d = Doc(label, a, b, key=key, file=sf.name)
        if label == "academic":
            d.sub = split_academic(types, a, b)
        elif label == "financial":
            d.sub = split_financial(types, a, b)
        docs.append(d)
    return Segmentation("folder", docs, list(types))


def _resolve_fine(feats: Sequence[_F], s: int, e: int, t: str) -> str:
    """A financial part's fine type; an unconfident part ("financial?") takes the fine type with the
    highest cue total over its pages (trade licence on a tie)."""
    if t in FINANCIAL_TITLE:
        return t
    tot = {k: sum(feats[i].scores.get(k, 0.0) for i in range(s, e + 1)) for k in FINE_OF["financial"]}
    return max(FINE_OF["financial"], key=lambda k: tot[k])


def _units(seg: Segmentation, docs: Sequence[Doc], with_roles: bool) -> List[Tuple[DocUnit, Doc]]:
    """Doc -> DocUnit mapping (RUNNER_SPEC 5.4), uids in page order. Without roles (provisional), every
    NID is 'nid_unassigned' and every death certificate 'death_cert'."""
    feats = _views(seg.feats)
    out: List[Tuple[DocUnit, Doc]] = []

    def add(key, fine, title, a, b, d, level="", sub=None):
        role = d.role if with_roles and d.role in ORDER_ROLES else ""
        how = d.role_how if role else ""
        u = DocUnit(uid="", key=key, fine=fine, title=title, pages=list(range(a, b + 1)), role=role,
                    role_how=how, misplaced=d.misplaced, level=level, sub=list(sub or []),
                    source_file=d.file)
        out.append((u, d))

    for d in docs:
        if d.label in ("nid", "death_cert"):
            role = d.role if with_roles else ""
            if d.label == "nid":
                key = ROLE_KEY.get(("nid", role), "nid_unassigned")
            else:
                key = ROLE_KEY.get(("death_cert", role), "death_cert")
            add(key, d.label, TITLE[key], d.start, d.end, d)
        elif d.label == "academic":
            subs = d.sub or [(d.start, d.end, "?")]
            if seg.mode == "bundle":
                for s, e, lv in subs:
                    lv = "" if lv == "?" else lv
                    add("academic", "academic", f"ACADEMIC {lv.upper()}".strip(), s, e, d, level=lv)
            else:
                lvs = []
                for _, _, lv in subs:
                    if lv != "?" and lv not in lvs:
                        lvs.append(lv)
                title = ("ACADEMIC " + lvs[0].upper() if len(lvs) == 1 else
                         "ACADEMIC (" + ", ".join(x.upper() for x in lvs) + ")" if lvs else "ACADEMIC")
                add("academic", "academic", title, d.start, d.end, d, level=lvs[0] if len(lvs) == 1 else "",
                    sub=[(s, e, "" if lv == "?" else lv) for s, e, lv in subs] if len(subs) > 1 else [])
        elif d.label == "financial":
            subs = d.sub or [(d.start, d.end, "financial?")]
            if seg.mode == "bundle":
                for s, e, t in subs:
                    fine = _resolve_fine(feats, s, e, t)
                    add("trade_license", fine, FINANCIAL_TITLE[fine], s, e, d)
            else:
                parts = [(s, e, _resolve_fine(feats, s, e, t)) for s, e, t in subs]
                fine = _file_fine(d.file) or parts[0][2]
                others = []
                for _, _, t in parts:
                    if t != fine and FINANCIAL_TITLE[t] not in others:
                        others.append(FINANCIAL_TITLE[t])
                title = FINANCIAL_TITLE[fine] + (f" (+ {', '.join(others)})" if others else "")
                add("trade_license", fine, title, d.start, d.end, d, sub=parts if len(parts) > 1 else [])
        elif d.label == "unrecognised":
            add("unrecognised", "unrecognised", TITLE["unrecognised"], d.start, d.end, d)
        else:
            add(d.label, d.label, TITLE[d.label], d.start, d.end, d)
    out.sort(key=lambda ud: ud[0].pages[0])
    for n, (u, _) in enumerate(out, 1):
        u.uid = f"D{n:02d}"
    return out


def provisional_units(seg: Segmentation, pages: List[PageRead]) -> List[DocUnit]:
    """DocUnits without roles (NID-block units keyed 'nid_unassigned', death certificates 'death_cert').
    Used by birth and identity before the persons are known. The unit pages (and uids) equal the final
    units' pages for every unit: finish() maps the same documents the same way."""
    return [u for u, _ in _units(seg, [replace(d, role="", role_how="") for d in seg.docs], with_roles=False)]


def _p1(a: int, b: int) -> Tuple[int, int]:
    return a + 1, b + 1


def _range(a: int, b: int) -> str:
    """"page N" / "pages N-M" of run pages a..b, numbered as the reader sees them (page of the file in folder
    mode)."""
    pa, pb = page_no(a), page_no(b)
    return f"page {pa}" if pa == pb else f"pages {pa}-{pb}"


def _touching(units: Sequence[DocUnit], a: int, b: int) -> Tuple[str, ...]:
    return tuple(u.uid for u in units if any(a <= i <= b for i in u.pages))


def _finding(verdict: str, rule: str, message: str, pages: Sequence[int], **ev) -> Finding:
    return Finding(verdict=verdict, rule=rule, message=message, source=SOURCE,
                   pages=tuple(page_no(i) for i in pages), evidence=dict(ev))


def _unit_notes(u: DocUnit, d: Doc) -> None:
    """Split-level findings on a unit (rev 2, change 8; folder change 7)."""
    who = {"father": "the father's", "mother": "the mother's", "student": "the student's"}.get(u.role, "")
    what = "death certificate" if "death" in u.key else "NID"
    a, b = u.pages[0], u.pages[-1]
    if u.role and u.role_how == "position":
        u.notes.append(_finding(FLAG, "split.role_position",
                                f"taken as {who} {what} from its place in the order (father, mother, student) "
                                f"only — the names could not confirm it; check by eye ({_range(a, b)})", u.pages))
    elif u.role and u.role_how == "name-partial":
        u.notes.append(_finding(FLAG, "split.role_partial",
                                f"taken as {who} {what}, but its name only partly matches — check by eye whose "
                                f"it is ({_range(a, b)})", u.pages))
    if u.key == "nid_unassigned":
        u.notes.append(_finding(FLAG, "split.nid_unassigned",
                                f"an NID whose person could not be told ({_range(a, b)}) — check by eye", u.pages))
    if u.key == "unrecognised":
        u.notes.append(_finding(FLAG, "split.unrecognised",
                                f"{_range(a, b)} not recognised as any document — check by eye", u.pages))
    if d.attached and u.key == "income_tax":
        x, y = d.attached[0], d.attached[-1]
        u.notes.append(_finding(NOTE, "split.attached_trailing",
                                f"{_range(x, y)} (not recognised) kept with INCOME TAX as its last "
                                f"{'page' if x == y else 'pages'}", d.attached))


def _fines(u: DocUnit) -> Set[str]:
    return {u.fine} | {t for _, _, t in u.sub if t}


def _missing(units: Sequence[DocUnit], program: str) -> Tuple[List[str], Dict[str, str]]:
    have: Set[str] = set()
    for u in units:
        for slot, keys in SLOT_ACCEPTS.items():
            if slot in ("father_id", "mother_id") and u.key in keys:
                have.add(slot)
        if u.key == "trade_license":
            f = _fines(u)
            if f & set(SLOT_ACCEPTS["owner_doc"]):
                have.add("owner_doc")
            if f & set(SLOT_ACCEPTS["tin"]):
                have.add("tin")
        elif u.key in SLOTS or u.key == "income_tax":
            have.add(u.key)
    maybe: Dict[str, str] = {}
    deaths = [u.uid for u in units if u.key == "death_cert"]
    open_ = [slot for slot in ("father_id", "mother_id") if slot not in have]
    for slot in open_:
        if deaths:
            maybe[slot] = deaths[0]
    missing = [s for s in slots_for(program) if s not in have and s not in maybe]
    if deaths and len(deaths) < len(open_):     # one death certificate cannot stand for both parents
        missing.append("parent_id")
    return missing, maybe


def _hints(missing: Sequence[str], feats: Sequence[_F], types: Sequence[PageType],
           texts: Sequence[str], units: Sequence[DocUnit]) -> List[Notice]:
    """'X MISSING in its place; page N reads partly like it' (bundle mode; never ORDER, never STOP)."""
    out: List[Notice] = []
    P = PT.proto()
    for slot in missing:
        if slot not in SLOT_SECTION:
            continue
        if slot == "photo":
            cands = [i for i in range(len(feats)) if len((texts[i] or "").strip()) < P.EMPTY_CHARS
                     or types[i].best == "photo"]
        else:
            fine = SLOT_FINE.get(slot, FINE_OF[SLOT_SECTION[slot]])
            # a page that reads best as the missing type, wherever it was put (absorbed into a neighbour,
            # read as an optional document, or misplaced)
            cands = [i for i in range(len(feats)) if feats[i].best_fine in fine and feats[i].best >= HINT_MIN]
        if cands:
            i = cands[0]
            out.append(Notice("HINT", _p1(i, i),
                              f"{MISSING_TEXT[slot]} in its place; page {i + 1} "
                              f"{'is blank' if slot == 'photo' else 'reads partly like it'} — if it is the "
                              f"{NICE[slot]}, move it to its place in the fixed order",
                              units=_touching(units, i, i)))
    return out


def _within_notices(pairs: Sequence[Tuple[DocUnit, Doc]], feats: Sequence[_F]) -> List[Notice]:
    """Order breaks inside a document or block: non-blocking ORDER notices (critique 7)."""
    out: List[Notice] = []
    units = [u for u, _ in pairs]
    for u, d in pairs:
        a, b = u.pages[0], u.pages[-1]
        if d.label == "nid" and not feats[a].translation and any(feats[i].translation for i in u.pages[1:]):
            out.append(Notice("ORDER", _p1(a, b), f"{_range(a, b)}: the NID translation is behind the original — "
                                                  f"put the translation first", False, (u.uid,)))
        if u.key == "academic":
            kinds = [feats[i].page_kind for i in u.pages]
            if "transcript" in kinds and "certificate" in kinds and kinds.index("transcript") < kinds.index("certificate"):
                lv = f"{u.level.upper()} " if u.level else ""
                out.append(Notice("ORDER", _p1(a, b), f"{_range(a, b)}: the {lv}transcript comes before its "
                                                      f"certificate — put the certificate first", False, (u.uid,)))
        if u.key == "bank":
            kinds = [feats[i].page_kind for i in u.pages]
            if "solvency" in kinds and "statement" in kinds and kinds.index("statement") < kinds.index("solvency"):
                out.append(Notice("ORDER", _p1(a, b), f"{_range(a, b)}: a bank statement page comes before the "
                                                      f"solvency certificate — put the solvency certificate first",
                                  False, (u.uid,)))
    by_doc: Dict[int, List[DocUnit]] = {}
    for u, d in pairs:                               # per academic document (one block of levels)
        if u.key == "academic":
            by_doc.setdefault(id(d), []).append(u)
    for acad in by_doc.values():
        ranks = [PT.LEVEL_RANK[u.level] for u in acad if u.level in PT.LEVEL_RANK]
        if ranks != sorted(ranks, reverse=True):
            a, b = acad[0].pages[0], acad[-1].pages[-1]
            out.append(Notice("ORDER", _p1(a, b), f"{_range(a, b)}: the academic papers are not newest first "
                                                  f"(degree, diploma, HSC, SSC) — put the newest level first",
                              False, tuple(u.uid for u in acad)))
    out.sort(key=lambda x: x.pages)
    return out


def finish(seg: Segmentation, types: List[PageType], pages: List[PageRead], refs: Refs, program: str) -> SplitResult:
    """Persons (strict names against `refs`), notices, STOP, missing slots and the final DocUnits."""
    if seg.mode == "folder":
        return _finish_folder(seg, types, pages, refs, program)
    feats = _views(types)
    texts = [p.text for p in pages]
    n = len(texts)
    docs = [replace(d, role="", role_how="") for d in seg.docs]
    order_msgs = assign_persons(docs, feats, texts, refs)
    for d in docs:                                   # misplaced NID / death certificate: names only
        if d.misplaced and d.label in ("nid", "death_cert"):
            d.role, d.role_how = _role_by_name(d, feats, texts, refs)
    pairs = _units(Segmentation(seg.mode, docs, list(types)), docs, with_roles=True)
    units = [u for u, _ in pairs]
    for u, d in pairs:
        _unit_notes(u, d)
    notices: List[Notice] = []
    stop_pages: List[Tuple[int, int]] = []
    blk = [d for d in docs if d.label in ("nid", "death_cert") and not d.misplaced]
    for msg in order_msgs:
        a, b = blk[0].start, blk[-1].end
        notices.append(Notice("ORDER", _p1(a, b), msg, False, _touching(units, a, b)))
        if any(d.role in ORDER_ROLES and d.role_how == "position" for d in blk):
            stop_pages.append((a, b))
    # misplaced runs: name the pages and where they belong
    inorder = [d for d in docs if _sec(d) in SEC_IDX and not d.misplaced]
    for d in docs:
        if not d.misplaced:
            continue
        k = SEC_IDX[_sec(d)]
        after = [x for x in inorder if SEC_IDX[_sec(x)] < k]
        want = (f"after {NICE[_sec(after[-1])]} ({_range(after[-1].start, after[-1].end)})"
                if after else "at the start")
        prev = [x for x in inorder if x.end < d.start]
        msg = (f"{_range(d.start, d.end)} {'looks' if d.start == d.end else 'look'} like {NICE[d.label]} but "
               f"{'comes' if d.start == d.end else 'come'} after "
               f"{NICE[_sec(prev[-1])] if prev else 'the start'} — in the fixed order "
               f"{'it goes' if d.start == d.end else 'they go'} {want}")
        blocks = True
        if d.label in ("nid", "death_cert"):
            if not d.role:
                stop_pages.append((d.start, d.end))
            elif d.label == "death_cert":
                blocks = False                       # its person is settled by name: judged where it is
                msg += f" (the {d.role}'s, by name)"
        notices.append(Notice("ORDER", _p1(d.start, d.end), msg, blocks, _touching(units, d.start, d.end)))
    # unrecognised runs in the middle of the bundle
    for u, d in pairs:
        if u.key == "unrecognised" and not d.misplaced and u.pages[0] > 0 and u.pages[-1] < n - 1:
            a, b = u.pages[0], u.pages[-1]
            notices.append(Notice("ORDER", _p1(a, b),
                                  f"{_range(a, b)} not recognised as any document of the fixed order — if "
                                  f"{'it belongs' if a == b else 'they belong'} to the document before or after, "
                                  f"keep {'it' if a == b else 'them'} with it; else take "
                                  f"{'it' if a == b else 'them'} out", True, (u.uid,)))
    notices += _within_notices(pairs, feats)
    missing, maybe = _missing(units, program)
    notices += _hints(missing, feats, types, texts, units)
    notices.sort(key=lambda x: (x.pages[0], x.pages[1]))
    stop = bool(stop_pages)
    if stop:
        where = ", ".join(f"{a + 1}-{b + 1}" if a != b else f"{a + 1}" for a, b in stop_pages)
        one = len(stop_pages) == 1 and stop_pages[0][0] == stop_pages[0][1]
        notices.append(Notice("STOP", (1, max(1, n)),
                              f"whose NID or death certificate is on {'page' if one else 'pages'} {where} cannot "
                              f"be told: it is out of its "
                              f"place and the names do not settle it — put the NIDs in the order father, mother, "
                              f"student (each translation in front of its card), a parent's death certificate in "
                              f"that parent's place, and check again"))
    return SplitResult(units=units, notices=notices, missing=missing, stop=stop, maybe=maybe)


def _sec(d: Doc) -> str:
    return "nid" if d.label == "death_cert" else d.label


def _finish_folder(seg: Segmentation, types: List[PageType], pages: List[PageRead], refs: Refs,
                   program: str) -> SplitResult:
    """Folder mode: roles from the file name (role_how 'file-name'); a generic death certificate or an
    NID file whose name gives no person gets its role by strict name alone (no place); no ORDER, HINT or
    STOP; FLAG split.name_vs_content when the pages read as another document than the name says."""
    feats = _views(types)
    texts = [p.text for p in pages]
    docs = [replace(d, role="", role_how="") for d in seg.docs]
    for d in docs:
        if d.label not in ("nid", "death_cert"):
            continue
        if d.key in KEY_ROLE:
            d.role, d.role_how = KEY_ROLE[d.key], "file-name"
        else:
            d.role, d.role_how = _role_by_name(d, feats, texts, refs)
    pairs = _units(Segmentation(seg.mode, docs, list(types)), docs, with_roles=True)
    units = [u for u, _ in pairs]
    for u, d in pairs:
        _unit_notes(u, d)
        if d.key:
            got = _majority_label(types, d.start, d.end)
            want = KEY_LABEL.get(d.key)
            if got and want and got != want:
                u.notes.append(_finding(FLAG, "split.name_vs_content",
                                        f"the file name says {NICE[want]} but its pages read like {NICE[got]} — "
                                        f"check by eye", u.pages, name_label=want, content_label=got))
    missing, maybe = _missing(units, program)
    return SplitResult(units=units, notices=[], missing=missing, stop=False, maybe=maybe)


# ============================================================== 9. cutting
def unit_file_name(u: DocUnit) -> str:
    """'<FILE TITLE> pNN-MM.pdf' (1-based pages; one page 'pNN'; the photo '.jpg'). dv.classify of the
    name gives u.key back for every bot key (tested)."""
    if u.key == "academic":
        title = f"{FILE_TITLE['academic']} {u.level.upper()}".strip()
    elif u.key == "trade_license":
        title = FINANCIAL_TITLE.get(u.fine, FILE_TITLE["trade_license"])
    else:
        title = FILE_TITLE[u.key]
    a, b = u.pages[0] + 1, u.pages[-1] + 1
    pp = f"p{a:02d}" if a == b else f"p{a:02d}-{b:02d}"
    return f"{title} {pp}{'.jpg' if u.key == 'photo' else '.pdf'}"


def cut_plan(res: SplitResult) -> List[Tuple[str, int, int]]:
    """(unit_file_name(u), first, last) for every unit, pages 0-based inclusive (engine.cut)."""
    return [(unit_file_name(u), u.pages[0], u.pages[-1]) for u in res.units]


def attach_paths(res: SplitResult, paths: Union[List[Path], Dict[str, Path]]) -> None:
    """Sets DocUnit.path: a list in cut_plan order (bundle cuts), or a dict file name -> staged copy
    (folder mode, by the unit's source file)."""
    if isinstance(paths, dict):
        for u in res.units:
            if u.source_file in paths:
                u.path = Path(paths[u.source_file])
        return
    paths = list(paths)
    if len(paths) != len(res.units):
        raise ValueError(f"{len(paths)} cut files for {len(res.units)} units")
    for u, p in zip(res.units, paths):
        u.path = Path(p)
