"""Safety guards of the document-check test runner (RUNNER_SPEC section 0, S1-S5 and S11; Task 1).

prepare() arms a sys.addaudithook guard for the runner process:
  * a write of any kind (write-mode open / os.open, os.remove, os.rename / os.replace, os.mkdir,
    os.rmdir, os.truncate, os.utime, os.chmod, os.link / os.symlink, shutil.copyfile / copystat /
    copymode / move / rmtree) whose target lies outside C:\\Hangeul\\DOCCHECK\\lab\\private-runner, or
    inside a FORBIDDEN folder or an `extra_forbidden` one, raises PermissionError BEFORE the operating
    system is asked, so nothing is created, changed or removed (S1, S3);
  * any open, read or write, and any directory listing under C:\\Hangeul\\BOT raises (S2);
  * network contact (socket.connect / getaddrinfo / sendto / gethostbyname ..., urllib.Request,
    http.client.connect and the other stdlib clients) raises (S4);
  * process creation (subprocess, os.system / spawn / exec / startfile, and _winapi.CreateProcess, which
    multiprocessing calls directly) is refused in the runner (S5: it spawns none; a child would escape the
    guard) and recorded in the GPU worker (gpu_guard asks nvidia-smi);
  * a hard link (os.link) counts as a write to its SOURCE too, and any write to an existing file that has
    another hard link (write-mode open, os.truncate / utime / chmod, _winapi.CreateFile with write access,
    _winapi.CopyFile2 onto it) is refused (realpath cannot see hard links);
  * sqlite3.connect, _winapi.CreateFile and _winapi.CopyFile2 (shutil.copy2 on Python 3.12) are checked like
    open (bot opens, writes; CopyFile2's source as a read); _winapi.CreateJunction is always refused;
  * the C:\\Hangeul\\BOT check also resolves the real path (junctions, symbolic links) and reads the
    device (\\\\.\\C:\\...), long (\\\\?\\C:\\...) and local-share (\\\\localhost\\C$\\...) spellings of a path;
    an NTFS stream suffix ("x.pdf:s", "dir::$INDEX_ALLOCATION") is the file or folder itself;
  * a device-namespace path that names no drive (\\\\?\\Volume{GUID}\\..., \\\\?\\GLOBALROOT\\Device\\...,
    \\\\.\\HarddiskVolumeN\\..., any share but a local drive share) is refused for every open, read or write: it
    cannot be placed against the roots (it was once made relative to the current folder, review 2);
  * refused events are counted; the first BLOCKED_KEEP are kept (a retry loop cannot fill the memory), and
    a refused create-new open (also one under C:\\Hangeul\\BOT) or folder creation raises CreateRefused,
    which tempfile does not retry;
  * tempfile and TMP / TEMP point to ...\\private-runner\\tmp, and bytecode writing is off;
  * the input (bundle file, or every file of the input folder) is snapshotted (size, mtime_ns, sha256);
    finish() re-snapshots and raises SafetyError on any change. An input folder that holds a junction or a
    symbolic link is refused before anything reads through it (check_input).
install_write_guard() arms the same guard for another process (the GPU worker, Task 8): writes only under
private-runner or to the listed files (gpu_guard's dev lock, S11), no network; with
block_bot_opens=False reads under C:\\Hangeul\\BOT (its venv) are allowed.

An audit hook cannot be removed, so a process arms it once (a second prepare() replaces the policy). Unit
tests arm it in a child process (tests/test_safety.py). Writes made by native code (MuPDF saving a PDF,
OpenCV writing an image) raise no audit event; the runner's own code calls assert_writable() before
every such write (engine.cut, engine.cut_page).

APP mode (places.py; APP_SPEC rev 1 section 4.2, rev 2 R2.1.6): the write root is DATA, nothing is forbidden but
the input, there is no bot folder and no sibling rule; prepare() also allows the report folder the user picked
(checked by check_out_dir's APP rules) and may be called twice with the same arguments; arm_window() and
allow_file() guard the window. DEV values, guards and messages are unchanged. A path that must lie in DATA (the cache
root, the page texts, a written path with no guard armed) is judged by _in_data: its real path against DATA's REAL path
(1.0.1), so a %LOCALAPPDATA% that resolves elsewhere is no refusal; the refusal says APP_DATA_OUTSIDE.

Standard library only, so the GPU worker can import it in the bot venv. No personal data.
"""
from __future__ import annotations

import hashlib
import os
import re
import sys
import tempfile
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence, Tuple

from . import botimport, places


class SafetyError(Exception):
    """An unsafe path or a changed input. The CLI turns it into exit 2 (before the run) or 1 (after)."""


class CreateRefused(OSError):
    """A refused create-new open (O_EXCL, as tempfile.mkstemp makes it) or a refused os.mkdir (as
    tempfile.mkdtemp / TemporaryDirectory make it). Not a PermissionError, so tempfile does not retry it (on
    Windows it retries PermissionError up to TMP_MAX = 2**31 - 1 times while the folder exists)."""


BLOCKED_KEEP = 200          # refused events kept in SafetyState.blocked (every one is counted in blocked_total)
_PATHLIKE = re.compile(r"(?:[A-Za-z]:[\\/]|\\\\|//)[^\n]*")


def redact(text: Any) -> str:
    """The text with every path (a drive or UNC path, to the end of its line) replaced by <path>: what is
    printed to stdout / stderr carries no file or folder name (a folder may be named after a student)."""
    return _PATHLIKE.sub("<path>", str(text))


# APP_SPEC rev 1 section 4.3: every value below comes from places (DEV: devplaces, the same values as before; APP:
# DATA, nothing forbidden, no bot folder, no sibling rule). The names are kept for the tests.
_PLACES = places.current()
APP_MODE = _PLACES.mode == places.APP
PRIVATE = botimport.PRIVATE
BOT = _PLACES.no_open_root                  # None in APP
HANGEUL = _PLACES.sibling_root              # the "* STUDENT DOCUMENTS" siblings are forbidden too; None in APP
FORBIDDEN = list(_PLACES.forbidden)
NO_INSTALL = list(_PLACES.no_install)       # never written either (S1)
TMP_DIR = _PLACES.temp_dir
WRITE_LABEL = _PLACES.write_label           # message words for the write root (DEV: as the messages always said)
NO_OPEN_LABEL = _PLACES.no_open_label       # message words for the folder nothing is opened in
# The roots the guard is armed with. Tests narrow them in a CHILD process (tests/test_safety.py) so that
# the guard can be proven without a single write outside private-runner and without touching the bot.
WRITE_ROOT = PRIVATE
BOT_ROOT = BOT

_SIBLING_RE = re.compile(r"student documents$")
_NETWORK_EVENTS = frozenset({
    "socket.connect", "socket.getaddrinfo", "socket.sendto", "socket.sendmsg", "socket.gethostbyname",
    "socket.gethostbyname_ex", "socket.gethostbyaddr", "socket.getnameinfo", "urllib.Request",
    "http.client.connect", "ftplib.connect", "smtplib.connect", "poplib.connect", "imaplib.open",
    "nntplib.connect", "telnetlib.Telnet.open", "webbrowser.open"})
_SPAWN_EVENTS = frozenset({"subprocess.Popen", "os.system", "os.startfile", "os.spawn", "os.exec",
                           "os.posix_spawn", "os.fork", "os.forkpty", "_winapi.CreateProcess"})
_LIST_EVENTS = frozenset({"os.listdir", "os.scandir"})
_WRITE_FLAGS = (os.O_WRONLY | os.O_RDWR | os.O_APPEND | os.O_CREAT | os.O_TRUNC
                | getattr(os, "O_TEMPORARY", 0))
_DEVNULL = frozenset({os.path.normcase(os.devnull), os.path.normcase("\\\\.\\nul")})
_LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1", "[::1]", os.environ.get("COMPUTERNAME", "").lower()} - {""}
_SHARE_RE = re.compile(r"^\\\\([^\\]+)\\([A-Za-z])\$(\\.*)?$")
# _winapi.CreateFile: GENERIC_WRITE, GENERIC_ALL, FILE_WRITE_DATA, FILE_APPEND_DATA, DELETE, FILE_WRITE_ATTRIBUTES
_WIN_WRITE_ACCESS = 0x40000000 | 0x10000000 | 0x2 | 0x4 | 0x10000 | 0x100
_WIN_CREATES = (1, 2, 4, 5)      # CREATE_NEW, CREATE_ALWAYS, OPEN_ALWAYS, TRUNCATE_EXISTING


# --------------------------------------------------------------------------------- paths (pure)
def _strip_streams(s: str) -> str:
    """The path without NTFS stream suffixes: "C:\\x\\in.pdf:s" is C:\\x\\in.pdf, "C:\\d::$INDEX_ALLOCATION\\f"
    is C:\\d\\f (a colon is never part of a Windows file name)."""
    head, rest = (s[:2], s[2:]) if re.match(r"[A-Za-z]:", s) else ("", s)
    if ":" not in rest:
        return s
    return head + "\\".join(part.split(":", 1)[0] for part in rest.split("\\"))


def _norm(p: Any) -> Optional[str]:
    """Absolute, normalised, case-folded form of a path argument; None for a file descriptor or None.
    A relative path is made absolute against the current folder (no file is touched). The long (\\\\?\\C:\\),
    device (\\\\.\\C:\\), long-UNC and local drive-share spellings become the drive path; a stream suffix is
    dropped. A device-namespace path that names no drive (\\\\?\\Volume{GUID}\\..., \\\\?\\GLOBALROOT\\...,
    \\\\.\\HarddiskVolumeN\\...) or any share but a local drive share (\\\\host\\share\\...) is returned
    whole, prefix kept (_device() says so): it is never stripped into a path relative to the current folder."""
    if p is None or isinstance(p, (int, bool)):
        return None
    try:
        s = os.fsdecode(os.fspath(p))
    except TypeError:
        return None
    s = s.replace("/", "\\")
    if s[:8].upper() in ("\\\\?\\UNC\\", "\\\\.\\UNC\\"):
        s = "\\\\" + s[8:]
    elif s.startswith(("\\\\?\\", "\\\\.\\")):
        if re.match(r"[A-Za-z]:(?:\\|$)", s[4:7]):
            s = s[4:]                                # \\?\C:\x and the device spelling \\.\C:\x
        else:
            return os.path.normcase(s)               # a device path with no drive: kept whole (see _device)
    m = _SHARE_RE.match(s)
    if m and _local_host(m.group(1)):
        s = m.group(2) + ":" + (m.group(3) or "\\")  # \\localhost\C$\x (or this computer's name) is C:\x
    elif s.startswith("\\\\"):                       # any other share (another host's, or a named share of this
        return os.path.normcase("\\\\?\\unc\\" + s[2:])   # computer that may map anywhere): never placed or opened
    if not s:
        return None
    return os.path.normcase(os.path.abspath(_strip_streams(s)))


def _local_host(host: str) -> bool:
    h = (host or "").lower()
    return h in _LOCAL_HOSTS or h.startswith("127.")


def _device(s: Optional[str]) -> bool:
    """True for a normalised path in the device namespace that names no drive (see _norm), other than the
    null device and a named pipe: such a path cannot be placed against the write root or C:\\Hangeul\\BOT."""
    if not s or s in _DEVNULL:
        return False
    return s.startswith(("\\\\?\\", "\\\\.\\")) and not s.startswith("\\\\.\\pipe\\")


def _under(s: Optional[str], root: Optional[str]) -> bool:
    if s is None or root is None:
        return False
    return s == root or s.startswith(root.rstrip("\\") + "\\")


def is_forbidden(p: Path) -> bool:
    """True for a path inside C:\\Hangeul\\BOT, the verified student documents, a sibling
    "C:\\Hangeul\\* STUDENT DOCUMENTS" folder, or the numpy venv. Pure: no file is opened or stat'ed."""
    s = _norm(p)
    if s is None:
        return False
    if any(_under(s, _norm(f)) for f in [*FORBIDDEN, *NO_INSTALL]):
        return True
    if HANGEUL is None:                          # APP: no sibling rule
        return False
    h = _norm(HANGEUL)
    if _under(s, h) and s != h:
        first = s[len(h):].lstrip("\\").split("\\", 1)[0]
        if _SIBLING_RE.search(first):
            return True
    return False


def _resolved(p: Path) -> Path:
    return Path(os.path.realpath(os.path.abspath(os.fspath(p))))


def _private(p: Path, what: str) -> Path:
    """p resolved; raises SafetyError unless it lies under PRIVATE and outside every forbidden folder. The
    pure checks come first, so a path under a forbidden folder is refused without touching it.
    APP mode: _in_data (PRIVATE is DATA there; its real path may differ from its name)."""
    if not Path(p).is_absolute():               # a relative path is never echoed: redact() cannot see it (S3)
        raise SafetyError(f"{what} must be an absolute path")
    if APP_MODE:
        return _in_data(Path(p), what, Path(PRIVATE))
    if is_forbidden(Path(p)) or not _under(_norm(p), _norm(PRIVATE)):
        raise SafetyError(f"{what} must lie under {PRIVATE}: {p}")
    r = _resolved(Path(p))
    if not _under(_norm(r), _norm(PRIVATE)) or is_forbidden(r):
        raise SafetyError(f"{what} must lie under {PRIVATE}: {p}")
    return r


# APP mode (1.0.1, APP_SPEC R2.10 I28): the refusal of a path that is not in the app's data folder. Pinned: the worker
# maps it to DATA_NOT_WRITABLE (not REPORT_NOT_WRITABLE: the report folder is not involved).
APP_DATA_OUTSIDE = "lies outside the app's data folder"


def _in_data(p: Path, what: str, data: Path, resolve: Optional[Callable[[Path], Path]] = None) -> Path:
    """APP mode's _private: p resolved; raises SafetyError(f"{what} {APP_DATA_OUTSIDE}") unless
      * p as written lies under DATA as written or under DATA's real path (a caller may pass either), and
      * p's real path lies under DATA's REAL path (resolved against resolved).
    The second rule is the one that matters for safety, and it is the same as before: a junction or link inside DATA
    that leads elsewhere is refused. What changed (1.0.1) is that DATA's own real path is the yardstick: when
    %LOCALAPPDATA% resolves elsewhere (an MSIX package's redirection of a new top-level folder, a junction, folder
    redirection), every path under DATA resolves there too, and 1.0.0 refused them all (its real path was compared
    with DATA's NAME). `resolve` is os.path.realpath's wrapper (tests pass a fake). Nothing is created; DATA need not
    exist yet (the real path of a missing folder is its name)."""
    resolve = resolve or _resolved
    s, d = _norm(p), _norm(data)
    d_real = _norm(resolve(Path(data)))
    if is_forbidden(Path(p)) or not (_under(s, d) or _under(s, d_real)):
        raise SafetyError(f"{what} {APP_DATA_OUTSIDE}: {p}")
    r = resolve(Path(p))
    if is_forbidden(r) or not _under(_norm(r), d_real):
        raise SafetyError(f"{what} {APP_DATA_OUTSIDE}: {p}")
    return r


def check_out_dir(out: Path, input_path: Path) -> Path:
    """The resolved --out folder. Raises SafetyError when it is not absolute, not strictly under
    private-runner, inside a forbidden folder, inside the input (or the input itself), or when the
    input lies inside it (its fixed file names are overwritten). Nothing is created. APP mode: the rules of
    APP_SPEC rev 1 section 4.2 instead (_check_out_dir_app)."""
    if APP_MODE:
        return _check_out_dir_app(Path(out), Path(input_path))
    o = _private(Path(out), "--out")
    if _norm(o) == _norm(PRIVATE):
        raise SafetyError(f"--out must be a folder inside {PRIVATE}, not the folder itself")
    if not Path(input_path).is_absolute():
        raise SafetyError("the input must be an absolute path")
    if _under(_norm(input_path), _norm(BOT_ROOT)):
        raise SafetyError(f"the input must not lie under {NO_OPEN_LABEL} (the runner never opens it)")
    i = _resolved(Path(input_path))
    if _under(_norm(o), _norm(i)):
        raise SafetyError("--out must not lie inside the input")
    if _under(_norm(i), _norm(o)):
        raise SafetyError("the input must not lie inside --out")
    return o


# APP mode (APP_SPEC rev 1 section 4.2): the report folder the user picked. The messages are pinned: the app maps
# them to its plain-English messages (REPORT_INSIDE_INPUT, INPUT_INSIDE_REPORT, REPORT_NOT_WRITABLE).
APP_OUT_RELATIVE = "the report folder must be an absolute path"
APP_OUT_DEVICE = "the report folder must be on a drive of this PC, not a network or device path"
APP_OUT_ROOT = "the report folder must not be a drive root"
APP_OUT_PROTECTED = "the report folder must not lie in a Windows, program or app folder"
APP_OUT_DATA = "the report folder must not be the app's data folder itself"
APP_OUT_INSIDE_INPUT = "the report folder must not lie inside the input"
APP_INPUT_INSIDE_OUT = "the input must not lie inside the report folder"
APP_INPUT_RELATIVE = "the input must be an absolute path"
_PROTECTED_ENV = ("SystemRoot", "WINDIR", "ProgramFiles", "ProgramFiles(x86)", "ProgramW6432", "ProgramData")


def _protected_roots() -> List[str]:
    """%WINDIR%, the program folders, %ProgramData% and the app's own folder (normalised; unset ones left out)."""
    roots = [os.environ.get(k, "") for k in _PROTECTED_ENV]
    roots += [str(places.app_install_dir())]
    if _PLACES.bundle is not None:
        roots.append(str(_PLACES.bundle))
    out: List[str] = []
    for r in roots:
        s = _norm(r) if r and os.path.isabs(r) else None
        if s and s not in out:
            out.append(s)
    return out


def _is_drive_root(p: Path) -> bool:
    a = Path(os.path.abspath(p))
    return a.parent == a


def _check_out_dir_app(out: Path, input_path: Path) -> Path:
    """The resolved report folder in APP mode. Pure checks, nothing is created: absolute; on a drive (not a share or
    a device path); not a drive root; not under %WINDIR%, the program folders, %ProgramData% or the app's own folder;
    not DATA itself (under DATA is fine); not inside the input, nor the input inside it."""
    if not out.is_absolute():
        raise SafetyError(APP_OUT_RELATIVE)
    if _device(_norm(out)):
        raise SafetyError(APP_OUT_DEVICE)
    o = _resolved(out)
    if _device(_norm(o)):
        raise SafetyError(APP_OUT_DEVICE)
    if _is_drive_root(out) or _is_drive_root(o):
        raise SafetyError(APP_OUT_ROOT)
    forms = (_norm(out), _norm(o))
    if any(_under(s, r) for s in forms for r in _protected_roots()):
        raise SafetyError(APP_OUT_PROTECTED)
    if any(s in (_norm(WRITE_ROOT), _norm(_resolved(Path(WRITE_ROOT)))) for s in forms):
        raise SafetyError(APP_OUT_DATA)
    if not input_path.is_absolute():
        raise SafetyError(APP_INPUT_RELATIVE)
    i = _resolved(input_path)
    ins = (_norm(input_path), _norm(i))
    if any(_under(s, x) for s in forms for x in ins):
        raise SafetyError(APP_OUT_INSIDE_INPUT)
    if any(_under(x, s) for s in forms for x in ins):
        raise SafetyError(APP_INPUT_INSIDE_OUT)
    return o


def assert_writable(p: Path) -> None:
    """Raises SafetyError unless p (a file or folder to be written) lies under private-runner and outside
    every forbidden folder. Called before writes made by native code, which the audit hook cannot see.
    APP mode: p must be allowed by the armed guard's roots (DATA, the report folder; the window's report root and
    allowed files), or lie under DATA when no guard is armed."""
    if APP_MODE:
        if not Path(p).is_absolute():
            raise SafetyError("a written path must be an absolute path")
        pol = _POLICY
        if pol is None:
            _private(Path(p), "a written path")
            return
        why = _write_refusal(pol, _norm(p))
        if why:
            raise SafetyError(f"{why}: {p}")
        return
    _private(Path(p), "a written path")
    if _POLICY is not None:
        why = _write_refusal(_POLICY, _norm(p))
        if why:
            raise SafetyError(f"{why}: {p}")


# --------------------------------------------------------------------------------- snapshots
def file_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _expand(paths: Iterable[Path]) -> List[Path]:
    out: List[Path] = []
    for p in paths:
        p = Path(p)
        if p.is_dir():
            out.extend(sorted(f for f in p.rglob("*") if f.is_file()))
        else:
            out.append(p)
    return out


def snapshot(paths: Iterable[Path]) -> Dict[str, Tuple[int, int, str]]:
    """{str(file): (size, mtime_ns, sha256)} for every file (a folder stands for every file under it). A
    path that does not exist is left out, so a vanished file shows as a change."""
    snap: Dict[str, Tuple[int, int, str]] = {}
    for f in _expand(paths):
        try:
            st = f.stat()
            snap[str(f)] = (st.st_size, st.st_mtime_ns, file_sha256(f))
        except OSError:
            continue
    return snap


# --------------------------------------------------------------------------------- the audit hook
@dataclass
class SafetyState:
    out: Optional[Path]
    cache_root: Optional[Path]
    snapshots: Dict[str, Tuple[int, int, str]]
    blocked: List[str] = field(default_factory=list)       # "<event>: <path or host>" of every refused event
    bot_opens: int = 0                                      # opens / listings under C:\Hangeul\BOT (all refused)
    input: Optional[Path] = None
    spawned: List[str] = field(default_factory=list)        # process creations seen (refused in the runner)
    mode: str = "runner"                                    # "runner" | "worker" | "window" (APP: arm_window)
    blocked_total: int = 0                                  # every refused event (blocked keeps the first ones)


@dataclass
class _Policy:
    write_roots: List[str]
    allowed_files: List[str]
    forbidden: List[str]
    bot_root: Optional[str]           # None: no bot-open check at all (tests only)
    state: SafetyState
    bot_read_ok: List[str] = field(default_factory=list)   # reads allowed under these (GPU worker: the bot venv)
    key: Any = None                   # APP prepare(): the arguments it was armed with (a second call re-arms it)


_POLICY: Optional[_Policy] = None
_HOOKED = False
_LOCK = threading.Lock()
_LOCAL = threading.local()


def _write_refusal(pol: _Policy, s: Optional[str]) -> Optional[str]:
    """Why a write to the normalised path s is refused, or None when it is allowed."""
    if s is None or s in _DEVNULL:
        return None
    if _device(s):
        return "write to a device-namespace path (it names no drive, so it cannot be checked)"
    if s in pol.allowed_files:
        return None
    if any(_under(s, f) for f in pol.forbidden):
        return "write into a forbidden folder"
    if is_forbidden(s) or _under(s, pol.bot_root):
        return "write into a protected folder"
    if not any(_under(s, r) for r in pol.write_roots):
        return f"write outside {WRITE_LABEL}"
    try:
        real = os.path.normcase(os.path.realpath(s))
    except (OSError, ValueError):
        return "write to a path that could not be resolved"
    if real != s:
        if real in pol.allowed_files:
            return None
        if (is_forbidden(real) or _under(real, pol.bot_root) or any(_under(real, f) for f in pol.forbidden)
                or not any(_under(real, r) for r in pol.write_roots)):
            return f"write through a link that leaves {WRITE_LABEL}"
    return None


def _refuse(pol: _Policy, event: str, what: Any, why: str, exc: type = PermissionError) -> None:
    note = f"{event}: {what}"
    pol.state.blocked_total += 1
    if len(pol.state.blocked) < BLOCKED_KEEP:
        pol.state.blocked.append(note)
    raise exc(f"runner safety guard: {why} ({note})")


def _check_write(pol: _Policy, event: str, path: Any, dir_fd: Any = None, exc: type = PermissionError) -> None:
    if path is None or isinstance(path, (int, bool)):
        return
    if dir_fd is not None:
        try:
            rel = not os.path.isabs(os.fsdecode(os.fspath(path)))
        except TypeError:
            rel = False
        if rel:
            _refuse(pol, event, path, "a write relative to a folder handle cannot be checked", exc)
    s = _norm(path)
    why = _write_refusal(pol, s)
    if why:
        _refuse(pol, event, path, why, exc)


def _bot_hit(pol: _Policy, path: Any) -> bool:
    """True when the path, as written or resolved (junctions, symbolic links), lies under C:\\Hangeul\\BOT and
    outside the folders reads are allowed in."""
    s = _norm(path)
    if s is None:
        return False
    cands = [s]
    try:
        r = _norm(os.path.realpath(s))
        if r and r != s:
            cands.append(r)
    except (OSError, ValueError):
        pass
    return any(_under(c, pol.bot_root) and not any(_under(c, ok) for ok in pol.bot_read_ok) for c in cands)


def _check_bot(pol: _Policy, event: str, path: Any, exc: type = PermissionError) -> None:
    """Refuses an open / read / listing under C:\\Hangeul\\BOT (as written or resolved) and any device-namespace
    path that names no drive (it cannot be placed). `exc`: CreateRefused for a create-new open, which tempfile
    must not retry."""
    if pol.bot_root is None:
        return
    if _device(_norm(path)):
        _refuse(pol, event, str(path)[:200], "a device-namespace or share path (volume GUID, GLOBALROOT, a network "
                                             "share) cannot be checked", exc)
    if _bot_hit(pol, path):
        pol.state.bot_opens += 1
        _refuse(pol, event, path, f"nothing under {NO_OPEN_LABEL} may be opened here" +
                (" (only its venv is read)" if pol.bot_read_ok else ""), exc)


def _check_links(pol: _Policy, event: str, path: Any) -> None:
    """A write of any kind (open for writing, truncate, utime, chmod, CreateFile with write access, CopyFile2 onto
    it) to an existing file with another hard link: the other name may lie anywhere."""
    s = _norm(path)
    if s is None or s in _DEVNULL:
        return
    try:
        n = os.stat(s).st_nlink
    except (OSError, ValueError):
        return
    if n > 1:
        _refuse(pol, event, path, "write to a file that has another hard link")


def _arg(args: tuple, i: int) -> Any:
    return args[i] if len(args) > i else None


def _handle(pol: _Policy, event: str, args: tuple) -> None:
    if event == "open":
        path, mode, flags = _arg(args, 0), _arg(args, 1), _arg(args, 2)
        if path is None or isinstance(path, (int, bool)):
            return
        write = isinstance(mode, str) and any(c in mode for c in "wax+")
        excl = isinstance(mode, str) and "x" in mode
        if isinstance(flags, int) and not isinstance(flags, bool):
            if flags & _WRITE_FLAGS:
                write = True
            excl = excl or bool(flags & getattr(os, "O_EXCL", 0))
        exc = CreateRefused if excl else PermissionError     # tempfile.mkstemp retries a PermissionError
        _check_bot(pol, event, path, exc)
        if write:
            _check_write(pol, event, path, exc=exc)
            _check_links(pol, event, path)
    elif event in _LIST_EVENTS:
        p = _arg(args, 0)
        _check_bot(pol, event, "." if p is None else p)
    elif event == "os.mkdir":
        path = _arg(args, 0)
        exists = False
        if not is_forbidden(path) and not any(_under(_norm(path), f) for f in pol.forbidden):
            try:
                exists = os.path.isdir(path)    # mkdir of an existing folder changes nothing
            except (TypeError, ValueError, OSError):
                exists = False
        if not exists:                        # CreateRefused: tempfile.mkdtemp never retries it
            _check_write(pol, event, path, _arg(args, 2), exc=CreateRefused)
    elif event in ("os.remove", "os.rmdir"):
        _check_write(pol, event, _arg(args, 0), _arg(args, 1))
    elif event in ("os.utime", "os.chmod"):
        _check_write(pol, event, _arg(args, 0), _arg(args, 3) if event == "os.utime" else _arg(args, 2))
        _check_links(pol, event, _arg(args, 0))
    elif event == "os.truncate":
        _check_write(pol, event, _arg(args, 0))
        _check_links(pol, event, _arg(args, 0))
    elif event == "os.rename":
        _check_write(pol, event, _arg(args, 0), _arg(args, 2))
        _check_write(pol, event, _arg(args, 1), _arg(args, 3))
    elif event in ("os.link", "os.symlink"):
        if event == "os.link":              # a hard link makes its source writable through the new name
            _check_bot(pol, event, _arg(args, 0))
            _check_write(pol, event, _arg(args, 0), _arg(args, 2))
        _check_write(pol, event, _arg(args, 1), _arg(args, 3))
    elif event == "sqlite3.connect":
        db = _arg(args, 0)
        if isinstance(db, (str, bytes, os.PathLike)):
            name = os.fsdecode(os.fspath(db))
            if name.startswith("file:"):
                _refuse(pol, event, name[:200], "a database URI cannot be checked")
            elif name not in ("", ":memory:"):
                _check_bot(pol, event, name)
                _check_write(pol, event, name)
    elif event == "_winapi.CreateFile":
        name, access, disp = _arg(args, 0), _arg(args, 1), _arg(args, 3)
        if isinstance(name, str) and not name.lower().replace("/", "\\").startswith("\\\\.\\pipe\\"):
            _check_bot(pol, event, name)
            if (isinstance(access, int) and access & _WIN_WRITE_ACCESS) or disp in _WIN_CREATES:
                _check_write(pol, event, name)
                _check_links(pol, event, name)
    elif event == "_winapi.CopyFile2":            # shutil.copy2 on Python 3.12: the source is read, the copy written
        src, dst = _arg(args, 0), _arg(args, 1)
        _check_bot(pol, event, src)
        _check_bot(pol, event, dst)
        _check_write(pol, event, dst)
        _check_links(pol, event, dst)
    elif event == "_winapi.CreateJunction":
        _refuse(pol, event, f"{_arg(args, 0)} -> {_arg(args, 1)}"[:300], "no junction is ever created here")
    elif event in ("shutil.copyfile", "shutil.copymode", "shutil.copystat", "shutil.copytree"):
        _check_write(pol, event, _arg(args, 1))
    elif event == "shutil.move":
        _check_write(pol, event, _arg(args, 0))
        _check_write(pol, event, _arg(args, 1))
    elif event == "shutil.rmtree":
        _check_write(pol, event, _arg(args, 0), _arg(args, 1))
    elif event == "shutil.unpack_archive":
        _check_write(pol, event, _arg(args, 1))
    elif event in _NETWORK_EVENTS:
        what = ", ".join(str(a) for a in args[:3] if not hasattr(a, "fileno"))[:200]
        _refuse(pol, event, what, "network access is disabled in the runner")
    elif event in _SPAWN_EVENTS:
        what = _arg(args, 1) if event == "_winapi.CreateProcess" else _arg(args, 0)   # (app, command line, cwd)
        if event == "_winapi.CreateProcess" and getattr(_LOCAL, "popen", False):
            _LOCAL.popen = False            # the process subprocess.Popen just announced (recorded once)
        else:
            pol.state.spawned.append(f"{event}: {what}"[:300])
        if pol.state.mode == "runner":      # S5: a child process would escape the whole guard
            _refuse(pol, event, str(what)[:200], "the runner starts no process")
        _LOCAL.popen = event == "subprocess.Popen"


_EVENTS = frozenset({"open", "os.mkdir", "os.remove", "os.rmdir", "os.utime", "os.chmod", "os.truncate",
                     "os.rename", "os.link", "os.symlink", "shutil.copyfile", "shutil.copymode",
                     "shutil.copystat", "shutil.copytree", "shutil.move", "shutil.rmtree",
                     "shutil.unpack_archive", "sqlite3.connect", "_winapi.CreateFile", "_winapi.CopyFile2",
                     "_winapi.CreateJunction"}) | _NETWORK_EVENTS | _SPAWN_EVENTS | _LIST_EVENTS


def _hook(event: str, args: tuple) -> None:
    if event not in _EVENTS:
        return
    pol = _POLICY
    if pol is None or getattr(_LOCAL, "busy", False):
        return
    _LOCAL.busy = True
    try:
        _handle(pol, event, args)
    finally:
        _LOCAL.busy = False


def _arm(pol: _Policy) -> None:
    global _POLICY, _HOOKED
    with _LOCK:
        _POLICY = pol
        if not _HOOKED:
            sys.addaudithook(_hook)
            _HOOKED = True


def armed() -> Optional[SafetyState]:
    """The state of the armed guard in this process (None when no guard is armed)."""
    return _POLICY.state if _POLICY is not None else None


def _temp_to_private() -> None:
    sys.dont_write_bytecode = True
    TMP_DIR.mkdir(parents=True, exist_ok=True)
    tempfile.tempdir = str(TMP_DIR)
    os.environ["TMP"] = os.environ["TEMP"] = str(TMP_DIR)


def _input_files(inp: Path) -> List[Path]:
    return [inp] if inp.is_file() else _expand([inp])


def check_input(input_path: Path) -> Path:
    """The resolved input. Raises SafetyError when it is not absolute, does not exist, or lies under
    C:\\Hangeul\\BOT as written, in a device or local-share spelling, or through a junction / link (the real
    path is resolved). run_case.py calls it before the GPU worker sees the input; prepare() calls it too."""
    if not Path(input_path).is_absolute():
        raise SafetyError("the input must be an absolute path")
    if _device(_norm(input_path)):
        raise SafetyError("the input must be a drive path (C:\\...), not a device-namespace spelling")
    if _under(_norm(input_path), _norm(BOT_ROOT)):
        raise SafetyError(f"the input must not lie under {NO_OPEN_LABEL} (the runner never opens it)")
    inp = _resolved(Path(input_path))
    if _under(_norm(inp), _norm(BOT_ROOT)):
        raise SafetyError(f"the input must not lie under {NO_OPEN_LABEL} (the runner never opens it)")
    if not inp.exists():
        raise SafetyError("the input does not exist")
    if inp.is_dir():
        _no_links_inside(inp)
    return inp


def _no_links_inside(folder: Path) -> None:
    """Raises SafetyError when the input folder holds (at any depth) a junction, a symbolic link or any entry whose
    real path leaves the folder: the snapshots (run_case, prepare) and the listing would read through it before a
    guard is armed (review 2). Nothing is followed: entries are stat'ed without following links."""
    root = os.path.normcase(os.path.realpath(folder))
    stack = [Path(folder)]
    while stack:
        d = stack.pop()
        try:
            with os.scandir(d) as it:
                entries = list(it)
        except OSError as e:
            raise SafetyError(f"a folder inside the input could not be listed ({type(e).__name__})") from None
        for e in entries:
            try:
                is_link = e.is_symlink() or bool(getattr(e, "is_junction", lambda: False)())
                is_dir = e.is_dir(follow_symlinks=False)
            except OSError:
                is_link, is_dir = True, False
            if is_link:
                raise SafetyError("the input folder holds a junction or a link; give a folder of plain files")
            if not _under(os.path.normcase(os.path.realpath(e.path)), root):
                raise SafetyError("the input folder holds an entry that leads outside it; give a folder of plain "
                                  "files")
            if is_dir:
                stack.append(Path(e.path))


def _make_out(o: Path) -> None:
    """Creates the --out folder; an existing FILE there, or a folder that cannot be made, is a SafetyError
    (exit 2, nothing written), never a crash."""
    if o.exists() and not o.is_dir():
        raise SafetyError("--out exists and is not a folder")
    try:
        o.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        raise SafetyError(f"--out could not be created ({type(e).__name__})") from None


def prepare(out: Path, cache_root: Path, input_path: Path,
            extra_forbidden: Sequence[Path] = ()) -> SafetyState:
    """Checks the paths, snapshots the input, points the temp folder into private-runner, creates --out and
    arms the runner's audit hook (writes only under private-runner and outside the input and
    `extra_forbidden`; nothing under C:\\Hangeul\\BOT opened; no process started; no network). Raises
    SafetyError on an unsafe path or a missing input. APP mode: _prepare_app."""
    if APP_MODE:
        return _prepare_app(Path(out), Path(cache_root), Path(input_path), extra_forbidden)
    o = check_out_dir(Path(out), Path(input_path))
    cr = _private(Path(cache_root), "the cache root")
    inp = check_input(Path(input_path))
    state = SafetyState(out=o, cache_root=cr, snapshots=snapshot(_input_files(inp)), input=inp, mode="runner")
    _temp_to_private()
    _make_out(o)
    forbidden = [s for s in (_norm(_resolved(Path(f))) for f in [inp, *extra_forbidden]) if s]
    _arm(_Policy(write_roots=[_norm(WRITE_ROOT)], allowed_files=[], forbidden=forbidden,
                 bot_root=_norm(BOT_ROOT), state=state))
    return state


def _app_roots(paths: Iterable[Path]) -> List[str]:
    """Normalised write roots, each as given and resolved (a junction or a redirected profile folder on the way)."""
    out: List[str] = []
    for p in paths:
        for s in (_norm(p), _norm(_resolved(Path(p)))):
            if s and s not in out:
                out.append(s)
    return out


def _prepare_app(out: Path, cache_root: Path, input_path: Path, extra_forbidden: Sequence[Path]) -> SafetyState:
    """prepare() in APP mode (APP_SPEC rev 1 section 4.2, rev 2 R2.1.6): writes only under DATA and the report
    folder; nothing inside the input (or `extra_forbidden`) written, even under those roots; no bot folder; network
    refused; process starts refused (the worker starts none). The input is snapshotted.
    A second call in the same process with the same arguments (the worker arms the guard before its card work, then
    pipeline.run arms it again) arms the same policy again and returns the same state (the first snapshot is kept,
    so finish() compares against the input as it was when the check started). A call with other arguments while
    such a guard is armed raises SafetyError: one worker process checks one input."""
    o = check_out_dir(out, input_path)
    cr = _private(cache_root, "the cache root")
    inp = check_input(input_path)
    forbidden = [s for s in (_norm(_resolved(Path(f))) for f in [inp, *extra_forbidden]) if s]
    roots = _app_roots([Path(WRITE_ROOT), o])
    key = ("app-runner", tuple(roots), tuple(forbidden), _norm(cr))
    pol = _POLICY
    if pol is not None and pol.key is not None:
        if pol.key != key:
            raise SafetyError("the guard of this process is already armed for another check")
        _temp_to_private()
        _make_out(o)
        _arm(pol)
        return pol.state
    state = SafetyState(out=o, cache_root=cr, snapshots=snapshot(_input_files(inp)), input=inp, mode="runner")
    _temp_to_private()
    _make_out(o)
    _arm(_Policy(write_roots=roots, allowed_files=[], forbidden=forbidden, bot_root=None, state=state, key=key))
    return state


def arm_window(roots: Sequence[Path] = ()) -> SafetyState:
    """The window's guard (APP_SPEC rev 1 section 4.2): writes only under DATA and `roots` (the report root) and the
    files allow_file() adds; network refused; process starts recorded, not refused (the window starts its own
    workers and opens the report). Temp points into DATA\\tmp. Called again (another report root), it replaces the
    window's policy. Returns the guard's state."""
    rs = [Path(r) for r in roots]
    if any(not r.is_absolute() for r in rs):
        raise SafetyError("a report root must be an absolute path")
    if any(_device(_norm(r)) for r in rs):
        raise SafetyError(APP_OUT_DEVICE)
    state = SafetyState(out=None, cache_root=None, snapshots={}, mode="window")
    _temp_to_private()
    _arm(_Policy(write_roots=_app_roots([Path(WRITE_ROOT), *rs]), allowed_files=[], forbidden=[],
                 bot_root=_norm(BOT_ROOT), state=state))
    return state


def allow_file(path: Path) -> str:
    """Allows writes to exactly this one file under the armed guard ("Save as PDF..." in the window; the caller has
    already refused a target inside the checked input). Returns the normalised path. Raises SafetyError when no
    guard is armed, or for a relative, device or share path, or one in a protected folder."""
    pol = _POLICY
    if pol is None:
        raise SafetyError("no guard is armed")
    p = Path(path)
    if not p.is_absolute():
        raise SafetyError("a file to allow must be an absolute path")
    s = _norm(p)
    if s is None or _device(s):
        raise SafetyError("a file to allow must be on a drive of this PC, not a network or device path")
    if is_forbidden(p) or _under(s, pol.bot_root) or any(_under(s, f) for f in pol.forbidden):
        raise SafetyError("a file in a protected folder cannot be allowed")
    with _LOCK:
        if s not in pol.allowed_files:
            pol.allowed_files.append(s)
    return s


def _bot_venv_name() -> str:
    from . import devplaces                     # DEV only: the GPU worker reads the bot's venv (never in the app)
    return devplaces.BOT_VENV_NAME


def install_write_guard(extra_allowed: Sequence[Path] = (), block_bot_opens: bool = True,
                        extra_forbidden: Sequence[Path] = (),
                        bot_read_ok: Optional[Sequence[Path]] = None) -> SafetyState:
    """The same guard for another process (the GPU worker, Task 8): writes only under private-runner or to
    exactly the `extra_allowed` files (gpu_guard's dev lock, S11), never into `extra_forbidden` (the input);
    no network; process creation recorded (gpu_guard asks nvidia-smi). Nothing under C:\\Hangeul\\BOT is
    opened, except, with block_bot_opens=False, reads under C:\\Hangeul\\BOT\\.venv (EasyOCR, torch and CUDA
    come from there); `bot_read_ok` names other folders reads are allowed in (a self-test harness that reads
    the bot's caches passes the bot folder itself). Returns the guard's state (blocked events)."""
    state = SafetyState(out=None, cache_root=None, snapshots={}, mode="worker")
    _temp_to_private()
    allowed = [os.path.normcase(os.path.abspath(os.fspath(f))) for f in extra_allowed]
    forbidden = [s for s in (_norm(_resolved(Path(f))) for f in extra_forbidden) if s]
    _arm(_Policy(write_roots=[_norm(WRITE_ROOT)], allowed_files=allowed, forbidden=forbidden,
                 bot_root=_norm(BOT_ROOT), state=state,
                 bot_read_ok=[] if block_bot_opens else [_norm(Path(p)) for p in (
                     bot_read_ok if bot_read_ok is not None else [Path(BOT_ROOT) / _bot_venv_name()])]))
    return state


def finish(state: SafetyState) -> Dict[str, Any]:
    """Re-snapshots the input and returns the audit summary. Raises SafetyError when any input file
    changed, appeared or vanished during the run."""
    after = snapshot(_input_files(state.input)) if state.input is not None else {}
    changed = sorted(set(state.snapshots) ^ set(after)) + sorted(
        k for k in set(state.snapshots) & set(after) if state.snapshots[k] != after[k])
    summary = {"blocked": list(state.blocked), "blocked_count": max(state.blocked_total, len(state.blocked)),
               "bot_opens": state.bot_opens, "spawned": list(state.spawned),
               "snapshot_before": dict(state.snapshots), "snapshot_after": after,
               "input_unchanged": not changed}
    if changed:
        raise SafetyError(f"the input changed during the run ({len(changed)} file(s))")
    return summary
