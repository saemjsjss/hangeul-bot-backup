"""
Page-level checks that look at the scan itself rather than its text:
colour vs black-and-white, QR codes, and Bangla text needing translation.

Used by doc_verifier for the reviewer's rules:
  * "All documents must be provided as clear colour scans … black-and-white will not be accepted."
  * "Documents containing a QR code must be scanned clearly so the QR code can be scanned."
  * "If any required document is in Bangla, it must be translated into English."

A file that cannot be opened or drawn has not been checked, which is not the same as
failing a check: those cases raise CheckError (or, for inspect, set info["error"] so that
colour_check reports ERROR) and the file is checked again on the next pass.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from src.config import settings
from src.verify import rules as R
from src.verify.common import CheckError

# A fault in the code rather than in the file: never turned into an ERROR row, so that it
# stops the run instead of being retried forever.
_CODE_FAULTS = (NameError, AttributeError, ImportError, TypeError)


def _why(e: Exception, path: Path) -> str:
    """'<Type>: message', with the file's full path cut down to its name: the message ends
    up in the report and in Telegram, where the student's folder path is only noise."""
    msg = str(e)
    for full in (str(path), repr(str(path))[1:-1]):
        msg = msg.replace(full, path.name)
    return f"{type(e).__name__}: {msg}"


def _read_image(path: Path):
    """An image file as an OpenCV BGR image. cv2.imread cannot open a path holding
    non-ASCII letters on Windows, so when it fails the bytes are read by Python and
    decoded instead. Raises CheckError when the file cannot be read or decoded."""
    import cv2
    import numpy as np
    img = cv2.imread(str(path))
    if img is not None:
        return img
    try:
        data = np.fromfile(str(path), dtype=np.uint8)
    except OSError as e:
        raise CheckError(f"could not open {path.name} ({_why(e, path)})") from e
    img = cv2.imdecode(data, cv2.IMREAD_COLOR) if data.size else None
    if img is None:
        raise CheckError(f"could not decode the image {path.name}")
    return img


def _pages(path: Path, dpi: int = 250, max_pages: int = 4):
    """Each page of a PDF/image as an OpenCV BGR image."""
    import cv2
    import numpy as np
    out = []
    if path.suffix.lower() in (".jpg", ".jpeg", ".png"):
        return [_read_image(path)]
    import pymupdf
    doc = pymupdf.open(str(path))
    try:
        for i, page in enumerate(doc):
            if i >= max_pages:
                break
            pix = page.get_pixmap(dpi=dpi)
            a = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width, pix.n)
            out.append(cv2.cvtColor(a, cv2.COLOR_RGB2BGR) if pix.n == 3 else cv2.cvtColor(a, cv2.COLOR_RGBA2BGR))
    finally:
        doc.close()
    return out


def _render(path: Path, dpi: int, max_pages: int, what: str):
    """_pages, with any failure to open or draw the file raised as CheckError."""
    try:
        return _pages(path, dpi=dpi, max_pages=max_pages)
    except (CheckError, *_CODE_FAULTS):
        raise
    except Exception as e:
        raise CheckError(f"could not render {path.name} {what} ({_why(e, path)})") from e


def zbar_available() -> Tuple[bool, str]:
    """Whether the QR reader works: pyzbar imports and its zbar library decodes a blank
    image. (True, "available"), or (False, the reason)."""
    try:
        import numpy as np
        from pyzbar.pyzbar import decode
        decode(np.full((32, 32), 255, dtype=np.uint8))
    except Exception as e:
        return False, f"{type(e).__name__}: {e}"
    return True, "available"


def _zbar_decoder():
    """pyzbar's decode. A missing reader is a broken installation, not a document without
    a QR, so it raises CheckError instead of quietly reading nothing."""
    try:
        from pyzbar.pyzbar import decode
    except Exception as e:
        raise CheckError(f"pyzbar (QR reader) not available: {type(e).__name__}: {e}") from e
    return decode


def is_digital(path: Path) -> bool:
    """True when the file was produced by a computer rather than scanned from paper.

    A digitally produced PDF carries a real text layer; a scan carries only pixels.  This
    matters because "must be a clear colour scan" is a rule about photocopies of originals
    — a personal statement, a study plan or an online bank printout is black text on white
    by nature and was never scanned at all."""
    if path.suffix.lower() in (".jpg", ".jpeg", ".png"):
        return False                       # a photo or scan by definition
    try:
        import pymupdf
        doc = pymupdf.open(str(path))
    except Exception:
        return False
    try:
        pages = min(len(doc), 4)
        if not pages:
            return False
        with_text = 0
        for i in range(pages):
            words = doc[i].get_text("words")          # real text objects, not OCR
            if len(words) >= 25:
                with_text += 1
        return with_text >= max(1, pages // 2 + pages % 2)
    finally:
        doc.close()


def _clean_code(raw: bytes) -> str:
    """Barcode payloads are sometimes UTF-16 and often carry control characters that
    openpyxl refuses to write into a cell."""
    for enc in ("utf-8", "utf-16-le", "utf-16-be", "latin-1"):
        try:
            t = raw.decode(enc)
            if "\x00" not in t:
                break
        except Exception:
            continue
    else:
        t = raw.decode("utf-8", "replace")
    t = "".join(c for c in t if ord(c) >= 32 or c in "\n\t")
    return re.sub(r"\s+", " ", t).strip()


def inspect(path: Path) -> Dict[str, object]:
    """{'saturation': float, 'qr': [decoded strings], 'pages': n, 'digital': bool}, plus
    'error': "<Type>: message" when the pages could not be rendered."""
    import cv2
    info: Dict[str, object] = {"saturation": None, "qr": [], "qr_seen": False,
                               "pages": 0, "digital": False}
    try:
        info["digital"] = is_digital(path)
    except Exception:
        pass
    try:
        imgs = _pages(path)
    except _CODE_FAULTS:
        raise
    except Exception as e:
        info["error"] = _why(e, path)
        return info
    info["pages"] = len(imgs)
    if not imgs:
        return info
    _zbar = _zbar_decoder()                # pyzbar reads real scans; OpenCV often cannot
    det = cv2.QRCodeDetector()
    sats, codes, seen = [], [], False
    for img in imgs:
        hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)
        # share of pixels that are clearly coloured (ink, seals, photos) rather than
        # paper or grey text — a grayscale scan has almost none
        coloured = ((hsv[:, :, 1] > 70) & (hsv[:, :, 2] > 60)).mean() * 100
        sats.append(float(coloured))
        try:
            ok, decoded, _, _ = det.detectAndDecodeMulti(img)
            if ok:
                codes += [d for d in decoded if d]
        except Exception:
            pass
        try:
            for r in _zbar(img):
                data = _clean_code(r.data)
                if data:
                    codes.append(data)
                    seen = True
        except Exception:
            pass
        try:
            if det.detectMulti(img)[0]:       # a QR pattern is there, readable or not
                seen = True
        except Exception:
            pass
    info["saturation"] = max(sats) if sats else None
    info["qr"] = sorted(set(codes))
    info["qr_seen"] = seen or bool(codes)
    return info


def colour_check(info: Dict[str, object], doc_key: str = "") -> List[tuple]:
    pct = info.get("saturation")          # % of clearly coloured pixels
    if doc_key in R.TYPED_DOCS:
        return [("PASS", "typed document — the colour-scan rule is for photocopies of originals")]
    if info.get("digital"):
        return [("PASS", "produced on a computer, not scanned from paper — "
                         "the colour-scan rule does not apply")]
    if info.get("error"):
        return [("ERROR", f"could not render the pages to judge colour ({info['error']})")]
    if pct is None:
        return [("FLAG", "could not judge whether the scan is in colour")]
    if pct >= R.MIN_COLOUR_PERCENT:
        return [("PASS", f"colour scan ({pct:.2f}% coloured pixels)")]
    if pct >= R.MIN_COLOUR_PERCENT / 4:
        return [("NOTE", f"only {pct:.2f}% of the page is coloured — a pale stamp or a light "
                         "scan; worth a glance that it is not a photocopy")]
    return [("FAIL", f"looks like a black-and-white scan ({pct:.1f}% coloured pixels) — "
                     "reviewer requires clear colour scans")]


def qr_check(info: Dict[str, object], required: bool = False, text: str = "") -> List[tuple]:
    """The guideline says "documents containing a QR code must be scanned clearly so the QR
    code can be scanned" — a rule about legibility, not a rule that every document carries
    one.  A Union Parishad certificate issued by hand has no QR and never did."""
    low = (text or "").lower()
    if "taxpayer" in low or "tin certificate" in low or "etin" in low:
        return []                      # a TIN is checked by name against the trade licence
    if info.get("error"):
        # colour_check has reported it already, except on a computer-made file, where the
        # colour rule needed no pages and said nothing: the QR was not looked at either way
        if info.get("digital"):
            return [("ERROR", f"could not render the pages to read a QR code ({info['error']})")]
        return []
    codes = info.get("qr") or []
    if codes:
        return [("PASS", "QR code scans cleanly: " + ", ".join(c[:48] for c in codes[:2]))]
    if info.get("qr_seen"):
        return [("FLAG", "a QR code is on the page but could not be read — it must be "
                         "scanned clearly enough to scan")]
    if required and not any(w in (text or "").lower() for w in R.ONLINE_BIRTH_WORDS):
        return [("FLAG", "no QR code and nothing showing it is the online registration copy — "
                         "the guideline asks for the online version")]
    return [("PASS", "no QR code on this document — the rule covers documents that carry one")]


def bangla_check(text: str) -> List[tuple]:
    """Flag Bangla text that has no English translation alongside it."""
    bangla = len(re.findall(r"[ঀ-৿]", text or ""))
    latin = len(re.findall(r"[A-Za-z]", text or ""))
    if bangla > 40 and latin < bangla:
        has_tr = any(w in (text or "").lower() for w in R.TRANSLATION_WORDS)
        if has_tr:
            return [("FLAG", "Bangla document with translation wording — check it is on a lawyer pad")]
        return [("FAIL", "document appears to be in Bangla with no English translation attached")]
    return []


def passport_seal_check(path: Path) -> List[tuple]:
    """A notary seal on a passport is optional, but if it is there it must sit on the
    white margin around the passport scan — the red seal must not touch the passport
    image itself."""
    import cv2
    import numpy as np
    try:
        imgs = _pages(path, dpi=200, max_pages=2)
    except Exception as e:
        return [("FLAG", f"could not inspect the passport scan ({e})")]
    out = []
    for img in imgs:
        H, W = img.shape[:2]
        hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)
        red = ((hsv[:, :, 1] > 90) & (hsv[:, :, 2] > 70) &
               ((hsv[:, :, 0] < 10) | (hsv[:, :, 0] > 170))).astype("uint8")
        if red.sum() < 0.0005 * H * W:        # no meaningful red seal on this page
            continue
        # the passport scan: the large non-white block, with the red seal removed first
        grey = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        ink = ((grey < 225) & (red == 0)).astype("uint8")
        ink = cv2.morphologyEx(ink, cv2.MORPH_CLOSE, np.ones((15, 15), np.uint8))
        n, _, stats, _ = cv2.connectedComponentsWithStats(ink, 8)
        if n < 2:
            continue
        order = np.argsort(-stats[1:, cv2.CC_STAT_AREA]) + 1
        box = None
        for i in order[:4]:
            x, y, w, h, area = stats[i]
            if area < 0.04 * H * W:
                break
            if w * h > 0.95 * H * W:          # covers the whole sheet: not a passport block
                continue
            box = (x, y, w, h)
            break
        if box is None:
            out.append(("FLAG", "red seal found, but the passport area could not be separated "
                                "from the page — check by eye that the seal is on the white margin"))
            continue
        x, y, w, h = box
        pad = int(0.01 * max(H, W))           # ignore a hair's breadth at the edge
        inner = red[y + pad:y + h - pad, x + pad:x + w - pad]
        inside = int(inner.sum()) if inner.size else 0
        total = int(red.sum())
        share = inside / total if total else 0
        if share > 0.10:
            out.append(("FAIL", f"the red notary seal overlaps the passport image ({share * 100:.0f}% "
                                "of the seal) — it must sit on the white margin only"))
        else:
            out.append(("PASS", "red notary seal is on the white margin, clear of the passport"))
    return out


def annotate(path: Path, out_png: str) -> str:
    """Debug helper: draw the detected passport block (green) and red-seal pixels (blue)."""
    import cv2
    import numpy as np
    img = _pages(path, dpi=200, max_pages=1)[0]
    H, W = img.shape[:2]
    hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)
    red = ((hsv[:, :, 1] > 90) & (hsv[:, :, 2] > 70) &
           ((hsv[:, :, 0] < 10) | (hsv[:, :, 0] > 170))).astype("uint8")
    grey = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    ink = ((grey < 225) & (red == 0)).astype("uint8")
    ink = cv2.morphologyEx(ink, cv2.MORPH_CLOSE, np.ones((15, 15), np.uint8))
    n, _, stats, _ = cv2.connectedComponentsWithStats(ink, 8)
    order = np.argsort(-stats[1:, cv2.CC_STAT_AREA]) + 1
    vis = img.copy()
    vis[red > 0] = (255, 0, 0)
    for i in order[:4]:
        x, y, w, h, area = stats[i]
        if area < 0.04 * H * W:
            break
        if w * h > 0.95 * H * W:
            continue
        cv2.rectangle(vis, (x, y), (x + w, y + h), (0, 255, 0), 6)
        break
    cv2.imwrite(out_png, vis)
    return out_png


def seal_present(path: Path) -> bool:
    """True when the page carries a notary-style seal: a violet/purple round stamp or a
    red wax seal. Stamp text is usually rotated and overlaps print, so OCR misses it —
    the colour is far more reliable."""
    import cv2
    import numpy as np
    imgs = _render(path, dpi=150, max_pages=4, what="to look for a seal")
    for img in imgs:
        H, W = img.shape[:2]
        hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)
        violet = ((hsv[:, :, 0] > 115) & (hsv[:, :, 0] < 165) &
                  (hsv[:, :, 1] > 60) & (hsv[:, :, 2] > 60)).astype("uint8")
        red = ((hsv[:, :, 1] > 90) & (hsv[:, :, 2] > 70) &
               ((hsv[:, :, 0] < 10) | (hsv[:, :, 0] > 170))).astype("uint8")
        for mask, need in ((violet, 0.0008), (red, 0.0015)):
            m = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, np.ones((9, 9), np.uint8))
            n, _, stats, _ = cv2.connectedComponentsWithStats(m, 8)
            for i in range(1, n):
                if stats[i, cv2.CC_STAT_AREA] > need * H * W:
                    return True
    return False


# --- academic documents: e-Apostille and the certificate that follows it ----------------
APOSTILLE_SUBJECTS = settings.verification_dir() / "apostille.json"


def apostille_subject(url: str) -> Optional[str]:
    """What qualification an apostille covers, if it has been looked up before.

    The apostille page itself never names the exam — only its QR does, and that page is a
    JavaScript app, so the subject is resolved separately and cached here as
    {application id: "HSC"}.  Missing simply means "not looked up yet"."""
    import json
    import re as _re
    try:
        data = json.loads(APOSTILLE_SUBJECTS.read_text(encoding="utf-8"))
    except Exception:
        return None
    m = _re.search(r"/([0-9]{6,})\s*$", (url or "").strip())
    return data.get(m.group(1)) if m else None


APOSTILLE_HOSTS = ("apostille.mygov.bd", "mofa-servicedirect", "apostille")

# What each page says it is.  HSC is tested before SSC because "Higher Secondary School
# Certificate" contains the SSC wording.
LEVELS = [
    ("HSC", r"higher\s+secondary|\bh\.?\s?s\.?\s?c\b|\balim\b|intermediate\s+certificate"),
    ("SSC", r"secondary\s+school\s+certificate|\bs\.?\s?s\.?\s?c\b|\bdakhil\b"),
    ("DIPLOMA", r"\bdiploma\b|polytechnic|technical\s+education\s+board"),
    # "M.A." needs its dot: a bare "MA" is as often a name ("MA SAMPLE") as a degree
    ("MASTER", r"\bmaster'?s?\b|\bm\.?\s?sc\b|\bmba\b|\bm\.\s?a\b\.?"),
    ("BACHELOR", r"\bbachelor'?s?\b|\bb\.?\s?sc\b|\bb\.?\s?a\b|\bb\.?\s?com\b|\bbba\b|honours|\bhons\b"),
]


def page_codes(path: Path, max_pages: int = 20) -> List[List[str]]:
    """Codes decoded on each page, in page order. Raises CheckError when the file cannot
    be opened or rendered, or the QR reader is missing."""
    import cv2
    zbar = _zbar_decoder()
    out: List[List[str]] = []
    imgs = _render(path, dpi=250, max_pages=max_pages, what="to read its QR codes")
    det = cv2.QRCodeDetector()
    for img in imgs:
        codes = []
        try:
            codes += [_clean_code(r.data) for r in zbar(img) if r.data]
        except Exception:
            pass
        if not codes:
            try:
                ok, decoded, _, _ = det.detectAndDecodeMulti(img)
                if ok:
                    codes += [d for d in decoded if d]
            except Exception:
                pass
        out.append(codes)
    return out


def level_of(text: str) -> Optional[str]:
    """Which qualification a page is about, or None."""
    t = (text or "").lower()
    for name, pattern in LEVELS:
        if re.search(pattern, t):
            return name
    return None


def academic_check(path: Path, pages_text: List[str]) -> List[tuple]:
    """The academic file must carry an e-Apostille with a readable QR, and the certificate
    and transcript that follow each apostille must be for the same qualification."""
    out: List[tuple] = []
    codes = page_codes(path)
    apostilles = [i for i, cs in enumerate(codes)
                  if any(any(h in c.lower() for h in APOSTILLE_HOSTS) for c in cs)]
    if not apostilles:
        has_wording = any(re.search(r"apostille", t or "", re.I) for t in pages_text)
        if has_wording:
            out.append(("FAIL", "an e-Apostille page is there but its QR could not be read — "
                                "the apostille QR must be scannable"))
        else:
            out.append(("FAIL", "no e-Apostille with a scannable QR — the certificate and "
                                "transcript must be apostilled through mygov.bd"))
        return out

    links = [c for i in apostilles for c in codes[i]
             if any(h in c.lower() for h in APOSTILLE_HOSTS)]
    out.append(("PASS", f"{len(apostilles)} e-Apostille QR code(s) read: " +
                        ", ".join(l[:52] for l in links[:3])))

    # Page 1 must be an e-Apostille.  The file is assembled apostille-first, and anything
    # else means the student has put it together in the wrong order — a hard no, whatever
    # the rest of the file contains.
    page1_fail = apostilles[0] != 0
    if page1_fail:
        out.append(("FAIL", f"page 1 is not an e-Apostille — the first apostille is on page "
                            f"{apostilles[0] + 1}. The file must start with the apostille, "
                            "followed by the certificate and transcript it covers"))

    # Each apostille owns the certificate and transcript that follow it, up to the next.
    bounds = apostilles + [len(pages_text)]
    for n, start in enumerate(apostilles):
        block = list(range(start + 1, min(bounds[n + 1], len(pages_text))))
        named = [(i + 1, level_of(pages_text[i])) for i in block]
        named = [(pg, lv) for pg, lv in named if lv]

        link = next((c for c in codes[start]
                     if any(h in c.lower() for h in APOSTILLE_HOSTS)), "")
        subject = apostille_subject(link)

        if not block:
            # the page-1 FAIL already says the apostille must come first, followed by the
            # certificate and transcript it covers: saying it again as a FLAG adds nothing
            if not page1_fail:
                out.append(("FLAG", f"the e-Apostille on page {start + 1} is not followed by the "
                                    "certificate and transcript it covers"))
            continue
        if not named:
            out.append(("NOTE", f"pages {block[0] + 1}-{block[-1] + 1} could not be read well "
                                f"enough to name the qualification behind the e-Apostille on "
                                f"page {start + 1}"))
            continue

        levels = {lv for _, lv in named}
        where = ", ".join(str(pg) for pg, _ in named)
        # FLAG, not FAIL: a transcript that names two exams reads as "mixed" (HANDOFF §5.1);
        # it stays a FLAG until proven against real documents (HANDOFF §8.1).
        if len(levels) > 1:
            detail = ", ".join(f"page {pg}: {lv}" for pg, lv in named)
            out.append(("FLAG", f"the pages after the e-Apostille on page {start + 1} are not all "
                                f"for one qualification ({detail}) — each apostille covers one "
                                f"certificate and one transcript"))
            continue

        found = levels.pop()
        if subject and subject != found:
            out.append(("FAIL", f"the e-Apostille on page {start + 1} was issued for {subject}, "
                                f"but page {where} is a {found} document — the certificate and "
                                f"transcript must match the apostille"))
        elif subject:
            out.append(("PASS", f"e-Apostille on page {start + 1} is for {subject} and page "
                                f"{where} is the matching {found} certificate/transcript"))
        else:
            out.append(("PASS", f"e-Apostille on page {start + 1} is followed by {found} "
                                f"certificate/transcript (page {where})"))
    return out
