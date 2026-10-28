"""The app's entry point: the flags and the dispatch (APP_SPEC rev 1 section 6.2, revision 2 R2.3.3).

The frozen exe runs ``sys.exit(main())`` (``stage\\hdc_entry.py``); DEV runs ``python -I -B -m
src.verify.runner.app.entry ...``. One exe, these roles:

    (no flag) [PATH]                       the window (Windows passes a PDF or folder dropped on the exe as PATH)
    --auto INPUT --program P [--today D] [--report-root DIR] [--auto-result FILE]      the window, automatic (tests)
    --worker JOB.JSON                      one check for the window; protocol on stdout, "cancel" on stdin
    --cli INPUT --program P --out DIR [--today D]       the same check IN THIS PROCESS (worker.run_job): the events
                                           as JSON lines on stdout, the worker's exit codes (tests use it)
    --selftest [--report-dir DIR]          the self-test, no window
    --selftest-part NAME [RESULT.JSON]     one self-test part in its own child
  any role:
    --trace-opens DIR                      test 8 c: trace.arm(DIR) before anything else
    --data-dir DIR                         sets HDC_DATA_DIR (tests)
    --gpu-lock-extra PATH                  one more watched lock file (tests; repeatable; never stored)

Exit codes of --worker / --cli: 0 done, 3 done with STOP, 2 refused, 1 crash, 4 cancelled, 5 platform or start-up
failure, 6 a card problem (``protocol``). The heavy modules (``worker``, ``window``, ``selftest``) are imported only
after boot, lazily. No personal data: stderr gets counts, codes and variable names, never paths or page text.
"""
from __future__ import annotations

import datetime as dt
import importlib
import json
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

from . import boot, protocol, spawn, trace, version

WORKER_MODULE = "src.verify.runner.app.worker"         # run_job(job: dict, listener) -> int
WINDOW_MODULE = "src.verify.runner.app.window"         # main(opts: Options) -> int
SELFTEST_MODULE = "src.verify.runner.app.selftest"     # main(report_dir=...) -> int; run_part(name, result) -> int
CARDGATE_MODULE = "src.verify.runner.app.cardgate"     # default_lock_setting() -> Optional[str]
SETTINGS_FILE = "settings.json"
SETTINGS_LOCK_KEY = "gpu_lock_path"

ROLES = ("window", "auto", "worker", "cli", "selftest", "selftest-part")
WORKER_ROLES = ("worker", "cli")
TITLE = "Hangeul Document Checker"
TEXT_WINDOWS_TOO_OLD = "This app needs Windows 10 version 1809 or newer (64-bit)."
TEXT_ALREADY_OPEN = "The checker is already open."
TEXT_START_FAILED = "The checker could not start ({what}). Run the Self-test and send SELFTEST.txt."


class BadArgs(Exception):
    """A wrong command line (the message names the flag, never a path)."""


@dataclass
class Options:
    role: str
    path: Optional[str] = None               # window: the dropped PDF or folder; auto / cli: the input
    job: Optional[str] = None                # --worker: job.json
    program: Optional[str] = None
    out: Optional[str] = None
    today: Optional[str] = None
    report_dir: Optional[str] = None         # --selftest
    report_root: Optional[str] = None        # --auto
    auto_result: Optional[str] = None        # --auto
    part: Optional[str] = None               # --selftest-part NAME
    part_result: Optional[str] = None        # --selftest-part NAME RESULT
    trace_dir: Optional[str] = None
    data_dir: Optional[str] = None
    lock_extra: List[str] = field(default_factory=list)
    argv: List[str] = field(default_factory=list)


# ------------------------------------------------------------------------------------------------ the flags
_VALUE_FLAGS = {"--worker": "job", "--cli": "path", "--auto": "path", "--program": "program", "--out": "out",
                "--today": "today", "--report-dir": "report_dir", "--report-root": "report_root",
                "--auto-result": "auto_result", "--trace-opens": "trace_dir", "--data-dir": "data_dir"}
_ROLE_FLAGS = {"--worker": "worker", "--cli": "cli", "--auto": "auto", "--selftest": "selftest",
               "--selftest-part": "selftest-part"}
_ABS_FIELDS = ("job", "out", "report_dir", "report_root", "auto_result", "trace_dir", "data_dir", "part_result")


def parse(argv: Sequence[str]) -> Options:
    """The command line as Options. Raises BadArgs. No argparse: a windowed exe has no console to print usage on,
    and nothing here may exit the process."""
    args = [str(a) for a in argv]
    vals: Dict[str, Any] = {}
    roles: List[str] = []
    positional: List[str] = []
    extras: List[str] = []
    i = 0
    while i < len(args):
        a = args[i]
        key, eq, inline = a.partition("=") if a.startswith("--") else (a, "", "")
        if key in _ROLE_FLAGS:
            roles.append(_ROLE_FLAGS[key])
        if key == "--selftest":
            if eq:
                raise BadArgs("--selftest takes no value")
            i += 1
            continue
        if key == "--selftest-part":
            rest = [inline] if eq else []
            j = i + 1
            while j < len(args) and len(rest) < 2 and not args[j].startswith("--"):
                rest.append(args[j])
                j += 1
            if not rest or not rest[0]:
                raise BadArgs("--selftest-part needs a part name")
            vals["part"] = rest[0]
            vals["part_result"] = rest[1] if len(rest) > 1 else None
            i = j
            continue
        if key == "--gpu-lock-extra" or key in _VALUE_FLAGS:
            if eq:
                value = inline
                i += 1
            else:
                if i + 1 >= len(args):
                    raise BadArgs(f"{key} needs a value")
                value = args[i + 1]
                i += 2
            if value == "":
                raise BadArgs(f"{key} needs a value")
            if key == "--gpu-lock-extra":
                extras.append(value)
                continue
            name = _VALUE_FLAGS[key]
            if name in vals:
                raise BadArgs(f"{key} is given twice")
            vals[name] = value
            continue
        if a.startswith("--"):
            raise BadArgs(f"unknown flag {key}")
        positional.append(a)
        i += 1

    if len(roles) > 1:
        raise BadArgs("only one of --worker, --cli, --auto, --selftest, --selftest-part")
    role = roles[0] if roles else "window"
    if role != "window" and positional:
        raise BadArgs("unexpected argument")
    opts = Options(role=role, argv=list(args), lock_extra=[os.path.abspath(x) for x in extras])
    for k, v in vals.items():
        setattr(opts, k, v)
    if role == "window" and positional:
        opts.path = positional[0]                         # Explorer: the first dropped item
    for k in _ABS_FIELDS:
        v = getattr(opts, k)
        if v is not None and not os.path.isabs(v):
            raise BadArgs(f"--{k.replace('_', '-')} must be an absolute path")
    if opts.today is not None:
        try:
            dt.date.fromisoformat(opts.today)
        except ValueError:
            raise BadArgs("--today must be YYYY-MM-DD") from None
    if role == "cli":
        if not opts.program or not opts.out:
            raise BadArgs("--cli needs --program and --out")
        opts.program = normalise_program(opts.program)
    if role == "auto" and opts.program:
        opts.program = normalise_program(opts.program)
    if role in ("cli", "auto"):
        opts.path = os.path.abspath(opts.path) if opts.path else opts.path
    if opts.lock_extra and role not in ("cli", "auto", "worker", "window"):
        raise BadArgs("--gpu-lock-extra is only for a check")
    return opts


def normalise_program(p: str) -> str:
    """KLP / EAP / BACHELOR / MASTER from any case, also "Bachelor's" and "Master's". Raises BadArgs."""
    from ..model import PROGRAMS
    s = str(p).strip().upper().replace("’", "'")
    if s.endswith("'S"):
        s = s[:-2]
    if s not in PROGRAMS:
        raise BadArgs("--program must be one of " + ", ".join(PROGRAMS))
    return s


# ------------------------------------------------------------------------------------------- worker roles
def _cancelled_type() -> Optional[type]:
    try:
        from .. import hooks
        return hooks.Cancelled
    except Exception:
        return None


def lock_paths_for_cli(data: Path, extras: Sequence[str], *, default_setting: Any = "auto") -> List[str]:
    """job.gpu.lock_paths of a --cli check: the settings' "gpu_lock_path" (a string, or null = off) when
    DATA\\settings.json has the key, else ``cardgate.default_lock_setting()``; then the --gpu-lock-extra paths."""
    setting: Optional[str] = None
    found = False
    try:
        with open(data / SETTINGS_FILE, "r", encoding="utf-8") as f:
            s = json.load(f)
        if isinstance(s, dict) and SETTINGS_LOCK_KEY in s:
            v = s[SETTINGS_LOCK_KEY]
            setting = v if isinstance(v, str) and v.strip() else None
            found = True
    except (OSError, ValueError):
        pass
    if not found:
        if default_setting == "auto":
            try:
                setting = importlib.import_module(CARDGATE_MODULE).default_lock_setting()
            except Exception:
                setting = None
        else:
            setting = default_setting
    out = [setting] if setting else []
    return out + [str(x) for x in extras]


def cli_job(opts: Options, index: int, data: Path, *, default_setting: Any = "auto") -> Dict[str, Any]:
    """The job of a --cli run: the same dict the window writes into job.json."""
    return protocol.make_job(input=opts.path, program=opts.program, out=os.path.abspath(opts.out),
                             today=opts.today, cache_root=str(data / "cache"), trace_dir=opts.trace_dir,
                             lock_paths=lock_paths_for_cli(data, opts.lock_extra, default_setting=default_setting),
                             index=index, cap_gb=version.CAP_GB, need_free_mb=version.NEED_FREE_MB)


def _import_failure_code(e: BaseException) -> str:
    text = str(e).lower()
    if "baseline" in text or "cpu dispatcher" in text or "sse4" in text:
        return protocol.CPU_UNSUPPORTED
    if "dll load failed" in text or isinstance(e, (OSError, ImportError)) and "dll" in text:
        return protocol.MISSING_PART
    return protocol.CRASH


def run_worker_role(opts: Options, *, stream: Any = "claim", gpuinfo: Any = None,
                    default_setting: Any = "auto") -> int:
    """--worker and --cli: the card environment, the protocol channel, hello, boot, then ``worker.run_job(job,
    listener)`` in this process. Every way out sends a terminal event (done / error / cancelled) first."""
    job: Optional[Dict[str, Any]] = None
    job_error: Optional[str] = None
    index: Optional[int] = None
    if opts.role == "worker":
        try:
            job = protocol.read_job(opts.job)
            index = int(job["gpu"]["index"])
        except protocol.JobError as e:
            job_error = str(e)
    env_info: Dict[str, Any] = {}
    env_error: Optional[str] = None
    try:
        env_info = boot.prepare_card_env(index, gpuinfo=gpuinfo)          # before numpy / torch
    except Exception as e:
        env_error = type(e).__name__
    out = spawn.claim_protocol_stdout() if stream == "claim" else stream
    writer = protocol.Writer(out)
    cancel = protocol.CancelFlag()
    listener = protocol.WorkerListener(writer, cancel, cancelled_exc=_cancelled_type(),
                                       cancel_on_broken=(opts.role == "worker"))
    listener.send(protocol.hello(version.APP_VERSION, os.getpid(), version.BUILD_ID))
    boot.log_line(f"role {opts.role}, app {version.APP_VERSION} build {version.BUILD_ID}, "
                  f"card index {env_info.get('index')}, removed {len(env_info.get('removed', []))} card "
                  f"variable(s) {','.join(env_info.get('removed', []))}")

    def refuse(code: str, rc: int, text: str = "", values: Optional[Dict[str, Any]] = None) -> int:
        listener.send(protocol.error(code, text, values))
        writer.close()
        return rc

    if job_error is not None:
        return refuse(protocol.BAD_ARGS, protocol.EXIT_REFUSED, job_error)
    if env_error is not None:
        return refuse(protocol.MISSING_PART, protocol.EXIT_PLATFORM, f"card environment failed ({env_error})")
    try:
        iso = boot.isolate()
    except Exception as e:
        return refuse(protocol.STARTUP_FAILED, protocol.EXIT_PLATFORM, f"boot failed ({type(e).__name__})")
    plat = boot.check_platform()
    boot.log_line(f"windows build {plat.windows_build}, 64-bit {plat.is64}, machine {plat.machine or '?'}, cpu "
                  + ", ".join(f"{k} {'?' if v is None else ('yes' if v else 'no')}" for k, v in plat.cpu.items())
                  + f", sys.path dropped {iso.get('dropped', 0)}, data ascii {iso.get('data_ascii')}")
    if not plat.ok:
        return refuse(plat.code or protocol.WINDOWS_TOO_OLD, protocol.EXIT_PLATFORM)
    pre = boot.preload_runtime()
    miss = boot.missing_runtime(pre)
    if miss is not None:
        return refuse(protocol.MISSING_PART, protocol.EXIT_PLATFORM, "", {"file": miss})
    if opts.role == "cli":
        spawn.lower_own_priority()
        try:
            job = cli_job(opts, int(env_info.get("index", 0) or 0), boot.data_dir(),
                          default_setting=default_setting)
        except (protocol.JobError, BadArgs) as e:
            return refuse(protocol.BAD_ARGS, protocol.EXIT_REFUSED, str(e))
    else:
        protocol.watch_stdin(cancel)

    try:
        mod = importlib.import_module(WORKER_MODULE)
    except BaseException as e:                          # numpy's CPU check, a DLL removed by an antivirus ...
        code = _import_failure_code(e)
        rc = protocol.EXIT_CRASH if code == protocol.CRASH else protocol.EXIT_PLATFORM
        return refuse(code, rc, f"the checking part could not load ({type(e).__name__})")

    cancelled_t = _cancelled_type()
    try:
        rc = mod.run_job(job, listener)
    except BaseException as e:
        if (cancelled_t is not None and isinstance(e, cancelled_t)) or isinstance(e, KeyboardInterrupt):
            if listener.terminal is None:
                listener.send(protocol.cancelled())
            rc = protocol.EXIT_CANCELLED
        elif isinstance(e, SystemExit):
            rc = e.code if isinstance(e.code, int) else protocol.EXIT_CRASH
        else:
            if listener.terminal is None:
                listener.send(protocol.error(protocol.CRASH, f"the check stopped ({type(e).__name__})"))
            rc = protocol.EXIT_CRASH
    if not isinstance(rc, int) or isinstance(rc, bool):
        rc = protocol.EXIT_CRASH
    if listener.terminal is None:                        # run_job returned without a final event
        if rc == protocol.EXIT_CANCELLED:
            listener.send(protocol.cancelled())
        else:
            listener.send(protocol.error(protocol.CRASH, "the check ended without a result"))
            if rc in (protocol.EXIT_OK, protocol.EXIT_STOP):
                rc = protocol.EXIT_CRASH
    writer.close()
    return rc


# ------------------------------------------------------------------------------------------- other roles
def message_box(text: str, title: str = TITLE) -> None:
    """A plain Windows message box (the window roles, before Tk exists). Never raises."""
    try:
        import ctypes
        ctypes.WinDLL("user32").MessageBoxW(None, str(text), str(title), 0x30)
    except Exception:
        pass


def _run_window(opts: Options, show: Any = message_box) -> int:
    quiet = opts.role == "auto"                          # a test run never blocks on a message box
    plat = boot.check_platform()
    if not plat.ok:
        if not quiet:
            show(TEXT_WINDOWS_TOO_OLD)
        return protocol.EXIT_PLATFORM
    boot.preload_runtime()                               # a missing DLL is the window's integrity check's job
    handle = boot.take_single_instance()
    if handle is None:
        if not quiet:
            show(TEXT_ALREADY_OPEN)
        return 1 if quiet else 0
    try:
        mod = importlib.import_module(WINDOW_MODULE)
        rc = mod.main(opts)
        return int(rc) if isinstance(rc, int) else 0
    finally:
        boot.release_single_instance(handle)


def _run_selftest(opts: Options) -> int:
    mod = importlib.import_module(SELFTEST_MODULE)
    if opts.role == "selftest":
        rc = mod.main(report_dir=opts.report_dir)
    else:
        rc = mod.run_part(opts.part, opts.part_result)
    return int(rc) if isinstance(rc, int) and not isinstance(rc, bool) else 1


def main(argv: Optional[Sequence[str]] = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    worker_like = any(a.split("=", 1)[0] in ("--worker", "--cli") for a in argv)
    try:
        opts = parse(argv)
    except BadArgs as e:
        if worker_like:                                  # still one protocol line, so a harness sees why
            try:
                sys.stdout.buffer.write(protocol.encode(protocol.error(protocol.BAD_ARGS, str(e))))
                sys.stdout.flush()
            except Exception:
                pass
        else:
            boot.log_line(f"bad arguments: {e}")
        return protocol.EXIT_REFUSED
    if opts.trace_dir:
        trace.arm(opts.trace_dir)                        # first, before boot
    if opts.data_dir:
        os.environ[boot.DATA_ENV] = os.path.abspath(opts.data_dir)
    if opts.role in WORKER_ROLES:
        return run_worker_role(opts)
    try:
        if boot.is_card_role(opts.role, opts.part):
            boot.prepare_card_env(None)
        boot.isolate()
        if opts.role in ("window", "auto"):
            return _run_window(opts)
        plat = boot.check_platform()
        if not plat.ok:
            boot.log_line(f"platform refused: {plat.code}")
            return protocol.EXIT_PLATFORM
        boot.preload_runtime()
        return _run_selftest(opts)
    except Exception as e:
        boot.log_line(f"start failed ({type(e).__name__})")
        if opts.role == "window":
            message_box(TEXT_START_FAILED.format(what=type(e).__name__))
        return protocol.EXIT_PLATFORM


if __name__ == "__main__":
    sys.exit(main())
