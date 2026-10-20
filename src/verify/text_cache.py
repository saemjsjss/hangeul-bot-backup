"""
The document check's two caches, so a file is read (and OCR'd) once and its picture is looked
at once, however many times the rules are run against it:

  data/verification/text/<PASSPORT>.json    what each file says
      "name:size:mtime:max_pages"           the whole file's text and page sizes
      "PAGES:name:size:mtime:max_pages"     each page's text (and the pages read sideways)
  data/verification/images/<PASSPORT>.json  what each file looks like (CPU only, no OCR)
      "fn:name:size:mtime:args"             page_checks.inspect / seal_present / page_codes

Every key carries the file's name, size and modification time, so a replaced or shrunk file is
read again and an unchanged one never is. The text-cache key format is the one the bot has always
used (src.cloud.records reads it for Supabase).

install() makes doc_verifier, field_check and page_checks read through both caches; uninstall()
saves them and puts the real readers back. One page limit (MAX_PAGES = 20) is used everywhere: a
file read whole under an older, smaller limit is reused when that read already covered every page,
and a file read page by page gives its whole text too, so most files never need a second OCR.

In cache-only mode (re-evaluating stored verdicts against new rules) nothing is OCR'd: text that
was never saved raises NotCached and is listed in `misses`, and that student waits for the normal
queue. Image facts are still computed then (CPU, no OCR). A technical failure (CheckError, an
"[unreadable: …]" read, a page list that came back empty) is never cached, so the next pass reads
the file again instead of repeating the failure.
"""
from __future__ import annotations

import copy
import json
import logging
import os
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from src.config import settings
from src.verify.common import NotCached

logger = logging.getLogger(__name__)

MAX_PAGES = 20
TEXT_DIR = settings.verification_dir() / "text"        # rebound by auto_verify.use_data_dir
IMAGES_DIR = settings.verification_dir() / "images"
IMAGE_SUFFIXES = (".jpg", ".jpeg", ".png")


@dataclass
class CacheHandle:
    cache: Dict[str, Any]
    path: Path
    images: Dict[str, Any]
    images_path: Path
    misses: List[str] = field(default_factory=list)     # files whose text was never saved (cache-only)
    cache_only: bool = False
    fresh: List[str] = field(default_factory=list)      # files read through the real reader this time
    dirty: bool = False
    images_dirty: bool = False
    saved: List[Tuple[Any, str, Any]] = field(default_factory=list)   # (module, name, what was there)

    @property
    def text_source(self) -> str:
        """"ocr" when any file was read for real, else "cache" (the record's engine.text)."""
        return "ocr" if self.fresh else "cache"


# --- small helpers -------------------------------------------------------------------------
def _ident(path) -> Optional[Tuple[str, int, int]]:
    try:
        st = Path(path).stat()
    except OSError:
        return None
    return Path(path).name, st.st_size, int(st.st_mtime)


def _is_image(path) -> bool:
    return Path(path).suffix.lower() in IMAGE_SUFFIXES


def page_count(path) -> Optional[int]:
    """How many pages the file has, read without rendering any (None when it will not open)."""
    if _is_image(path):
        return 1
    try:
        import pymupdf
        doc = pymupdf.open(str(path))
    except Exception:
        return None
    try:
        return len(doc)
    finally:
        doc.close()


def _page_sizes(path, n: int) -> Optional[List[Tuple[int, int]]]:
    """Each of the first `n` pages' size, as read_document reports it (an image: its pixels; a
    PDF page: its rect in points). None when the file will not open."""
    try:
        if _is_image(path):
            from PIL import Image
            with Image.open(path) as im:
                return [tuple(im.size)]
        import pymupdf
        doc = pymupdf.open(str(path))
    except Exception:
        return None
    try:
        return [(round(p.rect.width), round(p.rect.height)) for i, p in enumerate(doc) if i < n]
    finally:
        doc.close()


def _good_text(hit: Any) -> bool:
    """A whole-file entry that may be served: real text (not a failed open) and its page sizes."""
    return (isinstance(hit, dict) and isinstance(hit.get("text"), str) and isinstance(hit.get("sizes"), list)
            and bool(hit["sizes"]) and not hit["text"].lstrip().startswith("[unreadable"))


def _good_pages(hit: Any) -> bool:
    """A per-page entry that may be served: at least one page (an empty list came from a file
    that would not open)."""
    return isinstance(hit, dict) and isinstance(hit.get("pages"), list) and bool(hit["pages"])


def _entries(cache: Dict[str, Any], prefix: str):
    """(max_pages, value) of every entry whose key is `prefix` + ":<max_pages>"."""
    for key, value in cache.items():
        if not key.startswith(prefix + ":"):
            continue
        tail = key[len(prefix) + 1:]
        if tail.isdigit():
            yield int(tail), value


class _Lazy:
    """A value worked out once, and only if someone asks (the page count needs the file opened)."""

    def __init__(self, fn: Callable[[], Any]):
        self._fn, self._done, self._value = fn, False, None

    def __call__(self):
        if not self._done:
            self._value, self._done = self._fn(), True
        return self._value


def _load(path: Path) -> Dict[str, Any]:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            return data
        raise ValueError("not an object")
    except Exception as e:
        logger.warning("cache %s unreadable, starting it again: %s", path.name, e)
        return {}


def _save(path: Path, data: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, path)


# --- lookups -------------------------------------------------------------------------------
def whole_text(cache: Dict[str, Any], path, n: int = MAX_PAGES) -> Optional[Tuple[str, List[Tuple[int, int]], bool]]:
    """read_document(path, n) from the cache, or None: (text, sizes, composed). In this order:
      1. the exact key name:size:mtime:n;
      2. any whole-file entry of the same file that already covered every page (fewer pages than
         its limit, as many pages as the file has, or an image) and no more than n of them;
      3. a per-page entry holding the first n pages (or all of them), joined with "\\n", with the
         sizes taken from the page rects.
    `composed` is True for 2 and 3: the caller stores the result under the exact key."""
    ident = _ident(path)
    if ident is None:
        return None
    base = "{}:{}:{}".format(*ident)
    hit = cache.get(f"{base}:{n}")
    if _good_text(hit):
        return hit["text"], [tuple(x) for x in hit["sizes"]], False
    total = _Lazy(lambda: page_count(path))
    image = _is_image(path)
    for mp, hit in _entries(cache, base):
        if mp == n or not _good_text(hit):
            continue
        k = len(hit["sizes"])
        if k <= n and (image or k < mp or (total() is not None and k >= total())):
            return hit["text"], [tuple(x) for x in hit["sizes"]], True
    for mp, hit in _entries(cache, f"PAGES:{base}"):
        if not _good_pages(hit):
            continue
        pages = hit["pages"]
        if image or len(pages) < mp or len(pages) >= n or (total() is not None and len(pages) >= total()):
            sizes = _page_sizes(path, n)
            if sizes is None or len(sizes) != len(pages[:n]):
                continue
            return "\n".join(str(p) for p in pages[:n]), sizes, True
    return None


def page_texts(cache: Dict[str, Any], path, n: int = MAX_PAGES) -> Optional[Tuple[List[str], List[int]]]:
    """read_pages(path, n) from the cache, or None: (pages, sideways page numbers). Any per-page
    entry that holds the first n pages (a larger limit, or every page of the file) serves
    pages[:n], keeping only the sideways pages among them. A one-page file read whole (an image,
    a one-page PDF) serves its text as its only page."""
    ident = _ident(path)
    if ident is None:
        return None
    base = "{}:{}:{}".format(*ident)
    hit = cache.get(f"PAGES:{base}:{n}")
    if _good_pages(hit):
        return list(hit["pages"]), list(hit.get("sideways") or [])
    total = _Lazy(lambda: page_count(path))
    for mp, hit in _entries(cache, f"PAGES:{base}"):
        if not _good_pages(hit):
            continue
        pages = hit["pages"]
        if mp >= n or len(pages) < mp or (total() is not None and len(pages) >= total()):
            return list(pages[:n]), [s for s in hit.get("sideways") or [] if isinstance(s, int) and s <= n]
    for mp, hit in _entries(cache, base):
        if _good_text(hit) and len(hit["sizes"]) == 1 and (_is_image(path) or 1 < mp or total() == 1):
            return [hit["text"]], []
    return None


# --- install / uninstall -------------------------------------------------------------------
def _unwrap(fn):
    """The real reader behind one of our wrappers (install() may meet its own earlier patch)."""
    return getattr(fn, "_text_cache_original", fn)


def _patch(handle: CacheHandle, module, name: str, wrapper, original) -> None:
    wrapper._text_cache_original = original
    handle.saved.append((module, name, getattr(module, name)))
    setattr(module, name, wrapper)


def install(pas: str, *, cache_only: bool = False, text_dir: Optional[Path] = None,
            images_dir: Optional[Path] = None) -> CacheHandle:
    """Make doc_verifier, field_check and page_checks read `pas`'s files through the caches:
    dv.read_document, dv.read_pages, fc.read_document (field_check imported it by name),
    PC.inspect, PC.seal_present and PC.page_codes. `text_dir` / `images_dir` default to this
    module's TEXT_DIR / IMAGES_DIR as they are now. Always pair it with uninstall()."""
    from src.verify import doc_verifier as dv
    from src.verify import field_check as fc
    from src.verify import page_checks as PC

    tdir = Path(text_dir) if text_dir is not None else TEXT_DIR
    idir = Path(images_dir) if images_dir is not None else IMAGES_DIR
    path, images_path = tdir / f"{pas}.json", idir / f"{pas}.json"
    handle = CacheHandle(cache=_load(path), path=path, images=_load(images_path), images_path=images_path,
                         cache_only=cache_only)
    read_document = _unwrap(dv.read_document)
    read_pages = _unwrap(dv.read_pages)

    def _miss(file_path, what: str):
        name = Path(file_path).name
        if name not in handle.misses:
            handle.misses.append(name)
        raise NotCached(Path(file_path), what)

    def cached_document(file_path, max_pages: int = MAX_PAGES):
        f = Path(file_path)
        ident = _ident(f)
        hit = whole_text(handle.cache, f, max_pages) if ident else None
        if hit is not None:
            text, sizes, composed = hit
            if composed:
                handle.cache["{}:{}:{}:{}".format(*ident, max_pages)] = {"text": text, "sizes": [list(x) for x in sizes]}
                handle.dirty = True
            return text, sizes
        if cache_only:
            _miss(f, "text")
        text, sizes = read_document(file_path, max_pages)
        if f.name not in handle.fresh:
            handle.fresh.append(f.name)
        entry = {"text": text, "sizes": [list(x) for x in sizes]}
        if ident and _good_text(entry):            # a failed open is never kept (it is retried)
            handle.cache["{}:{}:{}:{}".format(*ident, max_pages)] = entry
            handle.dirty = True
        return text, sizes

    def cached_pages(file_path, max_pages: int = MAX_PAGES, sideways=None):
        f = Path(file_path)
        ident = _ident(f)
        hit = page_texts(handle.cache, f, max_pages) if ident else None
        if hit is not None:
            pages, turned = hit
            if sideways is not None:
                sideways.extend(turned)
            return pages
        if cache_only:
            _miss(f, "page text")
        turned: List[int] = []
        pages = read_pages(file_path, max_pages, turned)
        if f.name not in handle.fresh:
            handle.fresh.append(f.name)
        if ident and pages:                        # an empty list came from a file that would not open
            handle.cache["PAGES:{}:{}:{}:{}".format(*ident, max_pages)] = {"pages": list(pages), "sideways": list(turned)}
            handle.dirty = True
        if sideways is not None:
            sideways.extend(turned)
        return pages

    def facts(fn_name: str, original, file_path, args: Tuple[Any, ...]):
        """An image fact from the cache, or worked out now and kept (cache-only mode too: it
        needs the CPU, not OCR). A CheckError propagates and is not kept; neither is an
        inspect() that could not render the file, nor anything JSON cannot hold."""
        ident = _ident(file_path)
        if ident is None:
            return original(file_path, *args)
        key = "{}:{}:{}:{}:{}".format(fn_name, *ident, ",".join(str(a) for a in args))
        if key in handle.images:
            return copy.deepcopy(handle.images[key])
        value = original(file_path, *args)
        if fn_name == "inspect" and isinstance(value, dict) and value.get("error"):
            return value
        try:
            kept = json.loads(json.dumps(value))
        except (TypeError, ValueError):
            return value
        handle.images[key] = kept
        handle.images_dirty = True
        return copy.deepcopy(kept)

    inspect, seal_present, page_codes = _unwrap(PC.inspect), _unwrap(PC.seal_present), _unwrap(PC.page_codes)

    def cached_inspect(file_path):
        return facts("inspect", inspect, file_path, ())

    def cached_seal(file_path):
        return facts("seal_present", seal_present, file_path, ())

    def cached_codes(file_path, max_pages: int = MAX_PAGES):
        return facts("page_codes", page_codes, file_path, (max_pages,))

    _patch(handle, dv, "read_document", cached_document, read_document)
    _patch(handle, fc, "read_document", cached_document, read_document)
    _patch(handle, dv, "read_pages", cached_pages, read_pages)
    _patch(handle, PC, "inspect", cached_inspect, inspect)
    _patch(handle, PC, "seal_present", cached_seal, seal_present)
    _patch(handle, PC, "page_codes", cached_codes, page_codes)
    return handle


def uninstall(handle: CacheHandle) -> None:
    """Save both caches (only when something new went in) and put back what install() replaced."""
    try:
        if handle.dirty:
            _save(handle.path, handle.cache)
            handle.dirty = False
    except Exception as e:
        logger.warning("could not save the text cache %s: %s", handle.path.name, e)
    try:
        if handle.images_dirty:
            _save(handle.images_path, handle.images)
            handle.images_dirty = False
    except Exception as e:
        logger.warning("could not save the image cache %s: %s", handle.images_path.name, e)
    for module, name, previous in reversed(handle.saved):
        setattr(module, name, previous)
    handle.saved.clear()


# --- what read the files -------------------------------------------------------------------
@lru_cache(maxsize=1)
def _static_engine() -> Tuple[str, str, str]:
    from importlib import metadata

    def version(*names: str) -> str:
        for n in names:
            try:
                return metadata.version(n)
            except Exception:
                continue
        return "missing"

    try:
        from src.verify import page_checks as PC
        if hasattr(PC, "zbar_available"):
            zbar = bool(PC.zbar_available()[0])
        else:
            import pyzbar.pyzbar  # noqa: F401
            zbar = True
    except Exception:
        zbar = False
    return version("easyocr"), version("PyMuPDF", "pymupdf"), "available" if zbar else "missing"


def engine_info() -> Dict[str, str]:
    """What this evaluation was made with: {"ocr": "easyocr <version>", "device": "cuda" | "cpu" |
    "not used" (no OCR reader was started), "pymupdf": "<version>", "pyzbar": "available" |
    "missing"}. The caller adds "text": "cache" | "ocr"."""
    from src.verify import doc_verifier as dv
    ocr, pymupdf_version, zbar = _static_engine()
    reader = getattr(dv, "_reader", None)
    device = str(getattr(reader, "device", "") or "cpu") if reader is not None else "not used"
    return {"ocr": f"easyocr {ocr}", "device": device, "pymupdf": pymupdf_version, "pyzbar": zbar}
