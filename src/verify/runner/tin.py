"""
The TIN certificate: its QR read by the system and checked against the print (RUNNER_SPEC 5.9.3; a port of the
lab's tin_qr.py; owner decisions A1 and A6 i/j, critique items 2 and 20 of 27 Oct 2026).

Owner rule (27 Oct 2026): "tin qr must be checked by system and with the qr result it must match whats on the
actual tin and if its name is 100% matched by trade or employment".

What it does
  read_qr(path)            every QR payload on every page of a PDF / image, with the stage that read it
  parse_tin_qr(payload)    the NBR e-TIN QR ("Taxpayer's Name : .. DOB : .. Father's Name : .. TIN : ..
                           Date : .. Zone : .. Circle : ..") as a dict, or None
  printed_tin_fields(text) what the printed certificate says (OCR text): TIN, the "certify that X is a
                           Registered Taxpayer" name, "1) Name", "2) Father's Name", "3) Mother's Name"
  compare(qr, printed)     field by field: the TIN exactly; the names with strict.compare (letters, dots aside)
  check_tin_certificate    the above for one TIN sub-PDF, judged (A1: a confirmed difference = FAIL "may be altered")
  tin_findings             model.Finding rows: tin.qr, tin.qr_tin, tin.qr_name, tin.qr_father, tin.web_address (NEW)
  web_hosts / tax_office   the web address the certificate was printed from; the tax circle and zone

The TIN QR is the PRIMARY taxpayer source (A6 i): check_tin_certificate(...)["qr"]["name"].

Decode ladder, kept from the lab (a page stops climbing once it has a meaningful payload: a TIN QR, a URL
or 8+ characters; 1-3 character reads are junk from the Aruco detector and are dropped):
  A  whole page at 250 dpi: cv2.QRCodeDetector multi + cv2.QRCodeDetectorAruco multi + pyzbar, united
  B  candidate boxes, most QR-like first (texture_score), 8 s budget per page: OpenCV detect points,
     a texture-blob search (_blob_boxes: finds dense codes nothing else sees) and a finder-pattern
     search; each box resized to a ladder of sizes (as is, 300, 600, 450, 900, 750, 375, 1200 px)
     with gray / Otsu / CLAHE+Otsu / adaptive / close / open / sharpen / red-channel variants,
     pyzbar then cv2 then Aruco
  E  the 4 densest-texture square windows (12-22% of the page width), each grown x1.5 / x2.1 / x1
     around its centre, through the same box ladder      (deep=True; read the one TIN QR that no
     detector boxed - a code under a pink seal tint)
  C  the page again at 400 dpi through A and B          (deep=True: only for an expected QR page)
  D  overlapping 3x3 tiles at 250 dpi, x1 and x2         (deep=True)
Measured on the 228 real financial files (201 holding a TIN certificate): A alone 193/201 (96.0%),
A then B/E/C/D on the files A missed 201/201 (100%).

Changed from the lab (the only changes): the loose token matcher and its name-agreement kinds are gone
(strict.compare / strict.judge instead, critique 2); the e-return QR parser and the "other QR codes" note
are gone (critique 20); check_tin_certificate takes the shared re-read (`confirm`) and, to use it, the
unit's page texts and run pages; tin_findings, tin_page_types, web_hosts and tax_office are new.
Source PDFs are opened read-only; nothing is written; no network. No personal data in this file.
"""
from __future__ import annotations

import re
import time
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import cv2
import numpy as np

from . import hooks

try:
    from pyzbar.pyzbar import decode as _zbar_decode, ZBarSymbol
    _ZBAR = True
except Exception:                                    # the lab venvs have no pyzbar
    _ZBAR = False

_CV = cv2.QRCodeDetector()
_AR = cv2.QRCodeDetectorAruco() if hasattr(cv2, "QRCodeDetectorAruco") else None


# --- rendering -----------------------------------------------------------------------------
def render(path, dpi: int = 250, max_pages: int = 20) -> List[np.ndarray]:
    """Each page as a BGR image. PDFs are opened read-only with PyMuPDF."""
    p = Path(path)
    if p.suffix.lower() in (".jpg", ".jpeg", ".png"):
        data = np.fromfile(str(p), dtype=np.uint8)
        img = cv2.imdecode(data, cv2.IMREAD_COLOR)
        return [img] if img is not None else []
    import pymupdf
    doc = pymupdf.open(str(p))
    out = []
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


def render_page(path, index: int, dpi: int) -> Optional[np.ndarray]:
    import pymupdf
    p = Path(path)
    if p.suffix.lower() in (".jpg", ".jpeg", ".png"):
        imgs = render(p)
        if not imgs:
            return None
        if dpi > 250:
            f = dpi / 250
            return cv2.resize(imgs[0], None, fx=f, fy=f, interpolation=cv2.INTER_CUBIC)
        return imgs[0]
    doc = pymupdf.open(str(p))
    try:
        pix = doc[index].get_pixmap(dpi=dpi)
        a = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width, pix.n)
        return cv2.cvtColor(a, cv2.COLOR_RGB2BGR) if pix.n == 3 else cv2.cvtColor(a, cv2.COLOR_RGBA2BGR)
    finally:
        doc.close()


# --- payload clean-up ----------------------------------------------------------------------
def _clean(raw) -> str:
    if isinstance(raw, bytes):
        for enc in ("utf-8", "utf-16-le", "utf-16-be", "latin-1"):
            try:
                t = raw.decode(enc)
                if "\x00" not in t:
                    break
            except Exception:
                continue
        else:
            t = raw.decode("utf-8", "replace")
    else:
        t = str(raw)
    t = t.replace("\r\n", "\n").replace("\r", "\n")
    return "".join(c for c in t if ord(c) >= 32 or c in "\n\t").strip()


# --- decoders ------------------------------------------------------------------------------
def _gray(img: np.ndarray) -> np.ndarray:
    return img if img.ndim == 2 else cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)


def _dec_cv_multi(img, det) -> Tuple[List[str], list]:
    if det is None:
        return [], []
    try:
        ok, decoded, pts, _ = det.detectAndDecodeMulti(img)
    except Exception:
        return [], []
    codes = [_clean(d) for d in (decoded or []) if d]
    return codes, (list(pts) if pts is not None else [])


def _dec_cv_single(img, det) -> List[str]:
    if det is None:
        return []
    try:
        data, _, _ = det.detectAndDecode(img)
    except Exception:
        return []
    return [_clean(data)] if data else []


def _dec_zbar(img) -> List[str]:
    if not _ZBAR:
        return []
    try:
        res = _zbar_decode(_gray(img), symbols=[ZBarSymbol.QRCODE])
    except Exception:
        return []
    return [_clean(r.data) for r in res if r.data]


def _real(codes: List[str]) -> List[str]:
    """Drop junk decodes: QRCodeDetectorAruco sometimes 'reads' 1-3 characters out of noise, and
    that false read must never stop the ladder before pyzbar reads the real code."""
    return [c for c in codes if c and len(c.strip()) >= 4]


def meaningful(codes: List[str]) -> bool:
    """A payload worth stopping for: a TIN QR, a URL, or any payload of 8+ characters (an e-return QR is
    one of these: its own parser was removed, critique 20)."""
    return any(parse_tin_qr(c) or re.match(r"https?://", c, re.I) or len(c) >= 8 for c in codes)


def _decode_all(img) -> Tuple[List[str], str]:
    """Three decoders on one image; (payloads, which decoder)."""
    for name, fn in (("zbar", lambda: _dec_zbar(img)),
                     ("cv", lambda: _dec_cv_single(img, _CV)),
                     ("aruco", lambda: _dec_cv_single(img, _AR))):
        codes = _real(fn())
        if codes:
            return codes, name
    return [], ""


# --- candidate regions ---------------------------------------------------------------------
def _detect_boxes(img) -> List[Tuple[int, int, int, int]]:
    """Boxes (x0, y0, x1, y1) where a QR may be: OpenCV's detectors plus finder patterns."""
    boxes = []
    for det in (_CV, _AR):
        if det is None:
            continue
        try:
            ok, pts = det.detectMulti(img)
        except Exception:
            ok, pts = False, None
        if ok and pts is not None:
            for q in pts:
                q = np.asarray(q).reshape(-1, 2)
                boxes.append((int(q[:, 0].min()), int(q[:, 1].min()), int(q[:, 0].max()), int(q[:, 1].max())))
    boxes += _blob_boxes(img)
    boxes += _finder_boxes(img)
    return _merge_boxes(boxes)


def _blob_boxes(img) -> List[Tuple[int, int, int, int]]:
    """Square blocks of fine dark/light texture (a QR closes into a solid square; text closes into
    long bars). Works on dense, blurred codes where neither OpenCV detector nor the finder search
    sees the three corner squares."""
    g = _gray(img)
    h, w = g.shape
    s = 1000 / w
    small = cv2.resize(g, None, fx=s, fy=s, interpolation=cv2.INTER_AREA)
    _, bw = cv2.threshold(small, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
    cl = cv2.morphologyEx(bw, cv2.MORPH_CLOSE, cv2.getStructuringElement(cv2.MORPH_RECT, (9, 9)))
    cl = cv2.morphologyEx(cl, cv2.MORPH_OPEN, cv2.getStructuringElement(cv2.MORPH_RECT, (15, 15)))
    cnts, _ = cv2.findContours(cl, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    out = []
    for c in cnts:
        x, y, cw, ch = cv2.boundingRect(c)
        if not (0.7 < cw / ch < 1.4) or not (50 <= max(cw, ch) <= 400):
            continue
        if cv2.contourArea(c) < 0.6 * cw * ch:
            continue
        dark = bw[y:y + ch, x:x + cw].mean() / 255          # a QR is roughly half dark
        if not (0.3 < dark < 0.75):
            continue
        out.append(tuple(int(v / s) for v in (x, y, x + cw, y + ch)))
    return out


def _finder_boxes(img) -> List[Tuple[int, int, int, int]]:
    """QR finder patterns: a dark square holding a light square holding a dark square (three
    nested contours, about 7:5:3). Groups of 2+ similar finders close together give a box."""
    g = _gray(img)
    h, w = g.shape
    scale = 1.0
    if max(h, w) > 3000:
        scale = 3000 / max(h, w)
        g = cv2.resize(g, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)
    _, bw = cv2.threshold(cv2.GaussianBlur(g, (3, 3), 0), 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
    cnts, hier = cv2.findContours(bw, cv2.RETR_TREE, cv2.CHAIN_APPROX_SIMPLE)
    if hier is None:
        return []
    hier = hier[0]
    finders = []
    for i, c in enumerate(cnts):
        child = hier[i][2]
        if child < 0 or hier[child][2] < 0:
            continue
        x, y, cw, ch = cv2.boundingRect(c)
        if cw < 8 or ch < 8 or not (0.7 < cw / ch < 1.4):
            continue
        area = cv2.contourArea(c)
        if area < 0.6 * cw * ch:
            continue
        gx, gy, gw, gh = cv2.boundingRect(cnts[hier[child][2]])
        if gw <= 0 or not (0.2 < gw / cw < 0.65):
            continue
        finders.append((x + cw / 2, y + ch / 2, (cw + ch) / 2))
    boxes = []
    used = set()
    for i, (x, y, s) in enumerate(finders):
        group = [(x, y, s)]
        for j, (x2, y2, s2) in enumerate(finders):
            if j == i or j in used:
                continue
            if 0.6 < s2 / s < 1.6 and abs(x2 - x) < 12 * s and abs(y2 - y) < 12 * s:
                group.append((x2, y2, s2))
        if len(group) >= 2:
            xs = [p[0] for p in group]
            ys = [p[1] for p in group]
            m = max(p[2] for p in group)
            x0, y0 = min(xs) - 1.2 * m, min(ys) - 1.2 * m
            x1, y1 = max(xs) + 1.2 * m, max(ys) + 1.2 * m
            if len(group) == 2:          # the third finder is missing: grow to a square
                side = max(x1 - x0, y1 - y0)
                x1, y1 = x0 + side, y0 + side
                boxes.append(tuple(int(v / scale) for v in (x0 - side * 0.5, y0 - side * 0.5, x1 + side * 0.5, y1 + side * 0.5)))
            else:
                boxes.append(tuple(int(v / scale) for v in (x0, y0, x1, y1)))
    return boxes


def _merge_boxes(boxes):
    out = []
    for b in sorted(boxes, key=lambda b: -(b[2] - b[0]) * (b[3] - b[1])):
        if b[2] - b[0] < 20 or b[3] - b[1] < 20:
            continue
        if any(b[0] >= o[0] - 10 and b[1] >= o[1] - 10 and b[2] <= o[2] + 10 and b[3] <= o[3] + 10 for o in out):
            continue
        out.append(b)
    return out[:8]


def texture_score(img, box) -> float:
    """Mean dark/light transitions per row and column of the box at 200x200: a QR scores highest
    (about 30-45 on real scans), seals, photos and logos lower. Used to try likely boxes first."""
    x0, y0, x1, y1 = box
    g = _gray(img[max(0, y0):y1, max(0, x0):x1])
    if g.size == 0:
        return 0.0
    g = cv2.resize(g, (200, 200), interpolation=cv2.INTER_AREA)
    _, bw = cv2.threshold(g, 0, 1, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    b = bw.astype(np.int16)
    return float((np.abs(np.diff(b, axis=1)).sum(axis=1).mean() + np.abs(np.diff(b, axis=0)).sum(axis=0).mean()) / 2)


def _variants(crop: np.ndarray):
    """Crop variants, cheapest and most often successful first."""
    g = _gray(crop)
    yield "gray", g
    blur = cv2.GaussianBlur(g, (3, 3), 0)
    _, otsu = cv2.threshold(blur, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    yield "otsu", otsu
    clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(4, 4)).apply(g)
    _, co = cv2.threshold(clahe, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    yield "clahe_otsu", co
    yield "adaptive", cv2.adaptiveThreshold(g, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY, 31, 10)
    k = cv2.getStructuringElement(cv2.MORPH_RECT, (2, 2))
    yield "otsu_close", cv2.morphologyEx(otsu, cv2.MORPH_CLOSE, k)
    yield "otsu_open", cv2.morphologyEx(otsu, cv2.MORPH_OPEN, k)
    sharp = cv2.addWeighted(g, 1.8, cv2.GaussianBlur(g, (0, 0), 2), -0.8, 0)
    yield "sharpen", sharp
    if crop.ndim == 3:
        r = crop[:, :, 2]
        _, ro = cv2.threshold(r, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
        yield "red_otsu", ro          # drops red/pink stamps over the code


def _try_box(img, box) -> Tuple[List[str], str]:
    h, w = img.shape[:2]
    x0, y0, x1, y1 = box
    mx, my = int((x1 - x0) * 0.15) + 8, int((y1 - y0) * 0.15) + 8
    x0, y0, x1, y1 = max(0, x0 - mx), max(0, y0 - my), min(w, x1 + mx), min(h, y1 + my)
    crop = img[y0:y1, x0:x1]
    if crop.size == 0:
        return [], ""
    side0 = max(crop.shape[:2])
    # Decoding a scanned QR is very sensitive to its pixel size: one dense TIN code read only when
    # its box was resized to ~300 or ~600 px, never at 400/500/750. So a ladder of target sizes
    # (downscaling with INTER_AREA also averages the scan noise away), the box as it is first.
    for target in (None, 300, 600, 450, 900, 750, 375, 1200):
        f = 1.0 if target is None else target / side0
        if target is not None and (abs(f - 1) < 0.08 or side0 * f < 120):
            continue
        sc = crop if f == 1.0 else cv2.resize(crop, None, fx=f, fy=f,
                                              interpolation=cv2.INTER_CUBIC if f > 1 else cv2.INTER_AREA)
        sc = cv2.copyMakeBorder(sc, 20, 20, 20, 20, cv2.BORDER_CONSTANT, value=(255, 255, 255))
        for vname, v in _variants(sc):
            codes, dname = _decode_all(v)
            if codes:
                return codes, f"{'orig' if target is None else target}px/{vname}/{dname}"
    return [], ""


def _stage_a(img) -> Tuple[List[str], str]:
    """All three whole-page decoders, united (each misses codes the others read)."""
    got, who = [], []
    for name, codes in (("cv_multi", _dec_cv_multi(img, _CV)[0]), ("aruco_multi", _dec_cv_multi(img, _AR)[0]),
                        ("zbar", _dec_zbar(img))):
        codes = _real(codes)
        if codes:
            who.append(name)
            got += [c for c in codes if c not in got]
    return got, ("A/" + "+".join(who)) if got else ""


def _stage_b(img, budget: float = 8.0) -> Tuple[List[str], str, int]:
    """Candidate boxes, most QR-like first, within a time budget per page (a page with a photo,
    a seal and a logo but no QR must not cost a minute)."""
    t0 = time.perf_counter()
    boxes = sorted(_detect_boxes(img), key=lambda b: -texture_score(img, b))
    for b in boxes:
        if time.perf_counter() - t0 > budget:
            hooks.emit("budget", where="tin.stage_b", page=None)    # record only (APP_SPEC rev 1 section 10 item 7)
            break
        codes, how = _try_box(img, b)
        if codes:
            return codes, "B/" + how, len(boxes)
    return [], "", len(boxes)


def _stage_d(img) -> Tuple[List[str], str]:
    h, w = img.shape[:2]
    th, tw = h // 2, w // 2
    for ty in (0, h // 4, h // 2):
        for tx in (0, w // 4, w // 2):
            tile = img[ty:ty + th, tx:tx + tw]
            for f in (1.0, 2.0):
                t = tile if f == 1 else cv2.resize(tile, None, fx=f, fy=f, interpolation=cv2.INTER_CUBIC)
                codes, dname = _decode_all(t)
                if codes:
                    return codes, f"D/tile x{f:g}/{dname}"
    return [], ""


def _texture_windows(img, top: int = 6) -> List[Tuple[int, int, int, int]]:
    """Square windows (12-22% of the page width) with the densest dark/light texture, best first,
    not overlapping. Finds a code that no detector boxes: one real TIN QR under a pink seal tint
    was missed by OpenCV, the blob search and the finder search, and read at once from a crop."""
    g = _gray(img)
    h, w = g.shape
    s = 800 / w
    small = cv2.resize(g, None, fx=s, fy=s, interpolation=cv2.INTER_AREA)
    bw = cv2.adaptiveThreshold(small, 1, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY, 15, 5).astype(np.int16)
    tx = np.abs(np.diff(bw, axis=1))
    ty = np.abs(np.diff(bw, axis=0))
    ih, iw = small.shape
    ix = np.pad(tx, ((0, 0), (1, 0))).cumsum(0).cumsum(1)
    iy = np.pad(ty, ((1, 0), (0, 0))).cumsum(0).cumsum(1)

    def rect(ii, x0, y0, x1, y1):
        return ii[y1 - 1, x1 - 1] - (ii[y0 - 1, x1 - 1] if y0 else 0) - (ii[y1 - 1, x0 - 1] if x0 else 0) \
            + (ii[y0 - 1, x0 - 1] if x0 and y0 else 0)

    cands = []
    for frac in (0.12, 0.16, 0.22):
        side = int(iw * frac)
        step = max(8, side // 4)
        for y in range(0, ih - side, step):
            for x in range(0, iw - side, step):
                d = (rect(ix, x, y, x + side, y + side) + rect(iy, x, y, x + side, y + side)) / (side * side)
                cands.append((d, x, y, side))
    cands.sort(reverse=True)
    out: List[Tuple[int, int, int, int]] = []
    for d, x, y, side in cands:
        b = (x, y, x + side, y + side)
        if any(not (b[2] <= o[0] or b[0] >= o[2] or b[3] <= o[1] or b[1] >= o[3]) for o in out):
            continue
        out.append(b)
        if len(out) >= top:
            break
    return [tuple(int(v / s) for v in b) for b in out]


def _stage_e(img, budget: float = 12.0) -> Tuple[List[str], str]:
    t0 = time.perf_counter()
    h, w = img.shape[:2]
    for b in _texture_windows(img, top=4):
        cx, cy, side = (b[0] + b[2]) / 2, (b[1] + b[3]) / 2, b[2] - b[0]
        # the densest window is often only the middle of the code: try it grown around its centre
        for grow in (1.5, 2.1, 1.0):
            if time.perf_counter() - t0 > budget:
                hooks.emit("budget", where="tin.stage_e", page=None)
                return [], ""
            half = side * grow / 2
            box = (max(0, int(cx - half)), max(0, int(cy - half)), min(w, int(cx + half)), min(h, int(cy + half)))
            codes, how = _try_box(img, box)
            if codes:
                return codes, f"E/x{grow:g}/" + how
    return [], ""


def read_page(path, index: int, img250: Optional[np.ndarray] = None, deep: bool = True) -> Dict[str, object]:
    """{'codes': [...], 'stage': str, 'candidates': n, 'seconds': s} for one page."""
    t0 = time.perf_counter()
    img = img250 if img250 is not None else render_page(path, index, 250)
    found: List[str] = []
    stages: List[str] = []

    def add(codes, how):
        new = [c for c in codes if c not in found]
        if new:
            found.extend(new)
            stages.append(how)

    add(*_stage_a(img))
    cands = 0
    if not meaningful(found):
        codes, how, cands = _stage_b(img)
        add(codes, how)
    if not meaningful(found) and deep:
        add(*_stage_e(img))
    if not meaningful(found) and deep:
        hi = render_page(path, index, 400)
        if hi is not None:
            codes, how = _stage_a(hi)
            add(codes, "C/" + how)
            if not meaningful(found):
                codes, how, c2 = _stage_b(hi)
                cands = max(cands, c2)
                add(codes, "C/" + how)
    if not meaningful(found) and deep:
        add(*_stage_d(img))
    return {"codes": found, "stage": ";".join(stages), "candidates": cands,
            "seconds": round(time.perf_counter() - t0, 2)}


def read_qr(path, max_pages: int = 20, deep: bool = True) -> List[Dict[str, object]]:
    """One entry per page: {'page': i, 'codes': [...], 'stage': ..., ...}."""
    out = []
    for i, img in enumerate(render(path, 250, max_pages)):
        r = read_page(path, i, img, deep=deep)
        r["page"] = i
        out.append(r)
    return out


# --- payload parsing -----------------------------------------------------------------------
_TIN_LABELS = [("name", r"Tax\s*payer'?s?\s*Name"), ("dob", r"DOB|Date\s*of\s*Birth"),
               ("father", r"Father'?s?\s*Name"), ("tin", r"TIN"), ("date", r"Date"),
               ("zone", r"Zone"), ("circle", r"Circle")]


def parse_tin_qr(payload: str) -> Optional[Dict[str, str]]:
    """The NBR e-TIN certificate QR: labelled fields in a fixed order, separated by newlines or
    (after some readers) by single spaces. None when it is not that QR."""
    if not payload or not re.search(r"Tax\s*payer'?s?\s*Name\s*:", payload, re.I):
        return None
    pos, found = 0, []
    for key, lab in _TIN_LABELS:
        # a label starts the payload, follows white space, or follows the previous value's last
        # digit with nothing between ("Date : 01/02/2019Zone : ..." in 25 of 205 payloads)
        m = re.compile(r"(?:^|(?<=\s)|(?<=\d))(" + lab + r")\s*:", re.I).search(payload, pos)
        if not m:
            continue
        found.append((key, m.start(1), m.end()))
        pos = m.end()
    out: Dict[str, str] = {}
    for i, (key, start, vstart) in enumerate(found):
        vend = found[i + 1][1] if i + 1 < len(found) else len(payload)
        out[key] = re.sub(r"\s+", " ", payload[vstart:vend]).strip(" ,;")
    if "tin" in out:
        out["tin"] = re.sub(r"\D", "", out["tin"])
    return out


# --- the printed certificate (OCR text) ----------------------------------------------------
_NAME_CHARS = r"[A-Za-z.\s'`:,-]"            # "Md: NAME", "MD. NAME", "Mst, NAME" (OCR)
_FATHER = r"F[ae][tl]?h[ea]r\W{0,3}s?"          # Father's / Fathers / Father' s / Falher's
_MOTHER = r"M[o0][tl]?h[ea]r\W{0,3}s?"
_NAMEL = r"N[ae]r?n?m?e"
_JUNK = r"(?:[^\w\s]{1,3}\s*)?"                   # one stray OCR mark after a value (integration, test 1)
# rev 2.5: OCR item-number noise before the next item ("<A> <B> 3 3) Mother's Name", owner EAP folder): up to three
# stray item numbers ("3", "3)", "3 3)") between a value and the next label
_ITEMNO = r"(?:\b\d{1,2}\s?[)}\].]?\W{0,2}\s*){0,3}"


def _clean_name(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip(" .:,")


_TIN_ANCHOR = re.compile(r"registered\s+'?tax\s*payer|tax\s*payer'?s?\W{0,3}\s*particulars", re.I)


def tin_section(text: str) -> str:
    """Only the TIN certificate's own text. A TRADE LICENSE slot file often holds the trade licence
    (with its own 'Name', 'Father's Name', 'Owner' rows) before the TIN page; reading the printed
    TIN fields from the whole file took the trade licence's rows in 4 of 199 files."""
    t = text or ""
    m = _TIN_ANCHOR.search(t)
    if not m:
        return t
    start = max(0, m.start() - 700)
    head = t[start:m.start()]
    cut = max(head.rfind("Government of the People"), head.rfind("National Board of Revenue"),
              head.lower().rfind("certificate"))
    if cut > 0:
        start += max(0, cut - 120)
    return t[start: m.end() + 1400]


def printed_tin_fields(text: str) -> Dict[str, str]:
    """TIN number, 'certify that X is a Registered Taxpayer', '1) Name', '2) Father's Name' and
    '3) Mother's Name' from the OCR text of an NBR TIN certificate. Missing fields are left out.
    A field ends at the next label, or at the next item number ("2)") when OCR garbled the label.
    Only the TIN certificate's own section is read (tin_section)."""
    t = re.sub(r"[ \t]+", " ", tin_section(text))
    out: Dict[str, str] = {}
    m = re.search(r"Certificate\W{0,6}TIN\W{0,4}((?:\d\W?){12})", t, re.I) \
        or re.search(r"\bTIN\b\s*(?:No\.?)?\s*[:\-]?\s*((?:\d\s?){12})(?!\d)", t, re.I)
    if m:
        out["tin"] = re.sub(r"\D", "", m.group(1))
    m = re.search(r"[tl][il1]fy\s+that\s+(" + _NAME_CHARS + r"{3,70}?)\s+[il1][sS]\s+(?:a\s+|&\s+|8\s+|an\s+)?"
                  r"registered\s+'?tax\s*payer", t, re.I)
    if m:
        out["certify_name"] = _clean_name(m.group(1))
    for pat in (r"\b1\W{0,2}\s*" + _NAMEL + r"\s*[:\-]?\s*(" + _NAME_CHARS + r"{3,70}?)\s*" + _ITEMNO + r"\s*" + _FATHER
                + r"\s*" + _NAMEL,
                r"\b1\W{0,2}\s*" + _NAMEL + r"\s*[:\-]?\s*(" + _NAME_CHARS + r"{3,70}?)\s*\b2\s?[)}\].]",
                r"\b" + _NAMEL + r"\s*[:\-]?\s*(" + _NAME_CHARS + r"{3,70}?)\s*" + _ITEMNO + r"\s*" + _FATHER + r"\s*"
                + _NAMEL):
        m = re.search(pat, t, re.I)
        if m:
            out["name"] = _clean_name(m.group(1))
            break
    # _JUNK: one stray OCR mark between the value and the next item ("... <A> <B> @ 3) Mother's Name", test 1)
    for pat in (_FATHER + r"\s*" + _NAMEL + r"\s*[:\-]?\s*(" + _NAME_CHARS + r"{3,70}?)\s*" + _JUNK + _ITEMNO + r"\s*"
                + _MOTHER + r"\s*" + _NAMEL,
                r"\b2\s?[)}\].]\s*\S{0,12}\s*" + _NAMEL + r"\s*[:\-]?\s*(" + _NAME_CHARS + r"{3,70}?)\s*" + _JUNK
                + r"\b3\s?[)}\].]"):
        m = re.search(pat, t, re.I)
        if m:
            out["father"] = _clean_name(m.group(1))
            break
    m = re.search(_MOTHER + r"\s*" + _NAMEL + r"\s*[:\-]?\s*(" + _NAME_CHARS + r"{3,70}?)\s*" + _ITEMNO
                  + r"(?:\b4\W{0,4}\w?\W{0,2})?\s*(?:Current|Present|Address)", t, re.I)
    if m:
        out["mother"] = _clean_name(m.group(1))
    return out



def _edit(a: str, b: str) -> int:
    if len(a) < len(b):
        a, b = b, a
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]


def tin_agreement(qr_tin: str, printed_tin: str, text: str) -> str:
    if not qr_tin:
        return "QR_NO_TIN"
    if not printed_tin:
        digits = re.sub(r"\D", "", text or "")
        return "NOT_READ_BUT_IN_TEXT" if qr_tin in digits else "NOT_READ"
    if qr_tin == printed_tin:
        return "EQUAL"
    if len(qr_tin) == len(printed_tin) and _edit(qr_tin, printed_tin) <= 2:
        return "OCR_DIGITS"
    return "DIFFERENT"



# =====================================================================================================
# The runner's judging (RUNNER_SPEC 5.9.3): strict names (A1), the shared re-read, model.Finding rows.
# Everything above this line is the lab's tin_qr.py (minus the removed parts listed in the docstring).
# =====================================================================================================
from typing import Any, Callable, Sequence  # noqa: E402

from . import strict  # noqa: E402
from .model import ERROR, FAIL, FLAG, NOTE, PASS, DocUnit, Finding, PageRead, PageType, page_no  # noqa: E402

SOURCE = "runner:tin"
_RULE_FIELD = {"tin.qr_tin": "tin", "tin.qr_name": "name", "tin.qr_father": "father"}
_NAME_FIELDS = ("name", "certify_name")   # the taxpayer's name is printed twice: "1) Name" and the certify line
_CODE_FAULTS = (NameError, AttributeError, ImportError, TypeError)   # == doc_verifier.CODE_FAULTS: never swallowed
KIND_TEXT = {"EQUAL": "the same", "DOTS": "only the dots differ", "OCR": "a look-alike character differs (I/l/1, O/0)",
             "ORDER": "the same words in another order", "SUBSET": "a word is missing or extra",
             "LETTERS": "one or two characters differ", "DIFFERENT": "a different name", "EMPTY": "not read"}


def _kind(qr_name: str, printed_name: str) -> str:
    """The QR is a machine read of the name as NBR holds it; the print is compared letter for letter,
    dots aside (dots are the names dot rule's), words in their order (critique 2)."""
    return strict.compare(qr_name or "", printed_name or "", dots=False)


def compare(qr: Optional[Dict[str, str]], printed: Dict[str, str], text: str = "") -> Dict[str, Any]:
    """Field-by-field agreement between the QR and the printed certificate. "name" is the worst strict kind
    over the printed reads of the taxpayer's name (the "1) Name" row and the certify line; EMPTY only when
    neither was read); "name_mixed" is True when one printed read equals the QR and another does not (the
    certificate then shows the QR's spelling, so the difference cannot be confirmed); "name_variant" is the
    printed spelling that differs."""
    qr = qr or {}
    qn = qr.get("name", "")
    reads = [(f, printed.get(f, "")) for f in _NAME_FIELDS if printed.get(f)]
    kinds = {f: _kind(qn, v) for f, v in reads}
    seen = [k for k in kinds.values() if k != "EMPTY"]
    name = max(seen, key=lambda k: strict.KIND_RANK[k]) if seen else "EMPTY"
    variant = next((v for f, v in reads if kinds[f] == name), "") if name not in ("EQUAL", "EMPTY") else ""
    return {
        "tin": tin_agreement(qr.get("tin", ""), printed.get("tin", ""), text),
        "name": name,
        "name_certify": kinds.get("certify_name", "EMPTY"),
        "name_field": kinds.get("name", "EMPTY"),
        "name_mixed": "EQUAL" in kinds.values() and name != "EQUAL",
        "name_variant": variant,
        "father": _kind(qr.get("father", ""), printed.get("father", "")),
    }


def verdicts(qr: Optional[Dict[str, str]], cmp: Optional[Dict[str, Any]], qr_seen: bool,
             name_confirmed: bool = False, father_confirmed: bool = False,
             notes: Optional[Dict[str, Dict[str, Any]]] = None) -> List[Tuple[str, str, str]]:
    """The findings for one TIN certificate (QR first), as (verdict, message, rule). Never a PASS on a
    difference (A1): a name difference is FAIL only when the printed spelling is confirmed (the QR is a
    machine read); otherwise FLAG "check by eye"."""
    notes = notes or {}
    if qr is None:
        if qr_seen:
            return [(FLAG, "the TIN certificate's QR code is there but could not be read - check it by eye", "tin.qr")]
        return [(FLAG, "no readable QR code on the TIN certificate - check it is the NBR e-TIN certificate", "tin.qr")]
    cmp = cmp or {}
    out: List[Tuple[str, str, str]] = [(PASS, "the TIN certificate's QR was read", "tin.qr")]
    t = cmp.get("tin")
    if t == "EQUAL":
        out.append((PASS, "the QR's TIN matches the printed TIN", "tin.qr_tin"))
    elif t == "CLOSER_EQUAL":
        out.append((PASS, "the QR's TIN matches the printed TIN (a closer look at the print reads the QR's digits; the "
                          "page read had misread a digit)", "tin.qr_tin"))
    elif t == "DIFFERENT":
        out.append((FAIL, "the QR's TIN is not the printed TIN - the certificate may be altered", "tin.qr_tin"))
    elif t == "CLOSER_DIFFERENT":
        # rev 2.5: never FAIL on it. The page read and the closer look are the same kind of recogniser on the same
        # print, so they can repeat one misread of a look-alike digit (owner folder 3: a closer-look version read the
        # TIN's last 3 as 2 in every view; the print is the QR's number by eye). Only a difference beyond OCR's reach
        # (tin_agreement "DIFFERENT") is FAIL.
        out.append((FLAG, "the printed TIN reads one or two look-alike digits off the QR's, and a closer look at the "
                          "print reads the same - check by eye (if the print really differs, the certificate may be "
                          "altered)", "tin.qr_tin"))
    else:
        out.append((FLAG, "the printed TIN could not be read clearly enough to compare with the QR", "tin.qr_tin"))

    def why(field: str) -> str:
        how = (notes.get(field) or {}).get("how", "")
        if how.startswith("refuted"):
            return "; a closer read shows another spelling - probably OCR"
        if how.startswith("another page"):
            return "; another page prints the QR's spelling"
        return ""

    n = cmp.get("name", "EMPTY")
    if n == "EQUAL":
        out.append((PASS, "the QR's taxpayer name matches the printed name", "tin.qr_name"))
    elif n == "EMPTY" and not qr.get("name"):
        out.append((FLAG, "the TIN QR carries no taxpayer name - check the certificate by eye", "tin.qr_name"))
    elif n == "EMPTY":
        out.append((FLAG, "the printed taxpayer name could not be read exactly - the QR name is used; check by eye",
                    "tin.qr_name"))
    elif strict.judge(n, False, name_confirmed and not cmp.get("name_mixed")) == FAIL:
        out.append((FAIL, "the QR's taxpayer name is not the printed one - the certificate may be altered "
                          f"({KIND_TEXT.get(n, n)})", "tin.qr_name"))
    else:
        out.append((FLAG, f"the QR's taxpayer name differs from the printed name ({KIND_TEXT.get(n, n)}) - check by "
                          "eye" + why("name"), "tin.qr_name"))
    if qr.get("father"):
        f = cmp.get("father", "EMPTY")
        if f == "EQUAL":
            out.append((PASS, "the QR's father's name matches the printed father's name", "tin.qr_father"))
        elif f == "EMPTY":
            out.append((NOTE, "the printed father's name could not be read - not compared with the QR's",
                        "tin.qr_father"))
        elif strict.judge(f, False, father_confirmed) == FAIL:
            out.append((FAIL, "the QR's father's name is not the printed one - the certificate may be altered "
                              f"({KIND_TEXT.get(f, f)})", "tin.qr_father"))
        else:
            out.append((FLAG, f"the QR's father's name differs from the printed one ({KIND_TEXT.get(f, f)}) - check "
                              "by eye" + why("father"), "tin.qr_father"))
    return out


def _confirm_printed(variant: str, reference: str, fields: Sequence[str], page_texts: Optional[Sequence[str]],
                     run_pages: Optional[Sequence[int]], confirm: Optional[Callable[[int, str], str]],
                     page_types: Optional[Sequence[str]]) -> Tuple[bool, Dict[str, Any]]:
    """Is the printed spelling `variant` CONFIRMED (A1)? Two pages of the unit print it, or the shared
    targeted re-read confirms it on a page. Not when any page prints the QR's spelling (`reference`).
    Without run_pages the confirmer cannot be pointed at a page, so it is not asked (FLAG, never a guess)."""
    info: Dict[str, Any] = {"variant": variant, "how": "", "reread": []}
    if not variant:
        return False, info
    on, ref = [], []
    for k, pt in enumerate(page_texts or []):
        f = printed_tin_fields(pt or "")
        vals = [f[x] for x in fields if f.get(x)]
        if any(strict.compare(variant, v, dots=False) == "EQUAL" for v in vals):
            on.append(k)
        if reference and any(strict.compare(reference, v, dots=False) == "EQUAL" for v in vals):
            ref.append(k)
    info["variant_pages"], info["reference_pages"] = on, ref
    if ref:
        info["how"] = "another page prints the QR's spelling"
        return False, info
    if len(on) >= 2:
        info["how"] = "two pages"
        return True, info
    if confirm is None or not run_pages:
        return False, info
    cands = on or [k for k, t in enumerate(page_types or []) if t == "tin"] or list(range(len(run_pages)))
    for k in cands[:2]:
        if k >= len(run_pages):
            continue
        r = confirm(run_pages[k], variant)
        info["reread"].append([k, r])
        if r in ("confirmed", "refuted"):
            info["how"], info["page"] = ("confirmed by a closer read" if r == "confirmed"
                                         else "refuted by a closer read"), k
            return r == "confirmed", info
    return False, info


def _printed_page(fields: Sequence[str], printed: Dict[str, str], page_texts: Optional[Sequence[str]]) -> Optional[int]:
    """The 0-based file page whose text prints the first of `fields` as read (None when unknown)."""
    want = [printed.get(f, "") for f in fields if printed.get(f)]
    for k, pt in enumerate(page_texts or []):
        f = printed_tin_fields(pt or "")
        if any(f.get(x) and f.get(x) in want for x in fields):
            return k
    return None


CLOSER_DPI = 400               # a vector (text-layer) page
CLOSER_SCAN_DPI = (150, 400)   # a scanned page: its own resolution, within these (closer.page_image's rule)


def closer_dpi(closer: Any, path, page: int) -> int:
    """The render resolution for the closer look: a scan at its own resolution (rendering a 150 dpi scan at 400 dpi
    adds no detail and doubles the closer look's time: owner folder 3's TIN 85 s -> about 25 s, the same decision);
    a vector page at CLOSER_DPI."""
    fn = getattr(closer, "native_dpi", None)
    nd = fn(path, page) if callable(fn) else None
    if not nd:
        return CLOSER_DPI
    return int(max(CLOSER_SCAN_DPI[0], min(CLOSER_SCAN_DPI[1], round(float(nd)))))


def tin_closer_look(path, page: int, qr_tin: str, printed_tin: str) -> Tuple[str, Dict[str, Any]]:
    """The printed TIN against the QR's when the page read differs from it by look-alike digits (tin_agreement
    "OCR_DIGITS"): the closer look (closer.look, kind "digits", on a render of the page at closer_dpi; candidates[0]
    = the QR's digits) decides. "same" -> "CLOSER_EQUAL" (PASS: the print is the QR's number; the page read misread a
    digit); "differs" -> "CLOSER_DIFFERENT" (both reads of the print give another number: still FLAG check by eye,
    never FAIL - the two reads are one kind of recogniser and can share a look-alike misread); "unclear" or no closer
    look -> "OCR_DIGITS" (FLAG check by eye). Returns (kind, evidence)."""
    info: Dict[str, Any] = {"how": "closer look", "page": page, "candidates": 2}
    try:
        import importlib
        closer = importlib.import_module(f"{__package__}.closer")
    except ImportError:
        info["how"] = "no closer look available"
        return "OCR_DIGITS", info
    try:
        dpi = closer_dpi(closer, path, page) if Path(str(path)).suffix.lower() == ".pdf" else CLOSER_DPI
        info["dpi"] = dpi
        img = render_page(path, page, dpi)
        if img is None:
            info["how"] = "the page did not render"
            return "OCR_DIGITS", info
        img = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)
        look = closer.look(img, None, [qr_tin, printed_tin], kind="digits")
    except _CODE_FAULTS:
        raise
    except Exception as e:                       # a page that will not render: no closer look, the FLAG stays
        info["how"] = f"closer look failed ({type(e).__name__})"
        return "OCR_DIGITS", info
    info.update(decision=look.decision, margin=round(float(look.margin), 3), agree=int(look.agree),
                variants=int(look.variants), note=str(look.note or ""))
    if look.decision == "same":
        return "CLOSER_EQUAL", info
    if look.decision == "differs":
        return "CLOSER_DIFFERENT", info
    return "OCR_DIGITS", info


def check_tin_certificate(path, text: str, page_types: Optional[List[str]] = None, max_pages: int = 20,
                          confirm: Optional[Callable[[int, str], str]] = None, *,
                          page_texts: Optional[Sequence[str]] = None,
                          run_pages: Optional[Sequence[int]] = None) -> Dict[str, Any]:
    """What the runner calls for one TIN sub-PDF (or a folder file holding the TIN certificate).

    Method (lab, measured on the 228 real financial files): stage A on every page; when no TIN QR was read,
    the full ladder (deep=True) on the pages that have no meaningful payload yet - only on the pages typed
    "tin" when page_types is given. Then the printed fields (OCR text) against the QR, strictly.
    page_texts (the unit's per-page texts) and run_pages (file page k -> 0-based run page) let a printed
    spelling be confirmed by a second page or by `confirm` (the shared re-read); without run_pages the
    re-read is not asked. Returns {'qr', 'qr_page', 'qr_seen', 'all_codes', 'printed', 'compare', 'confirm',
    'rows': [(verdict, message)], 'findings': [{verdict, rule, message, page}], 'page_types', 'seconds'}
    (file pages 0-based). No image is kept in the result."""
    t0 = time.perf_counter()
    try:
        imgs = render(path, 250, max_pages) if path is not None else []
        err = "" if path is not None else "no file"
    except _CODE_FAULTS:
        raise
    except Exception as e:                       # an unreadable cut: said, never a crash
        imgs, err = [], type(e).__name__
    pages = []
    for i, img in enumerate(imgs):
        codes, how = _stage_a(img)
        pages.append({"page": i, "codes": codes, "stage": how})
    qr, qr_page = None, None
    for p in pages:
        q = next((parse_tin_qr(c) for c in p["codes"] if parse_tin_qr(c)), None)
        if q:
            qr, qr_page = q, p["page"]
            break
    if qr is None:
        for p in pages:
            if meaningful(p["codes"]):
                continue
            if page_types and p["page"] < len(page_types) and page_types[p["page"]] != "tin":
                continue
            r = read_page(path, p["page"], imgs[p["page"]], deep=True)
            p["codes"], p["stage"] = r["codes"], r["stage"]
            qr = next((parse_tin_qr(c) for c in p["codes"] if parse_tin_qr(c)), None)
            if qr:
                qr_page = p["page"]
                break
    seen = False
    if qr is None:                       # is a QR there at all (readable or not)?
        for img in imgs:
            try:
                ok, _ = _CV.detectMulti(img)
            except Exception:
                ok = False
            if ok or _blob_boxes(img):
                seen = True
                break
    printed = printed_tin_fields(text)
    cmp = compare(qr, printed, text) if qr else None
    conf: Dict[str, Dict[str, Any]] = {}
    if qr and cmp and cmp.get("tin") == "OCR_DIGITS" and path is not None:
        # rev 2.5 (owner, 28 Oct): a number that reads differently is looked at again before any verdict
        k = _printed_page(("tin",), printed, page_texts)
        if k is None:
            k = next((j for j, t in enumerate(page_types or []) if t == "tin"), 0)
        cmp["tin"], conf["tin"] = tin_closer_look(path, k, qr.get("tin", ""), printed.get("tin", ""))
    name_ok = father_ok = False
    if qr and cmp:
        if cmp["name"] not in ("EQUAL", "EMPTY") and not cmp["name_mixed"]:
            name_ok, conf["name"] = _confirm_printed(cmp["name_variant"], qr.get("name", ""), _NAME_FIELDS,
                                                     page_texts, run_pages, confirm, page_types)
        if qr.get("father") and cmp["father"] not in ("EQUAL", "EMPTY"):
            father_ok, conf["father"] = _confirm_printed(printed.get("father", ""), qr.get("father", ""), ("father",),
                                                         page_texts, run_pages, confirm, page_types)
    rows = verdicts(qr, cmp, seen, name_ok, father_ok, conf)
    if err:
        rows = [(ERROR, f"the TIN certificate's pages could not be opened for the QR ({err})", "tin.qr")]
    first_tin = next((k for k, t in enumerate(page_types or []) if t == "tin"), 0)
    name_page = _printed_page(_NAME_FIELDS, printed, page_texts)
    page_of = {"tin.qr": qr_page if qr_page is not None else first_tin,
               "tin.qr_tin": _printed_page(("tin",), printed, page_texts),
               "tin.qr_name": (conf.get("name") or {}).get("page", name_page),
               "tin.qr_father": (conf.get("father") or {}).get("page", _printed_page(("father",), printed, page_texts))}
    findings = []
    for v, m, rule in rows:
        pg = page_of.get(rule)
        field_ = _RULE_FIELD.get(rule, "")
        ev: Dict[str, Any] = {"compare": (cmp or {}).get(field_, "")} if field_ else {"stages": [p["stage"] for p in pages]}
        if field_ in conf:
            ev["confirm"] = conf[field_]
        findings.append({"verdict": v, "rule": rule, "message": m,
                         "page": pg if pg is not None else (qr_page if qr_page is not None else first_tin),
                         "evidence": ev})
    return {"qr": qr, "qr_page": qr_page, "qr_seen": bool(qr) or seen,
            "all_codes": [c for p in pages for c in p["codes"]], "stages": [p["stage"] for p in pages],
            "printed": printed, "compare": cmp, "confirm": conf, "rows": [(v, m) for v, m, _ in rows],
            "findings": findings, "page_types": list(page_types or []), "error": err,
            "seconds": round(time.perf_counter() - t0, 1)}


# --- model.Finding rows ----------------------------------------------------------------------------
def _p1(*run_pages: Optional[int]) -> Tuple[int, ...]:
    return tuple(sorted({page_no(p) for p in run_pages if p is not None and p >= 0}))


def tin_page_types(unit: DocUnit, types: List[PageType]) -> List[str]:
    """Per page of the unit: "tin" for a TIN-certificate page, else "". A unit whose fine type is the TIN
    certificate is all TIN pages; a folder file holding several parts uses its `sub` parts (first, last,
    fine type; taken as positions in the file or as run pages) and the page typing."""
    out = []
    for k, i in enumerate(unit.pages):
        best = types[i].best if 0 <= i < len(types) and types[i] is not None else ""
        in_sub = any(lab == "tin_certificate" and (lo <= k <= hi or lo <= i <= hi) for lo, hi, lab in (unit.sub or []))
        out.append("tin" if unit.fine == "tin_certificate" or best == "tin_certificate" or in_sub else "")
    return out


# A browser prints the page's address in its header or footer ("https://host/path"). OCR often turns the
# slashes into I, l, 1 or | ("https:Ilhost.toplpath"), so the separator after the scheme is read loosely and a
# path glued onto the last label is cut off at a known top-level domain followed by such a slash look-alike.
_SLASHY = r"[/\|Il1]"
_HOSTY = r"([a-z0-9][a-z0-9-]*(?:\.[a-z0-9-]+|,(?=(?:gov|bd)\b)[a-z]+)+)"
_URL_HOST = re.compile(r"(?i)\bh\s?t\s?t\s?p\s?s?\s?[:;]\s?(?:" + _SLASHY + r"\s?){1,2}(?:www\.)?" + _HOSTY)
_WWW_HOST = re.compile(r"(?i)(?<![a-z0-9.])www\s?\.\s?" + _HOSTY)
TLDS = frozenset("""com net org info biz top xyz site online store shop club live app link icu vip pro cc co io me tv ws
pw tk ml ga cf gq in pk us uk bd gov edu ac mil int asia cloud website space tech fun work buzz today world news
digital email host press""".split())


def _host(raw: str) -> str:
    """A host from a captured address, lower case: "," read for "." is mended; a path glued onto the last
    label is cut at the longest known top-level domain followed by a slash look-alike; an address under
    .gov.bd with something glued right after "bd" is cut there. "" when it is no host."""
    h = raw.lower().replace(",", ".").strip(".-")
    parts = [p for p in h.split(".") if p]
    if len(parts) < 2:
        return ""
    last = parts[-1]
    if last not in TLDS:
        cut = [t for t in TLDS if last.startswith(t) and len(last) > len(t) and last[len(t)] in "il1|/"]
        if cut:
            parts[-1] = max(cut, key=len)
        elif len(parts) >= 3 and parts[-2] in ("gov", "g0v") and last.startswith("bd"):
            parts[-1] = "bd"
    if not re.fullmatch(r"[a-z]{2,}", parts[-1]):
        return ""
    return ".".join(parts)


def is_gov_host(host: str) -> bool:
    """Under the Bangladesh government's .gov.bd (an OCR "0" for "o" in "gov" is forgiven)."""
    h = re.sub(r"\.g0v\.bd$", ".gov.bd", host or "")
    return h == "gov.bd" or h.endswith(".gov.bd")


def web_hosts(text: str, payloads: Sequence[str]) -> List[str]:
    """Web hosts printed in the text (a browser's print header/footer: https://host/..., www.host) and in
    QR payloads, lower case, in reading order, without "www.". OCR may write ".gov,bd" or "https:Il"."""
    out: List[str] = []
    for src in [text or ""] + [p or "" for p in payloads]:
        for rx in (_URL_HOST, _WWW_HOST):
            for m in rx.finditer(src):
                h = _host(m.group(1))
                if h and h not in out:
                    out.append(h)
    return out


# the spec's patterns, plus: a number that runs on into a digit or an OCR look-alike ("2O5") is a broken
# read and is skipped (the next mention is tried), so a partial number is never compared
_CIRCLE = re.compile(r"(?i)circle\s*[-:#.]*\s*(?:no\.?\s*)?(\d{1,3})(?![0-9OoIl|])")
_ZONE = re.compile(r"(?i)zone\s*[-:#.]*\s*(?:no\.?\s*)?(\d{1,2})(?![0-9OoIl|])")


def tax_office(text: str) -> Dict[str, str]:
    """{"circle": "<digits>", "zone": "<digits>"} - the first tax circle and zone the text names ("" when
    not read); compare them as integers."""
    c, z = _CIRCLE.search(text or ""), _ZONE.search(text or "")
    return {"circle": str(int(c.group(1))) if c else "", "zone": str(int(z.group(1))) if z else ""}


def tin_findings(result: Dict[str, Any], unit: DocUnit, pages: List[PageRead]) -> List[Finding]:
    """check_tin_certificate's result as model.Finding rows on the unit's run pages (1-based): tin.qr,
    tin.qr_tin, tin.qr_name, tin.qr_father, plus tin.web_address (A6 j, NEW, FLAG only, Q14): the hosts
    printed on the TIN pages and in the unit's QR payloads; any host not under .gov.bd -> FLAG; every host
    .gov.bd -> PASS; no host read -> no row."""
    up = list(unit.pages) if unit is not None else []

    def runp(k: Optional[int]) -> Optional[int]:
        if k is not None and 0 <= k < len(up):
            return up[k]
        return up[0] if up else None

    out: List[Finding] = []
    rows = result.get("findings")
    if rows is None:                                   # a result with (verdict, message) rows only
        rows = [{"verdict": v, "message": m, "rule": "tin.qr", "page": None} for v, m in result.get("rows", [])]
    for r in rows:
        out.append(Finding(r["verdict"], r["rule"], r["message"], SOURCE, _p1(runp(r.get("page"))),
                           dict(r.get("evidence") or {})))
    pt = result.get("page_types") or []
    ks = [k for k, t in enumerate(pt) if t == "tin" and k < len(up)] or list(range(len(up)))
    hosts: List[str] = []
    where: Dict[str, Optional[int]] = {}
    for k in ks:
        i = up[k]
        if 0 <= i < len(pages):
            for h in web_hosts(pages[i].text, ()):
                if h not in hosts:
                    hosts.append(h)
                    where[h] = i
    for h in web_hosts("", result.get("all_codes") or []):
        if h not in hosts:
            hosts.append(h)
            where[h] = runp(result.get("qr_page"))
    bad = [h for h in hosts if not is_gov_host(h)]
    if bad:
        out.append(Finding(FLAG, "tin.web_address", f"the TIN certificate was printed from {', '.join(bad)}, not a "
                                                    "government (.gov.bd) address — verify the TIN on the NBR site",
                           SOURCE, _p1(*[where.get(h) for h in bad]), {"hosts": hosts, "new_check": True}))
    elif hosts:
        out.append(Finding(PASS, "tin.web_address", f"printed from {', '.join(hosts)}", SOURCE,
                           _p1(*[where.get(h) for h in hosts]), {"hosts": hosts, "new_check": True}))
    return out
