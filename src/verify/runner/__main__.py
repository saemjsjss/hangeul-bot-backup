"""Command line of the document-check test runner (RUNNER_SPEC section 1.1, revision 2).

    <runner venv python> -I -B -m src.verify.runner "<bundle.pdf | folder>" --program KLP|EAP|BACHELOR|MASTER ^
        --out C:\\Hangeul\\DOCCHECK\\lab\\private-runner\\runs\\<CASE> [--today YYYY-MM-DD] [--cache-root DIR] ^
        [--page-texts JSON] [--reading-note TEXT] [--no-reread-mrz] [--split-only] [--show]

Exit codes: 0 report written (whatever the verdict); 3 STOP (the NID-person ambiguity only; notices only);
2 bad arguments, an unsafe path or page texts that do not belong to the input (nothing written);
1 crash (log.txt holds the traceback, run.json {"error": ...}).
Removed in revision 2: --edge, --prime, --force-split, --no-reread-names (they are refused: exit 2).
stdout gets a short block with no personal data (the RESULT line, the headline and where the outputs are);
--show prints the whole summary (it holds the names read off the documents: for the owner's own terminal,
never for an agent run). Error lines name no file or folder (paths become <path>). Progress lines (stage
names and seconds) go to stderr.
"""
from __future__ import annotations

import argparse
import datetime as dt
import os
import sys
from pathlib import Path
from typing import List, Optional, Sequence

# Rev 2.7 (RUNNER_SPEC limit 15): numpy's OpenBLAS commits about 30 MB for each of its threads when numpy is first
# imported (one per logical CPU: 363 MB on the owner's 12-thread PC, kept for the whole run). The runner's BLAS
# products (the closer look's frame-by-class products, 3 x 3 and 2-element geometry) are bit-identical with one
# thread (step 1b); ONNX Runtime and OpenCV keep their own threads. Set before anything imports numpy; an explicit
# setting in the environment is kept.
os.environ.setdefault("OPENBLAS_NUM_THREADS", "1")

from . import pipeline  # noqa: E402
from . import places, safety  # noqa: E402
from . import report as REPORT  # noqa: E402
from .botimport import DEFAULT_CACHE_ROOT, PRIVATE  # noqa: E402
from .model import PROGRAMS, STOP  # noqa: E402
from .safety import redact  # noqa: E402

EXIT_OK, EXIT_CRASH, EXIT_BAD, EXIT_STOP = 0, 1, 2, 3
NOTE_MAX = 200


class BadArgs(Exception):
    pass


class _Parser(argparse.ArgumentParser):
    def error(self, message: str):                       # never sys.exit from inside the parser
        raise BadArgs(message)


def build_parser() -> argparse.ArgumentParser:
    p = _Parser(prog="python -I -B -m src.verify.runner", allow_abbrev=False,
                description="Check one bundle PDF or one folder of bot-named files against the owner's rules.")
    p.add_argument("input", help="the bundle PDF or the folder (read-only)")
    p.add_argument("--program", required=True, help="KLP, EAP, BACHELOR or MASTER (case-insensitive)")
    p.add_argument("--out", required=True, help=f"output folder under {PRIVATE}")
    p.add_argument("--today", default=None, help="YYYY-MM-DD (default: today)")
    p.add_argument("--cache-root", default=str(DEFAULT_CACHE_ROOT), help="the bot's text/image caches")
    p.add_argument("--page-texts", default=None, help="runner-page-texts/1 JSON read in advance (GPU pre-read)")
    p.add_argument("--reading-note", default="", help="one line shown after READING: (<= 200 characters)")
    p.add_argument("--no-reread-mrz", action="store_true", help="turn off the targeted MRZ re-read (tests only)")
    p.add_argument("--split-only", action="store_true", help="stop after the split")
    p.add_argument("--show", action="store_true",
                   help="print the whole summary (names included) to stdout; default: the RESULT lines only")
    return p


def _under(p: Path, root: Path) -> bool:
    try:
        return p.resolve().is_relative_to(root.resolve())
    except OSError:
        return False


def check_args(a: argparse.Namespace) -> argparse.Namespace:
    """The path and value checks that need no stage module. Raises BadArgs (exit 2, nothing written)."""
    a.program = str(a.program).upper()
    if a.program not in PROGRAMS:
        raise BadArgs(f"--program must be one of {', '.join(PROGRAMS)}")
    if a.today:
        try:
            a.today = dt.date.fromisoformat(a.today)
        except ValueError:
            raise BadArgs("--today must be YYYY-MM-DD") from None
    else:
        a.today = dt.date.today()
    for name in ("input", "out", "cache_root", "page_texts"):     # S1: every command uses ABSOLUTE paths
        v = getattr(a, name)
        if v and not Path(v).is_absolute():
            raise BadArgs(f"{'--' + name.replace('_', '-') if name != 'input' else 'the input'} must be an "
                          "absolute path")
    a.input = Path(a.input)
    if not a.input.exists():
        raise BadArgs("the input does not exist")
    a.out = Path(a.out)
    if places.current().mode == places.APP:              # APP_SPEC rev 1 section 4.2: the report folder's rules
        try:
            safety.check_out_dir(a.out, a.input)
        except safety.SafetyError as e:
            raise BadArgs(str(e)) from None
    else:
        out = a.out.resolve()
        if not _under(out, PRIVATE) or out == PRIVATE.resolve():
            raise BadArgs(f"--out must be a folder under {PRIVATE}")
        inp = a.input.resolve()
        if out == inp or (a.input.is_dir() and _under(out, inp)):
            raise BadArgs("--out must not lie inside the input")
    a.cache_root = Path(a.cache_root)
    if not _under(a.cache_root, PRIVATE):
        raise BadArgs(f"--cache-root must be under {PRIVATE}")
    if a.page_texts:
        a.page_texts = Path(a.page_texts)
        if not _under(a.page_texts, PRIVATE):
            raise BadArgs(f"--page-texts must be under {PRIVATE}")
        if not a.page_texts.is_file():
            raise BadArgs("the --page-texts file does not exist")
    else:
        a.page_texts = None
    note = " ".join(str(a.reading_note or "").split())
    a.reading_note = note if len(note) <= NOTE_MAX else note[: NOTE_MAX - 1] + "…"
    return a


def main(argv: Optional[Sequence[str]] = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if hasattr(sys.stdout, "reconfigure"):
        try:
            sys.stdout.reconfigure(errors="replace")
        except Exception:
            pass
    try:
        a = check_args(build_parser().parse_args(argv))
    except BadArgs as e:
        print(f"runner: {redact(e)}", file=sys.stderr)
        return EXIT_BAD
    except SystemExit as e:                              # --help
        return int(e.code or 0) if isinstance(e.code, int) else EXIT_BAD
    try:
        r = pipeline.run(a.input, a.program, a.out, today=a.today, cache_root=a.cache_root,
                         page_texts=a.page_texts, reading_note=a.reading_note, reread_mrz=not a.no_reread_mrz,
                         split_only=a.split_only)
    except pipeline.RefusedError as e:
        print(f"runner: refused: {redact(e)}", file=sys.stderr)
        return EXIT_BAD
    except pipeline.RunCrashed as e:
        print(f"runner: crashed: {redact(e)} (see log.txt)", file=sys.stderr)
        return EXIT_CRASH
    except Exception as e:                               # the traceback may hold values: type only
        print(f"runner: crashed ({type(e).__name__})", file=sys.stderr)
        return EXIT_CRASH
    print(REPORT.summary_text(r, a.out) if a.show else quiet_text(r, a.out), end="")
    return EXIT_STOP if r.overall == STOP else EXIT_OK


def quiet_text(r, out: Path) -> str:
    """What stdout gets without --show: the RESULT line, the headline and the outputs. No name, number or date
    read off a document (the case id is a hash)."""
    lines = [f"{r.case_id} | {r.program} | {r.mode} | {len(r.pages)} pages | RESULT: {r.overall}", r.headline,
             f"Summary: {Path(out) / 'summary.txt'} | Report: {Path(out) / 'report.xlsx'} | "
             f"JSON: {Path(out) / 'run.json'}"]
    return "".join(x + "\n" for x in lines)


if __name__ == "__main__":
    sys.exit(main())
