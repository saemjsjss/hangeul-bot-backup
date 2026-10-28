"""settings.json of the window (APP_SPEC rev 1 section 3.2, rev 2 R2.2 and R2.3.2).

<DATA>\\settings.json, schema "hdc-settings/2":
    report_root            the report folder the user chose, or null (= the default, worked out at each start)
    last_program           KLP | EAP | BACHELOR | MASTER (remembered)
    seconds_per_page_gpu   this PC's average seconds per page read on the graphics card (3.0 at first)
    pages_measured_gpu     how many pages that average holds (capped, so it keeps following this PC)
    cache_days             saved page texts and cache files are removed after this many days (30, Q3)
    gpu_lock_path          the lock file a check waits for, or null (Off). At the FIRST start the window stores
                           cardgate.default_lock_setting(): the bot's lock when its folder exists, else null.

Revision 1 files ("hdc-settings/1" or no schema; CPU seconds_per_page) are migrated: the CPU number is dropped,
the GPU fields get their defaults. A damaged file gives the defaults (and counts as a first start for the lock
setting, so the bot's lock is watched again). Unknown keys are kept. Writes are atomic (tmp + os.replace).
Standard library only; no personal data (the paths stored are the user's own choices).
"""
from __future__ import annotations

import json
import math
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, Optional, Union

SCHEMA = "hdc-settings/2"
OLD_SCHEMAS = ("hdc-settings/1",)
FILE_NAME = "settings.json"
PROGRAMS = ("KLP", "EAP", "BACHELOR", "MASTER")
PROGRAM_LABELS = {"KLP": "KLP", "EAP": "EAP", "BACHELOR": "Bachelor's", "MASTER": "Master's"}
DEFAULT_PROGRAM = "KLP"
DEFAULT_SECONDS_PER_PAGE_GPU = 3.0
SECONDS_PER_PAGE_RANGE = (0.05, 600.0)
AVERAGE_CAP = 200             # the running average holds at most this many pages, so a new PC's pace wins soon
DEFAULT_CACHE_DAYS = 30
CACHE_DAYS_RANGE = (1, 3650)
MAX_PATH_CHARS = 1024
_KNOWN = ("schema", "report_root", "last_program", "seconds_per_page_gpu", "pages_measured_gpu", "cache_days",
          "gpu_lock_path")
_DROPPED = ("seconds_per_page",)     # rev 1's CPU seconds per page: not a GPU number


@dataclass
class Settings:
    report_root: Optional[str] = None
    last_program: str = DEFAULT_PROGRAM
    seconds_per_page_gpu: float = DEFAULT_SECONDS_PER_PAGE_GPU
    pages_measured_gpu: int = 0
    cache_days: int = DEFAULT_CACHE_DAYS
    gpu_lock_path: Optional[str] = None
    extra: Dict[str, Any] = field(default_factory=dict)      # unknown keys, written back unchanged
    # not stored
    first_start: bool = False        # no file, a damaged file, or no gpu_lock_path key yet
    migrated_from: Optional[str] = None
    recovered: bool = False          # the file was damaged and the defaults were used
    dirty: bool = False              # load() changed something that should be saved (first start, migration)

    def to_json(self) -> Dict[str, Any]:
        d: Dict[str, Any] = dict(self.extra)
        d.update({"schema": SCHEMA, "report_root": self.report_root, "last_program": self.last_program,
                  "seconds_per_page_gpu": round(float(self.seconds_per_page_gpu), 3),
                  "pages_measured_gpu": int(self.pages_measured_gpu), "cache_days": int(self.cache_days),
                  "gpu_lock_path": self.gpu_lock_path})
        return d


# ------------------------------------------------------------------------------------------------ checks
def program_code(v: Any) -> Optional[str]:
    """KLP / EAP / BACHELOR / MASTER from a code or the window's label ("Bachelor's", "Master's")."""
    if not isinstance(v, str):
        return None
    s = v.strip()
    up = s.upper()
    if up in PROGRAMS:
        return up
    for code, label in PROGRAM_LABELS.items():
        if s.casefold() == label.casefold() or up.replace("'", "").rstrip("S") == code:
            return code
    return None


def valid_abs_path(v: Any) -> Optional[str]:
    """An absolute Windows path (drive or UNC) as a string, or None."""
    if not isinstance(v, str):
        return None
    s = v.strip()
    if not s or "\x00" in s or len(s) > MAX_PATH_CHARS or any(ch < " " for ch in s):
        return None
    if not (len(s) >= 3 and s[0].isalpha() and s[1] == ":" and s[2] in "\\/") and not s.startswith(("\\\\", "//")):
        return None
    return os.path.normpath(s)


def _float_in(v: Any, lo: float, hi: float) -> Optional[float]:
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return None
    x = float(v)
    return x if math.isfinite(x) and lo <= x <= hi else None


def _int_in(v: Any, lo: int, hi: int) -> Optional[int]:
    if isinstance(v, bool):
        return None
    if isinstance(v, float) and v.is_integer():
        v = int(v)
    return v if isinstance(v, int) and lo <= v <= hi else None


# ------------------------------------------------------------------------------------------------ load / save
def path_of(data_dir: Union[str, Path]) -> Path:
    return Path(data_dir) / FILE_NAME


def from_dict(raw: Any, default_lock: Optional[Callable[[], Optional[str]]] = None) -> Settings:
    """Settings from a parsed settings.json (any shape). `default_lock` answers the first-start lock setting."""
    s = Settings()
    if not isinstance(raw, dict):
        s.first_start = True
        s.recovered = raw is not None
        s.dirty = True
        _first_lock(s, default_lock)
        return s
    schema = raw.get("schema")
    if schema != SCHEMA:
        s.migrated_from = str(schema) if schema else "none"
        s.dirty = True
    s.extra = {k: v for k, v in raw.items() if k not in _KNOWN and k not in _DROPPED}
    if raw.get("report_root") is not None:
        s.report_root = valid_abs_path(raw.get("report_root"))
        if s.report_root is None:
            s.dirty = True
    s.last_program = program_code(raw.get("last_program")) or DEFAULT_PROGRAM
    sp = _float_in(raw.get("seconds_per_page_gpu"), *SECONDS_PER_PAGE_RANGE)
    if sp is not None:
        s.seconds_per_page_gpu = sp
        s.pages_measured_gpu = _int_in(raw.get("pages_measured_gpu"), 0, AVERAGE_CAP) or 0
    days = _int_in(raw.get("cache_days"), *CACHE_DAYS_RANGE)
    s.cache_days = days if days is not None else DEFAULT_CACHE_DAYS
    if "gpu_lock_path" not in raw:
        s.first_start = True
        _first_lock(s, default_lock)
    elif raw["gpu_lock_path"] is None:
        s.gpu_lock_path = None                       # the user turned it Off: never re-defaulted
    else:
        s.gpu_lock_path = valid_abs_path(raw["gpu_lock_path"])
        if s.gpu_lock_path is None:                  # damaged value: watch the default again
            _first_lock(s, default_lock)
            s.dirty = True
    return s


def _first_lock(s: Settings, default_lock: Optional[Callable[[], Optional[str]]]) -> None:
    value: Optional[str] = None
    if default_lock is not None:
        try:
            value = valid_abs_path(default_lock())
        except Exception:                            # a probe that fails must not stop the window
            value = None
    s.gpu_lock_path = value
    s.dirty = True


def load(data_dir: Union[str, Path], default_lock: Optional[Callable[[], Optional[str]]] = None) -> Settings:
    """Read <data_dir>\\settings.json. A missing file is a first start; a damaged one gives the defaults."""
    p = path_of(data_dir)
    try:
        text = p.read_text(encoding="utf-8")
    except FileNotFoundError:
        s = from_dict(None, default_lock)
        s.recovered = False
        return s
    except OSError:
        s = from_dict(None, default_lock)
        s.recovered = True
        return s
    try:
        raw = json.loads(text)
    except ValueError:
        raw = "damaged"
    s = from_dict(raw if isinstance(raw, dict) else None, default_lock)
    if not isinstance(raw, dict):
        s.recovered = True
    return s


def save(data_dir: Union[str, Path], s: Settings) -> Path:
    """Write settings.json atomically (tmp + os.replace); the data folder is made when missing."""
    p = path_of(data_dir)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(p.name + ".tmp")
    with open(tmp, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(s.to_json(), fh, ensure_ascii=False, indent=1, sort_keys=True)
        fh.write("\n")
    os.replace(tmp, p)
    s.dirty = False
    return p


# ------------------------------------------------------------------------------------------------ updates
def add_page_seconds(s: Settings, seconds: Any) -> bool:
    """Fold one check's per-page seconds (pages read on the card) into this PC's average. True when it changed."""
    xs = []
    for x in (seconds or ()):
        v = _float_in(x, *SECONDS_PER_PAGE_RANGE)
        if v is not None:
            xs.append(v)
    if not xs:
        return False
    n = min(int(s.pages_measured_gpu), AVERAGE_CAP)
    total = s.seconds_per_page_gpu * n + sum(xs)
    m = n + len(xs)
    avg = total / m
    lo, hi = SECONDS_PER_PAGE_RANGE
    s.seconds_per_page_gpu = min(hi, max(lo, avg))
    s.pages_measured_gpu = min(m, AVERAGE_CAP)
    s.dirty = True
    return True


def set_lock_path(s: Settings, path: Optional[str]) -> bool:
    """The Options row: a new lock file (Change…) or None (Off). False when the path is not absolute."""
    if path is None:
        s.gpu_lock_path = None
        s.dirty = True
        return True
    v = valid_abs_path(path)
    if v is None:
        return False
    s.gpu_lock_path = v
    s.dirty = True
    return True


def set_report_root(s: Settings, path: Optional[str]) -> bool:
    if path is None:
        s.report_root = None
        s.dirty = True
        return True
    v = valid_abs_path(path)
    if v is None:
        return False
    s.report_root = v
    s.dirty = True
    return True


def set_program(s: Settings, program: Any) -> bool:
    code = program_code(program)
    if code is None:
        return False
    if code != s.last_program:
        s.last_program = code
        s.dirty = True
    return True
