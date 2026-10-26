"""
PROTOTYPE (throwaway) - can we tell which page is which document from its OCR text alone?

Question: in a mixed scanned PDF, can each page be given a document type well enough to
group consecutive pages into documents and run the per-document hard rules on them?

What it does
  * loads the bot's saved OCR text, page by page, for every file in the verified student
    folders (read-only: data/verification/text/<PASSPORT>.json, keys name:size:mtime:max_pages
    and PAGES:name:size:mtime:max_pages);
  * the TRUE type of a file comes from its file name (rules.FILE_PATTERNS, as classify() does);
    the recogniser never sees names or paths - only page text and "is it an image file";
  * scores each page against weighted keyword/regex cues per type, falls back to 'other'
    when no cue is strong, then groups consecutive pages into documents;
  * splits STUDENTS into a tune half and a held-out half (seeded) and reports page accuracy,
    file-level document accuracy, the main confusions and per-type precision / recall.

Prints only counts, type labels and scores - never names, numbers or page text.

    python -m src.verify.prototype_page_types                 # held-out report
    python -m src.verify.prototype_page_types --half tune     # the half the cues were tuned on
    python -m src.verify.prototype_page_types --pages 40      # also show 40 held-out page decisions
    python -m src.verify.prototype_page_types --mixed 5       # simulate 5 merged student PDFs
"""
from __future__ import annotations

import argparse
import collections
import json
import random
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from src.verify import rules as R

DOCS_ROOT = Path(r"C:\Hangeul\VERIFIED STUDENT DOCUMENTS")
TEXT_DIR = Path(r"C:\Hangeul\BOT\data\verification\text")
IMAGE_SUFFIXES = (".jpg", ".jpeg", ".png")
SEED = 20261027


# ---------------------------------------------------------------- ground truth (file names)
def classify_name(filename: str) -> Optional[str]:
    """doc_verifier.classify, copied so this module does not pull in the OCR stack."""
    names = {re.sub(r"\s+", " ", s).strip().lower() for s in (filename, filename.replace("_", " "))}
    best, best_len = None, 0
    for key, pats in R.FILE_PATTERNS.items():
        for p in pats:
            if len(p) > best_len and any(p in n for n in names):
                best, best_len = key, len(p)
    return best


# bot key -> the label the recogniser is scored against (person assignment is out of scope)
TRUTH_LABEL = {
    "student_nid": "nid", "father_nid": "nid", "mother_nid": "nid",
    "father_death_cert": "death_cert", "mother_death_cert": "death_cert",
}


def truth_label(filename: str) -> Optional[str]:
    k = classify_name(filename)
    return TRUTH_LABEL.get(k, k) if k else None


# ---------------------------------------------------------------- data
@dataclass
class FileRec:
    student: str            # opaque id (index), never the name or passport number
    truth: Optional[str]
    image: bool
    pages: List[str]
    blob: bool = False      # multi-page file whose page boundaries are unknown (scored file-level only)


@dataclass
class Student:
    sid: str
    program: str
    files: List[FileRec] = field(default_factory=list)


def _pages_for(cache: Dict, f: Path) -> Tuple[Optional[List[str]], bool]:
    st = f.stat()
    base = f"{f.name}:{st.st_size}:{int(st.st_mtime)}"
    per_page = [v for k, v in cache.items() if k.startswith("PAGES:" + base + ":")
                and isinstance(v, dict) and v.get("pages")]
    if per_page:
        return [str(p) for p in max(per_page, key=lambda v: len(v["pages"]))["pages"]], False
    whole = [v for k, v in cache.items() if k.startswith(base + ":") and k[len(base) + 1:].isdigit()
             and isinstance(v, dict) and isinstance(v.get("text"), str) and v.get("sizes")
             and not v["text"].lstrip().startswith("[unreadable")]
    if not whole:
        return None, False
    w = max(whole, key=lambda v: len(v["sizes"]))
    n = len(w["sizes"])
    if n == 1:
        return [w["text"]], False
    parts = w["text"].split("\n")
    if len(parts) == n:                 # OCR'd pages are one line each, joined with "\n"
        return parts, False
    return [w["text"]], True


def load(docs_root: Path = DOCS_ROOT, text_dir: Path = TEXT_DIR) -> List[Student]:
    out: List[Student] = []
    for prog in sorted(p for p in docs_root.iterdir() if p.is_dir()):
        for sd in sorted(p for p in prog.iterdir() if p.is_dir()):
            m = re.search(r"\(([^)]+)\)\s*$", sd.name)
            if not m:
                continue
            cp = text_dir / f"{m.group(1).strip()}.json"
            if not cp.exists():
                continue
            try:
                cache = json.loads(cp.read_text(encoding="utf-8"))
            except Exception:
                continue
            s = Student(sid=f"S{len(out):03d}", program=prog.name.split()[0].upper())
            for f in sorted(sd.rglob("*")):
                if not f.is_file():
                    continue
                image = f.suffix.lower() in IMAGE_SUFFIXES
                pages, blob = _pages_for(cache, f)
                if pages is None:
                    continue
                s.files.append(FileRec(s.sid, truth_label(f.name), image, pages, blob))
            out.append(s)
    return out


def split(students: List[Student], seed: int = SEED) -> Tuple[List[Student], List[Student]]:
    ids = sorted(s.sid for s in students)
    random.Random(seed).shuffle(ids)
    tune = set(ids[: len(ids) // 2])
    return [s for s in students if s.sid in tune], [s for s in students if s.sid not in tune]


# ---------------------------------------------------------------- the recogniser (text only)
# fine page type -> the bot's document key it belongs to
COARSE = {
    "passport": "passport", "photo": "photo",
    "nid": "nid", "nid_translation": "nid",
    "birth_cert": "birth_cert", "death_cert": "death_cert", "family_cert": "family_cert",
    "academic_apostille": "academic", "academic_certificate": "academic", "academic_transcript": "academic",
    "bank_solvency": "bank", "bank_statement": "bank",
    "trade_license": "trade_license", "tin_certificate": "trade_license", "employment_certificate": "trade_license",
    "income_tax": "income_tax", "language_cert": "language_cert", "affidavit": "affidavit",
    "personal_statement": "personal_statement", "recommendation": "recommendation", "eca": "eca",
    "id_card_cv": "id_card_cv", "other": "other",
}

# (regex, weight); each cue counts once per page. Tuned on the tune half only.
CUES: Dict[str, List[Tuple[str, float]]] = {
    "passport": [(r"p\s?<\s?bgd", 4), (r"<<<<", 2), (r"dip\s?/?\s?dhaka", 3), (r"emergency contact", 3),
                 (r"bangladesh passport", 3), (r"personal data", 2), (r"legal guardian", 2),
                 (r"passport n[o0]", 1), (r"\bpassport\b", 0.5), (r"date of expiry", 1)],
    "nid": [(r"national (id )?card", 3), (r"nid n[o0]|id n[o0]\b", 1.5), (r"blood group", 1.5),
            (r"nearest post office", 3), (r"property of", 1.5), (r"this card", 1.5),
            (r"house ?holding", 1.5), (r"issuing authority|issue date", 1), (r"election commission", 2),
            (r"\bn[a-z]{0,2}t[a-z]{0,2}o?nal (id )?c[a-z]?rd", 1.5), (r"i\s?<\s?bgd", 3), (r"bgd\W+bgd|<bgd", 1.5),
            (r"government of the people", 1), (r"date of birth", 1)],
    "birth_cert": [(r"birth registration (certificate|number)", 3), (r"birth registration", 2),
                   (r"date of registration", 1.5), (r"registrar", 1), (r"date of issuance|issuance date", 1),
                   (r"\bsex\b", 1), (r"place of birth", 0.5), (r"in word", 1)],
    "death_cert": [(r"cause of death", 4), (r"place of death", 4), (r"date of death", 3),
                   (r"death registration (certificate|number)", 2), (r"registration of death", 2)],
    "family_cert": [(r"family (relationship )?certificate", 3), (r"\brelation\b", 2), (r"family members?", 2),
                    (r"consists? of", 2), (r"\b(sister|brother)\b", 1), (r"with each other", 1.5),
                    (r"well known", 1), (r"the family", 1), (r"\bself\b", 1), (r"union parishad|pourashava", 0.5)],
    "academic_apostille": [(r"apostille", 4), (r"convention de la haye", 3), (r"public document", 2),
                           (r"capacity of", 1), (r"foreign affairs", 1.5), (r"bears the seal", 2)],
    "academic_certificate": [(r"board of intermediate and secondary", 3), (r"secondary education", 2),
                             (r"(secondary school|higher secondary) certificate", 3),
                             (r"certificate examination", 3), (r"(dakhil|alim|fazil)\b", 2),
                             (r"technical education board", 3), (r"(bachelor|master)s? of", 2),
                             (r"degree of|has been awarded|duly (passed|completed)", 2), (r"\bdiploma\b", 1.5),
                             (r"\bg\.?p\.?a\b", 1), (r"examination", 1), (r"\bsession\b", 0.5),
                             (r"controller of examinations?", 2), (r"\buniversity\b", 1)],
    "academic_transcript": [(r"transcript", 3), (r"grade ?sheet|mark ?sheet", 3), (r"letter grade", 2),
                            (r"grade point", 2), (r"\bc\.?g\.?p\.?a\b", 2), (r"academic record", 2),
                            (r"subject", 1), (r"examination", 1)],
    "bank_solvency": [(r"solven", 3), (r"financially sound", 3), (r"been maintaining", 2),
                      (r"to whom it may concern", 1), (r"\busd\b|equivalent", 1), (r"\bbank\b", 1),
                      (r"account", 1)],
    "bank_statement": [(r"statement of account|account statement|statement period", 3),
                       (r"opening balance", 2), (r"closing balance", 2), (r"\bdebit\b", 1.5), (r"\bcredit\b", 1),
                       (r"\bbalance\b", 1), (r"transaction", 1.5), (r"withdraw|deposit", 1), (r"\bbank\b", 1),
                       (r"account", 1)],
    "trade_license": [(r"trade licen[cs]e", 4), (r"licen[cs]e n[o0]", 2), (r"(type|nature|kind) of business", 3),
                      (r"fiscal year|financial year", 1.5), (r"city corporation", 1), (r"business", 1)],
    "tin_certificate": [(r"identification number", 2), (r"tin certificate", 3), (r"board of revenue", 2),
                        (r"taxes (circle|zone)", 2), (r"registered taxpayer", 2), (r"previous tin", 2),
                        (r"\btin\b", 1), (r"please note", 0.5)],
    "income_tax": [(r"assessment year", 3), (r"return of income", 3), (r"has filed", 2), (r"tax certificate", 3),
                   (r"acknowledg", 2), (r"e-?return", 3), (r"total income", 2), (r"tax paid", 2),
                   (r"net wealth", 2), (r"challan", 2), (r"income tax", 1)],
    "employment_certificate": [(r"employment certificate|experience certificate|salary certificate", 4),
                               (r"working (as|with|in|at)", 2), (r"designation", 1), (r"salary", 2),
                               (r"\bemployee\b", 1), (r"\bjoined\b", 1.5)],
    "language_cert": [(r"ielts", 2), (r"test report form", 4), (r"\bcefr\b", 3), (r"listening", 1.5),
                      (r"reading", 1), (r"band score|overall band", 3), (r"topik", 4),
                      (r"proficiency in korean", 4), (r"toefl|duolingo", 3), (r"candidate", 1)],
    "affidavit": [(r"affidavit", 4), (r"deponent", 3), (r"solemnly", 2), (r"same person", 2),
                  (r"knowledge and belief", 2), (r"\bdeclare\b", 1), (r"notary public", 1)],
    "personal_statement": [(r"statement of purpose", 4), (r"study plan", 4), (r"personal statement", 4),
                           (r"study in (south )?korea", 2), (r"\bkorea", 1), (r"my (goal|dream|aim)", 1.5),
                           (r"\bi (believe|hope|want|plan)\b", 1)],
    "recommendation": [(r"recommend", 3), (r"letter of recommendation|recommendation letter", 4),
                       (r"i have known", 3), (r"without reservation", 2)],
    "eca": [(r"certificate of (appreciation|achievement|participation|completion|excellence)", 3),
            (r"certify that", 1), (r"this certificate", 1), (r"\bduring\b", 0.5), (r"participation", 2), (r"successfully completed|has completed", 2), (r"completion", 2),
            (r"\bcourse\b", 1), (r"volunteer", 2), (r"\bclub\b", 1), (r"workshop|seminar|training", 1.5),
            (r"competition|olympiad", 2), (r"awarded|presented to", 1.5)],
    "id_card_cv": [(r"if found", 3), (r"curriculum vitae|\bresume\b", 4), (r"career objective", 3),
                   (r"student id|id card|identity card", 2), (r"\broll\b", 1), (r"\bsession\b", 1),
                   (r"blood group", 1), (r"principal", 1), (r"\bskills\b", 1)],
}
_COMPILED = {t: [(re.compile(rx), w) for rx, w in cues] for t, cues in CUES.items()}
TRANSLATION_CUES = [(re.compile(rx), w) for rx, w in [
    (r"translat|true copy", 2), (r"\bfather", 1), (r"\bmother", 1), (r"address", 1),
    (r"notary|advocate", 1), (r"approved", 1)]]
TRANSLATED_AT = 3
FIRST_PERSON_RX = re.compile(r"\b(i|my|me)\b")
THIRD_PERSON_RX = re.compile(r"\b(he|she|his|her|him)\b")
OTHER_BELOW = 2.5        # best score under this -> 'other'
EMPTY_CHARS = 20
# EasyOCR reading an upside-down scan gives the letters back to front and turned over
# ("EXAMINATION" -> "NOILVNIWVX"); reading the text the other way round recovers the cues.
FLIP = True
_FLIP = str.maketrans({"l": "t", "v": "a", "w": "m", "d": "p", "p": "d", "u": "n", "n": "u", "q": "b", "b": "q"})
# Types whose documents come as translation + card copy: the same kind of page again, after a
# different one, starts the next person's document (father's NID, then mother's NID).
REPEAT_SPLITS = {"nid"}
# Documents that are (almost) always one page: an unrecognised page after one starts the next.
SINGLE_PAGE = {"photo", "passport", "birth_cert", "family_cert", "death_cert"}


@dataclass
class PagePred:
    fine: str
    score: float
    runner_up: str
    runner_score: float
    translated: bool = False

    @property
    def coarse(self) -> str:
        return COARSE[self.fine]


def _cue_scores(t: str) -> Dict[str, float]:
    return {k: sum(w for rx, w in cues if rx.search(t)) for k, cues in _COMPILED.items()}


def score_page(text: str, image: bool) -> Dict[str, float]:
    t = re.sub(r"\s+", " ", (text or "").lower())
    scores = _cue_scores(t)
    if FLIP and max(scores.values()) < OTHER_BELOW and len(t) > 100:
        flipped = _cue_scores(t[::-1].translate(_FLIP))
        if max(flipped.values()) >= OTHER_BELOW:
            scores = flipped
    first = len(FIRST_PERSON_RX.findall(t))
    third = len(THIRD_PERSON_RX.findall(t))
    if first >= 8:                                         # a typed letter, not an issued certificate
        for k in ("academic_certificate", "academic_transcript"):
            scores[k] = max(0.0, scores[k] - 2)
    if first >= 10:                                        # written in the first person about oneself
        scores["personal_statement"] += 2
    if third >= 6 and first >= 2:                          # someone writing about a student
        scores["recommendation"] += 2
    scores["photo"] = 5.0 if image and len(t.strip()) < EMPTY_CHARS else 0.0
    return scores


def predict_page(text: str, image: bool) -> PagePred:
    scores = score_page(text, image)
    ranked = sorted(scores.items(), key=lambda kv: -kv[1])
    (best, bs), (second, ss) = ranked[0], ranked[1]
    if bs < OTHER_BELOW:
        best = "other"
    t = (text or "").lower()
    translated = sum(w for rx, w in TRANSLATION_CUES if rx.search(t)) >= TRANSLATED_AT
    if best == "nid" and translated:
        best = "nid_translation"
    return PagePred(best, bs, second, ss, translated)


def academic_level(text: str) -> Optional[str]:
    """Cheap level guess for an academic page (not scored - no ground truth for it)."""
    t = (text or "").lower()
    for level, rx in (("degree", r"bachelor|master|degree|university"), ("diploma", r"diploma|polytechnic"),
                      ("HSC", r"higher secondary|\bhsc\b|\balim\b"), ("SSC", r"secondary school|\bssc\b|dakhil")):
        if re.search(rx, t):
            return level
    return None


# ---------------------------------------------------------------- grouping pages into documents
def group(preds: List[PagePred]) -> List[Tuple[int, int, str]]:
    """Consecutive pages -> documents [(first, last, coarse type)].
    A page with no strong cue ('other') continues the document before it. A new document starts
    when the type changes, or (REPEAT_SPLITS) when a kind of page already in the document comes
    again after a different page (translation + card, then the next translation + card)."""
    groups: List[List] = []
    seen: set = set()
    prev_fine = None
    pending: List[int] = []                 # 'other' pages waiting to see what comes next
    for i, p in enumerate(preds):
        c = p.coarse
        if c == "other":
            pending.append(i)
            prev_fine = "other"
            continue
        new = (not groups or c != groups[-1][2]
               or (c in REPEAT_SPLITS and p.fine in seen and prev_fine != p.fine))
        if new:
            # unrecognised pages after a one-page document belong to the next one
            forward = bool(pending) and (not groups or groups[-1][2] in SINGLE_PAGE)
            if pending and not forward:
                groups[-1][1] = pending[-1]
            groups.append([pending[0] if forward else i, i, c])
            seen = set()
        else:
            groups[-1][1] = i
        pending = []
        seen.add(p.fine)
        prev_fine = p.fine
    if pending:
        if groups:
            groups[-1][1] = pending[-1]
        else:
            groups.append([pending[0], pending[-1], "other"])
    return [tuple(g) for g in groups]


def file_type(preds: List[PagePred], how: str = "majority") -> str:
    if not preds:
        return "other"
    if how == "first":
        for p in preds:
            if p.coarse != "other":
                return p.coarse
        return "other"
    votes = collections.Counter(p.coarse for p in preds if p.coarse != "other")
    if not votes:
        return "other"
    top = max(votes.values())
    for p in preds:                                    # ties -> earliest page
        if votes.get(p.coarse) == top:
            return p.coarse
    return "other"


# ---------------------------------------------------------------- evaluation
LABELS = ["passport", "photo", "nid", "birth_cert", "death_cert", "family_cert", "academic", "bank",
          "trade_license", "income_tax", "language_cert", "affidavit", "personal_statement",
          "recommendation", "eca", "id_card_cv", "other"]


def _prf(pairs: List[Tuple[str, str]]) -> Dict[str, Tuple[int, float, float]]:
    out = {}
    for lab in LABELS:
        tp = sum(1 for t, p in pairs if t == lab and p == lab)
        npred = sum(1 for _, p in pairs if p == lab)
        ntrue = sum(1 for t, _ in pairs if t == lab)
        if ntrue or npred:
            out[lab] = (ntrue, tp / npred if npred else 0.0, tp / ntrue if ntrue else 0.0)
    return out


def _confusions(pairs, top=15):
    c = collections.Counter((t, p) for t, p in pairs if t != p)
    return c.most_common(top)


def _matrix(pairs) -> str:
    labs = [lab for lab in LABELS if any(t == lab or p == lab for t, p in pairs)]
    rows = ["true\\pred".ljust(19) + " ".join(lab[:5].rjust(5) for lab in labs)]
    for t in labs:
        row = [sum(1 for a, b in pairs if a == t and b == p) for p in labs]
        if sum(row):
            rows.append(t.ljust(19) + " ".join((str(x) if x else ".").rjust(5) for x in row))
    return "\n".join(rows)


def evaluate(students: List[Student], show_pages: int = 0) -> None:
    page_raw, page_ctx, file_maj, file_first = [], [], [], []
    shown = 0
    fine_counts = collections.Counter()
    for s in students:
        for f in s.files:
            if f.truth is None:
                continue
            preds = [predict_page(p, f.image) for p in f.pages]
            fine_counts.update(p.fine for p in preds)
            file_maj.append((f.truth, file_type(preds)))
            file_first.append((f.truth, file_type(preds, "first")))
            if f.blob:
                continue
            groups = group(preds)
            for i, p in enumerate(preds):
                page_raw.append((f.truth, p.coarse))
                g = next(g for g in groups if g[0] <= i <= g[1])
                page_ctx.append((f.truth, g[2]))
                if shown < show_pages:
                    lvl = academic_level(f.pages[i]) if p.coarse == "academic" else None
                    print(f"  {s.sid} file-truth={f.truth:<18} page {i + 1:>2}: {p.fine:<22} "
                          f"score={p.score:4.1f}  next={p.runner_up}:{p.runner_score:.1f}"
                          f"{'  level=' + lvl if lvl else ''}{'  [translated]' if p.translated else ''}")
                    shown += 1

    def acc(pairs):
        return sum(t == p for t, p in pairs) / max(1, len(pairs))

    n_files = sum(1 for s in students for f in s.files if f.truth)
    print(f"\nstudents={len(students)} files(with a known type)={n_files} pages scored={len(page_raw)}")
    print(f"page accuracy, page alone            : {acc(page_raw):.3f}")
    print(f"page accuracy, in its file's grouping: {acc(page_ctx):.3f}")
    print(f"document (file) accuracy, majority   : {acc(file_maj):.3f}  ({sum(t == p for t, p in file_maj)}/{len(file_maj)})")
    print(f"document (file) accuracy, first page : {acc(file_first):.3f}")
    print("\nfine page types predicted:", dict(fine_counts.most_common()))
    print("\nper type (pages alone)       n_true  precision  recall | files: n  precision  recall")
    pp, fp = _prf(page_raw), _prf(file_maj)
    for lab in LABELS:
        if lab in pp or lab in fp:
            a = pp.get(lab, (0, 0.0, 0.0))
            b = fp.get(lab, (0, 0.0, 0.0))
            print(f"  {lab:<26} {a[0]:>6}  {a[1]:>9.2f}  {a[2]:>6.2f} | {b[0]:>8}  {b[1]:>9.2f}  {b[2]:>6.2f}")
    print("\nmain page confusions (true -> predicted: pages):")
    for (t, p), n in _confusions(page_raw):
        print(f"  {t} -> {p}: {n}")
    print("\nmain file confusions (true -> predicted: files):")
    for (t, p), n in _confusions(file_maj, 10):
        print(f"  {t} -> {p}: {n}")
    print("\npage confusion matrix (pages alone):")
    print(_matrix(page_raw))


def simulate_mixed(students: List[Student], show: int, seed: int = SEED) -> None:
    """Merge each student's files (random order) into one 'PDF', recognise and group its pages,
    and check whether each original file comes back as one document of the right type."""
    rng = random.Random(seed)
    exact = total = pages_ok = pages_n = 0
    for k, s in enumerate(students):
        files = [f for f in s.files if f.truth and not f.blob]
        rng.shuffle(files)
        seq, spans = [], []
        for f in files:
            start = len(seq)
            seq.extend((f.truth, p, f.image) for p in f.pages)
            spans.append((start, len(seq) - 1, f.truth))
        preds = [predict_page(p, img) for _, p, img in seq]
        groups = group(preds)
        gset = set(groups)
        for sp in spans:
            total += 1
            exact += sp in gset
        for i, (t, _, _) in enumerate(seq):
            g = next(g for g in groups if g[0] <= i <= g[1])
            pages_ok += g[2] == t
            pages_n += 1
        if k < show:
            print(f"\n{s.sid} ({s.program}) merged PDF: {len(seq)} pages, {len(spans)} files -> {len(groups)} documents")
            for i, ((t, _, _), p) in enumerate(zip(seq, preds)):
                g = next(j for j, g in enumerate(groups) if g[0] <= i <= g[1])
                mark = "" if groups[g][2] == t else "   <-- wrong"
                print(f"  p{i + 1:>3} true={t:<18} page={p.fine:<22} {p.score:4.1f}  doc#{g + 1:<3} {groups[g][2]}{mark}")
    print(f"\nmerged-PDF simulation over {len(students)} students: files recovered exactly as one document "
          f"of the right type: {exact}/{total} = {exact / max(1, total):.3f}; pages in a document of the "
          f"right type: {pages_ok}/{pages_n} = {pages_ok / max(1, pages_n):.3f}")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--half", choices=["heldout", "tune", "all"], default="heldout")
    ap.add_argument("--pages", type=int, default=0, help="print this many per-page decisions")
    ap.add_argument("--mixed", type=int, default=-1, help="run the merged-PDF simulation, printing N students")
    ap.add_argument("--seed", type=int, default=SEED)
    a = ap.parse_args()
    students = load()
    tune, held = split(students, a.seed)
    chosen = {"heldout": held, "tune": tune, "all": students}[a.half]
    print(f"students with cached text: {len(students)}  (tune {len(tune)} / held-out {len(held)}, seed {a.seed}); "
          f"evaluating: {a.half}")
    evaluate(chosen, a.pages)
    if a.mixed >= 0:
        simulate_mixed(chosen, a.mixed, a.seed)


if __name__ == "__main__":
    main()
