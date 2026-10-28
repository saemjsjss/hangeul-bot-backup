"""The built-in self-test (APP_SPEC rev 1 section 1.2 with revision 2 R2.3.4; Task D).

Started by the window's Self-test button (``run_selftest`` in a thread) or by ``"Hangeul Document Checker.exe"
--selftest [--report-dir DIR]`` (``main``: no window; exit 0 when every part is OK, else 1). Nine parts, in this
order: integrity, runtime, core, pdf, card, qr, excel, tk, guard. ``integrity`` runs in the calling process; every
other part runs in its own child, one at a time (the same exe with ``--selftest-part <name> <result.json>``, through
C's ``spawn.self_command``; ``run_part(name, result)`` is the child's entry), 120 s each and 900 s for ``card`` (it
may wait for the Telegram bot's document check), each child in a kill-on-close job object so it never outlives the
self-test process. A native crash of a child (an illegal instruction, a missing DLL) becomes a FAIL line with its
exit code.

Contract: C's entry calls ``main(report_dir=...) -> int`` for ``--selftest`` and ``run_part(name, result) -> int`` for
``--selftest-part``; C's boot calls ``sysinfo.cpu_flags()``; F's ``build.py check`` reads ``<DATA>\\selftest\\
selftest.json`` and writes ``build_read_record(summary)`` as ``selftest\\build_read.json``; the window may also call
``run_selftest`` in a thread (``on_progress``, ``cancel``).

The scratch folder ``<DATA>\\selftest\\Ünïcode ঠ test\\`` (spaces and non-ASCII letters, so such paths are covered
even when the user name is plain ASCII) is emptied before each run; each part works in its own subfolder. The
output is ``<report folder>\\SELFTEST <YYYY-MM-DD HHMM>.txt`` plus a copy ``<DATA>\\selftest\\SELFTEST.txt`` and the
machine-readable ``<DATA>\\selftest\\selftest.json`` (counts, codes, CERs and the READ CODE; no text, no path), from
which the build stores ``build_read.json`` (``build_read_record``). No user name, PC name or path reaches SELFTEST.txt:
every free text goes through ``sysinfo.scrub``.

The card part (R2.3.4): gpuinfo.probe and problems; a card gate on the lock-path setting; gpuengine.start_card;
gpuengine.first_read on a folder in the non-ASCII scratch folder holding the shipped selftest_pages.pdf (2 made-up
pages, page 2 turned 90 degrees) and selftest_page1.jpg, so the photo is read by ``dv.read_document`` from that
folder through ``ascii_path``; gpuengine.light_reader and one readtext of page 1 (the photo's path, again through
``ascii_path``); one recogniser call checked for the shape [1, T, 97] float32. OK when no problem was found and the
reader is on the card; the CER is at most 2% on page 1, page 2, the photo and the light reader's page 1, and page 2 is
reported sideways; the strict flags read back as set; the peak reserved card memory is at most 3.0 GB and no call ran
on the processor.

READ CODE = sha256[:8] (shown XXXX-XXXX) of the four texts above (whitespace runs made one space), in that order; it
is compared with the build PC's code and card model in ``selftest\\build_read.json``. The same card model should give
the same code; another model may differ by a few characters.

Unit tests replace the other tasks' modules through ``_OVERRIDES`` (gpuinfo, cardgate, gpuengine, places, botimport,
ingest, safety, version, spawn, and the functions named in ``_fn``). No personal data: the pages are made up.
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import html
import importlib
import json
import os
import re
import shutil
import subprocess
import sys
import time
import unicodedata
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

from . import sysinfo

PARTS: Tuple[str, ...] = ("integrity", "runtime", "core", "pdf", "card", "qr", "excel", "tk", "guard")
OK, FAIL, SKIP = "OK", "FAIL", "SKIP"
PART_TIMEOUT_S = 120
CARD_TIMEOUT_S = 900
CANCEL_GRACE_S = 10.0              # after Cancel, a child gets this long to stop by itself before it is ended
POLL_S = 0.25
SCRATCH_NAME = "Ünïcode ঠ test"    # spaces and non-ASCII letters (rev 1 1.2)
SELFTEST_DIR = "selftest"
PARTS_DIR = "parts"
ASCII_DIR = "selftest-ascii"       # <DATA>\tmp\selftest-ascii: ascii_path's copies (relative to DATA: ASCII)
REPORTS_DIR_NAME = "Hangeul Document Checks"
QR_PAYLOAD = "https://example.invalid/selftest/0001"
CER_MAX = 0.02
CAP_GB = 3.0
MEMORY_WAIT_S = 300                # the card part waits at most this long for free card memory (inside its 900 s)
REC_PROBE_SHAPE = (1, 1, 64, 256)
REC_CLASSES = 97
PDF_NAME = "selftest_pages.pdf"
JPG_NAME = "selftest_page1.jpg"
EXPECTED_NAME = "selftest_expected.json"
BUILD_READ_NAME = "build_read.json"
MANIFEST_NAME = "MANIFEST.json"
SCHEMA_PART = "hdc-selftest-part/1"
SCHEMA_SUMMARY = "hdc-selftest/1"
SCHEMA_BUILD_READ = "hdc-build-read/1"
VCRT: Tuple[Tuple[str, str], ...] = (("msvcp140", "msvcp140.dll"), ("msvcp140_1", "msvcp140_1.dll"),
                                     ("vcruntime140", "vcruntime140.dll"), ("vcruntime140_1", "vcruntime140_1.dll"),
                                     ("msvcr120", "pyzbar/msvcr120.dll"))
CUDA_DLLS: Tuple[str, ...] = ("torch_cuda.dll", "torch_cpu.dll", "cudnn64_9.dll", "cublas64_12.dll",
                              "cudart64_12.dll")
CUDA_FAMILY = re.compile(r"^(?:cublas|cudart|cudnn|cufft|curand|cusolver|cusparse|nvrtc|nvjitlink|cupti|nvtoolsext"
                         r"|caffe2_nvrtc|torch_cuda|c10_cuda)", re.IGNORECASE)
CARD_CODES = ("NO_NVIDIA_CARD", "DRIVER_TOO_OLD", "CARD_NOT_SUPPORTED", "CARD_TOO_SMALL", "LOW_CARD_MEMORY",
              "CARD_FAILED")
# Windows exit codes of a crashed child (rev 1 3.3): (what happened, the window's message code)
NTSTATUS: Dict[int, Tuple[str, str]] = {
    0xC000001D: ("illegal instruction", "CPU_UNSUPPORTED"),
    0xC0000017: ("out of memory", "LOW_MEMORY"),
    0xC0000135: ("a DLL was not found", "MISSING_PART"),
    0xC0000139: ("a DLL entry point was not found", "MISSING_PART"),
    0xC0000142: ("a DLL failed to start", "MISSING_PART"),
    0xC0000005: ("access violation", "CRASH"),
    0xC00000FD: ("stack overflow", "CRASH"),
    0xC0000409: ("fail-fast exception", "CRASH"),
    0xC000013A: ("interrupted", "CRASH"),
}

_OVERRIDES: Dict[str, Any] = {}       # unit tests: module and function replacements by name


# ===================================================================================================== results
@dataclass
class PartResult:
    part: str
    status: str = FAIL                 # OK | FAIL | SKIP
    detail: str = ""                   # one scrubbed line
    code: str = ""                     # a message code (CPU_UNSUPPORTED, NO_NVIDIA_CARD, MISSING_PART ...) or ""
    facts: Dict[str, Any] = field(default_factory=dict)      # numbers, versions, codes; never text or paths
    warnings: List[str] = field(default_factory=list)
    seconds: float = 0.0

    @property
    def ok(self) -> bool:
        return self.status == OK

    def to_json(self) -> Dict[str, Any]:
        d = asdict(self)
        d["schema"] = SCHEMA_PART
        return _jsonable(d)

    @classmethod
    def from_json(cls, d: Any) -> Optional["PartResult"]:
        if not isinstance(d, dict) or d.get("schema") != SCHEMA_PART or not isinstance(d.get("part"), str):
            return None
        status = d.get("status") if d.get("status") in (OK, FAIL, SKIP) else FAIL
        return cls(part=d["part"], status=status, detail=str(d.get("detail") or ""), code=str(d.get("code") or ""),
                   facts=d.get("facts") if isinstance(d.get("facts"), dict) else {},
                   warnings=[str(w) for w in d.get("warnings") or [] if isinstance(w, str)],
                   seconds=float(d.get("seconds") or 0.0))


class PartFailed(Exception):
    """A part's clean FAIL: ``detail`` (scrubbed later) and an optional message ``code``."""

    def __init__(self, detail: str, code: str = "", facts: Optional[Dict[str, Any]] = None):
        super().__init__(detail)
        self.detail = detail
        self.code = code
        self.facts = facts or {}


@dataclass
class Outcome:
    ok: bool
    text: str
    parts: List[PartResult]
    summary: Dict[str, Any]
    report_path: Optional[Path] = None
    copy_path: Optional[Path] = None
    cancelled: bool = False
    write_error: str = ""

    @property
    def exit_code(self) -> int:
        return 0 if self.ok else 1


def _jsonable(x: Any) -> Any:
    if isinstance(x, dict):
        return {str(k): _jsonable(v) for k, v in x.items()}
    if isinstance(x, (list, tuple)):
        return [_jsonable(v) for v in x]
    if isinstance(x, (str, bool)) or x is None:
        return x
    if isinstance(x, int):
        return int(x)
    if isinstance(x, float):
        return float(x)
    try:
        return float(x) if "float" in type(x).__name__ else int(x)
    except Exception:
        return str(x)


# ================================================================================================ dependencies
def _mod(name: str) -> Any:
    """Another task's module (lazily: the window never imports numpy or torch through this one)."""
    m = _OVERRIDES.get(name)
    if m is not None:
        return m
    if name == "gpuinfo":
        from . import gpuinfo as m
    elif name == "cardgate":
        from . import cardgate as m
    elif name == "gpuengine":
        from . import gpuengine as m
    elif name == "version":
        from . import version as m
    elif name == "spawn":
        from . import spawn as m
    elif name == "userdirs":
        from . import userdirs as m
    elif name == "places":
        from .. import places as m
    elif name == "hooks":
        from .. import hooks as m
    elif name == "botimport":
        from .. import botimport as m
    elif name == "ingest":
        from .. import ingest as m
    elif name == "safety":
        from .. import safety as m
    elif name == "model":
        from .. import model as m
    else:
        raise KeyError(name)
    return m


def _fn(name: str, default: Callable[..., Any]) -> Callable[..., Any]:
    return _OVERRIDES.get(name) or default


class _LocalCancelled(BaseException):
    """Used only when the runner's hooks module cannot be imported."""


def _cancelled_type() -> type:
    try:
        return _mod("hooks").Cancelled
    except Exception:
        return _LocalCancelled


def _no_card_env() -> bool:
    return bool(os.environ.get("HDC_TEST_NO_CARD", ""))


def _frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


def _data_dir() -> Path:
    try:
        return Path(_mod("places").app_data_dir())
    except Exception:
        v = os.environ.get("HDC_DATA_DIR", "").strip()
        if v:
            return Path(os.path.abspath(v))
        base = os.environ.get("LOCALAPPDATA") or os.path.join(os.path.expanduser("~"), "AppData", "Local")
        return Path(base) / "Hangeul Document Checker"


def _bundle_dir() -> Optional[Path]:
    try:
        return Path(_mod("places").bundle_dir())
    except Exception:
        b = getattr(sys, "_MEIPASS", None)
        return Path(b) if b else None


def _app_dir() -> Optional[Path]:
    try:
        return Path(_mod("places").app_install_dir())
    except Exception:
        return Path(os.path.dirname(sys.executable)) if _frozen() else None


def _read_json(p: Optional[Path]) -> Any:
    if p is None:
        return None
    try:
        return json.loads(Path(p).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _write_json(p: Path, data: Any) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(p.name + ".tmp")
    tmp.write_text(json.dumps(_jsonable(data), indent=1, ensure_ascii=True), encoding="ascii")
    os.replace(tmp, p)


def _under(path: Any, root: Any) -> bool:
    if path is None or root is None:
        return False
    try:
        p = os.path.normcase(os.path.abspath(str(path)))
        r = os.path.normcase(os.path.abspath(str(root))).rstrip("\\/")
    except (TypeError, ValueError):
        return False
    return p == r or p.startswith(r + os.sep) or p.startswith(r + "/")


def _arch_flags() -> Any:
    try:
        flags = getattr(_mod("version"), "ARCH_FLAGS", None)
        return list(flags) if flags else None
    except Exception:
        return None


def app_versions() -> Dict[str, str]:
    """App version, build id, rules and runner versions, from C's version module (BUILD.json), else the runner's
    model constants."""
    out = {"app_version": "?", "build_id": "dev", "base_rules": "?", "runner_rules": "?", "runner": "?"}
    try:
        m = _mod("model")
        out.update(base_rules=m.BASE_RULES_VERSION, runner_rules=m.RUNNER_RULES_VERSION, runner=m.RUNNER_VERSION)
    except Exception:
        pass
    try:
        v = _mod("version")
        for key, attr in (("app_version", "APP_VERSION"), ("build_id", "BUILD_ID"),
                          ("base_rules", "BASE_RULES_VERSION"), ("runner_rules", "RUNNER_RULES_VERSION"),
                          ("runner", "RUNNER_VERSION")):
            val = getattr(v, attr, None)
            if val:
                out[key] = str(val)
    except Exception:
        pass
    return out


def lock_setting(data_dir: Path) -> List[str]:
    """The lock files the card part waits for: settings.json "gpu_lock_path" (a string, or null = Off); when the
    file or the key is missing, cardgate.default_lock_setting() (the bot's lock when its folder exists)."""
    d = _read_json(Path(data_dir) / "settings.json")
    if isinstance(d, dict) and "gpu_lock_path" in d:
        v = d.get("gpu_lock_path")
        return [v] if isinstance(v, str) and v.strip() else []
    try:
        v = _mod("cardgate").default_lock_setting()
    except Exception:
        v = None
    return [v] if v else []


def default_report_dir(data_dir: Path) -> Path:
    """settings.json "report_root" when the user chose one, else B's default (Documents\\Hangeul Document Checks,
    or the home folder when Documents is synced by OneDrive)."""
    d = _read_json(Path(data_dir) / "settings.json")
    if isinstance(d, dict) and isinstance(d.get("report_root"), str) and d["report_root"].strip():
        return Path(d["report_root"])
    try:
        return Path(_mod("userdirs").default_report_root())
    except Exception:
        return sysinfo.documents_dir() / REPORTS_DIR_NAME


# ============================================================================================ text measures
def cer_norm(s: Any) -> str:
    """Upper-case ASCII letters and digits only: spacing and punctuation do not count in the CER."""
    t = unicodedata.normalize("NFKC", str(s or "")).upper()
    return "".join(ch for ch in t if ch.isascii() and ch.isalnum())


def levenshtein(a: str, b: str) -> int:
    if a == b:
        return 0
    if len(a) < len(b):
        a, b = b, a
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]


def cer(read: Optional[str], expected: str) -> Optional[float]:
    """Character error rate of ``read`` against ``expected`` (both cer_norm'ed); None when nothing was read."""
    if read is None:
        return None
    a, b = cer_norm(read), cer_norm(expected)
    if not b:
        return 0.0 if not a else 1.0
    return levenshtein(a, b) / float(len(b))


def read_norm(s: Any) -> str:
    return " ".join(unicodedata.normalize("NFC", str(s or "")).split())


def read_code(texts: Sequence[Optional[str]]) -> str:
    """sha256[:8] of the normalised texts joined by newlines, as XXXX-XXXX."""
    h = hashlib.sha256("\n".join(read_norm(t) for t in texts).encode("utf-8")).hexdigest()
    return f"{h[:4]}-{h[4:8]}".upper()


def pct(x: Optional[float]) -> str:
    return "?" if x is None else f"{x * 100:.1f}%"


def exit_text(rc: int) -> str:
    u = int(rc) & 0xFFFFFFFF
    if u >= 0xC0000000:
        return f"0x{u:08X} ({NTSTATUS.get(u, ('a Windows error', 'CRASH'))[0]})"
    return f"exit code {rc}"


def exit_code_class(rc: int) -> str:
    u = int(rc) & 0xFFFFFFFF
    return NTSTATUS.get(u, ("", "CRASH"))[1] if u >= 0xC0000000 else "CRASH"


# =========================================================================================== the child side
@dataclass
class Context:
    """What a part child works with. ``scratch`` is the part's own folder inside the scratch folder ``root``."""
    part: str
    root: Path
    scratch: Path
    data_dir: Path
    bundle: Optional[Path]
    app_dir: Optional[Path]
    lock_paths: List[str] = field(default_factory=list)
    events: Optional[Path] = None
    cancel_file: Optional[Path] = None

    def selftest_file(self, name: str) -> Path:
        if self.bundle is None:
            raise PartFailed(f"the self-test files are not there ({name}): not a built app", "MISSING_PART")
        p = self.bundle / SELFTEST_DIR / name
        if not p.is_file():
            raise PartFailed(f"{SELFTEST_DIR}/{name} is missing from the app", "MISSING_PART")
        return p

    def emit(self, event: str, **data: Any) -> None:
        """The parts' event listener: Cancel (the parent's cancel file) raises hooks.Cancelled here; every event is
        appended to the events file the parent reads. Events hold counts and codes only."""
        if self.cancel_file is not None and self.cancel_file.exists():
            raise _cancelled_type()()
        if self.events is not None:
            try:
                with open(self.events, "a", encoding="ascii") as f:
                    f.write(json.dumps(_jsonable({"ev": event, **data}), ensure_ascii=True) + "\n")
            except OSError:
                pass


def make_context(name: str, result_path: Path) -> Context:
    """The child's Context from ``<result>.in.json`` (written by the parent); defaults when it is missing."""
    result_path = Path(result_path)
    inp = _read_json(result_path.with_name(result_path.stem + ".in.json"))
    inp = inp if isinstance(inp, dict) else {}
    root = Path(inp["root"]) if inp.get("root") else result_path.parent.parent
    data_dir = Path(inp["data_dir"]) if inp.get("data_dir") else _data_dir()
    bundle = Path(inp["bundle"]) if inp.get("bundle") else _bundle_dir()
    app_dir = Path(inp["app_dir"]) if inp.get("app_dir") else _app_dir()
    locks = inp.get("lock_paths")
    locks = [str(p) for p in locks if p] if isinstance(locks, list) else lock_setting(data_dir)
    scratch = root / name
    scratch.mkdir(parents=True, exist_ok=True)
    return Context(part=name, root=root, scratch=scratch, data_dir=data_dir, bundle=bundle, app_dir=app_dir,
                   lock_paths=locks, events=Path(inp["events"]) if inp.get("events") else None,
                   cancel_file=Path(inp["cancel"]) if inp.get("cancel") else None)


def execute_part(name: str, ctx: Context) -> PartResult:
    """One part, every exception turned into a FAIL result (scrubbed). hooks.Cancelled becomes "cancelled"."""
    t0 = time.perf_counter()
    cancelled = _cancelled_type()
    fn = PART_FUNCS.get(name)
    try:
        if fn is None:
            raise PartFailed(f"unknown part {name!r}")
        res = fn(ctx)
    except PartFailed as e:
        res = PartResult(name, FAIL, e.detail, e.code, facts=dict(e.facts))
    except cancelled:
        res = PartResult(name, FAIL, "cancelled")
    except _LocalCancelled:
        res = PartResult(name, FAIL, "cancelled")
    except Exception as e:
        res = PartResult(name, FAIL, f"{type(e).__name__}: {e}")
    res.part = name
    res.detail = _one_line(sysinfo.scrub(res.detail))
    res.warnings = [_one_line(sysinfo.scrub(w)) for w in res.warnings]
    res.seconds = round(time.perf_counter() - t0, 2)
    return res


def run_part(name: str, result_path: Any = None) -> int:
    """The child's entry, ``--selftest-part <name> [<result.json>]`` (C's entry calls it): run the part and write
    its result (always, also on a FAIL). Without a result path it goes to <DATA>\\selftest\\part-<name>.json.
    Exit 0 when the result was written, 1 otherwise."""
    name = str(name)
    if result_path is None:
        result_path = _data_dir() / SELFTEST_DIR / f"part-{re.sub(r'[^A-Za-z0-9_-]', '_', name)}.json"
    result_path = Path(result_path)
    old_cwd = os.getcwd()
    try:
        if name == "imports":             # build.py check's smoke: no scratch folder is made next to the result
            ctx = Context(part=name, root=result_path.parent, scratch=result_path.parent, data_dir=_data_dir(),
                          bundle=_bundle_dir(), app_dir=_app_dir())
        else:
            ctx = make_context(name, result_path)
        if name == "card" and ctx.data_dir.is_dir():
            os.chdir(ctx.data_dir)        # boot does this too: ascii_path's copies are then ASCII relative paths
        res = execute_part(name, ctx)
    except Exception as e:
        res = PartResult(name, FAIL, _one_line(sysinfo.scrub(f"{type(e).__name__}: {e}")))
    finally:
        try:
            os.chdir(old_cwd)
        except OSError:
            pass
    try:
        _write_json(result_path, res.to_json())
    except OSError:
        return 1
    return 0


part_main = run_part


def _one_line(s: str, limit: int = 400) -> str:
    s = " ".join(str(s).split())
    return s if len(s) <= limit else s[:limit - 3] + "..."


# ------------------------------------------------------------------------------------------------- integrity
def manifest_path(app_dir: Optional[Path], bundle: Optional[Path]) -> Optional[Path]:
    for p in ((bundle / MANIFEST_NAME) if bundle else None,
              (app_dir / "_internal" / MANIFEST_NAME) if app_dir else None):
        if p is not None and p.is_file():
            return p
    return None


def _sha256(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def part_integrity(app_dir: Optional[Path], bundle: Optional[Path], *, full: bool = True,
                   progress: Optional[Callable[[int, int], None]] = None) -> PartResult:
    """Every file of the app folder against MANIFEST.json (F's hdc-manifest/1: paths relative to the app folder,
    "/" separators, size and sha256): sizes always, sha256 too (full). Extra files (an installer's uninstaller) are
    not judged. A FAIL names up to three files, relative to the app folder."""
    mp = manifest_path(app_dir, bundle)
    if mp is None or app_dir is None:
        if _frozen():
            return PartResult("integrity", FAIL, "MANIFEST.json is missing", "MISSING_PART")
        return PartResult("integrity", SKIP, "not a built app: no MANIFEST.json")
    data = _read_json(mp)
    files = data.get("files") if isinstance(data, dict) else None
    if isinstance(files, list):
        files = {str(e.get("path")): e for e in files if isinstance(e, dict) and e.get("path")}
    if not isinstance(files, dict) or not files:
        return PartResult("integrity", FAIL, "MANIFEST.json is damaged", "MISSING_PART")
    missing: List[str] = []
    changed: List[str] = []
    names = sorted(files, key=str.lower)
    for i, rel in enumerate(names, 1):
        meta = files[rel] if isinstance(files[rel], dict) else {}
        parts = rel.replace("\\", "/").split("/")
        if not rel or rel.startswith("/") or ":" in rel or ".." in parts:
            changed.append(rel)
            continue
        p = app_dir.joinpath(*parts)
        try:
            st = p.stat()
        except OSError:
            missing.append(rel)
            continue
        if st.st_size != meta.get("size"):
            changed.append(rel)
        elif full and str(meta.get("sha256", "")).lower() != _sha256(p):
            changed.append(rel)
        if progress is not None and (i % 100 == 0 or i == len(names)):
            progress(i, len(names))
    detail = f"{len(names)} files, {len(missing)} missing, {len(changed)} changed"
    if missing or changed:
        named = [f"{r} (missing)" for r in missing[:3]] + [f"{r} (changed)" for r in changed[:max(0, 3 - len(missing))]]
        return PartResult("integrity", FAIL, detail + ": " + ", ".join(named), "MISSING_PART",
                          facts={"files": len(names), "missing": len(missing), "changed": len(changed)})
    return PartResult("integrity", OK, detail, facts={"files": len(names), "missing": 0, "changed": 0})


# --------------------------------------------------------------------------------------------------- runtime
def _load_dll(path: str) -> Any:
    import ctypes
    return ctypes.WinDLL(path)


def _import_torch_plain() -> Any:
    if _no_card_env():
        raise RuntimeError("torch is off for this test run")
    import torch
    return torch


def _where(path: str, app_dir: Optional[Path], windir: str) -> str:
    if _under(path, app_dir):
        return "app"
    if _under(path, windir):
        return "Windows"
    return "other"


def part_runtime(ctx: Context) -> PartResult:
    """The bundled VC++ DLLs loaded by full path, and whether the bundled copies are the ones in the process; torch
    imported (no CUDA work) under the worker's environment, then where torch_cuda, torch_cpu, cudnn64_9, cublas64_12
    and cudart64_12 were loaded from (they must be in the app folder; any CUDA library from elsewhere is a FAIL); and
    nvml.dll, which must come from Windows. Paths are judged, never reported."""
    app = ctx.app_dir or ctx.bundle
    windir = os.environ.get("SystemRoot") or os.environ.get("WINDIR") or r"C:\Windows"
    load = _fn("load_dll", _load_dll)
    problems: List[str] = []
    for stem, rel in VCRT:
        full = ctx.bundle.joinpath(*rel.split("/")) if ctx.bundle else None
        if full is None or not full.is_file():
            problems.append(f"{stem}.dll is missing from the app")
            continue
        try:
            load(str(full))
        except OSError as e:
            problems.append(f"{stem}.dll could not be loaded ({type(e).__name__})")
    torch_ver = None
    try:
        gi = _mod("gpuinfo")
        _fn("apply_card_env", gi.apply_card_env)(os.environ, 0)     # the worker's environment (CUDA_PATH* gone)
        nvml = gi.NVML_DLL
    except Exception:
        nvml = os.path.join(windir, "System32", "nvml.dll")
    try:
        torch = _fn("import_torch", _import_torch_plain)()
        torch_ver = str(getattr(torch, "__version__", "?"))
    except Exception as e:
        problems.append(f"torch could not be imported ({type(e).__name__})")
    if "load_dll" in _OVERRIDES or os.path.isfile(nvml):
        try:
            load(nvml)
        except OSError:
            pass                                        # reported below as "not loaded"
    index: Dict[str, List[str]] = {}
    for p in _fn("loaded_modules", sysinfo.loaded_modules)():
        index.setdefault(os.path.basename(str(p)).lower(), []).append(str(p))
    version_of = _fn("file_version", sysinfo.file_version)
    facts: Dict[str, Any] = {"vcrt": {}, "cuda": {}, "nvml": {}, "torch": torch_ver}
    vc_text = []
    for stem, rel in VCRT:
        paths = index.get(os.path.basename(rel).lower(), [])
        if not paths:
            if not any(x.startswith(f"{stem}.dll") for x in problems):
                problems.append(f"{stem}.dll is not loaded")
            continue
        wheres = sorted({_where(p, app, windir) for p in paths})
        ver = sysinfo.short_version(version_of(paths[0]), 2)
        others = [w for w in wheres if w != "app"]
        label = "bundled" if not others else " and ".join((["bundled"] if "app" in wheres else []) + others)
        facts["vcrt"][stem] = {"where": label, "version": ver}
        if stem in ("msvcp140", "vcruntime140", "msvcr120"):
            vc_text.append(f"{stem} {ver} ({label})")
        if others:
            problems.append(f"{stem}.dll: a copy from {' and '.join(others)} is in use")
    cuda_ok = torch_ver is not None
    for name in CUDA_DLLS:
        stem = name[:-4]
        paths = index.get(name, [])
        if not paths:
            problems.append(f"{stem} is not loaded")
            cuda_ok = False
            continue
        wheres = sorted({_where(p, app, windir) for p in paths})
        facts["cuda"][stem] = {"where": "app" if wheres == ["app"] else "+".join(wheres),
                               "version": version_of(paths[0])}
        if wheres != ["app"]:
            problems.append(f"{stem} was loaded from outside the app folder")
            cuda_ok = False
    for base, paths in sorted(index.items()):
        if CUDA_FAMILY.match(base) and base not in CUDA_DLLS:
            if any(_where(p, app, windir) != "app" for p in paths):
                problems.append(f"{base[:-4] if base.endswith('.dll') else base} was loaded from outside the app folder")
                cuda_ok = False
    npaths = index.get("nvml.dll", [])
    if not npaths:
        facts["nvml"] = {"where": "absent"}
        nvml_text = "nvml.dll not present (no NVIDIA driver)"
    else:
        wheres = sorted({_where(p, app, windir) for p in npaths})
        nver = version_of(npaths[0]) or "?"
        facts["nvml"] = {"where": "+".join(wheres), "version": nver}
        nvml_text = f"nvml.dll {nver} ({'Windows' if wheres == ['Windows'] else '+'.join(wheres)})"
        if wheres != ["Windows"]:
            problems.append("nvml.dll was not loaded from Windows")
    cudnn = sysinfo.short_version((facts["cuda"].get("cudnn64_9") or {}).get("version"), 3)
    cuda_text = (f"torch {torch_ver}: CUDA DLLs from the app folder (cuDNN {cudnn})" if cuda_ok
                 else f"torch {torch_ver or '?'}")
    detail = ", ".join(vc_text) + " | " + cuda_text + " | " + nvml_text
    if problems:
        return PartResult("runtime", FAIL, "; ".join(problems[:4]) + " | " + detail, "MISSING_PART", facts=facts)
    return PartResult("runtime", OK, detail, facts=facts)


# ------------------------------------------------------------------------------------------------------ core
def _numpy_cpu(np: Any) -> Tuple[Dict[str, bool], str]:
    m = None
    for path in ("numpy._core._multiarray_umath", "numpy.core._multiarray_umath"):
        try:
            m = importlib.import_module(path)
            break
        except Exception:
            continue
    feats = getattr(m, "__cpu_features__", {}) if m is not None else {}
    out = {k: bool(feats[k]) for k in ("SSE42", "POPCNT", "AVX", "AVX2", "AVX512F", "FMA3") if k in feats}
    base = list(getattr(m, "__cpu_baseline__", []) or []) if m is not None else []
    return out, (base[0] if len(base) == 1 else (base[-1] if base else "none"))


def part_core(ctx: Context) -> PartResult:
    """numpy, cv2 and PIL import; numpy's CPU features. A numpy baseline error is CPU_UNSUPPORTED."""
    try:
        import numpy as np
    except Exception as e:
        msg = str(e).lower()
        code = "CPU_UNSUPPORTED" if any(w in msg for w in ("baseline", "cpu", "instruction")) else ""
        raise PartFailed(f"numpy could not be imported ({type(e).__name__}: {e})", code)
    import cv2
    import PIL
    feats, base = _numpy_cpu(np)
    facts = {"numpy_features": feats, "numpy": np.__version__, "opencv": cv2.__version__,
             "pillow": PIL.__version__, "baseline": base}
    return PartResult("core", OK, f"numpy {np.__version__} (baseline {base}), opencv {cv2.__version__}, "
                                  f"pillow {PIL.__version__}", facts=facts)


# ------------------------------------------------------------------------------------------------------- pdf
TEXT_PDF_LINE = "Self-test text PDF \u2014 made up \u2026 \u09a0"      # "—", "…" and one Bengali letter


def _pymupdf() -> Any:
    try:
        import pymupdf
    except ImportError:
        import fitz as pymupdf
    return pymupdf


def part_pdf(ctx: Context) -> PartResult:
    """PyMuPDF: open the shipped 2-page PDF, render page 1 at 200 dpi, save a one-page cut into the scratch folder
    (a native write to a non-ASCII path), write a text PDF with insert_htmlbox ("—", "…", a Bengali letter) and read
    it back."""
    pm = _pymupdf()
    src = ctx.selftest_file(PDF_NAME)
    exp = _read_json(ctx.bundle / SELFTEST_DIR / EXPECTED_NAME) if ctx.bundle else None
    problems: List[str] = []
    cut = ctx.scratch / "cut \u09a0 page 1.pdf"
    with pm.open(str(src)) as doc:
        n = len(doc)
        if n != 2:
            problems.append(f"the self-test PDF has {n} pages, not 2")
        pix = doc[0].get_pixmap(dpi=200)
        size = [pix.width, pix.height]
        want = None
        if isinstance(exp, dict):
            want = (exp.get("page_px") or {}).get("1")
        if not want:
            r = doc[0].rect
            want = [int(round(r.width * 200 / 72.0)), int(round(r.height * 200 / 72.0))]
        if abs(size[0] - int(want[0])) > 2 or abs(size[1] - int(want[1])) > 2:
            problems.append(f"page 1 rendered at {size[0]}x{size[1]}, not {want[0]}x{want[1]}")
        d = pm.open()
        d.insert_pdf(doc, from_page=0, to_page=0)
        d.save(str(cut))
        d.close()
    with pm.open(str(cut)) as c:
        if len(c) != 1:
            problems.append("the one-page cut does not have one page")
    tp = ctx.scratch / "text.pdf"
    d = pm.open()
    page = d.new_page()
    page.insert_htmlbox(pm.Rect(50, 50, 545, 400), f"<p>{html.escape(TEXT_PDF_LINE)}</p>")
    d.save(str(tp))
    d.close()
    with pm.open(str(tp)) as t:
        got = " ".join(t[0].get_text().split())
    if got != TEXT_PDF_LINE:
        lost = [name for ch, name in (("\u2014", "dash"), ("\u2026", "ellipsis"), ("\u09a0", "Bengali letter"))
                if ch not in got]
        problems.append("the text PDF read back differently" + (f" (lost: {', '.join(lost)})" if lost else ""))
    ver = str(getattr(pm, "VersionBind", getattr(pm, "__version__", "?")))
    facts = {"pymupdf": ver, "render_px": size}
    if problems:
        return PartResult("pdf", FAIL, "; ".join(problems), facts=facts)
    return PartResult("pdf", OK, f"PyMuPDF {ver}: open, render, save, text PDF", facts=facts)


# -------------------------------------------------------------------------------------------------------- qr
def part_qr(ctx: Context) -> PartResult:
    """A made-up payload encoded with cv2.QRCodeEncoder, decoded by zbar (pyzbar) and by cv2.QRCodeDetector."""
    import cv2
    import numpy as np
    qr = np.asarray(cv2.QRCodeEncoder.create().encode(QR_PAYLOAD), dtype=np.uint8)
    if qr.ndim == 3:
        qr = cv2.cvtColor(qr, cv2.COLOR_BGR2GRAY)
    img = cv2.resize(qr, None, fx=8, fy=8, interpolation=cv2.INTER_NEAREST)
    img = cv2.copyMakeBorder(img, 40, 40, 40, 40, cv2.BORDER_CONSTANT, value=255)
    from pyzbar import pyzbar
    zbar = [r.data.decode("utf-8", "replace") for r in pyzbar.decode(img)]
    text, _points, _straight = cv2.QRCodeDetector().detectAndDecode(img)
    bad = []
    if QR_PAYLOAD not in zbar:
        bad.append("zbar")
    if text != QR_PAYLOAD:
        bad.append("OpenCV")
    if bad:
        return PartResult("qr", FAIL, f"{' and '.join(bad)} did not decode the made-up code")
    return PartResult("qr", OK, "zbar and OpenCV both decoded the made-up code")


# ----------------------------------------------------------------------------------------------------- excel
EXCEL_SHEETS: Dict[str, List[List[Any]]] = {
    "Summary": [["Item", "Value"], ["overall", "PASS"], ["code", "0000-0000"], ["text", "made up \u2014 sample \u2026"]],
    "Documents": [["Document", "Pages", "Verdict"], ["SAMPLE DOCUMENT", "1-2", "PASS"]],
    "Pages": [["Page", "Chars", "Seconds"], [1, 300, 1.5], [2, 320, 2.25]],
    "Findings": [["Rule", "Verdict"], ["sample.rule", "NOTE"]],
}


def part_excel(ctx: Context) -> PartResult:
    """A made-up workbook (sheets Summary, Documents, Pages, Findings) written with openpyxl into the scratch
    folder, opened again, and its cells compared."""
    import openpyxl
    path = ctx.scratch / "selftest \u09a0.xlsx"
    wb = openpyxl.Workbook()
    for i, (name, rows) in enumerate(EXCEL_SHEETS.items()):
        ws = wb.active if i == 0 else wb.create_sheet()
        ws.title = name
        for r in rows:
            ws.append(r)
    wb.save(str(path))
    wb2 = openpyxl.load_workbook(str(path))
    try:
        for name, rows in EXCEL_SHEETS.items():
            got = [list(r) for r in wb2[name].iter_rows(values_only=True)]
            if got != rows:
                return PartResult("excel", FAIL, f"sheet {name} read back differently")
    finally:
        wb2.close()
    return PartResult("excel", OK, f"openpyxl {openpyxl.__version__}: written and read back")


# -------------------------------------------------------------------------------------------------------- tk
def part_tk(ctx: Context) -> PartResult:
    """A hidden Tk root with one label: update, destroy; the Tcl/Tk version."""
    import tkinter as tk
    root = tk.Tk()
    try:
        root.withdraw()
        tk.Label(root, text="self-test").pack()
        root.update_idletasks()
        root.update()
    finally:
        root.destroy()
    return PartResult("tk", OK, f"Tcl/Tk {tk.TkVersion}")


# ----------------------------------------------------------------------------------------------------- guard
def _probe_network() -> str:
    import socket
    try:
        s = socket.create_connection(("127.0.0.1", 9), timeout=2)
        s.close()
        return "connected"
    except Exception as e:
        return "guard" if "guard" in str(e).lower() else f"not by the guard ({type(e).__name__})"


def _probe_write(target: Path) -> str:
    existed = target.exists()
    try:
        with open(target, "w", encoding="ascii") as f:
            f.write("x")
        result = "written"
    except Exception as e:
        result = "guard" if "guard" in str(e).lower() else f"not by the guard ({type(e).__name__})"
    if not existed and target.exists():
        try:
            target.unlink()
        except OSError:
            pass
        if result == "guard":
            result = "created"
    return result


def part_guard(ctx: Context) -> PartResult:
    """The APP guard armed (safety.prepare, as the worker arms it); then a connection to 127.0.0.1:9 and a write to
    %WINDIR%\\hdc-guard-probe.txt. Both must be refused by the guard before Windows is asked (nothing created)."""
    safety = _mod("safety")
    places = _mod("places")
    inp = ctx.scratch / "guard input.txt"
    inp.write_text("made-up guard probe input\n", encoding="ascii")
    out = ctx.scratch / "guard out"
    safety.prepare(out, places.current().cache_root, inp)
    state = safety.armed() if callable(getattr(safety, "armed", None)) else None
    before = int(getattr(state, "blocked_total", 0) or 0)
    target = Path(os.environ.get("SystemRoot") or os.environ.get("WINDIR") or r"C:\Windows") / "hdc-guard-probe.txt"
    net = _fn("probe_network", _probe_network)()
    write = _fn("probe_write", _probe_write)(target)
    after = int(getattr(state, "blocked_total", 0) or 0)
    problems = []
    if net != "guard":
        problems.append(f"network {net}")
    if write != "guard":
        problems.append(f"outside write {write}")
    if state is not None and after - before < 2:
        problems.append("the guard recorded fewer than 2 refusals")
    if problems:
        return PartResult("guard", FAIL, "; ".join(problems))
    return PartResult("guard", OK, "network refused, outside write refused")


# ------------------------------------------------------------------------------------------------------ card
class CountingGate:
    """The card gate with a log of every ``wait_turn(where)`` (to count the reader's passes per page). Attributes
    are the gate's (``on_pause`` set through this wrapper lands on the gate)."""

    def __init__(self, gate: Any) -> None:
        object.__setattr__(self, "_gate", gate)
        object.__setattr__(self, "turns", [])

    def wait_turn(self, where: str) -> None:
        self.turns.append(str(where))
        return self._gate.wait_turn(where)

    def __getattr__(self, name: str) -> Any:
        return getattr(object.__getattribute__(self, "_gate"), name)

    def __setattr__(self, name: str, value: Any) -> None:
        setattr(self._gate, name, value)

    def passes(self) -> List[int]:
        """The reader's calls ("read") after each page checkpoint ("page"), in order."""
        out: List[int] = []
        for w in self.turns:
            if w == "page":
                out.append(0)
            elif w == "read" and out:
                out[-1] += 1
        return out


CARD_REASONS: Dict[str, str] = {
    "NO_NVIDIA_CARD": "no NVIDIA graphics card or driver was found",
    "DRIVER_TOO_OLD": "the NVIDIA driver {driver} is too old (it must support CUDA 12.8: driver 570 or newer)",
    "CARD_NOT_SUPPORTED": "the graphics card {name} is not supported by this build ({which})",
    "CARD_TOO_SMALL": "the graphics card {name} has {total} GB; 6 GB or more is needed",
    "LOW_CARD_MEMORY": "only {free} GB of the graphics card's memory was free; 4.0 GB is needed",
    "CARD_FAILED": "the graphics card failed",
}


def _reason(code: str, values: Mapping[str, Any]) -> str:
    text = CARD_REASONS.get(code, "")
    if not text:
        return ""
    vals = {k: "?" for k in ("driver", "name", "which", "total", "free")}
    vals.update({str(k): str(v) for k, v in dict(values or {}).items()})
    if code == "CARD_NOT_SUPPORTED":
        vals["which"] = {"old": "too old", "new": "newer than this build"}.get(vals["which"], vals["which"])
    try:
        return text.format(**vals)
    except (KeyError, ValueError):
        return text


def _card_fail(code: str, values: Mapping[str, Any], facts: Dict[str, Any], stage: str = "",
               why: str = "") -> PartResult:
    detail = code or "ERROR"
    reason = _reason(code, values)
    if reason:
        detail += f": {reason}"
    if stage:
        detail += f" (step '{stage}')"
    if why:
        detail += f": {why}"
    facts = dict(facts)
    facts["code"] = code
    return PartResult("card", FAIL, detail, code, facts=facts)


def _store_pages(store: Path) -> Dict[Tuple[str, int], Dict[str, Any]]:
    d = _read_json(store)
    out: Dict[Tuple[str, int], Dict[str, Any]] = {}
    if isinstance(d, dict):
        for e in d.get("files") or []:
            if isinstance(e, dict):
                for p in e.get("pages") or []:
                    if isinstance(p, dict) and isinstance(p.get("page"), int):
                        out[(str(e.get("name")), int(p["page"]))] = p
    return out


def _cudnn_version() -> str:
    for p in _fn("loaded_modules", sysinfo.loaded_modules)():
        if os.path.basename(str(p)).lower() == "cudnn64_9.dll":
            v = _fn("file_version", sysinfo.file_version)(p)
            if v:
                return sysinfo.short_version(v, 3)
    try:
        return str(getattr(_mod("version"), "CUDNN", "") or "?")
    except Exception:
        return "?"


def _device_text(card: Any) -> str:
    try:
        return f"cuda:{int(card.torch.cuda.current_device())}"
    except Exception:
        return "cuda"


def build_read(bundle: Optional[Path]) -> Dict[str, Any]:
    d = _read_json(bundle / SELFTEST_DIR / BUILD_READ_NAME) if bundle else None
    return d if isinstance(d, dict) else {}


def compare_text(code: str, card_name: str, build: Mapping[str, Any]) -> Tuple[str, Optional[str]]:
    """The READ CODE's bracket, and a warning when the same card model read differently."""
    b_code, b_card = str(build.get("read_code") or ""), str(build.get("card") or "")
    if not b_code:
        return "(no build PC code in this build)", None
    if b_card and b_card == card_name:
        warn = None if b_code == code else ("the READ CODE differs from the build PC's on the same card model: "
                                            "send SELFTEST.txt")
        return f"(build PC, same card model: {b_code})", warn
    return f"(build PC, {b_card or 'another card'}: {b_code}; another card model may differ)", None


def part_card(ctx: Context) -> PartResult:
    """R2.3.4's card part (see the module text)."""
    gi = _mod("gpuinfo")
    info = gi.probe()
    facts: Dict[str, Any] = {"card_name": str(getattr(info, "name", "") or "")}
    probs = gi.problems(info, _arch_flags())
    if probs:
        return _card_fail(probs[0].code, probs[0].values, facts, "probe")
    if _no_card_env() and "gpuengine" not in _OVERRIDES:
        return _card_fail("CARD_FAILED", {}, facts, "start", "the graphics card is off for this test run")
    shipped_pdf, shipped_jpg = ctx.selftest_file(PDF_NAME), ctx.selftest_file(JPG_NAME)   # before any card work
    expected = _read_json(ctx.selftest_file(EXPECTED_NAME))
    if not isinstance(expected, dict) or not isinstance(expected.get("pages"), dict):
        raise PartFailed(f"{SELFTEST_DIR}/{EXPECTED_NAME} is damaged", "MISSING_PART")
    cg, ge, places = _mod("cardgate"), _mod("gpuengine"), _mod("places")
    gate = CountingGate(cg.CardGate(ctx.lock_paths, ctx.emit, free_mb=lambda: gi.free_mb(int(info.index))))
    cancelled = _cancelled_type()
    stage = "start"
    m: Dict[str, Any] = {}
    try:
        gate.wait_turn("start")
        gate.wait_memory(gi.NEED_FREE_MB, MEMORY_WAIT_S)
        card = ge.start_card(info, gate)
        cf = card.facts() if callable(getattr(card, "facts", None)) else {}
        facts.update(torch=str(cf.get("torch", "?")), cuda=str(cf.get("cuda", "?")))
        m["torch_s"], m["cuda_s"] = float(cf.get("import_s") or 0.0), float(cf.get("cuda_s") or 0.0)
        stage = "firstread"
        inp = ctx.scratch / "input"
        inp.mkdir(parents=True, exist_ok=True)
        pdf, jpg = inp / PDF_NAME, inp / JPG_NAME
        shutil.copyfile(shipped_pdf, pdf)
        shutil.copyfile(shipped_jpg, jpg)
        bot = _mod("botimport").load(ctx.root / "cache")
        src = _mod("ingest").open_input(inp)
        store = ctx.scratch / "pagetexts" / "selftest.json"
        ascii_dir = str(ctx.data_dir / "tmp" / ASCII_DIR)
        fr = ge.first_read(card, bot, src, gate, store, ctx.emit, ascii_dir=ascii_dir)
        pages = _store_pages(store)
        items = [(f.name, p) for f in src.files for p in range(1, int(f.n_pages) + 1)
                 if (pages.get((f.name, p)) or {}).get("kind") in ("ocr", "image")]
        counts = gate.passes()
        passes = dict(zip(items, counts)) if len(items) == len(counts) else {}
        stage = "light reader"
        t0 = time.perf_counter()
        reader = ge.light_reader(card, gate, places.current().glue_data, places.current().models_dir,
                                 ascii_dir=ascii_dir)
        m["light_load_s"] = time.perf_counter() - t0
        t0 = time.perf_counter()
        light_text = " ".join(str(t) for t in reader.readtext(str(jpg), detail=0))
        m["light_s"] = time.perf_counter() - t0
        import numpy as np
        rec_out = reader.rec.run(None, {"image": np.zeros(REC_PROBE_SHAPE, dtype=np.float32)})
        flags = ge.torch_flags(card.torch)
        alloc_gb, reserved_gb = card.peaks()
        rstats = reader.stats() if callable(getattr(reader, "stats", None)) else {}
        light_device = str(getattr(getattr(reader, "det", None), "device", "") or "")
        device = _device_text(card)
    except cg.CardMemoryShort as e:
        facts.update(_gate_facts(gate))
        return _card_fail("LOW_CARD_MEMORY", {"free": gi.gb_text(e.free_mb)}, facts, stage)
    except cancelled:
        raise
    except PartFailed:
        raise
    except Exception as e:
        facts.update(_gate_facts(gate))
        code = None
        try:
            code = ge.card_error_code(e)
        except Exception:
            pass
        values = getattr(e, "values", {}) if code == getattr(e, "code", None) else {}
        return _card_fail(code or "", values, facts, stage,
                          "" if type(e).__name__ == "CardRefused" else f"{type(e).__name__}: {e}")

    pg = expected["pages"]
    exp1, exp2 = " ".join(pg.get("1") or []), " ".join(pg.get("2") or [])
    p1, p2, ph = pages.get((PDF_NAME, 1)), pages.get((PDF_NAME, 2)), pages.get((JPG_NAME, 1))
    texts = [(r or {}).get("text") if r else None for r in (p1, p2, ph)] + [light_text]
    cers = [cer(t, e) for t, e in zip(texts, (exp1, exp2, exp1, exp1))]
    problems: List[str] = []
    for label, c in zip(("page 1", "page 2", "photo", "light reader page 1"), cers):
        if c is None:
            problems.append(f"{label} not read")
        elif c > CER_MAX:
            problems.append(f"{label} CER {pct(c)} (over 2%)")
    if not (p2 or {}).get("sideways"):
        problems.append("page 2 not reported sideways")
    if not fr.get("model_loaded", True):
        problems.append("the first read loaded no reader")
    if light_device and not light_device.startswith("cuda"):
        problems.append(f"the light reader is on {light_device}, not the card")
    strict = (flags.get("cudnn_deterministic") is True and flags.get("cudnn_benchmark") is False
              and flags.get("matmul_allow_tf32") is False and flags.get("cudnn_allow_tf32") is False)
    if not strict:
        problems.append("the strict float32 flags did not read back as set")
    out = rec_out[0] if isinstance(rec_out, (list, tuple)) and rec_out else None
    shape = list(getattr(out, "shape", ()) or ())
    if not (out is not None and str(getattr(out, "dtype", "")) == "float32" and len(shape) == 3 and shape[0] == 1
            and shape[1] > 0 and shape[2] == REC_CLASSES):
        problems.append(f"the recogniser's output is {shape or '?'} {getattr(out, 'dtype', '?')}, not [1, T, 97] "
                        "float32")
    cpu_calls = int(rstats.get("cpu_calls", 0) or 0)
    if reserved_gb is not None and float(reserved_gb) > CAP_GB:
        problems.append(f"peak reserved {float(reserved_gb):.2f} GB is over the {CAP_GB:.1f} GB cap")
    if cpu_calls:
        problems.append(f"{cpu_calls} call(s) ran on the processor")
    if int(fr.get("skipped", 0) or 0):
        problems.append(f"{fr.get('skipped')} page(s) did not fit the card's cap")
    code = read_code(texts)
    bracket, warn = compare_text(code, facts["card_name"], build_read(ctx.bundle))
    readers_s = float(fr.get("model_load_s") or 0.0) + m.get("light_load_s", 0.0)
    facts.update(_gate_facts(gate))
    facts.update({
        "cudnn": _cudnn_version(), "device": device, "cer": [None if c is None else round(c, 4) for c in cers],
        "sideways": [bool((p1 or {}).get("sideways")), bool((p2 or {}).get("sideways"))],
        "passes": [passes.get((PDF_NAME, 1)), passes.get((PDF_NAME, 2)), passes.get((JPG_NAME, 1))],
        "seconds": {"start": round(m["torch_s"] + m["cuda_s"] + readers_s, 2), "torch": round(m["torch_s"], 2),
                    "cuda": round(m["cuda_s"], 2), "readers": round(readers_s, 2),
                    "page1": (p1 or {}).get("seconds"), "page2": (p2 or {}).get("seconds"),
                    "photo": (ph or {}).get("seconds"), "light": round(m.get("light_s", 0.0), 2)},
        "peak_alloc_gb": alloc_gb, "peak_reserved_gb": reserved_gb, "cap_gb": CAP_GB, "cpu_calls": cpu_calls,
        "oom_retries": int(fr.get("oom_retries", 0) or 0) + int(rstats.get("oom_retries", 0) or 0),
        "skipped": int(fr.get("skipped", 0) or 0), "downscaled": int(fr.get("downscaled", 0) or 0),
        "strict": strict, "rec_shape": shape, "read_code": code,
        "build_read": {"read_code": str(build_read(ctx.bundle).get("read_code") or ""),
                       "card": str(build_read(ctx.bundle).get("card") or "")},
    })
    tail = f"CER {' / '.join(pct(c) for c in cers)}; READ CODE {code} {bracket}"
    res = PartResult("card", OK if not problems else FAIL,
                     (f"reader on {device}; " + tail) if not problems else "; ".join(problems) + "; " + tail,
                     "", facts=facts)
    if warn:
        res.warnings.append(warn)
    return res


def _gate_facts(gate: Any) -> Dict[str, Any]:
    out: Dict[str, Any] = {"peak_ws_mb": sysinfo.peak_working_set_mb()}
    try:
        st = gate.stats()
        out.update(waits=int(st.get("waits", 0) or 0), waited_s=float(st.get("waited_s", 0.0) or 0.0))
    except Exception:
        pass
    return out


# --------------------------------------------------------------------------------------- imports (test 8 g)
IMPORT_ROOT = "src.verify.runner"
IMPORT_SKIP = ("src.verify.runner.devplaces", "src.verify.runner.tests", "src.verify.runner.selftest",
               "src.verify.runner.app.tests")
IMPORT_THIRD_PARTY = ("numpy", "cv2", "pymupdf", "PIL.Image", "openpyxl", "pyzbar.pyzbar", "glue_numpy",
                      "easyocr_vendor", "torch", "torchvision", "easyocr", "easyocr.easyocr", "scipy.ndimage",
                      "skimage.io")


def runner_module_names(importer: Callable[[str], Any] = importlib.import_module) -> List[str]:
    """Every module of the runner package tree (the app's included), without the DEV and test packages: found with
    pkgutil.iter_modules package by package (PyInstaller's pkgutil hook lists the frozen ones), never walking into
    a skipped package."""
    import pkgutil
    names: List[str] = []
    todo = [IMPORT_ROOT]
    while todo:
        pkg_name = todo.pop()
        names.append(pkg_name)
        pkg = importer(pkg_name)
        for mi in pkgutil.iter_modules(list(getattr(pkg, "__path__", []) or []), pkg_name + "."):
            if mi.name.startswith(IMPORT_SKIP):
                continue
            if mi.ispkg:
                todo.append(mi.name)
            else:
                names.append(mi.name)
    return sorted(set(names))


def part_imports(ctx: Context, importer: Callable[[str], Any] = importlib.import_module) -> PartResult:
    """APP_SPEC R2.6 8 g, run by ``build.py check`` on the frozen dist (not one of the nine parts): every bundled
    src.* module (gpuread included; the bot modules through botimport.load with its src.config stub), then torch,
    torchvision, easyocr, scipy.ndimage, skimage.io and the other third-party packages, with no CUDA work (torch is
    only imported; the card is never initialised)."""
    failed: List[str] = []
    count = 0
    try:
        names = runner_module_names(importer)
    except BaseException as e:                         # noqa: BLE001 - a broken package is this part's FAIL
        raise PartFailed(f"the runner package could not be listed ({type(e).__name__})", "MISSING_PART")
    for n in names:
        try:
            importer(n)
            count += 1
        except BaseException as e:                     # noqa: BLE001 - SystemExit / DLL errors included
            failed.append(f"{n} ({type(e).__name__})")
    try:
        bi = importer(f"{IMPORT_ROOT}.botimport")
        bot = bi.load()
        count += sum(1 for k in ("dv", "PC", "TC", "R", "C", "FC", "OV", "P") if getattr(bot, k, None) is not None)
    except BaseException as e:                         # noqa: BLE001
        failed.append(f"the bot modules ({type(e).__name__})")
    for n in IMPORT_THIRD_PARTY:
        try:
            importer(n)
            count += 1
        except BaseException as e:                     # noqa: BLE001
            failed.append(f"{n} ({type(e).__name__})")
    facts: Dict[str, Any] = {"modules": count, "runner_modules": len(names)}
    torch = sys.modules.get("torch")
    if torch is not None:
        try:
            facts["cuda_initialized"] = bool(torch.cuda.is_initialized())
        except Exception:
            facts["cuda_initialized"] = None
        if facts.get("cuda_initialized"):
            failed.append("torch started CUDA during the imports")
    if len(names) < 2:
        failed.append("no runner module was found")
    if failed:
        raise PartFailed(f"{len(failed)} import(s) failed: " + "; ".join(failed[:8]), "MISSING_PART", facts)
    return PartResult("imports", OK, f"{count} modules imported, no CUDA work", facts=facts)


PART_FUNCS: Dict[str, Callable[[Context], PartResult]] = {
    "runtime": part_runtime, "core": part_core, "pdf": part_pdf, "card": part_card, "qr": part_qr,
    "excel": part_excel, "tk": part_tk, "guard": part_guard, "imports": part_imports,
}


# ========================================================================================== the parent side
@dataclass
class ChildOutcome:
    returncode: Optional[int]
    timed_out: bool = False
    cancelled: bool = False
    seconds: float = 0.0
    error: str = ""                   # the part could not be started (an exception's type name)


def self_command(*args: str) -> List[str]:
    """C's spawn.self_command, else: the frozen exe with ``args``; unfrozen, this Python with the app's entry."""
    try:
        fn = getattr(_mod("spawn"), "self_command")
        return list(fn(*args))
    except (ImportError, AttributeError, KeyError):
        pass
    if _frozen():
        return [sys.executable, *args]
    return [sys.executable, "-I", "-B", "-m", "src.verify.runner.app.entry", *args]


class KillOnCloseJob:
    """A Windows job object with JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE. A part child is put into it, so when this
    process ends in any way (the window's Cancel ends the --selftest process with TerminateProcess), Windows ends
    the child too: no card child is left holding the card. Best effort: without a job the child still runs."""

    def __init__(self) -> None:
        self.handle: Any = None
        self._k: Any = None
        try:
            import ctypes
            from ctypes import wintypes

            class _Io(ctypes.Structure):
                _fields_ = [(n, ctypes.c_ulonglong) for n in ("ReadOperationCount", "WriteOperationCount",
                                                              "OtherOperationCount", "ReadTransferCount",
                                                              "WriteTransferCount", "OtherTransferCount")]

            class _Basic(ctypes.Structure):
                _fields_ = [("PerProcessUserTimeLimit", ctypes.c_int64), ("PerJobUserTimeLimit", ctypes.c_int64),
                            ("LimitFlags", wintypes.DWORD), ("MinimumWorkingSetSize", ctypes.c_size_t),
                            ("MaximumWorkingSetSize", ctypes.c_size_t), ("ActiveProcessLimit", wintypes.DWORD),
                            ("Affinity", ctypes.c_size_t), ("PriorityClass", wintypes.DWORD),
                            ("SchedulingClass", wintypes.DWORD)]

            class _Extended(ctypes.Structure):
                _fields_ = [("BasicLimitInformation", _Basic), ("IoInfo", _Io), ("ProcessMemoryLimit", ctypes.c_size_t),
                            ("JobMemoryLimit", ctypes.c_size_t), ("PeakProcessMemoryUsed", ctypes.c_size_t),
                            ("PeakJobMemoryUsed", ctypes.c_size_t)]

            k = ctypes.WinDLL("kernel32", use_last_error=True)
            k.CreateJobObjectW.restype = wintypes.HANDLE
            k.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
            k.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
            k.SetInformationJobObject.restype = wintypes.BOOL
            k.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
            k.AssignProcessToJobObject.restype = wintypes.BOOL
            k.CloseHandle.argtypes = [wintypes.HANDLE]
            h = k.CreateJobObjectW(None, None)
            if not h:
                return
            info = _Extended()
            info.BasicLimitInformation.LimitFlags = 0x2000          # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
            if not k.SetInformationJobObject(h, 9, ctypes.byref(info), ctypes.sizeof(info)):   # extended limits
                k.CloseHandle(h)
                return
            self.handle, self._k = h, k
        except Exception:
            self.handle = None

    def add(self, popen: Any) -> bool:
        if not self.handle:
            return False
        try:
            from ctypes import wintypes
            return bool(self._k.AssignProcessToJobObject(self.handle, wintypes.HANDLE(int(popen._handle))))
        except Exception:
            return False

    def close(self) -> None:
        """Closing the last handle ends every process still in the job (only our own child is ever in it)."""
        if self.handle:
            try:
                self._k.CloseHandle(self.handle)
            except Exception:
                pass
            self.handle = None


class SubprocessRunner:
    """Runs one part child and waits for it: no console window, BELOW_NORMAL priority, its output into the part's
    log, inside a kill-on-close job. On a timeout the child is ended (TerminateProcess; it is our own child, no other
    process is touched). On Cancel the cancel file is made first, so the child stops at its next event; after
    CANCEL_GRACE_S it is ended."""

    def __init__(self, command: Callable[..., List[str]] = self_command, poll_s: float = POLL_S,
                 grace_s: float = CANCEL_GRACE_S) -> None:
        self.command = command
        self.poll_s = poll_s
        self.grace_s = grace_s

    def run(self, part: str, result_path: Path, timeout_s: float, *, cancel: Optional[Callable[[], bool]] = None,
            cancel_file: Optional[Path] = None, tick: Optional[Callable[[], None]] = None,
            log_path: Optional[Path] = None, env: Optional[Mapping[str, str]] = None) -> ChildOutcome:
        cmd = self.command("--selftest-part", part, str(result_path))
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) | getattr(subprocess, "BELOW_NORMAL_PRIORITY_CLASS", 0)
        t0 = time.monotonic()
        log = open(log_path, "wb") if log_path is not None else None
        job = KillOnCloseJob()
        try:
            try:
                p = subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=log or subprocess.DEVNULL,
                                     stderr=subprocess.STDOUT, env=dict(env) if env is not None else None,
                                     creationflags=flags, close_fds=True)
            except (OSError, ValueError) as e:
                return ChildOutcome(None, error=type(e).__name__)
            job.add(p)
            timed_out = cancelled = False
            cancel_at: Optional[float] = None
            try:
                while p.poll() is None:
                    if tick is not None:
                        tick()
                    now = time.monotonic()
                    if cancel_at is None and cancel is not None and cancel():
                        cancelled, cancel_at = True, now
                        if cancel_file is not None:
                            try:
                                cancel_file.write_text("cancel", encoding="ascii")
                            except OSError:
                                pass
                    if cancel_at is not None and now - cancel_at > self.grace_s:
                        p.kill()
                        break
                    if not cancelled and now - t0 > timeout_s:
                        timed_out = True
                        p.kill()
                        break
                    time.sleep(self.poll_s)
            finally:
                if p.poll() is None:
                    p.kill()
                try:
                    p.wait(timeout=30)
                except subprocess.TimeoutExpired:
                    pass
            if tick is not None:
                tick()
            return ChildOutcome(p.returncode, timed_out=timed_out, cancelled=cancelled,
                                seconds=round(time.monotonic() - t0, 2))
        finally:
            job.close()
            if log is not None:
                log.close()


class _EventTail:
    """Reads the card child's events file as it grows and forwards each event (counts and codes only)."""

    def __init__(self, path: Path, forward: Callable[[Dict[str, Any]], None]) -> None:
        self.path = path
        self.forward = forward
        self.offset = 0
        self.buf = b""
        self.waited_s = 0.0

    def poll(self) -> None:
        try:
            with open(self.path, "rb") as f:
                f.seek(self.offset)
                chunk = f.read()
        except OSError:
            return
        self.offset += len(chunk)
        self.buf += chunk
        while b"\n" in self.buf:
            line, self.buf = self.buf.split(b"\n", 1)
            try:
                ev = json.loads(line.decode("ascii"))
            except ValueError:
                continue
            if not isinstance(ev, dict):
                continue
            if ev.get("ev") == "gpu_wait" and isinstance(ev.get("waited_s"), (int, float)):
                self.waited_s = max(self.waited_s, float(ev["waited_s"]))
            self.forward(ev)


def _outcome_result(name: str, oc: ChildOutcome, timeout_s: float, tail: Optional[_EventTail]) -> PartResult:
    if oc.error:
        return PartResult(name, FAIL, f"the part could not be started ({oc.error})", "STARTUP_FAILED")
    if oc.cancelled:
        return PartResult(name, FAIL, "cancelled")
    if oc.timed_out:
        detail = f"did not finish within {timeout_s:.0f} s"
        if tail is not None and tail.waited_s > 0:
            detail += f" (it waited {tail.waited_s:.0f} s for the graphics card)"
        return PartResult(name, FAIL, detail, "CRASH")
    rc = oc.returncode
    if rc not in (0, None):
        return PartResult(name, FAIL, f"process ended with {exit_text(rc)}", exit_code_class(rc))
    return PartResult(name, FAIL, "the part ended without a result", "CRASH")


def _fresh_scratch(data_dir: Path) -> Path:
    """<DATA>\\selftest\\<SCRATCH_NAME>, emptied; also <DATA>\\tmp\\selftest-ascii."""
    root = Path(data_dir) / SELFTEST_DIR / SCRATCH_NAME
    if root.name != SCRATCH_NAME or not _under(root, data_dir):
        raise RuntimeError("the scratch folder is not under the data folder")
    for folder in (root, Path(data_dir) / "tmp" / ASCII_DIR):
        if folder.exists():
            if sys.version_info >= (3, 12):
                shutil.rmtree(folder, onexc=_rm_readonly)
            else:                                                       # pragma: no cover (the app is 3.12)
                shutil.rmtree(folder, onerror=_rm_readonly)
    root.mkdir(parents=True, exist_ok=True)
    return root


def _rm_readonly(func: Callable[..., Any], path: str, _exc: Any) -> None:
    """A read-only file in the scratch folder (a copy of a read-only input) is made writable and removed."""
    try:
        os.chmod(path, 0o666)
        func(path)
    except OSError:
        pass


def timeout_of(part: str) -> int:
    return CARD_TIMEOUT_S if part == "card" else PART_TIMEOUT_S


def run_selftest(report_dir: Optional[Any] = None, *, data_dir: Optional[Any] = None,
                 bundle_dir: Optional[Any] = None, app_dir: Optional[Any] = None,
                 lock_paths: Optional[Sequence[str]] = None, extra_locks: Sequence[str] = (),
                 runner: Any = None, on_progress: Optional[Callable[[str, str, Dict[str, Any]], None]] = None,
                 cancel: Optional[Callable[[], bool]] = None, parts: Sequence[str] = PARTS,
                 write: bool = True, now: Optional[dt.datetime] = None) -> Outcome:
    """The whole self-test (rev 1 1.2, R2.3.4). ``lock_paths`` defaults to the settings' lock path;
    ``on_progress(part, state, info)`` gets "start", "end" ({"status"}), "progress" (integrity: {"done", "of"}) and
    "event" (a card-part event: gpu_wait, page); ``cancel()`` True stops after the running part. Writes SELFTEST.txt
    to the report folder (default: the settings' report folder) and the copies in <DATA>\\selftest (``write``)."""
    data = Path(data_dir) if data_dir is not None else _data_dir()
    bundle = Path(bundle_dir) if bundle_dir is not None else _bundle_dir()
    app = Path(app_dir) if app_dir is not None else _app_dir()
    when = now or dt.datetime.now()
    locks = [str(p) for p in (list(lock_paths) if lock_paths is not None else lock_setting(data)) if p]
    locks += [str(p) for p in extra_locks if p]
    runner = runner or SubprocessRunner()

    def progress(part: str, state: str, info: Dict[str, Any]) -> None:
        if on_progress is not None:
            try:
                on_progress(part, state, info)
            except Exception:
                pass

    data.mkdir(parents=True, exist_ok=True)
    scratch = _fresh_scratch(data)
    parts_dir = scratch / PARTS_DIR
    parts_dir.mkdir(parents=True, exist_ok=True)
    cancel_file = parts_dir / "cancel"
    arch = _arch_flags()
    facts = _fn("collect", sysinfo.collect)(data, arch_flags=arch)
    env = dict(os.environ)
    env["HDC_DATA_DIR"] = str(data)
    results: List[PartResult] = []
    was_cancelled = False
    for name in parts:
        if not was_cancelled and cancel is not None and cancel():
            was_cancelled = True
        if was_cancelled:
            results.append(PartResult(name, FAIL, "not run (the self-test was cancelled)"))
            continue
        progress(name, "start", {})
        t0 = time.perf_counter()
        if name == "integrity":
            try:
                res = part_integrity(app, bundle, progress=lambda d, n: progress("integrity", "progress",
                                                                                   {"done": d, "of": n}))
            except Exception as e:
                res = PartResult(name, FAIL, f"{type(e).__name__}: {e}")
        else:
            res = _run_child(name, runner, parts_dir, scratch, data, bundle, app, locks, cancel, cancel_file,
                             env, progress)
            if res.detail == "cancelled":
                was_cancelled = True
        res.detail = _one_line(sysinfo.scrub(res.detail))
        res.warnings = [_one_line(sysinfo.scrub(w)) for w in res.warnings]
        if not res.seconds:
            res.seconds = round(time.perf_counter() - t0, 2)
        results.append(res)
        progress(name, "end", {"status": res.status, "code": res.code})
    by = {r.part: r for r in results}
    core_feats = (by.get("core").facts.get("numpy_features") if by.get("core") else None) or None
    warns = sysinfo.warnings(facts, core_feats)
    for r in results:
        warns += r.warnings
    lock_state = _lock_state(locks)
    card_facts: Dict[str, Any] = {}
    if by.get("card") is not None:
        card_facts = dict(by["card"].facts)
        if not card_facts.get("torch") and not card_facts.get("code"):
            card_facts["code"] = by["card"].code or "the card part failed"
    text = compose_text(app_versions(), when, results, sysinfo.lines(facts, core_feats), card_facts, lock_state,
                        warns)
    summary = make_summary(when, results, warns, facts)
    out = Outcome(ok=bool(results) and all(r.ok for r in results), text=text, parts=results,
                  summary=summary, cancelled=was_cancelled)
    if write:
        _write_outputs(out, data, report_dir, when)
    return out


def _run_child(name: str, runner: Any, parts_dir: Path, scratch: Path, data: Path, bundle: Optional[Path],
               app: Optional[Path], locks: List[str], cancel: Optional[Callable[[], bool]], cancel_file: Path,
               env: Dict[str, str], progress: Callable[[str, str, Dict[str, Any]], None]) -> PartResult:
    result_path = parts_dir / f"{name}.json"
    events = parts_dir / f"{name}.events.jsonl"
    _write_json(parts_dir / f"{name}.in.json", {
        "schema": "hdc-selftest-part-in/1", "part": name, "root": str(scratch), "data_dir": str(data),
        "bundle": str(bundle) if bundle else None, "app_dir": str(app) if app else None,
        "lock_paths": list(locks), "events": str(events), "cancel": str(cancel_file)})
    tail = _EventTail(events, lambda ev: progress(name, "event", ev))
    timeout = timeout_of(name)
    if name == "card":                       # the worker's environment (boot applies it in the child too)
        try:
            env = dict(_mod("spawn").child_env(0, env))
        except Exception:
            pass
    try:
        oc = runner.run(name, result_path, timeout, cancel=cancel, cancel_file=cancel_file, tick=tail.poll,
                        log_path=parts_dir / f"{name}.log", env=env)
    except Exception as e:
        oc = ChildOutcome(None, error=type(e).__name__)
    tail.poll()
    res = PartResult.from_json(_read_json(result_path))
    if res is None or res.part != name:
        return _outcome_result(name, oc, timeout, tail)
    return res


def _lock_state(locks: Sequence[str]) -> List[Tuple[str, bool]]:
    """(kind, present now) of each watched lock: stat only."""
    out = []
    try:
        cg = _mod("cardgate")
        now = time.time()
        for p in locks:
            out.append((cg.lock_kind(p), bool(cg.lock_active(p, now))))
    except Exception:
        out = [("other", False) for _p in locks]
    return out


def make_summary(when: dt.datetime, results: Sequence[PartResult], warns: Sequence[str],
                 facts: Mapping[str, Any]) -> Dict[str, Any]:
    """selftest.json: counts, codes, CERs and the READ CODE; never text, names of files or paths."""
    card = next((r.facts for r in results if r.part == "card"), {})
    keep = ("card_name", "torch", "cuda", "cudnn", "device", "cer", "sideways", "passes", "seconds",
            "peak_alloc_gb", "peak_reserved_gb", "cap_gb", "cpu_calls", "oom_retries", "skipped", "downscaled",
            "strict", "rec_shape", "read_code", "build_read", "waits", "waited_s", "peak_ws_mb", "code")
    c = facts.get("card") or {}
    return _jsonable({
        "schema": SCHEMA_SUMMARY, "run": when.strftime("%Y-%m-%d %H:%M"), "app": app_versions(),
        "ok": bool(results) and all(r.ok for r in results),
        "ok_parts": sum(r.ok for r in results), "parts": [{"part": r.part, "status": r.status, "code": r.code,
                                                            "seconds": r.seconds} for r in results],
        "card": {k: card.get(k) for k in keep if k in card},
        "nvml": {k: c.get(k) for k in ("name", "driver", "cuda_driver", "cc", "total_mb", "free_mb") if k in c},
        "warnings": list(warns)})


def build_read_record(summary: Mapping[str, Any]) -> Dict[str, Any]:
    """What the build stores as selftest\\build_read.json after the frozen self-test on the build PC (rev 1 1.2,
    R2.3.4): the READ CODE and the card model it was read on."""
    card = summary.get("card") or {}
    nv = summary.get("nvml") or {}
    return {"schema": SCHEMA_BUILD_READ, "read_code": card.get("read_code"), "card": card.get("card_name"),
            "cc": nv.get("cc"), "driver": nv.get("driver"), "torch": card.get("torch"), "cuda": card.get("cuda"),
            "cudnn": card.get("cudnn"), "build_id": (summary.get("app") or {}).get("build_id")}


def _write_outputs(out: Outcome, data: Path, report_dir: Optional[Any], when: dt.datetime) -> None:
    folder = Path(report_dir) if report_dir is not None else default_report_dir(data)
    try:
        folder.mkdir(parents=True, exist_ok=True)
        stem = f"SELFTEST {when:%Y-%m-%d %H%M}"
        target = folder / f"{stem}.txt"
        n = 2
        while target.exists():
            target = folder / f"{stem} ({n}).txt"
            n += 1
        target.write_text(out.text, encoding="utf-8")
        out.report_path = target
    except OSError as e:
        out.write_error = type(e).__name__
    try:
        copy = data / SELFTEST_DIR / "SELFTEST.txt"
        copy.parent.mkdir(parents=True, exist_ok=True)
        copy.write_text(out.text, encoding="utf-8")
        out.copy_path = copy
        _write_json(data / SELFTEST_DIR / "selftest.json", out.summary)
    except OSError as e:
        out.write_error = out.write_error or type(e).__name__


# ============================================================================================ SELFTEST.txt
def _f(v: Any, nd: int = 1) -> str:
    try:
        return f"{float(v):.{nd}f}"
    except (TypeError, ValueError):
        return "?"


def _waits_line(locks: Sequence[Tuple[str, bool]], waited_s: float) -> str:
    if not locks:
        what = "no lock file (the setting is off)"
    elif len(locks) == 1:
        kind, present = locks[0]
        who = "the Telegram bot's lock file" if kind == "bot" else "another program's lock file"
        what = f"{who} ({'present' if present else 'not present'} now)"
    else:
        bots = sum(1 for k, _p in locks if k == "bot")
        here = sum(1 for _k, p in locks if p)
        what = (f"{len(locks)} lock files" + (" (the Telegram bot's among them)" if bots else "")
                + f", {here} present now")
    return f"Waits for: {what} | waited {_f(waited_s, 0)} s during this test"


def card_lines(card: Mapping[str, Any], locks: Sequence[Tuple[str, bool]]) -> List[str]:
    """Reading engine, Waits for, Reading, Card memory and the reading process's peak memory (R2.3.4)."""
    out = []
    if card.get("torch"):
        out.append(f"Reading engine: torch {card.get('torch')}, CUDA {card.get('cuda', '?')}, cuDNN "
                   f"{card.get('cudnn', '?')} | cap {_f(card.get('cap_gb', CAP_GB))} GB | first read: bot EasyOCR "
                   "(torch defaults) | light reader: strict fp32")
    else:
        out.append(f"Reading engine: not started ({card.get('code') or 'the card part did not run'})")
    out.append(_waits_line(locks, float(card.get("waited_s") or 0.0)))
    s = card.get("seconds") if isinstance(card.get("seconds"), dict) else None
    if s:
        side = list(card.get("sideways") or [False, False]) + [False, False]
        passes = list(card.get("passes") or []) + [None, None, None]

        def page(label: str, sideways: bool, n: Any, secs: Any) -> str:
            more = f", {int(n)} passes" if isinstance(n, int) and n > 1 else ""
            return f"{label} ({'sideways' if sideways else 'upright'}{more}) {_f(secs)} s"

        out.append(f"Reading: start {_f(s.get('start'))} s (torch {_f(s.get('torch'))}, CUDA {_f(s.get('cuda'))}, "
                   f"readers {_f(s.get('readers'))}) | {page('page 1', side[0], passes[0], s.get('page1'))} | "
                   f"{page('page 2', side[1], passes[1], s.get('page2'))} | photo {_f(s.get('photo'))} s")
    else:
        out.append("Reading: not run")
    if card.get("peak_reserved_gb") is not None:
        out.append(f"Card memory: peak {_f(card.get('peak_alloc_gb'), 2)} GB allocated, "
                   f"{_f(card.get('peak_reserved_gb'), 2)} GB reserved (cap {_f(card.get('cap_gb', CAP_GB))} GB) | "
                   f"calls on the processor: {int(card.get('cpu_calls') or 0)}")
    else:
        out.append("Card memory: not measured")
    ws = card.get("peak_ws_mb")
    out.append(f"Peak memory of the reading process: {int(ws)} MB" if isinstance(ws, (int, float)) else
               "Peak memory of the reading process: not measured")
    return out


def compose_text(app: Mapping[str, str], when: dt.datetime, results: Sequence[PartResult],
                 system_lines: Sequence[str], card: Mapping[str, Any], locks: Sequence[Tuple[str, bool]],
                 warns: Sequence[str]) -> str:
    """SELFTEST.txt (rev 1 1.2's fixed layout with R2.3.4's lines)."""
    n_ok, n = sum(r.ok for r in results), len(results)
    overall = f"OK ({n_ok} of {n} parts)" if n and n_ok == n else f"FAIL ({n_ok} of {n} parts OK)"
    lines = ["HANGEUL DOCUMENT CHECKER - SELF-TEST",
             f"App {app.get('app_version', '?')}, build {app.get('build_id', '?')} | rules: bot "
             f"{app.get('base_rules', '?')} + runner {app.get('runner_rules', '?')} | runner {app.get('runner', '?')}",
             f"Run: {when:%Y-%m-%d %H:%M} | overall: {overall}"]
    lines += list(system_lines)
    lines += card_lines(card, locks)
    lines.append("PARTS")
    for r in results:
        lines.append(f"  {r.part:<11}{r.status:<6}{_one_line(r.detail)}".rstrip())
    lines.append("WARNINGS")
    seen = []
    for w in warns:
        w = _one_line(w)
        if w and w not in seen:
            seen.append(w)
    lines += [f"  {w}" for w in seen] or ["  (none)"]
    text = "\n".join(lines) + "\n"
    if sysinfo.holds_private(text):                  # belt and braces: never a path or a user name
        text = sysinfo.scrub(text)
    return text


# ===================================================================================================== CLI
def main(argv: Optional[Sequence[str]] = None, *, report_dir: Optional[Any] = None,
         extra_locks: Optional[Sequence[str]] = None) -> int:
    """``--selftest [--report-dir DIR] [--gpu-lock-extra PATH]...`` (C's entry calls ``main(report_dir=...)``; the
    other flags of the command line are the entry's and ignored here). Exit 0 when every part is OK, else 1."""
    ap = argparse.ArgumentParser(prog="Hangeul Document Checker --selftest", add_help=False, allow_abbrev=False)
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--report-dir")
    ap.add_argument("--gpu-lock-extra", action="append", default=[])
    try:
        a, _rest = ap.parse_known_args(list(sys.argv[1:] if argv is None else argv))
    except SystemExit:
        a = argparse.Namespace(report_dir=None, gpu_lock_extra=[])
    folder = report_dir if report_dir is not None else a.report_dir
    locks = list(extra_locks) if extra_locks is not None else list(a.gpu_lock_extra)
    out = run_selftest(Path(folder) if folder else None, extra_locks=locks)
    return out.exit_code
