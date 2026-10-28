"""Plain-English messages of the window (APP_SPEC rev 1 section 3.3 and rev 2 R2.3.2).

Codes come from the worker's error events, from the window's own pre-checks and from the card probe. Every text is
filled with VALUES only (a card name, a driver number, GB numbers, a page range, a file's base name, a step name),
never with a path: `safe_value` cuts any path down to its last part and `safe_detail` blanks paths in free text.

Standard library only; no I/O; no personal data (placeholders only).
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass
from typing import Any, Dict, Mapping, Optional, Tuple

# ------------------------------------------------------------------------------------------------ kinds
ERROR, WAIT, NOTE, INFO, CONFIRM = "error", "wait", "note", "info", "confirm"


@dataclass(frozen=True)
class Message:
    code: str
    title: str
    text: str
    kind: str = ERROR          # error (red) | wait (blue, with Cancel) | note (yellow) | info | confirm


# ------------------------------------------------------------------------------------------------ codes
# card codes (R2.3.1, in the order gpuinfo.problems reports them)
NO_NVIDIA_CARD = "NO_NVIDIA_CARD"
DRIVER_TOO_OLD = "DRIVER_TOO_OLD"
CARD_NOT_SUPPORTED = "CARD_NOT_SUPPORTED"
CARD_TOO_SMALL = "CARD_TOO_SMALL"
LOW_CARD_MEMORY = "LOW_CARD_MEMORY"
CARD_FAILED = "CARD_FAILED"
HARD_CARD_CODES = (NO_NVIDIA_CARD, DRIVER_TOO_OLD, CARD_NOT_SUPPORTED, CARD_TOO_SMALL)
CARD_CODES = HARD_CARD_CODES + (LOW_CARD_MEMORY, CARD_FAILED)

# rev 1 section 3.3
ORDER = "ORDER"
STOP = "STOP"
NOT_PDF_OR_FOLDER = "NOT_PDF_OR_FOLDER"
EMPTY_FOLDER = "EMPTY_FOLDER"
FOLDER_OF_FOLDERS = "FOLDER_OF_FOLDERS"
ENCRYPTED = "ENCRYPTED"
UNREADABLE_PDF = "UNREADABLE_PDF"
NO_PAGES = "NO_PAGES"
UNREADABLE_IMAGE = "UNREADABLE_IMAGE"
PATH_TOO_LONG = "PATH_TOO_LONG"
REPORT_INSIDE_INPUT = "REPORT_INSIDE_INPUT"
INPUT_INSIDE_REPORT = "INPUT_INSIDE_REPORT"
REPORT_NOT_WRITABLE = "REPORT_NOT_WRITABLE"
DISK_FULL = "DISK_FULL"
LOW_MEMORY = "LOW_MEMORY"
INPUT_CHANGED = "INPUT_CHANGED"
CPU_UNSUPPORTED = "CPU_UNSUPPORTED"
WINDOWS_TOO_OLD = "WINDOWS_TOO_OLD"
MISSING_PART = "MISSING_PART"
STARTUP_FAILED = "STARTUP_FAILED"
CRASH = "CRASH"
CANCELLED = "CANCELLED"

# the window's own notes (not worker codes)
LOW_CARD_MEMORY_NOTE = "LOW_CARD_MEMORY_NOTE"
LOW_RAM_WARNING = "LOW_RAM_WARNING"
NO_XLSX_PROGRAM = "NO_XLSX_PROGRAM"
FOLDER_GONE = "FOLDER_GONE"
FILE_MISSING = "FILE_MISSING"
SAVE_INSIDE_INPUT = "SAVE_INSIDE_INPUT"
SAVE_FAILED = "SAVE_FAILED"
SAVED = "SAVED"
ALREADY_OPEN = "ALREADY_OPEN"
NO_INPUT = "NO_INPUT"
BAD_DATE = "BAD_DATE"
SELFTEST_RUNNING = "SELFTEST_RUNNING"
SELFTEST_OK = "SELFTEST_OK"
SELFTEST_FAILED = "SELFTEST_FAILED"

NEED_FREE_MB = 4096          # = gpuinfo.NEED_FREE_MB (the text says "4.0 GB")
MEMORY_WAIT_MIN = 15         # = cardgate.MEMORY_WAIT_S / 60
RAM_NEED_GB = "4.5"          # R2.6 P5: the worker's peak target

WAIT_TITLE = "Waiting for the graphics card"
WAIT_KINDS = ("bot", "other", "memory")

# ------------------------------------------------------------------------------------------------ table
# code -> (title, template, kind). Templates use {file} {stage} {driver} {name} {total} {free} {need} {pages}
# {message} {code}; every value is passed through safe_value first.
_TABLE: Dict[str, Tuple[str, str, str]] = {
    # ---- the graphics card (R2.3.2)
    NO_NVIDIA_CARD: (
        "No NVIDIA graphics card",
        "This checker reads documents on an NVIDIA graphics card, and none was found on this PC (or its driver is "
        "not installed). Use a PC with an NVIDIA GeForce RTX card (RTX 20 series or newer, 6 GB or more) and its "
        "driver. Nothing was checked.", ERROR),
    DRIVER_TOO_OLD: (
        "Graphics driver too old",
        "The NVIDIA driver on this PC (version {driver}) is too old for the checker. Install NVIDIA driver 570 or "
        "newer (it supports CUDA 12.8) from NVIDIA's website or the NVIDIA App, restart the PC and check again. "
        "Nothing was checked.", ERROR),
    CARD_NOT_SUPPORTED + ":old": (
        "Graphics card not supported",
        "This PC's graphics card ({name}) is too old for the checker's reading engine. It needs a GeForce RTX 20 "
        "series card or newer. Nothing was checked.", ERROR),
    CARD_NOT_SUPPORTED + ":new": (
        "Graphics card not supported",
        "This PC's graphics card ({name}) is newer than this version of the checker supports. A newer version of "
        "the checker is needed. Nothing was checked.", ERROR),
    CARD_TOO_SMALL: (
        "Graphics card memory too small",
        "This PC's graphics card ({name}) has {total} GB of memory; the checker needs a card with at least 6 GB. "
        "Nothing was checked.", ERROR),
    LOW_CARD_MEMORY: (
        "Graphics card busy",
        "Only {free} GB of the graphics card's memory is free; the checker needs {need} GB. Close programs that use "
        "the graphics card (games, video or photo editors, other AI tools) and check again.", ERROR),
    CARD_FAILED: (
        "Graphics card error",
        "The graphics card stopped working during the check (step '{stage}'). Your files were not changed. Restart "
        "the PC and check again; if it happens again, update the NVIDIA driver, run the Self-test and send "
        "SELFTEST.txt.", ERROR),
    # ---- rev 1 section 3.3
    STOP: (
        "Check stopped",
        "The father's and the mother's NID pages could not be told apart ({pages}). Put the father's NID first, "
        "then the mother's, and check again.", ERROR),
    NOT_PDF_OR_FOLDER: (
        "Not a PDF",
        "Choose one .pdf file (the whole bundle) or one folder of the student's documents.", ERROR),
    EMPTY_FOLDER: (
        "Empty folder",
        "This folder has no PDF, JPG or PNG files. Choose the folder that holds the student's documents.", ERROR),
    FOLDER_OF_FOLDERS: (
        "Wrong folder",
        "This folder holds other folders, not documents. Open it and choose one student's folder.", ERROR),
    ENCRYPTED: (
        "Password-protected PDF",
        "{file} is password-protected. Open it, save or print a copy without the password, and check that copy.",
        ERROR),
    UNREADABLE_PDF: (
        "PDF cannot be opened",
        "{file} could not be opened. It may be damaged or not a real PDF. If it opens in a PDF viewer, print it to "
        "a new PDF and check that.", ERROR),
    NO_PAGES: ("Empty PDF", "{file} has no pages.", ERROR),
    UNREADABLE_IMAGE: ("Picture cannot be opened", "{file} is not a readable JPG or PNG picture.", ERROR),
    PATH_TOO_LONG: (
        "Path too long",
        "The file or report path is too long for Windows. Move the folder closer to the drive (for example "
        "C:\\Checks) or choose a shorter report folder.", ERROR),
    REPORT_INSIDE_INPUT: (
        "Choose another report folder",
        "The report folder is inside the folder being checked (or the other way round). Choose another report "
        "folder in Options.", ERROR),
    INPUT_INSIDE_REPORT: (
        "Choose another report folder",
        "The report folder is inside the folder being checked (or the other way round). Choose another report "
        "folder in Options.", ERROR),
    REPORT_NOT_WRITABLE: (
        "Cannot save reports",
        "The report folder cannot be written. Choose another folder in Options.", ERROR),
    DISK_FULL: (
        "Disk full",
        "There is not enough free disk space for the report. Free some space and check again.", ERROR),
    LOW_MEMORY: ("Not enough memory", "The PC ran out of memory. Close other programs and check again.", ERROR),
    INPUT_CHANGED: (
        "File changed",
        "The file changed while it was being checked (still scanning or syncing?). Check again when it is finished.",
        ERROR),
    CPU_UNSUPPORTED: (
        "Processor too old",
        "This PC's processor lacks an instruction the reading engine needs (SSE4.2). Use a newer PC, and send "
        "SELFTEST.txt.", ERROR),
    WINDOWS_TOO_OLD: ("Windows too old", "This app needs Windows 10 version 1809 or newer (64-bit).", ERROR),
    MISSING_PART: (
        "A program file is missing",
        "{file} is missing or changed — often an antivirus removed it. Restore it from the antivirus history, allow "
        "this folder, or install again.", ERROR),
    STARTUP_FAILED: (
        "Could not start the check",
        "The checking part did not start. Your antivirus may have blocked it. Run the Self-test and send "
        "SELFTEST.txt.", ERROR),
    CRASH: (
        "The check stopped",
        "The check stopped unexpectedly in step '{stage}'. Your files were not changed. Run the Self-test, then send "
        "SELFTEST.txt and checkfacts.json.", ERROR),
    CANCELLED: ("Cancelled", "Check cancelled. Nothing was saved.", INFO),
    # ---- the window's own notes
    LOW_CARD_MEMORY_NOTE: (
        "Graphics card busy",
        "Only {free} GB of the graphics card's memory is free now; a check needs {need} GB. A check waits for it "
        "(at most {minutes} min). Close programs that use the graphics card.", NOTE),
    LOW_RAM_WARNING: (
        "Little free memory",
        "Only {free} GB of the PC's memory is free; a check needs about {need} GB. Close other programs, then "
        "continue.", CONFIRM),
    NO_XLSX_PROGRAM: (
        "No program for Excel files",
        "No program on this PC opens Excel files. The report folder is opened instead; report.xlsx is in it.", INFO),
    FOLDER_GONE: ("Folder not found", "That report folder no longer exists.", INFO),
    FILE_MISSING: ("File not found", "{file} is not in the report folder.", INFO),
    SAVE_INSIDE_INPUT: (
        "Choose another place",
        "The PDF cannot be saved inside the file or folder being checked. Choose another place.", ERROR),
    SAVE_FAILED: ("Could not save", "The PDF could not be saved there. Choose another place.", ERROR),
    SAVED: ("Saved", "The summary was saved as {file}.", INFO),
    ALREADY_OPEN: ("Already open", "The checker is already open.", INFO),
    NO_INPUT: ("Nothing chosen", "Choose a PDF or a folder first.", INFO),
    BAD_DATE: ("Check date", "Type the fixed check date as YYYY-MM-DD (for example 2026-10-28).", ERROR),
    SELFTEST_RUNNING: (
        "Self-test",
        "The self-test is running (about 2 minutes; longer while the graphics card is busy).", INFO),
    SELFTEST_OK: (
        "Self-test",
        "Self-test finished: every part is OK. SELFTEST.txt is in the report folder.", INFO),
    SELFTEST_FAILED: (
        "Self-test",
        "Self-test finished with problems. Send SELFTEST.txt from the report folder.", ERROR),
}

_DEFAULTS = {"file": "The file", "stage": "unknown", "driver": "unknown", "name": "model not known",
             "total": "?", "free": "?", "need": "4.0", "pages": "the pages", "message": "", "code": "UNKNOWN",
             "minutes": str(MEMORY_WAIT_MIN)}

_CODE_RE = re.compile(r"^[A-Z][A-Z0-9_]{0,39}$")
_DRIVE_PATH = re.compile(r"(?:[A-Za-z]:[\\/]|\\\\|//)[^\s\"'<>|]*")
_BACKSLASH_TOKEN = re.compile(r"[^\s\"'<>|]*\\[^\s\"'<>|]*")


# ------------------------------------------------------------------------------------------------ values
def _clean(v: Any) -> str:
    if isinstance(v, float) and not math.isfinite(v):
        return "?"
    return "".join(ch for ch in str(v) if ch >= " " and ch != "\x7f")


def safe_value(v: Any, limit: int = 80) -> str:
    """A short value for a message (a card name, a driver, a file name): text, never a path. A value that looks
    like a path (a drive letter, a UNC prefix, a leading slash, or any backslash) is cut to its last part; control
    characters are dropped; long values are cut."""
    if v is None:
        return ""
    s = _clean(v).strip()
    if "\\" in s or re.match(r"^(?:[A-Za-z]:|//|/)", s):
        parts = [p for p in re.split(r"[\\/]", s) if p]
        s = parts[-1] if parts else ""
        if re.match(r"^[A-Za-z]:$", s):
            s = ""
    s = s.strip()
    return s if len(s) <= limit else s[: limit - 1] + "…"


def safe_text(v: Any, limit: int = 300) -> str:
    """A longer value (a runner message inside a sentence): kept as prose, but any absolute path in it becomes
    <path> (forward slashes in words such as "HSC/SSC" stay)."""
    if v is None:
        return ""
    s = _DRIVE_PATH.sub("<path>", _clean(v))
    s = _BACKSLASH_TOKEN.sub("<path>", s)            # the rest of a path with spaces ("Some Name\file.pdf")
    s = " ".join(s.split())
    return s if len(s) <= limit else s[: limit - 1] + "…"


def safe_relative(v: Any, limit: int = 120) -> str:
    """MISSING_PART names a file relative to the app folder (rev 1 section 1.4). An absolute path is cut to its
    base name; a relative one is kept."""
    if v is None:
        return ""
    s = "".join(ch for ch in str(v) if ch >= " ").strip()
    if re.match(r"^(?:[A-Za-z]:|\\\\|//|[\\/])", s):
        return safe_value(s, limit)
    s = s.replace("/", "\\")
    return s if len(s) <= limit else "…" + s[-(limit - 1):]


def safe_detail(text: Any, limit: int = 300) -> str:
    """A worker's scrubbed text as a small detail line: any path left in it becomes <path>."""
    if not text:
        return ""
    s = "".join(ch if ch >= " " else " " for ch in str(text))
    return safe_text(s, limit)


def gb(mb: Any) -> str:
    """MiB -> "8.0" (GB as the card makers print it: MiB / 1024, one decimal)."""
    try:
        x = float(mb)
    except (TypeError, ValueError):
        return "?"
    if not math.isfinite(x) or x < 0:
        return "?"
    return f"{x / 1024.0:.1f}"


def _gb_value(values: Mapping[str, Any], key: str) -> Optional[str]:
    """`free`/`total` as GB text from any of <key>, <key>_gb, <key>_mb (a bare number above 100 is taken as MB)."""
    if values.get(key + "_mb") not in (None, ""):
        return gb(values[key + "_mb"])
    for k in (key + "_gb", key):
        v = values.get(k)
        if v in (None, ""):
            continue
        try:
            x = float(v)
        except (TypeError, ValueError):
            return safe_value(v, 12)
        if not math.isfinite(x):
            return "?"
        return gb(x) if (k == key and x > 100) else f"{x:.1f}"
    return None


def card_side(values: Mapping[str, Any]) -> str:
    """CARD_NOT_SUPPORTED's "old" / "new" value, whatever key E put it under; else from a "cc" value; else old."""
    for k in ("which", "age", "kind", "side", "reason", "why", "value", "support"):
        v = str(values.get(k, "")).strip().lower()
        if v in ("old", "new"):
            return v
    for v in values.values():
        if str(v).strip().lower() in ("old", "new"):
            return str(v).strip().lower()
    cc = values.get("cc")
    try:
        if isinstance(cc, (tuple, list)) and len(cc) == 2:
            x = float(cc[0]) + float(cc[1]) / 10.0
        else:
            x = float(str(cc))
        return "new" if x > 12.0 else "old"
    except (TypeError, ValueError):
        return "old"


def normalise_values(code: str, values: Optional[Mapping[str, Any]]) -> Dict[str, str]:
    """The message's placeholder values from an event's or a Problem's `values` (several spellings accepted)."""
    vals: Mapping[str, Any] = values or {}
    out: Dict[str, str] = {}
    for k in ("driver", "pages", "minutes"):
        if vals.get(k) not in (None, ""):
            out[k] = safe_value(vals[k], 40)
    if vals.get("stage") not in (None, ""):
        out["stage"] = stage_words(vals["stage"])
    if vals.get("message") not in (None, ""):
        out["message"] = safe_text(vals["message"], 300)
    name = vals.get("name", vals.get("card"))
    if name not in (None, ""):
        out["name"] = safe_value(name)
    f = vals.get("file", vals.get("part", vals.get("name") if code == MISSING_PART else None))
    if f not in (None, ""):
        out["file"] = safe_relative(f) if code == MISSING_PART else safe_value(f)
    for key in ("total", "free"):
        g = _gb_value(vals, key)
        if g is not None:
            out[key] = g
    if vals.get("need_mb") not in (None, ""):
        out["need"] = gb(vals["need_mb"])
    elif vals.get("need") not in (None, ""):
        out["need"] = safe_value(vals["need"], 12)
    return out


def _fill(template: str, values: Mapping[str, str]) -> str:
    def rep(m: "re.Match[str]") -> str:
        k = m.group(1)
        v = values.get(k)
        return v if v not in (None, "") else _DEFAULTS.get(k, "")
    return re.sub(r"\{([a-z_]+)\}", rep, template)


# ------------------------------------------------------------------------------------------------ render
def known(code: str) -> bool:
    return code in _TABLE or code == CARD_NOT_SUPPORTED


def render(code: str, values: Optional[Mapping[str, Any]] = None) -> Message:
    """The window's message for a code and its values. An unknown code gives a CRASH-like text naming the code."""
    code = str(code or "").strip()
    vals = normalise_values(code, values)
    if code == CARD_NOT_SUPPORTED:
        title, tpl, kind = _TABLE[code + ":" + card_side(values or {})]
        if "name" not in vals:                                   # "(model not known)" reads badly here
            tpl = tpl.replace(" ({name})", "")
    elif code in _TABLE:
        title, tpl, kind = _TABLE[code]
    else:
        shown = code if _CODE_RE.match(code) else "UNKNOWN"
        return Message(shown, "The check stopped",
                       f"The check stopped ({shown}). Your files were not changed. Run the Self-test and send "
                       "SELFTEST.txt.", ERROR)
    if code in (CARD_TOO_SMALL,) and "name" not in vals:
        tpl = tpl.replace(" ({name})", "")
    return Message(code, title, _fill(tpl, vals), kind)


def is_card_code(code: str) -> bool:
    return code in CARD_CODES


def is_hard_card_code(code: str) -> bool:
    """A card problem a second try cannot fix: Check stays disabled (R2.1.4)."""
    return code in HARD_CARD_CODES


# ------------------------------------------------------------------------------------------------ waiting
def minutes(waited_s: Any) -> int:
    try:
        x = float(waited_s)
    except (TypeError, ValueError):
        return 0
    return int(x // 60) if math.isfinite(x) and x > 0 else 0


def waiting(why: str, lock: Optional[str] = None, waited_s: Any = 0, free_mb: Any = None,
            need_mb: Any = None) -> Message:
    """The waiting banner of R2.3.2 (why "lock" with lock "bot" / "other", or why "memory"). The window shows
    its Cancel button next to it."""
    m = minutes(waited_s)
    if why == "memory":
        need = gb(need_mb) if need_mb not in (None, "") else gb(NEED_FREE_MB)
        free = gb(free_mb) if free_mb not in (None, "") else "?"
        text = (f"Waiting for the graphics card: {free} GB free, {need} GB needed (waited {m} min, at most "
                f"{MEMORY_WAIT_MIN}). Close programs that use the graphics card.")
        return Message("WAIT_MEMORY", WAIT_TITLE, text, WAIT)
    if lock == "bot":
        text = (f"Waiting for the graphics card: the Telegram bot is checking documents (waited {m} min). The check "
                "goes on by itself when the bot is done.")
        return Message("WAIT_BOT", WAIT_TITLE, text, WAIT)
    text = (f"Waiting for the graphics card: another program's lock file is present (waited {m} min). The check "
            "goes on by itself.")
    return Message("WAIT_OTHER", WAIT_TITLE, text, WAIT)


def low_card_memory_note(free_mb: Any, need_mb: Any = NEED_FREE_MB) -> Message:
    """The yellow note when the card has less free memory than a check needs: Check stays enabled (R2.1.4)."""
    return render(LOW_CARD_MEMORY_NOTE, {"free_mb": free_mb, "need_mb": need_mb})


# ------------------------------------------------------------------------------------------------ lines
def card_line(name: Any = None, total_mb: Any = None, driver: Any = None, found: bool = True) -> str:
    """The footer's card line: "Graphics card: <name> (<total> GB) · driver <driver>"."""
    if not found or not name:
        return "Graphics card: none found"
    s = f"Graphics card: {safe_value(name)}"
    if total_mb not in (None, "", 0):
        s += f" ({gb(total_mb)} GB)"
    if driver not in (None, ""):
        s += f" · driver {safe_value(driver, 20)}"
    return s


def fmt_left(seconds: Any) -> str:
    try:
        x = float(seconds)
    except (TypeError, ValueError):
        return ""
    if not math.isfinite(x) or x < 0:
        return ""
    if x < 100:
        return f"about {max(1, int(round(x)))} s left"
    return f"about {max(2, int(round(x / 60.0)))} min left"


def reading_progress(done: int, of: int, left_s: Any = None) -> str:
    """"Reading page k of N on the graphics card — about X s left" (k = the page being read now)."""
    of = max(0, int(of))
    done = max(0, min(int(done), of)) if of else max(0, int(done))
    if of and done >= of:
        return f"Read {of} of {of} pages on the graphics card"
    k = done + 1
    s = f"Reading page {k} of {of} on the graphics card" if of else f"Reading page {k} on the graphics card"
    left = fmt_left(left_s) if left_s is not None else ""
    return s + (f" — {left}" if left else "")


PROGRESS_STARTING = "Starting the check…"
PROGRESS_CARD = "Starting the graphics card…"
PROGRESS_CARD_READY = "Graphics card ready"
PROGRESS_FIRSTREAD = "Reading the pages on the graphics card…"
PROGRESS_SAVED = "Using the page texts saved by an earlier check"
PROGRESS_PAUSED = "Paused: waiting for the graphics card"
PROGRESS_CANCELLING = "Cancelling…"
PROGRESS_SELFTEST = "Self-test running…"

STAGE_WORDS = {
    "card": "starting the graphics card", "firstread": "reading the pages", "safety": "preparing",
    "ingest": "opening the files", "engine": "loading the reader", "stage": "copying the files",
    "ocr": "reading the pages", "turn": "turning sideways pages", "typing": "sorting the pages",
    "segment": "finding the documents", "birth": "birth certificates", "identity": "identity documents",
    "split": "finding the documents", "verdict": "deciding the verdicts", "cut": "cutting the documents",
    "names": "comparing the names", "bot": "checking the documents", "bank": "bank documents",
    "tin": "TIN certificates", "financial": "the financial chain", "report": "writing the report",
}


def stage_words(key: Any) -> str:
    k = safe_value(key, 40)
    return STAGE_WORDS.get(k, k or "unknown")


def checking_progress(key: Any) -> str:
    return f"Checking documents… ({stage_words(key)})"


def order_text(pages: str, message: str, blocks: bool = True) -> str:
    """The yellow "Fix these pages" line of an ORDER notice (rev 1 section 3.3)."""
    msg = safe_text(message, 300).rstrip(". ")
    pages = safe_value(pages, 40) or "the pages"
    head = f"Some pages are not in the fixed order. Fix {pages}" + (f": {msg}." if msg else ".")
    if blocks:
        return head + (" The documents on these pages are NOT JUDGED until you rescan or reorder them and check "
                       "again.")
    return head + " (the documents were still checked)"


def stop_text(pages: str) -> str:
    return render(STOP, {"pages": pages}).text


# ------------------------------------------------------------------------------------------------ exits
WIN_ILLEGAL_INSTRUCTION = 0xC000001D
WIN_NO_MEMORY = 0xC0000017
WIN_DLL_NOT_FOUND = 0xC0000135
WIN_ENTRY_NOT_FOUND = 0xC0000139
EXIT_DONE, EXIT_CRASH, EXIT_REFUSED, EXIT_STOP, EXIT_CANCELLED, EXIT_PLATFORM, EXIT_CARD = 0, 1, 2, 3, 4, 5, 6


def classify_exit(exit_code: Optional[int], saw_hello: bool) -> Optional[str]:
    """The code for a worker that ended WITHOUT a done, error or cancelled event (rev 1 section 3.3, R2.3.3).
    None while it runs (exit_code None)."""
    if exit_code is None:
        return None
    try:
        code = int(exit_code)
    except (TypeError, ValueError):
        return CRASH
    u = code & 0xFFFFFFFF
    if u == WIN_ILLEGAL_INSTRUCTION:
        return CPU_UNSUPPORTED
    if u == WIN_NO_MEMORY:
        return LOW_MEMORY
    if u in (WIN_DLL_NOT_FOUND, WIN_ENTRY_NOT_FOUND):
        return MISSING_PART
    if not saw_hello:
        return STARTUP_FAILED
    if (u & 0xF0000000) == 0xC0000000:
        return CRASH
    if code == EXIT_CANCELLED:
        return CANCELLED
    if code == EXIT_CARD:
        return CARD_FAILED
    return CRASH
