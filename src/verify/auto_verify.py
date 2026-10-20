"""
Automatic verification of downloaded documents (run by the bot after each portal sync).

Each pass:
  1. Re-evaluation — stored verdicts are re-tested against the current rules from the text saved
     when the files were first read (no OCR): every student when the rules change (the
     "roll-out", announced once as one summary), a student whose portal values changed, and
     everyone once a day from 07:00 (date rules such as "issued within 3 months" move with the
     calendar). Only verdicts that change are announced.
  2. Document check — every student who is new, or whose files changed, is read (PDF text layer
     first, OCR only for scans) and tested against the program guidelines. Results go into
     DOCUMENT CHECK.xlsx, one sheet per program.
  3. Field check — the same students' portal values are compared against what their documents
     actually say. Results go into FIELD CHECK.xlsx, one sheet per program.

Every document is OCR'd once only: the text is kept in data/verification/text/<PASSPORT>.json
and reused until that file itself changes (src/verify/text_cache.py), and what each page looks
like (colour, QR codes, seals) in data/verification/images/<PASSPORT>.json.

The results live in data/verification/results.json. It is never silently lost: every save keeps
the previous file as results.json.bak and the day's first state as results-YYYY-MM-DD.json (the
last 7 days); an unreadable file is copied aside and the check goes on from the backup, or stops
without overwriting anything. A file that could not be CHECKED (locked, unrenderable, OCR or the
QR reader broke) gives an ERROR row, and that student is tried again on the next passes instead of
keeping a wrong FAIL or FLAG.

Both reports are rebuilt in full from results.json after a pass that changed anything
(finish_reports, called after the Telegram message so an open report never costs a message).
FIELD CHECK.xlsx also carries a "Corrections" sheet: every portal field that changed after a
check, with what it was, what it is now, and whether the change fixed the problem.

OCR is slow (minutes per student), so each pass has a budget of students whose documents are new;
whatever is left over is picked up by the next pass.

CLI (run from the BOT folder; every command takes the check's lock, --wait waits for it):
  python -m src.verify.auto_verify                    # one pass, as the scheduler runs it
  python -m src.verify.auto_verify --budget 0         # no limit: drain the whole queue
  python -m src.verify.auto_verify --pending          # just list what is waiting
  python -m src.verify.auto_verify --passport A99900001            # show the stored result
  python -m src.verify.auto_verify --passport A99900001 --force    # re-run that student now
  python -m src.verify.auto_verify --recheck          # re-evaluate everyone from the saved text
  python -m src.verify.auto_verify --recheck --announce --publish --wait   # the roll-out
  python -m src.verify.auto_verify --recheck --dry-run --data-dir <copy> --export-file <rows.json>
  python -m src.verify.auto_verify --rebuild          # only rewrite the two reports
  python -m src.verify.auto_verify --recheck-all      # set every stored result aside and start over
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import logging
import os
import re
import shutil
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional, Tuple

from src.config import settings
from src.verify import common as C
from src.verify import text_cache
from src.verify.common import RULES_VERSION

logger = logging.getLogger(__name__)

REPORT_DIR = settings.verification_dir()
STORE_PATH = REPORT_DIR / "results.json"
TEXT_DIR = REPORT_DIR / "text"          # OCR output, kept so nothing is read twice
IMAGES_DIR = REPORT_DIR / "images"      # what each page looks like (colour, QR codes, seals)
LOCK_PATH = REPORT_DIR / "auto_verify.lock"
LOCK_STALE_SECONDS = 4 * 3600
LOCK_WAIT_SECONDS = 40 * 60             # --wait
LOCK_POLL_SECONDS = 15
DOC_REPORT = REPORT_DIR / "DOCUMENT CHECK.xlsx"
FIELD_REPORT = REPORT_DIR / "FIELD CHECK.xlsx"
DRY_RUN_MARKER = "DRY_RUN_COPY"         # the copy step writes it into a dry run's data folder
KEEP_DAILY_COPIES = 7

# RAFIQ MD MOTIN UR — our head of branch, not an applicant.
SKIP_PASSPORTS = {"A00990004"}

PROGRAM_ORDER = ["KLP", "EAP", "BACHELOR", "MASTER"]
DEFAULT_BUDGET = 6          # students per pass
MAX_PAGES = text_cache.MAX_PAGES

ROLLOUT_TITLE = "🔍 Document check — rules updated"
DAILY_REEVAL_HOUR = 7       # the daily re-evaluation starts with the first pass from 07:00
REEVAL_SECONDS = 1200       # re-evaluation time per pass; the rest continues on the next pass
MAX_ERROR_PASSES = 3        # passes in a row with a read error before the ERROR rows are kept

# A fault in the rules themselves (not a document problem): the run stops rather than skip
# every student one by one and leave a report that looks clean and is worthless.
_CODE_FAULTS = (NameError, AttributeError, ImportError, TypeError)

_clock = time.monotonic


def _now() -> dt.datetime:
    return dt.datetime.now()


def _iso(when: Optional[dt.datetime] = None) -> str:
    return (when or _now()).isoformat(timespec="seconds")


def _trim(text: str, n: int) -> str:
    text = str(text or "").strip()
    return text if len(text) <= n else text[:n - 1] + "…"


def use_data_dir(path) -> Path:
    """Point every file of the check at `path` instead of data/verification: results.json, the
    text and image caches, the lock and both reports (--data-dir, the dry run). The e-Apostille
    subjects looked up before (apostille.json) are read from there too when the folder has them,
    so a dry run from another checkout judges the academic files as the live check does."""
    global REPORT_DIR, STORE_PATH, TEXT_DIR, IMAGES_DIR, LOCK_PATH, DOC_REPORT, FIELD_REPORT
    REPORT_DIR = Path(path)
    STORE_PATH = REPORT_DIR / "results.json"
    TEXT_DIR = REPORT_DIR / "text"
    IMAGES_DIR = REPORT_DIR / "images"
    LOCK_PATH = REPORT_DIR / "auto_verify.lock"
    DOC_REPORT = REPORT_DIR / "DOCUMENT CHECK.xlsx"
    FIELD_REPORT = REPORT_DIR / "FIELD CHECK.xlsx"
    text_cache.TEXT_DIR, text_cache.IMAGES_DIR = TEXT_DIR, IMAGES_DIR
    if (REPORT_DIR / "apostille.json").exists():
        from src.verify import page_checks as PC
        PC.APOSTILLE_SUBJECTS = REPORT_DIR / "apostille.json"
    return REPORT_DIR


# --- the lock ------------------------------------------------------------------------
def _pid_alive(pid: int) -> bool:
    """Is that process still running?  A lock left behind by a process that died must not
    block the next run until it goes stale."""
    if pid <= 0:
        return False
    if os.name == "nt":
        import ctypes
        h = ctypes.windll.kernel32.OpenProcess(0x1000, False, pid)   # QUERY_LIMITED_INFORMATION
        if not h:
            return False
        code = ctypes.c_ulong()
        ctypes.windll.kernel32.GetExitCodeProcess(h, ctypes.byref(code))
        ctypes.windll.kernel32.CloseHandle(h)
        return code.value == 259                                     # STILL_ACTIVE
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def _lock_held(path) -> bool:
    """True only when another *live* run holds this lock. A stale lock, or one left by a
    process that is no longer running, is cleared."""
    if not path.exists():
        return False
    try:
        age = time.time() - path.stat().st_mtime
    except OSError:
        return False
    if age >= LOCK_STALE_SECONDS:
        logger.info("clearing a lock that is %.1f hours old", age / 3600)
        path.unlink(missing_ok=True)
        return False
    try:
        raw = path.read_text(encoding="utf-8", errors="replace").strip()
    except OSError:
        return True                      # being written this very moment
    if not raw:
        if age < 60:
            return True                  # just taken: its PID is not written yet
        logger.info("clearing an empty lock left behind")
        path.unlink(missing_ok=True)
        return False
    try:
        pid = int(raw)
    except ValueError:
        return True                      # unreadable but fresh — leave it alone
    if _pid_alive(pid):
        return True
    logger.info("clearing a lock left by process %s, which is no longer running", pid)
    path.unlink(missing_ok=True)
    return False


def _take_lock() -> bool:
    """Take the lock atomically: two passes starting together cannot both get it."""
    if _lock_held(LOCK_PATH):
        return False
    try:
        fd = os.open(str(LOCK_PATH), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        return False
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(str(os.getpid()))
    return True


def _release_lock() -> None:
    try:
        if LOCK_PATH.read_text(encoding="utf-8", errors="replace").strip() == str(os.getpid()):
            LOCK_PATH.unlink(missing_ok=True)
    except OSError:
        pass


def _lock_pid() -> str:
    try:
        return LOCK_PATH.read_text(encoding="utf-8", errors="replace").strip() or "?"
    except OSError:
        return "?"


@contextmanager
def _locked(wait: float = 0):
    """Hold the check's lock for the block: yields False (and holds nothing) when another live
    run has it and `wait` seconds of polling did not free it."""
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    give_up = _clock() + max(0.0, wait)
    while not _take_lock():
        if _clock() >= give_up:
            yield False
            return
        time.sleep(LOCK_POLL_SECONDS)
    try:
        yield True
    finally:
        _release_lock()


# --- results.json ----------------------------------------------------------------------
class StoreUnreadable(Exception):
    """results.json cannot be read and there is no readable backup: the check stops and
    overwrites nothing."""


class StoreShrinkRefused(RuntimeError):
    """A save would replace a store holding students with one holding none."""


def _empty_store() -> Dict[str, Any]:
    return {"documents": {}, "fields": {}, "corrections": [], "meta": {}}


def _parse_store(path: Path) -> Dict[str, Any]:
    """The store in `path`, or an exception saying why it is not one."""
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("not a JSON object")
    for key, kind in (("documents", dict), ("fields", dict), ("corrections", list), ("meta", dict)):
        if data.get(key) is not None and not isinstance(data[key], kind):
            raise ValueError(f"'{key}' is not a {kind.__name__}")
    return {"documents": data.get("documents") or {}, "fields": data.get("fields") or {},
            "corrections": data.get("corrections") or [], "meta": data.get("meta") or {}}


def _bak_path() -> Path:
    return STORE_PATH.with_name(STORE_PATH.name + ".bak")


def load_store() -> Dict[str, Any]:
    """results.json. A missing file is a fresh start (an empty store), unless a results.json.bak
    exists: then, as for an unreadable file, the store comes from the backup, with store["_note"]
    saying so. An unreadable file is first copied to results.corrupt-<time>.json. With no
    readable backup either: StoreUnreadable, and nothing is touched."""
    bak = _bak_path()
    if not STORE_PATH.exists():
        if not bak.exists():
            return _empty_store()
        problem = "results.json is missing"
    else:
        try:
            return _parse_store(STORE_PATH)
        except Exception as e:
            problem = f"results.json is unreadable ({type(e).__name__}: {_trim(str(e), 120)})"
            keep = STORE_PATH.with_name(f"results.corrupt-{_now():%Y-%m-%d_%H%M%S}.json")
            try:
                shutil.copy2(STORE_PATH, keep)
                problem += f"; it is kept as {keep.name}"
            except OSError as ce:
                logger.warning("could not keep a copy of the unreadable results.json: %s", ce)
    if not bak.exists():
        raise StoreUnreadable(f"{problem}, and there is no results.json.bak")
    try:
        store = _parse_store(bak)
    except Exception as e:
        raise StoreUnreadable(f"{problem}, and results.json.bak is unreadable too ({type(e).__name__})") from e
    saved = dt.datetime.fromtimestamp(bak.stat().st_mtime).strftime("%Y-%m-%d %H:%M")
    store["_note"] = f"{problem} — the check went on from results.json.bak (saved {saved})"
    logger.warning(store["_note"])
    return store


def _peek_store() -> Dict[str, Any]:
    """results.json read as it is, changing nothing (StoreUnreadable when it cannot be read)."""
    if not STORE_PATH.exists():
        return _empty_store()
    try:
        return _parse_store(STORE_PATH)
    except Exception as e:
        raise StoreUnreadable(f"results.json is unreadable ({type(e).__name__}: {_trim(str(e), 120)})") from e


_last_written: Optional[Tuple[str, int, int]] = None     # (path, mtime_ns, size) of our last save


def _prune_daily_copies() -> None:
    pattern = re.compile(r"^results-\d{4}-\d{2}-\d{2}\.json$")
    copies = sorted(p for p in STORE_PATH.parent.iterdir() if pattern.match(p.name))
    for old in copies[:-KEEP_DAILY_COPIES]:
        try:
            old.unlink()
        except OSError as e:
            logger.warning("could not remove the old copy %s: %s", old.name, e)


def save_store(store: Dict[str, Any], *, allow_shrink: bool = False) -> None:
    """Write results.json safely: the current file (when it is a readable store) is first kept as
    results.json.bak, and as results-YYYY-MM-DD.json for the day's first save (7 days kept); the
    new one is written to a temporary file and moved into place. Keys starting with "_" (such as
    "_note") are not saved. A store holding students is never replaced by one holding none
    (StoreShrinkRefused) unless `allow_shrink`."""
    global _last_written
    clean = {k: v for k, v in store.items() if not str(k).startswith("_")}
    for key, default in (("documents", {}), ("fields", {}), ("corrections", []), ("meta", {})):
        clean.setdefault(key, default)
    STORE_PATH.parent.mkdir(parents=True, exist_ok=True)
    if STORE_PATH.exists():
        st = STORE_PATH.stat()
        ours = _last_written == (str(STORE_PATH), st.st_mtime_ns, st.st_size)
        current = None
        if not ours or not clean["documents"]:
            try:
                current = _parse_store(STORE_PATH)
            except Exception:
                current = None
        if not clean["documents"] and not allow_shrink and current is not None and current["documents"]:
            raise StoreShrinkRefused(f"refusing to replace results.json ({len(current['documents'])} "
                                     "students) with an empty store")
        if ours or current is not None:          # an unreadable file never overwrites the backup
            try:
                shutil.copy2(STORE_PATH, _bak_path())
                daily = STORE_PATH.with_name(f"results-{_now():%Y-%m-%d}.json")
                if not daily.exists():
                    shutil.copy2(STORE_PATH, daily)
                    _prune_daily_copies()
            except OSError as e:
                logger.warning("could not keep a backup of results.json: %s", e)
    tmp = STORE_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(clean, indent=1, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, STORE_PATH)
    st = STORE_PATH.stat()
    _last_written = (str(STORE_PATH), st.st_mtime_ns, st.st_size)


# --- before a pass -------------------------------------------------------------------------
def preflight() -> str:
    """"" when the check can run, else why not. The QR reader (pyzbar) must load and work:
    without it every academic file would wrongly FAIL its e-Apostille. OpenCV, PyMuPDF, EasyOCR
    and torch must be installed."""
    from importlib.util import find_spec
    missing = []
    for module in ("cv2", "pymupdf", "easyocr", "torch"):
        try:
            if find_spec(module) is None:
                missing.append(module)
        except (ImportError, ValueError):
            missing.append(module)
    if missing:
        return f"{', '.join(missing)} not installed"
    try:
        import pyzbar.pyzbar  # noqa: F401
    except Exception as e:
        return f"the QR reader (pyzbar) cannot load: {type(e).__name__}: {_trim(str(e), 120)}"
    try:
        from src.verify import page_checks as PC
        if hasattr(PC, "zbar_available"):
            ok, why = PC.zbar_available()
            if not ok:
                return f"the QR reader (pyzbar) is not working: {_trim(why, 120)}"
    except Exception as e:
        return f"the QR reader could not be tested: {type(e).__name__}: {_trim(str(e), 120)}"
    return ""


def _implausible(store: Dict[str, Any], folders, portal) -> str:
    """"" unless the document folders or the portal list came back nearly empty (a drive or the
    portal unavailable): then every stored student would look gone, and must not be treated so."""
    stored = [p for p in store["documents"] if p not in SKIP_PASSPORTS]
    if len(stored) < 5:
        return ""
    found = sum(1 for p in stored if p in folders and p in portal)
    if found * 2 >= len(stored):
        return ""
    return (f"only {found} of the {len(stored)} checked students were found in the document folders "
            "and the portal's student list (a folder or the portal may be unavailable)")


# --- what has changed since last time ------------------------------------------------
def _hash(parts: List[str]) -> str:
    return hashlib.sha1("|".join(parts).encode("utf-8", "replace")).hexdigest()


def _file_parts(folder: Path) -> List[str]:
    out = []
    for f in sorted(folder.iterdir()):
        if f.is_file() and not f.name.startswith("."):
            st = f.stat()
            out.append(f"{f.name}:{st.st_size}:{int(st.st_mtime)}")
    return out


def doc_fingerprint(folder: Path) -> str:
    """Changes only when a document is added, replaced or resized — not when the portal is
    edited, so correcting a portal field never causes the scans to be read again."""
    return _hash(_file_parts(folder))


_issue_memo: Optional[Dict[str, str]] = None     # the passport-issue cache, read once per pass


def _load_issue_dates() -> Dict[str, str]:
    try:
        from src.sheets import passport_issue
        return dict(passport_issue.load() or {})
    except Exception:
        return {}


def _issue_dates() -> Dict[str, str]:
    return _issue_memo if _issue_memo is not None else _load_issue_dates()


def portal_fp(student: Dict[str, str]) -> str:
    """Changes when any portal value the document check reads changes (name, parents, DOB,
    gender, passport, sponsor, GPAs, program)."""
    from src.verify import doc_verifier as dv
    return _hash([f"{k}={student.get(k, '')}" for k in dv.PORTAL_KEYS_READ])


def field_fingerprint(folder: Path, student: Dict[str, str], issues: Optional[Dict[str, str]] = None) -> str:
    """Changes when a document changes OR a portal value the checks compare is edited — not
    when a workflow field such as Current Stage moves.

    The passport issue date lives in its own cache rather than the students export, so it
    has to be folded in here — otherwise correcting it on the portal would never cause the
    student to be checked again."""
    from src.verify import doc_verifier as dv
    from src.verify import field_check as fc
    issue = (issues if issues is not None else _issue_dates()).get(
        (student.get("Passport No") or "").strip().upper(), "")
    values = fc.checked_portal_values(student)
    return _hash(_file_parts(folder) + [f"{k}={values[k]}" for k in sorted(values)]
                 + [f"{k}={student.get(k, '')}" for k in dv.PORTAL_KEYS_READ] + [f"_issue={issue}"])


def _status(pas: str, rec: Optional[Dict[str, Any]], folders, portal, today: str = "") -> str:
    """Where a student stands: "orphan" (no folder or portal row), "new" (never checked),
    "retry" (a read error last time), "files" (documents changed), "needs_ocr" (text never
    saved), "error" (re-evaluation hit a read error today) or "ok"."""
    if pas in SKIP_PASSPORTS or pas not in folders or pas not in portal:
        return "orphan"
    if rec is None:
        return "new"
    if not rec.get("fingerprint"):
        return "retry"
    if rec["fingerprint"] != doc_fingerprint(folders[pas][0]):
        return "files"
    if rec.get("needs_ocr"):
        return "needs_ocr"
    if today and str((rec.get("reeval_error") or {}).get("at", ""))[:10] == today:
        return "error"
    return "ok"


def pending(store: Dict[str, Dict[str, Any]], folders, portal) -> List[str]:
    """Passports whose documents must be read: never checked, then documents changed, then text
    never saved (needs_ocr), then a read error to retry — each group oldest download first. A
    portal edit alone is not here: it is re-evaluated from the saved text."""
    order = {"new": 0, "files": 1, "needs_ocr": 2, "retry": 3}
    groups: List[List[Tuple[float, str]]] = [[], [], [], []]
    for pas, (folder, _name) in folders.items():
        g = order.get(_status(pas, store["documents"].get(pas), folders, portal))
        if g is None:
            continue
        try:
            mtime = folder.stat().st_mtime
        except OSError:
            mtime = 0.0
        groups[g].append((mtime, pas))
    return [p for group in groups for _, p in sorted(group)]


def _due(pas: str, rec: Dict[str, Any], store: Dict[str, Any], folder: Path, student: Dict[str, str],
         now: dt.datetime) -> str:
    """Why a stored verdict must be re-evaluated from the saved text ("" when it need not):
    "rules" (made under other rules), "portal" (a portal value it reads, or one the field check
    compares, changed) or "daily" (not evaluated yet today, and it is 07:00 or later)."""
    if rec.get("rules_version") != RULES_VERSION:
        return "rules"
    if rec.get("portal_fp") != portal_fp(student):
        return "portal"
    if (store["fields"].get(pas) or {}).get("fingerprint") != field_fingerprint(folder, student):
        return "portal"
    last = str(rec.get("evaluated_at") or rec.get("checked") or "")[:10]
    if now.hour >= DAILY_REEVAL_HOUR and last < now.date().isoformat():
        return "daily"
    return ""


# --- reading each document once -------------------------------------------------------
def _text_cache_path(pas: str) -> Path:
    return TEXT_DIR / f"{pas}.json"


def install_text_cache(pas: str, cache_only: bool = False) -> text_cache.CacheHandle:
    """Make both checkers read through the caches (src/verify/text_cache.py), so a document is
    only ever OCR'd once. The cache key is the file's name, size and modification time, so a
    re-uploaded or shrunk document is read again, and an unchanged one never is."""
    return text_cache.install(pas, cache_only=cache_only, text_dir=TEXT_DIR, images_dir=IMAGES_DIR)


def save_text_cache(handle: text_cache.CacheHandle) -> None:
    text_cache.uninstall(handle)


# --- checking one student ------------------------------------------------------------
def _record_corrections(store: Dict[str, Any], pas: str, program: str, name: str,
                        previous: List[Dict[str, Any]], current: List[Dict[str, Any]]) -> int:
    """Log every portal field that changed since this student was last checked, and say
    whether the change settled the problem."""
    was = {str(r.get("field")): r for r in previous}
    n = 0
    for r in current:
        old = was.get(str(r.get("field")))
        if not old:
            continue
        before, after = str(old.get("portal", "")), str(r.get("portal", ""))
        if before == after:
            continue
        store.setdefault("corrections", []).append({
            "noticed": dt.datetime.now().isoformat(timespec="seconds"),
            "student": name, "passport": pas, "program": program,
            "field": str(r.get("field")),
            "was": before, "now": after,
            "was_result": str(old.get("result", "")), "now_result": str(r.get("result", "")),
            "detail": str(r.get("detail", "")),
        })
        n += 1
    return n


def _plain(rows: Optional[List[Dict[str, Any]]]) -> List[Dict[str, Any]]:
    """Rows as they read, without the bookkeeping fields: a row only gaining its document key or
    error mark has not changed (it does not make the student "checked" again)."""
    return [{k: v for k, v in r.items() if k not in ("key", "has_error")} for r in rows or []]


def _store_fields(store: Dict[str, Any], pas: str, program: str, name: str, frows: List[Dict[str, Any]],
                  field_fp: str, now: str, read: bool) -> Tuple[int, bool]:
    """Keep the field check's rows: -> (portal corrections noticed, whether the rows changed)."""
    prev = store["fields"].get(pas)
    before = (prev or {}).get("rows") or []
    corrected = _record_corrections(store, pas, program, name, before, frows) if before else 0
    changed = prev is None or before != frows
    store["fields"][pas] = {
        "fingerprint": field_fp, "checked": now if (read or changed) else prev.get("checked", now),
        "evaluated_at": now, "rules_version": RULES_VERSION, "student": name, "program": program,
        "rows": frows,
    }
    return corrected, changed


def _evaluate(pas: str, folder: Path, student: Dict[str, str], store: Dict[str, Any], *,
              cache_only: bool, mode: str) -> Dict[str, Any]:
    """Document check then, straight away, the field check for the same student, and their
    records. `mode` "queue" (the files are new or changed: they are read) or "reeval" (stored
    verdicts tested again, normally from the saved text only).

    -> {"kind": "done", "entry", "change", "republish", "final_error", "field_note"}
     | {"kind": "needs_ocr", "misses"}      cache-only, and some text was never saved: the record
                                            is only marked needs_ocr
     | {"kind": "retry", "error"}           a read error: the old record is kept (fingerprint "")
     | {"kind": "error_kept", "error"}      the same during a re-evaluation (retried tomorrow)"""
    from src.verify import doc_verifier as dv
    from src.verify import field_check as fc

    program = dv.program_of(student)
    name = student.get("Full Name", "")
    doc_fp = doc_fingerprint(folder)
    field_fp = field_fingerprint(folder, student)
    pfp = portal_fp(student)

    handle = install_text_cache(pas, cache_only=cache_only)
    try:
        try:
            rows = dv.verify_student(folder, student, program)
            frows = fc.check_student(pas, folder, student)
        except C.NotCached:
            if not handle.misses:              # never ours: let it surface
                raise
            rows, frows = [], []               # a check let the miss through: the same as a missed row
    finally:
        save_text_cache(handle)
    now = _iso()
    engine = {**text_cache.engine_info(), "text": handle.text_source}
    docs = store["documents"]
    prev = docs.get(pas)
    who = {"student": name, "passport": pas, "program": program}

    if handle.misses:
        if prev is not None:
            prev["needs_ocr"] = True
        return {"kind": "needs_ocr", "misses": list(handle.misses)}

    read = mode == "queue" or bool(handle.fresh)
    retry = None
    if any(C.row_has_error(r) for r in rows):
        doc, msg = _error_of(rows)
        if mode == "reeval" and prev is not None:
            prev["reeval_error"] = {"at": now, "error": f"{doc}: {msg}"}
            return {"kind": "error_kept", "error": {**who, "doc": doc, "msg": msg, "count": 0}}
        on_retry = prev is not None and not prev.get("fingerprint")
        count = (int((prev.get("retry") or {}).get("count", 0)) if on_retry else 0) + 1
        retry = {"count": count, "error": f"{doc}: {msg}", "at": now}
        if count < MAX_ERROR_PASSES:
            if prev is not None:                       # the old verdict stands until a clean read
                prev["retry"], prev["fingerprint"] = retry, ""
            else:
                docs[pas] = {"fingerprint": "", "portal_fp": pfp, "checked": now, "evaluated_at": now,
                             "rules_version": RULES_VERSION, "max_pages": MAX_PAGES, "engine": engine,
                             "folder": str(folder), "student": name, "program": program,
                             "verdict": C.student_verdict(rows), "rows": rows, "retry": retry}
            _store_fields(store, pas, program, name, frows, field_fp, now, read)
            return {"kind": "retry", "error": {**who, "doc": doc, "msg": msg, "count": count}}
        retry["final"] = True                          # the third pass in a row: kept as found

    verdict = C.student_verdict(rows)
    first = prev is None or (bool(prev.get("retry")) and not prev.get("first_checked")
                             and not prev.get("fingerprint"))
    previous = None if first else prev.get("verdict")
    rows_changed = prev is None or _plain(prev.get("rows")) != _plain(rows)
    rec = {"fingerprint": doc_fp, "portal_fp": pfp,
           "checked": now if (read or rows_changed or first) else prev.get("checked", now),
           "first_checked": now if first else (prev.get("first_checked") or prev.get("checked") or now),
           "evaluated_at": now, "rules_version": RULES_VERSION, "max_pages": MAX_PAGES, "engine": engine,
           "folder": str(folder), "student": name, "program": program, "verdict": verdict, "rows": rows}
    if previous is not None and previous != verdict:
        rec["previous_verdict"], rec["verdict_changed_at"] = previous, now
    elif not first:
        for k in ("previous_verdict", "verdict_changed_at"):
            if k in prev:
                rec[k] = prev[k]
    if retry is not None:
        rec["retry"] = retry
    docs[pas] = rec
    corrected, fields_changed = _store_fields(store, pas, program, name, frows, field_fp, now, read)

    differs = sum(1 for r in frows if r.get("result") == fc.DIFFERS)
    match = sum(1 for r in frows if r.get("result") == fc.MATCH)
    change = None
    if previous is not None and previous != verdict:
        change = {"passport": pas, "student": name, "program": program, "old": previous, "new": verdict,
                  "reason": change_reason(prev, rec), "why": ""}
    entry = {**who, "verdict": verdict, "previous": previous, "first": first, "differs": differs,
             "corrected": corrected, "reason": verdict_reason(rec)}
    republish = prev is None or prev.get("rows") != rows or previous != verdict or fields_changed
    return {"kind": "done", "entry": entry, "change": change, "republish": republish,
            "final_error": retry is not None, "field_note": f"{match} matched, {differs} differ"}


def check_one(pas: str, folder: Path, student: Dict[str, str], store: Dict[str, Dict[str, Any]], *,
              allow_ocr: bool = True) -> Dict[str, Any]:
    """Read a student whose documents are new or changed (OCR where no text was saved), check
    them, then the field check for the same student. See _evaluate for what comes back."""
    return _evaluate(pas, folder, student, store, cache_only=not allow_ocr, mode="queue")


# --- re-evaluation from the saved text --------------------------------------------------
_PRIORITY = {"rules": 0, "portal": 1, "daily": 2}


def reevaluate(store: Dict[str, Any], folders, portal, *, passports: Optional[Iterable[str]] = None,
               force: bool = False, allow_ocr: bool = False, deadline: Optional[float] = None,
               why: str = "daily", save: Optional[Callable[[Dict[str, Any]], None]] = None) -> Dict[str, Any]:
    """Test stored verdicts again, from the saved text (no OCR unless `allow_ocr`).

    A stored student is due when their verdict was made under other rules, a portal value the
    checks read or compare changed, or (from 07:00) it was last evaluated before today. Students
    whose files changed, whose text was never saved (needs_ocr) or who are on a read-error retry
    belong to the OCR queue and are left alone, as are students no longer in a folder or on the
    portal. `force` re-evaluates every one of `passports` (or every stored student) whatever its
    state. `deadline` (a time.monotonic() value) ends the work early: "done" is then False and
    the rest is due on the next pass. `save(store)` is called after each student.

    A file whose text was never saved marks the student needs_ocr and changes nothing else; a
    read error keeps the old record (and is tried again tomorrow). Otherwise the document and
    field records, fingerprints and corrections are updated; rows that changed put the student in
    "republish", and a verdict that changed adds a change entry.

    -> {"changed": [change], "republish": [P], "needs_ocr": [P], "errors": [str], "done": bool,
        "evaluated": [P], "skipped": [P], "orphans": [P]}"""
    out: Dict[str, Any] = {"changed": [], "republish": [], "needs_ocr": [], "errors": [], "done": True,
                           "evaluated": [], "skipped": [], "orphans": []}
    now = _now()
    today = now.date().isoformat()
    docs = store["documents"]
    wanted = list(dict.fromkeys(p.strip().upper() for p in passports if p and p.strip())) if passports else list(docs)
    work = []
    for pas in wanted:
        rec = docs.get(pas)
        if rec is None and not force:
            continue
        state = _status(pas, rec, folders, portal, today)
        if state == "orphan":
            out["orphans"].append(pas)
            continue
        folder, student = folders[pas][0], portal[pas]
        reason = _due(pas, rec, store, folder, student, now) if rec is not None else ""
        if not force:
            if state in ("needs_ocr", "files"):
                out["needs_ocr"].append(pas)
                continue
            if state in ("retry", "error"):
                out["skipped"].append(pas)
                continue
            if not reason:
                continue
        elif reason == "daily" or not reason:
            reason = why
        last = str((rec or {}).get("evaluated_at") or (rec or {}).get("checked") or "")
        work.append((_PRIORITY.get(reason, 3), last, pas, reason))

    for _, _, pas, reason in sorted(work):
        if deadline is not None and _clock() >= deadline:
            out["done"] = False
            break
        student = portal[pas]
        name = student.get("Full Name", "")
        try:
            res = _evaluate(pas, folders[pas][0], student, store, cache_only=not allow_ocr, mode="reeval")
        except _CODE_FAULTS as e:
            raise RuntimeError(
                f"the rules failed on {name} ({pas}): {type(e).__name__}: {e}. "
                "This is a code fault, not a document problem — the run has stopped "
                "so the report is not silently wrong.") from e
        except Exception as e:
            logger.warning("%s (%s) could not be re-evaluated: %s", name, pas, e)
            out["errors"].append(f"{name} ({pas}): {_trim(str(e), 120)}")
            out["skipped"].append(pas)
            if pas in docs:                            # tried again tomorrow, never blocks a roll-out
                docs[pas]["reeval_error"] = {"at": _iso(), "error": _trim(str(e), 200)}
            if save:
                save(store)
            continue
        kind = res["kind"]
        if kind == "needs_ocr":
            out["needs_ocr"].append(pas)
        elif kind in ("error_kept", "retry"):
            err = res["error"]
            out["errors"].append(f"{name} ({pas}): " + ": ".join(x for x in (err["doc"], err["msg"]) if x))
            out["skipped"].append(pas)
        else:
            out["evaluated"].append(pas)
            if res["republish"]:
                out["republish"].append(pas)
            if res["change"]:
                out["changed"].append({**res["change"], "why": reason})
                print(f"   {name[:30]:32} {res['change']['old']} -> {res['change']['new']} [{reason}]", flush=True)
        if save:
            save(store)
    return out


# --- the roll-out of new rules -----------------------------------------------------------
def _counts(docs: Dict[str, Any]) -> Dict[str, int]:
    out: Dict[str, int] = {}
    for rec in docs.values():
        v = str((rec or {}).get("verdict") or "")
        if v:
            out[v] = out.get(v, 0) + 1
    return out


def _start_rollout(store: Dict[str, Any]) -> Dict[str, Any]:
    """meta.rollout for this RULES_VERSION, begun now (verdict counts before it) unless it is
    already under way."""
    meta = store["meta"]
    ro = meta.get("rollout")
    if isinstance(ro, dict) and ro.get("to") == RULES_VERSION and not ro.get("complete"):
        return ro
    old = sorted({str(rec.get("rules_version") or "unversioned") for rec in store["documents"].values()}
                 - {RULES_VERSION}) or [str(meta.get("rules_version") or "unversioned")]
    ro = {"from": old, "to": RULES_VERSION, "started": _iso(), "before": _counts(store["documents"]),
          "changes": [], "done": [], "needs_ocr": [], "skipped": [], "complete": None, "announced": None}
    meta["rollout"] = ro
    return ro


def _merge_rollout(ro: Dict[str, Any], reev: Dict[str, Any]) -> None:
    """A pass's re-evaluation into the roll-out: each student once, from their verdict before the
    roll-out to their latest."""
    by = {c["passport"]: c for c in ro.get("changes") or []}
    for c in reev["changed"]:
        earlier = by.get(c["passport"])
        by[c["passport"]] = {**c, "old": earlier["old"]} if earlier else dict(c)
    ro["changes"] = [c for c in by.values() if c["old"] != c["new"]]
    ro["done"] = list(dict.fromkeys(list(ro.get("done") or []) + list(reev["evaluated"])))


def _close_rollout(store: Dict[str, Any], folders, portal) -> Optional[List[str]]:
    """Finish the roll-out when every student the saved text can re-evaluate is at RULES_VERSION:
    those whose text was never saved (or whose files changed) are listed in needs_ocr and reach
    the new rules through the OCR queue; those kept on a read error, or no longer in a folder or
    on the portal, are listed in skipped. None of them holds the queue up. -> the summary lines,
    or None while students remain."""
    meta = store["meta"]
    ro = meta["rollout"]
    today = _now().date().isoformat()
    needs, skipped = [], []
    for pas, rec in store["documents"].items():
        state = _status(pas, rec, folders, portal, today)
        if state in ("needs_ocr", "files"):
            needs.append(pas)
        elif state in ("orphan", "retry", "error"):
            skipped.append(pas)
        elif rec.get("rules_version") != RULES_VERSION:
            return None
    ro.update(needs_ocr=sorted(needs), skipped=sorted(skipped), complete=_iso(),
              now=_counts(store["documents"]))
    meta["rules_version"] = RULES_VERSION
    lines = rollout_lines(ro)
    ro["lines"] = lines
    try:
        (REPORT_DIR / f"rollout-{RULES_VERSION}.txt").write_text(
            ROLLOUT_TITLE + "\n\n" + "\n".join(lines) + "\n", encoding="utf-8")
    except OSError as e:
        logger.warning("could not save the roll-out summary: %s", e)
    return lines


def _mark_announced(store: Dict[str, Any], at: Optional[str] = None) -> None:
    meta = store["meta"]
    ro = meta.pop("rollout", None)
    if isinstance(ro, dict):
        ro["announced"] = at or _iso()
        meta["last_rollout"] = ro


def _unannounced(store: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """The completed roll-out whose summary has not been sent yet, if any."""
    ro = ((store or {}).get("meta") or {}).get("rollout")
    return ro if isinstance(ro, dict) and ro.get("complete") and not ro.get("announced") else None


# --- the two reports -----------------------------------------------------------------
def _cell(v):
    """Excel refuses control characters; nothing should ever fail a whole report."""
    if not isinstance(v, str):
        return v
    return "".join(c for c in v if ord(c) >= 32 or c in "\n\t")[:32000]


def _style_header(ws) -> None:
    from openpyxl.styles import Font
    for c in ws[1]:
        c.font = Font(bold=True)
    ws.freeze_panes = "A2"


def _programs_in(section: Dict[str, Any]) -> List[str]:
    found = {v.get("program", "") for v in section.values()}
    ordered = [p for p in PROGRAM_ORDER if p in found]
    return ordered + sorted(found - set(ordered) - {""})


def _stamp(value: Any) -> str:
    return str(value or "")[:16].replace("T", " ")


def write_document_report(store: Dict[str, Dict[str, Any]], path: Optional[Path] = None) -> Path:
    from openpyxl import Workbook
    from openpyxl.styles import PatternFill
    path = Path(path) if path is not None else DOC_REPORT
    colours = {"PASS": "C6EFCE", "FLAG": "FFEB9C", "FAIL": "FFC7CE", "MISSING": "F2F2F2",
               "NOTE": "DDEBF7", "REVIEW": "FFEB9C", "INCOMPLETE": "F2F2F2", "ERROR": "F8CBAD"}
    section = store["documents"]
    path.parent.mkdir(parents=True, exist_ok=True)
    wb = Workbook()

    ws = wb.active
    ws.title = "Summary"
    ws.append([_cell(x) for x in ["Student", "Passport", "Program", "Verdict", "Missing", "Fail", "Flag", "Pass",
                                  "Checked", "Rules", "Evaluated"]])
    _style_header(ws)
    for pas, rec in sorted(section.items(), key=lambda kv: (kv[1].get("program", ""), kv[1].get("student", ""))):
        counts: Dict[str, int] = {}
        for r in rec.get("rows", []):
            counts[r["verdict"]] = counts.get(r["verdict"], 0) + 1
        ws.append([rec.get("student", ""), pas, rec.get("program", ""), rec.get("verdict", ""),
                   counts.get("MISSING", 0), counts.get("FAIL", 0), counts.get("FLAG", 0),
                   counts.get("PASS", 0), _stamp(rec.get("checked")), rec.get("rules_version") or "",
                   _stamp(rec.get("evaluated_at"))])
        ws.cell(row=ws.max_row, column=4).fill = PatternFill(
            "solid", fgColor=colours.get(rec.get("verdict", ""), "FFFFFF"))
    for col, w in zip("ABCDEFGHIJK", (28, 13, 11, 13, 9, 7, 7, 7, 17, 14, 17)):
        ws.column_dimensions[col].width = w

    for program in _programs_in(section):
        sheet = wb.create_sheet(program[:31])
        sheet.append([_cell(x) for x in ["Student", "Passport", "Student verdict", "Document", "File", "Verdict", "Details"]])
        _style_header(sheet)
        for pas, rec in sorted(section.items(), key=lambda kv: kv[1].get("student", "")):
            if rec.get("program") != program:
                continue
            for r in rec.get("rows", []):
                sheet.append([_cell(x) for x in [rec.get("student", ""), pas, rec.get("verdict", ""),
                              r.get("doc", ""), r.get("file", ""), r.get("verdict", ""), r.get("detail", "")]])
                sheet.cell(row=sheet.max_row, column=6).fill = PatternFill(
                    "solid", fgColor=colours.get(r.get("verdict", ""), "FFFFFF"))
                sheet.cell(row=sheet.max_row, column=3).fill = PatternFill(
                    "solid", fgColor=colours.get(rec.get("verdict", ""), "FFFFFF"))
        for col, w in zip("ABCDEFG", (28, 13, 14, 38, 34, 10, 120)):
            sheet.column_dimensions[col].width = w
    wb.save(path)
    return path


def write_field_report(store: Dict[str, Dict[str, Any]], path: Optional[Path] = None) -> Path:
    from openpyxl import Workbook
    from openpyxl.styles import PatternFill
    from src.verify import field_check as fc
    path = Path(path) if path is not None else FIELD_REPORT
    not_confirmed = getattr(fc, "NOT_CONFIRMED", "NOT CONFIRMED")
    colour = {fc.MATCH: "C6EFCE", fc.DIFFERS: "FFC7CE", fc.UNREADABLE: "FFEB9C", not_confirmed: "FCE4D6",
              fc.NO_DOC: "F2F2F2", "BLANK": "FFFFFF"}
    section = store["fields"]
    path.parent.mkdir(parents=True, exist_ok=True)
    wb = Workbook()

    ws = wb.active
    ws.title = "Summary"
    ws.append([_cell(x) for x in ["Student", "Passport", "Program", "Checked", "Match", "Differs",
               "Not confirmed", "Unreadable", "No document", "Blank", "Last checked"]])
    _style_header(ws)
    for pas, rec in sorted(section.items(), key=lambda kv: (kv[1].get("program", ""), kv[1].get("student", ""))):
        counts: Dict[str, int] = {}
        for r in rec.get("rows", []):
            counts[str(r["result"])] = counts.get(str(r["result"]), 0) + 1
        checked = (counts.get(fc.MATCH, 0) + counts.get(fc.DIFFERS, 0) + counts.get(not_confirmed, 0)
                   + counts.get(fc.UNREADABLE, 0))
        ws.append([rec.get("student", ""), pas, rec.get("program", ""), checked,
                   counts.get(fc.MATCH, 0), counts.get(fc.DIFFERS, 0), counts.get(not_confirmed, 0),
                   counts.get(fc.UNREADABLE, 0), counts.get(fc.NO_DOC, 0), counts.get("BLANK", 0),
                   _stamp(rec.get("checked"))])
        if counts.get(fc.DIFFERS, 0):
            ws.cell(row=ws.max_row, column=6).fill = PatternFill("solid", fgColor="FFC7CE")
    for col, w in zip("ABCDEFGHIJK", (28, 13, 11, 9, 8, 8, 13, 11, 13, 7, 17)):
        ws.column_dimensions[col].width = w

    for program in _programs_in(section):
        sheet = wb.create_sheet(program[:31])
        sheet.append([_cell(x) for x in ["Student", "Passport", "Field", "Portal value", "Result", "Detail"]])
        _style_header(sheet)
        for pas, rec in sorted(section.items(), key=lambda kv: kv[1].get("student", "")):
            if rec.get("program") != program:
                continue
            for r in rec.get("rows", []):
                sheet.append([_cell(x) for x in [rec.get("student", ""), pas, r.get("field", ""), r.get("portal", ""),
                              r.get("result", ""), r.get("detail", "")]])
                sheet.cell(row=sheet.max_row, column=5).fill = PatternFill(
                    "solid", fgColor=colour.get(str(r.get("result", "")), "FFFFFF"))
        for col, w in zip("ABCDEF", (28, 13, 22, 30, 14, 80)):
            sheet.column_dimensions[col].width = w

    # What changed on the portal after a check — usually an employee acting on a flag.
    ws3 = wb.create_sheet("Corrections")
    ws3.append([_cell(x) for x in ["Noticed", "Student", "Passport", "Program", "Field", "Was on portal",
                "Now on portal", "Was", "Now", "Detail"]])
    _style_header(ws3)
    for c in reversed(store.get("corrections", [])):       # newest first
        ws3.append([str(c.get("noticed", ""))[:16].replace("T", " "), c.get("student", ""),
                    c.get("passport", ""), c.get("program", ""), c.get("field", ""),
                    c.get("was", ""), c.get("now", ""), c.get("was_result", ""),
                    c.get("now_result", ""), c.get("detail", "")])
        ws3.cell(row=ws3.max_row, column=9).fill = PatternFill(
            "solid", fgColor=colour.get(str(c.get("now_result", "")), "FFFFFF"))
        ws3.cell(row=ws3.max_row, column=8).fill = PatternFill(
            "solid", fgColor=colour.get(str(c.get("was_result", "")), "FFFFFF"))
    for col, w in zip("ABCDEFGHIJ", (17, 28, 13, 11, 22, 26, 26, 14, 14, 70)):
        ws3.column_dimensions[col].width = w
    wb.save(path)
    return path


def write_reports(store: Dict[str, Dict[str, Any]]) -> List[Path]:
    """Both reports straight into place (finish_reports is the safe way: an open file there
    costs nothing)."""
    return [write_document_report(store), write_field_report(store)]


def _remove(path: Path) -> None:
    try:
        path.unlink(missing_ok=True)
    except OSError:
        pass


def _unwritten(name: str, e: Exception) -> str:
    return (f"📄 {name}.xlsx could not be written ({type(e).__name__}: {_trim(str(e), 120)}) — "
            "the next pass tries again.")


def _save_report(writer, store: Dict[str, Any], path: Path) -> str:
    """Write one report through "<NAME>.tmp.xlsx" and move it into place. When the usual file is
    open in Excel, the new report is kept as "<NAME> (updated HH-MM).xlsx" instead (replacing
    the previous such copy) and the note says so. -> "" or the note."""
    name = path.stem
    tmp = path.with_name(f"{name}.tmp.xlsx")
    stale = lambda: [p for p in path.parent.glob(f"{name} (updated *).xlsx")]   # noqa: E731
    try:
        writer(store, tmp)
    except Exception as e:
        logger.warning("%s could not be written: %s", path.name, e)
        _remove(tmp)
        return _unwritten(name, e)
    try:
        os.replace(tmp, path)
    except PermissionError:
        alt = path.with_name(f"{name} (updated {_now():%H-%M}).xlsx")
        for old in stale():
            if old != alt:
                _remove(old)
        try:
            os.replace(tmp, alt)
        except OSError as e:
            _remove(tmp)
            return (f"📄 {name}.xlsx was open in Excel and the new report could not be saved beside it "
                    f"({type(e).__name__}) — close it and the next pass writes it again.")
        return (f"📄 {name}.xlsx was open in Excel — this pass saved '{alt.name}'; close it and the "
                "next pass writes the usual file again.")
    except OSError as e:
        _remove(tmp)
        return _unwritten(name, e)
    for old in stale():                                # the usual file is current again
        _remove(old)
    return ""


def finish_reports(result: Optional[Dict[str, Any]]) -> List[str]:
    """Write DOCUMENT CHECK.xlsx and FIELD CHECK.xlsx from the pass's store, when the pass says
    they are due (result["reports_due"]). Called after the pass's Telegram message, so a report
    left open in Excel never costs a message. Never raises. -> notes for a follow-up message, e.g.
    that a report was saved under another name because the usual one was open."""
    try:
        if not result or not result.get("reports_due"):
            return []
        store = result.get("store")
        if not isinstance(store, dict) or not isinstance(store.get("documents"), dict):
            return []
        notes = []
        for writer, path in ((write_document_report, DOC_REPORT), (write_field_report, FIELD_REPORT)):
            note = _save_report(writer, store, Path(path))
            if note:
                notes.append(note)
        return notes
    except Exception as e:
        logger.warning("the reports could not be written: %s", e)
        return [f"📄 The reports could not be written ({type(e).__name__}) — the next pass tries again."]


# --- the pass the scheduler runs ------------------------------------------------------
def _result() -> Dict[str, Any]:
    return {"checked": [], "reevaluated": [], "rollout_lines": [], "republish": [], "errors": [],
            "skipped": [], "waiting": 0, "store": None, "reports_due": False}


def run(budget: int = DEFAULT_BUDGET, only: List[str] | None = None, recheck: bool = False, *,
        force: bool = False, allow_ocr: bool = True, reeval: bool = True,
        reeval_seconds: Optional[float] = REEVAL_SECONDS, export_rows: Optional[List[Dict[str, Any]]] = None
        ) -> Dict[str, Any]:
    """One pass: re-evaluate what is due from the saved text (at most `reeval_seconds`; the
    roll-out when the rules changed), then check what is pending (up to `budget` students; 0 =
    no limit). The reports are not written here: the result says whether they are due
    (finish_reports, after the message).

    `only` limits the pass to these passports. `recheck` re-evaluates every stored student (of
    `only`) whatever is due, with no time limit, and never blanks a fingerprint; `force` does the
    same for `only`. Both read files whose text was never saved only when `allow_ocr`, and leave
    the queue alone. `export_rows` stand in for the portal's student list.

    -> {"checked": [entry], "reevaluated": [change], "rollout_lines": [str], "republish": [P],
        "errors": [entry], "skipped": [str], "waiting": n, "store": store | None,
        "reports_due": bool, "busy"?, "stopped"?, "store_error"?, "store_note"?}
    entry = {"student", "passport", "program", "verdict", "previous", "first", "differs",
             "corrected", "reason"}"""
    with _locked() as ok:
        if not ok:
            print(f"Another check is already running (pid {_lock_pid()}) — skipped.")
            return {**_result(), "busy": True}
        return _pass(budget=budget, only=only, recheck=recheck, force=force, allow_ocr=allow_ocr,
                     reeval=reeval, reeval_seconds=reeval_seconds, export_rows=export_rows,
                     queue=True, hand_out=True)


def _pass(*, budget: int, only, recheck: bool, force: bool, allow_ocr: bool, reeval: bool,
          reeval_seconds: Optional[float], export_rows, queue: bool, hand_out: bool) -> Dict[str, Any]:
    """The pass itself, under the lock. `hand_out`: a completed roll-out's summary goes into
    this result and is marked announced (the scheduled pass; its caller sends it). The command
    line marks it only once Telegram accepted it (--announce)."""
    global _issue_memo
    from src.verify import doc_verifier as dv
    result = _result()

    stop = preflight()
    if stop:
        result["stopped"] = stop
        print(f"document check paused: {stop}")
        return result
    try:
        store = load_store()
    except StoreUnreadable as e:
        result["store_error"] = str(e)
        print(f"document check stopped: {e}")
        return result
    note = store.pop("_note", None)
    if note:
        result["store_note"] = note
        print(note)
    portal = dv.portal_students(rows=export_rows) if export_rows is not None else dv.portal_students()
    folders = dv.student_folders(portal)
    odd = _implausible(store, folders, portal)
    if odd:
        result["stopped"] = odd
        print(f"document check paused: {odd}")
        return result
    result["store"] = store

    _issue_memo = _load_issue_dates()
    try:
        _work(result, store, folders, portal, budget=budget, only=only, recheck=recheck, force=force,
              allow_ocr=allow_ocr, reeval=reeval, reeval_seconds=reeval_seconds, queue=queue,
              hand_out=hand_out)
    finally:
        _issue_memo = None
    return result


def _work(result: Dict[str, Any], store: Dict[str, Any], folders, portal, *, budget, only, recheck,
          force, allow_ocr, reeval, reeval_seconds, queue, hand_out) -> None:
    meta = store.setdefault("meta", {})
    meta_before = json.dumps(meta, sort_keys=True, default=str)
    now = _now()
    sel = [p.strip().upper() for p in only if p and p.strip()] if only else None

    # 1. re-evaluation from the saved text; the roll-out when the rules changed
    if meta.get("rules_version") != RULES_VERSION and not store["documents"]:
        meta["rules_version"] = RULES_VERSION          # nothing stored yet: nothing to roll out
    rolling = meta.get("rules_version") != RULES_VERSION
    forced = bool(recheck or force)
    reev = None
    if rolling and (forced or reeval):
        _start_rollout(store)
    if forced:
        reev = reevaluate(store, folders, portal, passports=sel, force=True, allow_ocr=allow_ocr,
                          why="manual", save=save_store)
    elif reeval:
        deadline = None if reeval_seconds is None else _clock() + reeval_seconds
        reev = reevaluate(store, folders, portal, passports=sel, deadline=deadline, why="daily",
                          save=save_store)
    paused = False
    if reev is not None:
        result["reeval"] = reev
        result["republish"] = list(reev["republish"])
        if reev["evaluated"]:
            result["reports_due"] = True
        if reev["done"] and not sel and now.hour >= DAILY_REEVAL_HOUR:
            meta["reevaluated_on"] = now.date().isoformat()
        if rolling:
            _merge_rollout(meta["rollout"], reev)
            lines = _close_rollout(store, folders, portal)
            if lines is None:
                paused = True                          # the queue waits for the roll-out
            else:
                result["rollout_lines"] = lines
                result["reports_due"] = True
        else:
            result["reevaluated"] = list(reev["changed"])
    if hand_out and _unannounced(store):
        result["rollout_lines"] = list(result["rollout_lines"] or _unannounced(store).get("lines")
                                       or rollout_lines(_unannounced(store)))
        _mark_announced(store)
    if json.dumps(meta, sort_keys=True, default=str) != meta_before:
        save_store(store)

    # 2. the queue: students whose documents must be read
    waiting = pending(store, folders, portal)
    if sel:
        waiting = [p for p in waiting if p in sel]
    todo: List[str] = []
    if queue and allow_ocr and not forced and not paused:
        todo = waiting[:budget] if budget else list(waiting)
    for i, pas in enumerate(todo, 1):
        student = portal[pas]
        name = student.get("Full Name", "")
        try:
            out = check_one(pas, folders[pas][0], student, store)
        except _CODE_FAULTS as e:
            # A fault in the rules themselves affects every student.  Skipping them
            # one by one produces a report that looks clean and is worthless, so the
            # run stops and says so.  (A NameError in check_bank once did exactly
            # that: every bank check was skipped and nothing in the report showed it.)
            raise RuntimeError(
                f"the rules failed on {name} ({pas}): {type(e).__name__}: {e}. "
                "This is a code fault, not a document problem — the run has stopped "
                "so the report is not silently wrong.") from e
        except Exception as e:
            logger.warning("%s (%s) could not be checked: %s", name, pas, e)
            result["skipped"].append(f"{name} ({pas}): {_trim(str(e), 80)}")
            continue
        save_store(store)                  # a stopped run never loses finished work
        if out["kind"] in ("retry", "error_kept"):
            err = out["error"]
            result["errors"].append(err)
            print(f"[{i}/{len(todo)}] {name[:30]:32} read error ({err['doc']}: {err['msg']}) — "
                  f"retried on the next pass", flush=True)
            continue
        if out["kind"] != "done":
            continue
        entry = out["entry"]
        result["checked"].append(entry)
        print(f"[{i}/{len(todo)}] {name[:30]:32} {entry['verdict']:11} {out['field_note']}"
              + (f" | {entry['corrected']} portal field(s) changed since last check" if entry["corrected"] else ""),
              flush=True)
    result["waiting"] = max(0, len(waiting) - len(result["checked"]))
    if result["checked"] or result["errors"] or not DOC_REPORT.exists() or not FIELD_REPORT.exists():
        result["reports_due"] = True
    if result["skipped"]:
        print(f"\n{len(result['skipped'])} student(s) could not be read:")
        for s in result["skipped"][:10]:
            print(f"   {s}")


# --- the Telegram lines --------------------------------------------------------------------
_FINDING = re.compile(r"^(PASS|NOTE|FLAG|FAIL|ERROR|MISSING):\s*(.*)$", re.S)
_NEW_ORDER = {"PASS": 0, "REVIEW": 1, "INCOMPLETE": 2, "FAIL": 3, "ERROR": 4}     # roll-out list order
_SEVERITY = {"PASS": 0, "REVIEW": 1, "ERROR": 2, "FAIL": 3, "INCOMPLETE": 4}     # student_verdict's order
_WHY = {"daily": " [re-checked: today's date]", "portal": " [portal values changed]",
        "rules": " [rules updated]"}


def _findings(detail: Any) -> List[Tuple[str, str]]:
    """A row's detail as its findings: [(verdict, text)], "" for a part with no verdict."""
    out = []
    for part in str(detail or "").split(" | "):
        m = _FINDING.match(part.strip())
        out.append((m.group(1), m.group(2).strip()) if m else ("", part.strip()))
    return out


def _first_fail(rows: List[Dict[str, Any]]) -> str:
    """The first failing rule: "{document}: {finding}"."""
    for r in rows:
        if r.get("verdict") == "FAIL":
            detail = str(r.get("detail", ""))
            m = re.search(r"\bFAIL:\s*([^|]+)", detail)
            detail = m.group(1).strip() if m else detail
            return _trim(f"{r.get('doc', '')}: {detail}".strip(": "), 160)
    return ""


def _missing(rows: List[Dict[str, Any]]) -> List[str]:
    return [str(r.get("doc", "")) for r in rows if r.get("verdict") == "MISSING"]


def _review_reasons(rows: List[Dict[str, Any]], limit: int = 2) -> List[str]:
    """The first FLAG of the first `limit` flagged documents, in row order."""
    out, seen = [], set()
    for r in rows:
        doc = str(r.get("doc", ""))
        if r.get("verdict") != "FLAG" or doc in seen:
            continue
        flag = next((t for v, t in _findings(r.get("detail")) if v == "FLAG"), "") or str(r.get("detail", ""))
        out.append(f"{doc}: {_trim(flag, 90)}".strip(": "))
        seen.add(doc)
        if len(out) >= limit:
            break
    return out


def _error_msg(text: str) -> str:
    """The technical reason inside doc_verifier's ERROR finding."""
    text = str(text or "").strip()
    m = re.match(r"could not check this file \((.*)\)(?:\s*— it is retried on the next pass)?$", text, re.S)
    text = m.group(1) if m else re.sub(r"\s*— it is retried on the next pass$", "", text)
    return _trim(text, 120)


def _error_of(rows: List[Dict[str, Any]]) -> Tuple[str, str]:
    """(document, technical reason) of the first row that could not be checked."""
    for r in rows:
        if C.row_has_error(r):
            msg = next((t for v, t in _findings(r.get("detail")) if v == "ERROR"), "") or str(r.get("detail", ""))
            return str(r.get("doc", "")), _error_msg(msg)
    return "", ""


def verdict_reason(rec: Dict[str, Any]) -> str:
    """Why a student has their verdict, for the per-pass message: the first FAIL; the documents
    missing (and how many FAILs besides); the first FLAG of up to two documents; the read
    error; nothing for PASS."""
    verdict, rows = rec.get("verdict"), rec.get("rows") or []
    if verdict == "FAIL":
        return _first_fail(rows)
    if verdict == "INCOMPLETE":
        fails = sum(1 for r in rows if r.get("verdict") == "FAIL")
        return f"missing: {', '.join(_missing(rows))}" + (f"; plus {fails} FAIL" if fails else "")
    if verdict == "REVIEW":
        return "; ".join(_review_reasons(rows))
    if verdict == "ERROR":
        doc, msg = _error_of(rows)
        retry = rec.get("retry") or {}
        tail = (f"could not be checked {retry.get('count', MAX_ERROR_PASSES)} passes in a row"
                if retry.get("final") or rec.get("fingerprint") else "retried next pass")
        return f"{doc}: {msg} — {tail}".strip(": ")
    return ""


def _blocking(rec: Optional[Dict[str, Any]]) -> str:
    """The one finding that holds a verdict where it is ("" for PASS)."""
    if not rec:
        return ""
    verdict, rows = rec.get("verdict"), rec.get("rows") or []
    if verdict == "FAIL":
        return _first_fail(rows)
    if verdict == "INCOMPLETE":
        return f"missing: {', '.join(_missing(rows))}"
    if verdict == "REVIEW":
        top = _review_reasons(rows, 1)
        return top[0] if top else ""
    if verdict == "ERROR":
        doc, msg = _error_of(rows)
        return f"{doc}: {msg}".strip(": ")
    return ""


def _accepted_instead(old_rec: Dict[str, Any], new_rec: Dict[str, Any]) -> str:
    """A required document that was missing and is now covered by its alternative (a deceased
    parent's death certificate for their NID): "05 Father NID: death certificate accepted"."""
    was_missing = set(_missing(old_rec.get("rows") or []))
    for r in new_rec.get("rows") or []:
        m = re.search(r"accepted in place of[^(]*\((.+?) not needed\)", str(r.get("detail", "")))
        if m and m.group(1) in was_missing:
            doc = str(r.get("doc", ""))
            what = "death certificate" if "death" in doc.lower() else re.sub(r"^[\d.]+\s*", "", doc)
            return f"{m.group(1)}: {what} accepted"
    return ""


def change_reason(old_rec: Optional[Dict[str, Any]], new_rec: Dict[str, Any]) -> str:
    """Why a verdict changed: when it got worse, what now holds it (the first FAIL, the missing
    documents, the top FLAG, the read error); when it got better, "cleared: " and what held it
    before (a deceased parent shows as "05 Father NID: death certificate accepted")."""
    if not old_rec:
        return _blocking(new_rec)
    old, new = _SEVERITY.get(str(old_rec.get("verdict")), 0), _SEVERITY.get(str(new_rec.get("verdict")), 0)
    if new < old:
        instead = _accepted_instead(old_rec, new_rec)
        if instead:
            return instead
        before = _blocking(old_rec)
        return f"cleared: {before}" if before else "cleared"
    return _blocking(new_rec)


def _first_check(d: Dict[str, Any]) -> bool:
    """Entries without "first" (older callers) are first checks unless they name a previous
    verdict."""
    first = d.get("first")
    return d.get("previous") is None if first is None else bool(first)


def summary_lines(result: Dict[str, Any]) -> List[str]:
    """The document check's part of the Telegram message. A student is announced only on a first
    check or a verdict change, each with its reason; students read again with an unchanged
    verdict are only counted. Read errors (retried on the next pass) and students who could not
    be checked are listed. [] when there is nothing to say."""
    if result.get("store_error"):
        return [f"⚠️ Document check stopped: {result['store_error']} — nothing was overwritten"]
    if result.get("stopped"):
        return [f"⚠️ Document check paused: {result['stopped']} — no student was checked"]
    if result.get("busy"):
        return []
    docs = ((result.get("store") or {}).get("documents") or {})
    checked = list(result.get("checked") or [])
    told = [d for d in checked if _first_check(d) or d.get("previous") != d.get("verdict")]
    same = [d for d in checked if d not in told]
    changes = list(result.get("reevaluated") or [])
    errors = [e for e in result.get("errors") or [] if isinstance(e, dict)]
    skipped = list(result.get("skipped") or [])
    note = result.get("store_note")
    if not (told or changes or errors or skipped or note):
        return []

    def reason(passport: str, verdict: str, fallback: str) -> str:
        rec = docs.get(passport)
        why = verdict_reason({**rec, "verdict": verdict}) if rec else (fallback or "")
        return f" ({why})" if why else ""

    out: List[str] = []
    if note:
        out.append(f"⚠️ {note}")
    if told or changes or same:
        out.append(f"🔍 Documents checked: {len(told) + len(changes)}"
                   + (f" ({len(same)} re-checked, verdict unchanged)" if same else ""))
    for d in told:
        bits = (d["verdict"] if _first_check(d) else f"{d.get('previous')} -> {d['verdict']}") \
            + reason(d.get("passport"), d["verdict"], d.get("reason"))
        if d.get("differs"):
            bits += f", {d['differs']} field(s) differ from the portal"
        if d.get("corrected"):
            bits += f", {d['corrected']} portal field(s) corrected since last check"
        out.append(f"   • {d['student']} ({d['program']}) — {bits}")
    for c in changes:
        out.append(f"   • {c['student']} ({c['program']}) — {c['old']} -> {c['new']}"
                   + reason(c.get("passport"), c["new"], c.get("reason")) + _WHY.get(c.get("why", ""), ""))
    if errors:
        listed = "; ".join(f"{e.get('student')} ({e.get('doc')}: {e.get('msg')})" for e in errors[:5])
        more = f"; … and {len(errors) - 5} more" if len(errors) > 5 else ""
        out.append(f"   ⚠️ {len(errors)} student(s) hit a read error — retried on the next pass: {listed}{more}")
    if skipped:
        more = f"; … and {len(skipped) - 5} more" if len(skipped) > 5 else ""
        out.append(f"   ⚠️ {len(skipped)} student(s) could not be checked: {'; '.join(skipped[:5])}{more}")
    if result.get("waiting"):
        out.append(f"   {result['waiting']} more waiting — they run on the next passes.")
    if any(d["verdict"] != "PASS" or d.get("differs") for d in told) or any(c.get("new") != "PASS" for c in changes):
        out.append(f"   Reports: DOCUMENT CHECK.xlsx / FIELD CHECK.xlsx in {REPORT_DIR}")
    return out


def _distribution(counts: Dict[str, int]) -> str:
    """"FAIL a, REVIEW b, INCOMPLETE c, PASS d" (and ERROR, or any other verdict, when present)."""
    main = ["FAIL", "REVIEW", "INCOMPLETE", "PASS"]
    parts = [f"{v} {counts.get(v, 0)}" for v in main]
    parts += [f"{v} {n}" for v, n in sorted(counts.items(), key=lambda kv: (kv[0] != "ERROR", kv[0]))
              if v not in main and n]
    return ", ".join(parts)


def rollout_lines(ro: Dict[str, Any]) -> List[str]:
    """The one message of a roll-out (ROLLOUT_TITLE): how many students the new rules were
    applied to from the saved text, the verdicts before and now, and every verdict that changed
    with its reason."""
    n = len(ro.get("done") or [])
    lines = [f"Rules {', '.join(ro.get('from') or ['unversioned'])} -> {ro.get('to') or RULES_VERSION}: "
             f"{n} student(s) re-evaluated from the saved text (no new OCR).",
             f"Before: {_distribution(ro.get('before') or {})}",
             f"Now: {_distribution(ro.get('now') or {})}"]
    changes = sorted(ro.get("changes") or [], key=lambda c: (_NEW_ORDER.get(c.get("new"), 9), c.get("student", "")))
    if changes:
        lines.append(f"{len(changes)} verdict(s) changed:")
        for c in changes:
            lines.append(f"   • {c['student']} ({c['program']}) — {c['old']} -> {c['new']}"
                         + (f" ({c['reason']})" if c.get("reason") else ""))
    else:
        lines.append("No verdict changed.")
    if ro.get("needs_ocr"):
        lines.append(f"{len(ro['needs_ocr'])} student(s) have documents whose text was never saved — "
                     "they join the normal queue.")
    if ro.get("skipped"):
        lines.append(f"{len(ro['skipped'])} student(s) could not be re-evaluated (a read error, or no longer "
                     "on the portal or this PC) — their verdicts stay as they were.")
    lines.append("Reports refreshed: DOCUMENT CHECK.xlsx / FIELD CHECK.xlsx")
    return lines


# --- the command line ------------------------------------------------------------------
def dry_run_refusal(data_dir: Any) -> str:
    """"" when `data_dir` is a safe place for a dry run, else why not: it must be a copy — never
    inside a BOT\\data folder (the live data), never this checkout's data\\verification — and the
    copy step must have written the DRY_RUN_COPY marker into it."""
    if not data_dir:
        return "--dry-run needs --data-dir, a copy of data\\verification"
    p = Path(data_dir).resolve()
    parts = [x.lower() for x in p.parts]
    if any(a == "bot" and b == "data" for a, b in zip(parts, parts[1:])):
        return f"{p} is inside a BOT\\data folder (live data) — use a copy somewhere else"
    if p == settings.verification_dir().resolve():
        return f"{p} is this checkout's own data\\verification — use a copy"
    if not (p / DRY_RUN_MARKER).exists():
        return f"{p} has no {DRY_RUN_MARKER} marker file — the copy step writes it"
    return ""


def _show(passports: List[str]) -> None:
    """Print the stored verdict and rows of each passport (changes nothing)."""
    store = _peek_store()
    for pas in passports:
        rec, frec = store["documents"].get(pas), store["fields"].get(pas)
        if not rec and not frec:
            print(f"{pas}: no stored result")
            continue
        rec = rec or {}
        print(f"{rec.get('student') or (frec or {}).get('student', '')} ({pas}) — {rec.get('program', '')} — "
              f"{rec.get('verdict', '?')}")
        bits = [f"rules {rec.get('rules_version') or 'unversioned'}", f"evaluated {_stamp(rec.get('evaluated_at')) or '?'}",
                f"checked {_stamp(rec.get('checked')) or '?'}"]
        if rec.get("needs_ocr"):
            bits.append("text never saved: waits for a fresh read")
        if rec.get("retry"):
            bits.append(f"read error {rec['retry'].get('count')}/{MAX_ERROR_PASSES}: {rec['retry'].get('error')}")
        print("   " + ", ".join(bits))
        for r in rec.get("rows") or []:
            print(f"   {r.get('verdict', ''):8} {r.get('doc', '')}" + (f" [{r.get('file')}]" if r.get("file") else "")
                  + f": {r.get('detail', '')}")
        if frec:
            counts: Dict[str, int] = {}
            for r in frec.get("rows") or []:
                counts[str(r.get("result"))] = counts.get(str(r.get("result")), 0) + 1
            print("   field check: " + ", ".join(f"{k} {v}" for k, v in sorted(counts.items())))
            for r in frec.get("rows") or []:
                if r.get("result") not in ("MATCH", "BLANK"):
                    print(f"     {r.get('field')}: {r.get('portal')} — {r.get('result')} — {r.get('detail')}")


def _dry_run_summary(before: Dict[str, int], result: Dict[str, Any]) -> Path:
    """rollout_summary.txt in the dry run's data folder, for the go/no-go review: the verdicts
    before and after, the roll-out message, every change with its reason, every ERROR row, the
    students whose text was never saved, and every birth-certificate FAIL to spot-check."""
    store = result.get("store") or _empty_store()
    docs = store["documents"]
    meta = store.get("meta") or {}
    ro = meta.get("rollout") or meta.get("last_rollout") or {}
    changes = list(ro.get("changes") or []) if result.get("rollout_lines") else []
    changes += [c for c in result.get("reevaluated") or [] if c not in changes]
    lines = [f"Document check dry run — {_iso()}", f"Rules {RULES_VERSION}; data {REPORT_DIR}", "",
             f"Before: {_distribution(before)}", f"After:  {_distribution(_counts(docs))}", ""]
    if result.get("rollout_lines"):
        lines += [ROLLOUT_TITLE] + list(result["rollout_lines"]) + [""]
    lines.append(f"{len(changes)} verdict change(s):")
    for c in sorted(changes, key=lambda c: (_NEW_ORDER.get(c.get("new"), 9), c.get("student", ""))):
        lines.append(f"   • {c.get('student')} ({c.get('passport')}, {c.get('program')}) — {c.get('old')} -> "
                     f"{c.get('new')} ({c.get('reason') or ''}) [{c.get('why') or ''}]")
    errors = [(rec.get("student", ""), pas, r) for pas, rec in sorted(docs.items())
              for r in rec.get("rows") or [] if C.row_has_error(r)]
    lines += ["", f"{len(errors)} ERROR row(s):"]
    lines += [f"   • {s} ({p}) — {r.get('doc')}: {_error_of([r])[1]}" for s, p, r in errors]
    needs = sorted(p for p, rec in docs.items() if rec.get("needs_ocr"))
    lines += ["", f"{len(needs)} student(s) whose text was never saved (needs_ocr):"]
    lines += [f"   • {docs[p].get('student', '')} ({p})" for p in needs]
    births = [(rec.get("student", ""), pas, r) for pas, rec in sorted(docs.items()) for r in rec.get("rows") or []
              if r.get("verdict") == "FAIL" and (r.get("key") == "birth_cert" or str(r.get("doc", "")).startswith("04 "))]
    lines += ["", f"{len(births)} birth-certificate FAIL(s) — spot-check each:"]
    lines += [f"   • {s} ({p}) — {_first_fail([r])}" for s, p, r in births]
    reev_errors = list((result.get("reeval") or {}).get("errors") or [])
    if reev_errors:
        lines += ["", f"{len(reev_errors)} student(s) could not be re-evaluated:"] + [f"   • {e}" for e in reev_errors]
    path = REPORT_DIR / "rollout_summary.txt"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def _sheet_hooks():
    from src.cloud import sheet_hooks
    return sheet_hooks


def _hand_over_notice(sent: List[Tuple[str, List[str], float]]) -> None:
    """--announce without --publish: the sent roll-out summary as a Supabase notification
    (job portal_sync, as the pass that would have sent it). Never raises."""
    try:
        sh = _sheet_hooks()
        for title, lines, at in sent:
            notice = getattr(sh, "after_verify_notice", None)
            if notice is not None:
                notice(title, lines, at)
            else:
                sh.hand_over(sh.JOB_SYNC, lambda t=title, ls=lines, a=at:
                             ([sh._notification("\n".join(ls), a, "auto_verify", t)], []))
    except Exception as e:
        print(f"the notification could not be handed to Supabase: {type(e).__name__}: {e}")


def _publish(result: Dict[str, Any], sent: List[Tuple[str, List[str], float]]) -> None:
    """--publish: hand the pass's records (and any notice sent) over to Supabase. Never raises."""
    try:
        import inspect
        sh = _sheet_hooks()
        refresh = getattr(sh, "after_verify_refresh", None)
        if refresh is None:
            print("publishing is not available in this version of sheet_hooks — skipped")
            _hand_over_notice(sent)
            return
        if "sent" in inspect.signature(refresh).parameters:
            path = refresh(result, sent=list(sent))
        else:
            path = refresh(result)
            _hand_over_notice(sent)
        print(f"handed over to Supabase: {path}" if path else "nothing handed over (publishing is off)")
    except Exception as e:
        print(f"publishing failed: {type(e).__name__}: {e}")


def _announce(store: Dict[str, Any]) -> Optional[Tuple[str, List[str], float]]:
    """Send the completed roll-out's summary (ROLLOUT_TITLE) and mark it announced only when
    Telegram accepted it. -> (title, lines, epoch) once sent, else None."""
    ro = _unannounced(store)
    if ro is None:
        pending_ro = (store.get("meta") or {}).get("rollout")
        print("the roll-out is still under way — nothing to announce yet" if pending_ro
              else "no roll-out summary waiting to be sent")
        return None
    lines = list(ro.get("lines") or rollout_lines(ro))
    from src.sheets.auto_sync import notify
    if not notify(lines, title=ROLLOUT_TITLE):
        print("Telegram did not accept the roll-out summary — it stays unsent (try --announce again)")
        return None
    at = time.time()
    _mark_announced(store, _iso(dt.datetime.fromtimestamp(at)))
    save_store(store)
    print("roll-out summary sent")
    return ROLLOUT_TITLE, lines, at


def main(argv: Optional[List[str]] = None) -> int:
    import sys
    try:                                   # a plain console is cp1252 and chokes on emoji
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    logging.basicConfig(level=logging.WARNING, format="%(asctime)s  %(message)s")
    ap = argparse.ArgumentParser(description="Check downloaded documents and portal fields (local, no portal writes).")
    ap.add_argument("--budget", type=int, default=DEFAULT_BUDGET, help="students this pass (0 = no limit)")
    ap.add_argument("--passport", default="",
                    help="these passports only (comma separated): shows their stored result; with --force re-runs them")
    ap.add_argument("--force", action="store_true", help="with --passport: re-run those students now")
    ap.add_argument("--ocr", action="store_true",
                    help="with --force / --recheck: read files whose text was never saved (otherwise saved text only)")
    ap.add_argument("--recheck", action="store_true",
                    help="re-evaluate every stored student from the saved text against the current rules")
    ap.add_argument("--announce", action="store_true",
                    help="send the roll-out summary to Telegram (marked sent only when Telegram accepts it)")
    ap.add_argument("--publish", action="store_true", help="hand the results over to Supabase afterwards")
    ap.add_argument("--wait", action="store_true", help="wait up to 40 minutes for a running check instead of giving up")
    ap.add_argument("--data-dir", default="", help="use this folder instead of data/verification")
    ap.add_argument("--export-file", default="", help="a JSON list of portal export rows, used instead of the portal")
    ap.add_argument("--dry-run", action="store_true",
                    help="a trial on a copy (needs --data-dir): re-evaluates from the saved text, sends and "
                         "publishes nothing, writes rollout_summary.txt")
    ap.add_argument("--pending", action="store_true", help="list what is waiting and stop")
    ap.add_argument("--rebuild", action="store_true", help="only rewrite the two reports")
    ap.add_argument("--recheck-all", action="store_true",
                    help="set every stored result aside (results.cleared-<time>.json) and start over")
    args = ap.parse_args(argv)

    if args.dry_run:
        why = dry_run_refusal(args.data_dir)
        if why:
            print(f"--dry-run refused: {why}")
            return 2
        if args.announce or args.publish:
            print("--dry-run sends and publishes nothing: --announce / --publish ignored")
        args.announce = args.publish = False
    if args.data_dir:
        use_data_dir(args.data_dir)
    export_rows = None
    if args.export_file:
        try:
            export_rows = json.loads(Path(args.export_file).read_text(encoding="utf-8"))
        except Exception as e:
            print(f"--export-file: could not read {args.export_file}: {type(e).__name__}: {e}")
            return 2
        if not isinstance(export_rows, list):
            print("--export-file must hold a JSON list of export rows")
            return 2
    passports = [p.strip().upper() for p in args.passport.split(",") if p.strip()]
    if args.force and not passports:
        print("--force needs --passport")
        return 2

    with _locked(LOCK_WAIT_SECONDS if args.wait else 0) as ok:
        if not ok:
            print(f"Another check is already running (pid {_lock_pid()}) — try again later, or add --wait.")
            return 1
        return _cli(args, passports, export_rows)


def _cli(args, passports: List[str], export_rows) -> int:
    """The command line's work, under the lock."""
    if args.recheck_all and STORE_PATH.exists():
        keep = STORE_PATH.with_name(f"results.cleared-{_now():%Y-%m-%d_%H%M%S}.json")
        os.replace(STORE_PATH, keep)
        save_store(_empty_store(), allow_shrink=True)     # an empty store, so the backup is not restored
        print(f"stored results set aside as {keep.name} — everyone will be checked again")

    if args.rebuild:
        try:
            store = load_store()
        except StoreUnreadable as e:
            print(f"cannot rebuild the reports: {e}")
            return 1
        for n in finish_reports({"reports_due": True, "store": store}):
            print(n)
        print(f"reports: {DOC_REPORT}\n         {FIELD_REPORT}")
        return 0

    rerun = args.recheck or args.force or args.dry_run
    if args.pending or (passports and not rerun):
        try:
            if passports and not args.pending:
                _show(passports)
                return 0
            from src.verify import doc_verifier as dv
            store = _peek_store()
        except StoreUnreadable as e:
            print(f"{e} — nothing was changed")
            return 1
        portal = dv.portal_students(rows=export_rows) if export_rows is not None else dv.portal_students()
        folders = dv.student_folders(portal)
        q = pending(store, folders, portal)
        print(f"{len(q)} student(s) waiting to be checked")
        for p in q[:20]:
            print(f"   {p} ({_status(p, store['documents'].get(p), folders, portal)})")
        return 0

    try:
        before = _counts(_peek_store()["documents"])
    except StoreUnreadable:
        before = {}
    if rerun:
        result = _pass(budget=0, only=passports or None, recheck=not passports, force=bool(passports),
                       allow_ocr=args.ocr, reeval=True, reeval_seconds=None, export_rows=export_rows,
                       queue=False, hand_out=False)
    elif args.announce or args.publish:
        result = _result()
        try:
            result["store"] = load_store()
        except StoreUnreadable as e:
            print(f"results.json cannot be read: {e}")
            return 1
    else:
        result = _pass(budget=args.budget, only=None, recheck=False, force=False, allow_ocr=True, reeval=True,
                       reeval_seconds=REEVAL_SECONDS, export_rows=export_rows, queue=True, hand_out=False)

    for line in summary_lines(result):
        print(line)
    if result.get("busy") or result.get("stopped") or result.get("store_error"):
        return 1
    reev = result.get("reeval") or {}
    if rerun:
        print(f"\nre-evaluated {len(reev.get('evaluated') or [])} student(s); "
              f"{len(reev.get('needs_ocr') or [])} need a fresh read; {len(reev.get('errors') or [])} error(s)")
        for e in reev.get("errors") or []:
            print(f"   ⚠️ {e}")
    if result.get("rollout_lines"):
        print(f"\n{ROLLOUT_TITLE}")
        for line in result["rollout_lines"]:
            print(line)
    if args.dry_run:
        print(f"\nsummary: {_dry_run_summary(before, result)}")

    sent: List[Tuple[str, List[str], float]] = []
    if args.announce and result.get("store") is not None:
        done = _announce(result["store"])
        if done:
            sent.append(done)
    if rerun:
        result["reports_due"] = True                  # a re-run always leaves fresh reports
    notes = finish_reports(result)
    for n in notes:
        print(n)
    if args.announce and notes:
        try:
            from src.sheets.auto_sync import CHECK_TITLE, notify
            if notify(notes, title=CHECK_TITLE):
                sent.append((CHECK_TITLE, list(notes), time.time()))
        except Exception as e:
            print(f"the report note could not be sent: {type(e).__name__}: {e}")
    if args.publish:
        _publish(result, sent)
    elif sent:
        _hand_over_notice(sent)
    print(f"\nreports: {DOC_REPORT}\n         {FIELD_REPORT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
