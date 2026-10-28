"""Per-document checks of the test runner (RUNNER_SPEC section 5.8; Task 5).

The bot's own checkers (doc_verifier / page_checks from THIS worktree, unchanged) run on every unit the
split produced, called with DerivedRecord.portal_view() (every name, date, number, sponsor and GPA field
empty: no portal data, R1/S7). Their rows are then post-processed (DROP / KEEP_ONLY / REWRITE, pinned to
the bot's message literals by tests/test_doccheck.py) and the runner's own rows are added:

  passport      passport_rows() (validity / number / MRZ from identity.py's trusted reads) + identity notes
  photo         bundle: crop_photo() finds the photo's rectangular edge (critique 1), then the bot's shape and
                background rows only; no edge -> photo.edges FLAG and no shape/background row
  NIDs          bot rows minus its NID-number rows, + nid_number_rows() (OCR-glued digits, A6 d)
  death certs   death.stands_in only when that parent's NID is absent (critique 17)
  birth cert    birth.check_birth_certificate() (A3; Task 7); its QR name -> a MachineRead
  academic      bot rows; in a bundle the apostille page-1 rule becomes an ORDER finding (critique 8);
                board_qr() reads the SSC/HSC board QR with the crop/threshold ladder (A6 c)
  bank          bot rows minus account age, dates, USD and seal (bank.py judges those, A7 / critique 15)
  trade_license bot rows minus the loose TIN-vs-trade-licence row (financial.py judges names)
  income_tax    MASTER: bot rows; other programs: every FLAG/FAIL becomes NOTE + a PRESENT line (A6 h)
  language_cert language.check_language_file() on row text (IELTS / TOEFL / TOPIK; others PRESENT)
  affidavit, personal_statement, recommendation, eca: present; id_card_cv: PRESENT, never read
  + page_rows(): the bot's colour / QR / Bangla page checks (the QR check on defused text; a whole bundle
    scanned in grey gives ONE bundle FLAG, Q15) + doc.dob_consistency / doc.passport_no_consistency (5.9.5)

Names are not judged here (names.py, bank.py, tin.py, financial.py). No personal data.
"""
from __future__ import annotations

import importlib
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence, Tuple

from . import botimport
from .model import (ERROR, FAIL, FLAG, NOTE, ORDER, PASS, PORTAL_KEYS, PRESENT, AffidavitInfo, BirthFacts,
                    DerivedRecord, DocUnit, Finding, Identity, MachineRead, PageRead)

SRC = "runner:doccheck"

# --- what the bot's rows lose or become (pinned by tests/test_doccheck.py::test_pinned_messages) -------------
_NID_DROP = [r"-digit ID number reads ", r"reads the same on the original and the translation",
             r"read, but not on two pages in the same format", r"^no NID/birth-registration number could be read"]
DROP: Dict[str, List[str]] = {
    "father_nid": _NID_DROP,
    "mother_nid": _NID_DROP,
    "student_nid": _NID_DROP,
    "bank": [r"^account opened ",                                              # A7: account age removed
             r"^solvency certificate and statement both dated", r"^the solvency certificate is dated ",
             r"^solvency certificate dated ", r"^statement generated ", r"^neither date could be read",
             r"^solvency amount stated in USD", r"^no USD equivalent found",   # critique 15: bank.py, solvency page
             r"^carries a seal and signature", r"^no seal or signature of any kind found"],
    "trade_license": [r"^TIN and trade licence are both in the name of", r"^the TIN names '"],
}
# owner rule 28 Oct (rev 2.5): the bank OPENING-BALANCE rule is dropped (only the solvency balance counts). The bot's
# opening_balance_check rows (any verdict) become one NOTE OPENING_BALANCE_NOTE.
OPENING_BALANCE_ROWS: List[str] = [r"^no line is labelled as the opening balance", r"^the statement opens at ",
                                   r"^opening balance .{0,24} BDT is within the expected range"]
OPENING_BALANCE_NOTE = "opening balance not judged (owner rule 28 Oct)"
# academic units whose board QR was read: the bot's page-level "no QR" rows (qr_check is skipped there too)
DROP_BOARD_QR: List[str] = [r"^no QR code and nothing showing it is the online registration copy",
                            r"^a QR code is on the page but could not be read", r"^no QR code on this document"]
# bundle photo: the owner's rule is background and shape only
KEEP_ONLY: Dict[str, List[str]] = {
    "photo": [r"^\d+x\d+px, ratio", r"^background is", r"^photo file could not be opened",
              r"^could not open the image"],
}
REWRITE: Dict[str, Dict[str, str]] = {
    "student_nid": {r"^no notarisation found — parents' NID must be notarised$":
                    "no notarisation found — the student's NID must be notarised"},
}
# bundle academic: the bot's page-1 rule is about the page order, which the owner fixes (critique 8)
APOSTILLE_PAGE1 = r"^page 1 is not an e-Apostille — the first apostille is on page (\d+)"
# the bot's own holder rows; the empty portal record means they cannot fire (asserted in the tests)
HOLDER_ROWS = [r"bank account holder", r"account appears to be in the name of", r"expected account holder",
               r"portal sponsor is"]

INFO_SUFFIX = " (information only: the income-tax guideline is for Master's)"
COLOUR_BUNDLE_TEXT = ("the whole bundle was scanned in black and white — the colour rule cannot be judged per "
                      "document; rescan in colour")
TAX_DEFUSE = re.compile(r"(?i)taxpayer|tin\s?certificate|etin")

NOT_CHECKED = frozenset({"nid_unassigned", "unrecognised"})
NO_PAGE_ROWS = frozenset({"photo", "id_card_cv", "nid_unassigned", "unrecognised"})
NID_KEYS = {"father_nid": "father", "mother_nid": "mother", "student_nid": "student"}
DEATH_KEYS = {"father_death_cert": "father", "mother_death_cert": "mother", "death_cert": ""}
PRESENT_KEYS = frozenset({"personal_statement", "recommendation", "eca"})
_NOT_MEASURED = ("typed document", "produced on a computer")     # colour rows that judged no colour

PHOTO_RATIO_HW = (1.0, 1.6)        # the photo's height / width
PHOTO_AREA = (0.02, 0.60)          # share of the page
SHORTCUT_COVER = (0.60, 0.90)      # a single embedded image covering this share of the page IS the photo
CROP_INSET = 0.02                  # crop this far inside the found edge


class PortalLeak(Exception):
    """A bot row that needs a portal value fired: the empty record leaked (R1). Stops the run."""


@dataclass
class DocChecks:
    per_unit: Dict[str, List[Finding]]   # uid -> findings
    machine: List[MachineRead]           # board-QR and birth-QR names (A1 machine reads)
    bundle: List[Finding]                # bundle-level findings (page.colour_bundle)
    # (person, uid, written, run page) of names read here that enter the A2 dot rule: the language certificate's
    # candidate name (A1 lists the language-certificate candidate)
    person_reads: List[Tuple[str, str, str, int]] = field(default_factory=list)


# --- small helpers ------------------------------------------------------------------------------------------
def _bot() -> SimpleNamespace:
    return botimport._loaded if botimport._loaded is not None else botimport.load()


def _runner_mod(name: str):
    """Another task's runner module, imported when first needed (tests put fakes in sys.modules)."""
    return importlib.import_module(f"{__package__}.{name}")


def _call_opt(fn: Callable, *args, **optional):
    """fn(*args, **the optional keywords fn accepts): another task's function that may or may not take them."""
    import inspect
    try:
        params = inspect.signature(fn).parameters
    except (TypeError, ValueError):
        params = {}
    takes_all = any(p.kind is p.VAR_KEYWORD for p in params.values())
    return fn(*args, **{k: v for k, v in optional.items() if takes_all or k in params})


def _faults() -> tuple:
    return tuple(_bot().dv.CODE_FAULTS)


def _empty_view() -> Dict[str, str]:
    return {k: "" for k in PORTAL_KEYS}


def input_page(i: int, pages: Optional[Sequence[PageRead]]) -> int:
    """1-based input page of run page i: the bundle page, or the page of the folder file."""
    if pages is not None and 0 <= i < len(pages):
        return int(pages[i].file_page) + 1
    return i + 1


def unit_pages(unit: DocUnit, pages: Optional[Sequence[PageRead]] = None) -> Tuple[int, ...]:
    return tuple(input_page(i, pages) for i in unit.pages)


def _span(pg: Sequence[int]) -> str:
    if not pg:
        return "?"
    a, b = min(pg), max(pg)
    return str(a) if a == b else f"{a}-{b}"


def _drop(rows: Iterable[Tuple[str, str]], patterns: Sequence[str]) -> List[Tuple[str, str]]:
    return [(v, m) for v, m in rows if not any(re.search(p, m) for p in patterns)]


def _keep_only(rows: Iterable[Tuple[str, str]], patterns: Sequence[str]) -> List[Tuple[str, str]]:
    return [(v, m) for v, m in rows if any(re.search(p, m) for p in patterns)]


def _rewrite(rows: Iterable[Tuple[str, str]], table: Optional[Dict[str, str]]) -> List[Tuple[str, str]]:
    out = []
    for v, m in rows:
        for pat, new in (table or {}).items():
            if re.search(pat, m):
                m = re.sub(pat, new, m)
        out.append((v, m))
    return out


def bot_findings(rows: List[Tuple[str, str]], fn: str, unit: DocUnit, pages: List[PageRead]) -> List[Finding]:
    """(verdict, message) rows of bot function `fn` -> Findings on the unit's pages (1-based input pages)."""
    pg = unit_pages(unit, pages)
    return [Finding(v, f"bot.{fn}", part_page_refs(m, unit, pages), f"bot:{fn}", pg) for v, m in rows]


# rev 2.5: a page word and EVERY number of the list after it: "page 2", "pages 2-3", "page 2, 3", "pages 2 and 3",
# "page(s) 2, 5" (the bot's sideways NOTE), "pages 2, 3 and 5"
_PAGE_N = r"\d{1,3}(?!\d|,\d{3})"           # a page number, never the head of an amount ("1,800,000")
_PAGE_REF = re.compile(r"\b(pages?|page\(s\))\s+(" + _PAGE_N + r"(?:\s*(?:[-–]|,|\band\b|&)\s*" + _PAGE_N + r")*)")
_PAGE_NUM = re.compile(r"\d+")


def part_page_refs(message: str, unit: DocUnit, pages: Optional[Sequence[PageRead]]) -> str:
    """Folder mode, revision 2.3: a unit cut out of a file that holds several documents (DocUnit.part) is read by
    the bot as its own file, so a bot message's "page N" is the N-th page of the cut; it becomes the page inside the
    input file, as every other folder message names it. Revision 2.5: every number of a page list is mapped ("page
    2, 3", "pages 2 and 3", "page(s) 2", "pages 2-3"), not only the first. Any other unit: the message unchanged."""
    if not getattr(unit, "part", "") or not pages or not message:
        return message

    def one(m: "re.Match") -> str:
        n = int(m.group(0))
        return str(input_page(unit.pages[n - 1], pages)) if 0 < n <= len(unit.pages) else m.group(0)

    def rep(m: "re.Match") -> str:
        word, nums = m.groups()
        return f"{word} {_PAGE_NUM.sub(one, nums)}"

    return _PAGE_REF.sub(rep, message)


# --- passport ------------------------------------------------------------------------------------------------
_EXPIRY_SOURCE = {"MRZ": "the MRZ", "MRZ re-read": "the MRZ (zoomed re-read)",
                  "passport label": "the 'Date of Expiry' label"}


def passport_rows(text: str, ident: Identity, today) -> List[Finding]:
    """passport.validity / passport.number / passport.mrz from identity.py's reads (R2). FAIL only on a
    trusted expiry (MRZ, MRZ re-read or the 'Date of Expiry' label); a date merely seen on the scan is
    never FAIL. The number is the MRZ's (check digit) against the data page's printed number."""
    B = _bot()
    dv, R = B.dv, B.R
    src = "runner:passport"
    out: List[Finding] = []
    exp, how, trusted = ident.expiry, ident.expiry_source, bool(ident.expiry_trusted)
    if exp is None and text:
        future = [d for d in dv.find_dates(text) if d >= today]
        if future:
            exp, how, trusted = max(future), "scan date", False
    if exp is None:
        out.append(Finding(FLAG, "passport.validity", "could not read an expiry date — check the passport by eye", src))
    else:
        m = dv.months_between(today, exp)
        need = R.PASSPORT_MIN_MONTHS
        if trusted:
            if m < 0:
                out.append(Finding(FAIL, "passport.validity", f"passport expired on {exp}", src))
            elif m < need:
                out.append(Finding(FAIL, "passport.validity",
                                   f"only {m} month(s) validity left (expires {exp}); at least {need} months are "
                                   "required — must be renewed", src))
            else:
                out.append(Finding(PASS, "passport.validity",
                                   f"valid until {exp} ({m} months left; read from {_EXPIRY_SOURCE.get(how, how or 'the MRZ')})",
                                   src))
        elif m >= need:
            out.append(Finding(PASS, "passport.validity",
                               f"valid until {exp} ({m} months left; read from a date on the scan, not the MRZ)", src))
        else:
            out.append(Finding(FLAG, "passport.validity",
                               f"the latest date on the scan is {exp} ({m} month(s) away; read from a date on the "
                               "scan, not the MRZ) — check the expiry by eye", src))
    mrz = ident.mrz or {}
    number = ident.passport_no or str(mrz.get("passport_no") or "")
    if not mrz or not number:
        out.append(Finding(FLAG, "passport.number", "the MRZ could not be read — check the passport by eye", src))
    elif ident.passport_no_ok:
        printed = ident.printed_passport_no
        if not printed:
            out.append(Finding(PASS, "passport.number", f"passport number {number} read from the MRZ (its check "
                                                        "digit agrees); the printed number could not be read", src))
        elif printed == number:
            out.append(Finding(PASS, "passport.number", f"passport number {number} is the same in the MRZ (check "
                                                        "digit agrees) and on the data page", src))
        else:
            out.append(Finding(FLAG, "passport.number", f"the MRZ reads passport number {number} (check digit "
                                                        f"agrees) but the data page prints {printed} — check by eye",
                               src))
    else:
        out.append(Finding(FLAG, "passport.number", f"the MRZ passport number {number} fails its check digit — "
                                                    "check the number by eye", src))
    if mrz.get("line2_ok"):
        where = ident.dob_source or ident.expiry_source or "MRZ"
        if where not in ("MRZ", "MRZ re-read"):
            where = "MRZ"
        out.append(Finding(PASS, "passport.mrz", f"MRZ read; its check digits agree ({where})", src))
    return out


# --- photo ---------------------------------------------------------------------------------------------------
_CROPS: Dict[str, bool] = {}       # resolved photo path -> True (a crop inside a found edge) / False (no edge)


def _ckey(p: Any) -> str:
    try:
        return str(Path(p).resolve()).lower()
    except OSError:
        return str(p).lower()


def crop_known(p: Optional[Path]) -> bool:
    return p is not None and _ckey(p) in _CROPS


def crop_succeeded(p: Optional[Path]) -> bool:
    return p is not None and _CROPS.get(_ckey(p)) is True


def _rect_of(approx, page_area: float):
    import cv2
    if len(approx) != 4 or not cv2.isContourConvex(approx):
        return None
    x, y, w, h = cv2.boundingRect(approx)
    if w <= 0 or h <= 0:
        return None
    area = float(cv2.contourArea(approx))
    if not (PHOTO_AREA[0] * page_area <= area <= PHOTO_AREA[1] * page_area):
        return None
    if not (PHOTO_RATIO_HW[0] <= h / w <= PHOTO_RATIO_HW[1]):
        return None
    if area < 0.80 * w * h:                  # a rectangle, not a diamond or a blob
        return None
    return area, (x, y, w, h)


def find_photo_rect(grey) -> Optional[Tuple[int, int, int, int]]:
    """(x, y, w, h) of the photo's rectangular edge on a grey page, or None (critique 1): Canny 50/150, a
    3 px dilation, external contours, approxPolyDP to a convex 4-corner shape covering 2-60% of the page
    with h/w 1.0-1.6; the largest wins. Never a dark-pixel mask (that finds the head, not the photo)."""
    import cv2
    import numpy as np
    H, W = grey.shape[:2]
    edges = cv2.Canny(grey, 50, 150)
    edges = cv2.dilate(edges, np.ones((3, 3), np.uint8), iterations=1)
    found = cv2.findContours(edges, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    contours = found[0] if len(found) == 2 else found[1]
    best = None
    for c in contours:
        peri = cv2.arcLength(c, True)
        if peri <= 0:
            continue
        hit = _rect_of(cv2.approxPolyDP(c, 0.02 * peri, True), float(H * W))
        if hit and (best is None or hit[0] > best[0]):
            best = hit
    return best[1] if best else None


def embedded_image_box(pdf_path: Path, page_index: int) -> Optional[Tuple[float, float, float, float]]:
    """The page's single embedded image as (x0, y0, x1, y1) fractions of the page, or None (no image, several
    images, or not a PDF). Used only for crop_photo's whole-image shortcut."""
    p = Path(pdf_path)
    if p.suffix.lower() != ".pdf":
        return None
    import pymupdf
    with pymupdf.open(str(p)) as doc:
        if not 0 <= page_index < len(doc):
            return None
        page = doc[page_index]
        imgs = page.get_images(full=True)
        if len(imgs) != 1:
            return None
        rects = page.get_image_rects(imgs[0][0])
        if len(rects) != 1:
            return None
        r, pr = rects[0], page.rect
        if pr.width <= 0 or pr.height <= 0:
            return None
        return (max(0.0, (r.x0 - pr.x0) / pr.width), max(0.0, (r.y0 - pr.y0) / pr.height),
                min(1.0, (r.x1 - pr.x0) / pr.width), min(1.0, (r.y1 - pr.y0) / pr.height))


def crop_photo(eng: Any, page_image, out_jpg: Path,
               embedded: Optional[Tuple[float, float, float, float]] = None) -> Optional[Path]:
    """The passport-size photo cut out of a bundle page (critique 1). `page_image`: the page at 300 dpi, RGB.
    `embedded`: the page's single embedded image box (embedded_image_box), when known.
      1. Whole-image shortcut ONLY when that image is not page-sized: it covers >= 60% and < 90% of the page
         and its h/w is 1.0-1.6 (a scanned bundle page, one image covering the whole page, never takes it).
      2. Otherwise the photo's rectangular edge (find_photo_rect), cropped 2% inside.
      3. No rectangle: None; nothing is written and the photo is not judged on an unverified crop.
    Writes a JPEG (quality 95) to out_jpg. The outcome is remembered for photo_rows()."""
    import numpy as np
    out_jpg = Path(out_jpg)
    img = np.asarray(page_image)
    if img.ndim == 2:
        img = np.stack([img] * 3, axis=-1)
    img = img[:, :, :3].astype(np.uint8)
    H, W = img.shape[:2]
    box = None
    if embedded:
        x0, y0, x1, y1 = embedded
        cover = max(0.0, x1 - x0) * max(0.0, y1 - y0)
        hw = ((y1 - y0) * H) / max(1e-6, (x1 - x0) * W)
        if SHORTCUT_COVER[0] <= cover < SHORTCUT_COVER[1] and PHOTO_RATIO_HW[0] <= hw <= PHOTO_RATIO_HW[1]:
            box = (int(round(x0 * W)), int(round(y0 * H)), int(round(x1 * W)), int(round(y1 * H)))
    if box is None:
        grey = (0.299 * img[:, :, 0] + 0.587 * img[:, :, 1] + 0.114 * img[:, :, 2]).astype(np.uint8)
        rect = find_photo_rect(grey)
        if rect is None:
            _CROPS[_ckey(out_jpg)] = False
            return None
        x, y, w, h = rect
        dx, dy = int(round(CROP_INSET * w)), int(round(CROP_INSET * h))
        box = (x + dx, y + dy, x + w - dx, y + h - dy)
    x0, y0, x1, y1 = box
    crop = img[max(0, y0):min(H, y1), max(0, x0):min(W, x1)]
    if crop.size == 0:
        _CROPS[_ckey(out_jpg)] = False
        return None
    from PIL import Image
    out_jpg.parent.mkdir(parents=True, exist_ok=True)
    Image.fromarray(np.ascontiguousarray(crop)).save(str(out_jpg), format="JPEG", quality=95)
    _CROPS[_ckey(out_jpg)] = True
    return out_jpg


def crop_unit_photo(eng: Any, unit: DocUnit, pages: List[PageRead], source_pdf: Optional[Path] = None,
                    docs_dir: Optional[Path] = None) -> Optional[Path]:
    """crop_photo() for a bundle's photo unit: its first page at 300 dpi, the embedded-image box from
    `source_pdf` (the bundle; optional), written to docs\\PASSPORT SIZE PHOTO p<NN>.jpg. On success
    unit.path becomes the crop; on failure unit.path is kept and marked 'no edge found'."""
    if not unit.pages:
        return None
    i = unit.pages[0]
    page = pages[i]
    if docs_dir is None:
        docs_dir = Path(unit.path).parent if unit.path is not None else botimport.PRIVATE / "tmp"
    out = Path(docs_dir) / f"PASSPORT SIZE PHOTO p{input_page(i, pages):02d}.jpg"
    embedded = None
    if source_pdf is not None:
        try:
            embedded = embedded_image_box(Path(source_pdf), int(page.file_page))
        except _faults():
            raise
        except Exception:
            embedded = None
    img = eng.page_image_of(page, 300)
    got = crop_photo(eng, img, out, embedded)
    if got is not None:
        unit.path = got
    elif unit.path is not None:
        _CROPS[_ckey(unit.path)] = False
    return got


def photo_rows(unit: DocUnit, mode: str, eng: Any, pages: Optional[List[PageRead]] = None) -> List[Finding]:
    """Folder: the bot's check_photo, every row (the Gender NOTE cannot fire with the empty record).
    Bundle: only the shape and background rows of check_photo on the crop; when no photo edge was found,
    photo.edges FLAG and no shape or background row (critique 1)."""
    dv = eng.bot.dv
    pg = unit_pages(unit, pages)
    path = Path(unit.path) if unit.path is not None else None
    if mode == "bundle":
        if path is None or not path.exists() or not crop_succeeded(path):
            return [Finding(FLAG, "photo.edges", f"the photo's edges were not found on "
                                                 f"{'page' if len(set(pg)) <= 1 else 'pages'} {_span(pg)} — "
                                                 "check by eye", "runner:photo", pg)]
        rows = _keep_only(dv.check_photo("", _empty_view(), [], path), KEEP_ONLY["photo"])
        return bot_findings(rows, "check_photo", unit, pages or [])
    return bot_findings(dv.check_photo("", _empty_view(), [], path), "check_photo", unit, pages or [])


# --- NID numbers (A6 d) --------------------------------------------------------------------------------------
_NID_RUN = re.compile(r"\d[\d\s.\-]{7,32}\d")              # the digit runs dv.nid_numbers joins
_GLUE = {10: (11, 12), 13: (14, 15, 16), 17: (18, 19, 20)}  # number size -> glued run lengths looked into


def nid_number_rows(page_texts: List[str], unit: DocUnit, pages: Optional[List[PageRead]] = None) -> List[Finding]:
    """nid.number: the ID number on the original and the translation, with OCR-glued noise digits undone.
    Per page dv.nid_numbers(text), plus every window of the right size inside a joined run of 11-12, 14-16
    or 18-20 digits that equals a clean number read on ANOTHER page of the unit. Then the bot's own
    dv.nid_number_agreement() on those sets."""
    dv = _bot().dv
    pg = unit_pages(unit, pages)
    clean = [set(dv.nid_numbers(t or "")) for t in page_texts]
    per_page = [set(s) for s in clean]
    glued: List[str] = []
    for i, t in enumerate(page_texts):
        elsewhere = set().union(*(clean[j] for j in range(len(clean)) if j != i)) if len(clean) > 1 else set()
        if not elsewhere:
            continue
        for run in _NID_RUN.findall(t or ""):
            digits = re.sub(r"\D", "", run)
            for size, lengths in _GLUE.items():
                if len(digits) not in lengths:
                    continue
                for k in range(len(digits) - size + 1):
                    w = digits[k:k + size]
                    if w in elsewhere and w not in per_page[i]:
                        per_page[i].add(w)
                        glued.append(w)
    every = sorted({n for s in per_page for n in s})
    src = "runner:nid_numbers"
    if not every:
        return [Finding(FLAG, "nid.number", "no NID/birth-registration number could be read from the scan", src, pg)]
    ev = {"glued_pages": len(glued)} if glued else {}
    return [Finding(v, "nid.number", m, src, pg, dict(ev)) for v, m in dv.nid_number_agreement(per_page, every)]


# --- NID back side (owner rule 28 Oct, rev 2.5) --------------------------------------------------------------
# A smart NID's back carries a TD1 machine-readable zone (ICAO 9303 part 5): line 1 "I<BGD" + the document number
# (9 characters + check digit; a 10-digit NID: 9 digits, "<", the 10th digit and the check digit in the optional
# data), line 2 the date of birth + check, the sex, the expiry + check, line 3 the name. The number and the two
# dates carry check digits, so a back that belongs to another person can be CONFIRMED: the owner's folder 9 mother
# NID file held the father's card back (in the scan and in the translation). An old laminated card has no MRZ: only
# reliable signals, else a NOTE.
_MRZ_W = (7, 3, 1)
_TD1_L1 = re.compile(r"[IK1L|][\s<]{0,3}[B8][G6C][D0O]\s*((?:[0-9]\s?){9})\s?([0-9<])\s?((?:[0-9]\s?){0,8})\s?(?=<)")
_TD1_L2 = re.compile(r"(?<![0-9])((?:[0-9]\s?){6})([0-9])\s?([MF<])\s?((?:[0-9]\s?){6})([0-9])(?![0-9])")
_TD1_NAME = re.compile(r"([A-Z]{2,}(?:<[A-Z]{2,})*)\s?<<\s?([A-Z]{2,}(?:\s?<\s?[A-Z]{2,})*)")
ROLE_SEX = {"father": "M", "mother": "F"}
NID_BACK_SRC = "runner:nid_back"


def mrz_check_digit(s: str) -> int:
    """ICAO 9303 check digit: digits as themselves, A-Z as 10-35, '<' as 0, weights 7-3-1."""
    tot = 0
    for k, ch in enumerate(s):
        v = int(ch) if ch.isdigit() else (ord(ch) - 55 if "A" <= ch <= "Z" else 0)
        tot += v * _MRZ_W[k % 3]
    return tot % 10


def _yymmdd(s: str, today=None) -> Optional["dt.date"]:
    import datetime as _dt
    try:
        yy, mm, dd = int(s[:2]), int(s[2:4]), int(s[4:6])
        now = (today or _dt.date.today()).year % 100
        return _dt.date((1900 if yy > now else 2000) + yy, mm, dd)
    except ValueError:
        return None


def td1_reads(text: str, today=None) -> List[Dict[str, Any]]:
    """Every TD1 (smart NID back) machine-readable zone in one page's OCR text: {"number", "number_ok", "dob",
    "dob_ok", "sex", "line2_ok", "name"} (a field None when unread). Line 1 and line 2 are found apart (OCR joins and
    splits the lines); the date of birth counts only with its own check digit, the sex letter when at least one of the
    two date check digits beside it agrees (rev 2.6; the line is anchored digit by digit, so the letter sits in place).
    OCR's "K<BGD" / "I<860" / lower case are read as the printed "I<BGD"."""
    t = (text or "").upper()
    out: List[Dict[str, Any]] = []
    nums = []
    for m in _TD1_L1.finditer(t):
        head, chk, ext = (re.sub(r"\s", "", g or "") for g in m.groups())
        if chk == "<" and ext:                          # a long number: its last digits and check in the optional data
            number, chk = head + ext[:-1], ext[-1]
        else:
            number = head
        ok = chk.isdigit() and mrz_check_digit(number) == int(chk)
        nums.append((number, ok))
    lines2 = []
    for m in _TD1_L2.finditer(t):
        dob, c1, sex, exp, c2 = (re.sub(r"\s", "", g) for g in m.groups())
        d_ok, e_ok = mrz_check_digit(dob) == int(c1), mrz_check_digit(exp) == int(c2)
        # rev 2.6 (checker's rules probe): the sex letter sits between the two dated fields, which the pattern
        # anchors digit by digit; ONE of their check digits agreeing shows a real line 2 with the letter in place
        # (a misread date digit is a substitution, not a shift). The date of birth itself still needs its own check.
        lines2.append({"dob": _yymmdd(dob, today) if d_ok else None, "dob_ok": d_ok, "exp_ok": e_ok,
                       "sex": sex if ((d_ok or e_ok) and sex in "MF") else None, "line2_ok": d_ok and e_ok})
    name = None
    if nums or any(x["line2_ok"] for x in lines2):
        nm = _TD1_NAME.search(t.replace(" <", "<").replace("< ", "<"))
        if nm:
            name = (nm.group(1) + " " + nm.group(2)).replace("<", " ")
    good2 = [x for x in lines2 if x["line2_ok"]] or [x for x in lines2 if x["dob_ok"] or x["exp_ok"]]
    for k in range(max(len(nums), 1 if good2 else 0)):
        number, ok = nums[k] if k < len(nums) else (None, False)
        l2 = good2[k] if k < len(good2) else (good2[0] if good2 else {})
        out.append({"number": number if ok else None, "number_ok": ok, "number_read": number,
                    "dob": l2.get("dob"), "dob_ok": bool(l2.get("dob_ok")), "sex": l2.get("sex"),
                    "line2_ok": bool(l2.get("line2_ok")), "name": name})
    return [r for r in out if r["number_ok"] or r["line2_ok"]]


def _digits_apart(a: str, b: str) -> int:
    if len(a) != len(b):
        return max(len(a), len(b))
    return sum(1 for x, y in zip(a, b) if x != y)


def nid_back_rows(page_texts: List[str], unit: DocUnit, pages: Optional[List[PageRead]], ident: Optional[Identity],
                  today=None) -> List[Finding]:
    """nid.back_side (owner rule 28 Oct): does the card's back side (its MRZ, in the scan and/or the translation)
    belong to the person on the front and to the file's role? Signals, each from an MRZ field whose check digit
    agrees: the number (vs the front's number, read on the unit's pages: 3 digits or more apart, never an OCR slip),
    the date of birth (vs the front's labelled date) and the sex (vs the role: father M, mother F; the student: the
    passport's). Two signals or more that differ = CONFIRMED -> FAIL "the back side of this NID belongs to another
    person"; one -> FLAG check by eye; compared and all equal -> PASS; no MRZ read (an old laminated card has none)
    -> NOTE (never judged)."""
    dv = _bot().dv
    pg = unit_pages(unit, pages)
    role = NID_KEYS.get(unit.key, "")
    want_sex = ROLE_SEX.get(role) or ((getattr(ident, "sex", "") or "").upper()[:1] if role == "student" else "")
    reads: List[Tuple[int, Dict[str, Any]]] = []
    front_nums: Dict[str, List[int]] = {}
    front_dobs: Dict[Any, List[int]] = {}
    for k, t in enumerate(page_texts):
        for r in td1_reads(t, today):
            reads.append((k, r))
        for n in dv.nid_numbers(t or ""):
            front_nums.setdefault(n, []).append(k)
        d = dv.labelled_dob(t or "")
        if d:
            front_dobs.setdefault(d, []).append(k)
    if not reads:
        return [Finding(NOTE, "nid.back_side", "the card's back side was not compared with its front (no machine-"
                                               "readable zone read; an old laminated card has none)", NID_BACK_SRC, pg)]
    mrz_nums = {r["number"] for _, r in reads if r["number"]}
    fronts = {n: ks for n, ks in front_nums.items() if n not in mrz_nums}
    signals: Dict[str, str] = {}
    agree: List[str] = []
    ev: Dict[str, Any] = {"role": role, "mrz_pages": sorted({input_page(unit.pages[k], pages) if k < len(unit.pages)
                                                             else k + 1 for k, _ in reads}), "reads": len(reads)}
    # the number
    for _, r in reads:
        n = r["number"]
        if not n:
            continue
        same = [f for f in front_nums if f == n]
        if same:
            agree.append("number")
            break
        far = [f for f in fronts if len(f) == len(n) and _digits_apart(f, n) >= 3]
        if far and len(far) == len([f for f in fronts if len(f) == len(n)]):
            signals["number"] = f"the back's number {n} is not the front's {far[0]}"
            break
    # the date of birth
    for _, r in reads:
        d = r["dob"]
        if not d or not front_dobs:
            continue
        if d in front_dobs:
            agree.append("date of birth")
        else:
            f = sorted(front_dobs, key=lambda x: -len(front_dobs[x]))[0]
            signals["dob"] = f"the back's date of birth {d.isoformat()} is not the front's {f.isoformat()}"
        break
    # the sex against the role
    sexes = [r["sex"] for _, r in reads if r["sex"]]
    if sexes and want_sex in ("M", "F"):
        s = max(set(sexes), key=sexes.count)
        if s != want_sex:
            whose = {"father": "the father's", "mother": "the mother's", "student": "the student's"}.get(role, "this")
            signals["sex"] = f"the back says sex {s} on {whose} card"
        else:
            agree.append("sex")
    ev.update(signals=dict(signals), agree=list(dict.fromkeys(agree)),
              mrz=[{"page": k, "number_ok": r["number_ok"], "line2_ok": r["line2_ok"]} for k, r in reads])
    if "number" in agree and set(signals) == {"dob"}:
        # the back's checked number IS the front's: one card. The front's date of birth (an OCR read, no check
        # digit) read otherwise is a misread of the front, not another person's back
        ev["dob_note"] = signals.pop("dob")
        ev["signals"] = dict(signals)
    if len(signals) >= 2:
        both = len({k for k, _ in reads}) >= 2
        return [Finding(FAIL, "nid.back_side", "the back side of this NID belongs to another person ("
                        + "; ".join(signals.values()) + ")" + (" — in the scan and the translation" if both else ""),
                        NID_BACK_SRC, pg, ev)]
    if signals:
        return [Finding(FLAG, "nid.back_side", "the back side of this NID may belong to another person ("
                        + "; ".join(signals.values()) + ") — check by eye", NID_BACK_SRC, pg, ev)]
    if agree:
        return [Finding(PASS, "nid.back_side", "the back side (machine-readable zone) matches the front ("
                        + ", ".join(dict.fromkeys(agree)) + ")", NID_BACK_SRC, pg, ev)]
    return [Finding(NOTE, "nid.back_side", "the card's back side was read but could not be compared with its front "
                                           "(nothing on the front to compare)", NID_BACK_SRC, pg, ev)]


# --- board QR (A6 c) -----------------------------------------------------------------------------------------
_QR_NAME_LABEL = re.compile(r"(?i)name\s*[:=]")
_QR_NOT_STUDENT = re.compile(r"(?i)(father|mother|husband|guardian|parent)\W{0,4}s?\W{0,3}$")
_QR_VALUE = re.compile(r"\s*([A-Za-z][A-Za-z .'\-]{2,}?)\s*(?=$|[\r\n,;/|]|\s+[A-Za-z'’ ]{2,24}?\s*[:=])")
_QR_SLASHED = re.compile(r"^\s*\d{3,12}\s*/\s*([A-Za-z][A-Za-z .'\-]{2,}?)\s*/\s*\d(?:[.,]\d{1,2})?\s*(?:/|$)")


def board_qr_name(payload: str) -> str:
    """The student's name a board QR carries, or "": a 'Name: X' field (not the father's or mother's), or
    the slash format 'roll/name/GPA' the education boards print."""
    s = payload or ""
    m = _QR_SLASHED.match(s)
    if m:
        return re.sub(r"\s+", " ", m.group(1)).strip(" .-")
    for lab in _QR_NAME_LABEL.finditer(s):
        if _QR_NOT_STUDENT.search(s[max(0, lab.start() - 16):lab.start()]):
            continue
        v = _QR_VALUE.match(s, lab.end())
        if v:
            return re.sub(r"\s+", " ", v.group(1)).strip(" .-")
    return ""


def _is_apostille(code: str, PC: Any) -> bool:
    low = (code or "").lower()
    return any(h in low for h in getattr(PC, "APOSTILLE_HOSTS", ("apostille",)))


def _meaningful(codes: List[str], tin: Any) -> bool:
    fn = getattr(tin, "meaningful", None)
    if callable(fn):
        return bool(fn(codes))
    return any(re.match(r"https?://", c, re.I) or len(c) >= 8 for c in codes)


def board_qr(unit: DocUnit, pages: List[PageRead]) -> Tuple[List[Finding], List[MachineRead]]:
    """academic.board_qr: the board QR on SSC/HSC pages, read with tin.read_qr's crop/threshold ladder
    (A6 c). Apostille codes are not board codes. A payload naming the student becomes a MachineRead. No
    code -> no row (the bot's qr_check row stays)."""
    if unit.path is None:
        return [], []
    tin = _runner_mod("tin")
    PC = _bot().PC
    try:
        res = tin.read_qr(Path(unit.path), deep=True)
    except _faults():
        raise
    except Exception:
        return [], []
    out: List[Finding] = []
    machine: List[MachineRead] = []
    for r in res or []:
        k = int(r.get("page", 0) or 0)
        codes = [str(c) for c in (r.get("codes") or []) if c and not _is_apostille(str(c), PC)]
        if not codes or not _meaningful(codes, tin):
            continue
        run = unit.pages[k] if 0 <= k < len(unit.pages) else None
        n = input_page(run, pages) if run is not None else k + 1
        out.append(Finding(PASS, "academic.board_qr", f"board QR read on page {n}", "runner:board_qr", (n,),
                           {"codes": [c[:120] for c in codes]}))
        for c in codes:
            name = board_qr_name(c)
            if name:
                machine.append(MachineRead(unit=unit.uid, person="student", name=name, source="board QR",
                                           page=run if run is not None else -1))
    return out, machine


# --- page checks ---------------------------------------------------------------------------------------------
FIN_KIND_UNSURE = r"^could not tell which financial document this is$"
FIN_KIND_TEXT = {"trade_license": "trade licence", "tin_certificate": "TIN certificate",
                 "employment_certificate": "employment certificate"}
_BUSINESS_START = r"business start(?:ing)? date\D{0,20}([\d./-]{6,12}|\d{1,2}\s+\w+\s+\d{4})"   # == check_financial


def licence_rows(text: str, path: Optional[Path], dv: Any) -> List[Tuple[str, str]]:
    """The rows dv.check_financial gives a trade licence once its words say "trade licen" (doc_verifier 1595-1607,
    unchanged): the fiscal year (fiscal_year_check), the business start date and the notarisation."""
    out = list(dv.fiscal_year_check(text))
    m = re.search(_BUSINESS_START, text or "", re.I)
    if m:
        d = dv.find_dates(m.group(1))
        if d:
            yrs = (dv.TODAY - d[0]).days / 365.25
            out.append((PASS, f"business started {d[0]} ({yrs:.1f} years ago)") if yrs >= 2
                       else (FLAG, f"business started {d[0]} — under 2 years; older businesses are "
                                   "viewed more positively"))
    out.append((PASS, "trade licence is notarised (wording or seal found)") if dv.notarised(text, path)
               else (FAIL, "trade licence is not notarised — notarisation is required for it"))
    return out


def typed_as(u: DocUnit, pages: Optional[List[PageRead]], fine: str) -> bool:
    """A page of the unit is typed `fine` by its own text (pagetype.page_features), not by a file name or a default."""
    if not pages or not fine:
        return False
    PT = _runner_mod("pagetype")
    return any(PT.page_features(pages[i].text, pages[i].image_only).best == fine
               for i in u.pages if 0 <= i < len(pages))


def financial_kind_rows(rows: List[Tuple[str, str]], u: DocUnit, text: str, path: Optional[Path],
                        dv: Any, pages: Optional[List[PageRead]] = None) -> List[Tuple[str, str]]:
    """Revision 2.4: dv.check_financial tells a trade licence, a TIN certificate or an employment certificate by a
    few title words ("trade licen", "taxpayer's identification", "tin certificate", "employment"). A title it does
    not know ("TRADE AND BUSINESS LICENSE", owner folder 9) or an OCR slip ("Certificale", owner folder 3) gives FLAG
    "could not tell which financial document this is", and a licence's own rows (fiscal year, notarisation) never
    run. When a page of the unit is typed as the unit's fine type by its own text (typed_as), that row becomes PASS
    "contains: <kind> (by the page typing ...)" and a trade licence gets the bot's licence rows on its text
    (licence_rows). A fine type from the file name or a default only: the row stays."""
    kind = FIN_KIND_TEXT.get(u.fine or "")
    if not kind or not any(re.search(FIN_KIND_UNSURE, m) for _, m in rows) or not typed_as(u, pages, u.fine):
        return rows
    out: List[Tuple[str, str]] = []
    for v, m in rows:
        if re.search(FIN_KIND_UNSURE, m):
            out.append((PASS, f"contains: {kind} (by the page typing; the bot's title words were not read on it)"))
            if u.fine == "trade_license":
                out += licence_rows(text, path, dv)
        else:
            out.append((v, m))
    return out


# --- what the bot's "notarised" seal was (evidence only; owner question pending, rev 2.5) -------------------------
NOTARISED_ROW = r"^notarised \(wording or seal found\)$"
SEAL_RENDER_DPI = 150          # == page_checks.seal_present
SEAL_MAX_PAGES = 4


def seal_components(img_bgr) -> List[Dict[str, Any]]:
    """The violet / red seal-coloured blobs page_checks.seal_present counts (its own masks and sizes), described:
    the box as page fractions, the colour, the share of yellow / green around it and `emblem_like` (a round red
    blob near the top centre with yellow / green around it: the government monogram printed on an NBR / Union
    Parishad form, not a notary's stamp)."""
    import cv2
    import numpy as np
    H, W = img_bgr.shape[:2]
    hsv = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2HSV)
    h, s, v = hsv[:, :, 0], hsv[:, :, 1], hsv[:, :, 2]
    violet = ((h > 115) & (h < 165) & (s > 60) & (v > 60)).astype("uint8")
    red = ((s > 90) & (v > 70) & ((h < 10) | (h > 170))).astype("uint8")
    # the monogram: a red disc holding a yellow map, in a green ring (yellow or green around the red)
    green = ((h > 20) & (h < 90) & (s > 60) & (v > 50))
    out: List[Dict[str, Any]] = []
    for name, mask, need in (("violet", violet, 0.0008), ("red", red, 0.0015)):
        m = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, np.ones((9, 9), np.uint8))
        n, _, stats, _ = cv2.connectedComponentsWithStats(m, 8)
        for i in range(1, n):
            x, y, w, hh, area = (int(stats[i, k]) for k in range(5))
            if area <= need * H * W:
                continue
            # the monogram's map and ring lie inside and AROUND its red disc: measured in the box grown by half its size
            gx0, gy0 = max(0, int(x - w / 2)), max(0, int(y - hh / 2))
            gx1, gy1 = min(W, int(x + w * 1.5)), min(H, int(y + hh * 1.5))
            g = float(green[gy0:gy1, gx0:gx1].mean()) if gx1 > gx0 and gy1 > gy0 else 0.0
            cx, cy = (x + w / 2) / W, (y + hh / 2) / H
            round_ = 0.7 <= (w / max(hh, 1)) <= 1.4
            out.append({"colour": name, "box": [round(x / W, 3), round(y / H, 3), round((x + w) / W, 3),
                                                round((y + hh) / H, 3)],
                        "area": round(area / (H * W), 4), "green_around": round(g, 3), "round": round_,
                        "emblem_like": bool(name == "red" and cy < 0.30 and 0.30 <= cx <= 0.70 and round_
                                            and g >= EMBLEM_GREEN)})
    return out


EMBLEM_GREEN = 0.01            # share of yellow / green pixels around a red disc: the government monogram
# printed words that say the paper is a government office's own (its seal is the office's, not a notary's)
OFFICE_SEAL_WORDS = re.compile(r"(?i)seal\s+of\s+(?:the\s+)?(?:tax\s+)?office|commissioner\s+of\s+taxes|"
                               r"deputy\s+commissioner|tax\s+circle|seal\s+of\s+the\s+official|union\s+parishad|"
                               r"chairman|upazila\s+nirbahi")


def seal_evidence(text: str, path: Optional[Path], dv: Any, R: Any) -> Dict[str, Any]:
    """Evidence for the bot's "notarised (wording or seal found)" row: which of the two proved it. The wording
    (R.NOTARY_WORDS, the bot's fuzzy has_any), else the seal colour, with every seal-coloured blob described
    (seal_components) and `emblem_like_only` = every blob looks like the government emblem (the owner question: is
    a government emblem / tax-office seal a notarisation?). Never changes the verdict."""
    ev: Dict[str, Any] = {"wording": bool(dv.has_any(text or "", R.NOTARY_WORDS))}
    if ev["wording"] or path is None:
        ev["how"] = "wording" if ev["wording"] else "none"
        return ev
    import cv2
    import numpy as np
    blobs: List[Dict[str, Any]] = []
    try:
        p = Path(path)
        if p.suffix.lower() in (".jpg", ".jpeg", ".png"):
            img = cv2.imdecode(np.fromfile(str(p), dtype=np.uint8), cv2.IMREAD_COLOR)
            imgs = [img] if img is not None else []
        else:
            import pymupdf
            imgs = []
            with pymupdf.open(str(p)) as doc:
                for k, pg in enumerate(doc):
                    if k >= SEAL_MAX_PAGES:
                        break
                    pix = pg.get_pixmap(dpi=SEAL_RENDER_DPI)
                    a = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width, pix.n)
                    imgs.append(cv2.cvtColor(a[:, :, :3], cv2.COLOR_RGB2BGR))
        for k, img in enumerate(imgs):
            for b in seal_components(img):
                blobs.append(dict(b, page=k + 1))
    except _faults():
        raise
    except Exception as e:                       # evidence only: never a crash
        ev["error"] = type(e).__name__
    ev["how"] = "seal colour" if blobs else "none"
    ev["seals"] = blobs[:12]
    ev["emblem_like"] = sum(1 for b in blobs if b["emblem_like"])
    ev["emblem_like_only"] = bool(blobs) and all(b["emblem_like"] for b in blobs)
    words = OFFICE_SEAL_WORDS.search(text or "")
    ev["office_paper"] = bool(words)
    if words:
        ev["office_words"] = words.group(0)
    if blobs and (ev["emblem_like_only"] or words):
        what = []
        if ev["emblem_like"]:
            what.append("the government emblem printed at the top of the form")
        if words and not ev["emblem_like_only"]:
            what.append(f"the issuing office's own seal (the paper prints \"{words.group(0)}\")")
        ev["note"] = ("no notary wording; the seal colour found looks like " + " and ".join(what) + ", not a notary's "
                      "stamp — owner question pending (is that a notarisation?); the verdict is the bot's")
    return ev


def with_seal_evidence(fs: List[Finding], text: str, path: Optional[Path], dv: Any, R: Any) -> List[Finding]:
    """The bot's "notarised (wording or seal found)" PASS gets seal_evidence() in its evidence (run.json); the
    verdict and the message stay the bot's."""
    out = []
    for f in fs:
        if f.verdict == PASS and re.search(NOTARISED_ROW, f.message or ""):
            f = Finding(f.verdict, f.rule, f.message, f.source, f.pages,
                        dict(f.evidence or {}, notarised=seal_evidence(text, path, dv, R)))
        out.append(f)
    return out


# --- a QR the bot's page check did not find (rev 2.5) -----------------------------------------------------------
QR_NONE_ROWS = [r"^no QR code on this document", r"^a QR code is on the page but could not be read",
                r"^no QR code and nothing showing it is the online registration copy"]
QR_LADDER_PAGES = 20
# the documents an issuing office prints with a verification QR (Union Parishad / City Corporation trade licences and
# family certificates): only their "no QR" rows get the (slow, ~25 s a page) closer look
QR_LADDER_FINES = frozenset({"trade_license", "family_cert"})
QR_LADDER_NONE = " (a QR-like block on {where} was looked at closely with the crop/threshold QR ladder; nothing decoded)"


def qr_ladder_rows(prs: List[Finding], unit: DocUnit, pages: Optional[List[PageRead]]) -> List[Finding]:
    """Rev 2.5: before the runner reports that a document has no QR (or an unreadable one), the crop/threshold QR
    ladder (tin.read_qr, deep) looks again: a degraded licence QR (owner folder 3) that it decodes turns the bot's row
    into PASS "QR code read on page N (closer look: ...)". Nothing decoded: the bot's row stays."""
    hits = [f for f in prs if f.rule == "page.qr" and any(re.search(p, f.message) for p in QR_NONE_ROWS)]
    if not hits or unit.path is None or (unit.fine or unit.key) not in QR_LADDER_FINES:
        return prs
    tin = _runner_mod("tin")
    PC = _bot().PC
    found, looked = [], []
    t0 = time.perf_counter()
    try:
        for k in range(min(len(unit.pages) or 1, QR_LADDER_PAGES)):
            img = tin.render_page(Path(unit.path), k, 250)
            if img is None:
                continue
            # only a page that shows a QR-like square (finder patterns or a QR-textured block) is looked at closely;
            # the deep stages (400 dpi, more thresholds: 25-45 s a page) only where QR finder patterns are seen (a
            # textured block alone is often a seal or a photo, owner folders 3 and 9: the deep stages never decoded one)
            finders = tin._finder_boxes(img)
            if not (finders or tin._blob_boxes(img)):
                continue
            looked.append(k)
            r = tin.read_page(Path(unit.path), k, img, deep=bool(finders))
            codes = [str(c) for c in (r.get("codes") or []) if c]
            if codes and _meaningful(codes, tin):
                found.append((k, codes, str(r.get("stage") or "")))
                break
    except _faults():
        raise
    except Exception:
        return prs
    secs = round(time.perf_counter() - t0, 1)

    def page_of(k: int) -> int:
        run = unit.pages[k] if 0 <= k < len(unit.pages) else None
        return input_page(run, pages) if run is not None else k + 1

    if not found:
        # nothing decoded: the bot's row (and verdict) stays. When a QR-like block was looked at, the message says so
        # (a blurred QR the ladder cannot decode either, owner folder 3 p5: whether that FLAGs is an owner question);
        # when no page shows a QR-like block, the row is the bot's, word for word
        if not looked:
            return [Finding(f.verdict, f.rule, f.message, f.source, f.pages,
                            dict(f.evidence or {}, qr_ladder="no QR-like block on any page", qr_ladder_s=secs))
                    if any(f is h for h in hits) else f for f in prs]
        where = ("page " if len(looked) == 1 else "pages ") + ", ".join(str(page_of(k)) for k in looked)
        tail = QR_LADDER_NONE.format(where=where)
        return [Finding(f.verdict, f.rule, f.message + tail, f.source, f.pages,
                        dict(f.evidence or {}, qr_ladder="tried, nothing decoded", qr_ladder_pages=[page_of(k)
                                                                                                for k in looked],
                             qr_ladder_s=secs))
                if any(f is h for h in hits) else f for f in prs]
    k, codes, stage = found[0]
    run = unit.pages[k] if 0 <= k < len(unit.pages) else None
    n = input_page(run, pages) if run is not None else k + 1
    new = Finding(PASS, "page.qr", f"QR code read on page {n} with a closer look (crop/threshold ladder): "
                                   + ", ".join(c[:48] for c in codes[:2]), "runner:qr_ladder", (n,),
                  {"stage": stage, "bot": [f.message for f in hits], "apostille": any(_is_apostille(c, PC)
                                                                                    for c in codes)})
    out, done = [], False
    for f in prs:
        if any(f is h for h in hits):
            if not done:
                out.append(new)
                done = True
            continue
        out.append(f)
    return out


def holds_tin_pages(unit: DocUnit) -> bool:
    return unit.fine == "tin_certificate" or any(len(s) > 2 and s[2] == "tin_certificate" for s in unit.sub)


def page_rows(unit: DocUnit, text: str, eng: Any, skip_qr: bool,
              pages: Optional[List[PageRead]] = None) -> List[Finding]:
    """The bot's page checks: PC.colour_check, PC.qr_check on DEFUSED text (so 'marketing' or 'meeting' no
    longer skips it; TIN, birth and board-QR units pass skip_qr) and PC.bangla_check (not for the birth
    certificate: birth.py owns its translation rule)."""
    B = eng.bot
    PC, R = B.PC, B.R
    pg = unit_pages(unit, pages)
    info = PC.inspect(Path(unit.path))
    out = [Finding(v, "page.colour", m, "bot:page_checks.colour_check", pg) for v, m in PC.colour_check(info, unit.key)]
    if not skip_qr:
        defused = TAX_DEFUSE.sub(" ", text or "")
        out += [Finding(v, "page.qr", m, "bot:page_checks.qr_check", pg)
                for v, m in PC.qr_check(info, required=unit.key in R.QR_DOCS, text=defused)]
    if unit.key != "birth_cert":
        out += [Finding(v, "page.bangla", m, "bot:page_checks.bangla_check", pg) for v, m in PC.bangla_check(text)]
    if getattr(unit, "part", ""):                    # rev 2.3: a cut part's page numbers -> the file's pages
        out = [Finding(f.verdict, f.rule, part_page_refs(f.message, unit, pages), f.source, f.pages, f.evidence)
               for f in out]
    return out


# --- date of birth and passport number on other documents (5.9.5) ------------------------------------------
_PP_LABEL = re.compile(r"(?i)passport\s*(?:no|number|num)\b\.?")
_PP_NUMBER = re.compile(r"(?<![A-Z0-9])([A-Z]{1,2}\d{7,8})(?![A-Z0-9])")


def closer_look(eng: Any, page: PageRead, candidates: Sequence[str], kind: str, box=None) -> Optional[Any]:
    """closer.look() (Agent A's closer look, CPU ONNX engine) at a high-resolution render of one run page:
    candidates[0] is the reference. None when the closer look is not available (no closer module, no engine, a page
    that will not render): the caller then keeps its FLAG (a false PASS is worse than a FLAG)."""
    if eng is None or page is None or len([c for c in candidates if c]) < 2:
        return None
    try:
        closer = _runner_mod("closer")
    except ImportError:
        return None
    try:
        img = closer.page_image(eng, page)
        if img is None:
            return None
        return closer.look(img, box, list(candidates), kind=kind)
    except _faults():
        raise
    except Exception:                                # a page that will not render: no closer look
        return None


_DATE_RAW = re.compile(r"\d{1,2}\s*[./-]\s*\d{1,2}\s*[./-]\s*\d{4}|\d{4}\s*[./-]\s*\d{1,2}\s*[./-]\s*\d{1,2}|"
                       r"\d{1,2}\s*(?:st|nd|rd|th)?[ ,.-]*[A-Za-z]{3,9}[ ,.-]+\d{4}")


def raw_date(text: str, d: Any) -> str:
    """The date as printed in `text` that dv.find_dates reads as d (the first one after a birth label first), or
    ""."""
    dv = _bot().dv
    t = text or ""
    lab = re.search(r"(?i)birth|dob", t)
    order = list(_DATE_RAW.finditer(t, lab.start())) if lab else []
    order += [m for m in _DATE_RAW.finditer(t) if m not in order]
    for m in order:
        if d in dv.find_dates(m.group(0)):
            return m.group(0)
    return ""


def as_printed(raw: str, d: Any, ref: Any) -> str:
    """`raw` (a printed form of date d) rewritten to say date `ref` in the same layout (zero padding, separators and
    the month word kept): the reference spelling the closer look compares the print with."""
    if not raw:
        return ""
    months = ["", "January", "February", "March", "April", "May", "June", "July", "August", "September", "October",
              "November", "December"]
    nums = re.findall(r"\d+", raw)
    word = re.search(r"[A-Za-z]{3,9}", raw)
    if word:                                         # "01 January 2000" -> day and year numbers, the month word
        day_s, year_s = nums[0], nums[-1]
        mw = word.group(0)
        new_m = months[ref.month][:len(mw)] if len(mw) <= 3 else months[ref.month]
        new_m = new_m.upper() if mw.isupper() else new_m
        out = raw.replace(mw, new_m, 1)
        out = re.sub(r"^\s*" + re.escape(day_s), str(ref.day).zfill(len(day_s)), out, count=1)
        return out[: len(out) - len(year_s)] + str(ref.year) if out.endswith(year_s) else out
    if len(nums) != 3:
        return ""
    if len(nums[0]) == 4:
        want = [ref.year, ref.month, ref.day]
    elif int(nums[0]) == d.month and int(nums[1]) == d.day and d.day != d.month:
        want = [ref.month, ref.day, ref.year]
    else:
        want = [ref.day, ref.month, ref.year]
    it = iter(want)
    return re.sub(r"\d+", lambda m: str(next(it)).zfill(len(m.group(0))), raw)


def dob_rows(text: str, ident: Identity, unit: DocUnit, pages: Optional[List[PageRead]] = None,
             eng: Any = None) -> List[Finding]:
    """doc.dob_consistency: the labelled date of birth on the student NID or the SSC certificate against the
    passport's TRUSTED date. PASS / FLAG, never FAIL; nothing when either is unread. Rev 2.5 (owner, 28 Oct: a date
    that reads differently is looked at again before any verdict): the run's own page reads count with the bot's
    read; when a read differs from the passport's date the closer look compares the print with the two spellings
    (closer.look kind "digits") — it supports the passport's date: PASS; otherwise FLAG check by eye."""
    if not (ident.dob and ident.dob_trusted):
        return []
    dv = _bot().dv
    reads: List[Tuple[Any, Optional[int], str]] = []          # (date, run page or None for the bot's read, its text)
    for i in (unit.pages if pages else []):
        if 0 <= i < len(pages):
            dd = dv.labelled_dob(pages[i].text or "")
            if dd:
                reads.append((dd, i, pages[i].text or ""))
    d0 = dv.labelled_dob(text or "")
    if d0:
        reads.append((d0, None, text or ""))
    if not reads:
        return []
    pg = unit_pages(unit, pages)
    title = unit.title or unit.key
    src = ident.dob_source or "MRZ"
    if all(dd == ident.dob for dd, _, _ in reads):
        return [Finding(PASS, "doc.dob_consistency", f"date of birth {ident.dob} on the {title} is the passport's "
                                                     f"({src})", SRC, pg)]
    d, run, raw_text = next((x for x in reads if x[0] != ident.dob))
    look_page = run if run is not None else next((i for _, i, _ in reads if i is not None), None)
    if look_page is None and pages and unit.pages:
        look_page = unit.pages[0]
    raw = raw_date(raw_text, d)
    ref_raw = as_printed(raw, d, ident.dob)
    ev: Dict[str, Any] = {"reads": [[x.isoformat(), i] for x, i, _ in reads], "variant": d.isoformat()}
    look = closer_look(eng, pages[look_page] if (pages and look_page is not None) else None, [ref_raw, raw], "digits")
    if look is not None:
        ev["closer"] = {"decision": look.decision, "margin": round(float(look.margin), 3), "note": look.note}
        if look.decision == "same":
            return [Finding(PASS, "doc.dob_consistency", f"date of birth {ident.dob} on the {title} is the passport's "
                                                         f"({src}; one read gave {d}, a closer look at the print "
                                                         f"reads {ident.dob})", SRC, pg, ev)]
    return [Finding(FLAG, "doc.dob_consistency", f"the {title} gives date of birth {d}, the passport {ident.dob} "
                                                 f"({src}) — check by eye", SRC, pg, ev)]


def passport_no_rows(text: str, ident: Identity, unit: DocUnit,
                     pages: Optional[List[PageRead]] = None) -> List[Finding]:
    """doc.passport_no_consistency: a passport-format number after a 'Passport No' label on an affidavit or
    a solvency certificate that is not the passport's -> FLAG."""
    ref = ident.passport_no if (ident.passport_no_ok and ident.passport_no) else ident.printed_passport_no
    if not ref:
        return []
    seen = []
    for m in _PP_LABEL.finditer(text or ""):
        n = _PP_NUMBER.search((text or "")[m.end():m.end() + 30].upper())
        if n and len(n.group(1)) == 9:
            seen.append(n.group(1))
    if not seen:
        return []
    pg = unit_pages(unit, pages)
    other = [s for s in seen if s != ref]
    if other:
        return [Finding(FLAG, "doc.passport_no_consistency",
                        f"a different passport number ({other[0]}) is written after a 'Passport No' label on the "
                        f"{unit.title or unit.key}; the passport's is {ref} — check by eye", SRC, pg)]
    return [Finding(PASS, "doc.passport_no_consistency",
                    f"the passport number on the {unit.title or unit.key} is the passport's", SRC, pg)]


# --- language ------------------------------------------------------------------------------------------------
def language_rows(unit: DocUnit, pages: List[PageRead], eng: Any, ident: Identity, program: str, today,
                  aff: AffidavitInfo, reads_out: Optional[List[Tuple[str, str, str, int]]] = None) -> List[Finding]:
    """language.<check> rows for IELTS / TOEFL / TOPIK (row text from the page boxes, else the 200 dpi text);
    doc.present_only PRESENT for any other certificate kind (critique 20). `reads_out` receives the candidate's
    name as written, given names first (person "student"), for the A2 dot rule."""
    from . import language as L
    names = _runner_mod("names")
    identity = _runner_mod("identity")
    faults = _faults()
    pg = unit_pages(unit, pages)
    per_page = []
    for i in unit.pages:
        t = ""
        try:
            t = L.rows_text(eng.read_boxes(eng.page_image_of(pages[i], 300)))
        except faults:
            raise
        except Exception:
            t = ""
        if len((t or "").strip()) < 40:
            t = pages[i].text
        per_page.append(t or "")
    ref = ident.student.written or ident.student.parts

    def covered_fn(cert_name: str) -> bool:
        return bool(names.covered(aff, ref, cert_name))

    rep = L.check_language_file("\f".join(per_page), identity.passport_for_language(ident), program, today,
                                covered_fn)
    out: List[Finding] = []
    for cert in rep["certificates"]:
        test = cert["test"]
        if reads_out is not None and unit.pages:
            f = cert.get("fields") or {}
            if f.get("family_name") and f.get("first_name"):
                reads_out.append(("student", unit.uid, f"{f['first_name']} {f['family_name']}", unit.pages[0]))
            elif f.get("name"):
                reads_out.append(("student", unit.uid, str(f["name"]), unit.pages[0]))
        if test not in L.CHECKED_TESTS:
            out.append(Finding(PRESENT, "doc.present_only",
                               f"{L.PRESENT_ONLY_TEXT} ({L.TEST_LABEL.get(test, test)})", "runner:language", pg))
            continue
        for v, check, why in cert["findings"]:
            out.append(Finding(v, "language." + check, f"{test}: {why}", "runner:language", pg))
    return out


# --- the loop ------------------------------------------------------------------------------------------------
# the bot's (page_checks.academic_check) mixed-level FLAG: its level reader takes "Alim" anywhere on the page for HSC,
# so a Dakhil (SSC) transcript of an "... Alim Madrasah" reads as HSC (owner folder 9)
MIXED_LEVELS = r"^the pages after the e-Apostille on page (\d+) are not all for one qualification \((.*?)\) — "
_LEVEL_DETAIL = re.compile(r"page (\d+): ([A-Z]+)")


def level_override(m: str, unit: DocUnit, pages: Optional[List[PageRead]]) -> Optional[Finding]:
    """Rev 2.5: the bot's "not all for one qualification (page 5: SSC, page 6: HSC)" FLAG, re-judged by the runner's
    level reader (pagetype.level_of: the EXAMINATION a page names decides between HSC and SSC — Dakhil = SSC, Alim =
    HSC — never a word of the institution's name). When the runner reads ONE level on every page the bot named, the
    row becomes PASS "... followed by <LEVEL> certificate/transcript"; otherwise None (the bot's FLAG stays). The page
    numbers are the bot's (pages of the file it read: the unit)."""
    hit = re.search(MIXED_LEVELS, m)
    if not hit or not pages:
        return None
    PT = _runner_mod("pagetype")
    named = [(int(k), lv) for k, lv in _LEVEL_DETAIL.findall(hit.group(2))]
    if not named:
        return None
    ours: Dict[int, str] = {}
    for k, _ in named:
        if not (0 < k <= len(unit.pages)):
            return None
        lv = PT.level_of(pages[unit.pages[k - 1]].text or "")
        if lv is None:
            return None
        ours[k] = lv[0]
    if len(set(ours.values())) != 1:
        return None
    lv = next(iter(ours.values()))
    if unit.level and unit.level.upper() != lv.upper():
        return None
    where = ", ".join(str(k) for k, _ in named)
    msg = (f"e-Apostille on page {hit.group(1)} is followed by {lv} certificate/transcript (page {where}) — the level "
           "is read from the examination each page names (Dakhil = SSC, Alim = HSC), not from an institution's name")
    return Finding(PASS, "academic.level", part_page_refs(msg, unit, pages), SRC, unit_pages(unit, pages),
                   {"bot": m, "bot_levels": {str(k): v for k, v in named}, "runner_levels": {str(k): v for k, v in
                                                                                         ours.items()}})


def _academic_rows(rows: List[Tuple[str, str]], unit: DocUnit, pages: List[PageRead], mode: str) -> List[Finding]:
    keep, order = [], []
    lifted: List[Finding] = []
    for v, m in rows:
        if v == FLAG:
            alt = level_override(m, unit, pages)
            if alt is not None:
                lifted.append(alt)
                continue
        hit = re.search(APOSTILLE_PAGE1, m)
        if mode == "bundle" and hit:
            k = int(hit.group(1))
            run = unit.pages[k - 1] if 0 < k <= len(unit.pages) else None
            n = input_page(run, pages) if run is not None else k
            order.append(Finding(ORDER, "academic.apostille_order",
                                 f"the e-Apostille for {unit.level or 'this level'} is on page {n} — put it first in "
                                 "that level's pages", SRC, (n,)))
            continue
        keep.append((v, m))
    return bot_findings(keep, "check_academic", unit, pages) + lifted + order


def _error(rule: str, message: str, unit: DocUnit, pages: List[PageRead]) -> Finding:
    return Finding(ERROR, rule, message, SRC, unit_pages(unit, pages))


def _why(e: BaseException, path: Optional[Path]) -> str:
    msg = f"{type(e).__name__}: {e}"
    if path is not None:
        for full in (str(path), repr(str(path))[1:-1]):
            if full:
                msg = msg.replace(full, Path(path).name)
    return msg


def check_documents(units: List[DocUnit], pages: List[PageRead], rec: DerivedRecord, eng: Any,
                    mode: str, aff: AffidavitInfo, births: Dict[Tuple[int, ...], BirthFacts],
                    confirm: Callable[[int, str], str], *, work_dir: Optional[Path] = None,
                    source_pdf: Optional[Path] = None) -> DocChecks:
    """The per-document checks, in the bot's own order: units sorted by R.REQUIRED[program] + R.OPTIONAL +
    ["income_tax"] key order (so the passport text exists before the NIDs; income tax has a place for every
    program), page order within a key. nid_unassigned and unrecognised units get no findings here (their
    split notes are merged by verdict.merge). `work_dir`: where birth re-reads write (default <out>\\work,
    derived from the unit's file); `source_pdf`: the bundle (the photo crop's embedded-image shortcut)."""
    B = eng.bot
    dv, R = B.dv, B.R
    faults = tuple(dv.CODE_FAULTS)
    view = rec.portal_view()
    ident, program, today = rec.identity, rec.program, rec.today
    present = {u.key for u in units}
    order = list(dict.fromkeys(list(R.REQUIRED[program]) + list(R.OPTIONAL) + ["income_tax"]))
    rank = {k: i for i, k in enumerate(order)}
    todo = sorted(units, key=lambda u: (rank.get(u.key, len(order)), min(u.pages) if u.pages else 0, u.uid))
    per_unit: Dict[str, List[Finding]] = {u.uid: [] for u in units}
    machine: List[MachineRead] = []
    person_reads: List[Tuple[str, str, str, int]] = []
    texts: Dict[str, Any] = {}
    passport_noted = False

    def work_for(u: DocUnit) -> Path:
        if work_dir is not None:
            return Path(work_dir)
        if u.path is not None:
            return Path(u.path).resolve().parent.parent / "work"
        return botimport.PRIVATE / "tmp" / "work"

    def check_unit(u: DocUnit) -> List[Finding]:
        key = u.key
        pg = unit_pages(u, pages)
        if key == "id_card_cv":                       # R13: never read, no page checks
            return [Finding(PRESENT, "doc.present_only", "present — not checked (owner, 21 Oct)", SRC, pg)]
        if key == "photo":
            if mode == "bundle" and not crop_known(u.path):
                crop_unit_photo(eng, u, pages, source_pdf,
                                Path(u.path).parent if u.path is not None else work_for(u).parent / "docs")
            return photo_rows(u, mode, eng, pages)
        if u.path is None:
            return [_error("doc.unreadable", "no file was cut or staged for this document — it could not be "
                                             "checked", u, pages)]
        path = Path(u.path)
        text, sizes = dv.read_document(path)
        if (text or "").lstrip().startswith("[unreadable:"):
            return [_error("doc.unreadable", f"could not read this file ({text.strip().strip('[]')})", u, pages)]
        texts[key] = texts.get(key, "") + "\n" + text
        fs: List[Finding] = []
        skip_qr = False
        board_read = False
        if key == "passport":
            fs += passport_rows(text, ident, today)
        elif key in NID_KEYS:
            rows = dv.check_nid(text, view, sizes, NID_KEYS[key], path, texts)
            rows = _rewrite(_drop(rows, DROP[key]), REWRITE.get(key))
            fs += bot_findings(rows, "check_nid", u, pages)
            fs += nid_number_rows([pages[i].text for i in u.pages], u, pages)
            fs += nid_back_rows([pages[i].text for i in u.pages], u, pages, ident, today)     # rev 2.5
            if key == "student_nid":
                fs += dob_rows(text, ident, u, pages, eng)
        elif key in DEATH_KEYS:
            parent = DEATH_KEYS[key]
            if not parent:
                fs.append(Finding(FLAG, "death.role", "whose death certificate could not be told — check by eye",
                                  SRC, pg))
            elif f"{parent}_nid" in present:
                fs.append(Finding(NOTE, "death.stands_in", f"the {parent}'s NID is also given — the death "
                                                           "certificate is not used in its place", SRC, pg))
            else:
                fs.append(Finding(PASS, "death.stands_in", f"accepted in place of the {parent}'s NID", SRC, pg))
        elif key == "birth_cert":
            birth = _runner_mod("birth")
            names = _runner_mod("names")
            facts = births.get(tuple(u.pages))
            if facts is None:
                facts = birth.read_birth_certificate(u, pages, eng, work_for(u), dv.notarised)
            printed, printed_ok = names.printed_student_name(u, pages, facts)
            fs += list(_call_opt(birth.check_birth_certificate, facts, u, ident, printed, printed_ok, confirm,
                                 pages=pages))
            if facts.qr_name:
                where = facts.english_pages[0] if facts.english_pages else (u.pages[0] if u.pages else -1)
                machine.append(MachineRead(unit=u.uid, person="student", name=facts.qr_name, source="birth QR",
                                           page=where))
            qp = getattr(birth, "qr_parent_reads", None)          # rev 2.3: the parents' names in the payload
            if callable(qp):
                machine.extend(qp(facts, u))
            skip_qr = True
        elif key == "family_cert":
            fs += bot_findings(dv.check_family_cert(text, view, sizes, path), "check_family_cert", u, pages)
        elif key == "academic":
            fs += _academic_rows(dv.check_academic(text, view, sizes, path, texts), u, pages, mode)
            qf, mr = board_qr(u, pages)
            fs += qf
            machine.extend(mr)
            board_read = skip_qr = bool(qf)
            if (u.level or "").upper() == "SSC":
                fs += dob_rows(text, ident, u, pages, eng)
        elif key == "bank":
            rows = opening_balance_dropped(_drop(dv.check_bank(text, view, sizes, program, path, texts), DROP["bank"]))
            _assert_no_holder_rows(rows)
            fs += bot_findings(rows, "check_bank", u, pages)
            fs += passport_no_rows(text, ident, u, pages)
        elif key == "trade_license":
            rows = _drop(dv.check_financial(text, view, sizes, path, texts), DROP["trade_license"])
            rows = financial_kind_rows(rows, u, text, path, dv, pages)        # rev 2.4
            _assert_no_holder_rows(rows)
            fs += bot_findings(rows, "check_financial", u, pages)
            skip_qr = holds_tin_pages(u)
        elif key == "income_tax":
            fs += with_seal_evidence(bot_findings(dv.check_income_tax(text, view, sizes, program, path),
                                                  "check_income_tax", u, pages), text, path, dv, B.R)   # rev 2.5
        elif key == "language_cert":
            fs += language_rows(u, pages, eng, ident, program, today, aff, person_reads)
        elif key == "affidavit":
            fs.append(Finding(PASS, "doc.present", "affidavit present — used by the name rule", SRC, pg))
            fs += passport_no_rows(text, ident, u, pages)
        else:                                        # personal_statement, recommendation, eca, anything else
            fs.append(Finding(PASS, "doc.present", "present (no automatic rule)", SRC, pg))
        if key not in NO_PAGE_ROWS:
            try:
                prs = page_rows(u, text, eng, skip_qr, pages)
            except faults:
                raise
            except Exception as e:                   # a broken render or QR reader: not a document problem
                prs = [_error("page.checks", f"could not check the pages ({_why(e, path)})", u, pages)]
            if board_read:
                prs = [f for f in prs if not any(re.search(p, f.message) for p in DROP_BOARD_QR)]
            if not skip_qr:                          # rev 2.5: the QR ladder before "no QR" (issuing-office papers)
                prs = qr_ladder_rows(prs, u, pages)
            fs += prs
        return fs

    for u in todo:
        if u.key in NOT_CHECKED:
            continue
        try:
            fs = check_unit(u)
        except faults + (PortalLeak,):
            raise
        except Exception as e:                       # the file could not be checked (CheckError and the like)
            fs = [_error("doc.unreadable", f"could not check this file ({_why(e, u.path)})", u, pages)]
        if u.key == "passport" and not passport_noted:
            fs = fs + list(ident.notes)
            passport_noted = True
        per_unit[u.uid] = fs
    bundle = _bundle_colour(per_unit, units, pages) if mode == "bundle" else []
    if program != "MASTER":
        for u in units:
            if u.key == "income_tax":
                _income_tax_info(per_unit[u.uid], u, pages, program)
    return DocChecks(per_unit=per_unit, machine=machine, bundle=bundle, person_reads=person_reads)


def opening_balance_dropped(rows: List[Tuple[str, str]]) -> List[Tuple[str, str]]:
    """Owner rule 28 Oct: the opening-balance rule is dropped. Every row of the bot's opening_balance_check (PASS,
    NOTE or FLAG) is removed; when there was one, a single NOTE OPENING_BALANCE_NOTE takes its place (never a FLAG
    or FAIL)."""
    out: List[Tuple[str, str]] = []
    noted = False
    for v, m in rows:
        if any(re.search(p, m) for p in OPENING_BALANCE_ROWS):
            if not noted:
                out.append((NOTE, OPENING_BALANCE_NOTE))
                noted = True
            continue
        out.append((v, m))
    return out


def _assert_no_holder_rows(rows: List[Tuple[str, str]]) -> None:
    """The bot's holder / sponsor rows need portal values; with the empty record they cannot fire. One that
    does would mean a portal value leaked in (R1): stop the run rather than report it."""
    for _, m in rows:
        if any(re.search(p, m) for p in HOLDER_ROWS):
            raise PortalLeak("a bot holder/sponsor row fired with the empty portal record")


def _bundle_colour(per_unit: Dict[str, List[Finding]], units: List[DocUnit],
                   pages: List[PageRead]) -> List[Finding]:
    """Q15 / critique 19: when the colour check FAILs on every unit whose colour was measured, the bundle was
    scanned in grey: those rows are dropped and one bundle-level FLAG is returned. A single grey unit
    inside a colour scan keeps its own row."""
    measured = [(uid, f) for uid, fs in per_unit.items() for f in fs
                if f.rule == "page.colour" and f.verdict in (PASS, NOTE, FAIL)
                and not f.message.startswith(_NOT_MEASURED)]
    if not measured or any(f.verdict != FAIL for _, f in measured):
        return []
    grey = {id(f) for _, f in measured}
    pg = set()
    for uid in {uid for uid, _ in measured}:
        pg.update(p for f in per_unit[uid] if id(f) in grey for p in f.pages)
        per_unit[uid][:] = [f for f in per_unit[uid] if id(f) not in grey]
    return [Finding(FLAG, "page.colour_bundle", COLOUR_BUNDLE_TEXT, SRC, tuple(sorted(pg)),
                    {"units": len({uid for uid, _ in measured})})]


def _income_tax_info(fs: List[Finding], unit: DocUnit, pages: List[PageRead], program: str) -> None:
    """A6 h / critique 18: outside Master's the income-tax guideline is information only: every FLAG or FAIL of
    the bot's rows and of the page rows becomes NOTE, and the line is PRESENT."""
    for f in fs:
        if f.verdict in (FLAG, FAIL):
            f.evidence = dict(f.evidence or {}, was=f.verdict)
            f.verdict = NOTE
            f.message = f.message + INFO_SUFFIX
    fs.append(Finding(PRESENT, "doc.present_info", f"present — information only for {program}", SRC,
                      unit_pages(unit, pages)))
