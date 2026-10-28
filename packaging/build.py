"""The app build, one heavy step at a time (APP_SPEC r2 R2.4.6). Run with the build env's python (G10):

    set BPY=C:\\Hangeul\\DOCCHECK\\lab\\app-build\\.venv-build\\Scripts\\python.exe
    %BPY% -I -B packaging\\build.py stage                     -> app-build\\builds\\<id>\\stage (prints <id>)
    %BPY% -I -B packaging\\build.py freeze --build-id <id>    -> PyInstaller at BELOW_NORMAL (dist, work)
    %BPY% -I -B packaging\\build.py check  --build-id <id>    -> import smoke, MANIFEST.json, scan_bundle, dll_check,
                                                                the frozen --selftest through cardrun, build_read.json
    %BPY% -I -B packaging\\build.py installer --build-id <id> --check-date YYYY-MM-DD --this-pc-code XXXX-XXXX

Every output goes under C:\\Hangeul\\DOCCHECK\\lab\\app-build\\builds\\<id>\\{stage, work, dist, installer, reports}.
PYTHONDONTWRITEBYTECODE=1 and PYINSTALLER_CONFIG_DIR=app-build\\pyi-config are set for this process and its children.
Every child runs at BELOW_NORMAL inside a kill-on-close job (stage.run_low), so none is left behind. Nothing is
installed anywhere; the worktree and the lab venvs are only read. Exit: 0 done, 1 a step failed, 2 bad arguments.
"""
from __future__ import annotations

import argparse
import importlib.util
import inspect
import json
import os
import re
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

PKG = Path(__file__).resolve().parent
if str(PKG) not in sys.path:
    sys.path.insert(0, str(PKG))

import manifest  # noqa: E402
import stage  # noqa: E402
import version as build_version  # noqa: E402

WORKTREE = PKG.parent
APP_BUILD = Path(r"C:\Hangeul\DOCCHECK\lab\app-build")
BUILDS = APP_BUILD / "builds"
PYI_CONFIG = APP_BUILD / "pyi-config"
BUILD_VENV = APP_BUILD / ".venv-build"
BPY = BUILD_VENV / "Scripts" / "python.exe"
BUILD_PTH = BUILD_VENV / "Lib" / "site-packages" / "hdc_build.pth"
SPEC_FILE = PKG / "hdc.spec"
APP_NAME = build_version.PRODUCT_NAME

FREEZE_TIMEOUT_S = 3 * 3600
SMOKE_TIMEOUT_S = 900
TOOL_TIMEOUT_S = 3600
SELFTEST_TIMEOUT_S = 2400          # nine parts; the card part alone may wait 900 s for the bot (R2.3.4)

SELFTEST_PARTS = 9                 # integrity, runtime, core, pdf, card, qr, excel, tk, guard (R2.3.4)
SUMMARY_SCHEMA = "hdc-selftest/1"  # Task D's <DATA>\selftest\selftest.json
BUILD_READ_SCHEMA = "hdc-build-read/1"
READ_CODE_RE = re.compile(r"READ CODE ([0-9A-F]{4}-[0-9A-F]{4})")
CARD_LINE_RE = re.compile(r"^Graphics card: (.+?)(?: \||$)", re.M)
OVERALL_RE = re.compile(r"overall: (OK|FAIL)\b.*?\((\d+) of (\d+) parts\)")


class BuildError(Exception):
    """A step failed; the message names the step and the rule, never page text or student data."""


def say(text: str) -> None:
    print(text, flush=True)


@dataclass(frozen=True)
class Layout:
    root: Path

    @property
    def stage(self) -> Path:
        return self.root / "stage"

    @property
    def work(self) -> Path:
        return self.root / "work"

    @property
    def dist(self) -> Path:
        return self.root / "dist"

    @property
    def installer(self) -> Path:
        return self.root / "installer"

    @property
    def reports(self) -> Path:
        return self.root / "reports"

    @property
    def app(self) -> Path:
        return self.dist / APP_NAME

    @property
    def exe(self) -> Path:
        return self.app / (APP_NAME + ".exe")

    @property
    def internal(self) -> Path:
        return self.app / "_internal"


@dataclass
class Ctx:
    """What a command uses; the unit tests replace the parts that would start real processes."""
    builds: Path = BUILDS
    pkg: Path = PKG
    python: Path = field(default_factory=lambda: Path(sys.executable))
    run: Callable[..., stage.Ran] = stage.run_low
    load: Callable[[str, Path], Any] = None  # type: ignore[assignment]
    log: Callable[[str], None] = say
    stage_tools: Optional[stage.Tools] = None
    sources: Optional[stage.Sources] = None
    git_run: Optional[Callable[..., Any]] = None
    today: Optional[str] = None

    def __post_init__(self) -> None:
        if self.load is None:
            self.load = load_module

    def layout(self, build_id: str) -> Layout:
        build_version.parse_build_id(build_id)
        root = Path(self.builds) / build_id
        if Path(os.path.abspath(root)).parent != Path(os.path.abspath(self.builds)):
            raise BuildError("the build folder must lie directly in builds")
        return Layout(root)


def load_module(name: str, path: Path) -> Any:
    """Imports a sibling task's module by its path (H's cardrun, G's build_installer)."""
    if not Path(path).is_file():
        raise BuildError(f"{Path(path).name} is missing ({Path(path).parent.name})")
    spec = importlib.util.spec_from_file_location(f"hdc_build_{name}", str(path))
    mod = importlib.util.module_from_spec(spec)
    for p in (str(Path(path).parent), str(PKG)):
        if p not in sys.path:
            sys.path.append(p)
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod


def prepare_process() -> None:
    os.environ["PYTHONDONTWRITEBYTECODE"] = "1"
    os.environ["PYINSTALLER_CONFIG_DIR"] = str(PYI_CONFIG)
    sys.dont_write_bytecode = True
    if os.name == "nt":                                   # this process too: hashing 4.6 GB stays below normal
        try:
            import ctypes
            k32 = ctypes.WinDLL("kernel32", use_last_error=True)
            k32.GetCurrentProcess.restype = ctypes.c_void_p
            k32.SetPriorityClass.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
            k32.SetPriorityClass(k32.GetCurrentProcess(), stage.BELOW_NORMAL)
        except OSError:
            pass


def require_build_python(python: Path) -> None:
    if os.path.normcase(os.path.abspath(str(python))) != os.path.normcase(str(BPY)):
        raise BuildError(f"run build.py with the build env's python ({BPY}); see make_build_env.py")


def site_dirs_from_pth(pth: Path) -> List[Path]:
    """The two site-packages folders of hdc_build.pth, in order (G10)."""
    if not Path(pth).is_file():
        raise BuildError("hdc_build.pth is missing: run make_build_env.py create and check first")
    dirs = [Path(s.strip()) for s in Path(pth).read_text(encoding="utf-8").splitlines()
            if s.strip() and not s.lstrip().startswith(("#", "import"))]
    if len(dirs) != 2:
        raise BuildError("hdc_build.pth must hold exactly two folders")
    return dirs


def _write_json(path: Path, data: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(data, indent=1, ensure_ascii=True) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def _read_json(path: Path) -> Dict[str, Any]:
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


# --------------------------------------------------------------------------------------------------------
# stage

def cmd_stage(ctx: Ctx, build_id: Optional[str] = None) -> str:
    short, dirty = build_version.git_facts(WORKTREE, ctx.git_run) if ctx.git_run else \
        build_version.git_facts(WORKTREE)
    day = ctx.today or time.strftime("%Y%m%d")
    build_id = build_id or build_version.next_build_id(ctx.builds, day, short, dirty)
    L = ctx.layout(build_id)
    if L.root.exists():
        raise BuildError(f"builds\\{build_id} exists already; a build id is staged once")
    for d in (L.stage, L.work, L.dist, L.installer, L.reports):
        d.mkdir(parents=True, exist_ok=False)
    sources = ctx.sources or stage.Sources(site_dirs=tuple(site_dirs_from_pth(BUILD_PTH)),
                                           python_exe=Path(ctx.python))
    try:
        stage.run(sources, L.stage, L.reports, build_id, ctx.stage_tools)
    except stage.StageError as e:
        _write_json(L.reports / "stage.json", {"build_id": build_id, "ok": False, "error": str(e)})
        raise BuildError(f"stage: {e}") from None
    ctx.log(f"build id: {build_id}")
    return build_id


# --------------------------------------------------------------------------------------------------------
# freeze

def cmd_freeze(ctx: Ctx, build_id: str) -> None:
    L = ctx.layout(build_id)
    if not _read_json(L.reports / "stage.json").get("ok") or not (L.stage / "BUILD.json").is_file():
        raise BuildError("the stage of this build is missing or failed: run build.py stage")
    if L.app.exists():
        raise BuildError("this build is frozen already; a build id is frozen once")
    env = stage.child_env(extra={"HDC_STAGE": str(L.stage), "HDC_REPORTS": str(L.reports),
                                 "PYINSTALLER_CONFIG_DIR": str(PYI_CONFIG)})
    cmd = [str(ctx.python), "-I", "-B", "-m", "PyInstaller", "--noconfirm", "--clean",
           "--distpath", str(L.dist), "--workpath", str(L.work), str(SPEC_FILE)]
    ctx.log("freeze: PyInstaller at BELOW_NORMAL (about 15-25 min); log in reports\\freeze.log")
    r = ctx.run(cmd, env=env, cwd=L.root, timeout=FREEZE_TIMEOUT_S, log_path=L.reports / "freeze.log")
    facts = {"build_id": build_id, "exit": r.returncode, "seconds": round(r.seconds, 1), "timed_out": r.timed_out}
    if r.timed_out or r.returncode != 0 or not L.exe.is_file():
        _write_json(L.reports / "freeze.json", {**facts, "ok": False})
        raise BuildError(f"freeze failed (exit {r.returncode}, timed out {r.timed_out}); see reports\\freeze.log")
    if (L.internal / "BUILD.json").read_bytes() != (L.stage / "BUILD.json").read_bytes():
        _write_json(L.reports / "freeze.json", {**facts, "ok": False})
        raise BuildError("the frozen BUILD.json differs from the staged one")
    _write_json(L.reports / "freeze.json", {**facts, "ok": True})
    ctx.log(f"freeze: done in {r.seconds / 60:.1f} min")


# --------------------------------------------------------------------------------------------------------
# check

def parse_selftest(text: str) -> Dict[str, Any]:
    """From SELFTEST.txt (r1 1.2 layout, r2 R2.3.4 lines): overall, parts, READ CODE of the card part, card name."""
    out: Dict[str, Any] = {"overall": None, "ok_parts": None, "parts": None, "read_code": None, "card": None}
    m = OVERALL_RE.search(text)
    if m:
        out.update(overall=m.group(1), ok_parts=int(m.group(2)), parts=int(m.group(3)))
    for line in text.splitlines():
        if line.strip().startswith("card ") and "READ CODE" in line:
            rc = READ_CODE_RE.search(line)
            if rc:
                out["read_code"] = rc.group(1)
    c = CARD_LINE_RE.search(text)
    if c:
        out["card"] = c.group(1).strip()
    return out


def build_read_record(summary: Dict[str, Any]) -> Dict[str, Any]:
    """selftest\\build_read.json from Task D's selftest.json (the same fields as selftest.build_read_record)."""
    card = summary.get("card") or {}
    nv = summary.get("nvml") or {}
    return {"schema": BUILD_READ_SCHEMA, "read_code": card.get("read_code"), "card": card.get("card_name"),
            "cc": nv.get("cc"), "driver": nv.get("driver"), "torch": card.get("torch"), "cuda": card.get("cuda"),
            "cudnn": card.get("cudnn"), "build_id": (summary.get("app") or {}).get("build_id")}


def selftest_facts(data_dir: Path, report_dir: Path) -> Tuple[Dict[str, Any], Optional[Dict[str, Any]]]:
    """(facts, build_read record) of the frozen self-test: from <DATA>\\selftest\\selftest.json (Task D's summary)
    when it is there, else from the newest SELFTEST*.txt of the report folder."""
    summary = _read_json(Path(data_dir) / "selftest" / "selftest.json")
    if summary.get("schema") == SUMMARY_SCHEMA:
        rec = build_read_record(summary)
        parts = summary.get("parts") if isinstance(summary.get("parts"), list) else []
        facts = {"overall": "OK" if summary.get("ok") is True else "FAIL", "ok_parts": summary.get("ok_parts"),
                 "parts": len(parts), "read_code": rec["read_code"], "card": rec["card"],
                 "build_id": rec["build_id"], "source": "selftest.json"}
        return facts, rec
    texts = sorted(Path(report_dir).glob("SELFTEST*.txt"), key=lambda p: p.stat().st_mtime)
    if not texts:
        return {"source": None}, None
    facts = parse_selftest(texts[-1].read_text(encoding="utf-8", errors="replace"))
    facts["source"] = "SELFTEST.txt"
    rec = {"schema": BUILD_READ_SCHEMA, "read_code": facts.get("read_code"), "card": facts.get("card")}
    return facts, rec


def _rc(result: Any) -> Optional[int]:
    """cardrun.run's result as an exit code: an int, an object with .returncode, or a dict."""
    if result is None or isinstance(result, bool):
        return None
    if isinstance(result, int):
        return result
    if isinstance(result, dict):
        v = result.get("returncode", result.get("exit"))
        return v if isinstance(v, int) else None
    v = getattr(result, "returncode", None)
    return v if isinstance(v, int) else None


def cmd_check(ctx: Ctx, build_id: str) -> Dict[str, Any]:
    L = ctx.layout(build_id)
    report: Dict[str, Any] = {"build_id": build_id, "ok": False, "steps": {}}
    steps = report["steps"]

    def done(name: str, ok: bool, **facts: Any) -> bool:
        steps[name] = {"ok": ok, **facts}
        _write_json(L.reports / "check.json", report)
        ctx.log(f"check: {name} {'OK' if ok else 'FAIL'}")
        return ok

    if not _read_json(L.reports / "freeze.json").get("ok") or not L.exe.is_file():
        raise BuildError("this build is not frozen: run build.py freeze")
    built = _read_json(L.internal / "BUILD.json")
    if not done("dist", built.get("build_id") == build_id and
                (L.internal / "BUILD.json").read_bytes() == (L.stage / "BUILD.json").read_bytes()):
        raise BuildError("the dist's BUILD.json is not this build's")
    app_version = built.get("app_version", build_version.APP_VERSION)

    smoke_data = L.reports / "smoke-data"
    r = ctx.run([str(L.exe), "--selftest-part", "imports", str(L.reports / "imports.json")],
                env=stage.child_env(extra={"HDC_DATA_DIR": str(smoke_data)}), cwd=L.reports,
                timeout=SMOKE_TIMEOUT_S, log_path=L.reports / "imports.log")
    imp = _read_json(L.reports / "imports.json")          # D's part result (run_part exits 0 once it is written)
    ok = done("imports", r.returncode == 0 and not r.timed_out and imp.get("status") == "OK", exit=r.returncode,
              seconds=round(r.seconds, 1), status=imp.get("status"), detail=str(imp.get("detail") or "")[:300])

    m = manifest.write(L.app, build_id=build_id, app_version=app_version)
    ok = done("manifest", True, files=m["count"], bytes=m["bytes"]) and ok

    tool_args = {"scan_bundle": ["--stage-list", str(L.reports / "STAGE_LIST.txt"),
                                 "--json", str(L.reports / "scan_bundle.json")],
                 "dll_check": ["--json", str(L.reports / "dll_check.json")]}
    for tool, extra in tool_args.items():
        path = Path(ctx.pkg) / "tests" / f"{tool}.py"
        if not path.is_file():
            ok = done(tool, False, error=f"packaging\\tests\\{tool}.py is missing") and ok
            continue
        r = ctx.run([str(ctx.python), "-I", "-B", str(path), str(L.app), *extra], env=stage.child_env(),
                    cwd=L.reports, timeout=TOOL_TIMEOUT_S, log_path=L.reports / f"{tool}.log")
        passed = r.returncode == 0 and not r.timed_out
        ok = done(tool, passed, exit=r.returncode, seconds=round(r.seconds, 1)) and ok
    if not ok:
        raise BuildError("check failed before the self-test; see reports\\check.json and the logs")

    sdir, sdata = L.reports / "selftest", L.reports / "selftest-data"
    sdir.mkdir(parents=True, exist_ok=True)
    cardrun = ctx.load("cardrun", Path(ctx.pkg) / "tests" / "cardrun.py")
    cmd = [str(L.exe), "--data-dir", str(sdata), "--selftest", "--report-dir", str(sdir)]
    try:
        accepted = set(inspect.signature(cardrun.run).parameters)
    except (TypeError, ValueError):
        accepted = set()
    kw = {k: v for k, v in (("what", "build.py check: frozen --selftest"), ("log_dir", L.reports / "cardrun"))
          if k in accepted}
    saved = {k: os.environ.get(k) for k in stage.TEST_ONLY_VARS}
    for k in stage.TEST_ONLY_VARS:                       # cardrun passes os.environ on to the frozen app
        os.environ.pop(k, None)
    os.environ["HDC_DATA_DIR"] = str(sdata)              # the frozen app never writes the owner's real DATA folder
    try:
        rc = _rc(cardrun.run(cmd, SELFTEST_TIMEOUT_S, **kw))
    except Exception as e:                               # e.g. Q-O1 not approved, another card run busy
        done("selftest", False, error=f"cardrun: {type(e).__name__}: {e}")
        raise BuildError(f"the frozen self-test could not run through cardrun ({type(e).__name__})") from None
    finally:
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
    facts, record = selftest_facts(sdata, sdir)
    st_ok = (rc == 0 and facts.get("overall") == "OK" and facts.get("parts") == SELFTEST_PARTS
             and facts.get("ok_parts") == SELFTEST_PARTS and bool(facts.get("read_code"))
             and facts.get("build_id") in (None, build_id))
    if not done("selftest", st_ok, exit=rc, **{k: facts.get(k) for k in ("overall", "ok_parts", "parts",
                                                                         "read_code", "card", "source")}):
        raise BuildError("the frozen self-test did not pass 9 of 9 with a READ CODE; see reports\\selftest")

    _write_json(L.internal / "selftest" / "build_read.json", dict(record or {}, build_id=build_id))
    m = manifest.write(L.app, build_id=build_id, app_version=app_version)
    problems = manifest.verify(L.app, manifest.load(L.internal / "MANIFEST.json"), full=True)
    if not done("build_read", not problems, read_code=facts["read_code"], files=m["count"], bytes=m["bytes"],
                problems=problems[:10]):
        raise BuildError("MANIFEST.json does not match the dist after build_read.json")
    report["ok"] = True
    _write_json(L.reports / "check.json", report)
    ctx.log(f"check: passed; READ CODE {facts['read_code']} on this PC's card")
    return report


# --------------------------------------------------------------------------------------------------------
# installer

def cmd_installer(ctx: Ctx, build_id: str, check_date: str, this_pc_code: str) -> int:
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", check_date or ""):
        raise BuildError("--check-date must be YYYY-MM-DD")
    if not re.fullmatch(r"[0-9A-F]{4}-[0-9A-F]{4}", this_pc_code or ""):
        raise BuildError("--this-pc-code must be XXXX-XXXX (upper-case hex)")
    L = ctx.layout(build_id)
    chk = _read_json(L.reports / "check.json")
    if not chk.get("ok") or chk.get("build_id") != build_id:
        raise BuildError("build.py check has not passed for this build")
    problems = manifest.verify(L.app, manifest.load(L.internal / "MANIFEST.json"), full=True)
    if problems:
        raise BuildError(f"the dist no longer matches MANIFEST.json ({len(problems)} problem(s), e.g. {problems[0]})")
    bi = ctx.load("build_installer", Path(ctx.pkg) / "installer" / "build_installer.py")
    ctx.log("installer: Task G's build_installer (ISCC at BELOW_NORMAL, about 30-60 min)")
    rc = bi.main(["--build-id", build_id, "--check-date", check_date, "--this-pc-code", this_pc_code])
    return int(rc or 0)


# --------------------------------------------------------------------------------------------------------

def _parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="build.py", description="Build the Hangeul Document Checker (GPU) app.")
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("stage")
    s.add_argument("--build-id", help="normally made from the date and the git commit")
    for name in ("freeze", "check"):
        sub.add_parser(name).add_argument("--build-id", required=True)
    i = sub.add_parser("installer")
    i.add_argument("--build-id", required=True)
    i.add_argument("--check-date", required=True)
    i.add_argument("--this-pc-code", required=True)
    return ap


def main(argv: Optional[Sequence[str]] = None, ctx: Optional[Ctx] = None) -> int:
    try:
        a = _parser().parse_args(list(sys.argv[1:] if argv is None else argv))
    except SystemExit as e:
        return 2 if e.code else 0
    real = ctx is None
    ctx = ctx or Ctx()
    try:
        if real:
            prepare_process()
            require_build_python(ctx.python)
        if a.cmd == "stage":
            print(cmd_stage(ctx, a.build_id))
        elif a.cmd == "freeze":
            cmd_freeze(ctx, a.build_id)
        elif a.cmd == "check":
            cmd_check(ctx, a.build_id)
        else:
            return cmd_installer(ctx, a.build_id, a.check_date, a.this_pc_code)
        return 0
    except ValueError as e:
        ctx.log(f"build.py: {e}")
        return 2
    except BuildError as e:
        ctx.log(f"build.py {a.cmd}: {e}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
