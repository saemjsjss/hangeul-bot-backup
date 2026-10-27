"""Input of the document-check test runner: one bundle PDF or one folder of bot-named files (RUNNER_SPEC
section 5.1; Task 1).

open_input() turns the input into a model.Source (read-only: it opens each file to count its pages and
hashes the bundle's bytes; it never writes). classify_name() gives a folder file's bot key from its name:
the bot's rules.FILE_PATTERNS with doc_verifier.classify semantics (longest pattern wins; the name as it
is and with "_" as a space), except that a pattern of at most 4 letters must match as a whole word
("CONTINUATION" is not a TIN), plus the runner's overlay RUNNER_FILE_PATTERNS (the checker inventory
probe's fixes: TRADE LICENCE, HSC, SSC, TAX RETURN, SALARY CERTIFICATE, CV ...). stage() copies a
folder's files into the run's docs\\ folder with the same name, size and modification time, so the bot's
text-cache key name:size:mtime stays the original's.

Importable in the bot venv too (the GPU worker uses open_input and file_sha256): standard library plus
pymupdf / PIL, imported when needed; the bot's rules module is imported directly (it needs no config).
No personal data.
"""
from __future__ import annotations

import hashlib
import re
import shutil
import stat as _stat
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

from .model import Source, SourceFile
from . import safety
from .safety import file_sha256  # noqa: F401  (the one definition: engine.load_page_texts and gpuread use it)

PDF_SUFFIXES = (".pdf",)
IMAGE_SUFFIXES = (".jpg", ".jpeg", ".png")
INPUT_SUFFIXES = PDF_SUFFIXES + IMAGE_SUFFIXES
SHORT_PATTERN_LETTERS = 4          # a pattern of at most this many letters must match as a whole word

RUNNER_FILE_PATTERNS: Dict[str, List[str]] = {
    "trade_license": ["trade licence", "tin certificate", "e-tin", "etin", "salary certificate", "employment"],
    "academic": ["hsc", "ssc", "apostille", "marksheet", "mark sheet", "academic"],
    "income_tax": ["tax return", "return acknowledgement"],
    "father_nid": ["father id", "father national id"],
    "mother_nid": ["mother id", "mother national id"],
    "id_card_cv": ["cv", "resume"],
    "language_cert": ["duolingo", "pte", "english score", "englishscore", "language"],
    "personal_statement": ["sop", "statement of purpose"],
}


class InputError(Exception):
    """The input cannot be used: not a .pdf or a folder, an empty folder, a file that will not open, or
    page texts that do not belong to it. The CLI turns it into exit 2 (nothing written)."""


# --------------------------------------------------------------------------------- classification
def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "")).strip().lower()


def _letters(p: str) -> int:
    return sum(ch.isalpha() for ch in p)


def _matches(pattern: str, name: str) -> bool:
    if _letters(pattern) <= SHORT_PATTERN_LETTERS:
        return re.search(r"(?<![a-z0-9])" + re.escape(pattern) + r"(?![a-z0-9])", name) is not None
    return pattern in name


def _all_patterns() -> List[Tuple[str, str]]:
    from src.verify import rules as R          # pure module: no src.config, no OCR stack
    out = [(k, p) for k, pats in R.FILE_PATTERNS.items() for p in pats]
    out += [(k, p) for k, pats in RUNNER_FILE_PATTERNS.items() for p in pats]
    return out


def classify_name(filename: str) -> Optional[str]:
    """The bot key a file name gives, or None. The bot's FILE_PATTERNS first, then the runner overlay; the
    longest matching pattern wins (the first one on a tie); the name is tried as it is and with "_" as a
    space; a pattern of at most 4 letters must stand as a whole word."""
    names = {_norm(filename), _norm(filename.replace("_", " "))}
    best, best_len = None, 0
    for key, p in _all_patterns():
        if len(p) > best_len and any(_matches(p, n) for n in names):
            best, best_len = key, len(p)
    return best


# --------------------------------------------------------------------------------- opening the input
def _sha1_file(path: Path) -> str:
    h = hashlib.sha1()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _hidden(p: Path) -> bool:
    if p.name.startswith((".", "~$")):
        return True
    try:
        attrs = getattr(p.stat(), "st_file_attributes", 0)
    except OSError:
        return True
    return bool(attrs & (getattr(_stat, "FILE_ATTRIBUTE_HIDDEN", 0) | getattr(_stat, "FILE_ATTRIBUTE_SYSTEM", 0)))


def file_label(k: int, n: int, mode: str = "folder") -> str:
    """How an error line names an input file: never by its name (a student's file or folder may carry the
    student's name, and error lines reach the agent's transcript, S3). "the bundle", or "file <k> of <n> in the
    folder (sorted by name)": the owner finds it by that position."""
    return "the bundle" if mode == "bundle" else f"file {k} of {n} in the folder (sorted by name)"


def pdf_page_count(path: Path, label: str = "the PDF") -> int:
    """Pages of a PDF; raises InputError when it will not open, needs a password or has no page. The error
    names the file by `label` (file_label), never by its name."""
    import pymupdf
    try:
        doc = pymupdf.open(str(path))
    except Exception as e:
        raise InputError(f"{label} could not be opened as a PDF: {type(e).__name__}") from e
    try:
        if getattr(doc, "needs_pass", False):
            raise InputError(f"{label} is password-protected")
        if not doc.is_pdf:
            raise InputError(f"{label} is not a PDF")
        n = len(doc)
    finally:
        doc.close()
    if n < 1:
        raise InputError(f"{label} has no pages")
    return n


def _image_ok(path: Path, label: str = "the image") -> None:
    from PIL import Image
    try:
        with Image.open(path) as im:
            im.size
    except Exception as e:
        raise InputError(f"{label} could not be opened as an image: {type(e).__name__}") from e


def _source_file(p: Path, key: Optional[str], label: str = "the file") -> SourceFile:
    st = p.stat()
    image = p.suffix.lower() in IMAGE_SUFFIXES
    if image:
        _image_ok(p, label)
        n = 1
    else:
        n = pdf_page_count(p, label)
    return SourceFile(path=p, name=p.name, is_image=image, n_pages=n, size=st.st_size, mtime=st.st_mtime,
                      key_by_name=key)


def folder_files(folder: Path) -> List[Path]:
    """The folder's .pdf/.jpg/.jpeg/.png files: not recursive, hidden / system and ~$ files skipped,
    sorted by name (case-insensitive, as Windows lists them)."""
    files = [p for p in Path(folder).iterdir()
             if p.is_file() and p.suffix.lower() in INPUT_SUFFIXES and not _hidden(p)]
    return sorted(files, key=lambda p: (p.name.casefold(), p.name))


def folder_case_id(files: Sequence[SourceFile]) -> str:
    joined = "\n".join(f"{f.name}|{f.size}|{int(f.mtime)}" for f in files)
    return "F-" + hashlib.sha1(joined.encode("utf-8")).hexdigest()[:16]


def open_input(path: Path) -> Source:
    """A .pdf file -> mode "bundle" (one SourceFile); a folder -> mode "folder" (its .pdf/.jpg/.jpeg/.png
    files, not recursive, sorted by name, hidden and ~$ files skipped); anything else -> InputError.
    case_id: "B-" + sha1(bundle bytes)[:16], or "F-" + sha1("name|size|int(mtime)" lines)[:16]."""
    p = Path(path)
    if not p.is_absolute():
        raise InputError("the input must be an absolute path")
    if p.is_file():
        if p.suffix.lower() not in PDF_SUFFIXES:
            raise InputError("the input must be one bundle .pdf or one folder")
        f = _source_file(p, None, file_label(1, 1, "bundle"))
        return Source(mode="bundle", root=p, files=[f], case_id="B-" + _sha1_file(p)[:16])
    if p.is_dir():
        found = folder_files(p)
        if not found:
            raise InputError("the folder holds no .pdf, .jpg, .jpeg or .png file")
        files = [_source_file(f, classify_name(f.name), file_label(k, len(found)))
                 for k, f in enumerate(found, 1)]
        return Source(mode="folder", root=p, files=files, case_id=folder_case_id(files))
    raise InputError("the input does not exist or is not a file or folder")


def stage(src: Source, docs_dir: Path) -> Dict[str, Path]:
    """Folder mode: every file copied (shutil.copy2) to docs_dir/<same name>, keeping its size and
    int(mtime) (asserted), so the text-cache key of the copy is the original's. Bundle mode: {} (the
    bundle is read in place, read-only). docs_dir must lie under private-runner."""
    if src.mode != "folder":
        return {}
    docs_dir = Path(docs_dir)
    safety.assert_writable(docs_dir)
    docs_dir.mkdir(parents=True, exist_ok=True)
    out: Dict[str, Path] = {}
    for k, f in enumerate(src.files, 1):
        dst = docs_dir / f.name
        label = file_label(k, len(src.files))
        if dst.resolve() == Path(f.path).resolve():
            raise InputError(f"{label}: the staged copy would be the input itself")
        shutil.copy2(f.path, dst)
        st = dst.stat()
        if st.st_size != f.size or int(st.st_mtime) != int(f.mtime):
            raise InputError(f"{label}: the staged copy did not keep its size and modification time")
        out[f.name] = dst
    return out
