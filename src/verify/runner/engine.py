"""The runner's reading engine (RUNNER_SPEC section 5.2; Task 1): a port of the engine study's
light_engine.py, plus serving page texts read in advance (5.2.1).

The bot's own functions do the reading (the worktree's src.verify.doc_verifier, via botimport.load):
dv.read_pages (200 dpi render for a page with < 40 characters of text layer; the text layer otherwise),
dv.read_document, and dv._ocr_image with its rotation retries. Only dv._reader is replaced, by the light
reader: glue_numpy.LightReader (easyocr.Reader(["en"]).readtext with the same defaults, no torch) on two
onnxruntime sessions with the CPU execution provider ONLY (S5: never DirectML, which the numpy venv's
onnxruntime also offers). dv._ocr_image is wrapped by PageLog for timing and an exact same-pixels memo.

Pages present in a page-texts file (normally written by the GPU worker, gpuread.py) are served from it
(engine "gpu", or "layer" for its text-layer pages); the pages it lacks are read here on the CPU (engine
"cpu", or "layer"). All caches live under the cache root (private-runner\\runner-data by default):
verification\\text\\<case_id>.json and verification\\images\\<case_id>.json, the bot's own two caches.

An array returned by page_image_of() is shared by the memo and is READ-ONLY: copy it before drawing on it.
No personal data.
"""
from __future__ import annotations

import contextlib
import json
import os
import re
import time
from collections import Counter, OrderedDict
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional, Sequence, Tuple

from . import botimport, safety
from .ingest import IMAGE_SUFFIXES, InputError, file_sha256
from .ingest import file_label as ingest_file_label
from .model import PAGE_TEXTS_SCHEMA, PageRead, Source

ONNX_LAB = Path(r"C:\Hangeul\DOCCHECK\lab\onnx")
MODELS = ONNX_LAB / "models"
GLUE_DATA = ONNX_LAB / "pkg" / "glue_data.json"
PROVIDERS = ["CPUExecutionProvider"]
DET_ARENA = False             # rev 2.7: the detector's session has ORT's CPU memory arena off (make_reader)
REC_ARENA = False             # rev 2.7 (step 1b): the recogniser's too
HEAVY = ("torch", "torchvision", "easyocr", "scipy", "skimage")
LAYER_MIN_CHARS = 40          # dv.read_pages: a page with fewer layer characters is OCR'd
PAGE_DPI = 200                # dv.read_pages render
IMAGE_ONLY_COVER = 0.5        # PageRead.image_only: one embedded image covering >= 50% of the page
IMAGE_MEMO = 12               # page_image_of keeps at most the last 12 renders ...
IMAGE_MEMO_MB = 200           # ... rev 2.7: and at most this many MB of them (the newest render is always kept)
RUNNER_PAGE_KEY = "RUNNER-PAGE"   # case-cache key of a page read alone (partial page texts)
TURN_KEY = "RUNNER-TURN"          # case-cache key of a page read turned (rev 2.5, pagetype.turn_junk_pages)
LINE_DPI = 100                    # render for text_line_ratio (rev 2.5)
LINE_SMEAR = 11                   # px at 1000 px a page: 15 also joined two close lines (a synthetic letter read 0.6)
_CASE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,79}$")


# --------------------------------------------------------------------------------- the reader
def session_options(threads: int = 0, arena: bool = True):
    """ONNX Runtime session options of the light reader: quiet log, `threads` intra-op threads (0 = ORT default),
    the CPU memory arena on or off."""
    import onnxruntime as ort
    so = ort.SessionOptions()
    so.log_severity_level = 3
    if threads:
        so.intra_op_num_threads = threads
    so.enable_cpu_mem_arena = bool(arena)
    return so


def make_reader(threads: int = 0):
    """LightReader on two ORT sessions with the CPU execution provider only (threads 0 = ORT default).

    Rev 2.7 (RUNNER_SPEC limit 15): the DETECTOR's session runs with ONNX Runtime's CPU memory arena OFF
    (DET_ARENA). One CRAFT pass on a large page needs about 1-2.4 GB of activations for a moment; with the arena
    on, ORT kept that memory for the rest of the process and grew it again on the next page (two 2560-pixel
    detections: 9.2 GB committed). Off, the memory goes back to Windows after each pass. The kernels and
    the numbers are the same (only where the buffers live changes; tests/test_memory27.py checks the outputs
    are bit-identical). Step 1b: the RECOGNISER's session has the arena off too (REC_ARENA): its inputs are single
    text lines, but the closer look enlarges a line up to 6000 pixels wide and the arena then kept a few hundred MB
    for the rest of the run (the EAP folder: 65 MB more at the run's peak); the readtext results of real pages are
    identical with it off (step 1b, 19 reads of 10 pages at 200 and 300 dpi), and the run takes no longer."""
    import onnxruntime as ort
    import glue_numpy
    det = ort.InferenceSession(str(MODELS / "craft_mlt_25k.onnx"), session_options(threads, DET_ARENA),
                               providers=PROVIDERS)
    rec = ort.InferenceSession(str(MODELS / "english_g2.onnx"), session_options(threads, REC_ARENA),
                               providers=PROVIDERS)
    for s in (det, rec):
        if s.get_providers() != PROVIDERS:
            raise RuntimeError(f"an ONNX session is not CPU-only: {s.get_providers()}")
    return glue_numpy.LightReader(det, rec, GLUE_DATA)


def free_render_store() -> None:
    """Rev 2.7 (RUNNER_SPEC limit 15): empties MuPDF's store, the cache of decoded page images that pymupdf keeps for
    the whole process (up to 256 MB: every scanned page rendered leaves its decoded scan there). Called before each
    detection, so the detector's memory does not come on top of it. A cache only: a later render decodes the page
    again and gives the same pixels. Nothing happens when pymupdf is not loaded."""
    import sys
    m = sys.modules.get("pymupdf")
    if m is not None:
        try:
            m.TOOLS.store_shrink(100)
        except Exception:
            pass


class CountingReader:
    """The reader with a count of model calls (every readtext, rotation retries included). Rev 2.7: MuPDF's render
    store is emptied before each call (free_render_store)."""

    def __init__(self, reader: Any):
        self.inner = reader
        self.calls = 0
        self.seconds = 0.0

    def readtext(self, image, *args, **kw):
        free_render_store()
        t = time.perf_counter()
        try:
            return self.inner.readtext(image, *args, **kw)
        finally:
            self.calls += 1
            self.seconds += time.perf_counter() - t

    def __getattr__(self, name):
        return getattr(self.inner, name)


class PageLog:
    """Wraps dv._ocr_image: one entry per call (shape, seconds, chars, turned, memo hit). An identical page
    array (same shape and bytes) is OCR'd once: the bot's function is deterministic on the same pixels, so
    the memo cannot change any text. A second PageLog in one process wraps the bot's original function,
    not the first PageLog."""

    def __init__(self, dv):
        current = dv._ocr_image
        self.dv = dv
        self.real = getattr(current, "_runner_real", current)
        self._runner_real = self.real
        self.memo: Dict[Tuple, Tuple[str, int]] = {}
        self.calls: List[dict] = []
        dv._ocr_image = self

    def __call__(self, arr, turned=None):
        import hashlib
        key = (arr.shape, hashlib.sha1(memoryview(arr.copy(order="C"))).hexdigest())
        hit = key in self.memo
        t = time.perf_counter()
        if not hit:
            mine: List[bool] = []
            self.memo[key] = (self.real(arr, mine), len(mine))
        text, nturn = self.memo[key]
        if turned is not None:
            turned.extend([True] * nturn)
        self.calls.append({"h": arr.shape[0], "w": arr.shape[1], "s": round(time.perf_counter() - t, 3),
                           "chars": len(text), "turned": nturn, "memo": hit})
        return text

    def mark(self) -> int:
        return len(self.calls)

    def since(self, m: int) -> List[dict]:
        return self.calls[m:]


def heavy_modules() -> List[str]:
    import sys
    return sorted(m for m in HEAVY if m in sys.modules)


# --------------------------------------------------------------------------------- page facts
def page_kinds(pdf: Path, dpi_rule: str = "pages") -> List[str]:
    """'layer' or 'ocr' per page, by the bot's own threshold (read_pages: < 40 chars; read_document: <= 80)."""
    import pymupdf
    limit = LAYER_MIN_CHARS if dpi_rule == "pages" else 81
    with pymupdf.open(str(pdf)) as doc:
        return ["layer" if len(p.get_text().strip()) >= limit else "ocr" for p in doc]


def page_facts(pdf: Path) -> Tuple[List[str], List[bool], List[str]]:
    """(kinds, image_only, layer texts) per page. kind as read_pages decides it; image_only = read by OCR
    (no usable text layer) and exactly one embedded image placement covering >= 50% of the page."""
    import pymupdf
    kinds, image_only, layers = [], [], []
    with pymupdf.open(str(pdf)) as doc:
        for page in doc:
            layer = page.get_text().strip()
            kind = "layer" if len(layer) >= LAYER_MIN_CHARS else "ocr"
            only = False
            if kind == "ocr":
                try:
                    infos = page.get_image_info()
                except Exception:
                    infos = []
                if len(infos) == 1:
                    r = pymupdf.Rect(infos[0]["bbox"]) & page.rect
                    area = max(page.rect.width * page.rect.height, 1.0)
                    only = (not r.is_empty) and (r.width * r.height) / area >= IMAGE_ONLY_COVER
            kinds.append(kind)
            image_only.append(only)
            layers.append(layer)
    return kinds, image_only, layers


# --------------------------------------------------------------------------------- cutting
def cut_bundle(bundle: Path, parts: Sequence[Tuple[str, int, int]], out_dir: Path) -> List[Path]:
    """light_engine.cut_bundle, unchanged. One file per document from a bundle PDF. parts = [(file name,
    first page, last page)], 0-based, inclusive. A .pdf part is the pages copied with insert_pdf (content
    streams, fonts and images copied as they are: no re-render, no re-encode; text layer kept). A
    .jpg/.jpeg part (the photo) is the page's largest embedded image written out byte for byte when it is
    a JPEG, else the page rendered at 300 dpi. Each file gets the bundle's mtime, so the text cache key
    (name:size:mtime) is stable across re-cuts."""
    import pymupdf
    out_dir.mkdir(parents=True, exist_ok=True)
    st = Path(bundle).stat()
    written = []
    with pymupdf.open(str(bundle)) as src:
        for name, a, b in parts:
            dst = out_dir / name
            if dst.suffix.lower() in (".jpg", ".jpeg"):
                page = src[a]
                imgs = sorted(page.get_images(full=True), key=lambda im: -(im[2] * im[3]))
                data = None
                if imgs:
                    x = src.extract_image(imgs[0][0])
                    if x and x.get("ext") in ("jpeg", "jpg"):
                        data = x["image"]
                if data is None:
                    data = page.get_pixmap(dpi=300).tobytes("jpg", jpg_quality=95)
                dst.write_bytes(data)
            else:
                d = pymupdf.open()
                d.insert_pdf(src, from_page=a, to_page=b)
                d.save(str(dst), garbage=3, deflate=False)
                d.close()
            os.utime(dst, (st.st_atime, st.st_mtime))
            written.append(dst)
    return written


# --------------------------------------------------------------------------------- page texts (5.2.1)
@dataclass
class PageTexts:
    """Page texts read in advance: {(file name, 0-based file page): (text, kind, sideways, seconds)}.
    complete = every page of every input file is present; engine = the file's engine label."""
    pages: Dict[Tuple[str, int], Tuple[str, str, bool, float]]
    complete: bool
    engine: str
    path: Optional[Path] = None
    declared_complete: bool = False

    def get(self, name: str, page: int) -> Optional[Tuple[str, str, bool, float]]:
        return self.pages.get((name, page))

    def __contains__(self, key) -> bool:
        return key in self.pages

    def __len__(self) -> int:
        return len(self.pages)


def load_page_texts(path: Path, src: Source) -> PageTexts:
    """Reads a runner-page-texts/1 file (section 5.2.1). Raises InputError (exit 2) when the path is not
    under private-runner, the schema or mode is wrong, a file entry is not in `src`, an entry is repeated,
    a file's sha256 or page count differs from the input file's, or a page entry is malformed."""
    p = Path(path)
    try:
        r = safety._private(p, "--page-texts")
    except safety.SafetyError as e:
        raise InputError(str(e)) from e
    try:
        data = json.loads(r.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        raise InputError(f"the page-texts file could not be read: {type(e).__name__}") from e
    if not isinstance(data, dict) or data.get("schema") != PAGE_TEXTS_SCHEMA:
        raise InputError(f"the page-texts file is not {PAGE_TEXTS_SCHEMA}")
    if data.get("mode") not in (None, src.mode):
        raise InputError(f"the page texts are for a {data.get('mode')} input, not a {src.mode}")
    entries = data.get("files")
    if not isinstance(entries, list):
        raise InputError("the page-texts file has no file list")
    by_name = {f.name: f for f in src.files}
    # error lines name a file by its position, never by its name (S3: a file may carry the student's name)
    label_of = {f.name: ingest_file_label(k, len(src.files), src.mode) for k, f in enumerate(src.files, 1)}
    pages: Dict[Tuple[str, int], Tuple[str, str, bool, float]] = {}
    seen = set()
    for e in entries:
        name = e.get("name") if isinstance(e, dict) else None
        if name not in by_name:
            raise InputError("the page texts name a file that is not in the input")
        if name in seen:
            raise InputError("the page texts list a file twice")
        seen.add(name)
        f = by_name[name]
        lab = label_of[name]
        if e.get("n_pages") != f.n_pages:
            raise InputError(f"the page texts give {lab} {e.get('n_pages')} page(s), the input has {f.n_pages}")
        if str(e.get("sha256", "")).lower() != file_sha256(f.path):
            raise InputError(f"the page texts were read from another file than {lab} (sha256 differs)")
        for pg in e.get("pages") or []:
            no = pg.get("page") if isinstance(pg, dict) else None
            if not isinstance(no, int) or isinstance(no, bool) or not 1 <= no <= f.n_pages:
                raise InputError(f"a page entry of {lab} has no valid page number")
            text, kind = pg.get("text"), pg.get("kind", "ocr")
            if not isinstance(text, str) or kind not in ("layer", "ocr", "image"):
                raise InputError(f"page {no} of {lab} is malformed")
            try:
                secs = float(pg.get("seconds") or 0.0)
            except (TypeError, ValueError) as ex:
                raise InputError(f"page {no} of {lab} has bad seconds") from ex
            pages[(name, no - 1)] = (text, kind, bool(pg.get("sideways")), secs)
    complete = all((f.name, i) in pages for f in src.files for i in range(f.n_pages))
    return PageTexts(pages=pages, complete=complete, engine=str(data.get("engine", "")), path=r,
                     declared_complete=bool(data.get("complete")))


def text_line_ratio(img) -> float:
    """Rev 2.5: which way a page's text lines run, without reading it. The ink (long rules and borders out) is smeared
    LINE_SMEAR px along the rows and, apart, along the columns (the page scaled to 1000 px: it joins the letters and
    words of a line, not two lines); the bars a smear makes (3+ times longer than thick) are summed. Returns (row bars
    + 1) / (column bars + 1). The owner folders' 99 pages: text upright 1.57 and up (NID backs lowest), the four
    sideways certificates 0.02-0.04, photos 1.0-1.3, a blank page 1.0."""
    import cv2
    import numpy as np
    if img is None or getattr(img, "ndim", 0) < 2:
        return 1.0
    g = cv2.cvtColor(img, cv2.COLOR_RGB2GRAY) if img.ndim == 3 else img
    s = 1000.0 / max(g.shape[:2])
    g = cv2.resize(g, None, fx=s, fy=s, interpolation=cv2.INTER_AREA)
    _, bw = cv2.threshold(g, 0, 1, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
    H, W = bw.shape
    rules = (cv2.morphologyEx(bw, cv2.MORPH_OPEN, cv2.getStructuringElement(cv2.MORPH_RECT, (max(2, W // 8), 1)))
             | cv2.morphologyEx(bw, cv2.MORPH_OPEN, cv2.getStructuringElement(cv2.MORPH_RECT, (1, max(2, H // 8)))))
    bw = bw & (1 - rules)
    total = []
    for kernel, along in (((LINE_SMEAR, 1), 2), ((1, LINE_SMEAR), 3)):
        sm = cv2.morphologyEx(bw, cv2.MORPH_CLOSE, cv2.getStructuringElement(cv2.MORPH_RECT, kernel))
        n, _, st, _ = cv2.connectedComponentsWithStats(sm.astype(np.uint8), 8)
        across = 5 - along                        # 2 (width) <-> 3 (height)
        total.append(sum(int(st[i, along]) for i in range(1, n)
                         if st[i, along] >= 3 * st[i, across] and st[i, along] >= 25))
    return (total[0] + 1.0) / (total[1] + 1.0)


# --------------------------------------------------------------------------------- the engine
class Engine:
    """The light engine inside the bot's read path. `reader` replaces the ONNX light reader (tests)."""

    def __init__(self, cache_root: Path = botimport.DEFAULT_CACHE_ROOT, threads: int = 0,
                 reader: Any = None) -> None:
        self.bot = botimport.load(cache_root)
        self.reader = CountingReader(reader if reader is not None else make_reader(threads))
        self.bot.dv._reader = self.reader
        self.log = PageLog(self.bot.dv)
        self.handle = None
        self._files: Dict[str, Path] = {}
        self._images: "OrderedDict[Tuple[str, int, int], Any]" = OrderedDict()
        self._pages_by_engine: Counter = Counter()
        self._cache_hits = 0

    # ------------------------------------------------------------------ setup
    def set_today(self, today) -> None:
        self.bot.dv.TODAY = today

    @contextlib.contextmanager
    def case(self, case_id: str) -> Iterator[Any]:
        """The bot's text and image caches for one case: <cache root>\\verification\\{text,images}\\<case_id>.json
        (text_cache.install ... uninstall, which saves them)."""
        if not _CASE_RE.match(case_id or ""):
            raise ValueError("a case id is letters, digits, '_', '.' and '-' only")
        root = self.bot.cache_root / "verification"
        tdir, idir = root / "text", root / "images"
        safety.assert_writable(tdir)
        safety.assert_writable(idir)
        h = self.bot.TC.install(case_id, text_dir=tdir, images_dir=idir)
        try:
            for p in (h.path, h.images_path):
                if not Path(p).resolve().is_relative_to(self.bot.cache_root):
                    raise RuntimeError(f"a case cache outside the cache root: {p}")
            self.handle = h
            yield h
        finally:
            self.handle = None
            self.bot.TC.uninstall(h)

    # ------------------------------------------------------------------ reading
    def _count(self, pages: List[PageRead]) -> None:
        for p in pages:
            self._pages_by_engine[p.engine] += 1

    def read_file(self, path: Path, name: str, start_index: int,
                  given: Optional[PageTexts] = None) -> List[PageRead]:
        """One input file's pages. PDF: pages present in `given` are served from it; the others are read
        by the bot's read_pages (every page, no 20-page cap) or, when `given` holds some of the file's
        pages, page by page exactly as read_pages reads a page (the case cache first). Image file: from
        `given`, else one PageRead(kind="image") with dv.read_document's text."""
        path = Path(path)
        self._files[name] = path
        if path.suffix.lower() in IMAGE_SUFFIXES:
            pr = self._read_image(path, name, start_index, given)
            self._count([pr])
            return [pr]
        kinds, image_only, layers = page_facts(path)
        n = len(kinds)
        served = {}
        if given is not None:
            for i in range(n):
                hit = given.get(name, i)
                if hit is not None:
                    served[i] = hit
        texts: List[str] = [""] * n
        secs: List[float] = [0.0] * n
        side: set = set()
        if not served:
            texts, side, secs = self._read_pages_bot(path, kinds)
        else:
            for i, (t, _k, sw, s) in served.items():
                texts[i], secs[i] = t, float(s)
                if sw:
                    side.add(i + 1)
            missing = [i for i in range(n) if i not in served]
            if missing:
                t2, side2, secs2 = self._read_missing(path, missing, kinds, layers)
                for i in missing:
                    texts[i], secs[i] = t2[i], secs2[i]
                side |= side2
        out = []
        for i in range(n):
            if kinds[i] == "layer":
                eng = "layer"
            elif i in served:
                eng = "gpu"
            else:
                eng = "cpu"
            out.append(PageRead(index=start_index + i, file=name, file_page=i, text=texts[i], kind=kinds[i],
                                chars=len(texts[i]), sideways=(i + 1) in side, seconds=round(secs[i], 3),
                                image_only=image_only[i], engine=eng))
        self._count(out)
        return out

    def _read_image(self, path: Path, name: str, index: int, given: Optional[PageTexts]) -> PageRead:
        hit = given.get(name, 0) if given is not None else None
        if hit is not None:
            text, _kind, sw, s = hit
            return PageRead(index=index, file=name, file_page=0, text=text, kind="image", chars=len(text),
                            sideways=bool(sw), seconds=round(float(s), 3), image_only=True, engine="gpu")
        c0, t0 = self.reader.calls, time.perf_counter()
        text = self.bot.dv.read_document(path)[0]
        fresh = self.reader.calls > c0
        if not fresh:
            self._cache_hits += 1
        return PageRead(index=index, file=name, file_page=0, text=text, kind="image", chars=len(text),
                        sideways=False, seconds=round(time.perf_counter() - t0, 3) if fresh else 0.0,
                        image_only=True, engine="cpu")

    def _read_pages_bot(self, path: Path, kinds: List[str]) -> Tuple[List[str], set, List[float]]:
        """light_engine.ocr_pages: the bot's read_pages on every page (through the case cache)."""
        n = len(kinds)
        side: List[int] = []
        m = self.log.mark()
        texts = list(self.bot.dv.read_pages(path, max_pages=n, sideways=side))
        if len(texts) != n:
            raise RuntimeError(f"read_pages gave {len(texts)} page(s) for a {n}-page file")
        calls = self.log.since(m)
        ocr_idx = [i for i, k in enumerate(kinds) if k == "ocr"]
        secs = [0.0] * n
        if calls:
            for i, c in zip(ocr_idx, calls):
                secs[i] = c["s"]
        else:
            self._cache_hits += len(ocr_idx)
        return texts, set(side), secs

    def _read_missing(self, path: Path, missing: List[int], kinds: List[str],
                      layers: List[str]) -> Tuple[Dict[int, str], set, Dict[int, float]]:
        """The pages a partial page-texts file lacks: the layer text for a layer page; else a whole-file
        read_pages entry of the case cache; else the page rendered at 200 dpi and read by dv._ocr_image
        exactly as read_pages reads it, kept in the case cache under RUNNER-PAGE:<name>:<size>:<mtime>:<page>."""
        import numpy as np
        import pymupdf
        dv, TC = self.bot.dv, self.bot.TC
        texts: Dict[int, str] = {}
        secs: Dict[int, float] = {i: 0.0 for i in missing}
        side: set = set()
        st = path.stat()
        base = f"{path.name}:{st.st_size}:{int(st.st_mtime)}"
        cached_pages = None
        if self.handle is not None:
            cached_pages = TC.page_texts(self.handle.cache, path, len(kinds))
        todo = []
        for i in missing:
            if kinds[i] == "layer":
                texts[i] = layers[i]
            elif cached_pages is not None:
                texts[i] = cached_pages[0][i]
                if (i + 1) in cached_pages[1]:
                    side.add(i + 1)
                self._cache_hits += 1
            else:
                hit = self.handle.cache.get(f"{RUNNER_PAGE_KEY}:{base}:{i + 1}") if self.handle else None
                if isinstance(hit, dict) and isinstance(hit.get("text"), str):
                    texts[i] = hit["text"]
                    if hit.get("sideways"):
                        side.add(i + 1)
                    self._cache_hits += 1
                else:
                    todo.append(i)
        if todo:
            with pymupdf.open(str(path)) as doc:
                for i in todo:
                    pix = doc[i].get_pixmap(dpi=PAGE_DPI)
                    a = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width, pix.n)
                    turned: List[bool] = []
                    m = self.log.mark()
                    t = dv._ocr_image(a[:, :, :3], turned)
                    calls = self.log.since(m)
                    texts[i] = t
                    secs[i] = calls[0]["s"] if calls else 0.0
                    if turned:
                        side.add(i + 1)
                    if self.handle is not None:
                        self.handle.cache[f"{RUNNER_PAGE_KEY}:{base}:{i + 1}"] = {"text": t, "sideways": bool(turned)}
                        self.handle.dirty = True
        return texts, side, secs

    def read_source(self, src: Source, staged: Dict[str, Path],
                    given: Optional[PageTexts] = None) -> List[PageRead]:
        """bundle: read_file(src.root); folder: read_file(staged copy) for each file in name order."""
        pages: List[PageRead] = []
        for f in src.files:
            path = src.root if src.mode == "bundle" else (staged.get(f.name) or f.path)
            pages += self.read_file(path, f.name, len(pages), given)
        return pages

    # ------------------------------------------------------------------ files
    def cut(self, bundle: Path, parts: Sequence[Tuple[str, int, int]], out_dir: Path) -> List[Path]:
        """light_engine.cut_bundle, after checking that out_dir lies under private-runner."""
        out_dir = Path(out_dir)
        safety.assert_writable(out_dir)
        for name, _a, _b in parts:
            if Path(name).name != name or not name:
                raise ValueError("a cut file name must not hold a folder")
        return cut_bundle(Path(bundle), parts, out_dir)

    def _path_of(self, page: PageRead) -> Path:
        try:
            return self._files[page.file]
        except KeyError:
            raise KeyError("the page's file was not read by this engine") from None

    def cut_page(self, page: PageRead, out_dir: Path) -> Path:
        """A one-page PDF of that run page, "<file stem> p<NN>.pdf" (NN = the page within its file,
        1-based), with the source file's mtime; an image page becomes a one-page PDF."""
        import pymupdf
        out_dir = Path(out_dir)
        safety.assert_writable(out_dir)
        src = self._path_of(page)
        out_dir.mkdir(parents=True, exist_ok=True)
        dst = out_dir / f"{Path(page.file).stem} p{page.file_page + 1:02d}.pdf"
        if src.suffix.lower() in IMAGE_SUFFIXES:
            with pymupdf.open(str(src)) as img:
                data = img.convert_to_pdf()
            dst.write_bytes(data)
        else:
            with pymupdf.open(str(src)) as s:
                d = pymupdf.open()
                d.insert_pdf(s, from_page=page.file_page, to_page=page.file_page)
                d.save(str(dst), garbage=3, deflate=False)
                d.close()
        st = src.stat()
        os.utime(dst, (st.st_atime, st.st_mtime))
        return dst

    def page_image(self, path: Path, page: int, dpi: int = 300):
        """RGB uint8 array of a page (pymupdf render at `dpi`); an image file at its own pixels (PIL)."""
        import numpy as np
        path = Path(path)
        if path.suffix.lower() in IMAGE_SUFFIXES:
            from PIL import Image
            with Image.open(path) as im:
                return np.array(im.convert("RGB"))
        import pymupdf
        with pymupdf.open(str(path)) as doc:
            pix = doc[page].get_pixmap(dpi=dpi)
            a = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width, pix.n)
            return a[:, :, :3].copy()

    def page_image_of(self, page: PageRead, dpi: int = 300):
        """page_image() of a run page, memoised per (file, page, dpi) for the last IMAGE_MEMO renders, at most
        IMAGE_MEMO_MB of them (rev 2.7; the newest is always kept). The array is shared and read-only: copy it
        before changing it."""
        key = (page.file, page.file_page, int(dpi))
        img = self._images.get(key)
        if img is None:
            img = self.page_image(self._path_of(page), page.file_page, dpi)
            img.flags.writeable = False
            self._images[key] = img
            while len(self._images) > 1 and (len(self._images) > IMAGE_MEMO or
                                             sum(a.nbytes for a in self._images.values()) > IMAGE_MEMO_MB * 2 ** 20):
                self._images.popitem(last=False)
        else:
            self._images.move_to_end(key)
        return img

    # ------------------------------------------------------------------ a page read turned (rev 2.5)
    def read_turned(self, page: PageRead, angle: int) -> str:
        """The page rendered at the read resolution (PAGE_DPI), turned `angle` degrees clockwise (90, 180, 270) and
        read by the light reader (CPU), as dv._ocr_image reads a page (words joined by spaces). Kept in the case cache
        (RUNNER-TURN:<name>:<size>:<mtime>:<page>:<angle>), so a re-run reads nothing again."""
        import cv2
        codes = {90: cv2.ROTATE_90_CLOCKWISE, 180: cv2.ROTATE_180, 270: cv2.ROTATE_90_COUNTERCLOCKWISE}
        if angle not in codes:
            raise ValueError("a page is turned by 90, 180 or 270 degrees")
        path = self._path_of(page)
        key = None
        if self.handle is not None:
            st = Path(path).stat()
            key = f"{TURN_KEY}:{Path(path).name}:{st.st_size}:{int(st.st_mtime)}:{page.file_page + 1}:{angle}"
            hit = self.handle.cache.get(key)
            if isinstance(hit, dict) and isinstance(hit.get("text"), str):
                self._cache_hits += 1
                return hit["text"]
        img = self.page_image_of(page, PAGE_DPI)
        text = " ".join(str(t) for t in self.reader.readtext(cv2.rotate(img, codes[angle]), detail=0))
        if key is not None:
            self.handle.cache[key] = {"text": text}
            self.handle.dirty = True
        return text

    def line_ratio(self, page: PageRead) -> float:
        """text_line_ratio() of the page rendered at LINE_DPI: > 1 its text lines run across the page (upright or
        upside down), < 1 down it (scanned sideways). Milliseconds; no reading."""
        return text_line_ratio(self.page_image_of(page, LINE_DPI))

    # ------------------------------------------------------------------ OCR of pictures
    def read_text(self, img) -> str:
        """The bot's dv._ocr_image on a picture (rotation retries), through PageLog (memo, timing)."""
        return self.bot.dv._ocr_image(img)

    def read_boxes(self, img) -> List[Tuple[list, str, float]]:
        """reader.readtext(img, detail=1): [(box as four [x, y] points, text, confidence)]."""
        out = []
        for box, text, conf in self.reader.readtext(img, detail=1):
            out.append(([[int(round(float(x))), int(round(float(y)))] for x, y in box], str(text), float(conf)))
        return out

    # ------------------------------------------------------------------ priming
    def prime(self, cut_path: Path, page_texts: List[str]) -> None:
        """Writes "PAGES:<name>:<size>:<int mtime>:20" = {"pages": page_texts} into the open case cache
        (smoke_test2.prime_pages) and drops any other entry of that file, so dv.read_document and
        dv.read_pages on the cut file serve these texts and make no fresh OCR (one read per page)."""
        h = self.handle
        if h is None:
            raise RuntimeError("prime() needs an open case (Engine.case)")
        if not page_texts:
            return
        p = Path(cut_path)
        st = p.stat()
        base = f"{p.name}:{st.st_size}:{int(st.st_mtime)}"
        for k in [k for k in h.cache if k.startswith(base + ":") or k.startswith("PAGES:" + base + ":")]:
            del h.cache[k]
        h.cache[f"PAGES:{base}:{self.bot.TC.MAX_PAGES}"] = {"pages": [str(t) for t in page_texts],
                                                            "sideways": []}
        h.dirty = True

    # ------------------------------------------------------------------ statistics
    def stats(self) -> Dict[str, Any]:
        calls = self.log.calls
        return {"ocr_calls": self.reader.calls, "ocr_seconds": round(self.reader.seconds, 2),
                "ocr_pages": sum(not c["memo"] for c in calls), "memo_hits": sum(bool(c["memo"]) for c in calls),
                "cache_hits": self._cache_hits,
                "pages_by_engine": {k: self._pages_by_engine.get(k, 0) for k in ("gpu", "cpu", "layer")}}
