"""checkfacts.json: the facts of one check WITHOUT personal data (APP_SPEC rev 1 section 3.2, rev 2 R2.1.1).

The owner sends this file from another PC when its result code differs from the office PC's. It holds versions,
the build id, the result code, the check date, the program, the mode, the pages per engine, OCR seconds, model
load times, peak memory, budget hits (where, page), one entry per summary line (title, pages, verdict and the
rule ids of its reasons), the MISSING texts, the notice kinds and pages, and the "gpu" block (card, engine
versions, first-read counts, calls on the card and on the processor by model, OOM retries, waits, peaks).

It NEVER holds a finding's message, a name, a number read off a document, a page text, a file name or a path:
  * every line is built from rule ids, verdict words, report titles and page numbers;
  * folder mode names a file by its position (file 1, 2 ... in run order), never by its name;
  * a MISSING text is kept only when it is one of split's fixed texts (a conditional line keeps its fixed part);
  * every block another task hands in (gate statistics, session statistics, card facts) goes through clean(),
    which keeps numbers, booleans and short strings that cannot be a path.
Standard library plus the runner's report / verdict / model (all light).
"""
from __future__ import annotations

import importlib
import json
import math
import re
import statistics
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Mapping, Optional, Sequence, Set, Tuple

from .. import report as REPORT
from .. import verdict as V
from ..model import ERROR, FAIL, FLAG, RunResult

SCHEMA = "hdc-checkfacts/1"
REASON_LEVELS = (FAIL, ERROR, FLAG)          # the levels that are reasons (as in the result code)
MAX_STR = 80                                 # a longer string is dropped by clean()
MAX_DEPTH = 6
CARD_CODES = ("NO_NVIDIA_CARD", "DRIVER_TOO_OLD", "CARD_NOT_SUPPORTED", "CARD_TOO_SMALL", "LOW_CARD_MEMORY",
              "CARD_FAILED")
_DROP = object()
_PATHISH = re.compile(r"[A-Za-z]:[\\/]|\\|//|^/|^~[\\/]")
_HEAD_COUNT = re.compile(r"(\d+) (FAIL|FLAG|MISSING|ORDER|NOT JUDGED|STOP)\b")

# aliases of the statistics keys another task may use (TorchSession.stats, the card's peaks); first match wins
_ALIASES = {
    "card_calls": ("calls_card", "card_calls", "calls_cuda", "cuda_calls", "calls_gpu", "gpu_calls"),
    "card_s": ("seconds_card", "card_seconds", "card_s", "seconds_cuda", "cuda_seconds", "seconds_gpu"),
    "cpu_calls": ("calls_cpu", "cpu_calls"),
    "cpu_s": ("seconds_cpu", "cpu_seconds", "cpu_s"),
    "oom_retries": ("oom_retries", "ooms", "oom"),
    "peak_alloc_gb": ("peak_alloc_gb", "peak_allocated_gb", "max_alloc_gb", "alloc_gb"),
    "peak_reserved_gb": ("peak_reserved_gb", "max_reserved_gb", "reserved_gb"),
}


# ------------------------------------------------------------------------------------------ cleaning
def safe_str(s: Any, max_len: int = MAX_STR) -> Optional[str]:
    """s when it is a short printable string that cannot be a path (no backslash, no drive letter, no "//",
    no leading "/"), else None."""
    if not isinstance(s, str):
        return None
    if len(s) > max_len or _PATHISH.search(s) or not s.isprintable():
        return None
    return s


def _num(x: Any) -> Any:
    if isinstance(x, bool) or x is None:
        return x
    if isinstance(x, int):
        return x
    if isinstance(x, float):
        return round(x, 4) if math.isfinite(x) else None
    try:                                      # numpy scalars and the like
        f = float(x)
    except (TypeError, ValueError):
        return _DROP
    if f.is_integer() and type(x).__name__.startswith(("int", "uint")):
        return int(f)
    return round(f, 4) if math.isfinite(f) else None


def clean(x: Any, depth: int = 0) -> Any:
    """Numbers, booleans, None and safe strings (safe_str) are kept; mappings and sequences are cleaned item by
    item (a key that is not a safe string drops its item); anything else is dropped. Never raises."""
    if depth > MAX_DEPTH:
        return _DROP
    if x is None or isinstance(x, bool):
        return x
    if isinstance(x, str):
        s = safe_str(x)
        return _DROP if s is None else s
    if isinstance(x, (int, float)):
        return _num(x)
    if isinstance(x, Mapping):
        out: Dict[str, Any] = {}
        for k, v in x.items():
            ks = safe_str(k if isinstance(k, str) else str(k) if isinstance(k, (int, float)) else None, 60)
            if ks is None:
                continue
            cv = clean(v, depth + 1)
            if cv is not _DROP:
                out[ks] = cv
        return out
    if isinstance(x, (list, tuple, set, frozenset)):
        items = [clean(v, depth + 1) for v in (sorted(x, key=repr) if isinstance(x, (set, frozenset)) else x)]
        return [v for v in items if v is not _DROP]
    n = _num(x)
    return n


def cleaned(x: Any) -> Any:
    """clean(x), with a dropped top-level value as None."""
    v = clean(x)
    return None if v is _DROP else v


def pick(d: Any, key: str, default: Any = None) -> Any:
    """d[alias] for the first alias of `key` (_ALIASES) that d has, else default."""
    if not isinstance(d, Mapping):
        return default
    for k in _ALIASES.get(key, (key,)):
        if k in d and d[k] is not None:
            return d[k]
    return default


def count(x: Any) -> int:
    """An int as it is, the length of a list / tuple / dict, else 0."""
    if isinstance(x, bool):
        return int(x)
    if isinstance(x, int):
        return x
    if isinstance(x, float) and math.isfinite(x):
        return int(x)
    if isinstance(x, (list, tuple, dict, set)):
        return len(x)
    return 0


def number(x: Any) -> Optional[float]:
    if isinstance(x, bool) or x is None:
        return None
    try:
        f = float(x)
    except (TypeError, ValueError):
        return None
    return round(f, 4) if math.isfinite(f) else None


def seconds_stats(values: Iterable[float]) -> Dict[str, Any]:
    vs = [float(v) for v in values if isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)]
    if not vs:
        return {"n": 0, "total": 0.0, "mean": None, "median": None, "max": None}
    return {"n": len(vs), "total": round(sum(vs), 3), "mean": round(statistics.fmean(vs), 3),
            "median": round(statistics.median(vs), 3), "max": round(max(vs), 3)}


# ------------------------------------------------------------------------------------------ the gpu block
def cc_text(cc: Any) -> Optional[str]:
    if isinstance(cc, (tuple, list)) and len(cc) == 2 and all(isinstance(v, int) for v in cc):
        return f"{cc[0]}.{cc[1]}"
    return safe_str(cc) if isinstance(cc, str) else None


def card_info_block(info: Any) -> Dict[str, Any]:
    """The NVML facts of the card (gpuinfo.CardInfo): name, compute capability, driver, CUDA driver version,
    total and free MB, index. No path."""
    if info is None:
        return {}
    return {"nvml": bool(getattr(info, "nvml", False)), "count": count(getattr(info, "count", 0)),
            "index": count(getattr(info, "index", 0)), "name": safe_str(getattr(info, "name", None)),
            "cc": cc_text(getattr(info, "cc", None)), "driver": safe_str(getattr(info, "driver", None)),
            "cuda_driver": count(getattr(info, "cuda_driver", 0)) or None,
            "total_mb": count(getattr(info, "total_mb", 0)), "free_mb": count(getattr(info, "free_mb", 0))}


def first_read_block(fr: Optional[Mapping[str, Any]]) -> Dict[str, Any]:
    """gpuengine.first_read's result (counts only): pages by kind, downscaled, skipped, OOM retries, seconds,
    model load and the phase-1 peaks."""
    if not isinstance(fr, Mapping):
        return {}
    pages = fr.get("pages") if isinstance(fr.get("pages"), Mapping) else {}
    return {"pages": {k: count(pages.get(k, 0)) for k in ("total", "read", "ocr", "layer", "image", "skipped")},
            "downscaled": count(fr.get("downscaled")), "skipped": count(fr.get("skipped")),
            "oom_retries": count(fr.get("oom_retries")), "seconds_total": number(fr.get("seconds_total")),
            "model_load_s": number(fr.get("model_load_s")),
            "peak_alloc_gb": number(pick(fr, "peak_alloc_gb")),
            "peak_reserved_gb": number(pick(fr, "peak_reserved_gb"))}


def session_block(st: Any) -> Dict[str, Any]:
    """One TorchSession.stats(): calls and seconds on the card and on the processor, OOM retries."""
    if not isinstance(st, Mapping):
        return {"card_calls": 0, "card_s": None, "cpu_calls": 0, "cpu_s": None, "oom_retries": 0}
    return {"card_calls": count(pick(st, "card_calls", 0)), "card_s": number(pick(st, "card_s")),
            "cpu_calls": count(pick(st, "cpu_calls", 0)), "cpu_s": number(pick(st, "cpu_s")),
            "oom_retries": count(pick(st, "oom_retries", 0))}


def waits_block(gs: Any) -> Dict[str, Any]:
    """cardgate.CardGate.stats(): the number of waits and the seconds waited, plus the rest (by why and where,
    the last free MB) cleaned."""
    if not isinstance(gs, Mapping):
        return {"count": 0, "seconds": 0.0, "detail": {}}
    rest = {k: v for k, v in gs.items() if k not in ("waits", "waited_s")}
    return {"count": count(gs.get("waits", 0)), "seconds": number(gs.get("waited_s")) or 0.0,
            "detail": cleaned(rest) or {}}


def gpu_block(*, info: Any = None, card: Optional[Mapping[str, Any]] = None, cap_gb: Any = None,
              start_s: Any = None, fr: Optional[Mapping[str, Any]] = None,
              sessions: Optional[Mapping[str, Any]] = None, light_load_s: Any = None,
              gate: Optional[Mapping[str, Any]] = None, peaks: Optional[Mapping[str, Any]] = None) -> Dict[str, Any]:
    """The checkfacts "gpu" block. Every argument is optional (a check that stopped early has only some)."""
    card = card if isinstance(card, Mapping) else {}
    frb = first_read_block(fr)
    ses = {k: session_block(v) for k, v in (sessions or {}).items() if safe_str(k)}
    totals = {"card_calls": sum(s["card_calls"] for s in ses.values()),
              "cpu_calls": sum(s["cpu_calls"] for s in ses.values()),
              "oom_retries": sum(s["oom_retries"] for s in ses.values())}
    pk_alloc = [v for v in (number(pick(peaks or {}, "peak_alloc_gb")), frb.get("peak_alloc_gb")) if v is not None]
    pk_res = [v for v in (number(pick(peaks or {}, "peak_reserved_gb")), frb.get("peak_reserved_gb"))
              if v is not None]
    return {
        "card": card_info_block(info),
        "engine": {"torch": safe_str(card.get("torch")), "cuda": safe_str(card.get("cuda")),
                   "cudnn": safe_str(card.get("cudnn")),
                   "cap_gb": number(cap_gb if cap_gb is not None else card.get("cap_gb"))},
        "card_start_s": number(start_s),
        "first_read": frb,
        "light_reader": {"model_load_s": number(light_load_s), "calls": ses, **totals},
        "model_load_s": {"card_start": number(start_s), "first_read": frb.get("model_load_s"),
                         "light_reader": number(light_load_s)},
        "cpu_calls": totals["cpu_calls"],
        "oom_retries": totals["oom_retries"] + count(frb.get("oom_retries")),
        "waits": waits_block(gate),
        "peaks": {"alloc_gb": max(pk_alloc) if pk_alloc else None,
                  "reserved_gb": max(pk_res) if pk_res else None,
                  "first_read_alloc_gb": frb.get("peak_alloc_gb"),
                  "first_read_reserved_gb": frb.get("peak_reserved_gb")},
    }


def done_gpu(block: Optional[Mapping[str, Any]]) -> Dict[str, Any]:
    """The "gpu" part of the done event (R2.3.3): card, peaks, calls on the processor, waits."""
    b = block if isinstance(block, Mapping) else {}
    card, peaks, waits = b.get("card") or {}, b.get("peaks") or {}, b.get("waits") or {}
    return {"card": card.get("name"), "peak_alloc_gb": peaks.get("alloc_gb"),
            "peak_reserved_gb": peaks.get("reserved_gb"), "cpu_calls": count(b.get("cpu_calls")),
            "waits": count(waits.get("count")), "waited_s": number(waits.get("seconds")) or 0.0}


# ------------------------------------------------------------------------------------------ the run's lines
def reasons(fs: Iterable[Any]) -> List[List[Any]]:
    """Sorted unique [verdict, rule, sorted pages] of the findings that are reasons (FAIL, ERROR, FLAG). No
    message."""
    seen: Set[Tuple[str, str, Tuple[int, ...]]] = set()
    for f in fs or ():
        v = getattr(f, "verdict", "")
        if v not in REASON_LEVELS:
            continue
        rule = safe_str(getattr(f, "rule", "")) or "?"
        pages = tuple(sorted({int(p) for p in (getattr(f, "pages", ()) or ())
                              if isinstance(p, int) and not isinstance(p, bool)}))
        seen.add((v, rule, pages))
    rank = {FAIL: 0, ERROR: 1, FLAG: 2}
    return [[v, r, list(p)] for v, r, p in sorted(seen, key=lambda t: (rank[t[0]], t[1], t[2]))]


def _file_index(r: RunResult) -> Dict[str, int]:
    """Folder mode: each input file's position (1-based) in run order. A file is never named."""
    idx: Dict[str, int] = {}
    for p in r.pages or ():
        f = getattr(p, "file", "")
        if f not in idx:
            idx[f] = len(idx) + 1
    return idx


def where_of(u: Any, r: RunResult, fidx: Mapping[str, int]) -> Dict[str, Any]:
    """Bundle: {"pages": [first, last]} (1-based bundle pages). Folder: {"file": <position>, "pages": [first,
    last]} (pages inside that file)."""
    ps = [i for i in (getattr(u, "pages", None) or []) if isinstance(i, int) and 0 <= i < len(r.pages)]
    if not ps:
        return {"pages": []}
    if r.mode == "folder":
        first, last = r.pages[min(ps)], r.pages[max(ps)]
        return {"file": fidx.get(getattr(first, "file", ""), 0),
                "pages": [int(getattr(first, "file_page", 0)) + 1, int(getattr(last, "file_page", 0)) + 1]}
    return {"pages": [min(ps) + 1, max(ps) + 1]}


def doc_lines(r: RunResult) -> List[Dict[str, Any]]:
    """One entry per document line, in the summary's order: title, key, where, verdict, reasons."""
    fidx = _file_index(r)
    out = []
    for d in REPORT.display_docs(r):
        u = d.unit
        out.append({"title": safe_str(REPORT.title_of(u)) or "?", "key": safe_str(getattr(u, "key", "")) or "?",
                    "where": where_of(u, r, fidx), "verdict": safe_str(d.verdict) or "?",
                    "reasons": reasons(d.findings)})
    return out


def cross_lines(r: RunResult) -> List[Dict[str, Any]]:
    """NAMES, FINANCIAL CHAIN and BUNDLE (when they have findings): the line's level and reasons."""
    groups: Dict[str, List[Any]] = {k: [] for k in REPORT.CROSS_LINES}
    for f in r.cross or ():
        groups.setdefault(REPORT._cross_line_of(f), []).append(f)
    return [{"line": name, "verdict": V.worst(fs), "reasons": reasons(fs)} for name, fs in groups.items() if fs]


def known_missing_texts() -> Set[str]:
    """split's fixed MISSING texts (and the bank solvency text); empty when split cannot be imported."""
    try:
        S = importlib.import_module(f"{__package__.rsplit('.', 1)[0]}.split")
    except Exception:
        return set()
    out = {str(v) for v in (getattr(S, "MISSING_TEXT", {}) or {}).values()}
    if getattr(S, "SOLVENCY_MISSING_TEXT", None):
        out.add(str(S.SOLVENCY_MISSING_TEXT))
    return out


def missing_entries(lines: Sequence[Any], known: Iterable[str]) -> List[List[str]]:
    """[level, text] per MISSING line. A text is kept only when it is a fixed text; a line that starts with one
    (the conditional "unless the death certificate on <file> ..." line) keeps that part plus "(conditional)";
    any other line becomes "(other)"."""
    kn = sorted({k for k in known if k}, key=len, reverse=True)
    out: List[List[str]] = []
    for item in lines or ():
        try:
            lv, text = item
        except (TypeError, ValueError):
            continue
        lv = safe_str(lv) or "?"
        text = str(text)
        if text in kn:
            out.append([lv, text])
            continue
        base = next((k for k in kn if text.startswith(k)), None)
        out.append([lv, f"{base} (conditional)" if base else "(other)"])
    return out


def notice_entries(r: RunResult) -> List[Dict[str, Any]]:
    out = []
    for n in r.notices or ():
        pages = [int(p) for p in (getattr(n, "pages", ()) or ()) if isinstance(p, int) and not isinstance(p, bool)]
        out.append({"kind": safe_str(getattr(n, "kind", "")) or "?", "pages": pages,
                    "blocks": bool(getattr(n, "blocks", False))})
    return sorted(out, key=lambda e: (e["pages"], e["kind"]))


def not_checked_block(r: RunResult) -> Dict[str, Any]:
    by: Dict[str, int] = {}
    n = 0
    for item in getattr(r, "not_checked", None) or ():
        try:
            _name, why = item
        except (TypeError, ValueError):
            continue
        n += 1
        key = safe_str(str(why)) or "(other)"
        by[key] = by.get(key, 0) + 1
    return {"files": n, "reasons": by}


def headline_counts(headline: str) -> Dict[str, int]:
    """{"FAIL": n, "FLAG": n, "MISSING": n, ...} parsed from the runner's headline (its numbers only)."""
    return {k: int(n) for n, k in _HEAD_COUNT.findall(str(headline or ""))}


def page_seconds(r: RunResult) -> Dict[str, Any]:
    """OCR seconds per page (n, total, mean, median, max): "first_read" = pages read in phase 1 (the runner's
    engine "gpu": served from the page-texts file), "light_reader" = pages the runner read itself ("cpu")."""
    by: Dict[str, List[float]] = {"gpu": [], "cpu": []}
    for p in r.pages or ():
        e = getattr(p, "engine", "") or ""
        if e in by:
            by[e].append(getattr(p, "seconds", 0.0) or 0.0)
    return {"first_read": seconds_stats(by["gpu"]), "light_reader": seconds_stats(by["cpu"])}


def budget_entries(hits: Iterable[Mapping[str, Any]]) -> List[Dict[str, Any]]:
    out = []
    for h in hits or ():
        if not isinstance(h, Mapping):
            continue
        page = h.get("page")
        out.append({"where": safe_str(str(h.get("where", ""))) or "?",
                    "page": page if isinstance(page, int) and not isinstance(page, bool) else None})
    return out


# ------------------------------------------------------------------------------------------ the document
def build(r: RunResult, *, result_code: str, app: Mapping[str, Any], gpu: Optional[Mapping[str, Any]] = None,
          budget_hits: Iterable[Mapping[str, Any]] = (), process: Optional[Mapping[str, Any]] = None,
          app_timings: Optional[Mapping[str, Any]] = None, warnings: Sequence[str] = (),
          missing_known: Optional[Iterable[str]] = None) -> Dict[str, Any]:
    """checkfacts.json of a finished check (no personal data, see the module docstring)."""
    rd = r.reading or {}
    by = rd.get("pages_by_engine") or {}
    stats = rd.get("engine_stats")
    known = known_missing_texts() if missing_known is None else set(missing_known)
    files = _file_index(r)
    return {
        "schema": SCHEMA,
        "app": cleaned(dict(app or {})) or {},
        "runner": {"version": safe_str(r.runner_version), "rules_bot": safe_str(r.base_rules_version),
                   "rules_runner": safe_str(r.runner_rules_version), "run_schema": safe_str(r.schema)},
        "result_code": safe_str(result_code),
        "check_date": safe_str(r.today),
        "program": safe_str(r.program),
        "mode": safe_str(r.mode),
        "overall": safe_str(r.overall),
        "headline_counts": headline_counts(r.headline),
        "pages": len(r.pages or ()),
        "files": len(files),
        "pages_by_engine": {k: count(by.get(k, 0)) for k in ("gpu", "cpu", "layer")},
        "ocr_seconds": page_seconds(r),
        "engine_stats": cleaned(stats) if isinstance(stats, Mapping) else None,
        "timings_s": {**(cleaned(dict(r.timings or {})) or {}), **(cleaned(dict(app_timings or {})) or {})},
        "budget_hits": budget_entries(budget_hits),
        "lines": doc_lines(r),
        "cross": cross_lines(r),
        "missing": missing_entries(r.missing_lines, known),
        "notices": notice_entries(r),
        "not_checked": not_checked_block(r),
        "process": cleaned(dict(process or {})) or {},
        "gpu": cleaned(dict(gpu)) if gpu else None,
        "warnings": [w for w in (safe_str(str(x), 120) for x in warnings or ()) if w],
    }


def build_error(*, code: str, stage: str, exc_type: str, app: Mapping[str, Any], check_date: Optional[str],
                program: Optional[str], gpu: Optional[Mapping[str, Any]] = None,
                budget_hits: Iterable[Mapping[str, Any]] = (), process: Optional[Mapping[str, Any]] = None,
                app_timings: Optional[Mapping[str, Any]] = None) -> Dict[str, Any]:
    """checkfacts.json of a check that stopped with an error (a crash or a card failure): the error code, the
    stage and the exception's type name (never its text), plus what is known of the card."""
    from .. import model as M
    return {
        "schema": SCHEMA,
        "app": cleaned(dict(app or {})) or {},
        "runner": {"version": M.RUNNER_VERSION, "rules_bot": M.BASE_RULES_VERSION,
                   "rules_runner": M.RUNNER_RULES_VERSION, "run_schema": M.RUN_SCHEMA},
        "error": {"code": safe_str(code) or "CRASH", "stage": safe_str(stage) or "?",
                  "type": safe_str(exc_type) or "?"},
        "check_date": safe_str(check_date),
        "program": safe_str(program),
        "budget_hits": budget_entries(budget_hits),
        "timings_s": cleaned(dict(app_timings or {})) or {},
        "process": cleaned(dict(process or {})) or {},
        "gpu": cleaned(dict(gpu)) if gpu else None,
    }


def to_text(facts: Mapping[str, Any]) -> str:
    return json.dumps(facts, ensure_ascii=True, indent=1, sort_keys=False) + "\n"


def write(facts: Mapping[str, Any], path: Path, assert_writable: Optional[Callable[[Path], None]] = None) -> Path:
    """Writes the facts as ASCII JSON. `assert_writable` (the guard's check) is called first; by default the
    runner's safety.assert_writable, looked up when needed."""
    path = Path(path)
    if assert_writable is None:
        assert_writable = importlib.import_module(f"{__package__.rsplit('.', 1)[0]}.safety").assert_writable
    assert_writable(path)
    path.write_text(to_text(facts), encoding="ascii")
    return path
