"""Test P3: the install cycle of the Setup.exe (APP_SPEC revision 2, R2.6 P3; revision 1, 8 e).

    %RPY% -I -B packaging\\tests\\install_cycle.py --build-id <id>

It NEVER touches an install or the data of the owner. It runs only when none of these exists, else it reports
SKIPPED (exit 3) and changes nothing:
  - the HKCU uninstall key ``{AppId}_is1`` of the app (any install of it, wherever it went),
  - ``%LOCALAPPDATA%\\Hangeul Document Checker`` (the app's data),
  - ``%LOCALAPPDATA%\\Programs\\Hangeul Document Checker`` (the default install folder),
  - a Start-menu or desktop shortcut "Hangeul Document Checker",
  - a non-empty ``%LOCALAPPDATA%\\Temp\\HDC install test`` (a leftover of an earlier run).

Steps (all as this normal user; Setup and the uninstaller run at BELOW_NORMAL inside a job object, so their whole
process tree is waited for and nothing is left running):
  1. ``Setup.exe /VERYSILENT /SUPPRESSMSGBOXES /NORESTART /DIR="%LOCALAPPDATA%\\Temp\\HDC install test" /TASKS=""
     /LOG="<reports>\\install-1.log"``, then the checks below, then the installed ``--selftest`` through
     ``cardrun.run`` (Task H; the card is used only there).
  2. A made-up file is put in ``_internal``; Setup runs again over the install (the upgrade path); its
     ``[InstallDelete]`` must remove that file; the same checks and the self-test again.
  2b. Only with ``--case <r8 run>`` (G3): the installed app checks that one case with ``--cli`` through
     ``cardrun.run``, with its REAL data folder (no ``--data-dir``), so the case's saved page texts land where a
     user's would. Its result code must equal P1's (``builds\\<id>\\reports\\parity.json``), its verdicts must
     equal r8's (parity.py's (b)-(d)), and its page texts must equal P1's read of the same case page by page.
     Its reports go to a new private folder ``private-runner\\apptest\\<id>\\p3-<stamp>``.
  3. ``unins000.exe /VERYSILENT /SUPPRESSMSGBOXES /NORESTART /LOG=...``; then the test folder, the uninstall key,
     the shortcuts and the data folder made by the self-test (and by the case) must all be gone (the silent
     uninstall answers the data question with Yes).
  The uninstall also runs when a check failed, so a failed run leaves no install behind; it only ever runs the
  uninstaller of the test folder, and only when the uninstall key (if any) points at the test folder.

Checks after each install: every installed file equals the build's MANIFEST.json (size and sha256; nothing extra
except MANIFEST.json itself and Inno's unins000.exe/.dat/.msg); the installed MANIFEST.json equals the build's;
the HKCU key with DisplayVersion = the app version, InstallLocation = the test folder and DisplayName; the
Start-menu shortcut points at the installed exe; no desktop shortcut (/TASKS=""); the installed BUILD.json
``build_id`` = the Setup's ``/DBuildId``; the installed ``--selftest`` exits 0.

The installed self-test uses the card through Task H's cardrun.py, which refuses until the orchestrator has approved
Q-O1 (``HDC_CARDRUN_APPROVAL=Q-O1``); without it the self-test check fails with that hint. Run P3 only when no other
heavy job runs (one heavy process at a time): Setup unpacks about 4.6 GB twice.

With ``--case``: ``%RPY% -I -B packaging\\tests\\install_cycle.py --build-id <id> --case owner_eap1_r8``.

``--data-dir <new folder outside AppData>`` (G3): the installed self-test and case get ``--data-dir`` instead of the
real ``%LOCALAPPDATA%\\Hangeul Document Checker``. Needed when this script runs inside a packaged (MSIX) app's
process tree, such as the Claude desktop app's: Windows then redirects a NEW top-level folder of %LOCALAPPDATA% into
``%LOCALAPPDATA%\\Packages\\<package>\\LocalCache\\Local``, so DATA's real path differs from its name and the app's
guard (safety._private) refuses the cache root (exit 2, REPORT_NOT_WRITABLE; self-test part ``guard`` FAIL).
Existing folders such as ``%LOCALAPPDATA%\\Temp`` and ``Programs`` are not redirected, so Setup's files are real.
Before the uninstall a made-up file is then put in the data folder, so the uninstaller's data question still runs.

Exit codes: 0 PASS; 1 FAIL; 2 bad arguments or missing build files; 3 SKIPPED; 4 PARTIAL (--no-selftest).
Output: ok/MISS lines with counts, versions, codes and the app's own relative file names; never an absolute path,
a user name or any student data. ``<reports>\\install_cycle.json`` holds the same. Default reports folder:
``<builds>\\<id>\\reports\\install_cycle``.
"""
from __future__ import annotations

import argparse
import inspect
import json
import os
import re
import struct
import sys
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Mapping, Optional, Tuple

HERE = Path(__file__).resolve().parent
_INSTALLER_DIR = HERE.parent / "installer"
if str(_INSTALLER_DIR) not in sys.path:
    sys.path.insert(0, str(_INSTALLER_DIR))
import build_installer as bi  # noqa: E402  (packaging\installer, Task G)

TEST_DIR_NAME = "HDC install test"
PROBE_REL = "_internal/hdc_install_cycle_probe.txt"
PROBE_TEXT = "made-up file of the install cycle test; the upgrade's [InstallDelete] must remove it\n"
MARKER_NAME = "hdc_install_cycle_marker.txt"
MARKER_TEXT = "made-up file of the install cycle test; the uninstaller's data question must delete it\n"
EXIT_PASS, EXIT_FAIL, EXIT_USAGE, EXIT_SKIPPED, EXIT_PARTIAL = 0, 1, 2, 3, 4
INSTALL_TIMEOUT_S = 3600.0
UNINSTALL_TIMEOUT_S = 1800.0
SELFTEST_TIMEOUT_S = 2700.0      # 9 parts; the card part alone may wait 900 s for the bot
GRACE_S = 120.0                  # after the uninstaller: time for Explorer or an antivirus to let go of a file
# Test-only variables of the app (rev 1 8 a, R2.3.3). The installed self-test must run as on a user's PC: with
# HDC_TEST_NO_CARD it would refuse the card, and with HDC_DATA_DIR its data would not be the folder the uninstall
# question deletes. main() removes them from this process's environment, so cardrun's child does not inherit them.
TEST_ENV_VARS = ("HDC_TEST_NO_CARD", "HDC_DATA_DIR", "HDC_MODE", "HDC_BUNDLE_DIR", "HDC_CODE_ROOT")

FOLDERID_PROGRAMS = "A77F5D77-2E2B-44C3-A6A2-ABA601054A51"       # {autoprograms} of a per-user install
FOLDERID_DESKTOP = "B4BFCC3A-DB2C-424C-B029-7FE99A87C641"        # {autodesktop} of a per-user install
FOLDERID_LOCAL_APPDATA = "F1B32785-6FBA-4FCF-9D55-7B8E7F157091"


# ----------------------------------------------------------------------------------------------------------------
# Places

@dataclass(frozen=True)
class Places:
    local_appdata: Path
    programs: Path          # the user's Start-menu "Programs" folder
    desktop: Path           # the user's desktop folder

    @property
    def test_dir(self) -> Path:
        return self.local_appdata / "Temp" / TEST_DIR_NAME

    @property
    def data_dir(self) -> Path:
        return self.local_appdata / bi.APP_NAME

    @property
    def default_dir(self) -> Path:
        return self.local_appdata / "Programs" / bi.APP_NAME

    @property
    def start_lnk(self) -> Path:
        return self.programs / (bi.APP_NAME + ".lnk")

    @property
    def desktop_lnk(self) -> Path:
        return self.desktop / (bi.APP_NAME + ".lnk")

    @property
    def exe(self) -> Path:
        return self.test_dir / bi.EXE_NAME


def known_folder(guid: str) -> Optional[Path]:
    """SHGetKnownFolderPath (the folders Inno Setup uses for {autoprograms}, {autodesktop}, {localappdata})."""
    if os.name != "nt":
        return None
    try:
        import ctypes
        import uuid
        from ctypes import wintypes
        raw = (ctypes.c_ubyte * 16).from_buffer_copy(uuid.UUID(guid).bytes_le)
        shell32 = ctypes.WinDLL("shell32")
        ole32 = ctypes.WinDLL("ole32")
        shell32.SHGetKnownFolderPath.argtypes = [ctypes.c_void_p, wintypes.DWORD, wintypes.HANDLE,
                                                 ctypes.POINTER(ctypes.c_void_p)]
        shell32.SHGetKnownFolderPath.restype = ctypes.c_long
        ole32.CoTaskMemFree.argtypes = [ctypes.c_void_p]
        ole32.CoTaskMemFree.restype = None
        out = ctypes.c_void_p()
        if shell32.SHGetKnownFolderPath(ctypes.byref(raw), 0, None, ctypes.byref(out)) != 0 or not out.value:
            return None
        try:
            return Path(ctypes.wstring_at(out.value))
        finally:
            ole32.CoTaskMemFree(out)
    except Exception:
        return None


def real_places() -> Places:
    la = os.environ.get("LOCALAPPDATA")
    local = Path(la) if la else known_folder(FOLDERID_LOCAL_APPDATA)
    if local is None:
        raise RuntimeError("LOCALAPPDATA is unknown")
    programs = known_folder(FOLDERID_PROGRAMS)
    if programs is None:
        programs = Path(os.environ["APPDATA"]) / "Microsoft" / "Windows" / "Start Menu" / "Programs"
    desktop = known_folder(FOLDERID_DESKTOP)
    if desktop is None:
        desktop = Path(os.environ["USERPROFILE"]) / "Desktop"
    return Places(local, programs, desktop)


def same_path(a: Any, b: Any) -> bool:
    if not a or not b:
        return False
    na = os.path.normcase(os.path.normpath(str(a).rstrip("\\/")))
    nb = os.path.normcase(os.path.normpath(str(b).rstrip("\\/")))
    return na == nb


# ----------------------------------------------------------------------------------------------------------------
# The uninstall key and shortcuts (read-only)

def read_uninstall_key() -> Optional[Dict[str, Any]]:
    """The values of HKCU\\...\\Uninstall\\{AppId}_is1 (64-bit view, then 32-bit view), or None."""
    import winreg
    for view in (winreg.KEY_WOW64_64KEY, winreg.KEY_WOW64_32KEY):
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, bi.UNINSTALL_KEY, 0, winreg.KEY_READ | view) as k:
                values: Dict[str, Any] = {}
                i = 0
                while True:
                    try:
                        name, value, _kind = winreg.EnumValue(k, i)
                    except OSError:
                        break
                    values[name] = value
                    i += 1
                return values
        except FileNotFoundError:
            continue
    return None


LNK_CLSID = bytes.fromhex("0114020000000000c000000000000046")      # a COM CLSID (pii-scan: not a number)


def _utf16z(data: bytes, pos: int) -> str:
    end = pos
    while end + 1 < len(data) and data[end:end + 2] != b"\0\0":
        end += 2
    return data[pos:end].decode("utf-16-le", "replace")


def _ansiz(data: bytes, pos: int) -> str:
    end = data.index(b"\0", pos)
    raw = data[pos:end]
    try:
        return raw.decode("mbcs")
    except (LookupError, UnicodeDecodeError):
        return raw.decode("latin-1")


def parse_lnk(data: bytes) -> Dict[str, Optional[str]]:
    """The target of a Windows shortcut ([MS-SHLLINK]): LinkInfo's local path (the Unicode one when present),
    plus the relative path, working folder and arguments strings. ValueError when it is not a shell link."""
    if len(data) < 0x4C or struct.unpack_from("<I", data, 0)[0] != 0x4C or data[4:20] != LNK_CLSID:
        raise ValueError("not a shell link")
    flags = struct.unpack_from("<I", data, 0x14)[0]
    pos = 0x4C
    if flags & 0x01:                                    # HasLinkTargetIDList
        pos += 2 + struct.unpack_from("<H", data, pos)[0]
    target = None
    if flags & 0x02:                                    # HasLinkInfo
        li = pos
        size, header, li_flags = struct.unpack_from("<III", data, li)
        _vol, base_off, _net, suffix_off = struct.unpack_from("<IIII", data, li + 12)
        if li_flags & 0x01:                             # VolumeIDAndLocalBasePath
            if header >= 0x24:
                ubase, usuffix = struct.unpack_from("<II", data, li + 28)
                target = _utf16z(data, li + ubase) + (_utf16z(data, li + usuffix) if usuffix else "")
            else:
                target = _ansiz(data, li + base_off) + (_ansiz(data, li + suffix_off) if suffix_off else "")
        pos = li + size
    out: Dict[str, Optional[str]] = {"target": target or None}
    unicode = bool(flags & 0x80)
    for bit, name in ((0x04, "name"), (0x08, "relative"), (0x10, "workdir"), (0x20, "args"), (0x40, "icon")):
        if flags & bit:
            n = struct.unpack_from("<H", data, pos)[0]
            pos += 2
            if unicode:
                out[name] = data[pos:pos + 2 * n].decode("utf-16-le", "replace")
                pos += 2 * n
            else:
                out[name] = data[pos:pos + n].decode("latin-1")
                pos += n
    return out


def lnk_target(path: Path) -> Optional[str]:
    """The full target path of a shortcut file, or None when it cannot be read."""
    try:
        info = parse_lnk(Path(path).read_bytes())
    except (OSError, ValueError, struct.error, IndexError):
        return None
    if info.get("target"):
        return info["target"]
    rel = info.get("relative")
    if rel:
        return os.path.normpath(os.path.join(str(Path(path).parent), rel))
    return None


# ----------------------------------------------------------------------------------------------------------------
# Side effects, behind one object so the unit tests can replace them

def exit_code_of(result: Any) -> Optional[int]:
    """cardrun.run's result as an exit code: an int, or an object/map with returncode / exit_code / code / rc."""
    if result is None or isinstance(result, bool):
        return None
    if isinstance(result, int):
        return result
    for name in ("returncode", "exit_code", "exitcode", "code", "rc"):
        v = result.get(name) if isinstance(result, Mapping) else getattr(result, name, None)
        if isinstance(v, int) and not isinstance(v, bool):
            return v
    if isinstance(result, tuple) and result and isinstance(result[0], int) and not isinstance(result[0], bool):
        return result[0]
    return None


def _load_cardrun() -> Any:
    if str(HERE) not in sys.path:
        sys.path.insert(0, str(HERE))
    import cardrun  # noqa: E402  (packaging\tests\cardrun.py, Task H)
    return cardrun


def cardrun_selftest(exe: Path, report_dir: Path, timeout_s: float,
                     loader: Callable[[], Any] = _load_cardrun, data_dir: Optional[Path] = None
                     ) -> Tuple[Optional[int], str]:
    """``<exe> --selftest --report-dir <dir>`` through Task H's ``cardrun.run(cmd, timeout)`` (R2.6, Q-O1).

    No ``--data-dir`` by default: the self-test uses the app's real data folder, which the silent uninstall must then
    delete. ``data_dir`` (``--data-dir`` of this script, see there) adds ``--data-dir <dir>``.
    When cardrun.run takes ``what`` / ``env`` and offers ``app_env()`` (the environment without the test switches
    and the approval variable), they are passed too."""
    try:
        cardrun = loader()
    except Exception as e:
        return None, "cardrun.py could not be loaded (%s)" % type(e).__name__
    cmd = [str(exe)] + (["--data-dir", str(data_dir)] if data_dir is not None else []) + [
        "--selftest", "--report-dir", str(report_dir)]
    kwargs: Dict[str, Any] = {}
    try:
        params = inspect.signature(cardrun.run).parameters
    except (TypeError, ValueError):
        params = {}
    if "what" in params:
        kwargs["what"] = "hdc install cycle (P3): installed --selftest"
    if "env" in params and callable(getattr(cardrun, "app_env", None)):
        kwargs["env"] = cardrun.app_env() if data_dir is None else cardrun.app_env(data_dir)
    try:
        result = cardrun.run(cmd, timeout_s, **kwargs)
    except Exception as e:
        hint = (" (Q-O1: set HDC_CARDRUN_APPROVAL=Q-O1 only after the orchestrator approved it)"
                if type(e).__name__ == "NotApproved" else "")
        return None, "cardrun.run raised %s%s" % (type(e).__name__, hint)
    code = exit_code_of(result)
    return code, "" if code is not None else "cardrun.run gave no exit code"


# ----------------------------------------------------------------------------------------------------------------
# One r8 case on the INSTALLED app (--case; G3: the installed app must give P1's result code)

RUNS = Path(r"C:\Hangeul\DOCCHECK\lab\private-runner\runs")
APPTEST = Path(r"C:\Hangeul\DOCCHECK\lab\private-runner\apptest")
CASE_TIMEOUT_S = 3600.0          # one check incl. the app's own waits for the bot (= parity.DEFAULT_TIMEOUT_S)
CASE_DONE_EXITS = (0, 3)         # rev 1 section 2: 0 done, 3 done with STOP
CODE_RE = re.compile(r"^[0-9A-F]{4}-[0-9A-F]{4}$")
LABEL_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.\-]{0,63}$")


@dataclass
class CaseSpec:
    """One r8 case for the installed app. ``input`` is a student's file or folder: it is never printed."""
    label: str                       # the r8 run, e.g. owner_eap1_r8
    input: str
    program: str
    today: Optional[str]
    case_id: str
    mode: str
    r8_dir: Path                     # private-runner\runs\<label>
    expect_code: str                 # P1's result code of this case
    apptest: Path                    # private-runner\apptest\<id>: a new p3-<stamp> folder is made in it
    p1_store: Optional[Path] = None  # P1's page-texts file of the same case (the frozen dist's read)
    p1_r8_differing: Optional[List[int]] = None   # the pages P1 read differently from r8 (explained there)
    timeout_s: float = CASE_TIMEOUT_S


def case_command(exe: Path, spec: CaseSpec, out_dir: Path, data_dir: Optional[Path] = None) -> List[str]:
    """R2.3.3 ``--cli <input> --program P --out DIR [--today D]``; without ``data_dir`` no ``--data-dir``: the app's
    real data folder, as on a user's PC, which the uninstall must then delete."""
    cmd = [str(exe), "--cli", str(spec.input), "--program", spec.program, "--out", str(out_dir)]
    if spec.today:
        cmd += ["--today", spec.today]
    if data_dir is not None:
        cmd += ["--data-dir", str(data_dir)]
    return cmd


def case_store(data_dir: Path, case_id: str) -> Optional[Path]:
    """The case's OWN page-texts file under DATA (``cache\\pagetexts\\<case_id>.json``), never another one (the
    self-test's)."""
    if not case_id:
        return None
    p = Path(data_dir) / "cache" / "pagetexts" / (case_id + ".json")
    if p.is_file():
        return p
    hits = sorted(Path(data_dir).rglob("pagetexts/%s.json" % case_id)) if Path(data_dir).is_dir() else []
    return hits[0] if hits else None


def new_run_dir(base: Path, prefix: str) -> Path:
    """A NEW folder base\\<prefix>-<YYYYmmdd-HHMMSS>[-n] (as cardrun.new_run_dir); earlier runs are kept."""
    stamp = time.strftime("%Y%m%d-%H%M%S")
    Path(base).mkdir(parents=True, exist_ok=True)
    for n in range(1000):
        d = Path(base) / ("%s-%s%s" % (prefix, stamp, "-%d" % n if n else ""))
        try:
            d.mkdir()
            return d
        except FileExistsError:
            continue
    raise RuntimeError("no free run folder name")


def _load_parity() -> Any:
    if str(HERE) not in sys.path:
        sys.path.insert(0, str(HERE))
    import parity  # noqa: E402  (packaging\tests\parity.py, P1)
    return parity


def _pages_of(rep: Mapping[str, Any]) -> List[int]:
    return [int(d["page"]) for d in rep.get("differing") or [] if isinstance(d, Mapping) and "page" in d]


def cardrun_case(exe: Path, spec: CaseSpec, out_dir: Path, data_dir: Path, *, override: bool = False,
                 loader: Callable[[], Any] = _load_cardrun,
                 judge_loader: Callable[[], Any] = _load_parity) -> Dict[str, Any]:
    """``case_command`` through ``cardrun.run`` (env ``cardrun.app_env()``: no test switches, no HDC_DATA_DIR, no
    approval variable; with ``override`` the app gets ``--data-dir data_dir`` and HDC_DATA_DIR), judged with
    parity.py's (a)-(d) against r8 and its page texts against P1's. Returns counts, codes and page numbers only."""
    f: Dict[str, Any] = {"exit": None, "timed_out": False, "code": None, "why": ""}
    try:
        cardrun = loader()
        parity = judge_loader()
    except Exception as e:
        f["why"] = "cardrun.py or parity.py could not be loaded (%s)" % type(e).__name__
        return f
    try:
        res = cardrun.run(case_command(exe, spec, out_dir, data_dir if override else None), spec.timeout_s,
                          what="hdc install cycle (P3): installed --cli %s" % spec.label,
                          env=cardrun.app_env(data_dir) if override else cardrun.app_env(),
                          log_dir=Path(out_dir).parent)
    except Exception as e:
        hint = (" (Q-O1: set HDC_CARDRUN_APPROVAL=Q-O1 only after the orchestrator approved it)"
                if type(e).__name__ == "NotApproved" else "")
        f["why"] = "cardrun.run raised %s%s" % (type(e).__name__, hint)
        return f
    f["exit"] = exit_code_of(res)
    f["timed_out"] = bool(getattr(res, "timed_out", False))
    stats = res.stats() if callable(getattr(res, "stats", None)) else {}
    events = cardrun.events_of(getattr(res, "lines", None) or [])
    done = cardrun.done_event(events) or {}
    gpu = done.get("gpu") or {}
    f.update(seconds=stats.get("seconds"), slot_wait_s=stats.get("slot_wait_s"),
             peak_working_set_mb=stats.get("peak_working_set_mb"),
             job_peak_process_commit_mb=stats.get("job_peak_process_commit_mb"),
             errors=list(cardrun.error_codes(events)), cpu_calls=gpu.get("cpu_calls"), waits=gpu.get("waits"),
             downscaled=gpu.get("downscaled"), skipped=gpu.get("skipped"))
    case = parity.R8Case(label=spec.label, desc=spec.label, dir=spec.r8_dir, input=spec.input, program=spec.program,
                         today=spec.today, case_id=spec.case_id, mode=spec.mode)
    rep = parity.judge_outputs(case, out_dir, data_dir)
    f["code"] = rep.get("code") or done.get("code")
    sm, rl, lk, pt = rep["summary"], rep["run"], rep["looks"], rep["page_texts"]
    f.update(verdicts_equal=bool(rep.get("verdicts_equal")), summary_equal=bool(sm.get("equal")),
             summary_lines=sm.get("lines_r8"), run_equal=bool(rl.get("equal")), looks_equal=lk.get("equal_looks"),
             looks_r8=lk.get("looks_r8"), verdict_diffs=len(sm.get("diffs") or []) + len(rl.get("diffs") or [])
             + len(lk.get("diffs") or []), r8_equal_pages=pt.get("equal_pages"), r8_pages=pt.get("pages_r8"),
             r8_differing=_pages_of(pt))
    store = case_store(data_dir, spec.case_id)
    f["store_in_data"] = store is not None
    if spec.p1_store is not None and store is not None:
        p1 = parity.compare_page_texts(cardrun.read_json(spec.p1_store), cardrun.read_json(store))
        f.update(p1_equal=bool(p1.get("equal")), p1_equal_pages=p1.get("equal_pages"), p1_pages=p1.get("pages_r8"),
                 p1_differing=_pages_of(p1), p1_problems=len(p1.get("problems") or []))
    return f


def load_case(build_id: str, builds: Path, label: str, *, case_input: Optional[str] = None,
              expect_code: Optional[str] = None, runs: Path = RUNS, apptest: Path = APPTEST,
              timeout_s: float = CASE_TIMEOUT_S) -> CaseSpec:
    """The case's input, program and ``today`` from r8's run.json (as parity.py), the expected code and P1's
    page-texts file from P1's parity.json of this build. ValueError (never a path) when something is missing."""
    if not LABEL_RE.match(label or ""):
        raise ValueError("--case takes an r8 run name such as owner_eap1_r8")
    if expect_code is not None and not CODE_RE.match(expect_code):
        raise ValueError("--expect-code must look like XXXX-XXXX (upper-case hex)")
    r8_dir = Path(runs) / label
    try:
        r = json.loads((r8_dir / "run.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raise ValueError("--case: the r8 run %s has no readable run.json" % label) from None
    try:
        p1 = json.loads((Path(builds) / build_id / "reports" / "parity.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        p1 = {}
    if p1 and p1.get("build_id") not in (None, build_id):
        raise ValueError("the build's parity.json belongs to another build id")
    p1_case = next((c for c in p1.get("cases") or [] if isinstance(c, Mapping) and c.get("label") == label), None)
    if expect_code is None:
        if not p1_case or not p1_case.get("ok") or not p1.get("passed"):
            raise ValueError("P1 (parity.json) has no passed result for %s: give --expect-code" % label)
        expect_code = str(p1_case.get("code") or "")
        if not CODE_RE.match(expect_code):
            raise ValueError("P1's code for %s is malformed" % label)
    inp = case_input or str(r.get("input") or "")
    if not inp or not Path(inp).is_absolute() or not Path(inp).exists():
        raise ValueError("the input of %s is not there (give an absolute --case-input stand-in)" % label)
    case_id = str(r.get("case_id") or "")
    p1_store = None
    if p1.get("run_folder") and p1_case and case_id:
        p = Path(apptest) / build_id / str(p1["run_folder"]) / label / "data" / "cache" / "pagetexts" / (case_id + ".json")
        p1_store = p if p.is_file() else None
    p1_diff = [int(d["page"]) for d in ((p1_case or {}).get("page_texts") or {}).get("differing") or []
               if isinstance(d, Mapping) and "page" in d] if p1_case else None
    return CaseSpec(label=label, input=inp, program=str(r.get("program") or ""), today=r.get("today") or None,
                    case_id=case_id, mode=str(r.get("mode") or ""), r8_dir=r8_dir, expect_code=expect_code,
                    apptest=Path(apptest) / build_id, p1_store=p1_store, p1_r8_differing=p1_diff,
                    timeout_s=timeout_s)


class System:
    """The real side effects on this PC."""

    def uninstall_key(self) -> Optional[Dict[str, Any]]:
        return read_uninstall_key()

    def run(self, cmd: str, *, timeout_s: float) -> bi.RunResult:
        return bi.run_tree(cmd, timeout_s=timeout_s, below_normal=True)

    def selftest(self, exe: Path, report_dir: Path, timeout_s: float,
                 data_dir: Optional[Path] = None) -> Tuple[Optional[int], str]:
        return cardrun_selftest(exe, report_dir, timeout_s, data_dir=data_dir)

    def check_case(self, exe: Path, spec: CaseSpec, out_dir: Path, data_dir: Path,
                   override: bool = False) -> Dict[str, Any]:
        return cardrun_case(exe, spec, out_dir, data_dir, override=override)

    def sleep(self, seconds: float) -> None:
        time.sleep(seconds)


def setup_command(setup_exe: Path, test_dir: Path, log_path: Path) -> str:
    for p in (setup_exe, test_dir, log_path):
        if '"' in str(p):
            raise ValueError("a path holds a double quote")
    return ('"%s" /VERYSILENT /SUPPRESSMSGBOXES /NORESTART /DIR="%s" /TASKS="" /LOG="%s"'
            % (setup_exe, test_dir, log_path))


def uninstall_command(uninstaller: Path, log_path: Path) -> str:
    for p in (uninstaller, log_path):
        if '"' in str(p):
            raise ValueError("a path holds a double quote")
    return '"%s" /VERYSILENT /SUPPRESSMSGBOXES /NORESTART /LOG="%s"' % (uninstaller, log_path)


# ----------------------------------------------------------------------------------------------------------------
# The cycle

@dataclass
class Ctx:
    build_id: str
    app_version: str
    setup_exe: Path
    manifest: Dict[str, bi.Entry]
    manifest_rel: str
    manifest_sha256: str
    reports: Path
    selftest: bool = True
    install_timeout_s: float = INSTALL_TIMEOUT_S
    uninstall_timeout_s: float = UNINSTALL_TIMEOUT_S
    selftest_timeout_s: float = SELFTEST_TIMEOUT_S
    grace_s: float = GRACE_S
    case: Optional[CaseSpec] = None
    app_data: Optional[Path] = None      # --data-dir: the installed app's data folder instead of the real one


@dataclass
class Check:
    step: str
    name: str
    ok: bool
    detail: str


@dataclass
class Outcome:
    status: str = "FAIL"
    reasons: List[str] = field(default_factory=list)
    checks: List[Check] = field(default_factory=list)
    notes: List[str] = field(default_factory=list)
    seconds: Dict[str, float] = field(default_factory=dict)
    facts: Dict[str, Any] = field(default_factory=dict)       # counts and codes only (the --case run)
    out: Callable[[str], None] = print

    def add(self, step: str, name: str, ok: bool, detail: str) -> bool:
        self.checks.append(Check(step, name, bool(ok), detail))
        self.out("  %-4s %-10s %s: %s" % ("ok" if ok else "MISS", step, name, detail))
        return bool(ok)

    def note(self, step: str, text: str) -> None:
        self.notes.append("%s: %s" % (step, text))
        self.out("  note %-10s %s" % (step, text))

    @property
    def failed(self) -> List[Check]:
        return [c for c in self.checks if not c.ok]

    def to_json(self) -> Dict[str, Any]:
        return {"status": self.status, "reasons": self.reasons, "checks": [asdict(c) for c in self.checks],
                "notes": self.notes, "seconds": self.seconds, "facts": self.facts}


def preconditions(pl: Places, sysm: Any) -> List[str]:
    """Reasons NOT to run (each means an install or data of the owner, or a leftover); empty = safe to run."""
    reasons = []
    if sysm.uninstall_key() is not None:
        reasons.append("the app's uninstall key {AppId}_is1 exists in HKCU (the app is installed)")
    if pl.data_dir.exists():
        reasons.append("%LOCALAPPDATA%\\Hangeul Document Checker exists (the app's data)")
    if pl.default_dir.exists():
        reasons.append("%LOCALAPPDATA%\\Programs\\Hangeul Document Checker exists (the default install folder)")
    if os.path.lexists(pl.start_lnk):
        reasons.append("a Start-menu shortcut 'Hangeul Document Checker' exists")
    if os.path.lexists(pl.desktop_lnk):
        reasons.append("a desktop shortcut 'Hangeul Document Checker' exists")
    if pl.test_dir.exists() and (not pl.test_dir.is_dir() or any(pl.test_dir.iterdir())):
        reasons.append("%LOCALAPPDATA%\\Temp\\HDC install test is not empty (a leftover of an earlier run)")
    return reasons


def check_install(ctx: Ctx, pl: Places, sysm: Any, o: Outcome, step: str) -> None:
    mkey = bi.rel_key(ctx.manifest_rel)
    rep = bi.compare_tree(pl.test_dir, ctx.manifest, allow_extra=lambda r: bi.rel_key(r) == mkey or bi.inno_own_file(r),
                          hash_files=True)
    o.add(step, "files", rep.ok, rep.summary() + " (size and sha256)")
    inno = sorted(r for r in rep.allowed_extra if bi.inno_own_file(r))
    o.add(step, "uninstaller", any(r.lower() == "unins000.exe" for r in inno), ", ".join(inno) or "no unins000.exe")
    m = pl.test_dir / ctx.manifest_rel
    same = m.is_file() and bi.sha256_file(m) == ctx.manifest_sha256
    o.add(step, "MANIFEST.json", same, "the installed MANIFEST.json %s the build's" % ("equals" if same else
                                                                                       "differs from"))
    key = sysm.uninstall_key()
    if o.add(step, "uninstall key", key is not None, "HKCU ...\\Uninstall\\{AppId}_is1 %s"
             % ("present" if key is not None else "missing")):
        dv = key.get("DisplayVersion")
        o.add(step, "DisplayVersion", dv == ctx.app_version, "%s (expected %s)" % (dv, ctx.app_version))
        o.add(step, "InstallLocation", same_path(key.get("InstallLocation"), pl.test_dir),
              "the test folder" if same_path(key.get("InstallLocation"), pl.test_dir) else "another folder")
        dn = key.get("DisplayName")
        o.add(step, "DisplayName", dn == bi.UNINSTALL_DISPLAY_NAME, "%s" % dn)
    target = lnk_target(pl.start_lnk) if os.path.lexists(pl.start_lnk) else None
    o.add(step, "Start menu", target is not None and same_path(target, pl.exe),
          "the shortcut points at the installed exe" if target is not None and same_path(target, pl.exe)
          else ("no shortcut" if not os.path.lexists(pl.start_lnk) else "the shortcut points elsewhere"))
    o.add(step, "desktop", not os.path.lexists(pl.desktop_lnk), "no desktop shortcut (/TASKS=\"\")"
          if not os.path.lexists(pl.desktop_lnk) else "a desktop shortcut was made although /TASKS=\"\"")
    try:
        bid = bi.read_build_json(pl.test_dir).get("build_id")
    except bi.BuildError:
        bid = None
    o.add(step, "BUILD.json", bid == ctx.build_id, "build_id %s (the Setup's /DBuildId %s)" % (bid, ctx.build_id))


def run_selftest(ctx: Ctx, pl: Places, sysm: Any, o: Outcome, step: str, n: int) -> None:
    report_dir = ctx.reports / ("selftest-%d" % n)
    report_dir.mkdir(parents=True, exist_ok=True)
    t0 = time.monotonic()
    if ctx.app_data is None:
        code, why = sysm.selftest(pl.exe, report_dir, ctx.selftest_timeout_s)
    else:
        code, why = sysm.selftest(pl.exe, report_dir, ctx.selftest_timeout_s, data_dir=ctx.app_data)
    o.seconds["selftest-%d" % n] = round(time.monotonic() - t0, 1)
    o.add(step, "self-test", code == 0, "installed --selftest exit %s through cardrun%s"
          % (code, ("; " + why) if why else ""))
    if not list(report_dir.glob("SELFTEST*.txt")):
        o.note(step, "no SELFTEST*.txt in the self-test's report folder")


def run_case(ctx: Ctx, pl: Places, sysm: Any, o: Outcome) -> None:
    """The --case check on the installed app (after the upgrade and its self-test, before the uninstall)."""
    spec = ctx.case
    step = "case"
    root = new_run_dir(spec.apptest, "p3")
    out_dir = root / spec.label / "out"
    out_dir.mkdir(parents=True, exist_ok=True)
    t0 = time.monotonic()
    if ctx.app_data is None:
        f = sysm.check_case(pl.exe, spec, out_dir, pl.data_dir)
    else:
        f = sysm.check_case(pl.exe, spec, out_dir, ctx.app_data, override=True)
    o.seconds[step] = round(time.monotonic() - t0, 1)
    o.facts[step] = dict(f, label=spec.label, case_id=spec.case_id, expect_code=spec.expect_code,
                         run_folder=root.name)
    ex, why = f.get("exit"), f.get("why") or ""
    o.add(step, "run", ex in CASE_DONE_EXITS and not f.get("timed_out"),
          "installed --cli %s (%s) exit %s%s through cardrun%s" % (spec.label, spec.case_id, ex,
                                                                   ", timed out" if f.get("timed_out") else "",
                                                                   ("; " + why) if why else ""))
    code = f.get("code")
    o.add(step, "result code", code == spec.expect_code, "%s (P1: %s)" % (code or "-", spec.expect_code))
    o.add(step, "verdicts", f.get("verdicts_equal") is True,
          "against r8: summary %s (%s lines), run.json %s, closer looks %s of %s equal"
          % ("equal" if f.get("summary_equal") else "DIFFERS", f.get("summary_lines"),
             "equal" if f.get("run_equal") else "DIFFERS", f.get("looks_equal"), f.get("looks_r8")))
    o.add(step, "data folder", f.get("store_in_data") is True,
          "the case's page texts were saved in the app's data folder" if f.get("store_in_data")
          else "no page-texts file of the case in the app's data folder")
    if "p1_equal" in f:
        o.add(step, "page texts", f["p1_equal"] is True, "%s of %s pages equal to P1's read of the same case%s"
              % (f.get("p1_equal_pages"), f.get("p1_pages"),
                 "" if f["p1_equal"] else " (differing pages %s, %s problem(s))"
                 % (f.get("p1_differing"), f.get("p1_problems"))))
    elif spec.p1_store is None:
        o.note(step, "P1's page texts of this case were not found: the page texts were compared with r8 only")
    if f.get("r8_pages") is not None:
        same = spec.p1_r8_differing is None or f.get("r8_differing") == spec.p1_r8_differing
        o.note(step, "page texts against r8: %s of %s equal%s%s"
               % (f.get("r8_equal_pages"), f.get("r8_pages"),
                  " (pages %s differ)" % f.get("r8_differing") if f.get("r8_differing") else "",
                  "" if spec.p1_r8_differing is None else
                  ("; the same pages as in P1" if same else "; P1 had pages %s" % spec.p1_r8_differing)))


def leftovers(pl: Places, sysm: Any) -> List[str]:
    left = []
    if pl.test_dir.exists():
        left.append("test folder")
    if sysm.uninstall_key() is not None:
        left.append("uninstall key")
    if os.path.lexists(pl.start_lnk) or os.path.lexists(pl.desktop_lnk):
        left.append("shortcut")
    if pl.data_dir.exists():
        left.append("data folder")
    return left


def uninstall(ctx: Ctx, pl: Places, sysm: Any, o: Outcome) -> None:
    step = "uninstall"
    key = sysm.uninstall_key()
    if key is not None and not same_path(key.get("InstallLocation"), pl.test_dir):
        o.add(step, "ownership", False, "the uninstall key does not point at the test folder: nothing was "
                                        "uninstalled; check Settings > Apps by hand")
        return
    unins = pl.test_dir / "unins000.exe"
    if unins.is_file():
        if ctx.app_data is not None:
            # The app used --data-dir, so %LOCALAPPDATA%\Hangeul Document Checker may not exist: a made-up file there
            # (the preconditions proved the folder did not exist before this run) keeps the uninstaller's data
            # question exercised; "data gone" below must then see it deleted.
            pl.data_dir.mkdir(parents=True, exist_ok=True)
            (pl.data_dir / MARKER_NAME).write_text(MARKER_TEXT, encoding="ascii")
            o.note(step, "the app used --data-dir: a made-up file was put in the app's data folder for the "
                         "uninstaller's data question")
        r = sysm.run(uninstall_command(unins, ctx.reports / "uninstall.log"), timeout_s=ctx.uninstall_timeout_s)
        o.seconds[step] = r.seconds
        end = time.monotonic() + ctx.grace_s
        while leftovers(pl, sysm) and time.monotonic() < end:
            sysm.sleep(2.0)
        o.add(step, "uninstaller", not r.timed_out and (r.returncode == 0 or not leftovers(pl, sysm)),
              "exit %s in %.0f s%s%s" % (r.returncode, r.seconds, ", timed out" if r.timed_out else "",
                                         "" if r.in_job else ", tree not tracked"))
    else:
        o.add(step, "uninstaller", key is None and not pl.test_dir.exists(),
              "no unins000.exe in the test folder" + ("" if key is None else " although the key exists"))
    left = leftovers(pl, sysm)
    o.add(step, "folder gone", "test folder" not in left, "the test install folder %s"
          % ("is gone" if "test folder" not in left else "is still there (files the app wrote?)"))
    o.add(step, "key gone", "uninstall key" not in left, "HKCU {AppId}_is1 %s"
          % ("is gone" if "uninstall key" not in left else "is still there"))
    o.add(step, "shortcut gone", "shortcut" not in left, "Start menu and desktop %s"
          % ("hold no shortcut of the app" if "shortcut" not in left else "still hold a shortcut of the app"))
    o.add(step, "data gone", "data folder" not in left,
          "the app's data folder (%s) is gone" % ("made-up file" if ctx.app_data is not None else
                                                 "made by the self-test") if "data folder" not in left else
          "%LOCALAPPDATA%\\Hangeul Document Checker is still there: it holds only this test's files; "
          "delete it by hand before the next run")


def run_cycle(ctx: Ctx, pl: Places, sysm: Any, out: Callable[[str], None] = print) -> Outcome:
    o = Outcome(out=out)
    out("P3 install cycle - build %s" % ctx.build_id)
    reasons = preconditions(pl, sysm)
    if reasons:
        o.status, o.reasons = "SKIPPED", reasons
        for r in reasons:
            out("  SKIPPED: " + r)
        out("  (nothing was installed or changed)")
        return o
    o.add("pre", "preconditions", True, "no install, data folder or shortcut of the app on this PC")
    ctx.reports.mkdir(parents=True, exist_ok=True)
    if pl.test_dir.is_dir():
        pl.test_dir.rmdir()          # empty (preconditions): let Setup create it, so the uninstall removes it
    started = False
    try:
        for n in (1, 2):
            step = "install-%d" % n
            probe = pl.test_dir / PROBE_REL
            if n == 2:
                probe.parent.mkdir(parents=True, exist_ok=True)
                probe.write_text(PROBE_TEXT, encoding="ascii")
            started = True
            r = sysm.run(setup_command(ctx.setup_exe, pl.test_dir, ctx.reports / ("install-%d.log" % n)),
                         timeout_s=ctx.install_timeout_s)
            o.seconds[step] = r.seconds
            if not o.add(step, "setup", r.returncode == 0 and not r.timed_out,
                         "Setup exit %s in %.0f s%s" % (r.returncode, r.seconds, ", timed out" if r.timed_out else "")):
                break
            check_install(ctx, pl, sysm, o, step)
            if n == 2:
                o.add(step, "upgrade", not probe.exists(),
                      "[InstallDelete] removed the made-up file in _internal" if not probe.exists()
                      else "the made-up file in _internal survived the upgrade")
            if ctx.selftest:
                run_selftest(ctx, pl, sysm, o, step, n)
            if n == 2 and ctx.case is not None:
                run_case(ctx, pl, sysm, o)
    except Exception as e:                       # a bug or an I/O error: record it, then clean up
        o.add("cycle", "error", False, "unexpected %s" % type(e).__name__)
    finally:
        if started:
            try:
                uninstall(ctx, pl, sysm, o)
            except Exception as e:
                o.add("uninstall", "error", False, "unexpected %s; check Settings > Apps and the test folder"
                      % type(e).__name__)
    if o.failed:
        o.status = "FAIL"
    elif not ctx.selftest:
        o.status = "PARTIAL"
    else:
        o.status = "PASS"
    return o


# ----------------------------------------------------------------------------------------------------------------
# Inputs of a build

def load_ctx(build_id: str, builds: Path, reports: Optional[Path]) -> Ctx:
    """The Setup (checked against installer.json's sha256) and the build's MANIFEST.json."""
    bi.parse_build_id(build_id)
    inst = builds / build_id / "installer"
    info = json.loads((inst / "installer.json").read_text(encoding="utf-8"))
    if info.get("build_id") != build_id:
        raise ValueError("installer.json belongs to another build id")
    if info.get("app_id") not in (None, bi.APP_ID):
        raise ValueError("installer.json names another AppId")
    files = info.get("files") or []
    if not files or not info.get("setup") or files[0].get("name") != info["setup"]:
        raise ValueError("installer.json lists no Setup.exe")
    for f in files:
        p = inst / f["name"]
        if not p.is_file() or p.stat().st_size != f.get("bytes") or bi.sha256_file(p) != f.get("sha256"):
            raise RuntimeError("%s differs from installer.json (size or sha256)" % f["name"])
    app = builds / build_id / "dist" / bi.APP_NAME
    mpath = bi.find_manifest(app)
    if mpath is None:
        raise ValueError("the build's MANIFEST.json is missing")
    return Ctx(build_id=build_id, app_version=info.get("app_version") or bi.APP_VERSION_DEFAULT,
               setup_exe=inst / info["setup"], manifest=bi.load_manifest(mpath),
               manifest_rel=mpath.relative_to(app).as_posix(), manifest_sha256=bi.sha256_file(mpath),
               reports=reports or (builds / build_id / "reports" / "install_cycle"))


def main(argv: Optional[List[str]] = None, *, system: Any = None, places: Optional[Places] = None,
         out: Callable[[str], None] = print) -> int:
    p = argparse.ArgumentParser(prog="install_cycle.py", description="APP_SPEC r2 test P3: install, upgrade, "
                                "uninstall the Setup.exe of one build; never touches the owner's install or data.")
    p.add_argument("--build-id", required=True)
    p.add_argument("--builds-root", default=str(bi.BUILDS))
    p.add_argument("--reports", help="default: <builds>\\<id>\\reports\\install_cycle")
    p.add_argument("--no-selftest", action="store_true", help="skip the installed self-test (result PARTIAL)")
    p.add_argument("--install-timeout-s", type=float, default=INSTALL_TIMEOUT_S)
    p.add_argument("--uninstall-timeout-s", type=float, default=UNINSTALL_TIMEOUT_S)
    p.add_argument("--selftest-timeout-s", type=float, default=SELFTEST_TIMEOUT_S)
    p.add_argument("--grace-s", type=float, default=GRACE_S)
    p.add_argument("--case", help="after the upgrade, the installed app also checks this r8 case (private-runner"
                                  "\\runs\\<label>, e.g. owner_eap1_r8) through cardrun; its code must equal P1's")
    p.add_argument("--case-input", help="an absolute stand-in for the case's input (as parity.py --input)")
    p.add_argument("--expect-code", help="default: the case's code in builds\\<id>\\reports\\parity.json (P1)")
    p.add_argument("--case-timeout-s", type=float, default=CASE_TIMEOUT_S)
    p.add_argument("--data-dir", help="an absolute, new or empty folder outside AppData: the installed self-test and "
                                      "case use it (--data-dir) instead of %%LOCALAPPDATA%%\\Hangeul Document Checker; "
                                      "for a test PC where that folder is redirected (see the docstring)")
    p.add_argument("--runs-root", default=str(RUNS), help=argparse.SUPPRESS)
    p.add_argument("--apptest-root", default=str(APPTEST), help=argparse.SUPPRESS)
    try:
        a = p.parse_args(argv)
    except SystemExit as e:
        return e.code if isinstance(e.code, int) else EXIT_USAGE
    if (a.case_input or a.expect_code) and not a.case:
        out("install_cycle: --case-input and --expect-code need --case")
        return EXIT_USAGE
    app_data = None
    if a.data_dir:
        app_data = Path(a.data_dir)
        la = os.environ.get("LOCALAPPDATA", "")
        if (not app_data.is_absolute() or (la and bi.rel_key(str(app_data)).startswith(bi.rel_key(la) + "/"))
                or (app_data.exists() and (not app_data.is_dir() or any(app_data.iterdir())))):
            out("install_cycle: --data-dir must be an absolute, new or empty folder outside %LOCALAPPDATA%")
            return EXIT_USAGE
    try:
        ctx = load_ctx(a.build_id, Path(a.builds_root), Path(a.reports) if a.reports else None)
        if a.case:
            ctx.case = load_case(a.build_id, Path(a.builds_root), a.case, case_input=a.case_input,
                                 expect_code=a.expect_code, runs=Path(a.runs_root), apptest=Path(a.apptest_root),
                                 timeout_s=a.case_timeout_s)
    except RuntimeError as e:
        out("install_cycle: FAIL: %s" % e)
        return EXIT_FAIL
    except OSError as e:
        out("install_cycle: cannot start: a build file is missing or unreadable (%s); run build.py installer "
            "for this build id first" % type(e).__name__)
        return EXIT_USAGE
    except (ValueError, KeyError, TypeError, AttributeError, bi.BuildError) as e:
        out("install_cycle: cannot start: %s" % (e if isinstance(e, (ValueError, bi.BuildError))
                                                 else type(e).__name__))
        return EXIT_USAGE
    ctx.selftest = not a.no_selftest
    ctx.app_data = app_data
    if app_data is not None:
        out("  note: the installed app uses --data-dir (a folder outside AppData) instead of its real data folder")
    ctx.install_timeout_s, ctx.uninstall_timeout_s = a.install_timeout_s, a.uninstall_timeout_s
    ctx.selftest_timeout_s, ctx.grace_s = a.selftest_timeout_s, a.grace_s
    if system is None:
        removed = [v for v in TEST_ENV_VARS if os.environ.pop(v, None) is not None]
        if removed:
            out("  note: removed test variables from the environment: %s" % ", ".join(removed))
    o = run_cycle(ctx, places or real_places(), system or System(), out)
    ctx.reports.mkdir(parents=True, exist_ok=True)
    (ctx.reports / "install_cycle.json").write_text(json.dumps(dict(o.to_json(), build_id=ctx.build_id),
                                                               indent=2) + "\n", encoding="utf-8")
    ok = sum(1 for c in o.checks if c.ok)
    out("RESULT: %s (%d of %d checks ok)" % (o.status, ok, len(o.checks)))
    return {"PASS": EXIT_PASS, "SKIPPED": EXIT_SKIPPED, "PARTIAL": EXIT_PARTIAL}.get(o.status, EXIT_FAIL)


if __name__ == "__main__":
    sys.exit(main())
