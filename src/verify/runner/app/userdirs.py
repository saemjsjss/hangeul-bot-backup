"""Folders of the window: the app data folder, the report folder, case labels, path lengths, the window's fast
pre-checks, the case folder and its marker, and the start-up housekeeping (APP_SPEC rev 1 sections 1.4, 3.2, 4.1;
rev 2 R2.1.2 for the ascii copies under tmp).

- DATA = %LOCALAPPDATA%\\Hangeul Document Checker (HDC_DATA_DIR overrides it, for tests).
- The default report root is <Documents>\\Hangeul Document Checks (Known Folder Documents, through ctypes), or
  %USERPROFILE%\\Hangeul Document Checks when Documents is synced by OneDrive (Q2's recommended default).
- A case folder is "<label> <PROGRAM> <YYYY-MM-DD HHMM>" (" (2)" ... on a clash). The label is the input's stem,
  Windows-safe, at most 40 characters, and cut further until the longest output path stays under 240 characters.
- A case folder made by the window holds the marker ".hdc-run-<job>" while its check runs; only a folder holding
  that marker is ever removed (a cancelled check).

Standard library only (ctypes for the Known Folder call). No personal data: nothing here prints a path.
"""
from __future__ import annotations

import datetime as dt
import os
import re
import stat as _stat
import time
import unicodedata
import uuid
from pathlib import Path
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence, Union

from . import messages as M

APP_DIR_NAME = "Hangeul Document Checker"
REPORTS_DIR_NAME = "Hangeul Document Checks"
DATA_ENV = "HDC_DATA_DIR"

LABEL_MAX = 40                  # case labels are cut to 40 characters
PATH_LIMIT = 240                # the longest output path must stay under this
INPUT_PATH_LIMIT = 260          # an input path of 260 or more is refused
REPORT_ROOT_LIMIT = 150         # a report root over 150 characters is refused
LONGEST_UNIT_NAME = 44          # split.unit_file_name: "ACADEMIC CERTIFICATE DIPLOMA p100-120.pdf" is 41
LONGEST_WORK_NAME = 40          # names / birth crops: "look_p01_12.png", "birth_qr_p01.json"
CLASH_SUFFIX_MAX = len(" (99)")
MAX_CLASH = 99
MARKER_PREFIX = ".hdc-run-"
DOC_SUFFIXES = (".pdf", ".jpg", ".jpeg", ".png")     # = ingest.INPUT_SUFFIXES
FREE_DISK_EXTRA = 500 * 1024 * 1024                  # free disk >= 2 x input + 500 MB
KEEP_WORKER_LOGS = 20
_RESERVED = {"CON", "PRN", "AUX", "NUL"} | {f"COM{i}" for i in range(1, 10)} | {f"LPT{i}" for i in range(1, 10)}
_BAD_CHARS = set('<>:"/\\|?*')

FOLDERID_DOCUMENTS = "FDD39AD0-238F-46AF-ADB4-6C85480369C7"
FOLDERID_LOCAL_APP_DATA = "F1B32785-6FBA-4FCF-9D55-7B8E7F157091"

PathLike = Union[str, Path]


# ================================================================================================ known folders
def known_folder(guid: str, shell32: Any = None, ole32: Any = None) -> Optional[Path]:
    """SHGetKnownFolderPath(guid) through ctypes; None when it fails. `shell32` / `ole32` may be fakes."""
    try:
        import ctypes
        from ctypes import wintypes

        class _GUID(ctypes.Structure):
            _fields_ = [("Data1", wintypes.DWORD), ("Data2", wintypes.WORD), ("Data3", wintypes.WORD),
                        ("Data4", ctypes.c_ubyte * 8)]

        u = uuid.UUID(guid)
        g = _GUID(u.time_low, u.time_mid, u.time_hi_version, (ctypes.c_ubyte * 8)(*u.bytes[8:]))
        if shell32 is None:
            shell32 = ctypes.WinDLL("shell32")
        if ole32 is None:
            ole32 = ctypes.WinDLL("ole32")
        fn = shell32.SHGetKnownFolderPath
        fn.argtypes = [ctypes.POINTER(_GUID), wintypes.DWORD, wintypes.HANDLE, ctypes.POINTER(ctypes.c_wchar_p)]
        fn.restype = ctypes.c_long
        out = ctypes.c_wchar_p()
        hr = fn(ctypes.byref(g), 0, None, ctypes.byref(out))
        try:
            value = out.value if hr == 0 else None
        finally:
            if out:
                free = ole32.CoTaskMemFree
                free.argtypes = [ctypes.c_void_p]
                free.restype = None
                free(ctypes.cast(out, ctypes.c_void_p))
        return Path(value) if value else None
    except Exception:                                   # no Windows shell (tests on fakes, odd PCs): fall back
        return None


def documents_dir(finder: Callable[[str], Optional[Path]] = known_folder) -> Path:
    p = finder(FOLDERID_DOCUMENTS)
    return p if p is not None else Path.home() / "Documents"


def data_dir(environ: Optional[Mapping[str, str]] = None,
             finder: Callable[[str], Optional[Path]] = known_folder) -> Path:
    """DATA: HDC_DATA_DIR when set (tests), else %LOCALAPPDATA%\\Hangeul Document Checker."""
    env = os.environ if environ is None else environ
    if env.get(DATA_ENV):
        return Path(env[DATA_ENV])
    base = env.get("LOCALAPPDATA")
    root = Path(base) if base else finder(FOLDERID_LOCAL_APP_DATA)
    if root is None:
        root = Path.home() / "AppData" / "Local"
    return root / APP_DIR_NAME


def onedrive_roots(environ: Optional[Mapping[str, str]] = None) -> List[str]:
    env = os.environ if environ is None else environ
    out = []
    for k in ("OneDrive", "OneDriveConsumer", "OneDriveCommercial"):
        v = env.get(k)
        if v:
            out.append(os.path.normcase(os.path.normpath(v)))
    return out


def is_synced(path: PathLike, environ: Optional[Mapping[str, str]] = None) -> bool:
    """True when `path` lies in a OneDrive folder (an OneDrive variable's folder, or a folder named OneDrive*)."""
    p = os.path.normcase(os.path.normpath(str(path)))
    for root in onedrive_roots(environ):
        if p == root or p.startswith(root.rstrip("\\") + "\\"):
            return True
    return any(part.casefold().startswith("onedrive") for part in Path(str(path)).parts[1:])


def default_report_root(environ: Optional[Mapping[str, str]] = None, documents: Optional[PathLike] = None,
                        home: Optional[PathLike] = None) -> Path:
    """<Documents>\\Hangeul Document Checks, or <home>\\Hangeul Document Checks when Documents is synced."""
    env = os.environ if environ is None else environ
    docs = Path(documents) if documents is not None else documents_dir()
    if not is_synced(docs, env):
        return docs / REPORTS_DIR_NAME
    h = Path(home) if home is not None else Path(env.get("USERPROFILE") or Path.home())
    return h / REPORTS_DIR_NAME


# ================================================================================================ labels
def input_stem(input_path: PathLike) -> str:
    """The name a case label starts from: a file's stem, a folder's name, "drive X" for a drive root."""
    p = Path(str(input_path))
    if p.suffix.lower() in DOC_SUFFIXES and p.name:
        return p.stem
    if p.name:
        return p.name
    drive = p.drive.rstrip(":\\/")
    return f"drive {drive}" if drive else "case"


def safe_label(stem: Any, limit: int = LABEL_MAX) -> str:
    """A Windows-safe label: the characters <>:"/\\|?* and control characters become spaces, whitespace runs
    become one space, leading and trailing dots and spaces go, a device name (CON, NUL ...) gets a "_" in front;
    cut to `limit` characters (never below 1); "case" when nothing is left."""
    s = unicodedata.normalize("NFC", str(stem or ""))
    s = "".join(" " if (ch in _BAD_CHARS or unicodedata.category(ch) in ("Cc", "Cs", "Zl", "Zp")) else ch
                for ch in s)
    s = " ".join(s.split()).strip(" .")
    limit = max(1, int(limit))
    s = s[:limit].rstrip(" .")
    if not s:
        return "case"
    if s.split(".")[0].strip().upper() in _RESERVED:
        s = ("_" + s)[:limit].rstrip(" .") or "case"
    return s


def case_folder_name(label: str, program: str, when: dt.datetime, n: int = 1) -> str:
    """"<label> <PROGRAM> <YYYY-MM-DD HHMM>", with " (n)" from the second one on."""
    base = f"{label} {program} {when:%Y-%m-%d %H%M}"
    return base if n <= 1 else f"{base} ({n})"


def longest_output_len(case_dir: PathLike, input_names: Sequence[str] = ()) -> int:
    """The longest path a check writes under its case folder: docs\\parts\\<unit file>, docs\\<input file>
    (folder mode copies), work\\<crop>, the marker and the fixed outputs."""
    c = len(str(case_dir))
    longest_input = max((len(n) for n in input_names), default=0)
    return max(c + len("\\docs\\parts\\") + LONGEST_UNIT_NAME,
               c + len("\\docs\\") + longest_input,
               c + len("\\work\\") + LONGEST_WORK_NAME,
               c + 1 + len(MARKER_PREFIX) + 32,
               c + len("\\checkfacts.json"))


def fit_label(report_root: PathLike, stem: str, program: str, when: dt.datetime,
              input_names: Sequence[str] = ()) -> Optional[str]:
    """The case label (<= 40 characters) cut until the longest output path, with room for a clash suffix,
    stays under PATH_LIMIT; None when even one character does not fit (PATH_TOO_LONG)."""
    root = str(report_root).rstrip("\\/")
    label = safe_label(stem, LABEL_MAX)
    while True:
        name = case_folder_name(label, program, when) + "x" * CLASH_SUFFIX_MAX
        if longest_output_len(root + "\\" + name, input_names) < PATH_LIMIT:
            return label
        if len(label) <= 1:
            return None
        label = safe_label(label, len(label) - 1)


# ================================================================================================ pre-checks
def _hidden(entry: os.DirEntry) -> bool:
    """ingest._hidden: dot and ~$ names, hidden or system files."""
    if entry.name.startswith((".", "~$")):
        return True
    try:
        attrs = getattr(entry.stat(follow_symlinks=False), "st_file_attributes", 0)
    except OSError:
        return True
    return bool(attrs & (getattr(_stat, "FILE_ATTRIBUTE_HIDDEN", 0) | getattr(_stat, "FILE_ATTRIBUTE_SYSTEM", 0)))


def _listing(folder: PathLike):
    files, dirs = [], []
    with os.scandir(str(folder)) as it:
        for e in it:
            try:
                if _hidden(e):
                    continue
                if e.is_file():
                    files.append(e)
                elif e.is_dir():
                    dirs.append(e)
            except OSError:
                continue
    return files, dirs


def document_files(folder: PathLike) -> List[str]:
    """The folder's visible .pdf/.jpg/.jpeg/.png file names (not recursive), as ingest lists them."""
    files, _ = _listing(folder)
    names = [e.name for e in files if os.path.splitext(e.name)[1].lower() in DOC_SUFFIXES]
    return sorted(names, key=lambda n: (n.casefold(), n))


def check_input(input_path: PathLike) -> Optional[str]:
    """The window's fast input checks (rev 1 section 3.2), in this order: a path of 260 or more characters ->
    PATH_TOO_LONG; not a .pdf file or a folder -> NOT_PDF_OR_FOLDER; a folder with no document file ->
    FOLDER_OF_FOLDERS when it holds visible folders, else EMPTY_FOLDER; a document path inside the folder of 260
    or more -> PATH_TOO_LONG. None when the input may be checked."""
    s = str(input_path or "")
    if not s:
        return M.NOT_PDF_OR_FOLDER
    if len(s) >= INPUT_PATH_LIMIT:
        return M.PATH_TOO_LONG
    p = Path(s)
    try:
        if p.is_file():
            return None if p.suffix.lower() == ".pdf" else M.NOT_PDF_OR_FOLDER
        if not p.is_dir():
            return M.NOT_PDF_OR_FOLDER
        files, dirs = _listing(p)
    except OSError:
        return M.NOT_PDF_OR_FOLDER
    docs = [e for e in files if os.path.splitext(e.name)[1].lower() in DOC_SUFFIXES]
    if not docs:
        return M.FOLDER_OF_FOLDERS if dirs else M.EMPTY_FOLDER
    if any(len(os.path.join(s, e.name)) >= INPUT_PATH_LIMIT for e in docs):
        return M.PATH_TOO_LONG
    return None


def input_names(input_path: PathLike) -> List[str]:
    """Folder mode: the document file names (they are copied to docs\\); a bundle: none."""
    p = Path(str(input_path))
    try:
        return document_files(p) if p.is_dir() else []
    except OSError:
        return []


def input_bytes(input_path: PathLike) -> int:
    p = Path(str(input_path))
    try:
        if p.is_file():
            return p.stat().st_size
        total = 0
        for name in document_files(p):
            try:
                total += (p / name).stat().st_size
            except OSError:
                continue
        return total
    except OSError:
        return 0


def _real(p: PathLike) -> str:
    try:
        r = os.path.realpath(os.path.abspath(str(p)))
    except (OSError, ValueError):
        r = os.path.abspath(str(p))
    return os.path.normcase(os.path.normpath(r))


def is_within(child: PathLike, parent: PathLike) -> bool:
    """True when `child` is `parent` or lies inside it (junctions and links resolved, case-insensitive)."""
    c, p = _real(child), _real(parent)
    if c == p:
        return True
    try:
        return os.path.commonpath([c, p]) == p
    except ValueError:                      # other drives
        return False


def check_report_root(root: PathLike, input_path: Optional[PathLike] = None, make: bool = True) -> Optional[str]:
    """The report folder checks, in this order: over 150 characters -> PATH_TOO_LONG; inside the input folder
    (or the input itself) -> REPORT_INSIDE_INPUT; the input inside it -> INPUT_INSIDE_REPORT; cannot be made or
    written -> REPORT_NOT_WRITABLE (a probe file is written and removed). None when it is fine."""
    s = str(root or "")
    if not s or not os.path.isabs(s):
        return M.REPORT_NOT_WRITABLE
    if len(s) > REPORT_ROOT_LIMIT:
        return M.PATH_TOO_LONG
    if input_path:
        if is_within(s, input_path):
            return M.REPORT_INSIDE_INPUT
        if is_within(input_path, s):
            return M.INPUT_INSIDE_REPORT
    if not make:
        return None
    try:
        os.makedirs(s, exist_ok=True)
        probe = os.path.join(s, f".hdc-write-test-{os.getpid()}-{uuid.uuid4().hex[:8]}")
        with open(probe, "x", encoding="ascii") as fh:
            fh.write("ok")
        os.remove(probe)
    except OSError:
        return M.REPORT_NOT_WRITABLE
    return None


def check_free_disk(root: PathLike, need_input_bytes: int,
                    disk_usage: Optional[Callable[[str], Any]] = None) -> Optional[str]:
    """DISK_FULL when the report folder's drive has less than 2 x the input + 500 MB free."""
    if disk_usage is None:
        import shutil
        disk_usage = shutil.disk_usage
    try:
        free = int(disk_usage(str(root)).free)
    except (OSError, AttributeError, TypeError, ValueError):
        return None                         # cannot tell: the worker's own write errors still show DISK_FULL
    return M.DISK_FULL if free < 2 * max(0, int(need_input_bytes)) + FREE_DISK_EXTRA else None


# ================================================================================================ case folder
def marker_name(job_id: str) -> str:
    if not re.fullmatch(r"[0-9a-f]{32}", str(job_id)):
        raise ValueError("job id must be 32 lower-case hex digits")
    return MARKER_PREFIX + str(job_id)


def make_case_folder(report_root: PathLike, label: str, program: str, when: dt.datetime, job_id: str) -> Path:
    """Make <root>\\<label> <PROGRAM> <YYYY-MM-DD HHMM>[ (n)] (never an existing folder) with the marker
    .hdc-run-<job> inside. OSError when no free name is left."""
    root = Path(str(report_root))
    root.mkdir(parents=True, exist_ok=True)
    marker = marker_name(job_id)
    for n in range(1, MAX_CLASH + 1):
        p = root / case_folder_name(label, program, when, n)
        try:
            os.mkdir(p)
        except FileExistsError:
            continue
        with open(p / marker, "x", encoding="ascii"):
            pass
        return p
    raise FileExistsError("no free case folder name")


def remove_marker(folder: PathLike, job_id: str) -> bool:
    try:
        os.remove(os.path.join(str(folder), marker_name(job_id)))
        return True
    except (OSError, ValueError):
        return False


def _is_link(entry: os.DirEntry) -> bool:
    try:
        if entry.is_symlink():
            return True
        j = getattr(entry, "is_junction", None)
        return bool(j and j())
    except OSError:
        return True


def _remove_link(path: str) -> None:
    try:
        os.rmdir(path)                      # a directory junction or directory link: the link only
    except OSError:
        os.unlink(path)


def remove_tree(path: PathLike) -> int:
    """Remove a folder tree WITHOUT following junctions or links (a link is removed, never its target).
    Returns the number of entries that could not be removed."""
    errors = 0
    p = str(path)
    try:
        entries = list(os.scandir(p))
    except OSError:
        return 1
    for e in entries:
        try:
            if _is_link(e):
                _remove_link(e.path)
            elif e.is_dir(follow_symlinks=False):
                errors += remove_tree(e.path)
            else:
                try:
                    os.remove(e.path)
                except PermissionError:
                    os.chmod(e.path, _stat.S_IWRITE)
                    os.remove(e.path)
        except OSError:
            errors += 1
    try:
        os.rmdir(p)
    except OSError:
        errors += 1
    return errors


def remove_case_folder_if_ours(folder: PathLike, job_id: str) -> bool:
    """A cancelled check: remove its case folder, but only when it still holds this job's marker."""
    try:
        if not os.path.isfile(os.path.join(str(folder), marker_name(job_id))):
            return False
    except ValueError:
        return False
    return remove_tree(folder) == 0


# ================================================================================================ housekeeping
def _safe_data_dir(d: Path) -> bool:
    """Housekeeping runs only in an absolute, existing, real (not linked) folder that is not a drive root."""
    try:
        if not d.is_absolute() or len(d.parts) < 2 or not d.is_dir():
            return False
        if d.is_symlink() or (hasattr(os.path, "isjunction") and os.path.isjunction(d)):
            return False
    except OSError:
        return False
    return True


def _empty_folder(folder: Path) -> Dict[str, int]:
    removed, errors = 0, 0
    try:
        entries = list(os.scandir(folder))
    except FileNotFoundError:
        return {"removed": 0, "errors": 0}
    except OSError:
        return {"removed": 0, "errors": 1}
    for e in entries:
        try:
            if _is_link(e):
                _remove_link(e.path)
            elif e.is_dir(follow_symlinks=False):
                errors += remove_tree(e.path)
            else:
                os.remove(e.path)
            removed += 1
        except OSError:
            errors += 1
    return {"removed": removed, "errors": errors}


def _prune_old(folder: Path, cutoff: float, top: bool = True) -> Dict[str, int]:
    removed, errors = 0, 0
    try:
        entries = list(os.scandir(folder))
    except FileNotFoundError:
        return {"removed": 0, "errors": 0}
    except OSError:
        return {"removed": 0, "errors": 1}
    for e in entries:
        try:
            if _is_link(e):
                continue                                 # never followed, never removed here
            if e.is_dir(follow_symlinks=False):
                sub = _prune_old(Path(e.path), cutoff, top=False)
                removed += sub["removed"]
                errors += sub["errors"]
                try:
                    os.rmdir(e.path)                     # only when it is now empty
                except OSError:
                    pass
            elif e.stat(follow_symlinks=False).st_mtime < cutoff:
                os.remove(e.path)
                removed += 1
        except OSError:
            errors += 1
    return {"removed": removed, "errors": errors}


def housekeeping(data: PathLike, cache_days: int = 30, now: Optional[float] = None,
                 keep_logs: int = KEEP_WORKER_LOGS) -> Dict[str, int]:
    """Start-up housekeeping before any check (rev 1 section 3.2): empty DATA\\jobs and DATA\\tmp (the ascii
    copies of R2.1.2 included); remove files under DATA\\cache older than `cache_days` (saved page texts too);
    keep the newest `keep_logs` worker logs in DATA\\logs. Never follows a link; touches nothing else."""
    d = Path(str(data))
    out = {"jobs": 0, "tmp": 0, "cache": 0, "logs": 0, "errors": 0}
    if not _safe_data_dir(d):
        return out
    for sub in ("jobs", "tmp"):
        r = _empty_folder(d / sub)
        out[sub] = r["removed"]
        out["errors"] += r["errors"]
    now = time.time() if now is None else float(now)
    days = max(1, int(cache_days))
    r = _prune_old(d / "cache", now - days * 86400.0)
    out["cache"] = r["removed"]
    out["errors"] += r["errors"]
    logs_dir = d / "logs"
    try:
        logs = []
        with os.scandir(logs_dir) as it:
            for e in it:
                if e.is_file(follow_symlinks=False) and re.fullmatch(r"worker-.+\.log", e.name, re.IGNORECASE):
                    logs.append((e.stat(follow_symlinks=False).st_mtime, e.name, e.path))
        logs.sort(reverse=True)
        for _, _, path in logs[max(0, int(keep_logs)):]:
            try:
                os.remove(path)
                out["logs"] += 1
            except OSError:
                out["errors"] += 1
    except FileNotFoundError:
        pass
    except OSError:
        out["errors"] += 1
    return out
