"""Per-page features for the order-aware split (RUNNER_SPEC section 5.3, revision 2; Task 2).

A port of the lab's order_split.py lines 99-238 (the page features). The page's cue scores come from the
prototype recogniser (`prototype_page_types.score_page`, `OTHER_BELOW`, `EMPTY_CHARS`), reached through
`botimport.load().P` so the bot code is always the worktree's, with the stub config.

Changes from the lab (the only ones):
  * the name helpers (`_name_after`, `labelled`, `mrz_name`) use `strict.words` instead of the bot's
    `name_tokens` (which drops initials, two-letter words and honorifics): role scoring is strict (S8);
  * a page with no text layer whose one embedded image covers the page (`image_only`) and that reads
    fewer than 150 characters is the photo (score 5.0), so a scanned photo page with OCR specks is found;
  * bank pages get a kind: "solvency" (SOLVENCY_CUE) or "statement" (STATEMENT_CUE);
  * the result is a `model.PageType`: `best` is the best fine type, or "other" when its score is under
    the prototype's OTHER_BELOW bar (as `predict_page` says it); `score` is that best score; `level` is
    the level's name; `nid_numbers` and `birth_dates` are sorted lists (dates as ISO text);
  * `kind` is the academic kind (apostille / certificate / transcript) only on pages that show some
    academic sign (an academic cue, a level, grades, or certificate wording on a page no type claims),
    the bank kind on bank pages, and "" on every other page. Inside the academic block the split reads a
    page without a kind as the lab did ("transcript", pagetype.academic_kind).

No personal data: the module holds cue words only.
"""
from __future__ import annotations

import re
from typing import Any, Callable, Dict, List, Optional, Sequence, Set, Tuple

from . import botimport, strict
from .model import PageRead, PageType

# ------------------------------------------------------------------------- the prototype recogniser


def _bot():
    """The bot code (botimport.load()); the root already loaded wins, so the runner's cache root is kept."""
    return botimport._loaded if botimport._loaded is not None else botimport.load()


def proto():
    """`src.verify.prototype_page_types` from the worktree (score_page, OTHER_BELOW, EMPTY_CHARS ...)."""
    return _bot().P


# prototype fine type -> bot key (prototype_page_types.COARSE, with "other" -> "unrecognised").
# "nid" and "death_cert" need a role: split.finish turns them into father_nid / mother_death_cert ...
FINE_TO_KEY: Dict[str, str] = {
    "passport": "passport", "photo": "photo",
    "nid": "nid", "nid_translation": "nid",
    "birth_cert": "birth_cert", "death_cert": "death_cert", "family_cert": "family_cert",
    "academic_apostille": "academic", "academic_certificate": "academic", "academic_transcript": "academic",
    "bank_solvency": "bank", "bank_statement": "bank",
    "trade_license": "trade_license", "tin_certificate": "trade_license",
    "employment_certificate": "trade_license",
    "income_tax": "income_tax", "language_cert": "language_cert", "affidavit": "affidavit",
    "personal_statement": "personal_statement", "recommendation": "recommendation", "eca": "eca",
    "id_card_cv": "id_card_cv", "other": "unrecognised",
}

ACADEMIC_FINE = ("academic_apostille", "academic_certificate", "academic_transcript")
BANK_FINE = ("bank_solvency", "bank_statement")
ACADEMIC_KINDS = ("apostille", "certificate", "transcript")
BANK_KINDS = ("solvency", "statement")
PHOTO_IMAGE_CHARS = 150          # image_only page reading fewer characters than this: the photo (5.3)
SOLVENCY_CUE = re.compile(r"solvency|to\s+whom\s+it\s+may\s+concern|this\s+is\s+to\s+certify", re.I)
STATEMENT_CUE = re.compile(r"statement\s+of\s+account|opening\s+balance|closing\s+balance|withdraw|deposit",
                           re.I)

# ------------------------------------------------------------------------- order_split L95-121 (ported)
# Translation cue, letterhead / typed-page only. A bare "translation" also sits on the translation
# centre's stamp that is pressed on the ORIGINAL card copy, and notary / NID-back words fire on every
# page of a notarised bundle. "Translated" / "true copy" is the letterhead's own wording; a page that
# says "translat..." AND is mostly clean English words is a typed translation, not a card scan.
_LETTERHEAD = re.compile(r"translated|true\s*copy")
_TRANSLAT = re.compile(r"translat")
ENGLISH_TYPED = 0.45
_NID_NO = re.compile(r"(?:\bn?id|national\s+id(?:\s+card)?)\s*(?:no|number|n0|#)?\s*[.:;\-]?\s*(\d(?:[ ]?\d){9,16})(?!\d)")
_MON = {m: i for i, m in enumerate(["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct",
                                     "nov", "dec"], 1)}
_DOB_WORD = re.compile(r"birth\W{0,4}(\d{1,2})\s*[\-./ ]?\s*([a-z]{3,9})\s*[\-./, ]?\s*(\d{4})")
_DOB_NUM = re.compile(r"birth\W{0,4}(\d{1,2})[\-./](\d{1,2})[\-./](\d{4})")
_GRADE = re.compile(r"(?<![a-z0-9])(?:a\+|a-|a|b|c|d|f)\s*[\s:]\s*\d\.\d{1,2}(?!\d)")
_CERT_WORDS = re.compile(r"certify that|duly passed|has passed|passed the|this is to certify")
_LEVELS = [("degree", 4, r"bachelor|master|degree|university"), ("diploma", 3, r"diploma|polytechnic"),
           ("HSC", 2, r"higher secondary|\bhsc\b|\balim\b"), ("SSC", 1, r"secondary school|\bssc\b|dakhil")]
LEVEL_RANK: Dict[str, int] = {name: rank for name, rank, _ in _LEVELS}
_NAME_STOP = {"father", "fathers", "mother", "mothers", "husband", "wife", "spouse", "date", "birth", "dob",
              "sex", "gender", "address", "present", "permanent", "nid", "id", "no", "number", "place",
              "nationality", "religion", "occupation", "blood", "signature", "son", "daughter", "of", "and",
              "the", "card", "holder", "registration", "issue", "relation", "relationship", "name", "names",
              "mobile", "village", "post", "district", "age", "late", "deceased", "cause", "in", "word", "is",
              "was", "passport", "bangladesh", "nationality", "personal", "legal", "guardian", "emergency",
              # boilerplate seen right after a name on the forms (photo box, nationality, OCR of Bangla)
              "picture", "photo", "photograph", "affixed", "whole", "bangladeshi", "jon", "fet", "seal",
              "signed", "sign", "copy", "true", "translated", "translation", "notary", "advocate"}


def _eng_ratio(t: str) -> float:
    toks = t.split()
    if not toks:
        return 0.0
    good = sum(1 for w in toks if re.fullmatch(r"[A-Za-z][a-z]{2,}[:.,;]?|[A-Z]{3,}[:.,;]?", w))
    return good / len(toks)


def nid_numbers(tl: str) -> Set[str]:
    """NID / birth-registration numbers after an ID label (10, 13 or 17 digits; a 17-digit number is
    kept as its last 13). `tl`: the page text lower-cased with whitespace runs as one space."""
    boiler = proto().R.BOILERPLATE_NUMBERS
    out = set()
    for m in _NID_NO.finditer(tl):
        d = m.group(1).replace(" ", "")
        if len(d) in (10, 13, 17) and d not in boiler:
            out.add(d[-13:] if len(d) == 17 else d)
    return out


def birth_dates(tl: str) -> Set[Tuple[int, int, int]]:
    """(year, month, day) read right after a birth label ("Birth: 01 Jan 2000", "birth 01/01/2000")."""
    out = set()
    for m in _DOB_WORD.finditer(tl):
        mo = _MON.get(m.group(2)[:3])
        if mo:
            out.add((int(m.group(3)), mo, int(m.group(1))))
    for m in _DOB_NUM.finditer(tl):
        out.add((int(m.group(3)), int(m.group(2)), int(m.group(1))))
    return out


def _iso(d: Tuple[int, int, int]) -> str:
    return f"{d[0]:04d}-{d[1]:02d}-{d[2]:02d}"


def _name_after(text: str, start: int) -> List[str]:
    kept = []
    for tok in text[start:start + 120].split()[:7]:
        # "_" too: OCR prints an initial's dot as "_" ("A_ SAMPLE"), as names.py reads it (integration, test 1)
        w = re.sub(r"[’']s$", "", tok.strip(".,:;'\"()[]’_"))
        if not w or not re.fullmatch(r"[A-Za-z][A-Za-z.'\-]*", w):
            break
        if w.lower().replace(".", "") in _NAME_STOP:
            break
        if len(w) == 1 and w.islower():                       # Bangla read as a stray letter
            break
        kept.append(w)
        if len(kept) == 5:
            break
    return list(strict.words(" ".join(kept)))


def labelled(text: str, label: str) -> List[List[str]]:
    """Name-word lists (strict.words, upper case) written after `label` ("name", "father", "mother").
    For "name" the label must not be someone else's name field (Father's Name, Mother's Name, Husband's
    Name)."""
    out = []
    low = (text or "").lower()
    for m in re.finditer(r"\b" + label + r"(?:'?s)?\b\s*(?:name)?\s*[:;.\-]?\s*", low):
        if label == "name":
            before = low[max(0, m.start() - 14):m.start()]
            # Kept as in the lab: "Father's Name" (with the apostrophe) is NOT skipped. On NID translations
            # OCR often reads the labels and the values as two columns ("Name: Father's Name: <holder>
            # <father> ..."), and the value after it is then the holder's own; skipping it lost one right
            # person on the held-out bundles (sim_split, 27 Oct).
            if re.search(r"(father|mother|husband|spouse|wife|guardian|fathers|mothers)\W*$", before):
                continue
        toks = _name_after(text, m.end())
        if toks:
            out.append(toks)
    return out


def mrz_name(text: str) -> List[str]:
    """The name words of a passport MRZ line 1 ("P<BGD<surname><<<given>..."), surname first."""
    m = re.search(r"p\s?<\s?bgd([a-z<]{6,})", (text or "").lower())
    if not m:
        return []
    return list(strict.words(" ".join(t for t in m.group(1).split("<") if t)))


# revision 2.3: the examination a school certificate or transcript names ("Dakhil Examination 2020") says its level
# better than any other word on it: a Dakhil (SSC) transcript printing its school's name "... Alim Madrasah" read as
# HSC, and a folder file's levels are now cut apart by level. Only HSC / SSC: a degree page that names an HSC exam
# stays a degree.
_EXAM_LEVELS = [("HSC", r"higher\s+secondary(?:\s+certificate)?\s+exam|\balim\s+exam|\bh\.?\s?s\.?\s?c\.?\s+exam"),
                ("SSC", r"secondary\s+school(?:\s+certificate)?\s+exam|\bdakhil\s+exam|\bs\.?\s?s\.?\s?c\.?\s+exam")]


def level_of(text: str) -> Optional[Tuple[str, int]]:
    """The academic level the text names first in _LEVELS order: (name, rank) or None. Revision 2.3: between HSC and
    SSC, the level of the first examination the text names wins ("Dakhil Examination" -> SSC)."""
    tl = re.sub(r"\s+", " ", (text or "").lower())
    for name, rank, rx in _LEVELS:
        if re.search(rx, tl):
            if name in ("HSC", "SSC"):
                hits = [(m.start(), lv) for lv, ex in _EXAM_LEVELS for m in [re.search(ex, tl)] if m]
                if hits:
                    lv = min(hits)[1]
                    return lv, LEVEL_RANK[lv]
            return name, rank
    return None


# ------------------------------------------------------------------------- page features


def page_features(text: str, image_only: bool = False) -> PageType:
    """order_split.page_features, as a model.PageType (index 0: the caller sets it)."""
    P = proto()
    text = text or ""
    tl = re.sub(r"\s+", " ", text.lower())
    sc = P.score_page(text, False)
    n = len(text.strip())
    sc["photo"] = 5.0 if n < P.EMPTY_CHARS else 0.0          # a bundle page with no text: the photo
    if image_only and n < PHOTO_IMAGE_CHARS:                    # a scanned photo page with a few specks
        sc["photo"] = max(sc["photo"], 5.0)
    ranked = sorted(sc.items(), key=lambda kv: -kv[1])
    best_fine, best = ranked[0]
    if best < P.OTHER_BELOW and n < 150:                       # a photo scanned with a few specks
        sc["photo"] = max(sc["photo"], 3.0)
    translation = bool(_LETTERHEAD.search(tl)) or (bool(_TRANSLAT.search(tl)) and _eng_ratio(text) > ENGLISH_TYPED)
    level = level_of(tl)
    grades = len(_GRADE.findall(tl))
    cert_words = bool(_CERT_WORDS.search(tl))
    if sc["academic_apostille"] >= 4 and sc["academic_apostille"] >= sc["academic_certificate"]:
        akind = "apostille"
    elif grades >= 4 or sc["academic_transcript"] >= 5:
        akind = "transcript"
    elif cert_words:
        akind = "certificate"
    else:
        akind = "certificate" if sc["academic_certificate"] > sc["academic_transcript"] else "transcript"
    if best_fine in BANK_FINE:
        kind = "solvency" if SOLVENCY_CUE.search(text) else "statement" if STATEMENT_CUE.search(text) else ""
    elif (best_fine in ACADEMIC_FINE or level is not None or grades >= 4 or any(sc[k] > 0 for k in ACADEMIC_FINE)
          or (cert_words and best < P.OTHER_BELOW)):
        kind = akind
    else:
        kind = ""
    holders = labelled(text, "name")
    return PageType(index=0, best=best_fine if best >= P.OTHER_BELOW else "other", score=float(best),
                    scores=sc, translation=translation, level=level[0] if level else None, kind=kind,
                    nid_numbers=sorted(nid_numbers(tl)), birth_dates=sorted(_iso(d) for d in birth_dates(tl)),
                    holder=list(holders[0]) if holders else [])


def raw_best(pt: PageType) -> str:
    """The lab's `best_fine`: the top-scoring fine type even under the OTHER_BELOW bar. The alignment and
    the person split need it (a weak NID cue still decides which label pays the 'weak' cost). When
    `best` is "other", the photo score had not been raised before ranking (a raise lifts the score to
    5.0), so the ranking is the scores' order with photo at 0.0; ties go to the earlier type, as in
    the lab's stable sort."""
    if pt.best != "other":
        return pt.best
    best_k, best_v = "other", None
    for k, v in pt.scores.items():
        v = 0.0 if k == "photo" else v
        if best_v is None or v > best_v:
            best_k, best_v = k, v
    return best_k


def academic_kind(pt: PageType) -> str:
    """The academic kind the split uses for a page inside the academic block. A page that shows no
    academic sign has kind "" (or a bank kind); the lab's rule then gives it "transcript"."""
    return pt.kind if pt.kind in ACADEMIC_KINDS else "transcript"


def type_pages(pages: Sequence[PageRead]) -> List[PageType]:
    """One PageType per run page (index = PageRead.index). Rev 2.5: a page whose two best types score the same is
    settled by its own title, else by its neighbours (break_ties)."""
    out = []
    for p in pages:
        pt = page_features(p.text, p.image_only)
        pt.index = p.index
        out.append(pt)
    break_ties(out, [p.text for p in pages], [p.file for p in pages])
    return out


# ------------------------------------------------------------------------- rev 2.5: pages scanned sideways
# Board certificates are printed landscape and often scanned on their side (the EAP folder's academic p2 and p5,
# owner folder 3 p2 and p5). The reader then returns plenty of characters, but junk ("uollejalle Aue Inoylim" is
# "without any alteration" upside down), so the bot's own rotation retry (only for pages that read almost nothing)
# never runs, the page types "other" and its names are lost. A page typed "other" whose words are almost never
# common English / document words is read again turned 90, 270 and 180 degrees; a turned reading that types as a
# document and reads clearly better replaces it (PageRead.turned, sideways).
COMMON_WORDS = frozenset("""
the of and to in is that this for on by with as at from be are was were has have his her he she it its an a or not
no all any one two three four five six seven eight nine ten first second third year years date day month name names
father mother son daughter husband wife they them their we you your which who whom whose certify certificate
certified issued issue under above below following said same such been being will shall may government people
republic bangladesh board education secondary higher school examination exam college university roll registration
number serial passed duly group science arts commerce grade point average gpa result results held published
publication record records recorded birth registrar office district division union parishad bank account balance
statement savings branch amount total taka tk usd only lac lakh thousand hundred tax taxes income return national
revenue taxpayer identification circle zone assessment assessee trade license licence business owner proprietor
address present permanent village post police station upazila notary public advocate attested true copy translation
translated english bengali bangla court judge mobile phone email website road holding ward city corporation
municipality fee vat passport nationality citizen card id identity signature seal stamp authorized officer controller
examinations alteration erasure without valid validity verify verification online system generated please note
""".split())
JUNK_WORD_RATIO = 0.10           # common words among a page's words below this: junk (real pages 0.18-0.67, sideways 0.03-0.06)
JUNK_MIN_WORDS = 8               # fewer words: an empty or near-empty page (the bot's own rotation retry covers it)
TURN_ANGLES = (90, 270, 180)     # clockwise degrees tried, in this order
TURN_MIN_RATIO = 0.20            # a turned reading must read at least this well ...
TURN_GAIN = 0.15                 # ... and this much better than the first reading
TURN_GOOD = 0.30                 # a turned reading this good ends the search (the 180 degree read is skipped)


def word_quality(text: str) -> Tuple[float, int]:
    """(share of the page's words, 2+ letters, that are common English / document words; number of words)."""
    toks = re.findall(r"[A-Za-z]{2,}", text or "")
    if not toks:
        return 0.0, 0
    return sum(1 for w in toks if w.lower() in COMMON_WORDS) / len(toks), len(toks)


def looks_junk(page: PageRead, pt: Optional[PageType] = None) -> bool:
    """A page worth reading again turned: OCR'd (never a text layer), typed "other", with words, almost none of them
    common words."""
    if page.kind == "layer" or page.image_only and (page.chars or 0) < PHOTO_IMAGE_CHARS:
        return False
    pt = pt or page_features(page.text, page.image_only)
    if pt.best != "other":
        return False
    q, n = word_quality(page.text)
    return n >= JUNK_MIN_WORDS and q < JUNK_WORD_RATIO


def best_turn(text: str, read_turned: Callable[[int], str], image_only: bool = False,
              angles: Sequence[int] = TURN_ANGLES) -> Optional[Dict[str, Any]]:
    """Read the page turned (read_turned(angle) -> text, `angles` in order) and keep the best turned reading that
    types as a document (best type not "other") and reads clearly better than `text` (TURN_MIN_RATIO, TURN_GAIN).
    Returns {"angle", "text", "quality", "type", "score", "tried": {angle: (quality, type)}} or None."""
    q0, _ = word_quality(text)
    best: Optional[Dict[str, Any]] = None
    tried: Dict[int, Any] = {}
    for angle in angles:
        t = read_turned(angle) or ""
        q, _n = word_quality(t)
        pt = page_features(t, image_only)
        tried[angle] = [round(q, 3), pt.best, round(pt.score, 2)]
        if pt.best != "other" and q >= TURN_MIN_RATIO and q >= q0 + TURN_GAIN:
            cand = {"angle": angle, "text": t, "quality": round(q, 3), "type": pt.best, "score": float(pt.score)}
            if best is None or (cand["score"], cand["quality"]) > (best["score"], best["quality"]):
                best = cand
        if best is not None and best["quality"] >= TURN_GOOD:
            break
    if best is not None:
        best["tried"] = tried
        best["first_quality"] = round(q0, 3)
    return best


SIDEWAYS_RATIO = 0.5            # engine.text_line_ratio at or below: the lines run down the page (sideways: 0.02-0.04)
UPRIGHT_RATIO = 1.5             # at or above: they run across it (upright text pages 1.57 and up; Bangla or
                                # handwriting reads as junk the right way up: only an upside-down read is worth trying)


def angles_for(ratio: Optional[float]) -> Tuple[int, ...]:
    """The turns worth reading for a junk page whose text lines run as `ratio` says (None: not measured)."""
    if ratio is None:
        return TURN_ANGLES
    if ratio <= SIDEWAYS_RATIO:
        return (90, 270)
    if ratio >= UPRIGHT_RATIO:
        return (180,)
    return TURN_ANGLES


def turn_junk_pages(pages: List[PageRead], read_turned: Callable[[PageRead, int], str],
                    line_ratio: Optional[Callable[[PageRead], float]] = None) -> List[Dict[str, Any]]:
    """Every junk page (looks_junk) is read turned (best_turn); a better turned reading replaces the page's text in
    place (PageRead.text / chars, sideways=True, turned=angle). line_ratio(page) (engine.line_ratio: which way the
    text lines run, no reading) narrows the turns tried (angles_for): a junk page whose lines run across it (a Bangla
    page, CHECK_0001 p14) is read once (upside down), not three times. Returns one record per page tried: {"page" (run
    index), "file", "file_page", "angle" (0 when nothing better), "first_quality", "quality", "type", "lines"}."""
    out: List[Dict[str, Any]] = []
    for p in pages:
        if not looks_junk(p):
            continue
        ratio = None
        if line_ratio is not None:
            try:
                ratio = float(line_ratio(p))
            except (OSError, ValueError, RuntimeError):     # a page that will not render: every turn is tried
                ratio = None
        res = best_turn(p.text, lambda a, _p=p: read_turned(_p, a), p.image_only, angles_for(ratio))
        rec: Dict[str, Any] = {"page": p.index, "file": p.file, "file_page": p.file_page,
                               "first_quality": round(word_quality(p.text)[0], 3)}
        if ratio is not None:
            rec["lines"] = round(ratio, 3)
        if res is None:
            rec["angle"] = 0
        else:
            p.text, p.chars, p.sideways, p.turned = res["text"], len(res["text"]), True, int(res["angle"])
            rec.update(angle=int(res["angle"]), quality=res["quality"], type=res["type"], tried=res["tried"])
        out.append(rec)
    return out


def turned_note(records: Sequence[Dict[str, Any]], mode: str) -> str:
    """The NOTE naming every page read turned: "pages scanned sideways or upside down were turned to be read:
    <file> p2, p5 (...)" (folder) / "pages 2, 5" (bundle)."""
    done = [r for r in records if r.get("angle")]
    if not done:
        return ""
    by: Dict[str, List[int]] = {}
    for r in done:
        by.setdefault(r["file"] if mode == "folder" else "", []).append(int(r["file_page"]) + 1)
    where = "; ".join((f"{f} p" if f else ("page " if len(ps) == 1 else "pages ")) + ", ".join(str(x) for x in ps)
                      for f, ps in by.items())
    return (f"scanned sideways or upside down, turned to be read: {where} — the student should upload "
            f"{'it' if len(done) == 1 else 'them'} the right way up")


# ------------------------------------------------------------------------- rev 2.5: ties between two page types
# Owner folder 9: the INCOME TAX CERTIFICATE page right after the TIN certificate scored 7.0 for both (it prints
# the TIN) and the stable sort gave it to the TIN, so the TIN unit took the income tax's first page and its blank
# back. A tie is settled (1) by a TITLE the page prints in its head for one of the tied types, else (2) by the
# neighbours: a page that opens with a letterhead starts a document, so it goes with the type of the page after it;
# any other page continues the page before it. The winner's score gets TIE_BONUS, so the split's alignment follows the
# settle even across a blank back page between it and the next document.
TIE_EPS = 1e-6
TIE_BONUS = 3.0                  # > the alignment's cost of a blank page between (reenter 2.5): the settle holds
TIE_HEAD = 400                   # characters of the page's head where its title is looked for
# Only ties INSIDE the financial papers are settled (the TIN, the trade licence / employment certificate and the income
# tax print each other's numbers and names); any other tie stays as typed (an academic page inside the bank block
# keeps its "reads partly like it" HINT).
TITLE_CUES: Dict[str, "re.Pattern"] = {
    "income_tax": re.compile(r"income\s*tax\s*certificate|return\s+of\s+income|acknowledg\w*\s+(?:receipt|slip)", re.I),
    "tin_certificate": re.compile(r"identification\s+number\s*\(?\s*tin\s*\)?\s*certificat|\bt\s?i\s?n\s+certificat|"
                                  r"e-?tin\s+certificat", re.I),
    "trade_license": re.compile(r"trade\s*(?:and\s+business\s+)?licen[cs]e", re.I),
    "employment_certificate": re.compile(r"employment\s+certificate|salary\s+certificate|experience\s+certificate",
                                         re.I),
}
TIE_FAMILY = frozenset(TITLE_CUES)
_LETTERHEAD_HEAD = re.compile(r"government\s+of\s+the\s+people|republic\s+of\s+bangladesh|national\s+board\s+of\s+revenue",
                              re.I)


def tied_types(pt: PageType) -> List[str]:
    """The fine types that share the page's best score (two or more), photo aside; [] when there is no tie."""
    sc = {k: v for k, v in (pt.scores or {}).items() if k != "photo"}
    if not sc:
        return []
    top = max(sc.values())
    if top < proto().OTHER_BELOW:
        return []
    tied = [k for k, v in sc.items() if abs(v - top) <= TIE_EPS]
    return tied if len(tied) >= 2 and set(tied) <= TIE_FAMILY else []


def _neighbour(types: Sequence[PageType], files: Sequence[str], i: int, step: int) -> str:
    """The best type of the nearest page before (step -1) or after (step +1) that is not empty, in the same file
    ("" at the file's edge)."""
    j = i + step
    while 0 <= j < len(types) and files[j] == files[i]:
        if types[j].best not in ("photo", "other"):
            return types[j].best
        j += step
    return ""


def break_ties(types: List[PageType], texts: Sequence[str], files: Optional[Sequence[str]] = None) -> None:
    """Rev 2.5: settle every page whose best types tie (in place): its title, else its neighbours (see above). The
    pages are decided in order on the types as typed (a settled page does not move its neighbour's decision)."""
    files = list(files) if files is not None else [""] * len(types)
    plan: List[Tuple[int, str, str]] = []
    for i, pt in enumerate(types):
        tied = tied_types(pt)
        if not tied:
            continue
        head = (texts[i] or "")[:TIE_HEAD]
        firsts = []
        for k in tied:
            rx = TITLE_CUES.get(k)
            m = rx.search(head) if rx is not None else None
            if m:
                firsts.append((m.start(), k))
        win, how = "", ""
        if firsts:
            firsts.sort()
            if len(firsts) == 1 or firsts[0][0] < firsts[1][0]:
                win, how = firsts[0][1], "title"
        if not win:
            prv, nxt = _neighbour(types, files, i, -1), _neighbour(types, files, i, +1)
            starts = bool(_LETTERHEAD_HEAD.search((texts[i] or "")[:200]))
            if starts and nxt in tied:
                win, how = nxt, "a letterhead starts it; the next page's type"
            elif prv in tied:
                win, how = prv, "the page before's type"
            elif nxt in tied:
                win, how = nxt, "the next page's type"
        if win:
            plan.append((i, win, how))
    for i, win, how in plan:
        pt = types[i]
        pt.scores = dict(pt.scores)
        pt.scores[win] = float(pt.scores[win]) + TIE_BONUS
        pt.best, pt.score = win, float(pt.scores[win])
        pt.tie = f"{' / '.join(tied_types_of(pt, win))} -> {win} ({how})"


def tied_types_of(pt: PageType, win: str) -> List[str]:
    """The types the settled page had tied with (for the evidence line)."""
    base = pt.scores[win] - TIE_BONUS
    return sorted(k for k, v in pt.scores.items() if k != "photo" and abs(v - base) <= TIE_EPS) + [win]
