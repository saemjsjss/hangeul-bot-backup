"""8 c runtime independence (APP_SPEC rev 1 §8 c, amended by rev 2 R2.6): what the frozen app touched.

    check the trace files of a run:
      <runner python> -I -B packaging\\tests\\trace_check.py check --trace DIR --install DIR --data DIR
                      --input PATH --report DIR [--cwd DIR] [--manifest FILE] [--hash] [--json OUT]
    run CHECK_0001 through --auto with --trace-opens (via cardrun; NEEDS ORCHESTRATOR APPROVAL Q-O1), then check:
      <runner python> -I -B packaging\\tests\\trace_check.py run --build-id <id> [--app-dir DIR]

The trace (Task C's ``--trace-opens DIR``) is one JSON line per audit event in ``DIR\\trace-<pid>.jsonl``. This
checker reads the event name from ``event`` / ``ev`` / ``name`` and the arguments from ``args`` (or ``path``,
``mode``, ``src``, ``dst``, ``sub_key``); a refused socket event carries ``refused`` / ``blocked`` / ``denied`` true.
Pass (rev 1 + R2.6):
  - zero paths outside the allowed set {the install folder, DATA (incl. DATA\\tmp and DATA\\tmp\\<job>\\ascii), the
    input, the report folder, the trace folder, %WINDIR% (System32, Fonts, nvml.dll, nvcuda.dll, the DriverStore)},
    device names (NUL, CON, CONIN$, CONOUT$) and bare DLL names in ctypes.dlopen. Relative paths are taken against
    the working folder (DATA, set by boot);
  - nothing under C:\\Hangeul\\BOT (the lock is only stat'ed, which raises no audit event); nothing in the system
    %TEMP%;
  - zero write events under the install folder; no registry writes; registry reads only of the expected keys (CPU
    name, Windows version, LongPathsEnabled, Smart App Control state); no process start other than the app's own
    exe; no socket event that was not refused; no onnxruntime session (GPU build);
  - with ``--manifest``: the install folder still matches MANIFEST.json (sizes; sha256 with ``--hash``), none added.
Limit (listed, not checked): native opens (MuPDF fonts, Windows loading DLLs, NVIDIA's ComputeCache and ProgramData)
are not audit events.
Prints counts and categories; a path is shown only as a root token (<DATA>, <WINDIR>, ...) or masked; the full list
goes to the private JSON next to the trace folder. Exit 0 pass, 1 fail, 2 could not run.
"""
from __future__ import annotations

import sys

sys.dont_write_bytecode = True

from pathlib import Path  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent))

import argparse  # noqa: E402
import hashlib  # noqa: E402
import json  # noqa: E402
import ntpath  # noqa: E402
import os  # noqa: E402
from dataclasses import dataclass  # noqa: E402
from typing import Any, Dict, List, Optional, Sequence, Tuple  # noqa: E402

DEVICES = ("nul", "con", "conin$", "conout$")
BOT_ROOT = r"c:\hangeul\bot"
EXPECTED_REG = (
    r"hardware\description\system\centralprocessor",          # CPU name
    r"software\microsoft\windows nt\currentversion",           # Windows version
    r"system\currentcontrolset\control\filesystem",            # LongPathsEnabled
    r"system\currentcontrolset\control\ci\policy",             # Smart App Control state
)
REG_WRITES = ("winreg.createkey", "winreg.createkeyex", "winreg.setvalue", "winreg.setvalueex", "winreg.deletekey",
              "winreg.deletekeyex", "winreg.deletevalue", "winreg.savekey", "winreg.loadkey")
WRITE_EVENTS = ("os.mkdir", "os.remove", "os.rmdir", "os.unlink", "os.rename", "os.replace", "os.chmod", "os.utime",
                "os.truncate", "os.symlink", "os.link", "os.chown", "shutil.rmtree", "shutil.move",
                "shutil.copyfile", "shutil.copymode", "shutil.copystat", "shutil.copytree", "shutil.chown",
                "pymupdf.document.save", "pil.image.save", "cv2.imwrite")
EXEC_EVENTS = ("os.system", "os.exec", "os.spawn", "os.posix_spawn", "os.startfile", "subprocess.popen")
READ_WRAPPERS = ("pymupdf.open", "pil.image.open", "cv2.imread")
OPEN_WRITE_FLAGS = os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_APPEND | os.O_TRUNC
REFUSED_KEYS = ("refused", "blocked", "denied")
# The socket events that contact a network (the runner guard's socket events, safety._NETWORK_EVENTS): each must be
# refused. Other socket events (socket.__new__, socket.gethostname from platform.uname(), socket.bind ...) reach no
# other host; they are counted as advisory, not failed (integration of Task C's trace with this checker).
NETWORK_SOCKET_EVENTS = ("socket.connect", "socket.getaddrinfo", "socket.sendto", "socket.sendmsg",
                         "socket.gethostbyname", "socket.gethostbyname_ex", "socket.gethostbyaddr",
                         "socket.getnameinfo")
# Exceptions need a written reason here (rev 1 §8 c): (event, path category) -> reason.
EXCEPTIONS: Dict[Tuple[str, str], str] = {}


@dataclass
class Event:
    name: str
    args: List[Any]
    raw: Dict[str, Any]
    file: str


@dataclass
class Finding:
    kind: str          # outside, bot, temp, write-install, registry, registry-write, process, socket, onnx, manifest
    event: str
    where: str         # a root token or a masked path, never a full path
    detail: str = ""


@dataclass
class Roots:
    install: str
    data: str
    input: str
    report: str
    trace: str
    windir: str
    temp: str
    cwd: str

    def token(self, p: str) -> str:
        for name in ("trace", "data", "report", "install", "input", "windir"):
            r = getattr(self, name)
            if r and _under(p, r):
                return f"<{name.upper()}>"
        return mask(p)


def _norm(p: str) -> str:
    s = str(p)
    if s.startswith("\\\\?\\UNC\\"):
        s = "\\\\" + s[8:]
    elif s.startswith("\\\\?\\"):
        s = s[4:]
    return ntpath.normcase(ntpath.normpath(s))


def _under(p: str, root: str) -> bool:
    if not root:
        return False
    p, root = _norm(p), _norm(root)
    return p == root or p.startswith(root.rstrip("\\") + "\\")


def mask(p: str) -> str:
    """Drive and first folder only (C:\\Users\\... -> C:\\Users\\<...>): never a user, student or case name."""
    s = _norm(p)
    parts = [x for x in s.split("\\") if x]
    if not parts:
        return "<?>"
    if len(parts) == 1:
        return parts[0]
    ext = ntpath.splitext(parts[-1])[1]
    return f"{parts[0]}\\{parts[1]}\\<...>{ext}"


def load_events(trace_dir: Path) -> Tuple[List[Event], List[str]]:
    events: List[Event] = []
    errors: List[str] = []
    files = sorted(Path(trace_dir).glob("trace-*.jsonl"))
    if not files:
        errors.append("no trace-*.jsonl file in the trace folder")
    for f in files:
        for n, line in enumerate(f.read_text(encoding="utf-8", errors="replace").splitlines(), start=1):
            if not line.strip():
                continue
            try:
                obj = json.loads(line)
            except ValueError:
                errors.append(f"{f.name} line {n}: not JSON")
                continue
            if not isinstance(obj, dict):
                continue
            name = str(obj.get("event") or obj.get("ev") or obj.get("name") or "")
            args = obj.get("args")
            if not isinstance(args, list):
                args = [obj[k] for k in ("path", "src", "mode", "dst", "flags") if k in obj]
            events.append(Event(name, list(args), obj, f.name))
    return events, errors


def _paths(e: Event) -> List[Tuple[str, bool]]:
    """(path, is_write) pairs of one event; ints (file descriptors) and None are skipped."""
    n = e.name.lower()
    a = e.args
    raw = e.raw

    def s(x: Any) -> Optional[str]:
        if isinstance(x, bytes):
            return x.decode("utf-8", "replace")
        return x if isinstance(x, str) and x else None

    if n == "open":
        path = s(raw.get("path")) or (s(a[0]) if a else None)
        mode = raw.get("mode", a[1] if len(a) > 1 else None)
        flags = raw.get("flags", a[2] if len(a) > 2 else 0)
        write = (isinstance(mode, str) and any(c in mode for c in "wax+")) or (
            isinstance(flags, int) and bool(flags & OPEN_WRITE_FLAGS))
        return [(path, write)] if path else []
    if n in ("os.rename", "os.replace", "shutil.copyfile", "shutil.copytree", "shutil.move", "shutil.copymode",
             "shutil.copystat", "os.link", "os.symlink"):
        src = s(raw.get("src")) or (s(a[0]) if a else None)
        dst = s(raw.get("dst")) or (s(a[1]) if len(a) > 1 else None)
        src_write = n in ("os.rename", "os.replace", "shutil.move")
        return [p for p in ((src, src_write), (dst, True)) if p[0]]
    path = s(raw.get("path")) or next((s(x) for x in a if s(x)), None)
    if not path:
        return []
    return [(path, n in WRITE_EVENTS)]


def check_events(events: Sequence[Event], roots: Roots, extra_reg: Sequence[str] = ()) -> Dict[str, Any]:
    findings: List[Finding] = []
    counts: Dict[str, int] = {}
    allowed_roots = [roots.install, roots.data, roots.input, roots.report, roots.trace, roots.windir]
    reg_ok = tuple(x.lower() for x in EXPECTED_REG) + tuple(x.lower() for x in extra_reg)
    for e in events:
        n = e.name.lower()
        counts[n] = counts.get(n, 0) + 1
        if n.startswith("socket."):
            if n in NETWORK_SOCKET_EVENTS and not any(e.raw.get(k) for k in REFUSED_KEYS):
                findings.append(Finding("socket", e.name, "-", "a network socket event that was not refused"))
            continue
        if n.startswith("onnxruntime"):
            findings.append(Finding("onnx", e.name, "-", "an onnxruntime session in the GPU build"))
            continue
        if n in REG_WRITES:
            findings.append(Finding("registry-write", e.name, "-", "a registry write"))
            continue
        if n.startswith("winreg.openkey") or n == "winreg.connectregistry":
            sub = e.raw.get("sub_key")
            if sub is None and len(e.args) > 1:
                sub = e.args[1]
            sub_s = str(sub or "").lower().strip("\\")
            if n == "winreg.connectregistry" or not any(sub_s.startswith(k) for k in reg_ok):
                findings.append(Finding("registry", e.name, "-", "an unexpected registry key"))
            continue
        if n.startswith(EXEC_EVENTS):
            exe_s = _exe_of(e)
            if n == "os.startfile":
                ok = bool(exe_s) and _under(_abs(exe_s, roots.cwd), roots.report)
            else:
                ok = n == "subprocess.popen" and bool(exe_s) and _under(_abs(exe_s, roots.cwd), roots.install)
            if not ok:
                findings.append(Finding("process", e.name, roots.token(_abs(exe_s, roots.cwd)) if exe_s else "-",
                                        "a process start other than the app's own exe"))
            continue
        if n == "ctypes.dlopen":
            name = e.args[0] if e.args else e.raw.get("path")
            name_s = name.decode("utf-8", "replace") if isinstance(name, bytes) else str(name or "")
            if not name_s or ("\\" not in name_s and "/" not in name_s):
                continue                                           # a bare DLL name (or the process itself)
        for path, write in _paths(e):
            low = path.strip().lower()
            if low in DEVICES or low.startswith("\\\\.\\") and low[4:] in DEVICES:
                continue
            ap = _abs(path, roots.cwd)
            if _under(ap, BOT_ROOT):
                findings.append(Finding("bot", e.name, "C:\\Hangeul\\BOT\\<...>", "an access under C:\\Hangeul\\BOT"))
                continue
            if write and _under(ap, roots.install):
                findings.append(Finding("write-install", e.name, "<INSTALL>", "a write under the install folder"))
                continue
            if any(_under(ap, r) for r in allowed_roots if r):
                continue
            kind = "temp" if roots.temp and _under(ap, roots.temp) else "outside"
            if (e.name.lower(), kind) in EXCEPTIONS:
                continue
            findings.append(Finding(kind, e.name, mask(ap), "write" if write else "read"))
    return {"events": len(events), "counts": counts, "findings": findings}


def _abs(p: str, cwd: str) -> str:
    """A path as the app saw it: relative ones against the working folder (DATA, set by boot)."""
    s = str(p)
    drive, rest = ntpath.splitdrive(s)
    if drive and rest.startswith(("\\", "/")):
        return s
    return ntpath.join(cwd, s) if cwd else s


def _exe_of(e: Event) -> str:
    """The program a process-start event names: ``executable``, else argv[0] (a command line's first token)."""
    def text(x: Any) -> str:
        return x.decode("utf-8", "replace") if isinstance(x, bytes) else (x if isinstance(x, str) else "")
    exe = text(e.raw.get("executable")) or (text(e.args[0]) if e.args else "")
    if exe:
        return exe
    argv = e.args[1] if len(e.args) > 1 else None
    if isinstance(argv, (list, tuple)) and argv:
        return text(argv[0])
    line = text(argv).strip()
    if line.startswith('"'):
        return line[1:].split('"', 1)[0]
    return line.split(" ", 1)[0] if line else ""


# ---------------------------------------------------------------------------------------------- the manifest

def manifest_entries(obj: Any) -> Dict[str, Dict[str, Any]]:
    """{relative path (/): {"size": n, "sha256": h}} from MANIFEST.json (Task F's file; the shapes accepted:
    {"files": [{"path", "size", "sha256"}]}, {"files": {path: {...}}}, {path: {...}}, [{"path", ...}])."""
    files = obj.get("files", obj) if isinstance(obj, dict) else obj
    out: Dict[str, Dict[str, Any]] = {}
    if isinstance(files, dict):
        for k, v in files.items():
            if isinstance(v, dict):
                out[str(k).replace("\\", "/")] = v
    elif isinstance(files, list):
        for v in files:
            if isinstance(v, dict) and (v.get("path") or v.get("file")):
                out[str(v.get("path") or v.get("file")).replace("\\", "/")] = v
    return out


def check_manifest(install: Path, manifest: Path, hash_files: bool = False) -> List[Finding]:
    out: List[Finding] = []
    try:
        entries = manifest_entries(json.loads(Path(manifest).read_text(encoding="utf-8")))
    except (OSError, ValueError) as exc:
        return [Finding("manifest", "-", "-", f"MANIFEST.json unreadable: {type(exc).__name__}")]
    if not entries:
        return [Finding("manifest", "-", "-", "MANIFEST.json lists no files")]
    have: Dict[str, Path] = {}
    for dirpath, _dirs, files in os.walk(install):
        for fn in files:
            p = Path(dirpath) / fn
            have[p.relative_to(install).as_posix()] = p
    low_entries = {k.lower(): (k, v) for k, v in entries.items()}
    try:
        man_key = Path(manifest).resolve().relative_to(Path(install).resolve()).as_posix().lower()
    except ValueError:
        man_key = ""
    for rel, p in have.items():
        if rel.lower() not in low_entries and rel.lower() != man_key:
            out.append(Finding("manifest", "-", "<INSTALL>", f"a file not in MANIFEST.json ({ntpath.splitext(rel)[1]})"))
    for k, (rel, v) in low_entries.items():
        p = next((have[h] for h in have if h.lower() == k), None)
        if p is None:
            out.append(Finding("manifest", "-", "<INSTALL>", "a MANIFEST.json file is missing"))
            continue
        if v.get("size") is not None and p.stat().st_size != int(v["size"]):
            out.append(Finding("manifest", "-", "<INSTALL>", "a file's size changed"))
            continue
        if hash_files and v.get("sha256"):
            h = hashlib.sha256()
            with open(p, "rb") as fh:
                for chunk in iter(lambda: fh.read(2 ** 20), b""):
                    h.update(chunk)
            if h.hexdigest().lower() != str(v["sha256"]).lower():
                out.append(Finding("manifest", "-", "<INSTALL>", "a file's sha256 changed"))
    return out


# ---------------------------------------------------------------------------------------------- report

def run_check(trace: Path, install: Path, data: Path, input_path: Path, report: Path, cwd: Optional[Path] = None,
              manifest: Optional[Path] = None, hash_files: bool = False, extra_reg: Sequence[str] = (),
              environ: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
    env = os.environ if environ is None else environ
    roots = Roots(install=str(install), data=str(data), input=str(input_path), report=str(report), trace=str(trace),
                  windir=env.get("WINDIR") or env.get("SystemRoot") or r"C:\Windows",
                  temp=env.get("TEMP") or env.get("TMP") or "", cwd=str(cwd or data))
    events, errors = load_events(trace)
    res = check_events(events, roots, extra_reg)
    findings: List[Finding] = res["findings"]
    if manifest is not None:
        findings.extend(check_manifest(Path(install), Path(manifest), hash_files))
    by_kind: Dict[str, int] = {}
    for f in findings:
        by_kind[f.kind] = by_kind.get(f.kind, 0) + 1
    return {"passed": not findings and not errors, "events": res["events"], "counts": res["counts"],
            "errors": errors, "by_kind": by_kind, "findings": [f.__dict__ for f in findings],
            "limits": ["native opens (MuPDF fonts, Windows loading DLLs, NVIDIA ComputeCache/ProgramData) are not "
                       "audit events"]}


def print_result(r: Dict[str, Any]) -> None:
    print(f"8 c trace: {r['events']} events | findings {sum(r['by_kind'].values())} {r['by_kind'] or ''}")
    for e in r["errors"]:
        print(f"  FAIL {e}")
    seen = set()
    for f in r["findings"]:
        k = (f["kind"], f["event"], f["where"], f["detail"])
        if k in seen:
            continue
        seen.add(k)
        print(f"  FAIL {f['kind']}: {f['event']} {f['where']} ({f['detail']})")
    for lim in r["limits"]:
        print(f"  limit: {lim}")
    print(f"8 c {'PASS' if r['passed'] else 'FAIL'}")


def _cmd_check(a: argparse.Namespace) -> int:
    r = run_check(Path(a.trace), Path(a.install), Path(a.data), Path(a.input), Path(a.report),
                  Path(a.cwd) if a.cwd else None, Path(a.manifest) if a.manifest else None, a.hash, a.allow_reg)
    print_result(r)
    out = Path(a.json) if a.json else Path(a.trace).parent / "trace_check.json"
    out.write_text(json.dumps(r, indent=1, ensure_ascii=True), encoding="utf-8")
    return 0 if r["passed"] else 1


def _cmd_run(a: argparse.Namespace, run_fn: Any = None) -> int:
    import cardrun
    import parity
    try:
        app = cardrun.app_dir_for(a.build_id or None, a.app_dir or None)
        case = parity.load_r8(parity.CHECK_CASE, parity.RUNS)
    except (ValueError, OSError, KeyError) as exc:
        print(f"8 c: cannot start: {type(exc).__name__}: {exc}")
        return 2
    exe = app / cardrun.APP_EXE
    if not exe.is_file():
        print("8 c: cannot start: the app exe was not found in the app folder")
        return 2
    root = cardrun.new_run_dir(cardrun.apptest_dir(a.build_id or None), "trace")
    trace, data, reports = root / "trace", root / "data", root / "reports"
    for d in (trace, data, reports):
        d.mkdir(parents=True, exist_ok=True)
    cmd = cardrun.auto_cmd(exe, case.input, case.program, case.today, reports, root / "auto.json", data, trace)
    try:
        res = (run_fn or cardrun.run)(cmd, a.timeout, what="hdc 8c trace", env=cardrun.app_env(data), log_dir=root)
    except cardrun.STOP_ERRORS as exc:
        print(f"8 c: stopped: {type(exc).__name__}: {exc}")
        return 2
    print(f"8 c run: exit {res.returncode}{' TIMEOUT' if res.timed_out else ''} | run folder {root.name}")
    man = app / "_internal" / "MANIFEST.json"
    r = run_check(trace, app, data, Path(case.input), reports, None, man if man.is_file() else None, False,
                  a.allow_reg)
    print_result(r)
    (root / "trace_check.json").write_text(json.dumps(r, indent=1, ensure_ascii=True), encoding="utf-8")
    return 0 if (r["passed"] and res.returncode == 0) else 1


def main(argv: Optional[Sequence[str]] = None, run_fn: Any = None) -> int:
    ap = argparse.ArgumentParser(description="8 c runtime trace check")
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("check")
    c.add_argument("--trace", required=True)
    c.add_argument("--install", required=True)
    c.add_argument("--data", required=True)
    c.add_argument("--input", required=True)
    c.add_argument("--report", required=True)
    c.add_argument("--cwd", default="")
    c.add_argument("--manifest", default="")
    c.add_argument("--hash", action="store_true")
    c.add_argument("--json", default="")
    c.add_argument("--allow-reg", action="append", default=[])
    r = sub.add_parser("run")
    r.add_argument("--build-id", default="")
    r.add_argument("--app-dir", default="")
    r.add_argument("--timeout", type=float, default=3600.0)
    r.add_argument("--allow-reg", action="append", default=[])
    a = ap.parse_args(argv)
    try:
        return _cmd_check(a) if a.cmd == "check" else _cmd_run(a, run_fn)
    except Exception as exc:
        print(f"8 c: could not run: {type(exc).__name__}: {exc}")
        return 2


if __name__ == "__main__":
    sys.exit(main())
