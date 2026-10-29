"""P5 timing and memory (APP_SPEC rev 2, R2.6): the frozen app's speed and memory on THIS PC, via cardrun.

    <runner python> -I -B packaging\\tests\\timing.py --build-id <id> [--cases ...] [--app-dir DIR]
                    [--input LABEL=PATH ...]

Runs the four r8 cases with a FRESH DATA folder each, then CHECK_0001 again on its DATA (the saved page texts).
NEEDS ORCHESTRATOR APPROVAL Q-O1 (cardrun). Reported per run (counts and seconds only):
  - worker start: harness seconds to the ``card`` ready event and that event's own seconds (and any breakdown);
  - phase 1: pages read on the card, seconds per page (mean, median, p90, max) against r8's page_texts.json;
  - phase 2: seconds from the end of the first read to ``done`` (and run.json ``timings``);
  - calls on the card and on the processor, waits;
  - peak working set and commit of the worker (cardrun's job and process counters);
  - peak card memory allocated and reserved (the ``done`` event's gpu block).
Targets (a miss fails P5):
  - worker peak WORKING SET <= 3.5 GB; card peak reserved <= 3.0 GB (the orchestrator's P5 decision of 29 Oct,
    replacing R2.6's "commit <= 4.5 GB": G1's smoke worker committed 5.4 GB with a 1.8 GB working set, most of it
    CUDA and torch host reservations that are never touched);
  - worker peak commit <= 6.0 GB (reported; above it the cause is to be found and fixed before the installer);
  - each case's total <= r8's total + 60 s. r8's run_case.log total covers only the runner when its first read
    was reused (all four r8 logs: "gpuread: exit 0 after 0.4 s"), so for a FRESH run r8's first-read seconds
    (page_texts.json ``seconds_total``) are added; the re-check is compared with run_case.log's total as it is;
  - the re-check loads no first-read model (no phase-1 page event; a reported first-read model load of 0 s).
Exit 0 all targets met, 1 a target missed, 2 could not run.
"""
from __future__ import annotations

import sys

sys.dont_write_bytecode = True

from pathlib import Path  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent))

import argparse  # noqa: E402
import re  # noqa: E402
import statistics  # noqa: E402
from typing import Any, Dict, List, Optional, Sequence, Tuple  # noqa: E402

import cardrun  # noqa: E402
import parity  # noqa: E402

GB = 2 ** 30
WORKING_SET_MAX_GB = 3.5
COMMIT_MAX_GB = 6.0
RESERVED_MAX_GB = 3.0
TOTAL_SLACK_S = 60.0
REUSED_READ_S = 5.0          # an r8 first read this short reused saved page texts
DEFAULT_TIMEOUT_S = 3600.0

Events = List[Tuple[float, Dict[str, Any]]]


def stats(xs: Sequence[float]) -> Dict[str, Optional[float]]:
    v = sorted(float(x) for x in xs if isinstance(x, (int, float)))
    if not v:
        return {"n": 0, "mean": None, "median": None, "p90": None, "max": None}
    p90 = v[min(len(v) - 1, int(round(0.9 * (len(v) - 1))))]
    return {"n": len(v), "mean": round(statistics.fmean(v), 2), "median": round(statistics.median(v), 2),
            "p90": round(p90, 2), "max": round(v[-1], 2)}


def parse_run_case_log(text: str) -> Dict[str, Optional[float]]:
    """r8's run_case.log: the first read's seconds ("gpuread: exit N after X s") and the total ("total X s")."""
    read = re.search(r"gpuread: exit -?\d+ after ([0-9.]+) s", text or "")
    total = re.search(r"\btotal ([0-9.]+) s", text or "")
    return {"first_read_s": float(read.group(1)) if read else None,
            "total_s": float(total.group(1)) if total else None}


def r8_facts(case: parity.R8Case) -> Dict[str, Any]:
    log = parity._read_text(case.dir / "run_case.log") or ""
    store = parity._read_json_or_none(case.dir / "page_texts.json") or {}
    secs = [p.get("seconds") for _, _, p in parity._pages(store) if p.get("kind") == "ocr"]
    f = parse_run_case_log(log)
    f["page_texts_s"] = store.get("seconds_total")
    f["per_page"] = stats(secs)
    f["reused"] = f["first_read_s"] is not None and f["first_read_s"] < REUSED_READ_S
    f["engine"] = engine_stats(parity._read_json_or_none(case.dir / "run.json"))
    return f


def engine_stats(run: Any) -> Dict[str, Any]:
    """The runner's own reading counts in a run.json (reading.engine_stats) and its 'bot' stage seconds: the bot
    stage's verification-text cache hits make a run much shorter (G2: every r8 run reused 1-2 cached bot-stage
    reads from earlier runs; a fresh app DATA folder has none)."""
    es = ((run or {}).get("reading") or {}).get("engine_stats") or {} if isinstance(run, dict) else {}
    bot = ((run or {}).get("timings") or {}).get("bot") if isinstance(run, dict) else None
    return {"ocr_calls": es.get("ocr_calls"), "ocr_s": es.get("ocr_seconds"), "cache_hits": es.get("cache_hits"),
            "bot_stage_s": round(bot, 1) if isinstance(bot, (int, float)) else None}


def _find(obj: Any, *names: str) -> Optional[Any]:
    """The first value of any of ``names`` in a nested dict (the gpu blocks' field names are Task W's)."""
    if isinstance(obj, dict):
        for n in names:
            if n in obj and not isinstance(obj[n], dict):
                return obj[n]
        for v in obj.values():
            r = _find(v, *names)
            if r is not None:
                return r
    return None


def summarize(events: Events, res_stats: Dict[str, Any], out_dir: Optional[Path]) -> Dict[str, Any]:
    """One app run's numbers from its events, cardrun's counters and its out folder (checkfacts.json, run.json)."""
    done = cardrun.done_event(events) or {}
    g = done.get("gpu") if isinstance(done.get("gpu"), dict) else {}
    facts = parity._read_json_or_none(Path(out_dir) / "checkfacts.json") if out_dir else None
    run = parity._read_json_or_none(Path(out_dir) / "run.json") if out_dir else None
    fg = (facts or {}).get("gpu") if isinstance((facts or {}).get("gpu"), dict) else {}
    ready = cardrun.first(events, "card", state="ready")
    stage, p1_secs, t_fr_end, t_last_p1 = None, [], None, None
    t_done = next((t for t, e in reversed(events) if e.get("ev") == "done"), None)
    for t, e in events:
        if e.get("ev") == "stage":
            if e.get("key") == "firstread" and e.get("state") == "end":
                t_fr_end = t
            stage = e.get("key") if e.get("state") == "start" else None
        if e.get("ev") == "page" and (e.get("where") == "card" or stage == "firstread"):
            p1_secs.append(e.get("seconds"))
            t_last_p1 = t
    t_p2 = t_fr_end if t_fr_end is not None else t_last_p1
    waits = [e for _, e in events if e.get("ev") == "gpu_wait" and e.get("state") == "start"]
    commit = max([b for b in (res_stats.get("peak_commit_mb"), res_stats.get("job_peak_process_commit_mb"))
                  if b is not None], default=None)
    model_load = _find(fg, "first_read_model_load_s", "model_load_s")
    if model_load is None:
        model_load = _find(g, "first_read_model_load_s", "model_load_s")
    return {
        "exit": res_stats.get("returncode"), "total_s": res_stats.get("seconds"),
        "start": {"harness_s": round(ready[0], 2) if ready else None,
                  "card_ready": {k: v for k, v in (ready[1] if ready else {}).items()
                                 if isinstance(v, (int, float)) and (k == "seconds" or k.endswith("_s"))}},
        "phase1": {"pages": len(p1_secs), "per_page": stats(p1_secs)},
        "phase2_s": round(t_done - t_p2, 2) if (t_done is not None and t_p2 is not None) else None,
        "runner_timings_s": round(sum(v for v in ((run or {}).get("timings") or {}).values()
                                      if isinstance(v, (int, float))), 2) if run else None,
        "calls": {"card": _find(fg, "card_calls", "calls_card", "calls_gpu"),
                  "processor": g.get("cpu_calls", _find(fg, "cpu_calls", "calls_cpu"))},
        "first_read_model_load_s": model_load,
        "engine": engine_stats(run),
        "memory": {"peak_working_set_mb": res_stats.get("peak_working_set_mb"), "peak_commit_mb": commit},
        "card": {"peak_alloc_gb": g.get("peak_alloc_gb", _find(fg, "peak_alloc_gb")),
                 "peak_reserved_gb": g.get("peak_reserved_gb", _find(fg, "peak_reserved_gb"))},
        "waits": {"count": g.get("waits", len(waits)), "waited_s": g.get("waited_s"),
                  "bot": sum(1 for e in waits if e.get("lock") == "bot")},
    }


def targets(s: Dict[str, Any], r8: Dict[str, Any], recheck: bool) -> List[Tuple[str, bool, str]]:
    """[0] working set, [1] card reserved, [2] total, [3] (re-check only) no first-read model, last: commit."""
    out: List[Tuple[str, bool, str]] = []
    ws = s["memory"]["peak_working_set_mb"]
    out.append((f"worker peak working set <= {WORKING_SET_MAX_GB} GB",
                isinstance(ws, (int, float)) and ws <= WORKING_SET_MAX_GB * 1024,
                f"{ws} MB" if ws is not None else "not measured"))
    res = s["card"]["peak_reserved_gb"]
    out.append((f"card peak reserved <= {RESERVED_MAX_GB} GB", isinstance(res, (int, float))
                and res <= RESERVED_MAX_GB, f"{res} GB"))
    base = r8.get("total_s")
    if base is not None:
        add = 0.0 if recheck or not r8.get("reused") else float(r8.get("page_texts_s") or 0.0)
        limit = base + add + TOTAL_SLACK_S
        tot = s.get("total_s")
        out.append(("total <= r8 total" + (" + r8 first read" if add else "") + f" + {TOTAL_SLACK_S:.0f} s",
                    isinstance(tot, (int, float)) and tot <= limit, f"{tot} s vs {round(limit, 1)} s"))
    else:
        out.append(("total <= r8 total + 60 s", False, "r8 run_case.log total not found"))
    if recheck:
        ml = s.get("first_read_model_load_s")
        ok = s["phase1"]["pages"] == 0 and (ml is None or float(ml) == 0.0)
        out.append(("the re-check loads no first-read model", ok,
                    f"{s['phase1']['pages']} phase-1 page(s), model load {ml}"))
    commit = s["memory"]["peak_commit_mb"]
    out.append((f"worker peak commit <= {COMMIT_MAX_GB} GB", isinstance(commit, (int, float))
                and commit <= COMMIT_MAX_GB * 1024, f"{commit} MB" if commit is not None else "not measured"))
    return out


def run_one(case: parity.R8Case, exe: Path, d: Path, data: Path, timeout: float, run_fn: Any) -> Dict[str, Any]:
    out = d / "out"
    data.mkdir(parents=True, exist_ok=True)
    out.mkdir(parents=True, exist_ok=True)
    cmd = cardrun.cli_cmd(exe, case.input, case.program, out, case.today, data)
    res = run_fn(cmd, timeout, what=f"hdc P5 {case.label}", env=cardrun.app_env(data), log_dir=d)
    return summarize(cardrun.events_of(res.lines), res.stats(), out)


def line_of(label: str, s: Dict[str, Any], checks: List[Tuple[str, bool, str]]) -> List[str]:
    p1 = s["phase1"]["per_page"]
    lines = [f"{label}: exit {s['exit']} | total {s['total_s']} s | start {s['start']['harness_s']} s"
             f" {s['start']['card_ready']} | phase 1 {s['phase1']['pages']} pages, mean {p1['mean']} s,"
             f" median {p1['median']} s, p90 {p1['p90']} s, max {p1['max']} s | phase 2 {s['phase2_s']} s"
             f" | calls card {s['calls']['card']}, processor {s['calls']['processor']}"
             f" | peak WS {s['memory']['peak_working_set_mb']} MB, commit {s['memory']['peak_commit_mb']} MB"
             f" | card {s['card']['peak_alloc_gb']} / {s['card']['peak_reserved_gb']} GB"
             f" | waits {s['waits']['count']} ({s['waits']['waited_s']} s)"]
    for name, ok, detail in checks:
        lines.append(f"  {'ok  ' if ok else 'MISS'} {name} ({detail})")
    return lines


def main(argv: Optional[Sequence[str]] = None, run_fn: Any = None, runs: Path = parity.RUNS) -> int:
    ap = argparse.ArgumentParser(description="P5: timing and memory of the frozen app (needs Q-O1)")
    ap.add_argument("--build-id", default="")
    ap.add_argument("--app-dir", default="")
    ap.add_argument("--cases", default=",".join(c for c, _ in parity.CASES))
    ap.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT_S)
    ap.add_argument("--input", action="append", default=[], metavar="LABEL=PATH",
                    help="check PATH instead of r8's input for that case (as parity.py)")
    a = ap.parse_args(argv)
    try:
        app = cardrun.app_dir_for(a.build_id or None, a.app_dir or None)
        known = dict(parity.CASES)
        wanted = [c.strip() for c in a.cases.split(",") if c.strip()]
        if any(c not in known for c in wanted):
            raise ValueError("unknown case")
        cases = [parity.load_r8(c, runs, known[c]) for c in wanted]
        parity.apply_inputs(cases, parity.parse_inputs(a.input, list(known)))
    except (ValueError, OSError, KeyError) as exc:
        print(f"P5: cannot start: {type(exc).__name__}: {exc}")
        return 2
    exe = app / cardrun.APP_EXE
    if not exe.is_file():
        print("P5: cannot start: the app exe was not found in the app folder")
        return 2
    run_fn = run_fn or cardrun.run
    apptest = cardrun.apptest_dir(a.build_id or None)
    root = cardrun.new_run_dir(apptest, "p5")
    print(f"P5 timing and memory | build {a.build_id or '-'} | run folder {root.name}")
    results: List[Dict[str, Any]] = []
    plan = [(c, False) for c in cases] + [(c, True) for c in cases if c.label == parity.CHECK_CASE]
    for case, recheck in plan:
        d = root / (case.label + ("-recheck" if recheck else ""))
        data = (root / case.label / "data") if recheck else (d / "data")
        try:
            s = run_one(case, exe, d, data, a.timeout, run_fn)
        except cardrun.STOP_ERRORS as exc:
            print(f"P5: stopped: {type(exc).__name__}: {exc}")
            return 2
        r8 = r8_facts(case)
        checks = targets(s, r8, recheck)
        label = case.label + (" re-check" if recheck else "")
        for ln in line_of(label, s, checks):
            print(ln)
        print(f"  r8: total {r8['total_s']} s, first read {r8['page_texts_s']} s"
              f"{' (reused in run_case)' if r8['reused'] else ''}, per page mean {r8['per_page']['mean']} s,"
              f" median {r8['per_page']['median']} s")
        ea, er = s.get("engine") or {}, r8.get("engine") or {}
        print(f"  bot stage: app {ea.get('bot_stage_s')} s ({ea.get('ocr_calls')} reads, {ea.get('cache_hits')} cached),"
              f" r8 {er.get('bot_stage_s')} s ({er.get('ocr_calls')} reads, {er.get('cache_hits')} cached)")
        results.append({"label": label, "app": s, "r8": r8,
                        "targets": [{"name": n, "ok": ok, "detail": det} for n, ok, det in checks]})
    passed = all(t["ok"] for r in results for t in r["targets"])
    print(f"P5 {'PASS: all targets met' if passed else 'FAIL: a target was missed'}")
    report = {"schema": "hdc-p5/1", "build_id": a.build_id or None, "run_folder": root.name, "passed": passed,
              "runs": results}
    cardrun.write_json(root / "timing.json", report)
    rdir = cardrun.reports_dir_for(a.build_id or None)
    if rdir is not None and rdir.is_dir():
        cardrun.write_json(rdir / "timing.json", report)
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
