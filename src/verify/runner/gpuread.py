"""GPU pre-read worker (RUNNER_SPEC section 5.13, Task 8; owner decision A4).

Reads every page of one bundle PDF or one folder of bot-named files ON THE GRAPHICS CARD with the bot's own
engine and writes the page texts as `runner-page-texts/1` (RUNNER_SPEC 5.2.1), which the runner serves through
`--page-texts` instead of its CPU light engine. The engine is the worktree's doc_verifier (byte-identical to
the live bot's 07b1f22): `dv._ocr_reader()` (easyocr.Reader(["en"], gpu=True) on CUDA), driven through
`dv.read_pages` (200 dpi render for pages with < 40 text-layer characters) with its `_ocr_image` rotation
retries; image files through `dv.read_document`. This is the approach of
`private-runner\\test1\\tools\\read_gpu_pages.py`, which read CHECK_0001 under the guard.

It runs in the BOT's venv, read-only, as a separate process (RUNNER_SPEC 2.5):

    C:\\Hangeul\\BOT\\.venv\\Scripts\\python.exe -I -B <worktree>\\src\\verify\\runner\\gpuread.py ^
        "<bundle.pdf | folder>" <private-runner>\\runs\\<CASE>\\page_texts.json [--wait-s 120] [--slot-s 90]
    <runner venv python> -I -B <worktree>\\src\\verify\\runner\\gpuread.py ^
        --from-legacy <legacy pages.json> --source <PDF> --out <private-runner>\\...\\page_texts.json

Safety: the card is used only inside `gpu_guard.gpu_slot` (the bot's auto_verify.lock absent, the dev lock
held, enough free card memory) and under `gpu_guard.capped_process` with a torch memory cap of 3 GB;
`slot.checkpoint()` runs before every OCR page. Never DirectML. Writes go only under private-runner (plus
gpu_guard's own dev lock), the network is refused (`safety.install_write_guard`, S1/S4/S11). Nothing is
installed, nothing under C:\\Hangeul\\BOT is written, no other process is touched.

The output is rewritten atomically after every page with `"complete": false` until every page is read.
When the bot's check starts (SlotInterrupted) or the slot runs out (SlotExpired) the worker saves and exits 3,
releasing the card; run it again and the pages read in an earlier slot are reused (run_case.py does that).

Exit codes: 0 complete; 3 stopped at a checkpoint (run again to continue); 5 no GPU slot within --wait-s;
4 CUDA not available or the cap refused; 2 bad arguments; 1 crash (traceback in <OUT>.gpuread.log).
Prints counts and timings only: never page text, file names or paths. No personal data.
"""
from __future__ import annotations

import sys

sys.dont_write_bytecode = True

import argparse  # noqa: E402
import contextlib  # noqa: E402
import importlib.util  # noqa: E402
import json  # noqa: E402
import os  # noqa: E402
import time  # noqa: E402
import traceback  # noqa: E402
from pathlib import Path  # noqa: E402
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple  # noqa: E402

WORKTREE = Path(r"C:\Hangeul\JARVIS\prototype\page-types")
PRIVATE = Path(r"C:\Hangeul\DOCCHECK\lab\private-runner")
GPU_DATA = PRIVATE / "gpu-data"           # botimport cache root of the GPU worker (RUNNER_SPEC 2.5)
GUARD = Path(r"C:\Hangeul\DOCCHECK\lab\onnx\scripts\gpu_guard.py")
CAP_GB = 3.0                # gpu_guard.BOT_PC_MAX_CAP_GB on this PC
NEED_FREE_GB = 4.0          # the cap + the CUDA context + headroom (gpu_guard default)
WAIT_S = 120                # how long to wait for a GPU slot (run_case.py falls back to the CPU after it)
SLOT_S = 90.0               # gpu_guard.SLOT_MAX_S: one slot holds the card at most this long
LAYER_MIN = 40              # dv.read_pages: a page with fewer text-layer characters is read by OCR
SCHEMA = "runner-page-texts/1"            # == model.PAGE_TEXTS_SCHEMA (tested)
ENGINE_LABEL = f"bot EasyOCR on CUDA (gpu_guard, cap {CAP_GB:.1f} GB)"
LEGACY_ENGINE_LABEL = ENGINE_LABEL + "; converted from test1 read_gpu_pages.py output"
KINDS = ("layer", "ocr", "image")
EXIT_OK, EXIT_CRASH, EXIT_ARGS, EXIT_STOPPED, EXIT_NO_CUDA, EXIT_NO_SLOT = 0, 1, 2, 3, 4, 5
_GB = 1024 ** 3


# --------------------------------------------------------------------------------------------------------
# The other runner modules and the guard, behind one call each (tests replace these with fakes)

def _bootstrap() -> None:
    """Script mode (bot venv, -I): the worktree goes first on sys.path, so `src` is the worktree's."""
    if not sys.path or sys.path[0] != str(WORKTREE):
        sys.path.insert(0, str(WORKTREE))
    import src
    if not Path(src.__file__).resolve().is_relative_to(WORKTREE):
        raise RuntimeError("src is not imported from the worktree")


def _safety():
    from src.verify.runner import safety
    return safety


def _ingest():
    from src.verify.runner import ingest
    return ingest


def _botimport():
    from src.verify.runner import botimport
    return botimport


NO_CARD_ENV = "RUNNER_TEST_NO_CARD"     # set by the unit tests: a broken argument check can never reach the card


def _load_guard(path: Path = GUARD):
    """gpu_guard, loaded by path (RUNNER_SPEC 2.5). Reading the file is the only I/O. Refused when the
    RUNNER_TEST_NO_CARD environment variable is set (the tests' script runs: no dev lock, no nvidia-smi)."""
    if os.environ.get(NO_CARD_ENV):
        raise RuntimeError("the graphics card is off for this test run")
    spec = importlib.util.spec_from_file_location("gpu_guard", str(path))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _import_torch():
    import torch
    return torch


def _pymupdf():
    try:
        import pymupdf
    except ImportError:          # an older PyMuPDF only has the fitz name
        import fitz as pymupdf
    return pymupdf


# --------------------------------------------------------------------------------------------------------
# Small helpers

def under_private(p: Path) -> bool:
    """True when `p` resolves under C:\\Hangeul\\DOCCHECK\\lab\\private-runner (pure path test, no I/O)."""
    try:
        return Path(os.path.abspath(p)).is_relative_to(PRIVATE)
    except (OSError, ValueError):
        return False


def check_out(out: Path, input_path: Optional[Path] = None) -> Path:
    """The page-texts file path: absolute, under private-runner, a .json file name, not a folder, not inside
    the input. Raises ValueError (exit 2) otherwise. No I/O except an `is_dir` test."""
    out = Path(os.path.abspath(out))
    if not under_private(out):
        raise ValueError("the page-texts file must be under C:\\Hangeul\\DOCCHECK\\lab\\private-runner")
    if out.suffix.lower() != ".json":
        raise ValueError("the page-texts file must be a .json file")
    if out.is_dir():
        raise ValueError("the page-texts path is a folder")
    if input_path is not None:
        inp = Path(os.path.abspath(input_path))
        if out == inp or out.is_relative_to(inp):
            raise ValueError("the page-texts file must not lie inside the input")
    return out


def write_json(out: Path, data: Dict[str, Any]) -> None:
    """Atomic rewrite: OUT.tmp, then os.replace. Refuses any path outside private-runner."""
    out = Path(out)
    if not under_private(out):
        raise PermissionError("writes go only under private-runner")
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_name(out.name + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(tmp, out)


_SHA: Dict[Tuple[str, int, int], str] = {}


def _sha256(path: Path) -> str:
    """ingest.file_sha256 (the one definition), memoised per path, size and mtime."""
    st = os.stat(path)
    key = (str(path), st.st_size, st.st_mtime_ns)
    if key not in _SHA:
        _SHA[key] = _ingest().file_sha256(Path(path))
    return _SHA[key]


def layer_texts(path: Path) -> List[str]:
    """Each page's text layer, stripped, exactly as dv.read_pages sees it (the file is opened read-only)."""
    pm = _pymupdf()
    doc = pm.open(str(path))
    try:
        return [page.get_text().strip() for page in doc]
    finally:
        doc.close()


def _rec(page: int, text: str, kind: str, sideways: bool, seconds: float) -> Dict[str, Any]:
    return {"page": int(page), "text": text, "kind": kind, "sideways": bool(sideways),
            "seconds": round(float(seconds), 3)}


def _previous(done: Optional[Dict[str, Any]], name: str, sha: str, n_pages: int) -> Dict[int, Dict[str, Any]]:
    """The pages of this file read in an earlier slot: same name, sha256 and page count, else none."""
    if not isinstance(done, dict) or done.get("schema") != SCHEMA:
        return {}
    for e in done.get("files") or []:
        if not isinstance(e, dict):
            continue
        if e.get("name") == name and e.get("sha256") == sha and e.get("n_pages") == n_pages:
            out: Dict[int, Dict[str, Any]] = {}
            for r in e.get("pages") or []:
                p = r.get("page") if isinstance(r, dict) else None
                if (isinstance(p, int) and 1 <= p <= n_pages and isinstance(r.get("text"), str)
                        and r.get("kind") in KINDS):
                    out[p] = _rec(p, r["text"], r["kind"], bool(r.get("sideways")), r.get("seconds") or 0.0)
            return out
    return {}


def _close(result: Dict[str, Any]) -> None:
    result["complete"] = all(len(e["pages"]) == e["n_pages"] for e in result["files"])
    result["seconds_total"] = round(sum(p["seconds"] for e in result["files"] for p in e["pages"]), 2)


def counts(result: Dict[str, Any]) -> Dict[str, int]:
    """Pages by kind, plus the expected total (for the printed summary; no text)."""
    out = {"total": 0, "read": 0, "layer": 0, "ocr": 0, "image": 0}
    for e in result.get("files") or []:
        out["total"] += int(e.get("n_pages") or 0)
        for p in e.get("pages") or []:
            out["read"] += 1
            out[p.get("kind", "ocr")] = out.get(p.get("kind", "ocr"), 0) + 1
    return out


# --------------------------------------------------------------------------------------------------------
# The core loop (testable with a fake dv)

def read_all(src, dv, checkpoint: Callable[[], None], done: Dict[str, Any],
             save: Callable[[Dict[str, Any]], None]) -> Dict[str, Any]:
    """Read every page of `src` (a model.Source) and return the runner-page-texts/1 dict.

    Per PDF: dv.read_pages(path, max(20, n), sideways) with dv._ocr_image wrapped: checkpoint() before every
    OCR page (never for a text-layer page), pages already in `done` reused (resume; their sideways flag is
    replayed), save() after every page. Per image file: checkpoint(), then dv.read_document(path) once.
    On any exception (SlotInterrupted, SlotExpired, a crash) the partial dict (complete: false) is saved
    before the exception goes on. `done` is an earlier partial file of the same input, or {}."""
    done = done if isinstance(done, dict) else {}
    result: Dict[str, Any] = {"schema": SCHEMA, "complete": False, "engine": ENGINE_LABEL,
                              "mode": src.mode, "case_id": src.case_id, "files": [],
                              "slots": [dict(s) for s in (done.get("slots") or []) if isinstance(s, dict)],
                              "seconds_total": 0.0}
    plans = []
    for sf in src.files:
        sha = _sha256(sf.path)
        prev = _previous(done, sf.name, sha, sf.n_pages)
        entry = {"name": sf.name, "sha256": sha, "size": sf.size, "n_pages": sf.n_pages,
                 "pages": [prev[p] for p in sorted(prev)]}
        result["files"].append(entry)
        plans.append((sf, entry, prev))
    try:
        for sf, entry, prev in plans:
            if len(prev) >= sf.n_pages:
                continue                                  # every page read in an earlier slot
            if sf.is_image:
                _read_image(sf, entry, dv, checkpoint, save, result)
            else:
                _read_pdf(sf, entry, prev, dv, checkpoint, save, result)
    except BaseException:
        _close(result)
        save(result)
        raise
    _close(result)
    save(result)
    return result


def _read_image(sf, entry, dv, checkpoint, save, result) -> None:
    checkpoint()                                          # an image file is one OCR page
    t = time.perf_counter()
    text = dv.read_document(Path(sf.path))[0]             # the runner's engine reads images the same way
    entry["pages"] = [_rec(1, text, "image", False, time.perf_counter() - t)]
    _close(result)
    save(result)


def _read_pdf(sf, entry, prev, dv, checkpoint, save, result) -> None:
    layers = layer_texts(Path(sf.path))
    n = len(layers)
    if n != sf.n_pages:
        raise RuntimeError(f"the file has {n} page(s), the input listing said {sf.n_pages}")
    pages: Dict[int, Dict[str, Any]] = dict(prev)
    for p, t in enumerate(layers, 1):
        if len(t) >= LAYER_MIN and p not in pages:
            pages[p] = _rec(p, t, "layer", False, 0.0)

    def publish() -> None:
        entry["pages"] = [pages[p] for p in sorted(pages)]
        _close(result)

    publish()
    if len(pages) == n:
        save(result)
        return
    ocr_order = iter([p for p, t in enumerate(layers, 1) if len(t) < LAYER_MIN])
    real = dv._ocr_image

    def on_card(arr, turned=None):
        p = next(ocr_order, None)
        if p is None:
            raise RuntimeError("read_pages asked for more OCR pages than the text layer predicts")
        rec = pages.get(p)
        if rec is not None:                               # read in an earlier slot: reuse, replay the flag
            if rec["sideways"] and turned is not None:
                turned.append(True)
            return rec["text"]
        checkpoint()                                      # between pages: the bot's check started / slot used up
        here: List[bool] = []
        t0 = time.perf_counter()
        text = real(arr, here)                            # the bot's _ocr_image: upright read + rotation retries
        seconds = time.perf_counter() - t0
        if turned is not None:
            turned.extend(here)
        pages[p] = _rec(p, text, "ocr", bool(here), seconds)
        publish()
        save(result)
        return text

    dv._ocr_image = on_card
    sideways: List[int] = []
    try:
        texts = dv.read_pages(Path(sf.path), max(20, n), sideways)
    finally:
        dv._ocr_image = real
    if len(texts) != n:
        raise RuntimeError(f"read_pages returned {len(texts)} page(s) of {n}")
    for p, t in enumerate(texts, 1):
        rec = pages.get(p)
        if rec is None:
            raise RuntimeError(f"page {p} was not read")
        rec["text"] = t                                   # read_pages' own result is the page text
        if rec["kind"] == "ocr":
            rec["sideways"] = rec["sideways"] or (p in sideways)
    publish()


# --------------------------------------------------------------------------------------------------------
# The legacy conversion (no card)

def convert_legacy(legacy: Path, source: Path, out: Path) -> None:
    """test1\\tools\\read_gpu_pages.py output {"meta", "pages": {"1": {...}}} -> runner-page-texts/1, with the
    source PDF's sha256 added. The page count must agree with the PDF, and the legacy text-layer pages must
    be the PDF's text-layer pages (their text equal). Raises ValueError on a mismatch. No card."""
    out = check_out(out, source)
    d = json.loads(Path(legacy).read_text(encoding="utf-8"))
    pages = d.get("pages") if isinstance(d, dict) else None
    meta = (d.get("meta") or {}) if isinstance(d, dict) else {}
    if not isinstance(pages, dict) or not pages:
        raise ValueError("the legacy file holds no pages")
    ing = _ingest()
    src = ing.open_input(Path(source))
    if src.mode != "bundle" or len(src.files) != 1 or src.files[0].is_image:
        raise ValueError("the legacy source must be one PDF")
    sf = src.files[0]
    n = sf.n_pages
    if sorted(pages, key=lambda k: int(k) if str(k).isdigit() else -1) != [str(i) for i in range(1, n + 1)]:
        raise ValueError(f"the legacy read has {len(pages)} page(s), the PDF {n}")
    if meta.get("pages_in_pdf") not in (None, n):
        raise ValueError(f"the legacy read was made from a {meta.get('pages_in_pdf')}-page PDF, this one has {n}")
    layers = layer_texts(Path(sf.path))
    if len(layers) != n:
        raise ValueError("the PDF's page count changed while it was read")
    recs = []
    for i in range(1, n + 1):
        r = pages[str(i)]
        text = r.get("text") if isinstance(r, dict) else None
        if not isinstance(text, str):
            raise ValueError(f"legacy page {i} has no text")
        kind = "layer" if r.get("method") == "text_layer" else "ocr"
        pdf_layer = len(layers[i - 1]) >= LAYER_MIN
        if (kind == "layer") != pdf_layer or (kind == "layer" and text != layers[i - 1]):
            raise ValueError(f"legacy page {i} does not fit this PDF's text layer")
        seconds = r.get("seconds", r.get("gpu_seconds", 0.0)) or 0.0
        recs.append(_rec(i, text, kind, bool(r.get("sideways")), seconds))
    slots = []
    for s in meta.get("slots") or []:
        if isinstance(s, dict):
            slots.append({"slot": s.get("slot"), "waited_s": s.get("waited_s"),
                          "model_load_s": s.get("model_load_s"), "peak_allocated_gb": s.get("peak_allocated_gb"),
                          "peak_reserved_gb": s.get("peak_reserved_gb"),
                          "pages_read": len(s.get("pages_read") or []), "ended": s.get("ended")})
    result = {"schema": SCHEMA, "complete": True, "engine": LEGACY_ENGINE_LABEL, "mode": "bundle",
              "case_id": src.case_id,
              "files": [{"name": sf.name, "sha256": _sha256(sf.path), "size": sf.size, "n_pages": n,
                         "pages": recs}],
              "slots": slots, "seconds_total": 0.0}
    _close(result)
    write_json(out, result)


# --------------------------------------------------------------------------------------------------------
# One GPU session (testable with a fake guard, torch and dv)

def load_done(out: Path, src) -> Dict[str, Any]:
    """An earlier partial (or complete) page-texts file of the SAME input, else {} (a fresh start)."""
    try:
        d = json.loads(Path(out).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if (not isinstance(d, dict) or d.get("schema") != SCHEMA or d.get("case_id") != src.case_id
            or d.get("mode") != src.mode):
        return {}
    return d


def pending_pages(src, done: Dict[str, Any]) -> int:
    """Pages of `src` that `done` does not hold yet."""
    left = 0
    for sf in src.files:
        left += max(0, sf.n_pages - len(_previous(done, sf.name, _sha256(sf.path), sf.n_pages)))
    return left


def _never() -> None:
    raise RuntimeError("no page is left to read")


def run_session(src, out: Path, gg, dv, *, wait_s: float = WAIT_S, slot_s: float = SLOT_S,
                done: Optional[Dict[str, Any]] = None,
                torch_loader: Optional[Callable[[], Any]] = None,
                write: Optional[Callable[[Dict[str, Any]], None]] = None) -> int:
    """Take one GPU slot and read what is left. Returns the exit code (0, 3, 4 or 5; a crash raises)."""
    done = done if isinstance(done, dict) else {}
    write = write or (lambda d: write_json(out, d))
    if pending_pages(src, done) == 0:                    # complete from earlier slots: no card needed
        result = read_all(src, None, _never, done, write)
        _print_result(result, "already complete")
        return EXIT_OK
    slots_before = [dict(s) for s in (done.get("slots") or []) if isinstance(s, dict)]
    srec: Dict[str, Any] = {"slot": len(slots_before) + 1, "waited_s": None, "model_load_s": None,
                            "peak_allocated_gb": None, "peak_reserved_gb": None, "pages_read": 0,
                            "ended": None}
    start_read = sum(len(_previous(done, sf.name, _sha256(sf.path), sf.n_pages)) for sf in src.files)
    state: Dict[str, Any] = {"last": None, "in_slot": False, "peak": None}

    def save(d: Dict[str, Any]) -> None:
        peak = state["peak"]
        if peak is not None:
            srec["peak_allocated_gb"], srec["peak_reserved_gb"] = peak()
        srec["pages_read"] = max(0, counts(d)["read"] - start_read)
        d["slots"] = slots_before + [srec]
        state["last"] = d
        write(d)

    what = f"runner GPU pre-read {src.case_id}"
    t_wait = time.time()
    try:
        with gg.gpu_slot(what, need_free_gb=NEED_FREE_GB, wait_s=wait_s, max_s=slot_s) as slot:
            state["in_slot"] = True
            t_slot = time.time()
            srec["waited_s"] = round(t_slot - t_wait, 1)
            slot.check_cap(CAP_GB)
            with gg.capped_process():
                torch = (torch_loader or _import_torch)()
                if not torch.cuda.is_available():
                    srec["ended"] = "CUDA not available"
                    print("gpuread: CUDA is not available; nothing read")
                    return EXIT_NO_CUDA
                total = torch.cuda.get_device_properties(0).total_memory
                fraction = (CAP_GB * _GB) / total * 0.999
                torch.cuda.set_per_process_memory_fraction(fraction, 0)   # the guard refuses more than 3 GB
                t0 = time.perf_counter()
                reader = dv._ocr_reader()                                 # the bot's own EasyOCR reader
                srec["model_load_s"] = round(time.perf_counter() - t0, 2)
                device = str(getattr(reader, "device", "?"))
                srec["device"] = device
                if not device.startswith("cuda"):
                    srec["ended"] = "reader not on the card"
                    print(f"gpuread: the reader is on {device}, not the card; nothing read")
                    return EXIT_NO_CUDA
                state["peak"] = lambda: (round(torch.cuda.max_memory_allocated() / _GB, 3),
                                         round(torch.cuda.max_memory_reserved() / _GB, 3))
                result = read_all(src, dv, slot.checkpoint, done, save)
                srec["ended"] = "complete"
                srec["slot_seconds"] = round(time.time() - t_slot, 2)
                save(result)
    except (gg.SlotInterrupted, gg.SlotExpired) as e:
        srec["ended"] = type(e).__name__
        if state["last"] is not None:
            save(state["last"])
        _print_slots(slots_before + [srec])
        last = state["last"] or {"files": []}
        c = counts(last)
        print(f"gpuread: stopped at a checkpoint ({type(e).__name__}); pages read so far {c['read']}/"
              f"{sum(sf.n_pages for sf in src.files)}; run again to continue")
        return EXIT_STOPPED
    except gg.UncappedCardWork:
        print(f"gpuread: the guard refused the {CAP_GB:.1f} GB card-memory cap; nothing read")
        return EXIT_NO_CUDA
    except TimeoutError:
        if state["in_slot"]:
            raise
        print(f"gpuread: no GPU slot within {wait_s:g} s (the bot's check, another dev run or too little "
              "free card memory); nothing read")
        return EXIT_NO_SLOT
    except BaseException as e:
        if state["in_slot"]:
            srec["ended"] = f"error: {type(e).__name__}"
            if state["last"] is not None:
                with contextlib.suppress(Exception):
                    save(state["last"])
        raise
    _print_slots(slots_before + [srec])
    _print_result(result, "complete")
    return EXIT_OK


def _print_slots(slots: Sequence[Dict[str, Any]]) -> None:
    for s in slots:
        print(f"slot {s.get('slot')}: waited {s.get('waited_s')} s, model load {s.get('model_load_s')} s, "
              f"pages {s.get('pages_read')}, peak allocated {s.get('peak_allocated_gb')} GB, "
              f"peak reserved {s.get('peak_reserved_gb')} GB, ended {s.get('ended')}")


def _print_result(result: Dict[str, Any], how: str) -> None:
    c = counts(result)
    print(f"gpuread: {how}; files {len(result.get('files') or [])}; pages {c['read']}/{c['total']} "
          f"(layer {c['layer']}, ocr {c['ocr']}, image {c['image']}); GPU OCR {result.get('seconds_total')} s")


# --------------------------------------------------------------------------------------------------------
# Command line

class _Parser(argparse.ArgumentParser):
    def error(self, message):                     # no SystemExit inside main(): exit code 2 by return
        raise ValueError(message)


def _parser() -> argparse.ArgumentParser:
    ap = _Parser(prog="gpuread.py", description="GPU pre-read of a bundle PDF or folder (RUNNER_SPEC 5.13). "
                 "Exit 0 complete, 3 stopped at a checkpoint (run again), 5 no GPU slot, 4 no CUDA or cap "
                 "refused, 2 bad arguments, 1 crash.")
    ap.add_argument("input", nargs="?", help="the bundle PDF or the folder of bot-named files")
    ap.add_argument("page_texts", nargs="?", help="the page-texts JSON to write (under private-runner)")
    ap.add_argument("--wait-s", type=float, default=WAIT_S, help="longest wait for a GPU slot (s)")
    ap.add_argument("--slot-s", type=float, default=SLOT_S, help="longest one slot may hold the card (s)")
    ap.add_argument("--from-legacy", help="convert a test1 read_gpu_pages.py output instead (no card)")
    ap.add_argument("--source", help="--from-legacy: the PDF the legacy read was made from")
    ap.add_argument("--out", help="--from-legacy: the page-texts JSON to write (under private-runner)")
    return ap


def _parse(argv: Sequence[str]) -> argparse.Namespace:
    a = _parser().parse_args(list(argv))
    if a.from_legacy:
        if a.input or a.page_texts or not a.source or not a.out:
            raise ValueError("--from-legacy needs --source and --out and no positional arguments")
        a.out_path = check_out(Path(a.out), Path(a.source))
        _input_ok(Path(a.source))
    else:
        if not a.input or not a.page_texts or a.source or a.out:
            raise ValueError("give the input and the page-texts JSON")
        if not (0 <= a.wait_s <= 24 * 3600) or not (0 < a.slot_s <= SLOT_S):
            raise ValueError(f"--wait-s must be 0..86400 and --slot-s 0..{SLOT_S:g}")
        a.out_path = check_out(Path(a.page_texts), Path(a.input))
        _input_ok(Path(a.input))
    return a


def _input_ok(p: Path) -> None:
    """The input, resolved (junctions, device and local-share spellings), must not lie under C:\\Hangeul\\BOT
    (safety.check_input). Raises ValueError (exit 2)."""
    try:
        _safety().check_input(Path(os.path.abspath(p)))
    except Exception as e:
        raise ValueError(f"the input was refused ({type(e).__name__}: {_safety().redact(e)})") from None


def _crash_log(out: Optional[Path]) -> None:
    if out is None or not under_private(out):
        return
    with contextlib.suppress(Exception):
        log = out.with_name(out.stem + ".gpuread.log")
        log.write_text(traceback.format_exc(), encoding="utf-8")


def main(argv: Optional[Sequence[str]] = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if any(x in ("-h", "--help") for x in argv):
        _parser().print_help()
        return EXIT_OK
    try:
        a = _parse(argv)
    except ValueError as e:
        print(f"gpuread: {_safety().redact(e)}", file=sys.stderr)
        return EXIT_ARGS
    out = a.out_path
    try:
        if a.from_legacy:
            _safety().install_write_guard(block_bot_opens=True, extra_forbidden=[Path(os.path.abspath(a.source))])
            ing = _ingest()
            try:
                convert_legacy(Path(a.from_legacy), Path(os.path.abspath(a.source)), out)
            except (ValueError, OSError, ing.InputError) as e:
                print(f"gpuread: the legacy read does not fit: {type(e).__name__}: {_safety().redact(e)}",
                      file=sys.stderr)
                return EXIT_ARGS
            print(f"gpuread: converted the legacy read ({counts(json.loads(out.read_text(encoding='utf-8')))['read']} "
                  "pages)")
            return EXIT_OK
        gg = _load_guard()
        # S1, S4, S11: writes only under private-runner (+ the dev lock), no network, temp under private-runner
        _safety().install_write_guard(extra_allowed=[Path(gg.DEV_LOCK)], block_bot_opens=False,
                                      extra_forbidden=[Path(os.path.abspath(a.input))])
        bot = _botimport().load(GPU_DATA)        # the worktree's doc_verifier with the stub src.config
        ing = _ingest()
        try:
            src = ing.open_input(Path(os.path.abspath(a.input)))
        except ing.InputError as e:
            print(f"gpuread: bad input: {_safety().redact(e)}", file=sys.stderr)
            return EXIT_ARGS
        done = load_done(out, src)
        return run_session(src, out, gg, bot.dv, wait_s=a.wait_s, slot_s=a.slot_s, done=done)
    except Exception as e:
        _crash_log(out)
        print(f"gpuread: crashed ({type(e).__name__}); the traceback is in the .gpuread.log next to the output",
              file=sys.stderr)
        return EXIT_CRASH


if __name__ == "__main__":
    _bootstrap()
    sys.exit(main())
