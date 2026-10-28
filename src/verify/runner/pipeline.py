"""The runner's only orchestrator (RUNNER_SPEC section 5, revision 2): input -> pages -> page types -> split ->
birth certificate -> identity -> documents, bank, TIN, financial chain, names -> verdicts -> report.

    run(input_path, program, out, today=..., cache_root=..., page_texts=None, reading_note="",
        reread_mrz=True, split_only=False) -> RunResult

Stage modules are looked up by name when the run needs them (importlib, through sys.modules), so the unit
tests can stand fakes in for every stage. Errors:
  * RefusedError  bad arguments, an unsafe path, page texts that do not belong to the input -> exit 2,
                  nothing written in --out;
  * RunCrashed    anything else after the out folder was accepted -> exit 1; log.txt holds the traceback and
                  run.json holds {"error": ...} with whatever finished.
A STOP (the NID-person ambiguity only) returns a RunResult whose overall is STOP (exit 3 in __main__).
No personal data in this file; progress lines on stderr name stages and seconds only.
"""
from __future__ import annotations

import contextlib
import dataclasses
import datetime as dt
import importlib
import inspect
import sys
import time
import traceback
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, Iterator, List, Optional, Sequence, Tuple

from . import hooks, places
from . import report as REPORT
from . import verdict as V
from .model import (BASE_RULES_VERSION, NOTE, PROGRAMS, RUN_SCHEMA, RUNNER_RULES_VERSION, RUNNER_VERSION,
                    DerivedRecord, DocUnit, Finding, Notice, PageRead, RunResult, set_page_numbering)

VERBOSE = True                                   # progress lines on stderr (stage names and seconds only)
OWNER_FINES = ("trade_license", "employment_certificate")
BANK_IMAGE_DPI = 150                             # bank.ink_signatures is calibrated at this resolution
CHAIN_RULE_PREFIXES = ("financial.", "bank.holder")


class RefusedError(Exception):
    """Exit 2: bad arguments, an unsafe path, or page texts that do not belong to the input. Nothing written."""


class RunCrashed(Exception):
    """Exit 1: the run crashed after --out was accepted; log.txt and run.json {"error": ...} were written."""


# ------------------------------------------------------------------------------------------ plumbing
class _Modules:
    """The stage modules, imported by name on first use (sys.modules first: the tests' fakes win)."""

    def __getattr__(self, name: str):
        mod = importlib.import_module(f"{__package__}.{name}")
        setattr(self, name, mod)
        return mod


def _isinstance_of(e: BaseException, module: str, cls: str) -> bool:
    mod = sys.modules.get(f"{__package__}.{module}")
    c = getattr(mod, cls, None) if mod is not None else None
    return isinstance(c, type) and isinstance(e, c)


class _Clock:
    def __init__(self) -> None:
        self.t: Dict[str, float] = {}
        self.stage = ""

    @contextlib.contextmanager
    def __call__(self, key: str) -> Iterator[None]:
        self.stage = key
        hooks.emit("stage", key=key, state="start")       # the app's progress and cancel point (no-op in DEV)
        t0 = time.perf_counter()
        try:
            yield
        finally:
            dt_s = time.perf_counter() - t0
            self.t[key] = round(self.t.get(key, 0.0) + dt_s, 3)
            if VERBOSE:
                print(f"[runner] {key} {dt_s:.1f}s", file=sys.stderr, flush=True)
            hooks.emit("stage", key=key, state="end", seconds=round(dt_s, 3))   # the listener never raises here


@dataclass
class _Run:
    out: Path
    clock: _Clock
    log: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    partial: Dict[str, Any] = field(default_factory=dict)
    wrote: bool = False
    state: Any = None


def unit_text(u: DocUnit, pages: Sequence[PageRead]) -> str:
    """The unit's 200 dpi page texts joined as dv.read_document joins them ("\\n")."""
    return "\n".join(pages[i].text for i in u.pages)


def call_flex(fn: Callable[..., Any], **available: Any) -> Any:
    """Call fn with the keyword arguments it names, taken from `available` (for an interface the spec leaves
    open, e.g. financial.chain_person_reads(...)). A required parameter that is not available -> TypeError."""
    args: Dict[str, Any] = {}
    for name, p in inspect.signature(fn).parameters.items():
        if p.kind in (p.VAR_POSITIONAL, p.VAR_KEYWORD):
            continue
        if name in available:
            args[name] = available[name]
        elif p.default is p.empty:
            raise TypeError(f"{getattr(fn, '__name__', 'function')}() needs '{name}', which the pipeline lacks")
    return fn(**args)


def call_opt(fn: Callable[..., Any], *args: Any, **optional: Any) -> Any:
    """fn(*args, **the optional keyword arguments its signature names): extras a stage offers beyond the
    section-5 signature (e.g. doccheck's source_pdf) are passed when the stage has them, left out otherwise."""
    try:
        params = inspect.signature(fn).parameters
    except (TypeError, ValueError):
        return fn(*args)
    if any(p.kind == p.VAR_KEYWORD for p in params.values()):
        return fn(*args, **optional)
    return fn(*args, **{k: v for k, v in optional.items() if k in params})


def pages_by_engine(pages: Sequence[PageRead]) -> Dict[str, int]:
    by = {"gpu": 0, "cpu": 0, "layer": 0}
    for p in pages:
        e = p.engine or ("layer" if p.kind == "layer" else "cpu")
        by[e] = by.get(e, 0) + 1
    return by


# ------------------------------------------------------------------------------------------ the run
def run(input_path: Path, program: str, out: Path, *, today: dt.date, cache_root: Path,
        page_texts: Optional[Path] = None, reading_note: str = "", reread_mrz: bool = True,
        split_only: bool = False) -> RunResult:
    """Run every stage on one bundle PDF or one folder (RUNNER_SPEC section 5) and write the three outputs."""
    input_path, out, cache_root = Path(input_path), Path(out), Path(cache_root)
    program = (program or "").upper()
    if program not in PROGRAMS:
        raise RefusedError(f"--program must be one of {', '.join(PROGRAMS)}")
    if not input_path.exists():
        raise RefusedError("the input does not exist")
    if page_texts is not None and not Path(page_texts).is_file():
        raise RefusedError("the --page-texts file does not exist")
    m = _Modules()
    rs = _Run(out=out, clock=_Clock())
    rs.log += [f"runner {RUNNER_VERSION} | rules bot {BASE_RULES_VERSION} + runner {RUNNER_RULES_VERSION} | "
               f"schema {RUN_SCHEMA}", f"started {dt.datetime.now().isoformat(timespec='seconds')}",
               f"input {input_path}", f"out {out}", f"program {program} | today {today.isoformat()}",
               f"page texts {page_texts or '-'} | re-read MRZ {reread_mrz} | split only {split_only}"]
    try:
        with rs.clock("safety"):
            rs.state = m.safety.prepare(out, cache_root, input_path)
    except Exception as e:
        if _isinstance_of(e, "safety", "SafetyError"):
            raise RefusedError(str(e)) from e
        raise
    try:
        return _run(m, rs, input_path, program, out, today=today, cache_root=cache_root, page_texts=page_texts,
                    reading_note=reading_note, reread_mrz=reread_mrz, split_only=split_only)
    except RefusedError:
        raise
    except Exception as e:
        if not rs.wrote and (_isinstance_of(e, "ingest", "InputError") or _isinstance_of(e, "safety", "SafetyError")):
            raise RefusedError(str(e)) from e
        _crash(m, rs, e)
        # the message reaches stderr: the exception's type and the stage only (its text may hold a value read
        # off a document); log.txt and run.json keep the whole text
        raise RunCrashed(f"{type(e).__name__} in stage '{rs.clock.stage}'") from e
    finally:
        set_page_numbering(None)


def _run(m: _Modules, rs: _Run, input_path: Path, program: str, out: Path, *, today: dt.date, cache_root: Path,
         page_texts: Optional[Path], reading_note: str, reread_mrz: bool, split_only: bool) -> RunResult:
    clock = rs.clock
    with clock("ingest"):
        src = m.ingest.open_input(input_path)
    not_checked = [tuple(x) for x in (getattr(src, "not_checked", None) or [])]
    rs.partial.update(case_id=src.case_id, mode=src.mode, input=str(input_path), program=program,
                      today=today.isoformat(), not_checked=not_checked)
    given = None
    if page_texts is not None:                    # before the engine loads: a foreign file refuses fast (exit 2)
        with clock("engine"):
            given = m.engine.load_page_texts(Path(page_texts), src)
    with clock("engine"):
        eng = m.engine.Engine(cache_root)
        eng.set_today(today)
    docs_dir, work_dir = out / "docs", out / "work"
    for d in (out, docs_dir, work_dir):
        m.safety.assert_writable(d)
    rs.wrote = True
    for d in (docs_dir, work_dir):
        d.mkdir(parents=True, exist_ok=True)

    extra: Dict[str, Any] = {"names_rereads": [], "tin": {}}
    bank_out: Dict[str, Any] = {}
    with eng.case(src.case_id):
        with clock("stage"):
            staged = m.ingest.stage(src, docs_dir)
        with clock("ocr"):
            pages: List[PageRead] = list(eng.read_source(src, staged, given))
        rs.partial["pages"] = pages
        set_page_numbering(pages)             # findings name the page inside its file (folder mode)
        turned: List[Dict[str, Any]] = []
        turn = getattr(m.pagetype, "turn_junk_pages", None)
        read_turned = getattr(eng, "read_turned", None)
        if callable(turn) and callable(read_turned):  # rev 2.5: a sideways page read as junk is read turned
            with clock("turn"):
                turned = list(call_opt(turn, pages, read_turned,
                                       line_ratio=getattr(eng, "line_ratio", None)) or [])
        extra["turned"] = turned
        with clock("typing"):
            types = list(m.pagetype.type_pages(pages))
        rs.partial["types"] = types
        with clock("segment"):
            seg = (m.split.segment(types, program) if src.mode == "bundle"
                   else m.split.segment_folder(src, pages, types))
        confirm = m.names.make_confirmer(eng, pages, work_dir)
        with clock("birth"):
            prov = list(m.split.provisional_units(seg, pages))
            births = {}
            for u in prov:
                if u.key == "birth_cert":
                    births[tuple(u.pages)] = m.birth.read_birth_certificate(u, pages, eng, work_dir,
                                                                            eng.bot.dv.notarised)
        rs.partial["births"] = list(births.values())
        with clock("identity"):
            ident = m.identity.build_identity(prov, pages, births, eng if reread_mrz else None, work_dir, confirm)
        rs.partial["identity"] = ident
        with clock("split"):
            res = m.split.finish(seg, types, pages, m.identity.refs(ident), program)
            _turned_notice(m, res, turned, src.mode)
        rs.partial["split"] = res
        with clock("identity"):
            ident = m.identity.finish_identity(ident, res.units, pages, births, confirm)
        rs.partial["identity"] = ident

        if res.stop:                                      # the NID-person ambiguity: notices only, no cut
            with clock("verdict"):
                notices = sorted(res.notices, key=lambda n: (n.pages[0], n.pages[1]))
                docs, cross, missing = [], [], []
                overall, headline = V.overall(docs, missing, res, cross, notices)
            rs.log.append("STOP: the persons on the NID pages could not be told apart; nothing was cut")
        elif split_only:
            with clock("verdict"):
                notices = sorted(res.notices, key=lambda n: (n.pages[0], n.pages[1]))
                missing = V.missing_lines(res, program, ())
                docs, overall, headline = V.split_only(res, missing, notices)
                cross = []
        else:
            with clock("cut"):
                _cut_and_attach(m, eng, src, res, pages, staged, docs_dir)
            rec = DerivedRecord(program, today, ident)
            with clock("names"):
                aff = m.names.affidavit_info(res.units, pages)

            def cov(a: Any, b: Any) -> bool:
                return bool(m.names.covered(aff, a, b))

            with clock("bot"):
                dc = call_opt(m.doccheck.check_documents, res.units, pages, rec, eng, src.mode, aff, births,
                              confirm, work_dir=work_dir, source_pdf=src.root if src.mode == "bundle" else None)
            bank_map: Dict[str, List[Finding]] = {}
            with clock("bank"):
                def solvency_pdf(i: int) -> Optional[Path]:
                    return eng.cut_page(pages[i], work_dir)

                def page_image(i: int) -> Any:                 # rev 2.5: the handwritten-signature look
                    return eng.page_image_of(pages[i], BANK_IMAGE_DPI)

                for u in res.units:
                    if u.key == "bank":
                        o = call_opt(m.bank.check_bank_unit, u, [pages[i].text for i in u.pages], list(u.pages),
                                     ident, cov, confirm, solvency_pdf, page_image=page_image)
                        bank_out[u.uid] = o
                        bank_map[u.uid] = list(o.findings)
                _bank_files(res.units, bank_out, bank_map)
                if src.mode == "folder":
                    _split_bank_files(m, res.units, pages, bank_out, bank_map, dc.per_unit)
            tin_map: Dict[str, List[Finding]] = {}
            tin_raw: Dict[str, Any] = {}
            with clock("tin"):
                for u in res.units:
                    if u.key != "trade_license" or u.path is None:
                        continue
                    ptypes = list(m.tin.tin_page_types(u, types) or [])
                    if u.fine != "tin_certificate" and "tin" not in ptypes:
                        continue
                    result = call_opt(m.tin.check_tin_certificate, u.path, unit_text(u, pages), ptypes,
                                      confirm=confirm, page_texts=[pages[i].text for i in u.pages],
                                      run_pages=list(u.pages))
                    tin_raw[u.uid] = result
                    tin_map[u.uid] = list(m.tin.tin_findings(result, u, pages))
            extra["tin"] = tin_raw
            with clock("financial"):
                chain_map, itax_map, chain_reads = _financial(m, rs, res.units, pages, tin_raw, bank_out, ident,
                                                              cov, confirm, program)
            extra_reads = [tuple(x) for o in bank_out.values() for x in (getattr(o, "person_reads", None) or ())]
            extra_reads += [tuple(x) for x in chain_reads]
            extra_reads += [tuple(x) for x in (getattr(dc, "person_reads", None) or ())]
            with clock("names"):
                nres = m.names.evaluate(ident, res.units, pages, aff, dc.machine, births, confirm, extra_reads)
            extra["names_rereads"] = list(getattr(nres, "rereads", None) or [])
            with clock("verdict"):
                chain_sum = V.chain_summary(chain_map, bank_map, prefixes=CHAIN_RULE_PREFIXES)
                docs, cross, notices = V.merge(res, dc.per_unit, bank_map, tin_map, chain_map, itax_map,
                                               names=nres, chain_summary=chain_sum, bundle=dc.bundle,
                                               program=program)
                missing = V.missing_lines(res, program, list(bank_out.values()))
                overall, headline = V.overall(docs, missing, res, cross, notices)
        reading = {"pages_by_engine": pages_by_engine(pages), "note": reading_note,
                   "page_texts": str(page_texts) if page_texts else ""}
        try:
            reading["engine_stats"] = eng.stats()
        except Exception as e:                          # statistics only: never fail a run for them
            rs.warnings.append(f"engine.stats() failed: {type(e).__name__}")

    with clock("report"):
        safety_info = _finish_safety(m, rs)
        r = RunResult(schema=RUN_SCHEMA, runner_version=RUNNER_VERSION, base_rules_version=BASE_RULES_VERSION,
                      runner_rules_version=RUNNER_RULES_VERSION, case_id=src.case_id, mode=src.mode,
                      input=str(input_path), program=program, today=today.isoformat(), reading=reading, pages=pages,
                      types=types, split=res, identity=ident, births=list(births.values()), docs=docs, cross=cross,
                      notices=notices, missing_lines=list(missing), overall=overall, headline=headline,
                      timings=clock.t, safety=safety_info, not_checked=not_checked)
        REPORT.write_all(r, out, extra)
    rs.log.append(f"result {overall} | {headline}")
    _write_log(m, rs, r)
    return r


def _turned_notice(m: _Modules, res: Any, turned: Sequence[Dict[str, Any]], mode: str) -> None:
    """Rev 2.5: one NOTE notice naming every page read turned (scanned sideways or upside down)."""
    fn = getattr(m.pagetype, "turned_note", None)
    msg = fn(turned, mode) if callable(fn) else ""
    if not msg or res is None:
        return
    done = [int(r["page"]) for r in turned if r.get("angle")]
    res.notices.append(Notice(kind=NOTE, pages=(min(done) + 1, max(done) + 1), message=msg,
                              units=tuple(u.uid for u in res.units if any(i in u.pages for i in done))))


def _bank_files(units: List[DocUnit], bank_out: Dict[str, Any], bank_map: Dict[str, List[Finding]]) -> None:
    """Folder mode: each bank file is judged on its own. A file with no solvency certificate (a statement) while
    another bank file holds one is not "solvency missing": its row says where the certificate is."""
    have = [uid for uid, o in bank_out.items() if getattr(o, "solvency_pages", None)]
    if not have:
        return
    by = {u.uid: u for u in units}
    where = by[have[0]].source_file or by[have[0]].title if have[0] in by else "another bank file"
    for uid, o in bank_out.items():
        if not getattr(o, "solvency_missing", False):
            continue
        o.solvency_missing = False
        bank_map[uid] = [dataclasses.replace(f, message=f"no solvency certificate in this file; the solvency "
                                                        f"certificate is in {where}")
                         if f.rule == "bank.solvency_present" else f for f in bank_map.get(uid, [])]


ONE_PAGE_BOT_TEXT = "the bank file has only one page"
SPLIT_BANK_TEXT = ("one page in this file: the solvency certificate and the statement are separate bank files "
                   "({files}), judged together")


def _split_bank_files(m: _Modules, units: List[DocUnit], pages: List[PageRead], bank_out: Dict[str, Any],
                      bank_map: Dict[str, List[Finding]], per_unit: Dict[str, List[Finding]]) -> None:
    """Folder mode, two or more bank files: the bank document is the files together (review 2). When one file
    holds the solvency certificate and another a statement page, each one-page file's bot row "the bank file has
    only one page, so it cannot hold both ..." becomes a NOTE naming the files (the owner's two-page rule holds
    across them), and bank.dates compares the certificate's date with the statement in the other file (judged
    alone, neither file could). No certificate, or no statement page in any file: nothing changes."""
    banks = [u for u in units if u.key == "bank" and u.uid in bank_out and u.pages]
    if len(banks) < 2:
        return
    run_pages = [i for u in banks for i in u.pages]
    texts = [pages[i].text or "" for i in run_pages]
    sol = [k for k, t in enumerate(texts) if m.bank.is_solvency_page(t)]
    rest = [k for k in range(len(texts)) if k not in sol]
    if not sol or not rest:
        return
    files = ", ".join(dict.fromkeys(u.source_file or u.title for u in banks))
    note = SPLIT_BANK_TEXT.format(files=files)
    for u in banks:
        per_unit[u.uid] = [dataclasses.replace(f, verdict=NOTE, message=note)
                           if f.message.startswith(ONE_PAGE_BOT_TEXT) else f for f in per_unit.get(u.uid, [])]
    home = next(u for u in banks if run_pages[sol[0]] in u.pages)
    rows = [dataclasses.replace(f, evidence=dict(f.evidence or {}, run_pages=[run_pages[sol[0]], run_pages[rest[0]]],
                                                 files=files))
            for f in m.bank.dates_rows(texts, run_pages)]
    for u in banks:
        bank_map[u.uid] = [f for f in bank_map.get(u.uid, []) if f.rule != "bank.dates"]
    bank_map[home.uid] = bank_map.get(home.uid, []) + rows


def _cut_and_attach(m: _Modules, eng: Any, src: Any, res: Any, pages: List[PageRead], staged: Dict[str, Path],
                    docs_dir: Path) -> None:
    """Bundle: cut every unit, attach the paths, prime every cut (one read per page, always), then crop the photo
    (doccheck.crop_photo; the photo unit's path becomes the crop, or None when no photo edge was found).
    Folder: attach the staged copies (no priming: the bot reads each file as it does on folders). A unit that is
    one of several documents of its file (DocUnit.part, revision 2.3) gets its own cut of the staged copy, so its
    checks read only its pages. Revision 2.4: that cut is NOT primed either. The bot reads it as it reads every
    folder file (its own read, the whole-file read_document included); priming it with the run's page texts took
    that read away from the bot's rules for parts only (owner folder 3: the bank balance the bot's own read prints
    in full, the run's page read splits, so the part's balance became a FLAG while the uncut file had PASSed)."""
    if src.mode != "bundle":
        m.split.attach_paths(res, staged)
        _cut_parts(m, eng, res, pages, staged, docs_dir / "parts")
        return
    paths = eng.cut(src.root, m.split.cut_plan(res), docs_dir)
    m.split.attach_paths(res, paths)
    for u in res.units:
        if u.path is not None and u.pages:
            eng.prime(u.path, [pages[i].text for i in u.pages])
    for u in res.units:
        if u.key != "photo" or not u.pages:
            continue
        whole = getattr(m.doccheck, "crop_unit_photo", None)
        if whole is not None:                  # sets u.path to the crop, or keeps it marked "no edge found"
            whole(eng, u, pages, src.root, docs_dir)
            continue
        target = u.path if (u.path is not None and u.path.suffix.lower() in (".jpg", ".jpeg")) \
            else docs_dir / f"PASSPORT SIZE PHOTO p{u.pages[0] + 1:02d}.jpg"
        img = eng.page_image_of(pages[u.pages[0]], 300)
        u.path = m.doccheck.crop_photo(eng, img, target)


def _cut_parts(m: _Modules, eng: Any, res: Any, pages: List[PageRead], staged: Dict[str, Path],
               parts_dir: Path) -> None:
    """Folder mode (revision 2.3): cut every part unit out of its staged file (pages inside the file) and attach the
    cut. The cut's name is split.unit_file_name(u) (run pages: unique in the run, and dv.classify of it gives the
    unit's key back). Revision 2.4: the cut is not primed (the bot reads it itself, as every folder file)."""
    by_file: Dict[str, List[DocUnit]] = {}
    for u in res.units:
        if getattr(u, "part", "") and u.pages and u.source_file in staged:
            by_file.setdefault(u.source_file, []).append(u)
    for fname, us in by_file.items():
        plan = [(m.split.unit_file_name(u), int(pages[u.pages[0]].file_page), int(pages[u.pages[-1]].file_page))
                for u in us]
        paths = list(eng.cut(staged[fname], plan, parts_dir))
        if len(paths) != len(us):
            raise ValueError(f"{len(paths)} cut files for {len(us)} parts of one file")
        for u, p in zip(us, paths):
            u.path = Path(p)


def _financial(m: _Modules, rs: _Run, units: List[DocUnit], pages: List[PageRead], tin_raw: Dict[str, Any],
               bank_out: Dict[str, Any], ident: Any, cov: Callable[[Any, Any], bool],
               confirm: Callable[[int, str], str], program: str
               ) -> Tuple[Dict[str, List[Finding]], Dict[str, List[Finding]], List[Tuple[str, str, str, int]]]:
    """financial.chain_findings (taxpayer = holder = owner/employee; the TIN QR first) and
    financial.income_tax_findings for every income-tax unit."""
    by = {u.uid: u for u in units}
    tin_units = [by[uid] for uid in tin_raw if uid in by]
    owner_units = [u for u in units if u.key == "trade_license" and (
        u.fine in OWNER_FINES or not u.fine or any(len(s) > 2 and s[2] in OWNER_FINES for s in u.sub))]
    taxpayer: Dict[str, Any] = {}
    tin_unit: Optional[DocUnit] = None
    from_tin = getattr(m.financial, "taxpayer_from_tin", None)
    for u in tin_units:
        res_t = tin_raw.get(u.uid) or {}
        if from_tin is not None:               # the TIN QR first (A6 i), with the run pages of its reads
            tp = dict(from_tin(res_t, u) or {})
        else:
            tp = dict(m.financial.tin_taxpayer(unit_text(u, pages), list(res_t.get("all_codes") or [])) or {})
        if tin_unit is None or (tp.get("name") and not taxpayer.get("name")):
            taxpayer, tin_unit = tp, u
        if taxpayer.get("name"):
            break
    holder, bank_unit = None, None
    for uid, o in bank_out.items():
        if bank_unit is None:
            bank_unit = by.get(uid)
        if getattr(o, "holder", None) is not None:
            holder, bank_unit = o.holder, by.get(uid)
            break
    chain_map: Dict[str, List[Finding]] = {}
    reads: List[Tuple[str, str, str, int]] = []
    if tin_unit is not None or owner_units:
        owner_texts = {u.uid: [pages[i].text for i in u.pages] for u in owner_units}   # per page: reads keep pages
        got = m.financial.chain_findings(taxpayer, tin_unit, owner_units, owner_texts, holder, bank_unit, ident, cov,
                                         confirm) or {}
        chain_map = {uid: list(fs) for uid, fs in got.items()}
        fn = getattr(m.financial, "chain_person_reads", None)
        if fn is not None:
            try:
                reads = list(call_flex(fn, taxpayer=taxpayer, tin_unit=tin_unit, owner_units=owner_units,
                                       owner_texts=owner_texts, holder=holder, bank_unit=bank_unit, ident=ident,
                                       covered=cov, confirm=confirm, units=units, pages=pages,
                                       chain=chain_map) or [])
            except TypeError as e:
                rs.warnings.append(f"financial.chain_person_reads not called ({e}); the dot rule runs without "
                                   "the chain's reads")
    itax_map: Dict[str, List[Finding]] = {}
    tin_text = ""
    if tin_unit is not None:                 # only the TIN certificate's own pages and section (a trade licence's
        ptypes = list((tin_raw.get(tin_unit.uid) or {}).get("page_types") or [])   # "Zone" is not the tax zone)
        own = [i for k, i in enumerate(tin_unit.pages) if k < len(ptypes) and ptypes[k] == "tin"]
        text = "\n".join(pages[i].text for i in own) if own else unit_text(tin_unit, pages)
        sec = getattr(m.tin, "tin_section", None)
        tin_text = sec(text) if callable(sec) else text
    for u in units:
        if u.key == "income_tax":
            itax_map[u.uid] = list(m.financial.income_tax_findings(u, unit_text(u, pages), tin_text, program) or [])
    return chain_map, itax_map, reads


def _finish_safety(m: _Modules, rs: _Run) -> Dict[str, Any]:
    """safety.finish(state) (re-snapshots the input; raises SafetyError on any change), recorded for the report.
    Called before the report is written, so the report shows the snapshot after the run."""
    fin = m.safety.finish(rs.state)
    info: Dict[str, Any] = dict(fin) if isinstance(fin, dict) else {"finish": fin}
    st = rs.state
    info.setdefault("blocked", list(getattr(st, "blocked", None) or []))
    info.setdefault("bot_opens", getattr(st, "bot_opens", 0))
    if "snapshot_before" not in info and getattr(st, "snapshots", None) is not None:
        info["snapshot_before"] = st.snapshots
    return info


def _write_log(m: _Modules, rs: _Run, r: Optional[RunResult], tb: str = "") -> None:
    lines = list(rs.log)
    if r is not None:
        by = (r.reading or {}).get("pages_by_engine") or {}
        lines.append(f"case {r.case_id} | {r.mode} | {len(r.pages)} pages | gpu {by.get('gpu', 0)}, "
                     f"cpu {by.get('cpu', 0)}, layer {by.get('layer', 0)}")
        stats = (r.reading or {}).get("engine_stats")
        if stats is not None:
            lines.append("engine stats " + REPORT._short_json(stats, 2000))
        sf = r.safety or {}
        lines.append(f"safety: blocked {len(sf.get('blocked') or []) if isinstance(sf.get('blocked'), list) else sf.get('blocked')}"
                     f", opens under {places.current().no_open_label} {sf.get('bot_opens')}")
    lines.append("timings " + ", ".join(f"{k} {v:.1f}s" for k, v in rs.clock.t.items()))
    lines += [f"WARNING {w}" for w in rs.warnings]
    if tb:
        lines += ["CRASH in stage " + rs.clock.stage, tb]
    path = rs.out / "log.txt"
    m.safety.assert_writable(path)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _crash(m: _Modules, rs: _Run, e: BaseException) -> None:
    """log.txt with the traceback and run.json {"error": ...} with whatever finished. Secondary errors while
    writing them go to stderr only."""
    tb = "".join(traceback.format_exception(type(e), e, e.__traceback__))
    data: Dict[str, Any] = {"schema": RUN_SCHEMA, "runner_version": RUNNER_VERSION,
                            "base_rules_version": BASE_RULES_VERSION, "runner_rules_version": RUNNER_RULES_VERSION,
                            "error": f"{type(e).__name__}: {e}", "stage": rs.clock.stage, "traceback": tb,
                            "timings": rs.clock.t, "warnings": rs.warnings}
    data.update(rs.partial)
    try:
        data["safety"] = _finish_safety(m, rs)
    except Exception as e2:
        data["safety"] = {"finish_error": f"{type(e2).__name__}: {e2}"}
    try:
        m.safety.assert_writable(rs.out)
        rs.out.mkdir(parents=True, exist_ok=True)
    except Exception as e4:
        print(f"[runner] could not create the out folder ({type(e4).__name__})", file=sys.stderr)
    for what, fn in (("log.txt", lambda: _write_log(m, rs, None, tb)),
                     ("run.json", lambda: REPORT.write_json_data(data, rs.out / "run.json"))):
        try:
            fn()
        except Exception as e3:
            print(f"[runner] could not write {what} ({type(e3).__name__})", file=sys.stderr)
