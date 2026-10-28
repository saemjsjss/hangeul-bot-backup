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
  3. ``unins000.exe /VERYSILENT /SUPPRESSMSGBOXES /NORESTART /LOG=...``; then the test folder, the uninstall key,
     the shortcuts and the data folder made by the self-test must all be gone (the silent uninstall answers the
     data question with Yes).
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
                     loader: Callable[[], Any] = _load_cardrun) -> Tuple[Optional[int], str]:
    """``<exe> --selftest --report-dir <dir>`` through Task H's ``cardrun.run(cmd, timeout)`` (R2.6, Q-O1).

    No ``--data-dir``: the self-test uses the app's real data folder, which the silent uninstall must then delete.
    When cardrun.run takes ``what`` / ``env`` and offers ``app_env()`` (the environment without the test switches
    and the approval variable), they are passed too."""
    try:
        cardrun = loader()
    except Exception as e:
        return None, "cardrun.py could not be loaded (%s)" % type(e).__name__
    cmd = [str(exe), "--selftest", "--report-dir", str(report_dir)]
    kwargs: Dict[str, Any] = {}
    try:
        params = inspect.signature(cardrun.run).parameters
    except (TypeError, ValueError):
        params = {}
    if "what" in params:
        kwargs["what"] = "hdc install cycle (P3): installed --selftest"
    if "env" in params and callable(getattr(cardrun, "app_env", None)):
        kwargs["env"] = cardrun.app_env()
    try:
        result = cardrun.run(cmd, timeout_s, **kwargs)
    except Exception as e:
        hint = (" (Q-O1: set HDC_CARDRUN_APPROVAL=Q-O1 only after the orchestrator approved it)"
                if type(e).__name__ == "NotApproved" else "")
        return None, "cardrun.run raised %s%s" % (type(e).__name__, hint)
    code = exit_code_of(result)
    return code, "" if code is not None else "cardrun.run gave no exit code"


class System:
    """The real side effects on this PC."""

    def uninstall_key(self) -> Optional[Dict[str, Any]]:
        return read_uninstall_key()

    def run(self, cmd: str, *, timeout_s: float) -> bi.RunResult:
        return bi.run_tree(cmd, timeout_s=timeout_s, below_normal=True)

    def selftest(self, exe: Path, report_dir: Path, timeout_s: float) -> Tuple[Optional[int], str]:
        return cardrun_selftest(exe, report_dir, timeout_s)

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
                "notes": self.notes, "seconds": self.seconds}


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
    code, why = sysm.selftest(pl.exe, report_dir, ctx.selftest_timeout_s)
    o.seconds["selftest-%d" % n] = round(time.monotonic() - t0, 1)
    o.add(step, "self-test", code == 0, "installed --selftest exit %s through cardrun%s"
          % (code, ("; " + why) if why else ""))
    if not list(report_dir.glob("SELFTEST*.txt")):
        o.note(step, "no SELFTEST*.txt in the self-test's report folder")


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
          "the data folder made by the self-test is gone" if "data folder" not in left else
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
    try:
        a = p.parse_args(argv)
    except SystemExit as e:
        return e.code if isinstance(e.code, int) else EXIT_USAGE
    try:
        ctx = load_ctx(a.build_id, Path(a.builds_root), Path(a.reports) if a.reports else None)
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
