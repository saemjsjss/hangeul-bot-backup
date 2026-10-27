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


_PAGE_REF = re.compile(r"\b(pages?)\s+(\d+)(?:\s*([-–])\s*(\d+))?\b")


def part_page_refs(message: str, unit: DocUnit, pages: Optional[Sequence[PageRead]]) -> str:
    """Folder mode, revision 2.3: a unit cut out of a file that holds several documents (DocUnit.part) is read by
    the bot as its own file, so a bot message's "page N" is the N-th page of the cut; it becomes the page inside the
    input file, as every other folder message names it. Any other unit: the message unchanged."""
    if not getattr(unit, "part", "") or not pages or not message:
        return message

    def one(k: str) -> str:
        n = int(k)
        return str(input_page(unit.pages[n - 1], pages)) if 0 < n <= len(unit.pages) else k

    def rep(m: "re.Match") -> str:
        word, a, dash, b = m.groups()
        return f"{word} {one(a)}" + (f"{dash}{one(b)}" if b else "")

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


def dob_rows(text: str, ident: Identity, unit: DocUnit, pages: Optional[List[PageRead]] = None) -> List[Finding]:
    """doc.dob_consistency: the labelled date of birth on the student NID or the SSC certificate against the
    passport's TRUSTED date. PASS / FLAG, never FAIL; nothing when either is unread."""
    if not (ident.dob and ident.dob_trusted):
        return []
    d = _bot().dv.labelled_dob(text or "")
    if not d:
        return []
    pg = unit_pages(unit, pages)
    if d == ident.dob:
        return [Finding(PASS, "doc.dob_consistency", f"date of birth {d} on the {unit.title or unit.key} is the "
                                                     f"passport's ({ident.dob_source or 'MRZ'})", SRC, pg)]
    return [Finding(FLAG, "doc.dob_consistency", f"the {unit.title or unit.key} gives date of birth {d}, the passport "
                                                 f"{ident.dob} ({ident.dob_source or 'MRZ'}) — check by eye", SRC, pg)]


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
def _academic_rows(rows: List[Tuple[str, str]], unit: DocUnit, pages: List[PageRead], mode: str) -> List[Finding]:
    keep, order = [], []
    for v, m in rows:
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
    return bot_findings(keep, "check_academic", unit, pages) + order


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
            if key == "student_nid":
                fs += dob_rows(text, ident, u, pages)
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
                fs += dob_rows(text, ident, u, pages)
        elif key == "bank":
            rows = _drop(dv.check_bank(text, view, sizes, program, path, texts), DROP["bank"])
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
            fs += bot_findings(dv.check_income_tax(text, view, sizes, program, path), "check_income_tax", u, pages)
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
